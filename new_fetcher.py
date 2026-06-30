#!/usr/bin/env python3
"""
多源代理采集器 — 从 20+ 个源拉取代理，喂入 Redis DB 1
来源：ProxyScrape, docip, 89ip, clarketm, Thordata, hookzof,
      Free-Proxy-List 系列, 快代理, ip3366, MuRongPIG, OpenProxyList, VMHeaven 等
"""
import urllib.request, json, time, re, sys, os, ssl, random

# ── Redis 连接 ──
import redis as redis_lib
REDIS = redis_lib.Redis(host="proxy-redis", port=6379, db=1, decode_responses=True,
                         socket_connect_timeout=5, socket_timeout=5)

KEY_POOL = "proxies:pool"
PFX_PROXY = "proxy:"

# ── ip2region v4 ──
try:
    import searcher, util
    IP2R = searcher.new_with_file_only(util.IPv4, "/app/ip2region.xdb")
except Exception as e:
    print(f"  ⚠ ip2region load failed: {e}")
    IP2R = None

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"

def geo_lookup(ip):
    """优先本地离线库 geo._local_lookup，回退 ip2region xdb"""
    # 1. Try geo module's local binary DB
    try:
        import geo
        cc = geo._local_lookup(ip)
        if cc != "ZZ":
            country = geo.COUNTRY_CODE.get(cc, cc)
            is_cn = cc == "CN"
            return f"{country}|{cc}|", is_cn
    except Exception:
        pass

    # 2. Fallback to ip2region xdb
    if not IP2R:
        return "unknown", False
    try:
        result = IP2R.search(ip)
        if result and "|" in result:
            parts = result.split("|")
            country = parts[0] or "unknown"
            region = parts[2] or "unknown"
            city = parts[3] or "unknown"
            is_cn = country == "中国"
            return f"{country}|{region}|{city}", is_cn
    except Exception:
        pass
    return "unknown|unknown|unknown", False

def add_proxy(proxy_str, source, protocol="http"):
    """添加代理到 Redis，入库前快速验证延迟，只留 <500ms。"""
    if REDIS.zscore(KEY_POOL, proxy_str) is not None:
        return False  # 已存在

    parts = proxy_str.split(":")
    if len(parts) != 2:
        return False
    ip, port = parts[0], parts[1]
    try:
        pnum = int(port)
        if pnum <= 0 or pnum > 65535:
            return False
    except (ValueError, TypeError):
        return False
    if not protocol or protocol.lower() in ("unknown", "", "?"):
        return False

    # ── 入库前快速验证：HTTP 直连 <500ms 才收 ──
    latency = None
    try:
        import socket as _sock
        with _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM) as s:
            s.settimeout(3.0)
            t0 = time.time()
            s.connect((ip, pnum))
            s.send(f"GET http://www.qq.com/ HTTP/1.1\r\nHost: www.qq.com\r\nConnection: close\r\n\r\n".encode())
            data = b""
            while True:
                try:
                    chunk = s.recv(4096)
                    if not chunk: break
                    data += chunk
                except: break
            if data and (b"HTTP/" in data or len(data) > 100):
                latency = int((time.time() - t0) * 1000)
    except:
        pass

    if latency is None or latency <= 0 or latency >= 500:
        return False  # 慢/死代理直接丢弃
    # ──────────────────────────────────────────────

    geo_str, is_cn = geo_lookup(ip)
    geo_parts = geo_str.split("|")
    country = geo_parts[0] if len(geo_parts) > 0 else "unknown"
    region = geo_parts[1] if len(geo_parts) > 1 else "unknown"
    city = geo_parts[2] if len(geo_parts) > 2 else "unknown"

    # pipeline 事务：zadd + hset 原子执行
    pipe = REDIS.pipeline(transaction=True)
    pipe.zadd(KEY_POOL, {proxy_str: 20})
    pipe.hset(f"{PFX_PROXY}{proxy_str}", mapping={
        "ip": ip, "port": port, "protocol": protocol,
        "country": country, "region": region, "city": city,
        "is_china": str(is_cn), "source": source, "latency": str(latency)
    })
    pipe.execute()
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
        if not members:
            return
        # pipeline 批量 HGETALL — 1 次往返替代 N 次 HGET
        pipe = REDIS.pipeline(transaction=False)
        for m in members:
            pipe.hgetall(PFX_PROXY + m)
        metas = pipe.execute()
        for m, meta in zip(members, metas):
            proto = (meta or {}).get(b"protocol", b"") if isinstance(meta, dict) else b""
            if isinstance(proto, bytes):
                proto = proto.decode()
            lat = (meta or {}).get(b"latency", b"0") if isinstance(meta, dict) else "0"
            if isinstance(lat, bytes):
                lat = lat.decode()
            if "http" in proto and lat.isdigit() and int(lat) > 0:
                PROXY_CACHE.append(m)
    except Exception:
        pass
    PROXY_CACHE_TS = now

