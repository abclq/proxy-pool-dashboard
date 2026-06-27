#!/usr/bin/env python3
"""
ProxyPool 引擎 v3 — Redis 持久化 + 信用评分 + 粘性会话 + 站点隔离
═══════════════════════════════════════════════════════════
Harvester — proxy-pool 容器负责
Validator — 本服务后台线程
Proxy Server — HTTP/SOCKS5 转发
Dashboard — Web UI
═══════════════════════════════════════════════════════════
纯 Python 标准库 + redis-py
"""

import http.server
import json
import urllib.request
import urllib.parse
import socket
import threading
import time
import os
import re
import struct
import select
import random
import sys

# ══════════════════════════════════════════════════════════
# 环境清理
# ══════════════════════════════════════════════════════════
for k in list(os.environ.keys()):
    if k.lower().endswith("_proxy"):
        del os.environ[k]

# ══════════════════════════════════════════════════════════
# Redis 连接
# ══════════════════════════════════════════════════════════
import redis as redis_lib
REDIS_HOST = os.environ.get("REDIS_HOST", "proxy-redis")
REDIS = redis_lib.Redis(host=REDIS_HOST, port=6379, db=1, decode_responses=True,
                         socket_connect_timeout=3, socket_timeout=3)

# Key 命名空间
KEY_POOL      = "proxies:pool"        # sorted set: score=credit, member="ip:port"
KEY_BLACKLIST = "proxies:bl"          # set，元素带 TTL
KEY_STICKY_PX = "sticky:"             # prefix + session_id → "ip:port"
KEY_SELECTED  = "config:selected"     # 手动指定
KEY_STRATEGY  = "config:strategy"     # 路由策略
PFX_STATS     = "proxies:stats:"      # hash: {success, fail, last_used}
PFX_SITE      = "proxies:site:"       # sorted set per target site
PFX_PROXY     = "proxy:"              # hash: metadata

# ══════════════════════════════════════════════════════════
# 配置
# ══════════════════════════════════════════════════════════
PROXY_API           = "http://proxy-pool:5010"
HTTP_PROXY_PORT     = 8080
SOCKS5_PROXY_PORT   = 1080
CHECK_INTERVAL      = 20
BLACKLIST_TTL       = 300
STICKY_TTL          = 300      # 粘性会话 5 分钟

# 源质关闸：低质源自动屏蔽 (交叉验证共识)
SOURCE_QUALITY_MIN  = 0.10     # S+A / verified 最低要求（用于标记，不直接屏蔽）
SOURCE_USABLE_MIN   = 0.30     # S+A+B / verified 低于此值才屏蔽
SOURCE_BLACKLIST_ELITE = 0.05  # S+A / verified 低于此值 AND usable也低 → 双重确认屏蔽
SOURCE_QUALITY_CACHE = {}      # 运行时缓存
SOURCE_BLACKLIST = set()       # 自动黑名单

# 信用评分常数
CREDIT_NEW      = 20
CREDIT_SUCCESS  = 5
CREDIT_MAX      = 100
CREDIT_403      = -20
CREDIT_502      = -30
CREDIT_TIMEOUT  = -30

# ══════════════════════════════════════════════════════════
# Redis 操作
# ══════════════════════════════════════════════════════════

def _proxy_meta_key(proxy):
    return f"{PFX_PROXY}{proxy}"

def _stats_key(proxy):
    return f"{PFX_STATS}{proxy}"

def _site_key(target):
    return f"{PFX_SITE}{target}"

# ── 信用分 ──

def credit_get(proxy):
    """获取信用分（不存在返回 None）"""
    s = REDIS.zscore(KEY_POOL, proxy)
    return s if s is not None else None

def credit_add(proxy, delta, target=None):
    """加减信用分，<0 自动淘汰"""
    score = REDIS.zincrby(KEY_POOL, delta, proxy)
    if target:
        REDIS.zincrby(_site_key(target), delta, proxy)
    if score <= 0:
        REDIS.zrem(KEY_POOL, proxy)
        REDIS.delete(_proxy_meta_key(proxy))
        if target:
            REDIS.zrem(_site_key(target), proxy)
        print(f"[credit] 淘汰: {proxy} (分={score})")
    return score

def credit_init(proxy, metadata, score=CREDIT_NEW):
    """新代理入库"""
    REDIS.zadd(KEY_POOL, {proxy: score})
    REDIS.hset(_proxy_meta_key(proxy), mapping=metadata)

# ── 黑名单 ──

def blacklist_add(proxy):
    REDIS.sadd(KEY_BLACKLIST, proxy)
    REDIS.expire(KEY_BLACKLIST, BLACKLIST_TTL)  # refresh TTL on set

def blacklist_check(proxy):
    return REDIS.sismember(KEY_BLACKLIST, proxy)

def blacklist_remove(proxy):
    REDIS.srem(KEY_BLACKLIST, proxy)

# ── 元数据 ──

def proxy_meta(proxy):
    """获取单个代理元数据"""
    m = REDIS.hgetall(_proxy_meta_key(proxy))
    if m:
        m["proxy"] = proxy  # 回填 proxy key
    return m

def proxy_meta_set(proxy, **kwargs):
    REDIS.hset(_proxy_meta_key(proxy), mapping=kwargs)

