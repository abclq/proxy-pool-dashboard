# -*- coding: utf-8 -*-
"""GeoIP resolver - online APIs + local binary DB + Redis cache + proxy-pool rotation.

Local offline DB: data/ipdb.bin (DB-IP Country Lite, binary format).
Loads on startup, updates monthly. 二分查找, ~1μs per lookup.
Cache: Redis DB1 geo:<ip> + proxy:<ip:port> country/location.
API requests rotate through proxy pool to avoid rate limits.
"""

import json, os, time, threading, random, struct, socket
import redis
import urllib.request
import urllib.parse
import urllib.error

REDIS_HOST = os.environ.get("REDIS_HOST", "proxy-redis")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
_r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=1, decode_responses=True)

GEO_TTL = 7 * 24 * 3600
FILL_INTERVAL = 600
BATCH_SIZE = 45
HTTP_TIMEOUT = 12
LOCAL_DB_PATH = os.environ.get("IPDB_PATH", os.path.join(os.path.dirname(__file__), "data", "ipdb.bin"))
LOCAL_DB_UPDATE_URL = "https://download.db-ip.com/free/dbip-country-lite-{year}-{month:02d}.csv.gz"
LOCAL_DB_UPDATE_INTERVAL = 30 * 24 * 3600  # 每月更新

COUNTRY_CODE = {
    "CN": "中国", "US": "美国", "JP": "日本", "KR": "韩国", "GB": "英国",
    "DE": "德国", "FR": "法国", "CA": "加拿大", "AU": "澳大利亚", "RU": "俄罗斯",
    "IN": "印度", "BR": "巴西", "ID": "印尼", "NG": "尼日利亚", "ZA": "南非",
    "SG": "新加坡", "MY": "马来西亚", "TH": "泰国", "VN": "越南", "PH": "菲律宾",
    "MM": "缅甸", "KH": "柬埔寨", "LA": "老挝", "BD": "孟加拉国", "PK": "巴基斯坦",
    "IR": "伊朗", "TR": "土耳其", "SA": "沙特阿拉伯", "AE": "阿联酋",
    "EG": "埃及", "KE": "肯尼亚", "TZ": "坦桑尼亚", "UG": "乌干达",
    "GH": "加纳", "CI": "科特迪瓦", "SN": "塞内加尔", "CM": "喀麦隆",
    "AO": "安哥拉", "ZM": "赞比亚", "ZW": "津巴布韦", "DZ": "阿尔及利亚",
    "MA": "摩洛哥", "TN": "突尼斯", "MX": "墨西哥", "AR": "阿根廷",
    "CO": "哥伦比亚", "VE": "委内瑞拉", "CL": "智利", "PE": "秘鲁",
    "EC": "厄瓜多尔", "BO": "玻利维亚", "PY": "巴拉圭", "UY": "乌拉圭",
    "CR": "哥斯达黎加", "IT": "意大利", "ES": "西班牙", "NL": "荷兰",
    "BE": "比利时", "SE": "瑞典", "CH": "瑞士", "PL": "波兰",
    "UA": "乌克兰", "CZ": "捷克", "RO": "罗马尼亚", "HU": "匈牙利",
    "AT": "奥地利", "PT": "葡萄牙", "GR": "希腊", "IE": "爱尔兰",
    "DK": "丹麦", "FI": "芬兰", "NO": "挪威", "IL": "以色列",
    "HK": "香港", "TW": "台湾", "MO": "澳门",
}

# ═══════════ Local Binary IP Database (offline, ~1μs lookup) ═══════════

_local_db = {"entries": [], "loaded": False, "lock": threading.Lock()}


def _load_local_db(path=None):
    """Load binary IP range database into memory. 3.5MB for 355K entries."""
    path = path or LOCAL_DB_PATH
    with _local_db["lock"]:
        if _local_db["loaded"]:
            return True
        if not os.path.exists(path):
            return False
        try:
            with open(path, "rb") as f:
                count = struct.unpack("<I", f.read(4))[0]
                entries = []
                for _ in range(count):
                    data = f.read(10)
                    if len(data) < 10:
                        break
                    start_int = struct.unpack("<I", data[0:4])[0]
                    end_int = struct.unpack("<I", data[4:8])[0]
                    country = data[8:10].decode("ascii").rstrip("\x00")
                    entries.append((start_int, end_int, country))
            entries.sort(key=lambda x: x[0])
            _local_db["entries"] = entries
            _local_db["loaded"] = True
            return True
        except Exception:
            return False


