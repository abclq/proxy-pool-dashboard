# Proxy Pool Dashboard v2

[![Docker](https://img.shields.io/badge/Docker-✓-2496ED)](https://docker.com)
[![Python](https://img.shields.io/badge/Python-3.13-blue)](https://python.org)
[![Redis](https://img.shields.io/badge/Redis-7-red)](https://redis.io)

基于代理采集引擎的自定义 **评分/路由/面板一体化** 管理工具。支持采集过滤、五级评分、离线 IP 地理定位、实时前端筛选，158k+ 代理池规模。

## ✨ 核心功能

| 功能 | 说明 |
|------|------|
| 采集源 | **35** 源（20 GitHub + 15 爬虫/API），采集阶段过滤缺端口/协议的死数据 |
| 五级评分 | **S/A/B/C/D**，基于延迟：S<500ms / A<1s / B<3s / C<5s / D≥5s 或失败 |
| 离线 GeoIP | **DB-IP Country Lite** 二进制库（355K 条，3.5MB），3.6μs/查，零 API 限流 |
| 粘性会话 | 同目标站点复用同一代理，支持 X-Proxy-Session |
| 站点隔离 | 按目标站分组代理质量评分 |
| 源质关闸 | 低质源自动屏蔽（S+A+B 率 <30% 即隔离） |
| 前端筛选 | 实时按国家/城市/协议/延迟/评级筛选，窗口分页，SPA 响应式 |
| 代理网关 | 内置 HTTP :8080 / SOCKS5 :1080 转发 |

## 🏗 架构

```
采集引擎（35源）──→ Redis ──→ backend:5051 ──→ frontend:5050
                     │              │
                proxy:ip:port   geo.py (离线IP库 + 在线API fallback)
                     │              │
                validator     data/ipdb.bin (355K条)
```

| 组件 | 端口 | 说明 |
|------|------|------|
| `proxy-redis` | 6379 | ZSET 信用分 + Hash 元数据 |
| `backend.py` | 5051 | 纯 API + 多线程服务 |
| `frontend.py` | 5050 | 静态文件 + /api 反代 |
| `validator.py` | — | 后台验证引擎，30s 一轮 |
| `geo.py` | — | GeoIP：离线 DB-IP → Redis 缓存 → ip-api/ip9/freeipapi/ipwhois |

## 📦 快速开始

```bash
git clone https://github.com/abclq/proxy-pool-dashboard.git
cd proxy-pool-dashboard
docker-compose up -d
```

| 服务 | 地址 |
|------|------|
| 🖥 Dashboard | http://localhost:5050 |
| 📡 API | http://localhost:5051/api/proxies |
| 🔌 HTTP 代理 | `http://localhost:8080` |
| 🧦 SOCKS5 代理 | `socks5://localhost:1080` |

## 🔌 API

### GET `/api/proxies`

| 参数 | 说明 |
|------|------|
| `grade` | `s`/`a`/`b`/`c`/`d` |
| `country` | `CN`中国 / `!CN`海外 |
| `region` | 城市/地区关键词匹配 |
| `location` | 同 region，别名 |
| `protocol` | `http`/`https`/`socks4`/`socks5` |
| `delay` | 延迟上限ms，如 `delay=1000` |
| `search` | IP模糊搜索 |
| `sort` / `order` | `delay`/`grade` + `asc`/`desc` |
| `page` / `limit` | 分页，默认50，最大200 |

```json
{
  "total": 35200, "filtered": 156,
  "proxies": [{
    "ip": "123.45.67.89", "port": "8080",
    "protocol": "http", "delay": 234, "grade": "s",
    "region": "CN", "location": "浙江 杭州",
    "source": "github-xxx"
  }]
}
```

### GET `/api/stats`

```json
{"total":35200, "grades":{"s":1200,"a":2800,"b":3200,"c":10000,"d":18000}, "china":3400, "regions":{"CN":28000, "US":1200, …}}
```

## 🎯 评分机制

```
S 级:  <500ms   (延迟极低)
A 级:  <1s      (优秀)
B 级:  <3s      (良好)
C 级:  <5s      (一般)
D 级:  ≥5s 或验证失败 (差)
```

信用分系统：新代理 +20，验证通过 +5，请求成功 +5（上限 100），连接失败 -30（进黑名单 5min），验证失败 -15，分数 ≤0 自动清理。

## 🗺 GeoIP 管线

1. **离线 DB-IP**（内存二分查找，3.6μs）—— 355K 条 IP 段，355 个国家/地区覆盖
2. **Redis 缓存**（30 天 TTL）—— 在线 API 结果缓存
3. **ip-api.com**（免费，150/min）—— 主力在线 API
4. **ip9.com.cn** / **freeipapi.com** / **ipwhois.app**（备用 fallback）
5. **代理轮换**：在线 API 遇 403/429 时从 Redis 代理池取 HTTP 代理轮换

后台线程每月自动更新离线库。

## 📂 代码结构

```
proxy-pool-dashboard/
├── backend.py          # API 服务 + 数据加载 + 缓存
├── frontend.py         # 静态文件服务 + 反代
├── geo.py              # GeoIP 解析（离线 + 在线 fallback）
├── validator.py        # 后台验证引擎（TCP + HTTP 两阶段）
├── new_fetcher.py      # 采集引擎（35 源并发拉取）
├── dashboard.py        # PID 1 看门狗（进程管理 + 崩溃重启）
├── data/
│   └── ipdb.bin        # 离线 IP 二进制库（355K 条）
├── static/
│   ├── index.html      # SPA 入口
│   ├── app.js          # 前端逻辑（SPA 视图 + 筛选 + 分页）
│   └── style.css       # GitHub 暗色主题
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

## 🔧 技术要点

- **前后端筛选统一走后端**：region/protocol/search/speed 全通过 API 参数传给后端
- **AbortController 防竞态**：快速切换筛选时取消旧请求
- **窗口分页**：10k+ 代理时最多显示 7 个页码按钮
- **事件委托复制**：点击复制按钮不触发内联 onclick，走事件委托 + data 属性
- **XSS 防护**：所有 innerHTML 注入点经过 escapeHtml()
- **5s 缓存 TTL**：all_proxies() 返回带缓存，避免每次 150k 次 hgetall
- **多线程 HTTP 服务**：ThreadingHTTPServer，避免单请求阻塞全服务
- **启动预热**：启动时同步建缓存，首个用户不需等 30s