def all_proxies():
    """获取所有代理（带分数和元数据）"""
    members = REDIS.zrange(KEY_POOL, 0, -1, withscores=True)
    result = []
    for proxy, score in members:
        meta = proxy_meta(proxy)
        if not meta:
            REDIS.zrem(KEY_POOL, proxy)
            continue
        meta["proxy"] = proxy
        meta["credit"] = int(score)
        # Fallback: use "delay" field if "latency" missing (v3 batch validator compat)
        _raw_lat = meta.get("latency") or meta.get("delay")
        meta["latency"] = float(_raw_lat) if _raw_lat else None
        meta["speed"] = grade_for_latency(meta["latency"])
        # JS frontend expects boolean, not string
        meta["is_china"] = meta.get("is_china", "") == "True"
        meta["last_check"] = meta.get("last_check") or ""
        meta["success_rate"] = int(meta.get("success_rate") or 0)
        meta["anonymity"] = meta.get("anonymity") or "unknown"
        meta["stability"] = stability_score(meta)      # scylla 式综合评分 0-1
        meta["attempts"] = int(meta.get("attempts") or 0)
        # Country normalization
        if meta.get("country") == "Viet Nam":
            meta["country"] = "Vietnam"
        result.append(meta)
    return result

def proxy_count():
    return REDIS.zcard(KEY_POOL)

def proxy_list_raw():
    """返回 (ip, port, protocol) 列表供兼容旧 API"""
    members = REDIS.zrange(KEY_POOL, 0, -1)
    result = []
    for proxy in members:
        meta = proxy_meta(proxy)
        if meta:
            result.append(meta)
    return result

# ── 代理选择 ──

def get_best_proxy(target=None, strategy=None):
    """
    按策略从 Redis sorted set 选代理。
    sorted set 天然按信用分排序 → zrevrange 直接取高分
    """
    if not strategy:
        strategy = REDIS.get(KEY_STRATEGY) or "balanced"

    # 检查手动指定
    selected = REDIS.get(KEY_SELECTED)
    if selected and REDIS.zscore(KEY_POOL, selected) is not None:
        return proxy_meta(selected)

    key = KEY_POOL

    if strategy == "latency":
        # 取 top 50 信用分，再按延迟排
        tops = REDIS.zrevrange(key, 0, 49)
        best = None
        best_lat = 99999
        for p in tops:
            meta = proxy_meta(p)
            if meta and meta.get("latency"):
                lat = float(meta["latency"])
                if lat < best_lat:
                    best_lat = lat
                    best = meta
        if best: return best

    # balanced / sticky: 从 top 30 加权随机
    tops = REDIS.zrevrange(key, 0, 29, withscores=True)
    if not tops:
        return None

    if target:
        # 如果有目标站点评分池，合并加权
        site_scores = {}
        for p, _ in tops:
            ss = REDIS.zscore(_site_key(target), p)
            if ss is not None:
                site_scores[p] = ss
        if site_scores:
            tops = [(p, s + site_scores.get(p, 0)) for p, s in tops]

    # 加权随机
    items = [(p, max(s, 1)) for p, s in tops]
    total = sum(w for _, w in items)
    r = random.uniform(0, total)
    upto = 0
    for p, w in items:
        upto += w
        if upto >= r:
            return proxy_meta(p)
    return proxy_meta(items[-1][0]) if items else None

# ── 统计追踪 ──

def record_success(proxy, target=None):
    credit_add(proxy, CREDIT_SUCCESS, target)
    REDIS.hincrby(_stats_key(proxy), "success", 1)
    REDIS.hset(_stats_key(proxy), "last_used", time.time())

def record_fail(proxy, reason="", target=None):
    if reason in ("403", "503"):
        delta = CREDIT_403
    else:
        delta = CREDIT_502
    credit_add(proxy, delta, target)
    REDIS.hincrby(_stats_key(proxy), "fail", 1)
    REDIS.hset(_stats_key(proxy), "last_used", time.time())
    blacklist_add(proxy)

def get_proxy_stats_data():
    """聚合所有代理统计数据"""
    members = REDIS.zrange(KEY_POOL, 0, -1)
    total_s = 0
    total_f = 0
    for p in members:
        s = REDIS.hget(_stats_key(p), "success") or "0"
        f = REDIS.hget(_stats_key(p), "fail") or "0"
        total_s += int(s)
        total_f += int(f)
    total = total_s + total_f
    return {
        "total_success": total_s,
        "total_fail": total_f,
        "success_rate": round(total_s / max(total, 1) * 100, 1),
    }

# ── 粘性会话 ──

def sticky_get(session_id):
    """获取会话绑定的代理"""
    return REDIS.get(f"{KEY_STICKY_PX}{session_id}")

def sticky_set(session_id, proxy):
    REDIS.setex(f"{KEY_STICKY_PX}{session_id}", STICKY_TTL, proxy)

# ══════════════════════════════════════════════════════════
# IP 地理位置 (ip2region)
# ══════════════════════════════════════════════════════════
_ip2region_searcher = None

def _init_ip2region():
    global _ip2region_searcher
    import sys as _sys
    _sys.path.insert(0, "/app/ip2region")
    try:
        from searcher import Searcher
        from util import IPv4
        xdb_path = "/app/data/ip2region.xdb"
        _ip2region_searcher = Searcher(IPv4, xdb_path, None, None)
        print("[dashboard] ip2region 加载成功")
    except Exception as e:
        print(f"[dashboard] ip2region 加载失败: {e}")