def _local_lookup(ip_str):
    """Binary search for country code. Returns 'ZZ' if not found."""
    if not _local_db["loaded"]:
        _load_local_db()
    entries = _local_db["entries"]
    if not entries:
        return "ZZ"
    try:
        target = struct.unpack("!I", socket.inet_aton(ip_str))[0]
    except Exception:
        return "ZZ"
    lo, hi = 0, len(entries) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        s, e, c = entries[mid]
        if target < s:
            hi = mid - 1
        elif target > e:
            lo = mid + 1
        else:
            return c
    return "ZZ"


def _update_local_db():
    """Download latest DB-IP Country Lite CSV and rebuild binary DB."""
    import gzip, datetime
    now = datetime.datetime.utcnow()
    url = LOCAL_DB_UPDATE_URL.format(year=now.year, month=now.month)
    csv_gz_path = "/tmp/dbip-latest.csv.gz"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "proxy-dashboard/geo-updater"})
        raw = urllib.request.urlopen(req, timeout=60).read()
        with open(csv_gz_path, "wb") as f:
            f.write(raw)
    except Exception:
        return False
    entries = []
    try:
        with gzip.open(csv_gz_path, "rt", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(",")
                if len(parts) < 3 or ":" in parts[0]:
                    continue
                try:
                    start_int = struct.unpack("!I", socket.inet_aton(parts[0]))[0]
                    end_int = struct.unpack("!I", socket.inet_aton(parts[1]))[0]
                except Exception:
                    continue
                entries.append((start_int, end_int, parts[2]))
    except Exception:
        return False
    if len(entries) < 100000:
        return False
    entries.sort(key=lambda x: x[0])
    tmp_path = LOCAL_DB_PATH + ".tmp"
    try:
        with open(tmp_path, "wb") as f:
            f.write(struct.pack("<I", len(entries)))
            for start_int, end_int, country in entries:
                country_bytes = country.encode("ascii")[:2].ljust(2, b"\x00")
                f.write(struct.pack("<II", start_int, end_int))
                f.write(country_bytes)
        os.rename(tmp_path, LOCAL_DB_PATH)
        with _local_db["lock"]:
            _local_db["entries"] = entries
            _local_db["loaded"] = True
        return True
    except Exception:
        return False


def _local_db_updater_loop():
    """Background thread: update local DB monthly."""
    while True:
        try:
            _update_local_db()
        except Exception:
            pass
        time.sleep(LOCAL_DB_UPDATE_INTERVAL)


# ═══════════ API resolver + Redis cache ═══════════

_fill_thread = None
_fill_running = False
_proxy_cache = {"t": 0, "d": []}


def resolve(ip):
    # 1. Local binary DB (fastest, offline)
    cc = _local_lookup(ip)
    if cc != "ZZ":
        # Check Redis cache for richer location (province+city)
        data = _cached(ip)
        if data and not data.get("_placeholder"):
            loc = _format_location(data)
            if loc and loc != COUNTRY_CODE.get(cc, cc):
                return loc  # 有城市信息优先用
        country = COUNTRY_CODE.get(cc, cc)
        return country

    # 2. Redis cache
    data = _cached(ip)
    if data and not data.get("_placeholder"):
        return _format_location(data)

    # 3. Enqueue for online API lookup
    _enqueue_ip(ip)
    return "..."


def resolve_region(ip):
    # 1. Local binary DB
    cc = _local_lookup(ip)
    if cc != "ZZ":
        return cc

    # 2. Redis cache
    data = _cached(ip)
    if not data or data.get("_placeholder"):
        _enqueue_ip(ip)
        return "?"
    return data.get("countryCode", "?") or "?"


def resolve_and_store(ip, proxy_key=None, force=False):
    """Resolve one IP now and write geo cache + optional proxy hash fields."""
    if not force:
        data = _cached(ip)
        if data and not data.get("_placeholder"):
            if proxy_key:
                _write_proxy_geo(proxy_key, data)
            return data
        # Check local DB
        cc = _local_lookup(ip)
        if cc != "ZZ":
            data = {"status": "success", "country": COUNTRY_CODE.get(cc, cc),
                    "countryCode": cc, "regionName": "", "city": "",
                    "query": ip, "source": "local-db",
                    "geo_updated_at": 0 if cc == "CN" else int(time.time())}
            _store(ip, data)
            if proxy_key:
                _write_proxy_geo(proxy_key, data)
            # For CN IPs, enqueue online lookup to get city-level detail
            if cc == "CN":
                _enqueue_ip(ip)
            return data
    data = _query_one(ip)
    if data:
        _store(ip, data)
        if proxy_key:
            _write_proxy_geo(proxy_key, data)
    return data


def inject_geo(ip, country_code, country_name="", region="", city=""):
    cc = (country_code or "").upper()
    data = {
        "status": "success", "country": country_name or COUNTRY_CODE.get(cc, cc),
        "countryCode": cc, "regionName": region or "", "city": city or "",
        "query": ip, "source": "injected", "geo_updated_at": int(time.time()),
    }
    _store(ip, data)


def _format_location(data):
    cc = (data.get("countryCode") or "").upper()
    if cc in ("HK", "TW", "MO"):
        return COUNTRY_CODE.get(cc, data.get("country", "?"))
    if cc == "CN":
        prov = data.get("regionName", "") or ""
        city = data.get("city", "") or ""
        junk = ("阿里", "腾讯", "百度", "华为", "电信", "联通", "移动", "云")
        if city and any(j in city for j in junk):
            city = ""
        if city:
            has_cjk = any('\u4e00' <= c <= '\u9fff' for c in city)
            if not has_cjk and any(c.isascii() and c.isalpha() for c in city):
                city = ""
        if prov:
            has_cjk = any('\u4e00' <= c <= '\u9fff' for c in prov)
            if not has_cjk and any(c.isascii() and c.isalpha() for c in prov):
                prov = ""  # filter English province names like "Zhejiang"
        return (prov + city) if prov and city else (prov or city or "中国")
    return COUNTRY_CODE.get(cc, data.get("country", "?"))


def _cached(ip):
    if not ip or ip in ("0.0.0.0", "127.0.0.1"):
        return None
    try:
        raw = _r.get(f"geo:{ip}")
        return json.loads(raw) if raw else None
    except Exception:
        return None


def _store(ip, data):
    if not ip or data is None:
        return
    data["geo_updated_at"] = int(time.time())
    _r.setex(f"geo:{ip}", GEO_TTL, json.dumps(data, ensure_ascii=False))


def _write_proxy_geo(proxy_key, data):
    try:
        cc = (data.get("countryCode") or "?").upper()
        loc = _format_location(data)
        _r.hset(f"proxy:{proxy_key}", mapping={
            "country": cc,
            "location": loc,
            "geo_updated_at": str(int(time.time())),
            "geo_source": data.get("source", "ip-api"),
        })
    except Exception:
        pass


def _enqueue_ip(ip):
    try:
        if ip and ip not in ("0.0.0.0", "127.0.0.1"):
            _r.sadd("geo:queue", ip)
    except Exception:
        pass


def _load_proxy_pool():
    now = time.time()
    if _proxy_cache["d"] and now - _proxy_cache["t"] < 300:
        return _proxy_cache["d"]
    proxies = []
    try:
        # 取分数最高的一批，优先 HTTP。免费代理不稳定，多备一些。
        for m in _r.zrevrange("proxies:pool", 0, 499):
            if m.startswith(("0.0.0.0:", "127.0.0.1:")):
                continue
            hd = _r.hgetall(f"proxy:{m}")
            proto = (hd.get("protocol") or "http").lower()
            if proto in ("http", "https", "?"):
                proxies.append(m)
    except Exception:
        proxies = []
    random.shuffle(proxies)
    _proxy_cache["d"] = proxies
    _proxy_cache["t"] = now
    return proxies


def _open_url(req, timeout=HTTP_TIMEOUT):
    # 先直连；遇到 403/429/限流或失败，再用池子代理轮换。
    try:
        return urllib.request.urlopen(req, timeout=timeout).read()
    except urllib.error.HTTPError as e:
        if e.code not in (403, 429, 503):
            return None
    except Exception:
        pass
    for pxy in _load_proxy_pool()[:20]:
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({
                "http": f"http://{pxy}", "https": f"http://{pxy}"
            }))
            return opener.open(req, timeout=timeout).read()
        except Exception:
            continue
    return None


