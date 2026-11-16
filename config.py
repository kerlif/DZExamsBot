"""
DzExams Telegram Bot — configuration
جميع الإعدادات قابلة للتعديل عبر متغيرات البيئة أو ملف .env
"""
import os
import re

try:
    from dotenv import load_dotenv
    # Load .env from the directory this file lives in (project root)
    _env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(_env_path):
        load_dotenv(_env_path)
except ImportError:
    pass


def _int_list(raw: str):
    return [int(x) for x in re.findall(r"\d+", raw or "")]


# ─── Telegram ────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = set(_int_list(os.getenv("ADMIN_IDS", "")))

# ─── Site access (Cloudflare bypass) ─────────────────────────────────────
BASE_URL = "https://www.dzexams.com"

# The verified bypass User-Agent: robots.txt fully allows this AdSense crawler
# agent, so Cloudflare WAF passes it through without a JS challenge.
BYPASS_UA = "Mediapartners-Google"

# سلسلة User-Agent للتجاوز: الأساسي أولًا، والباقي احتياط عند حجب الأساسي.
UA_CHAIN = [
    "Mediapartners-Google",
    "AdsBot-Google-Mobile",
    "Googlebot/2.1 (+http://www.google.com/bot.html)",
]

# Fallback UA used only for the optional stealth-browser flow.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

ACCEPT_LANGUAGE = "ar,en;q=0.8,fr;q=0.6"

# ─── Rate limiting / politeness ──────────────────────────────────────────
MAX_CONCURRENT_REQUESTS = int(os.getenv("MAX_CONCURRENT_REQUESTS", "1"))
REQ_MIN_INTERVAL = float(os.getenv("REQ_MIN_INTERVAL", "1.2"))   # seconds between requests
REQ_TIMEOUT = int(os.getenv("REQ_TIMEOUT", "25"))                # seconds
PDF_TIMEOUT = int(os.getenv("PDF_TIMEOUT", "90"))                # seconds for PDF downloads

# ─── Caching ─────────────────────────────────────────────────────────────
CACHE_TTL = int(os.getenv("CACHE_TTL", "1800"))                  # HTML cache TTL (s)
CACHE_MAX = int(os.getenv("CACHE_MAX", "400"))                   # max cached entries

# ─── WARP / IP rotation ──────────────────────────────────────────────────
# Trigger exit-IP rotation after N consecutive blocked (403) responses.
WARP_FAIL_THRESHOLD = int(os.getenv("WARP_FAIL_THRESHOLD", "3"))
# warp-cli binary (official Cloudflare WARP client). Leave empty for auto-detect.
WARP_CLI_PATH = os.getenv("WARP_CLI_PATH", "")
# Glob pattern for wgcf-generated WireGuard profiles, e.g. "/etc/wireguard/warp*.conf".
# Rotation cycles between the matched profiles. Leave empty to disable.
WARP_WG_PROFILES = os.getenv("WARP_WG_PROFILES", "/etc/wireguard/warp*.conf")
# Comma-separated HTTP/SOCKS proxies as ultimate fallback, e.g.
# "socks5://user:pass@host:1080, http://host:8080". Empty = disabled.
PROXY_LIST = [p.strip() for p in os.getenv("PROXY_LIST", "").split(",") if p.strip()]

# ─── Optional stealth browser (LiteSpeed Bot-Verification solver) ────────
PLAYWRIGHT_ENABLED = os.getenv("PLAYWRIGHT_ENABLED", "0") == "1"
PLAYWRIGHT_HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "1") == "1"

# ─── Storage ─────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.getenv("DB_PATH", os.path.join(BASE_DIR, "dzexams.db"))
TMP_DIR = os.getenv("TMP_DIR", "/tmp/dzexams_bot")  # nosec B108: app-created dir, files deleted after use

# ─── Background indexing ─────────────────────────────────────────────────
# Comma-separated levels auto-indexed daily ("" = disabled), e.g. "bac,bem,3as"
AUTO_INDEX_LEVELS = [lv.strip() for lv in os.getenv("AUTO_INDEX_LEVELS", "").split(",") if lv.strip()]
AUTO_INDEX_HOUR = int(os.getenv("AUTO_INDEX_HOUR", "3"))         # hour of day (server TZ)
INDEX_SECTION_DELAY = float(os.getenv("INDEX_SECTION_DELAY", "1.5"))

# ─── UI ──────────────────────────────────────────────────────────────────
ITEMS_PER_PAGE = int(os.getenv("ITEMS_PER_PAGE", "8"))
SUBJECTS_PER_PAGE = int(os.getenv("SUBJECTS_PER_PAGE", "20"))

# مهلة بين تحميلين لنفس المستخدم (ثوانٍ) — حماية من إغراق الموقع
USER_DL_COOLDOWN = int(os.getenv("USER_DL_COOLDOWN", "10"))
# حد تيليغرام لإرسال الملفات: ما فوقه يُرسل كرابط فقط
TELEGRAM_DOC_LIMIT = 49 * 1024 * 1024