def geo_lookup(ip):
    try:
        if not _ip2region_searcher:
            return {}
        result = _ip2region_searcher.search(ip)
        if result:
            parts = result.split("|")
            country, _, raw_p, raw_c, _ = parts[0:5] if len(parts) >= 5 else [result, "", "", "", ""]
            province = raw_p if raw_p and raw_p != "0" else ""
            city = raw_c if raw_c and raw_c != "0" else ""
            is_cn = country == "中国"
            _isp_kw = ['阿里', '电信', '联通', '移动', '腾讯', '百度', '华为', '京东',
                       'Cogent', 'Amazon.com', 'AT&T', 'Comcast', 'China Mobile',
                       'China Unicom', 'Chinanet', 'CTGNet', 'HostRoyale', 'Google',
                       'Microsoft', 'DigitalOcean', 'Hetzner', 'OVH', 'Linode',
                       'FPT Telecom']
            def _is_isp(s):
                return s and any(kw in s for kw in _isp_kw)
            clean_city = "" if _is_isp(city) else city
            clean_province = "" if _is_isp(province) else province
            return {
                "country": country,
                "region": clean_province,
                "city": clean_city,
                "is_china": is_cn,
                "region_label": clean_province or clean_city or country,
            }
    except:
        pass
    return {}

# ══════════════════════════════════════════════════════════
# 延迟检测 (TCP ping)
# ══════════════════════════════════════════════════════════

def http_proxy_test(ip, port, timeout=8):
    "True HTTP proxy validation via httpbin"
    try:
        proxy_url = "http://{}:{}".format(ip, port)
        handler = urllib.request.ProxyHandler({"http": proxy_url})
        opener = urllib.request.build_opener(handler)
        req = urllib.request.Request("http://httpbin.org/ip",
            headers={"User-Agent": "proxy-validator/1.0"})
        start = time.time()
        resp = opener.open(req, timeout=timeout)
        body = resp.read().decode("utf-8", errors="ignore")
        lat = round((time.time() - start) * 1000, 1)
        if resp.status == 200 and "origin" in body:
            return lat
        return None
    except:
        return None

def tcp_ping(ip, port, timeout=3):
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        start = time.time()
        s.connect((ip, int(port)))
        return round((time.time() - start) * 1000, 1)
    except:
        return None
    finally:
        if s:
            try: s.close()
            except: pass

# ══════════════════════════════════════════════════════════
# 代理池同步 + 验证器 (Validator)
# ══════════════════════════════════════════════════════════

CHECK_PROGRESS = {"current": 0, "total": 0}
CHECK_PROGRESS_LOCK = threading.Lock()

# ── 源质评估 (交叉验证共识：隔离垃圾源) ──

def source_quality_eval():
    """评估每个源的 S/A/B 占比，自动黑名单低质源（仅统计已验证代理）"""
    from collections import defaultdict
    src_stats = defaultdict(lambda: {"total": 0, "verified": 0, "S": 0, "A": 0, "B": 0})
    for p in REDIS.zrange(KEY_POOL, 0, -1):
        meta = proxy_meta(p)
        if not meta:
            continue
        src = meta.get("source", "?")
        src_stats[src]["total"] += 1
        lat = meta.get("latency")
        # 仅统计已验证（有 latency + last_check）的代理
        has_check = bool(meta.get("last_check"))
        if lat and lat not in ("9999", "", "0") and has_check:
            src_stats[src]["verified"] += 1
            grade = grade_for_latency(float(lat))
            if grade in ("S", "A", "B"):
                src_stats[src][grade] += 1
    
    for src, s in src_stats.items():
        verified = s["verified"]
        if verified < 8:
            # 已验证样本不足 → 不评估，但也不屏蔽（等更多数据）
            SOURCE_QUALITY_CACHE[src] = {"elite": None, "usable": None, "total": s["total"], "verified": verified}
            continue
        elite_rate = (s["S"] + s["A"]) / verified
        usable_rate = (s["S"] + s["A"] + s["B"]) / verified
        SOURCE_QUALITY_CACHE[src] = {"elite": elite_rate, "usable": usable_rate, "total": s["total"], "verified": verified}
        
        if usable_rate < SOURCE_USABLE_MIN:
            # 可用率极低 → 直接屏蔽
            if src not in SOURCE_BLACKLIST:
                SOURCE_BLACKLIST.add(src)
                print(f"[quality] 屏蔽低质源: {src} (usable={usable_rate:.0%}, elite={elite_rate:.0%}, n={verified})")
        elif elite_rate < SOURCE_BLACKLIST_ELITE and usable_rate < 0.45:
            # 精英率极低 AND 可用率<45% → 次级屏蔽
            if src not in SOURCE_BLACKLIST:
                SOURCE_BLACKLIST.add(src)
                print(f"[quality] 次级屏蔽: {src} (elite={elite_rate:.0%}, usable={usable_rate:.0%}, n={verified})")
        else:
            if src in SOURCE_BLACKLIST:
                SOURCE_BLACKLIST.discard(src)
                print(f"[quality] 恢复源: {src} (elite={elite_rate:.0%}, usable={usable_rate:.0%})")
    
    return src_stats

