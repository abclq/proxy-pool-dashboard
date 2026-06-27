#!/usr/bin/env python3
"""后台验证+采集引擎 — Redis DB1 代理池维护"""
import redis, time, socket, threading, sys, os, datetime, json, urllib.request

REDIS = redis.Redis(host=os.environ.get("REDIS_HOST", "proxy-redis"), port=6379, db=1, decode_responses=True,
                     socket_connect_timeout=5, socket_timeout=5)
KEY_POOL = "proxies:pool"
PFX_PROXY = "proxy:"
CHECK_INTERVAL = 30          # 每轮验证间隔
HARVEST_INTERVAL = 300       # 采集间隔 (5分钟)
VALIDATE_THREADS = 200       # 验证线程数
VALIDATE_TIMEOUT = 5         # 超时秒数
CREDIT_MAX = 100

# ── GeoIP ──
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from searcher import new_with_file_only
    from util import IPv4
    SEARCHER = new_with_file_only(IPv4, os.path.join(os.path.dirname(__file__), "data", "ip2region.xdb"))
except:
    SEARCHER = None

def geo(ip):
    if not SEARCHER:
        return "unknown", "unknown"
    try:
        r = SEARCHER.search(ip)
        if r and "|" in r:
            parts = r.split("|")
            return parts[0], parts[-1]  # country, code
    except:
        pass
    return "unknown", "unknown"

# ── 信用分 ──
def credit_add(proxy, delta):
    s = REDIS.zincrby(KEY_POOL, delta, proxy)
    if (s or 0) < 0:
        REDIS.zrem(KEY_POOL, proxy)
        REDIS.delete(f"{PFX_PROXY}{proxy}")
        return True
    if s and s > CREDIT_MAX:
        REDIS.zadd(KEY_POOL, {proxy: CREDIT_MAX})
    return False

def proxy_count():
    return REDIS.zcard(KEY_POOL) or 0

# ── HTTP 探活 ──
def http_test(ip, port):
    """HEAD 请求 qq.com, 返回延迟 ms 或 None"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(VALIDATE_TIMEOUT)
        t0 = time.time()
        s.connect((ip, int(port)))
        s.send(b"HEAD / HTTP/1.1\r\nHost: www.qq.com\r\nConnection: close\r\n\r\n")
        resp = b""
        while True:
            try:
                chunk = s.recv(4096)
                if not chunk: break
                resp += chunk
            except: break
        s.close()
        lat = int((time.time() - t0) * 1000)
        if b"HTTP/" in resp:
            return min(lat, 9999)
    except:
        pass
    return None

def grade_for_latency(ms):
    if ms < 500: return "S"
    if ms < 1000: return "A"
    if ms < 3000: return "B"
    return "C"

# ── 采集新代理 ──
def harvest_new_proxies():
    """调用 new_fetcher.py 拉取新代理"""
    try:
        fetcher_path = os.path.join(os.path.dirname(__file__), "new_fetcher.py")
        if not os.path.exists(fetcher_path):
            print("[harvest] fetcher not found, skip")
            return 0

        # 直接 import 执行
        import importlib.util
        spec = importlib.util.spec_from_file_location("new_fetcher", fetcher_path)
        mod = importlib.util.module_from_spec(spec)
        # new_fetcher 是脚本式，直接 exec
        code = open(fetcher_path).read()
        exec(compile(code, fetcher_path, 'exec'), {"__name__": "__main__"})
        return proxy_count()
    except Exception as e:
        print(f"[harvest] error: {e}")
        return 0

# ── 验证线程池 ──
def validate_one(proxy_str, results):
    """单代理验证"""
    meta = REDIS.hgetall(f"{PFX_PROXY}{proxy_str}")
    if not meta:
        results["skipped"] += 1
        return

    ip = meta.get("ip", proxy_str.split(":")[0])
    port = int(meta.get("port", proxy_str.split(":")[1]))

    lat = http_test(ip, port)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if lat is not None:
        grade = grade_for_latency(lat)
        REDIS.hset(f"{PFX_PROXY}{proxy_str}", mapping={
            "latency": str(lat), "last_check": now, "success_rate": "100"
        })
        credit_add(proxy_str, 5)
        results["ok"] += 1
        results["grades"][grade] = results["grades"].get(grade, 0) + 1
    else:
        REDIS.hset(f"{PFX_PROXY}{proxy_str}", mapping={
            "last_check": now, "success_rate": "0"
        })
        removed = credit_add(proxy_str, -15)
        if removed:
            results["removed"] += 1
        else:
            results["fail"] += 1

def validate_all():
    """多线程验证全部代理，跳过最近已验证的"""
    proxies = REDIS.zrange(KEY_POOL, 0, -1)
    total = len(proxies)
    if total == 0:
        print("[validate] pool empty")
        return

    now = time.time()
    to_check = []
    skipped = 0
    for p in proxies:
        meta = REDIS.hgetall(f"{PFX_PROXY}{p}")
        if not meta:
            to_check.append(p)
            continue
        lc = meta.get("last_check", "")
        lat = meta.get("latency", "")
        # 有延迟且5分钟内验过的跳过
        if lat and lat not in ("9999", "", "0") and lc:
            try:
                ts = datetime.datetime.strptime(lc, "%Y-%m-%d %H:%M:%S").timestamp()
                grade = grade_for_latency(float(lat))
                skip_sec = {"S": 300, "A": 120, "B": 120, "C": 60}.get(grade, 60)
                if now - ts < skip_sec:
                    skipped += 1
                    continue
            except:
                pass
        to_check.append(p)

    if not to_check:
        print(f"[validate] all {total} up to date (skipped {skipped})")
        return

    results = {"ok": 0, "fail": 0, "removed": 0, "skipped_total": skipped,
               "grades": {"S": 0, "A": 0, "B": 0, "C": 0}}

    queue = to_check[:]
    lock = threading.Lock()

    def worker():
        while True:
            with lock:
                if not queue: break
                p = queue.pop()
            validate_one(p, results)

    threads = []
    num = min(VALIDATE_THREADS, len(to_check))
    for _ in range(num):
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    print(f"[validate] checked={len(to_check)} skipped={skipped} "
          f"ok={results['ok']} fail={results['fail']} "
          f"removed={results['removed']} "
          f"S={results['grades']['S']} A={results['grades']['A']} "
          f"B={results['grades']['B']} C={results['grades']['C']}")

# ── 主循环 ──
def main():
    print(f"[engine] start — pool={proxy_count()}")
    last_harvest = 0

    while True:
        try:
            now = time.time()

            # 采集新代理
            if now - last_harvest > HARVEST_INTERVAL:
                print("[engine] harvesting...")
                before = proxy_count()
                harvest_new_proxies()
                after = proxy_count()
                print(f"[engine] harvest done: {before} → {after} (+{after-before})")
                last_harvest = now

            # 验证全部
            validate_all()

            print(f"[engine] sleep {CHECK_INTERVAL}s")
            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            print("[engine] stopped")
            break
        except Exception as e:
            print(f"[engine] error: {e}")
            time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
