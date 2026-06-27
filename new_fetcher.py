#!/usr/bin/env python3
"""
多源代理采集器 — 从 10+ 个新源拉取代理，喂入 Redis DB 1
来源：ProxyScrape, docip, 89ip, clarketm, Thordata, hookzof, 
      Free-Proxy-List 系列, 快代理, ip3366, Data5u 等
"""
import urllib.request, json, time, re, sys, os, ssl, random

# ── Redis 连接 ──
import redis as redis_lib
REDIS = redis_lib.Redis(host=os.environ.get("REDIS_HOST", "proxy-redis"), port=6379, db=1, decode_responses=True,
                         socket_connect_timeout=5, socket_timeout=5)

KEY_POOL = "proxies:pool"
PFX_PROXY = "proxy:"

# ── ip2region ──
try:
    from searcher import Searcher
    from util import IPv4
    IP2R = Searcher(IPv4, "/app/data/ip2region.xdb", None, None)
except:
    IP2R = None

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"

def geo_lookup(ip):
    if not IP2R:
        return "unknown", False
    try:
        result = IP2R.searchByIPStr(ip)
        if result and "|" in result:
            parts = result.split("|")
            country = parts[0] or "unknown"
            region = parts[2] or "unknown"
            city = parts[3] or "unknown"
            is_cn = country == "中国"
            return f"{country}|{region}|{city}", is_cn
    except:
        pass
    return "unknown|unknown|unknown", False

def add_proxy(proxy_str, source, protocol="http"):
    """添加代理到 Redis，已存在则跳过"""
    if REDIS.zscore(KEY_POOL, proxy_str) is not None:
        return False  # 已存在
    
    parts = proxy_str.split(":")
    if len(parts) != 2:
        return False
    ip, port = parts[0], parts[1]
    
    geo_str, is_cn = geo_lookup(ip)
    geo_parts = geo_str.split("|")
    country = geo_parts[0] if len(geo_parts) > 0 else "unknown"
    region = geo_parts[1] if len(geo_parts) > 1 else "unknown"
    city = geo_parts[2] if len(geo_parts) > 2 else "unknown"
    
    REDIS.zadd(KEY_POOL, {proxy_str: 20})  # 起始分 20
    REDIS.hset(f"{PFX_PROXY}{proxy_str}", mapping={
        "ip": ip, "port": port, "protocol": protocol,
        "country": country, "region": region, "city": city,
        "is_china": str(is_cn), "source": source, "latency": "9999"
    })
    return True

# ── 代理池（用自己池子反爬被墙源） ──
PROXY_CACHE = []
PROXY_CACHE_TS = 0

def _load_proxies():
    global PROXY_CACHE, PROXY_CACHE_TS
    now = time.time()
    if now - PROXY_CACHE_TS < 60 and PROXY_CACHE:
        return
    PROXY_CACHE = []
    try:
        members = REDIS.zrange(KEY_POOL, 0, 200)
        for m in members:
            proto = REDIS.hget(PFX_PROXY + m, "protocol") or ""
            lat = REDIS.hget(PFX_PROXY + m, "latency") or "0"
            if "http" in proto and lat.isdigit() and int(lat) > 0:
                PROXY_CACHE.append(m)
    except:
        pass
    PROXY_CACHE_TS = now

def fetch(url, timeout=15, json_response=False, use_proxy=False):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    def _do_fetch(proxy=None):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            if proxy:
                host, port = proxy.rsplit(":", 1)
                req.set_proxy(f"{host}:{port}", "http")
            resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
            content = resp.read().decode("utf-8", errors="ignore")
            return json.loads(content) if json_response else content
        except Exception as e:
            return None
    
    # try direct first
    result = _do_fetch()
    if result is not None:
        return result
    
    # try with proxy
    _load_proxies()
    if not PROXY_CACHE:
        print(f"  ⚠ {url[:60]} → no proxy available")
        return None
    
    random.shuffle(PROXY_CACHE)
    for proxy in PROXY_CACHE[:10]:
        result = _do_fetch(proxy)
        if result is not None:
            return result
    
    print(f"  ⚠ {url[:60]} → all proxies failed")
    return None

# ═══════════════════════════════════════════════
# 1. ProxyScrape API
# ═══════════════════════════════════════════════
def fetch_proxyscrape():
    print("[ProxyScrape]")
    text = fetch("https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all")
    if not text:
        return 0
    count = 0
    for line in text.strip().split("\n"):
        line = line.strip()
        if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+$', line):
            if add_proxy(line, "proxyscrape"):
                count += 1
    print(f"  +{count} 新代理")
    return count