def refresh_proxy_list():
    """从 proxy-pool API 拉取，增量同步到 Redis"""
    try:
        data = json.loads(urllib.request.urlopen(f"{PROXY_API}/all?type=json", timeout=10).read())
        raw_proxies = data if isinstance(data, list) else data.get("proxies", data.get("data", []))
        if not raw_proxies:
            raw_proxies = json.loads(urllib.request.urlopen(f"{PROXY_API}/get?type=json", timeout=10).read())
    except Exception as e:
        print(f"[harvester] 拉取失败: {e}")
        return

    # 统一格式：list of {"ip": ..., "port": ...}
    proxies = []
    if isinstance(raw_proxies, list):
        proxies = raw_proxies
    elif isinstance(raw_proxies, dict):
        # 可能是 {"proxies": [...], "data": [...]} 或单个代理
        proxies = raw_proxies.get("proxies", raw_proxies.get("data", []))
        if not proxies and "ip" in raw_proxies:
            proxies = [raw_proxies]

    if not proxies:
        print("[harvester] 无代理数据")
        return

    new_count = 0
    for p in proxies:
        if p is None:
            continue
        # proxy-pool API 格式: {"proxy": "ip:port", ...}
        proxy_str = p.get("proxy", "")
        ip = p.get("ip") or p.get("host", "")
        port = str(p.get("port", ""))
        if not ip and ":" in proxy_str:
            ip, port = proxy_str.split(":", 1)
        if not ip or not port:
            continue

        proxy = f"{ip}:{port}"
        if REDIS.zscore(KEY_POOL, proxy) is not None:
            continue

        # 源质关闸：跳过黑名单源
        src = p.get("source", "proxy-pool")
        if src in SOURCE_BLACKLIST:
            continue

        proto = "https" if p.get("https") else p.get("protocol", p.get("type", "http"))
        geo = geo_lookup(ip)
        meta = {
            "ip": ip,
            "port": str(port),
            "protocol": proto,
            "country": geo.get("country", p.get("region", "")),
            "region": geo.get("region", ""),
            "city": geo.get("city", ""),
            "is_china": str(geo.get("is_china", p.get("region","") == "CN")),
            "region_label": geo.get("region_label", p.get("region", "")),
            "source": p.get("source", "proxy-pool"),
            "latency": "",
        }
        credit_init(proxy, meta)
        new_count += 1

    # 清理失联代理
    upstream_set = set()
    for p in proxies:
        ps = p.get("proxy", "")
        if ":" in ps:
            upstream_set.add(ps)
        else:
            upstream_set.add(f"{p.get('ip','')}:{p.get('port','')}")
    for proxy in REDIS.zrange(KEY_POOL, 0, -1):
        if proxy not in upstream_set:
            score = REDIS.zscore(KEY_POOL, proxy)
            if score is not None and score < 10:
                REDIS.zrem(KEY_POOL, proxy)
                REDIS.delete(_proxy_meta_key(proxy))

    print(f"[harvester] 同步完成: {proxy_count()} 个代理 (新增 {new_count})")

def latency_checker():
    """后台线程：分层测速 + C级剔除 + 源质评估 (交叉验证优化)"""
    round_count = 0
    while True:
        try:
            round_count += 1
            # 每 5 轮评估一次源质
            if round_count % 5 == 0:
                source_quality_eval()
            
            proxies = REDIS.zrange(KEY_POOL, 0, -1)
            now = time.time()
            total = len(proxies)
            with CHECK_PROGRESS_LOCK:
                CHECK_PROGRESS["total"] = total
                CHECK_PROGRESS["current"] = 0

            for proxy_str in proxies:
                meta = proxy_meta(proxy_str)
                if not meta or blacklist_check(proxy_str):
                    with CHECK_PROGRESS_LOCK:
                        CHECK_PROGRESS["current"] += 1
                    continue
                # 分层检测间隔: S-已验跳过300s, A/B-跳过120s, C-跳过300s
                _lat = meta.get("latency")
                _last = meta.get("last_check", "")
                skip = False
                if _lat and _lat not in ("9999", "", "0"):
                    grade = grade_for_latency(float(_lat))
                    interval = {"S": 300, "A": 120, "B": 120, "C": 300}.get(grade, 300)
                    # 解析 last_check 时间
                    if _last:
                        try:
                            import datetime as _dt
                            _last_ts = _dt.datetime.strptime(_last, "%Y-%m-%d %H:%M:%S").timestamp()
                            if now - _last_ts < interval:
                                skip = True
                        except:
                            pass
                if skip:
                    with CHECK_PROGRESS_LOCK:
                        CHECK_PROGRESS["current"] += 1
                    continue

                ip = meta["ip"]
                port = int(meta["port"])
                lat = http_proxy_test(ip, port)
                import datetime
                _now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if lat is not None:
                    grade = grade_for_latency(lat)
                    attempts = 1 + int(meta.get("attempts") or "0")
                    proxy_meta_set(proxy_str, latency=str(lat), last_check=_now, success_rate="100", attempts=str(attempts))
                    credit_add(proxy_str, 5)
                    # C级连续检测追踪
                    if grade == "C":
                        c_cnt = 1 + int(REDIS.hget(f"proxy:{proxy_str}", "c_streak") or "0")
                        REDIS.hset(f"proxy:{proxy_str}", "c_streak", c_cnt)
                        if c_cnt >= 2:
                            REDIS.zrem(KEY_POOL, proxy_str)
                            REDIS.delete(_proxy_meta_key(proxy_str))
                            print(f"[validator] 剔除慢性C级: {proxy_str} (C_streak={c_cnt})")
                    else:
                        REDIS.hdel(f"proxy:{proxy_str}", "c_streak")
                else:
                    proxy_meta_set(proxy_str, last_check=_now, success_rate="0")
                    credit_add(proxy_str, -15)

                with CHECK_PROGRESS_LOCK:
                    CHECK_PROGRESS["current"] += 1

            print(f"[validator] 检测完成 {total} 个代理")
        except Exception as e:
            print(f"[validator] 异常: {e}")

        time.sleep(CHECK_INTERVAL)

