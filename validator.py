#!/usr/bin/env python3
"""后台验证+采集引擎 — Redis DB1 代理池维护"""
import redis, time, socket, threading, sys, os, datetime, json, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

KEY_POOL = "proxies:pool"
PFX_PROXY = "proxy:"
CHECK_INTERVAL = 30          # 每轮验证间隔
HARVEST_INTERVAL = 300       # 采集间隔 (5分钟)
VALIDATE_THREADS = 50        # 验证线程数
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
            return parts[0], parts[-1]
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
    # C2: context manager 防 socket 泄漏
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
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
            capture_output=True, text=True, timeout=120,
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
    """单代理验证 — meta 由上层传入避免二次 HGETALL"""
    if not meta:
        return ("skipped", None)

    ip = meta.get("ip", "")
    port_str = meta.get("port", "")
    if not ip:
        ip = proxy_str.split(":")[0]
    if not port_str:
        # C5: 防御性解析
        parts = proxy_str.split(":")
        port_str = parts[1] if len(parts) > 1 else "0"

    try:
        port = int(port_str)
    except (ValueError, TypeError):
        return ("skipped", None)

    lat = http_test(ip, port)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if lat is not None:
        grade = grade_for_latency(lat)
        REDIS.hset(f"{PFX_PROXY}{proxy_str}", mapping={
            "latency": str(lat), "last_check": now, "success_rate": "100"
        })
        credit_add(proxy_str, 5)
        return ("ok", grade)
    else:
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
               "grades": {"S": 0, "A": 0, "B": 0, "C": 0}}
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
                except:
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
        except:
            results["fail"] += 1

    print(f"[validate] checked={checked} skipped={results['skipped_total']} "
          f"ok={results['ok']} fail={results['fail']} "
          f"removed={results['removed']} "
          f"S={results['grades']['S']} A={results['grades']['A']} "
          f"B={results['grades']['B']} C={results['grades']['C']}")

# ── 主循环 ──
def main():
    print(f"[engine] start — pool={proxy_count()}")
    last_harvest = 0

    executor = ThreadPoolExecutor(max_workers=VALIDATE_THREADS, thread_name_prefix="val")

    try:
        while True:
            try:
                now = time.time()

                if now - last_harvest > HARVEST_INTERVAL:
                    print("[engine] harvesting...")
                    added = harvest_new_proxies()
                    print(f"[engine] harvest done: +{added}")
                    last_harvest = now

                t0 = time.time()
                validate_all(executor)
                elapsed = time.time() - t0

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