def fetch(url, timeout=12, json_response=False, use_proxy=False):
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

    # try with proxy — 并行试前 5 个代理，第一个成功即返回
    _load_proxies()
    if not PROXY_CACHE:
        return None

    import concurrent.futures as cf
    random.shuffle(PROXY_CACHE)
    candidates = PROXY_CACHE[:5]  # 并行试 5 个
    with cf.ThreadPoolExecutor(max_workers=min(3, len(candidates))) as px:
        futures = {px.submit(_do_fetch, p): p for p in candidates}
        for f in cf.as_completed(futures):
            result = f.result()
            if result is not None:
                px.shutdown(wait=False, cancel_futures=True)
                return result

    return None

# ═══════════════════════════════════════════════
# ProxyScrape API
# ═══════════════════════════════════════════════
def fetch_proxyscrape():
    text = fetch("https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all")
    if not text:
        return 0
    count = 0
    for line in text.strip().split("\n"):
        line = line.strip()
        if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+$', line):
            if add_proxy(line, "proxyscrape", protocol="http"):
                count += 1
    return count

# ═══════════════════════════════════════════════
# GitHub 文本列表
# ═══════════════════════════════════════════════
def fetch_text_list(name, url, source_label):
    text = fetch(url)
    if not text:
        return 0
    count = 0
    for line in text.strip().split("\n"):
        line = line.strip()
        if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+$', line):
            if add_proxy(line, source_label):
                count += 1
    return count

# ═══════════════════════════════════════════════
# HTML 表格页面
# ═══════════════════════════════════════════════
def fetch_proxy_table(name, url, source_label):
    html = fetch(url)
    if not html:
        return 0
    count = 0
    ips = re.findall(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})</td>\s*<td[^>]*>(\d+)</td>', html)
    for ip, port in ips:
        if add_proxy(f"{ip}:{port}", source_label):
            count += 1
    return count

# ═══════════════════════════════════════════════
# 国内源
# ═══════════════════════════════════════════════
def fetch_kuaidaili():
    count = 0
    for page in range(1, 4):
        html = fetch(f"https://www.kuaidaili.com/free/inha/{page}/")
        if not html:
            break
        ips = re.findall(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})</td>\s*<td[^>]*>(\d+)</td>', html)
        for ip, port in ips:
            if add_proxy(f"{ip}:{port}", "kuaidaili", protocol="http"):
                count += 1
        time.sleep(1)
    return count

def fetch_ip3366():
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
    return count

def fetch_docip():
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
    return count

def fetch_89ip():
    text = fetch("http://api.89ip.cn/tqdl.html?api=1&num=60")
    if not text:
        return 0
    count = 0
    for match in re.finditer(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+)', text):
        if add_proxy(match.group(1), "89ip"):
            count += 1
    return count

# ── 国际大源（可能被墙，用代理轮换） ──
def fetch_openproxylist():
    count = 0
    for proto, url in [
        ('http', 'https://api.openproxylist.xyz/http.txt'),
        ('socks4', 'https://api.openproxylist.xyz/socks4.txt'),
        ('socks5', 'https://api.openproxylist.xyz/socks5.txt'),
    ]:
        try:
            text = fetch(url)
            if text:
                for m in re.finditer(r'(\d+\.\d+\.\d+\.\d+):(\d+)', text):
                    if add_proxy(f'{m.group(1)}:{m.group(2)}', 'openproxylist', protocol=proto):
                        count += 1
        except Exception:
            pass
    return count

def fetch_murongpig():
    count = 0
    urls = [
        ('https://raw.githubusercontent.com/MuRongPIG/Proxy-Master/main/http.txt', 'murongpig', 'http'),
        ('https://raw.githubusercontent.com/MuRongPIG/Proxy-Master/main/socks4.txt', 'murongpig', 'socks4'),
        ('https://raw.githubusercontent.com/MuRongPIG/Proxy-Master/main/socks5.txt', 'murongpig', 'socks5'),
    ]
    for url, src, proto in urls:
        try:
            text = fetch(url)
            if text:
                for m in re.finditer(r'(\d+\.\d+\.\d+\.\d+):(\d+)', text):
                    if add_proxy(f'{m.group(1)}:{m.group(2)}', src, protocol=proto):
                        count += 1
        except Exception:
            pass
    return count