def proxy_refresher():
    """后台线程：定期拉新代理"""
    while True:
        try:
            refresh_proxy_list()
        except Exception as e:
            print(f"[harvester] 循环异常: {e}")
        time.sleep(CHECK_INTERVAL * 2)

# ══════════════════════════════════════════════════════════
# HTTP 转发服务器 (Proxy Server)
# ══════════════════════════════════════════════════════════

class HTTPProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 30

    def log_message(self, format, *args):
        pass

    def do_GET(self):     self._forward_request("GET")
    def do_POST(self):    self._forward_request("POST")
    def do_PUT(self):     self._forward_request("PUT")
    def do_DELETE(self):  self._forward_request("DELETE")
    def do_PATCH(self):   self._forward_request("PATCH")
    def do_HEAD(self):    self._forward_request("HEAD")
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "http://frp6.ccszxc.xin:43161")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, PATCH, OPTIONS, CONNECT")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length > 0 else b""

    def _forward_request(self, method):
        body = self._read_body()
        target = self.headers.get("X-Proxy-Target", "")
        session_id = self.headers.get("X-Proxy-Session", "")

        tried = set()
        # self.path 可能是绝对 URL (代理模式) 或相对路径
        request_url = self.path
        parsed = urllib.parse.urlparse(self.path)
        if not parsed.netloc:
            # 相对路径 → 从 Host 头重建
            host = self.headers.get("Host", "localhost")
            request_url = f"http://{host}{self.path}"

        for attempt in range(3):
            proxy_meta_info = None
            if session_id:
                sticky_proxy = sticky_get(session_id)
                if sticky_proxy and sticky_proxy not in tried:
                    proxy_meta_info = proxy_meta(sticky_proxy)
                    if proxy_meta_info:
                        tried.add(sticky_proxy)

            if not proxy_meta_info:
                meta_info = get_best_proxy(target=target)
                if not meta_info:
                    self.send_error(502, "无可用代理")
                    return
                proxy_meta_info = meta_info
                tried.add(proxy_meta_info["proxy"])

            proxy = proxy_meta_info
            if session_id and attempt == 0:
                sticky_set(session_id, proxy["proxy"])

            try:
                # 通过代理 IP 发请求
                req = urllib.request.Request(request_url, data=body if method in ("POST", "PUT", "PATCH") else None, method=method)
                skip = {"host", "proxy-connection", "x-proxy-target", "x-proxy-session"}
                for k, v in self.headers.items():
                    if k.lower() not in skip:
                        req.add_header(k, v)

                # 设置代理
                protocol = proxy.get("protocol", "http")
                proxy_url = f"http://{proxy['ip']}:{proxy['port']}"
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({protocol: proxy_url}))
                resp = opener.open(req, timeout=15)
                status = resp.status
                body_content = resp.read()

                # 分析失败原因
                if status in (403, 503):
                    record_fail(proxy["proxy"], "403", target)
                    continue  # 重试
                if status >= 500 and status != 503:
                    record_fail(proxy["proxy"], "502", target)
                    continue

                # 成功
                record_success(proxy["proxy"], target)
                self.send_response(status)
                for k, v in resp.getheaders():
                    if k.lower() not in {"transfer-encoding", "connection"}:
                        self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body_content)
                return

            except Exception as e:
                reason = "timeout" if "timeout" in str(e).lower() else "error"
                record_fail(proxy["proxy"], reason, target)
                continue

        self.send_error(502, "所有代理失败")

    def do_CONNECT(self):
        host, port = self.path.split(":")
        port = int(port)
        tried = set()

        for _ in range(3):
            meta_info = get_best_proxy()
            if not meta_info or meta_info["proxy"] in tried:
                break
            proxy = meta_info
            tried.add(proxy["proxy"])

            try:
                remote_sock = socket.create_connection((proxy["ip"], int(proxy["port"])), timeout=10)
                connect_req = f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n"
                remote_sock.sendall(connect_req.encode())
                resp = b""
                while b"\r\n\r\n" not in resp:
                    resp += remote_sock.recv(4096)
                if b"200" not in resp:
                    remote_sock.close()
                    record_fail(proxy["proxy"], "502")
                    continue

                self.send_response(200, "Connection Established")
                self.end_headers()
                record_success(proxy["proxy"])

                # 双向管道
                client_sock = self.connection  # 不走 self.rfile/wfile
                sockets = [client_sock, remote_sock]
                while True:
                    r, _, _ = select.select(sockets, [], [], 60)
                    if not r: break
                    for sock in r:
                        data = sock.recv(8192)
                        if not data: return
                        target_sock = remote_sock if sock is client_sock else client_sock
                        try:
                            target_sock.sendall(data)
                        except:
                            return
                return
            except Exception as e:
                record_fail(proxy["proxy"], "timeout")
                continue

        self.send_error(502, "CONNECT 失败")

HTTP_SERVER_RUNNING = False
HTTP_SERVER_INSTANCE = None

def start_http_proxy():
    global HTTP_SERVER_RUNNING, HTTP_SERVER_INSTANCE
    if HTTP_SERVER_RUNNING: return
    from http.server import ThreadingHTTPServer
    server = ThreadingHTTPServer(("0.0.0.0", HTTP_PROXY_PORT), HTTPProxyHandler)
    HTTP_SERVER_INSTANCE = server
    HTTP_SERVER_RUNNING = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"[proxy] HTTP 代理启动 :{HTTP_PROXY_PORT}")