# ═══════════════════════════════════════════════
# 2. docip.net
# ═══════════════════════════════════════════════
def fetch_docip():
    print("[docip]")
    data = fetch("https://www.docip.net/data/free.json", json_response=True)
    if not data:
        return 0
    count = 0
    for item in data.get("data", []):
        ip = item.get("ip", "")
        port = item.get("port", "")
        if ip and port:
            if add_proxy(f"{ip}:{port}", "docip"):
                count += 1
    print(f"  +{count} 新代理")
    return count

# ═══════════════════════════════════════════════
# 3. 89ip.cn API
# ═══════════════════════════════════════════════
def fetch_89ip():
    print("[89ip]")
    text = fetch("http://api.89ip.cn/tqdl.html?api=1&num=60")
    if not text:
        return 0
    count = 0
    for match in re.finditer(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+)', text):
        if add_proxy(match.group(1), "89ip"):
            count += 1
    print(f"  +{count} 新代理")
    return count

# ═══════════════════════════════════════════════
# 4. clarketm/proxy-list (GitHub raw)
# ═══════════════════════════════════════════════
def fetch_text_list(name, url, source_label):
    print(f"[{name}]")
    text = fetch(url)
    if not text:
        return 0
    count = 0
    for line in text.strip().split("\n"):
        line = line.strip()
        if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+$', line):
            if add_proxy(line, source_label):
                count += 1
    print(f"  +{count} 新代理")
    return count

# ═══════════════════════════════════════════════
# 5. Free-Proxy-List.net 系列 (HTML table)
# ═══════════════════════════════════════════════
def fetch_proxy_table(name, url, source_label):
    print(f"[{name}]")
    html = fetch(url)
    if not html:
        return 0
    count = 0
    # These sites use <tr><td>IP</td><td>Port</td>... pattern
    # Extract all IP:port pairs
    ips = re.findall(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})</td>\s*<td>(\d+)</td>', html)
    for ip, port in ips:
        if add_proxy(f"{ip}:{port}", source_label):
            count += 1
    print(f"  +{count} 新代理")
    return count

# ═══════════════════════════════════════════════
# 6. 快代理 Kuaidaili
# ═══════════════════════════════════════════════
# 积流代理
def fetch_jiliu():
    count = 0
    for page in range(1, 11):
        try:
            html = fetch(f'https://www.jiliuip.com/free/page-{page}')
            for m in re.finditer(r'(\d+\.\d+\.\d+\.\d+)[^\d]+(\d+)', html):
                count += add_proxy(f'{m.group(1)}:{m.group(2)}', 'jiliu')
            time.sleep(1)
        except Exception as e:
            print(f'[jiliu] p{page} fail: {e}')
    return count

# 齐云代理
def fetch_qiyun():
    count = 0
    try:
        html = fetch('https://www.qiyunip.com/freeProxy/')
        for m in re.finditer(r'(\d+\.\d+\.\d+\.\d+)[^\d]+(\d+)', html):
            count += add_proxy(f'{m.group(1)}:{m.group(2)}', 'qiyun')
    except Exception as e:
        print(f'[qiyun] fail: {e}')
    return count

# OpenProxyList
def fetch_openproxylist():
    count = 0
    for proto, url in [
        ('http', 'https://api.openproxylist.xyz/http.txt'),
        ('socks4', 'https://api.openproxylist.xyz/socks4.txt'),
        ('socks5', 'https://api.openproxylist.xyz/socks5.txt'),
    ]:
        try:
            text = fetch(url)
            for m in re.finditer(r'(\d+\.\d+\.\d+\.\d+):(\d+)', text):
                count += add_proxy(f'{m.group(1)}:{m.group(2)}', 'openproxylist', protocol=proto)
        except Exception as e:
            print(f'[openproxylist] {proto} fail: {e}')
    return count

# MuRongPIG (biggest single source)
def fetch_murongpig():
    count = 0
    urls = [
        ('https://raw.githubusercontent.com/MuRongPIG/Proxy-Master/main/http.txt', 'murong'),
        ('https://raw.githubusercontent.com/MuRongPIG/Proxy-Master/main/socks4.txt', 'murong', 'socks4'),
        ('https://raw.githubusercontent.com/MuRongPIG/Proxy-Master/main/socks5.txt', 'murong', 'socks5'),
    ]
    for url, *rest in urls:
        proto = rest[1] if len(rest) > 1 else 'http'
        try:
            text = fetch(url)
            for m in re.finditer(r'(\d+\.\d+\.\d+\.\d+):(\d+)', text):
                count += add_proxy(f'{m.group(1)}:{m.group(2)}', 'murongpig', protocol=proto)
        except Exception as e:
            print(f'[murongpig] fail: {e}')
    return count

