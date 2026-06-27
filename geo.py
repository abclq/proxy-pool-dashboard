# -*- coding: utf-8 -*-
"""GeoIP resolver — online APIs + Redis cache (no local DB).

resolve() / resolve_region() → Redis cache only (instant).
Background thread: geo_fill() → batch-resolves uncached IPs via ip-api.com.
Source-provided geo (hideip.me etc.) → inject_geo() → pre-populates cache.
"""

import json, os, time, threading
import redis
import urllib.request

REDIS_HOST = os.environ.get("REDIS_HOST", "proxy-redis")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))

_r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=1, decode_responses=True)
_lock = threading.Lock()

GEO_TTL = 2592000  # 30 days

# ── Country code → Chinese name (compact, essential) ──
COUNTRY_CODE = {
    "CN": "中国", "US": "美国", "JP": "日本", "KR": "韩国", "GB": "英国",
    "DE": "德国", "FR": "法国", "CA": "加拿大", "AU": "澳大利亚", "RU": "俄罗斯",
    "IN": "印度", "BR": "巴西", "ID": "印尼", "NG": "尼日利亚", "ZA": "南非",
    "SG": "新加坡", "MY": "马来西亚", "TH": "泰国", "VN": "越南", "PH": "菲律宾",
    "MM": "缅甸", "KH": "柬埔寨", "LA": "老挝", "BD": "孟加拉国", "PK": "巴基斯坦",
    "IR": "伊朗", "TR": "土耳其", "SA": "沙特阿拉伯", "AE": "阿联酋",
    "EG": "埃及", "KE": "肯尼亚", "TZ": "坦桑尼亚", "UG": "乌干达",
    "GH": "加纳", "CI": "科特迪瓦", "SN": "塞内加尔",
    "CM": "喀麦隆", "AO": "安哥拉", "ZM": "赞比亚", "ZW": "津巴布韦",
    "DZ": "阿尔及利亚", "MA": "摩洛哥", "TN": "突尼斯",
    "MX": "墨西哥", "AR": "阿根廷", "CO": "哥伦比亚", "VE": "委内瑞拉",
    "CL": "智利", "PE": "秘鲁", "EC": "厄瓜多尔", "BO": "玻利维亚",
    "PY": "巴拉圭", "UY": "乌拉圭", "CR": "哥斯达黎加",
    "IT": "意大利", "ES": "西班牙", "NL": "荷兰", "BE": "比利时",
    "SE": "瑞典", "CH": "瑞士", "PL": "波兰", "UA": "乌克兰",
    "CZ": "捷克", "RO": "罗马尼亚", "HU": "匈牙利", "AT": "奥地利",
    "PT": "葡萄牙", "GR": "希腊", "IE": "爱尔兰", "DK": "丹麦",
    "FI": "芬兰", "NO": "挪威", "IL": "以色列",
    "HK": "香港", "TW": "台湾", "MO": "澳门",
}


def resolve_region(ip):
    """Return ISO country code (cached only, instant)."""
    data = _cached(ip)
    return data.get("countryCode", "?") if data else "?"


def resolve(ip):
    """Return Chinese location name (cached only, instant)."""
    data = _cached(ip)
    if not data or data.get("_placeholder"):
        return "..."

    cc = data.get("countryCode", "")
    if cc in ("HK", "TW", "MO"):
        return COUNTRY_CODE.get(cc, data.get("country", "?"))

    if cc == "CN":
        prov = data.get("regionName", "")
        city = data.get("city", "")
        _junk = ("阿里", "腾讯", "百度", "华为", "电信", "联通", "移动", "云")
        if city and any(j in city for j in _junk):
            city = ""
        # Filter non-Chinese city names (ip-api.com sometimes returns English network names)
        if city:
            has_cjk = any('\u4e00' <= c <= '\u9fff' for c in city)
            if not has_cjk and any(c.isascii() and c.isalpha() for c in city):
                city = ""
        if prov and city:
            return f"{prov}{city}"
        if prov:
            return prov
        if city:
            return city
        return "中国"

    return COUNTRY_CODE.get(cc, data.get("country", "?"))


def _cached(ip):
    """Redis cache lookup. Returns None if not cached."""
    if not ip or ip == "0.0.0.0":
        return None
    try:
        raw = _r.get(f"geo:{ip}")
        if raw:
            return json.loads(raw)
    except (json.JSONDecodeError, Exception):
        pass
    return None


def inject_geo(ip, country_code, country_name="", region="", city=""):
    """Inject source-provided geo data into cache."""
    data = {
        "country": country_name or COUNTRY_CODE.get(country_code, country_code),
        "countryCode": country_code,
        "regionName": region,
        "city": city,
        "query": ip,
        "source": "injected",
    }
    try:
        _r.setex(f"geo:{ip}", GEO_TTL, json.dumps(data, ensure_ascii=False))
    except Exception:
        pass