def stop_http_proxy():
    global HTTP_SERVER_RUNNING, HTTP_SERVER_INSTANCE
    if not HTTP_SERVER_RUNNING: return
    HTTP_SERVER_INSTANCE.shutdown()
    HTTP_SERVER_RUNNING = False
    HTTP_SERVER_INSTANCE = None
    print("[proxy] HTTP 代理已停止")

# ══════════════════════════════════════════════════════════
# SOCKS5 转发服务器
# ══════════════════════════════════════════════════════════

SOCKS_VERSION = 5
SOCKS5_RUNNING = False
SOCKS5_INSTANCE = None

def handle_socks5_client(client_sock, addr):
    try:
        # 握手
        data = client_sock.recv(262)
        if not data or data[0] != SOCKS_VERSION:
            client_sock.close(); return

        client_sock.sendall(struct.pack("!BB", SOCKS_VERSION, 0))

        # 请求
        data = client_sock.recv(262)
        if len(data) < 5:
            client_sock.close(); return

        cmd = data[1]
        atyp = data[3]

        if cmd != 1:  # 只支持 CONNECT
            client_sock.sendall(struct.pack("!BBBBIH", SOCKS_VERSION, 8, 0, 1, 0, 0))
            client_sock.close(); return

        # 解析目标
        if atyp == 1:  # IPv4
            host = socket.inet_ntoa(data[4:8])
            port = struct.unpack("!H", data[8:10])[0]
        elif atyp == 3:  # 域名
            domain_len = data[4]
            host = data[5:5+domain_len].decode()
            port = struct.unpack("!H", data[5+domain_len:7+domain_len])[0]
        else:
            client_sock.close(); return

        # 选代理（重试）
        tried = set()
        for _ in range(3):
            meta_info = get_best_proxy()
            if not meta_info or meta_info["proxy"] in tried:
                break
            proxy = meta_info
            tried.add(proxy["proxy"])

            try:
                remote = socket.create_connection((proxy["ip"], int(proxy["port"])), timeout=10)
                # CONNECT 握手
                connect_req = f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n"
                remote.sendall(connect_req.encode())
                resp = b""
                while b"\r\n\r\n" not in resp:
                    resp += remote.recv(4096)
                if b"200" not in resp:
                    remote.close()
                    record_fail(proxy["proxy"], "502")
                    continue

                client_sock.sendall(struct.pack("!BBBBIH", SOCKS_VERSION, 0, 0, 1, 0, 0))
                record_success(proxy["proxy"])

                # 双向管道
                sockets = [client_sock, remote]
                while True:
                    r, _, _ = select.select(sockets, [], [], 60)
                    if not r: break
                    for sock in r:
                        data = sock.recv(8192)
                        if not data: return
                        target_sock = remote if sock is client_sock else client_sock
                        try: target_sock.sendall(data)
                        except: return
                return
            except:
                record_fail(proxy["proxy"], "timeout")
                continue

        client_sock.sendall(struct.pack("!BBBBIH", SOCKS_VERSION, 1, 0, 1, 0, 0))
    except: pass
    finally:
        try: client_sock.close()
        except: pass

def start_socks5_proxy():
    global SOCKS5_RUNNING, SOCKS5_INSTANCE
    if SOCKS5_RUNNING: return
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind(("0.0.0.0", SOCKS5_PROXY_PORT))
    server_sock.listen(128)
    SOCKS5_INSTANCE = server_sock
    SOCKS5_RUNNING = True
    def _accept():
        while SOCKS5_RUNNING:
            try:
                client, addr = server_sock.accept()
                threading.Thread(target=handle_socks5_client, args=(client, addr), daemon=True).start()
            except: pass
    threading.Thread(target=_accept, daemon=True).start()
    print(f"[proxy] SOCKS5 代理启动 :{SOCKS5_PROXY_PORT}")

def stop_socks5_proxy():
    global SOCKS5_RUNNING, SOCKS5_INSTANCE
    if not SOCKS5_RUNNING: return
    SOCKS5_RUNNING = False
    try: SOCKS5_INSTANCE.close()
    except: pass
    SOCKS5_INSTANCE = None
    print("[proxy] SOCKS5 代理已停止")

# ══════════════════════════════════════════════════════════
# Dashboard Web API
# ══════════════════════════════════════════════════════════

def grade_for_latency(ms):
    if ms is None: return "C"
    if ms < 200: return "S"
    if ms < 400: return "A"
    if ms < 1200: return "B"
    return "C"

def stability_score(meta):
    """综合稳定性评分 0-1 (scylla 式): latency + success_rate + attempts"""
    lat = meta.get("latency")
    if not lat or lat in ("9999", "", "0"):
        return 0.0
    lat = float(lat)
    # 延迟分: 0ms→1.0, 1200ms→0.5, 5000ms→0.2
    lat_score = max(0, min(1, 200 / (lat + 100)))
    # 成功率: "100"→1.0, "0"→0.0, 缺失→0.5
    sr = meta.get("success_rate")
    if sr is None or sr == "":
        sr_score = 0.5
    else:
        sr_score = float(sr) / 100.0
    # 测试次数: 1次→0.3, 5次→0.7, 10次→0.9
    attempts = int(meta.get("attempts") or 0)
    att_score = min(1.0, attempts / 10.0) * 0.8 + 0.2
    # 加权: 延迟 50% + 成功率 30% + 测试次数 20%
    return round(lat_score * 0.5 + sr_score * 0.3 + att_score * 0.2, 3)