def fetch_vmheaven():
    count = 0
    for proto, url in [
        ('http', 'https://raw.githubusercontent.com/vmheaven/VMHeaven.io-Free-Proxy-List/main/http.txt'),
        ('socks4', 'https://raw.githubusercontent.com/vmheaven/VMHeaven.io-Free-Proxy-List/main/socks4.txt'),
        ('socks5', 'https://raw.githubusercontent.com/vmheaven/VMHeaven.io-Free-Proxy-List/main/socks5.txt'),
    ]:
        try:
            text = fetch(url)
            if text:
                for m in re.finditer(r'(\d+\.\d+\.\d+\.\d+):(\d+)', text):
                    if add_proxy(f'{m.group(1)}:{m.group(2)}', 'vmheaven', protocol=proto):
                        count += 1
        except Exception:
            pass
    return count

def fetch_proxifly_gh():
    count = 0
    try:
        text = fetch('https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt')
        if text:
            for m in re.finditer(r'(\d+\.\d+\.\d+\.\d+):(\d+)', text):
                if add_proxy(f'{m.group(1)}:{m.group(2)}', 'proxifly-gh', protocol="http"):
                    count += 1
    except Exception:
        pass
    return count

def fetch_jiliu():
    count = 0
    for page in range(1, 11):
        try:
            html = fetch(f'https://www.jiliuip.com/free/page-{page}')
            if html:
                for m in re.finditer(r'(\d+\.\d+\.\d+\.\d+)[^\d]+(\d+)', html):
                    if add_proxy(f'{m.group(1)}:{m.group(2)}', 'jiliu'):
                        count += 1
            time.sleep(1)
        except Exception:
            pass
    return count

def fetch_qiyun():
    count = 0
    try:
        html = fetch('https://www.qiyunip.com/freeProxy/')
        if html:
            for m in re.finditer(r'(\d+\.\d+\.\d+\.\d+)[^\d]+(\d+)', html):
                if add_proxy(f'{m.group(1)}:{m.group(2)}', 'qiyun'):
                    count += 1
    except Exception:
        pass
    return count

# ═══════════════════════════════════════════════# ── 新源 1: VPSLabCloud (163⭐) ──
def fetch_vpslabcloud():
    count = 0
    for proto, fname in [
        ('http', 'http_all.txt'),
        ('socks4', 'socks4_all.txt'),
        ('socks5', 'socks5_all.txt'),
    ]:
        try:
            url = f'https://raw.githubusercontent.com/VPSLabCloud/VPSLab-Free-Proxy-List/main/{fname}'
            text = fetch(url)
            if text:
                for line in text.strip().split('\n'):
                    line = line.strip().replace('\r', '')
                    if line and not line.startswith('#') and ':' in line:
                        m = re.match(r'^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+)', line)
                        if m:
                            if add_proxy(m.group(1), 'vpslab', protocol=proto):
                                count += 1
        except Exception:
            pass
    return count

# ── 新源 2: iplocate (140⭐, 5k proxies) ──
def fetch_iplocate():
    count = 0
    try:
        text = fetch('https://raw.githubusercontent.com/iplocate/free-proxy-list/main/all-proxies.txt')
        if text:
            for line in text.strip().split('\n'):
                line = line.strip()
                # Format: socks5://IP:port or http://IP:port
                m = re.match(r'^(socks5|socks4|http|https)://(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+)', line)
                if m:
                    proto, proxy = m.group(1), m.group(2)
                    if proto in ('http', 'https'):
                        proto = 'http'
                    if add_proxy(proxy, 'iplocate', protocol=proto):
                        count += 1
    except Exception:
        pass
    return count

# ── 新源 3: databay-labs ──
def fetch_databay():
    count = 0
    for proto, fname in [
        ('http', 'http.txt'),
        ('socks4', 'socks4.txt'),
        ('socks5', 'socks5.txt'),
    ]:
        try:
            url = f'https://raw.githubusercontent.com/databay-labs/free-proxy-list/master/{fname}'
            text = fetch(url)
            if text:
                for line in text.strip().split('\n'):
                    line = line.strip().replace('\r', '')
                    m = re.match(r'^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+)', line)
                    if m:
                        if add_proxy(m.group(1), 'databay', protocol=proto):
                            count += 1
        except Exception:
            pass
    return count

