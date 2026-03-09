from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import re
import os
import json

app = Flask(__name__)
CORS(app, origins="*", supports_credentials=False)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DSGVOChecker/1.0; +https://dsgvo-checker.de)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9",
    "Accept-Encoding": "identity",
}

TRACKER_PATTERNS = {
    "Google Analytics": [
        r"google-analytics\.com", r"googletagmanager\.com",
        r"gtag\(", r"UA-\d+", r"G-[A-Z0-9]+"
    ],
    "Facebook Pixel": [
        r"connect\.facebook\.net", r"fbq\(", r"facebook\.com/tr"
    ],
    "Hotjar": [r"hotjar\.com", r"hj\("],
    "LinkedIn Insight": [r"snap\.licdn\.com"],
    "Twitter/X Pixel": [r"static\.ads-twitter\.com"],
    "TikTok Pixel": [r"analytics\.tiktok\.com"],
    "Microsoft Clarity": [r"clarity\.ms"],
    "Matomo": [r"matomo\.js", r"piwik\.js"],
}

COOKIE_KEYWORDS = [
    # Bekannte Banner-Tools
    "cookiebot", "usercentrics", "borlabs", "onetrust", "trustarc",
    "cookiefirst", "klaro", "complianz", "cookieconsent", "cookie-banner",
    "cookie_notice", "cookie-law", "gdpr-cookie", "wp-gdpr",
    "cookie consent", "cookie banner", "cookie-script", "cookiescript",
    "iubenda", "termly", "quantcast", "didomi", "axeptio",
    # WordPress-Plugin-Spuren
    "cmplz", "wp-cmplz", "cookie-notice", "moove_gdpr",
    "gdpr-cookie-compliance", "gdpr-cookie-consent", "uk-cookie-consent",
    "webtoffee", "cookieyes", "cookie-law-info", "wt-cli",
    "cli_cookie", "wp_cookie", "real-cookie-banner",
    # Generische Consent-Begriffe
    "consent-banner", "consent-overlay", "consent-manager",
    "cookie-policy", "cookiepolicy", "cookie_policy",
]

PRIVACY_KEYWORDS = [
    "datenschutz", "datenschutzerklärung", "datenschutzerklaerung",
    "privacy policy", "privacy-policy", "datenschutzhinweise",
    "datenschutzrichtlinie", "privacy", "dsgvo", "gdpr",
    "datenschutzinformation", "datenschutzbeauftragter"
]

PRIVACY_URL_PATHS = [
    "/datenschutz", "/datenschutzerklaerung", "/datenschutzerklärung",
    "/privacy", "/privacy-policy", "/dsgvo", "/gdpr"
]

IMPRINT_KEYWORDS = [
    "impressum", "imprint", "legal notice", "anbieterkennzeichnung",
    "rechtliche hinweise", "legal", "kontakt & impressum",
    "über uns", "about", "pflichtangaben"
]

IMPRINT_URL_PATHS = [
    "/impressum", "/imprint", "/legal", "/legal-notice",
    "/rechtliches", "/kontakt", "/ueber-uns", "/about"
]


def fetch_page(url: str):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10, allow_redirects=True)
        soup = BeautifulSoup(resp.text, "html.parser")
        return resp, soup, resp.text, None
    except Exception as e:
        return None, None, "", str(e)


def check_ssl(url: str) -> dict:
    passed = url.startswith("https://")
    return {
        "check": "SSL / HTTPS",
        "icon": "🔒",
        "passed": passed,
        "detail": "Verbindung ist verschlüsselt." if passed else "Die Seite läuft nicht über HTTPS – ein erhebliches DSGVO-Risiko.",
        "weight": 20
    }