# ── Background geo filler ──
_FILL_INTERVAL = 120  # seconds between fill cycles
_fill_thread = None
_fill_running = False


def start_filler():
    """Start background thread that fills missing geo data."""
    global _fill_thread, _fill_running
    if _fill_running:
        return
    _fill_running = True
    _fill_thread = threading.Thread(target=_fill_loop, daemon=True)
    _fill_thread.start()


def _fill_loop():
    while _fill_running:
        try:
            _fill_cycle()
        except Exception:
            pass
        time.sleep(_FILL_INTERVAL)


def _fill_cycle():
    """One fill cycle: collect uncached IPs → batch resolve → cache."""
    # Collect IPs from pool that have no geo cache
    uncached = []
    try:
        # Scan pool members
        for member in _r.zscan_iter("proxies:pool"):
            ip = member[0].split(":")[0] if ":" in member[0] else member[0]
            if not _r.exists(f"geo:{ip}"):
                uncached.append(ip)
    except Exception:
        return

    if not uncached:
        return

    # Batch resolve via ip-api.com (45 IPs/min)
    batch_size = 45
    resolved = 0
    for i in range(0, len(uncached), batch_size):
        batch = uncached[i : i + batch_size]
        try:
            payload = json.dumps([{"query": ip} for ip in batch])
            req = urllib.request.Request(
                "http://ip-api.com/batch?lang=zh-CN",
                data=payload.encode(),
                headers={"Content-Type": "application/json"},
            )
            resp = urllib.request.urlopen(req, timeout=15)
            raw = json.loads(resp.read().decode())
            for item in raw:
                ip = item.get("query", "")
                if item.get("status") == "success":
                    _r.setex(f"geo:{ip}", GEO_TTL, json.dumps(item, ensure_ascii=False))
                    resolved += 1
                else:
                    # Mark as failed to avoid retry on every cycle
                    _r.setex(f"geo:{ip}", GEO_TTL, json.dumps({"_placeholder": True, "query": ip}))
            time.sleep(60)  # rate limit: 45/min
        except Exception:
            time.sleep(10)

    # Also try ip2location.io for any still-uncached
    remaining = [ip for ip in uncached if not _r.exists(f"geo:{ip}")]
    for i in range(0, len(remaining), 45):
        batch = remaining[i : i + 45]
        for ip in batch:
            try:
                req = urllib.request.Request(f"https://api.ip2location.io/?ip={ip}")
                resp = urllib.request.urlopen(req, timeout=5)
                data = json.loads(resp.read().decode())
                _r.setex(
                    f"geo:{ip}",
                    GEO_TTL,
                    json.dumps({
                        "status": "success",
                        "country": data.get("country_name", ""),
                        "countryCode": data.get("country_code", ""),
                        "regionName": data.get("region_name", ""),
                        "city": data.get("city_name", ""),
                        "query": ip,
                        "source": "ip2lio",
                    }, ensure_ascii=False),
                )
                resolved += 1
            except Exception:
                pass


# ── Backward compat stubs ──
def _init():
    start_filler()
_searcher = None


# ── Source geo injection helpers ──
def _name_to_code(name):
    """Map country name → ISO code."""
    name = name.strip().lower()
    if len(name) == 2 and name.upper() in COUNTRY_CODE:
        return name.upper()
    mapping = {
        "united states": "US", "usa": "US", "us": "US",
        "china": "CN", "russia": "RU", "france": "FR",
        "germany": "DE", "japan": "JP", "korea": "KR",
        "united kingdom": "GB", "uk": "GB", "england": "GB",
        "canada": "CA", "australia": "AU", "brazil": "BR",
        "india": "IN", "indonesia": "ID", "singapore": "SG",
        "netherlands": "NL", "holland": "NL",
        "sweden": "SE", "switzerland": "CH", "spain": "ES",
        "italy": "IT", "poland": "PL", "ukraine": "UA",
        "taiwan": "TW", "hong kong": "HK", "hongkong": "HK",
        "iran": "IR", "turkey": "TR", "thailand": "TH",
        "vietnam": "VN", "malaysia": "MY", "philippines": "PH",
        "mexico": "MX", "argentina": "AR", "colombia": "CO",
        "chile": "CL", "peru": "PE", "egypt": "EG",
        "south africa": "ZA", "south korea": "KR",
        "czech": "CZ", "romania": "RO", "hungary": "HU",
        "austria": "AT", "portugal": "PT", "greece": "GR",
        "ireland": "IE", "denmark": "DK", "finland": "FI",
        "norway": "NO", "belgium": "BE", "israel": "IL",
        "uae": "AE", "saudi": "SA", "pakistan": "PK",
        "bangladesh": "BD", "nigeria": "NG", "kenya": "KE",
    }
    return mapping.get(name)
