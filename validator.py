#!/usr/bin/env python3
"""后台验证+采集引擎 — Redis DB1 代理池维护"""
import redis, time, socket, threading, sys, os, datetime, json, urllib.request, random
from concurrent.futures import ThreadPoolExecutor, as_completed

import struct

KEY_POOL = "proxies:pool"
PFX_PROXY = "proxy:"
CHECK_INTERVAL = 30          # 每轮验证间隔
HARVEST_INTERVAL = 300       # 采集间隔 (5分钟)
VALIDATE_THREADS = 20        # 验证线程数 — 降为20，避免Redis连接池爆满
VALIDATE_TIMEOUT = 2           # 超时秒数
CREDIT_MAX = 100
SUBMIT_CHUNK = 1000          # 分批提交大小，防内存爆炸

# C1: 正确使用 ConnectionPool
POOL = redis.ConnectionPool(
    host=os.environ.get("REDIS_HOST", "proxy-redis"), port=6379, db=1,
    max_connections=VALIDATE_THREADS * 4,
    socket_connect_timeout=5, socket_timeout=5,
    decode_responses=True,
)
REDIS = redis.Redis(connection_pool=POOL)

# ── GeoIP ──
# 在线 Geo API + Redis 7 天缓存。验证器每次成功/失败都把结果写入 DB，
# 且请求在线 API 时会通过代理池轮换，避免单出口 IP 限流。
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import geo as geo_resolver
except Exception:
    geo_resolver = None

def resolve_geo_and_store(ip, proxy_key, force=False):
    if not geo_resolver:
        return "?", "?"
    try:
        data = geo_resolver.resolve_and_store(ip, proxy_key=proxy_key, force=force)
        if data:
            return data.get("countryCode", "?") or "?", geo_resolver.resolve(ip)
        return geo_resolver.resolve_region(ip), geo_resolver.resolve(ip)
    except Exception:
        return "?", "?"

# ── 信用分 ──
# Lua 脚本：原子化 credit_add（zincrby→zrem→delete）
CREDIT_SCRIPT = REDIS.register_script("""
    local s = redis.call('ZINCRBY', KEYS[1], ARGV[1], ARGV[2])
    if s < 0 then
        redis.call('ZREM', KEYS[1], ARGV[2])
        redis.call('DEL', KEYS[2] .. ARGV[2])
        return 1
    end
    if s > tonumber(ARGV[3]) then
        redis.call('ZADD', KEYS[1], ARGV[3], ARGV[2])
    end
    return 0
""")

def credit_add(proxy, delta):
    """原子化加减分，防并发竞态"""
    return CREDIT_SCRIPT(keys=[KEY_POOL, PFX_PROXY], args=[delta, proxy, CREDIT_MAX]) == 1

def proxy_count():
    return REDIS.zcard(KEY_POOL) or 0

# ── 探活（按协议分发）──
TARGET_HOST = "www.qq.com"
TARGET_IP = "103.7.30.123"   # qq.com 固定 IP，避免 DNS 依赖

def http_test(ip, port):
    """HTTP 代理检测：发绝对 URI 请求，验证代理转发能力"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(VALIDATE_TIMEOUT)
            t0 = time.time()
            s.connect((ip, int(port)))
            # 绝对 URI 才走代理转发 — 浏览器配 HTTP 代理时的标准行为
            s.send(f"GET http://{TARGET_HOST}/ HTTP/1.1\r\nHost: {TARGET_HOST}\r\nConnection: close\r\n\r\n".encode())
            resp = b""
            while True:
                try:
                    chunk = s.recv(4096)
                    if not chunk: break
                    resp += chunk
                except Exception: break
            lat = int((time.time() - t0) * 1000)
            # 代理返回 HTTP 响应（200/301/302 等）= 转发成功
            # 若返回 400/502/503 等不含典型标记的也算通了（服务端响应）
            if resp and (b"HTTP/" in resp or b"html" in resp.lower() or len(resp) > 200):
                return min(lat, 9999)
    except Exception:
        pass
    return None

def socks4_test(ip, port):
    """SOCKS4 代理检测：CONNECT 到目标 IP:80"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(VALIDATE_TIMEOUT)
            t0 = time.time()
            s.connect((ip, int(port)))
            # SOCKS4 CONNECT 请求: ver=4, cmd=1(CONNECT), port=80, ip=目标, userid=空
            target_bytes = socket.inet_aton(TARGET_IP)
            req = b"\x04\x01" + struct.pack(">H", 80) + target_bytes + b"\x00"
            s.send(req)
            resp = s.recv(8)
            lat = int((time.time() - t0) * 1000)
            # 响应: ver=0, rep=90(请求允许), port, ip
            if len(resp) >= 2 and resp[1] == 0x5a:  # 0x5a = 90 = granted
                return min(lat, 9999)
    except Exception:
        pass
    return None