# VMHeaven
def fetch_vmheaven():
    count = 0
    for proto, url in [
        ('http', 'https://raw.githubusercontent.com/vmheaven/VMHeaven.io-Free-Proxy-List/main/http.txt'),
        ('socks4', 'https://raw.githubusercontent.com/vmheaven/VMHeaven.io-Free-Proxy-List/main/socks4.txt'),
        ('socks5', 'https://raw.githubusercontent.com/vmheaven/VMHeaven.io-Free-Proxy-List/main/socks5.txt'),
    ]:
        try:
            text = fetch(url)
            for m in re.finditer(r'(\d+\.\d+\.\d+\.\d+):(\d+)', text):
                count += add_proxy(f'{m.group(1)}:{m.group(2)}', 'vmheaven', protocol=proto)
        except Exception as e:
            print(f'[vmheaven] {proto} fail: {e}')
    return count

# jetkai
def fetch_jetkai():
    count = 0
    try:
        text = fetch('https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies.txt')
        for m in re.finditer(r'(\d+\.\d+\.\d+\.\d+):(\d+)', text):
            count += add_proxy(f'{m.group(1)}:{m.group(2)}', 'jetkai')
    except Exception as e:
        print(f'[jetkai] fail: {e}')
    return count

# proxifly
def fetch_proxifly_gh():
    count = 0
    try:
        text = fetch('https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt')
        for m in re.finditer(r'(\d+\.\d+\.\d+\.\d+):(\d+)', text):
            count += add_proxy(f'{m.group(1)}:{m.group(2)}', 'proxifly-gh')
    except Exception as e:
        print(f'[proxifly-gh] fail: {e}')
    return count

def fetch_kuaidaili():
    print("[快代理]")
    count = 0
    for page in range(1, 4):
        html = fetch(f"https://www.kuaidaili.com/free/inha/{page}/")
        if not html:
            break
        ips = re.findall(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})</td>\s*<td[^>]*>(\d+)</td>', html)
        for ip, port in ips:
            if add_proxy(f"{ip}:{port}", "kuaidaili"):
                count += 1
        time.sleep(1)
    print(f"  +{count} 新代理")
    return count

# ═══════════════════════════════════════════════
# 7. ip3366
# ═══════════════════════════════════════════════
def fetch_ip3366():
    print("[ip3366]")
    count = 0
    for stype in [1, 2]:
        for page in range(1, 4):
            html = fetch(f"http://www.ip3366.net/free/?stype={stype}&page={page}")
            if not html:
                break
            ips = re.findall(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})</td>\s*<td>(\d+)</td>', html)
            for ip, port in ips:
                if add_proxy(f"{ip}:{port}", "ip3366"):
                    count += 1
            time.sleep(0.5)
    print(f"  +{count} 新代理")
    return count

# ═══════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════
if __name__ == "__main__":
    start_time = time.time()
    total = 0
    
    # API 源
    total += fetch_proxyscrape()
    total += fetch_docip()
    total += fetch_89ip()
    
    # GitHub 文本列表
    total += fetch_text_list("clarketm", 
        "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
        "clarketm")
    total += fetch_text_list("Thordata",
        "https://raw.githubusercontent.com/Thordata/awesome-free-proxy-list/main/proxies/all.txt",
        "thordata")
    total += fetch_text_list("hookzof/socks5",
        "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt",
        "hookzof")
    
    # HTML 表格页面
    total += fetch_proxy_table("Free-Proxy-List", "https://free-proxy-list.net/", "free-proxy-list")
    total += fetch_proxy_table("SSLProxies", "https://www.sslproxies.org/", "sslproxies")
    total += fetch_proxy_table("US-Proxy", "https://www.us-proxy.org/", "us-proxy")
    total += fetch_proxy_table("Socks-Proxy", "https://www.socks-proxy.net/", "socks-proxy")
    
    # 中国源
    total += fetch_kuaidaili()
    total += fetch_ip3366()
    total += fetch_openproxylist()
    total += fetch_murongpig()
    total += fetch_vmheaven()
    total += fetch_jetkai()
    total += fetch_proxifly_gh()
    total += fetch_jiliu()
    total += fetch_qiyun()
    
    elapsed = time.time() - start_time
    pool_size = REDIS.zcard(KEY_POOL)
    print(f"\n{'='*50}")
    print(f"完成: +{total} 新代理 | 池总量: {pool_size} | 耗时: {elapsed:.1f}s")
    print(f"{'='*50}")