def _query_batch(ips):
    if not ips:
        return []
    payload = json.dumps([{"query": ip} for ip in ips]).encode()
    req = urllib.request.Request(
        "http://ip-api.com/batch?lang=zh-CN&fields=status,message,country,countryCode,regionName,city,query",
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "proxy-dashboard/geo"},
    )
    raw = _open_url(req)
    if not raw:
        return []
    try:
        arr = json.loads(raw.decode("utf-8", errors="ignore"))
    except Exception:
        return []
    out = []
    for item in arr:
        if item.get("status") == "success":
            item["source"] = "ip-api"
            out.append(item)
    return out


def _query_one(ip):
    arr = _query_batch([ip])
    if arr:
        return arr[0]
    for fn in (_query_ip9, _query_freeipapi, _query_ipwhois):
        data = fn(ip)
        if data:
            return data
    return None


def _json_get(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": "proxy-dashboard/geo"})
    raw = _open_url(req, timeout=timeout)
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8", errors="ignore"))
    except Exception:
        return None


def _normalize(ip, country_code, country, region, city, source):
    cc = (country_code or "").upper()
    if not cc:
        return None
    return {
        "status": "success",
        "country": country or COUNTRY_CODE.get(cc, cc),
        "countryCode": cc,
        "regionName": region or "",
        "city": city or "",
        "query": ip,
        "source": source,
    }


def _query_ip9(ip):
    d = _json_get("https://ip9.com.cn/get?ip=" + urllib.parse.quote(ip), timeout=8)
    if not d or d.get("msg") == "Error":
        return None
    data = d.get("data") if isinstance(d.get("data"), dict) else d
    return _normalize(
        ip,
        data.get("country_code") or data.get("countryCode") or data.get("code"),
        data.get("country") or data.get("country_name"),
        data.get("prov") or data.get("province") or data.get("region") or data.get("regionName"),
        data.get("city") or data.get("cityName"),
        "ip9",
    )


def _query_freeipapi(ip):
    d = _json_get("https://freeipapi.com/api/json/" + urllib.parse.quote(ip), timeout=8)
    if not d:
        return None
    return _normalize(
        ip,
        d.get("countryCode"),
        d.get("countryName"),
        d.get("regionName"),
        d.get("cityName"),
        "freeipapi",
    )


def _query_ipwhois(ip):
    d = _json_get("https://ipwhois.app/json/" + urllib.parse.quote(ip) + "?lang=zh-CN", timeout=8)
    if not d or d.get("success") is False:
        return None
    return _normalize(
        ip,
        d.get("country_code"),
        d.get("country"),
        d.get("region"),
        d.get("city"),
        "ipwhois",
    )


def start_filler():
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
        time.sleep(FILL_INTERVAL)


def _needs_refresh(ip):
    data = _cached(ip)
    if not data or data.get("_placeholder"):
        return True
    ts = int(data.get("geo_updated_at") or 0)
    return time.time() - ts > GEO_TTL


def _fill_cycle():
    # queue first, then scan stale/missing from pool. Writes every successful result into DB.
    ips = []
    try:
        while len(ips) < BATCH_SIZE:
            ip = _r.spop("geo:queue")
            if not ip:
                break
            if _needs_refresh(ip):
                ips.append(ip)
    except Exception:
        pass
    if len(ips) < BATCH_SIZE:
        try:
            for m in _r.zscan_iter("proxies:pool", count=1000):
                key = m[0]
                ip = key.rsplit(":", 1)[0] if ":" in key else key
                if ip in ("0.0.0.0", "127.0.0.1"):
                    continue
                if _needs_refresh(ip):
                    ips.append(ip)
                if len(ips) >= BATCH_SIZE:
                    break
        except Exception:
            pass
    if not ips:
        return
    for item in _query_batch(ips):
        ip = item.get("query")
        if ip:
            _store(ip, item)
    # unresolved placeholders for 1 hour only; retry later, not 7 days.
    for ip in ips:
        if not _cached(ip):
            try:
                _r.setex(f"geo:{ip}", 3600, json.dumps({"_placeholder": True, "query": ip, "geo_updated_at": int(time.time())}))
            except Exception:
                pass


def _init():
    _load_local_db()
    t = threading.Thread(target=_local_db_updater_loop, daemon=True)
    t.start()
    start_filler()

_searcher = None