def socks5_test(ip, port):
    """SOCKS5 代理检测：无认证握手 + CONNECT 到目标"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(VALIDATE_TIMEOUT)
            t0 = time.time()
            s.connect((ip, int(port)))
            # 握手: ver=5, nmethods=1, method=0(无认证)
            s.send(b"\x05\x01\x00")
            resp = s.recv(2)
            if len(resp) < 2 or resp[0] != 5 or resp[1] != 0:
                return None
            # CONNECT 请求: ver=5, cmd=1, rsv=0, atyp=1(IPv4), addr, port
            target_bytes = socket.inet_aton(TARGET_IP)
            req = b"\x05\x01\x00\x01" + target_bytes + struct.pack(">H", 80)
            s.send(req)
            resp = s.recv(10)
            lat = int((time.time() - t0) * 1000)
            # 响应: ver=5, rep=0(成功), rsv=0, atyp=1, addr(4), port(2)
            if len(resp) >= 2 and resp[0] == 5 and resp[1] == 0:
                return min(lat, 9999)
    except Exception:
        pass
    return None

def grade_for_latency(ms):
    if ms <= 0: return "D"
    if ms < 500: return "S"
    if ms < 1000: return "A"
    if ms < 3000: return "B"
    if ms < 5000: return "C"
    return "D"

def grade_for_delay(delay):
    if delay <= 0: return "d"
    if delay < 500: return "s"
    if delay < 1000: return "a"
    if delay < 3000: return "b"
    if delay < 5000: return "c"
    return "d"

# ── 中国 D 级清理 ──
def cleanup_china_d(executor):
    """扫描所有代理，删除中国 D 级（拉闸货直接扔）。"""
    proxies = REDIS.zrange(KEY_POOL, 0, -1)
    if not proxies:
        return 0
    removed = 0
    # 分批处理
    for i in range(0, len(proxies), SUBMIT_CHUNK):
        chunk = proxies[i:i + SUBMIT_CHUNK]
        pipe = REDIS.pipeline(transaction=False)
        for p in chunk:
            pipe.hgetall(f"{PFX_PROXY}{p}")
        metas = pipe.execute()
        for p, meta in zip(chunk, metas):
            if not meta:
                continue
            country = (meta.get("country") or "").upper()
            lat_str = meta.get("latency", "")
            if country == "CN":
                try:
                    lat = float(lat_str) if lat_str and lat_str not in ("9999", "") else 9999
                except (ValueError, TypeError):
                    lat = 9999
                if grade_for_latency(lat) == "D":
                    REDIS.zrem(KEY_POOL, p)
                    REDIS.delete(f"{PFX_PROXY}{p}")
                    removed += 1
    if removed:
        print(f"[cleanup-cn-d] removed {removed} China D-grade proxies")
    return removed

# ── 海外 D 级走代理复测 ──
def _pick_test_proxies():
    """从 S/A 级海外 HTTP 代理中选 5 个做测试路由"""
    candidates = []
    try:
        for m in REDIS.zrevrange(KEY_POOL, 0, 500):
            hd = REDIS.hgetall(f"{PFX_PROXY}{m}")
            if not hd:
                continue
            proto = (hd.get("protocol") or "").lower()
            country = (hd.get("country") or "").upper()
            lat_str = hd.get("latency", "")
            if proto != "http" or country in ("CN", "?", ""):
                continue
            try:
                lat = float(lat_str) if lat_str and lat_str not in ("9999", "") else 9999
            except (ValueError, TypeError):
                continue
            if lat < 3000:  # S or A
                candidates.append(m)
            if len(candidates) >= 20:
                break
    except Exception:
        pass
    random.shuffle(candidates)
    return candidates[:5]

def _http_test_via_proxy(ip, port, via_proxy):
    """通过中间代理 via_proxy 测试目标 HTTP 代理 ip:port"""
    try:
        via_ip, via_port = via_proxy.split(":", 1)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(VALIDATE_TIMEOUT + 3)
            t0 = time.time()
            s.connect((via_ip, int(via_port)))
            # HTTP CONNECT 打隧道到目标代理
            s.send(f"CONNECT {ip}:{port} HTTP/1.1\r\nHost: {ip}:{port}\r\n\r\n".encode())
            resp = s.recv(4096)
            if b"200" not in resp:
                return None
            # 通过隧道向目标代理发请求
            s.send(f"GET http://{TARGET_HOST}/ HTTP/1.1\r\nHost: {TARGET_HOST}\r\nConnection: close\r\n\r\n".encode())
            data = b""
            while True:
                try:
                    chunk = s.recv(4096)
                    if not chunk: break
                    data += chunk
                except Exception: break
            lat = int((time.time() - t0) * 1000)
            if data and (b"HTTP/" in data or len(data) > 200):
                return min(lat, 9999)
    except Exception:
        pass
    return None

def retest_overseas_d(executor):
    """海外 D 级代理走已知好代理复测。仍 D 级的删除。"""
    test_routes = _pick_test_proxies()
    if not test_routes:
        print("[retest-overseas] no test routes available, skip")
        return 0

    proxies = REDIS.zrange(KEY_POOL, 0, -1)
    if not proxies:
        return 0

    retested = 0
    rescued = 0
    removed = 0
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for i in range(0, len(proxies), SUBMIT_CHUNK):
        chunk = proxies[i:i + SUBMIT_CHUNK]
        pipe = REDIS.pipeline(transaction=False)
        for p in chunk:
            pipe.hgetall(f"{PFX_PROXY}{p}")
        metas = pipe.execute()

        for p, meta in zip(chunk, metas):
            if not meta:
                continue
            country = (meta.get("country") or "").upper()
            proto = (meta.get("protocol") or "").lower()
            lat_str = meta.get("latency", "")
            try:
                lat = float(lat_str) if lat_str and lat_str not in ("9999", "") else 9999
            except (ValueError, TypeError):
                lat = 9999

            # 只复测海外 D 级 HTTP 代理
            if country == "CN" or proto != "http" or grade_for_latency(lat) != "D":
                continue

            ip = meta.get("ip", "") or p.split(":")[0]
            port_str = meta.get("port", "") or (p.split(":")[1] if ":" in p else "0")
            try:
                port = int(port_str)
            except (ValueError, TypeError):
                continue

            # 串行试每个测试路由
            new_lat = None
            for route in test_routes:
                new_lat = _http_test_via_proxy(ip, port, route)
                if new_lat is not None:
                    break

            retested += 1
            if new_lat is not None:
                grade = grade_for_latency(new_lat)
                country_cc, city = resolve_geo_and_store(ip, p, force=True)
                REDIS.hset(f"{PFX_PROXY}{p}", mapping={
                    "latency": str(new_lat), "last_check": now,
                    "country": country_cc, "location": city,
                })
                credit_add(p, 5)
                rescued += 1
            else:
                REDIS.zrem(KEY_POOL, p)
                REDIS.delete(f"{PFX_PROXY}{p}")
                removed += 1

    msg = f"retested={retested} rescued={rescued} removed={removed}"
    if test_routes:
        msg += f" routes={len(test_routes)}"
    print(f"[retest-overseas] {msg}")
    return rescued

# ── 采集新代理 ──
def harvest_new_proxies():
    """调用 new_fetcher.py 拉取新代理，返回本轮新增数"""
    before = proxy_count()
    try:
        fetcher_path = os.path.join(os.path.dirname(__file__), "new_fetcher.py")
        if not os.path.exists(fetcher_path):
            print("[harvest] fetcher not found, skip")
            return 0

        import subprocess
        result = subprocess.run(
            [sys.executable, fetcher_path],
            capture_output=True, text=True, timeout=300,
            env={**os.environ, "REDIS_HOST": os.environ.get("REDIS_HOST", "proxy-redis")}
        )
        # C6: 检查 returncode
        if result.returncode != 0:
            print(f"[harvest] subprocess exit={result.returncode}")
        if result.stdout:
            lines = result.stdout.strip().split("\n")
            for line in lines[-5:]:
                print(f"[harvest] {line}")
        if result.stderr:
            for line in result.stderr.strip().split("\n")[-3:]:
                print(f"[harvest:err] {line}")
        after = proxy_count()
        return max(0, after - before)
    except Exception as e:
        print(f"[harvest] error: {e}")
        return 0

# ── 验证核心 ──
def validate_one(proxy_str, meta):
    """单代理验证 — 按协议分发到对应检测函数"""
    if not meta:
        return ("skipped", None)

    ip = meta.get("ip", "")
    port_str = meta.get("port", "")
    proto = meta.get("protocol", "http").lower()
    if not ip:
        ip = proxy_str.split(":")[0]
    if not port_str:
        parts = proxy_str.split(":")
        port_str = parts[1] if len(parts) > 1 else "0"

    try:
        port = int(port_str)
    except (ValueError, TypeError):
        return ("skipped", None)

    # 按协议分发。？=未知协议 → 全测 HTTP→SOCKS5→SOCKS4，命中后记录正确协议
    proto_changed = False
    if proto == "?":
        lat = http_test(ip, port)
        if lat is not None:
            proto = "http"; proto_changed = True
        else:
            lat = socks5_test(ip, port)
            if lat is not None:
                proto = "socks5"; proto_changed = True
            else:
                lat = socks4_test(ip, port)
                if lat is not None:
                    proto = "socks4"; proto_changed = True
    elif proto.startswith("socks5"):
        lat = socks5_test(ip, port)
    elif proto.startswith("socks4"):
        lat = socks4_test(ip, port)
    else:
        lat = http_test(ip, port)
        # HTTP 失败了也试试 SOCKS — 采集源可能标错协议
        if lat is None:
            lat2 = socks5_test(ip, port)
            if lat2 is not None:
                lat = lat2; proto = "socks5"; proto_changed = True
            else:
                lat2 = socks4_test(ip, port)
                if lat2 is not None:
                    lat = lat2; proto = "socks4"; proto_changed = True

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if lat is not None:
        grade = grade_for_latency(lat)
        # 获取 Geo 信息并存储（在线 API + Redis 7 天缓存 + 代理池轮换）
        country, city = resolve_geo_and_store(ip, proxy_str, force=False)
        hset_args = {
            "latency": str(lat), "last_check": now, "success_rate": "100",
            "country": country, "location": city
        }
        if proto_changed:
            hset_args["protocol"] = proto
        REDIS.hset(f"{PFX_PROXY}{proxy_str}", mapping=hset_args)
        credit_add(proxy_str, 5)
        return ("ok", grade)
    else:
        # 失败也把 IP 加入 geo 队列，Dashboard 展示和后续筛选仍能拿到准确地区。
        if geo_resolver:
            try:
                geo_resolver.resolve(ip)
            except Exception:
                pass
        REDIS.hset(f"{PFX_PROXY}{proxy_str}", mapping={
            "last_check": now, "success_rate": "0"
        })
        removed = credit_add(proxy_str, -15)
        return ("removed" if removed else "fail", None)

def validate_all(executor):
    """多线程验证全部代理"""
    proxies = REDIS.zrange(KEY_POOL, 0, -1)
    total = len(proxies)
    if total == 0:
        print("[validate] pool empty")
        return

    results = {"ok": 0, "fail": 0, "removed": 0, "skipped_total": 0,
               "grades": {"S": 0, "A": 0, "B": 0, "C": 0, "D": 0}}
    now_ts = time.time()

    # Submit all tasks — executor handles queue with 50 workers
    to_check = []
    skipped = 0
    for i in range(0, total, SUBMIT_CHUNK):
        chunk = proxies[i:i + SUBMIT_CHUNK]
        pipe = REDIS.pipeline(transaction=False)
        for p in chunk:
            pipe.hgetall(f"{PFX_PROXY}{p}")
        metas = pipe.execute()

        for p, meta in zip(chunk, metas):
            if not meta:
                to_check.append((p, meta))
                continue
            lc = meta.get("last_check", "")
            lat = meta.get("latency", "")
            if lat and lat not in ("9999", "", "0") and lc:
                try:
                    ts = datetime.datetime.strptime(lc, "%Y-%m-%d %H:%M:%S").timestamp()
                    grade = grade_for_latency(float(lat))
                    skip_sec = {"S": 300, "A": 120, "B": 120, "C": 60}.get(grade, 60)
                    if now_ts - ts < skip_sec:
                        skipped += 1
                        continue
                except Exception:
                    pass
            to_check.append((p, meta))

    results["skipped_total"] = skipped
    print(f"[validate] {total} total → skip={skipped} check={len(to_check)}")

    if not to_check:
        return

    futures = {executor.submit(validate_one, p, meta): p for p, meta in to_check}
    checked = 0
    for f in as_completed(futures):
        try:
            status, grade = f.result()
            checked += 1
            if status == "ok" and grade:
                results["ok"] += 1
                results["grades"][grade] = results["grades"].get(grade, 0) + 1
            elif status == "removed":
                results["removed"] += 1
            elif status == "fail":
                results["fail"] += 1
        except Exception:
            results["fail"] += 1

    print(f"[validate] checked={checked} skipped={results['skipped_total']} "
          f"ok={results['ok']} fail={results['fail']} "
          f"removed={results['removed']} "
          f"S={results['grades']['S']} A={results['grades']['A']} "
          f"B={results['grades']['B']} C={results['grades']['C']} "
          f"D={results['grades']['D']}")

# ── 主循环 ──
def main():
    print(f"[engine] start — pool={proxy_count()}")
    last_harvest = 0
    round_num = 0

    executor = ThreadPoolExecutor(max_workers=VALIDATE_THREADS, thread_name_prefix="val")

    try:
        while True:
            try:
                now = time.time()
                round_num += 1

                if now - last_harvest > HARVEST_INTERVAL:
                    print("[engine] harvesting...")
                    added = harvest_new_proxies()
                    print(f"[engine] harvest done: +{added}")
                    last_harvest = now

                t0 = time.time()
                validate_all(executor)
                elapsed = time.time() - t0

                # 每 10 轮清理中国 D 级（拉闸货直接扔）
                if round_num % 10 == 0:
                    cleanup_china_d(executor)

                # 每 5 轮海外 D 级走代理复测
                if round_num % 5 == 0:
                    retest_overseas_d(executor)

                sleep_for = max(0, CHECK_INTERVAL - elapsed)
                print(f"[engine] round={elapsed:.1f}s sleep={sleep_for:.1f}s")
                if sleep_for > 0:
                    time.sleep(sleep_for)

            except Exception as e:
                print(f"[engine] error: {e}")
                time.sleep(CHECK_INTERVAL)

    finally:
        executor.shutdown(wait=False)

if __name__ == "__main__":
    main()