def check_cookie_banner_keywords(html: str, soup) -> tuple:
    """Fast keyword-based pre-check before calling Claude API."""
    html_lower = html.lower()

    for kw in COOKIE_KEYWORDS:
        if kw in html_lower:
            return True, f"Cookie-Tool erkannt: {kw}"

    for script in soup.find_all("script", src=True):
        src = script.get("src", "").lower()
        if any(kw in src for kw in ["cookie", "consent", "gdpr", "dsgvo", "cmplz", "cli-"]):
            return True, f"Cookie-Script gefunden"

    for tag in soup.find_all(attrs={"id": True}):
        if any(kw in tag.get("id", "").lower() for kw in ["cookie", "consent", "gdpr", "dsgvo"]):
            return True, f"Consent-Element gefunden"

    for tag in soup.find_all(["script", "link"], href=True):
        href = tag.get("href", "").lower()
        if "plugins" in href and any(kw in href for kw in ["cookie", "consent", "gdpr", "cmplz"]):
            return True, "WordPress Cookie-Plugin erkannt"

    return False, ""


def check_cookie_banner_with_claude(html: str, base_url: str = "") -> tuple:
    """Use Claude API to intelligently detect cookie consent mechanisms."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None, "Claude API Key nicht konfiguriert"

    html_snippet = html[:10000]

    prompt = f"""Du bist ein DSGVO-Experte und analysierst den HTML-Quelltext einer deutschen Webseite.

Frage: Hat diese Webseite ein Cookie-Consent-Banner oder eine Einwilligungslösung?

Suche nach diesen Hinweisen im HTML:
1. Script-Dateipfade die auf Cookie-Plugins hinweisen (z.B. /plugins/complianz/, /plugins/cookie-law-info/, /plugins/borlabs/, cookiebot.com, usercentrics.eu)
2. JavaScript-Variablen oder Funktionen wie cmplz_, cli_, CookieConsent, Cookiebot, uc_settings
3. CSS-Klassen oder IDs wie cookie-banner, consent-overlay, gdpr-popup, cmplz-
4. Inline-Styles oder div-Elemente mit cookie/consent im Namen

Wichtig: Auch wenn der Banner per JavaScript nachgeladen wird, hinterlässt das Plugin immer Spuren im HTML (Script-Tags, CSS-Klassen, Inline-Variablen).

HTML:
{html_snippet}