def proxy_anonymity_check(ip, port):
    """通过代理请求 ipify.org, 对比真实 IP 判定匿名度 (scylla 式)"""
    import urllib.request, ssl, socket
    ctx = ssl.create_default_context()
    ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    proxy_url = f"http://{ip}:{port}"
    try:
        req = urllib.request.Request("https://api.ipify.org?format=json")
        req.set_proxy(proxy_url, "https")
        resp = urllib.request.urlopen(req, timeout=8, context=ctx)
        data = json.loads(resp.read())
        proxy_ip = data.get("ip", "")
        if proxy_ip == ip:
            return "transparent"
        # 检查是否暴露了代理信息（elite 级不暴露任何头）
        return "anonymous"  # 简化: 非透明即匿名(近似elite需额外检测)
    except:
        return "unknown"

class DashboardHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args): pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/proxies":    self.api_proxies()
        elif path == "/api/stats":    self.api_stats()
        elif path == "/api/export":   self.api_export()
        elif path == "/api/check":    self.api_check()
        elif path == "/api/services": self.api_services()
        elif path == "/api/select":   self.api_select()
        elif path == "/api/strategy": self.api_strategy()
        elif path == "/api/nodes":    self.api_nodes()
        elif path == "/api/proxy-stats": self.api_proxy_stats()
        elif path == "/api/proxies/random": self.api_random_proxy()
        else: self.serve_static(path)

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/api/check":      self.api_check_post()
        elif path == "/api/services": self.api_services_post()
        elif path == "/api/select":   self.api_select_post()
        elif path == "/api/strategy": self.api_strategy_post()
        elif path == "/api/nodes":    self.api_nodes_post()
        else: self.send_response(404); self.end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "http://frp6.ccszxc.xin:43161")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def json_response(self, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "http://frp6.ccszxc.xin:43161")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def serve_static(self, path):
        if path == "/": path = "/index.html"
        file_path = f"/app/static{path}"
        content_types = {".html": "text/html", ".js": "application/javascript", ".css": "text/css"}
        ct = content_types.get(os.path.splitext(path)[1], "text/plain")
        try:
            with open(file_path, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", f"{ct}; charset=utf-8")
            # CSP: script-src 'self' 仅允许外部JS (交叉验证安全共识)
            if path.endswith(".html"):
                self.send_header("Content-Security-Policy",
                    "default-src 'self'; script-src 'self'; "
                    "style-src 'self' https://cdn.jsdelivr.net; "
                    "connect-src 'self'; img-src 'self' data:; "
                    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_response(404); self.end_headers()

    # ── API ──

    def api_proxies(self):
        """分页+过滤 API (scylla 式): ?limit=50&page=1&grade=S,A&country=CN&https=true&anon=anonymous"""
        from urllib.parse import parse_qs
        params = parse_qs(self.path.split("?")[1]) if "?" in self.path else {}
        limit = int(params.get("limit", [100])[0])
        page = int(params.get("page", [1])[0])
        grade_filter = params.get("grade", [None])[0]
        country_filter = params.get("country", [None])[0]
        https_only = params.get("https", [None])[0]
        anon_filter = params.get("anon", [None])[0]
        source_filter = params.get("source", [None])[0]
        
        proxies = all_proxies()
        # 过滤
        if grade_filter:
            grades = set(g.strip().upper() for g in grade_filter.split(","))
            proxies = [p for p in proxies if p.get("speed") in grades]
        if country_filter:
            countries = set(c.strip().upper() for c in country_filter.split(","))
            proxies = [p for p in proxies if (p.get("country") or "").upper() in countries]
        if https_only in ("true", "1", "yes"):
            proxies = [p for p in proxies if p.get("protocol") == "https"]
        if anon_filter:
            proxies = [p for p in proxies if p.get("anonymity") == anon_filter]
        if source_filter:
            sources = set(s.strip() for s in source_filter.split(","))
            proxies = [p for p in proxies if p.get("source") in sources]
        
        total = len(proxies)
        # 无参数 → 返回纯数组 (前端兼容)
        if not any([grade_filter, country_filter, https_only, anon_filter, source_filter]) and limit == 100:
            self.json_response(proxies)
            return
        total_page = max(1, (total + limit - 1) // limit)
        start = (page - 1) * limit
        result = {
            "proxies": proxies[start:start + limit],
            "count": total,
            "per_page": limit,
            "page": page,
            "total_page": total_page,
        }
        self.json_response(result)

    def api_stats(self):
        total = proxy_count()
        members = REDIS.zrange(KEY_POOL, 0, -1)
        grades = {"S": 0, "A": 0, "B": 0, "C": 0}
        latencies = []
        stabilities = []
        anon_dist = {"anonymous": 0, "transparent": 0, "unknown": 0}
        for p in members:
            meta = proxy_meta(p)
            if meta:
                lat = meta.get("latency")
                if lat and lat not in ("9999", "", "0"):
                    latencies.append(float(lat))
                grades[grade_for_latency(float(lat) if lat else None)] += 1
                stab = stability_score(meta)
                stabilities.append(stab)
                anon = meta.get("anonymity") or "unknown"
                anon_dist[anon] = anon_dist.get(anon, 0) + 1

        with CHECK_PROGRESS_LOCK:
            checking = (CHECK_PROGRESS["total"] > 0 and CHECK_PROGRESS["current"] < CHECK_PROGRESS["total"])
            prog = dict(CHECK_PROGRESS)

        bl_count = REDIS.scard(KEY_BLACKLIST) or 0

        self.json_response({
            "total": total,
            "verified": total,
            "blacklisted": bl_count,
            "S": grades["S"], "A": grades["A"], "B": grades["B"], "C": grades["C"],
            "avg_latency": round(sum(latencies) / len(latencies), 1) if latencies else 0,
            "avg_stability": round(sum(stabilities) / len(stabilities), 3) if stabilities else 0,
            "anonymity": anon_dist,
            "source_blacklist": list(SOURCE_BLACKLIST),
            "source_quality": SOURCE_QUALITY_CACHE,
            "checking": checking,
            "check_progress": prog,
        })

    def api_export(self):
        params = {}
        qs = self.path.split("?")[-1] if "?" in self.path else ""
        for p in qs.split("&"):
            if "=" in p: k, v = p.split("=", 1); params[k] = v

        proxies = all_proxies()
        protocol = params.get("protocol", "")
        speed = params.get("speed", "")
        china_only = params.get("china", "")

        if protocol: proxies = [p for p in proxies if p.get("protocol") == protocol]
        if speed: proxies = [p for p in proxies if p.get("speed", p.get("grade", "")) == speed]
        if china_only: proxies = [p for p in proxies if p.get("is_china") in ("True", "true", "1")]

        body = "\n".join(f"{p['ip']}:{p['port']}" for p in proxies).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Disposition", "attachment; filename=proxies.txt")
        self.send_header("Access-Control-Allow-Origin", "http://frp6.ccszxc.xin:43161")
        self.end_headers()
        self.wfile.write(body)

    def api_check(self):
        """触发立即检测"""
        threading.Thread(target=latency_checker, daemon=True).start()
        self.json_response({"status": "started"})

    def api_services(self):
        self.json_response({
            "http":  {"running": HTTP_SERVER_RUNNING,  "port": HTTP_PROXY_PORT},
            "socks5":{"running": SOCKS5_RUNNING, "port": SOCKS5_PROXY_PORT},
        })

    def api_services_post(self):
        try:
            body_len = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(body_len))
        except: body = {}
        action = body.get("action", "")
        service = body.get("service", "")

        if service == "http":
            if action == "start": start_http_proxy()
            elif action == "stop": stop_http_proxy()
        elif service == "socks5":
            if action == "start": start_socks5_proxy()
            elif action == "stop": stop_socks5_proxy()
        self.api_services()

    def api_select(self):
        sel = REDIS.get(KEY_SELECTED)
        info = proxy_meta(sel) if sel else None
        available = []
        for p in REDIS.zrange(KEY_POOL, 0, -1):
            m = proxy_meta(p)
            if m:
                m["speed"] = grade_for_latency(float(m["latency"]) if m.get("latency") else None)
                available.append(m)
        available.sort(key=lambda x: float(x.get("latency") or "9999"))
        self.json_response({"selected": sel, "info": info, "available": available})

    def api_select_post(self):
        try:
            body_len = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(body_len))
        except: body = {}
        if body.get("action") == "set":
            REDIS.set(KEY_SELECTED, body.get("proxy", ""))
        elif body.get("action") == "clear":
            REDIS.delete(KEY_SELECTED)
        self.api_select()

    def api_strategy(self):
        strat = REDIS.get(KEY_STRATEGY) or "balanced"
        self.json_response({"strategy": strat, "available": ["balanced", "latency", "sticky"]})

    def api_strategy_post(self):
        try:
            body_len = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(body_len))
        except: body = {}
        strat = body.get("strategy", "")
        if strat in ("balanced", "latency", "sticky"):
            REDIS.set(KEY_STRATEGY, strat)
            print(f"[strategy] 切换为: {strat}")
        self.api_strategy()

    def api_random_proxy(self):
        """爬虫主动拉取模式：返回一个最优代理"""
        proxy = get_best_proxy()
        if not proxy:
            self.send_response(503); self.end_headers()
            return
        self.json_response({"proxy": f"{proxy['ip']}:{proxy['port']}", "protocol": proxy.get("protocol", "http")})

    # ── 远程节点 ──
    def api_nodes(self):
        self.json_response({"nodes": {}})  # 预留

    def api_nodes_post(self):
        self.api_nodes()

    def api_proxy_stats(self):
        self.json_response(get_proxy_stats_data())

# ══════════════════════════════════════════════════════════
# 启动
# ══════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("[dashboard] 连接 Redis...")
    try:
        REDIS.ping()
        print("[dashboard] Redis 连接成功")
    except Exception as e:
        print(f"[dashboard] Redis 连接失败: {e}，继续运行（功能受限）")

    _init_ip2region()
    print("[dashboard] 首次拉取代理...")
    refresh_proxy_list()
    print(f"[pool] 当前 {proxy_count()} 个代理")

    # 首次源质评估 (交叉验证共识)
    print("[quality] 首次源质评估...")
    source_quality_eval()

    print("[dashboard] 启动后台线程...")
    threading.Thread(target=latency_checker, daemon=True).start()
    threading.Thread(target=proxy_refresher, daemon=True).start()

    from http.server import ThreadingHTTPServer
    port = int(os.environ.get("PORT", 5050))
    server = ThreadingHTTPServer(("0.0.0.0", port), DashboardHandler)
    print(f"[dashboard] Dashboard 运行在 :{port}")
    server.serve_forever()