# ── 新源 ErcinDedeoglu/proxies (373⭐, 40k proxies, daily) ──
def fetch_ercindededeoglu():
    count = 0
    for proto, fname in [
        ('http', 'http.txt'),
        ('http', 'https.txt'),
        ('socks4', 'socks4.txt'),
        ('socks5', 'socks5.txt'),
    ]:
        try:
            url = f'https://raw.githubusercontent.com/ErcinDedeoglu/proxies/main/proxies/{fname}'
            text = fetch(url)
            if text:
                for line in text.strip().split('\n'):
                    line = line.strip().replace('\r', '')
                    m = re.match(r'^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+)', line)
                    if m:
                        if add_proxy(m.group(1), 'ercindededeoglu', protocol=proto):
                            count += 1
        except Exception:
            pass
    return count

# ── 新源 ProxyScraper/ProxyScraper (207⭐, 8k proxies, every 8h) ──
def fetch_proxyscraper_repo():
    count = 0
    for proto, fname in [
        ('http', 'http.txt'),
        ('socks4', 'socks4.txt'),
        ('socks5', 'socks5.txt'),
    ]:
        try:
            url = f'https://raw.githubusercontent.com/ProxyScraper/ProxyScraper/main/{fname}'
            text = fetch(url)
            if text:
                for line in text.strip().split('\n'):
                    line = line.strip().replace('\r', '')
                    m = re.match(r'^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+)', line)
                    if m:
                        if add_proxy(m.group(1), 'proxyscraper-repo', protocol=proto):
                            count += 1
        except Exception:
            pass
    return count

# ── 新源 Anonym0usWork1221/Free-Proxies (201⭐, 8.5k proxies, every 2h) ──
def fetch_anon1221():
    count = 0
    for proto, fname in [
        ('http', 'proxy_files/http_proxies.txt'),
        ('socks4', 'proxy_files/socks4_proxies.txt'),
        ('socks5', 'proxy_files/socks5_proxies.txt'),
    ]:
        try:
            url = f'https://raw.githubusercontent.com/Anonym0usWork1221/Free-Proxies/main/{fname}'
            text = fetch(url)
            if text:
                for line in text.strip().split('\n'):
                    line = line.strip().replace('\r', '')
                    m = re.match(r'^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+)', line)
                    if m:
                        if add_proxy(m.group(1), 'anon1221', protocol=proto):
                            count += 1
        except Exception:
            pass
    return count

# ── 新源 hideip.me (474⭐, 1k proxies, every 10min) ──
def fetch_hideip():
    count = 0
    for proto, fname in [
        ('http', 'http.txt'),
        ('socks4', 'socks4.txt'),
        ('socks5', 'socks5.txt'),
    ]:
        try:
            url = f'https://raw.githubusercontent.com/zloi-user/hideip.me/master/{fname}'
            text = fetch(url)
            if text:
                for line in text.strip().split('\n'):
                    line = line.strip().replace('\r', '')
                    # hideip format: IP:port:Country — extract country
                    parts = line.rsplit(':')
                    country_name = ""
                    if len(parts) >= 3:
                        country_name = parts[-1].strip()
                        line = f'{parts[0]}:{parts[1]}'
                    m = re.match(r'^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+)', line)
                    if m:
                        addr = m.group(1)
                        ip = addr.split(':')[0]
                        if add_proxy(addr, 'hideip', protocol=proto):
                            count += 1
                            # Inject source-provided geo
                            if country_name and len(country_name) <= 30:
                                try:
                                    import geo
                                    cc = geo._name_to_code(country_name)
                                    if cc:
                                        geo.inject_geo(ip, cc)
                                except Exception:
                                    pass
        except Exception:
            pass
    return count

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

    # 国际大源（可能被墙，用代理轮换）
    total += fetch_openproxylist()
    total += fetch_murongpig()
    total += fetch_vmheaven()
    total += fetch_proxifly_gh()
    total += fetch_jiliu()
    total += fetch_qiyun()
    total += fetch_vpslabcloud()
    total += fetch_iplocate()
    total += fetch_databay()
    total += fetch_ercindededeoglu()
    total += fetch_proxyscraper_repo()
    total += fetch_anon1221()
    total += fetch_hideip()

    elapsed = time.time() - start_time
    pool_size = REDIS.zcard(KEY_POOL)
    print(f"\n{'='*50}")
    print(f"完成: +{total} 新代理 | 池总量: {pool_size} | 耗时: {elapsed:.1f}s")
    print(f"{'='*50}")