Antworte NUR mit purem JSON ohne Markdown:
{{"found": true/false, "reason": "Begründung auf Deutsch (max 120 Zeichen)"}}"""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 150,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=20
        )
        data = resp.json()
        text = data["content"][0]["text"].strip()
        text = re.sub(r"```json\s*|\s*```", "", text).strip()
        result = json.loads(text)
        return result.get("found", False), result.get("reason", "")
    except Exception as e:
        return None, str(e)


def check_cookie_banner(soup, html: str, base_url: str = "") -> dict:
    if not soup:
        return {"check": "Cookie-Banner", "icon": "🍪", "passed": False, "detail": "Seite konnte nicht geladen werden.", "weight": 20}

    # 1. Schneller Keyword-Check zuerst (kostenlos)
    keyword_found, keyword_reason = check_cookie_banner_keywords(html, soup)
    if keyword_found:
        return {"check": "Cookie-Banner", "icon": "🍪", "passed": True, "detail": f"Cookie-Consent erkannt: {keyword_reason}", "weight": 20}

    # 2. Claude API mit Datenschutzseite als zusätzlichem Signal
    claude_found, claude_reason = check_cookie_banner_with_claude(html, base_url)

    if claude_found is True:
        return {"check": "Cookie-Banner", "icon": "🍪", "passed": True, "detail": f"Cookie-Consent erkannt (KI-Analyse): {claude_reason}", "weight": 20}
    elif claude_found is False:
        return {"check": "Cookie-Banner", "icon": "🍪", "passed": False, "detail": f"Kein Cookie-Banner gefunden: {claude_reason}", "weight": 20}
    else:
        return {
            "check": "Cookie-Banner", "icon": "🍪",
            "passed": False,
            "passed_with_warning": True,
            "detail": "Prüfung nicht möglich – bitte manuell prüfen.",
            "weight": 20
        }


def get_base_url(url: str) -> str:
    from urllib.parse import urlparse
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def probe_url(url: str) -> bool:
    """Check if a URL returns a successful response."""
    try:
        r = requests.head(url, headers=HEADERS, timeout=5, allow_redirects=True)
        return r.status_code < 400
    except:
        return False


def check_privacy_policy(soup, base_url: str) -> dict:
    if not soup:
        return {"check": "Datenschutzerklärung", "icon": "📄", "passed": False, "detail": "Seite konnte nicht geladen werden.", "weight": 20}

    html_lower = str(soup).lower()

    # 1. Check raw HTML for keywords
    found_on_page = any(kw in html_lower for kw in PRIVACY_KEYWORDS)

    # 2. Check all links (href + link text)
    link_found = False
    link_url = ""
    for link in soup.find_all("a", href=True):
        href = link.get("href", "").lower()
        text = link.get_text(strip=True).lower()
        if any(kw in href or kw in text for kw in PRIVACY_KEYWORDS):
            link_found = True
            link_url = link.get("href", "")
            break

    # 3. Probe common privacy URLs if nothing found yet
    probed = False
    if not found_on_page and not link_found:
        base = get_base_url(base_url)
        for path in PRIVACY_URL_PATHS:
            if probe_url(base + path):
                probed = True
                link_url = base + path
                break

    passed = found_on_page or link_found or probed
    if passed:
        detail = f"Datenschutzerklärung gefunden{': ' + link_url if link_url else ''}."
    else:
        detail = "Kein Link zu einer Datenschutzerklärung gefunden – nach DSGVO Art. 13 Pflicht."
    return {"check": "Datenschutzerklärung", "icon": "📄", "passed": passed, "detail": detail, "weight": 20}


def check_imprint(soup, base_url: str) -> dict:
    if not soup:
        return {"check": "Impressum", "icon": "🏢", "passed": False, "detail": "Seite konnte nicht geladen werden.", "weight": 10}

    html_lower = str(soup).lower()

    # 1. Check raw HTML for keywords
    found_on_page = any(kw in html_lower for kw in IMPRINT_KEYWORDS)

    # 2. Check all links (href + link text)
    link_found = False
    link_url = ""
    for link in soup.find_all("a", href=True):
        href = link.get("href", "").lower()
        text = link.get_text(strip=True).lower()
        if any(kw in href or kw in text for kw in IMPRINT_KEYWORDS):
            link_found = True
            link_url = link.get("href", "")
            break

    # 3. Probe common imprint URLs if nothing found yet
    probed = False
    if not found_on_page and not link_found:
        base = get_base_url(base_url)
        for path in IMPRINT_URL_PATHS:
            if probe_url(base + path):
                probed = True
                link_url = base + path
                break

    passed = found_on_page or link_found or probed
    if passed:
        detail = f"Impressum gefunden{': ' + link_url if link_url else ''}."
    else:
        detail = "Kein Impressum gefunden – nach § 5 TMG in Deutschland Pflicht."
    return {"check": "Impressum", "icon": "🏢", "passed": passed, "detail": detail, "weight": 10}


def check_trackers(html: str) -> dict:
    found_trackers = []
    for name, patterns in TRACKER_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, html, re.IGNORECASE):
                found_trackers.append(name)
                break

    has_trackers = len(found_trackers) > 0
    if has_trackers:
        detail = f"Erkannte Tracker: {', '.join(found_trackers)}. Ohne Einwilligung nicht DSGVO-konform."
    else:
        detail = "Keine bekannten Tracker-Skripte gefunden."
    return {
        "check": "Tracker / Analytics",
        "icon": "📊",
        "passed": not has_trackers,
        "passed_with_warning": has_trackers,
        "detail": detail,
        "weight": 15,
        "trackers": found_trackers
    }


def check_third_party_scripts(soup, html: str) -> dict:
    if not soup:
        return {"check": "Drittanbieter-Skripte", "icon": "🔗", "passed": False, "detail": "Seite konnte nicht geladen werden.", "weight": 15}

    known_third_party = [
        ("Google Fonts", r"fonts\.googleapis\.com|fonts\.gstatic\.com"),
        ("Google Maps", r"maps\.googleapis\.com|maps\.google\.com"),
        ("YouTube", r"youtube\.com/embed|youtube-nocookie\.com"),
        ("Vimeo", r"player\.vimeo\.com"),
        ("jQuery CDN", r"code\.jquery\.com|ajax\.googleapis\.com/ajax/libs/jquery"),
        ("reCAPTCHA", r"google\.com/recaptcha"),
        ("WhatsApp Widget", r"wa\.me|whatsapp\.com"),
        ("Instagram", r"instagram\.com/embed"),
    ]

    found = []
    for name, pattern in known_third_party:
        if re.search(pattern, html, re.IGNORECASE):
            found.append(name)

    has_scripts = len(found) > 0
    if has_scripts:
        detail = f"Gefunden: {', '.join(found)}. Diese laden Ressourcen von externen Servern – ggf. Einwilligung erforderlich."
    else:
        detail = "Keine auffälligen Drittanbieter-Einbindungen erkannt."

    return {
        "check": "Drittanbieter-Skripte",
        "icon": "🔗",
        "passed": not has_scripts,
        "passed_with_warning": has_scripts,
        "detail": detail,
        "weight": 15,
        "scripts": found
    }


def calculate_score(results: list) -> int:
    total_weight = sum(r["weight"] for r in results)
    earned = 0
    for r in results:
        if r.get("passed") and not r.get("passed_with_warning"):
            earned += r["weight"]
        elif r.get("passed_with_warning"):
            earned += r["weight"] * 0.4  # partial credit for warnings
    return round((earned / total_weight) * 100) if total_weight > 0 else 0


def get_traffic_light(score: int) -> str:
    if score >= 75:
        return "green"
    elif score >= 45:
        return "yellow"
    else:
        return "red"


@app.route("/check", methods=["GET", "POST"])
def check():
    if request.method == "POST":
        data = request.get_json(force=True) or {}
        url = data.get("url", "").strip()
    else:
        url = request.args.get("url", "").strip()

    if not url:
        return jsonify({"error": "Keine URL angegeben."}), 400

    if not url.startswith("http"):
        url = "https://" + url

    resp, soup, html, error = fetch_page(url)

    if error:
        return jsonify({"error": f"Seite konnte nicht erreicht werden: {error}"}), 400

    results = [
        check_ssl(url),
        check_cookie_banner(soup, html, get_base_url(url)),
        check_privacy_policy(soup, url),
        check_imprint(soup, url),
        check_trackers(html),
        check_third_party_scripts(soup, html),
    ]

    score = calculate_score(results)
    traffic_light = get_traffic_light(score)

    return jsonify({
        "url": url,
        "score": score,
        "traffic_light": traffic_light,
        "results": results
    })


@app.route("/debug-cookie", methods=["GET"])
def debug_cookie():
    url = request.args.get("url", "https://dietzwetzel.de")
    _, soup, html, error = fetch_page(url)
    if error:
        return jsonify({"error": error})

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    html_snippet = html[:8000]

    prompt = f"""Analysiere diesen HTML-Quelltext einer Webseite und beantworte: Gibt es Hinweise auf ein Cookie-Consent-Banner?
Antworte NUR mit einem JSON-Objekt: {{"found": true/false, "reason": "kurze Begründung"}}
HTML:
{html_snippet}"""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=15
        )
        raw = resp.json()
        text = raw["content"][0]["text"].strip()
        return jsonify({
            "status_code": resp.status_code,
            "raw_text": text,
            "parsed": json.loads(text) if text.startswith("{") else "JSON parse fehler"
        })
    except Exception as e:
        return jsonify({"exception": str(e)})



@app.route("/debug", methods=["GET"])
def debug():
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"api_key": "FEHLT", "status": "kein Key gefunden"})

    # Test Claude API
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 20,
                "messages": [{"role": "user", "content": "Antworte nur: OK"}]
            },
            timeout=15
        )
        return jsonify({
            "api_key": f"{api_key[:8]}...",
            "status_code": resp.status_code,
            "response": resp.json()
        })
    except Exception as e:
        return jsonify({"api_key": f"{api_key[:8]}...", "error": str(e)})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
