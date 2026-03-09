from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import re

app = Flask(__name__)
CORS(app)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DSGVOChecker/1.0; +https://dsgvo-checker.de)"
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
    "cookiebot", "usercentrics", "borlabs", "onetrust", "trustarc",
    "cookiefirst", "klaro", "complianz", "cookieconsent", "cookie-banner",
    "cookie_notice", "cookie-law", "gdpr-cookie", "wp-gdpr",
    "cookie consent", "cookie banner"
]

PRIVACY_KEYWORDS = [
    "datenschutz", "datenschutzerklärung", "privacy policy",
    "datenschutzhinweise", "privacy", "dsgvo", "gdpr"
]

IMPRINT_KEYWORDS = [
    "impressum", "imprint", "legal notice", "anbieterkennzeichnung"
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


def check_cookie_banner(soup, html: str) -> dict:
    if not soup:
        return {"check": "Cookie-Banner", "icon": "🍪", "passed": False, "detail": "Seite konnte nicht geladen werden.", "weight": 20}

    html_lower = html.lower()
    found = []

    for kw in COOKIE_KEYWORDS:
        if kw in html_lower:
            found.append(kw)

    for tag in soup.find_all(attrs={"id": True}):
        tag_id = tag.get("id", "").lower()
        if any(kw in tag_id for kw in ["cookie", "consent", "gdpr", "dsgvo"]):
            found.append(f"id='{tag.get('id')}'")

    for tag in soup.find_all(attrs={"class": True}):
        classes = " ".join(tag.get("class", [])).lower()
        if any(kw in classes for kw in ["cookie", "consent", "gdpr", "dsgvo"]):
            found.append("CSS-Klasse mit Cookie/Consent-Bezug")

    passed = len(found) > 0
    unique = list(dict.fromkeys(found))[:3]
    detail = f"Gefunden: {', '.join(unique)}" if passed else "Kein Cookie-Consent-Banner erkannt. Nach DSGVO bei nicht-essentiellen Cookies Pflicht."
    return {"check": "Cookie-Banner", "icon": "🍪", "passed": passed, "detail": detail, "weight": 20}


def check_privacy_policy(soup, base_url: str) -> dict:
    if not soup:
        return {"check": "Datenschutzerklärung", "icon": "📄", "passed": False, "detail": "Seite konnte nicht geladen werden.", "weight": 20}

    html_lower = str(soup).lower()
    found_on_page = any(kw in html_lower for kw in PRIVACY_KEYWORDS)

    # Check links
    links = soup.find_all("a", href=True)
    link_found = False
    for link in links:
        href = link.get("href", "").lower()
        text = link.get_text().lower()
        if any(kw in href or kw in text for kw in PRIVACY_KEYWORDS):
            link_found = True
            break

    passed = found_on_page or link_found
    detail = "Link zur Datenschutzerklärung gefunden." if passed else "Kein Link zu einer Datenschutzerklärung gefunden – nach DSGVO Art. 13 Pflicht."
    return {"check": "Datenschutzerklärung", "icon": "📄", "passed": passed, "detail": detail, "weight": 20}


def check_imprint(soup, base_url: str) -> dict:
    if not soup:
        return {"check": "Impressum", "icon": "🏢", "passed": False, "detail": "Seite konnte nicht geladen werden.", "weight": 10}

    html_lower = str(soup).lower()
    found_on_page = any(kw in html_lower for kw in IMPRINT_KEYWORDS)

    links = soup.find_all("a", href=True)
    link_found = False
    for link in links:
        href = link.get("href", "").lower()
        text = link.get_text().lower()
        if any(kw in href or kw in text for kw in IMPRINT_KEYWORDS):
            link_found = True
            break

    passed = found_on_page or link_found
    detail = "Impressum-Link gefunden." if passed else "Kein Impressum gefunden – nach § 5 TMG in Deutschland Pflicht."
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
        ("Cloudflare CDN", r"cdnjs\.cloudflare\.com"),
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
        check_cookie_banner(soup, html),
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


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
