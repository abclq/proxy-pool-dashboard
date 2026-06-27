#!/usr/bin/env python3
"""backend — pure API, port 5051. GeoIP via ip2region."""
import json, os, time, urllib.parse, hashlib, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
import redis, sys

sys.path.insert(0, "/app")
import geo

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

HOST = os.environ.get("REDIS_HOST", "proxy-redis")
r0 = redis.Redis(host=HOST, port=6379, db=0, decode_responses=True)
r1 = redis.Redis(host=HOST, port=6379, db=1, decode_responses=True)
PER_PAGE = 50

_cache = {"t": 0, "d": []}
_cache_lock = threading.Lock()

def load():
    now = time.time()
    with _cache_lock:
        if now - _cache["t"] < 30:
            return _cache["d"]
    # DB0: source/anon/https from jhao104 use_proxy hash
    jhao = {}
    for f, v in r0.hscan_iter("use_proxy"):
        try:
            jhao[f] = json.loads(v)
        except json.JSONDecodeError:
            pass
    # DB1: delay/latency/last_check from engine ZSET + hashes
    members = r1.zrange("proxies:pool", 0, -1)
    result = []
    pipe = r1.pipeline(transaction=False)
    for i, m in enumerate(members):
        pipe.hgetall(f"proxy:{m}")
        if (i + 1) % 1000 == 0 or i == len(members) - 1:
            for hd in pipe.execute():
                if not hd or not hd.get("ip"):
                    continue
                ip, port = hd["ip"], hd["port"]
                key = f"{ip}:{port}"
                proto = hd.get("protocol", "?").lower()
                delay = float(hd.get("delay", "0") or hd.get("latency", "0") or 0)
                jd = jhao.get(key, {})
                if jd.get("https"):
                    proto = "https"
                # GeoIP — city detail for China, country for foreign
                detail = geo.resolve(ip)
                region_code = geo.resolve_region(ip)
                # grade
                if delay < 500: g = "s"
                elif delay < 1000: g = "a"
                elif delay < 3000: g = "b"
                else: g = "c"
                result.append({
                    "ip": ip, "port": port, "protocol": proto,
                    "delay": delay, "grade": g,
                    "region": region_code,
                    "location": detail,  # 浙江 杭州 / 美国 / ?
                    "source": jd.get("source", "?"),
                    "anon": jd.get("anonymous", "?"),
                    "last_check": hd.get("last_check", "?"),
                    "is_china": region_code == "CN",
                })
            pipe = r1.pipeline(transaction=False)
    with _cache_lock:
        _cache["d"] = result
        _cache["t"] = now
    return result

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _json(self, d, code=200):
        b = json.dumps(d).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(b))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(b)
    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        path = p.path.rstrip("/") or "/"
        q = urllib.parse.parse_qs(p.query)
        if path == "/api/proxies":
            proxies = load()
            grade_f = q.get("grade", [""])[0].lower()
            country_f = q.get("country", [""])[0]
            proto_f = q.get("protocol", [""])[0].lower()
            search = q.get("search", [""])[0].lower()
            delay_f = q.get("delay", [""])[0]
            location_f = q.get("location", [""])[0]
            filtered = []
            for px in proxies:
                if grade_f and px["grade"] != grade_f: continue
                if proto_f and px["protocol"] != proto_f: continue
                if country_f:
                    if country_f == "!CN":
                        if px["is_china"]: continue
                    elif px["region"] != country_f:
                        continue
                if delay_f:
                    try:
                        if px["delay"] > float(delay_f): continue
                    except Exception: pass
                if search and search not in px["ip"]: continue
                if location_f and location_f.lower() not in px["location"].lower(): continue
                filtered.append(px)
            # Sorting
            sort_by = q.get("sort", ["delay"])[0]
            sort_asc = q.get("order", ["asc"])[0] != "desc"
            try:
                if sort_by == "delay":
                    filtered.sort(key=lambda x: x.get("delay") if x.get("delay", -1) >= 0 else 99999, reverse=not sort_asc)
                elif sort_by == "grade":
                    og = {"s": 0, "a": 1, "b": 2, "c": 3}
                    filtered.sort(key=lambda x: og.get(x.get("grade","c"), 3), reverse=not sort_asc)
            except Exception: pass
            try:
                page = max(1, int(q.get("page", ["1"])[0]))
            except Exception:
                page = 1
            try:
                limit = min(int(q.get("limit", [str(PER_PAGE)])[0]), 200)
            except Exception:
                limit = PER_PAGE
            start = (page - 1) * limit
            body = {
                "total": len(proxies),
                "filtered": len(filtered),
                "page": page,
                "pages": max(1, -(-len(filtered) // limit)),
                "limit": limit,
                "proxies": filtered[start:start + limit],
            }
            b = json.dumps(body).encode()
            etag = hashlib.md5(b).hexdigest()
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", len(b))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "max-age=10")
            self.end_headers()
            self.wfile.write(b)
            return
        elif path == "/api/stats":
            proxies = load()
            grades = {"s": 0, "a": 0, "b": 0, "c": 0}
            protos = {}; regions = {}; china = 0
            for px in proxies:
                grades[px["grade"]] = grades.get(px["grade"], 0) + 1
                protos[px["protocol"]] = protos.get(px["protocol"], 0) + 1
                r = px["region"]
                regions[r] = regions.get(r, 0) + 1
                if px["is_china"]: china += 1
            self._json({
                "total": len(proxies), "grades": grades, "protocols": protos,
                "china": china,
                "regions": dict(sorted(regions.items(), key=lambda x: -x[1])[:40]),
            })
        else:
            self._json({"error": "not found"}, 404)

if __name__ == "__main__":
    print("API on :5051")
    # Pre-warm cache
    t0 = time.time()
    load()
    print(f"Cache ready in {time.time()-t0:.1f}s")
    # Start background geo filler (online API)
    geo._init()
    ThreadingHTTPServer(("0.0.0.0", 5051), H).serve_forever()
