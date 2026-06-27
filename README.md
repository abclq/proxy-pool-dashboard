# Proxy Pool Dashboard v2

[![Docker](https://img.shields.io/badge/Docker-✓-2496ED)](https://docker.com)
[![Python](https://img.shields.io/badge/Python-3.13-blue)](https://python.org)
[![Redis](https://img.shields.io/badge/Redis-7-red)](https://redis.io)

基于 [jhao104/proxy_pool](https://github.com/jhao104/proxy_pool) 的自定义 **评分/路由/面板一体化** 增强版。在原有采集+校验基础上，追加信用评分、粘性会话、站点隔离、源质关闸等生产级功能。

## ✨ 相比原版的新增

| 功能 | 原版 | Dashboard v2 |
|------|:--:|:--:|
| 采集源 | 15 | **69**（15 + 35 GitHub + 19 直爬） |
| 代理评分 | ❌ | ✅ S/A/B/C 四级信用评分 |
| 粘性会话 | ❌ | ✅ 同目标站点复用同一代理 |
| 站点隔离 | ❌ | ✅ 按目标站分组代理质量 |
| 源质关闸 | ❌ | ✅ 低质源自动屏蔽 |
| 面板筛选 | 简陋 | ✅ 实时过滤/排序/搜索+GeoIP |
| HTTP/SOCKS5转发 | ❌ | ✅ 内置 :8080 / :1080 代理 |

## 🏗 架构

```
jhao104/proxy_pool (15源) ──┐
new_fetcher.py (19源)  ─────┼──→ Redis ──→ backend:5051 ──→ frontend:5050
import_github_proxies (35源)┘                    │
                                          ┌──────┴──────┐
                                          │  validator   │ 后台校验
                                          └─────────────┘
```

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
| `grade` | `s`/`a`/`b`/`c` |
| `country` | `CN`中国 / `!CN`海外 |
| `protocol` | `http`/`https`/`socks4`/`socks5` |
| `delay` | 延迟上限ms，如 `delay=1000` |
| `search` | IP模糊搜索 |
| `sort` / `order` | `delay`/`grade` + `asc`/`desc` |
| `page` / `limit` | 分页，默认50，最大200 |

```json
{
  "total": 8234, "filtered": 156,
  "proxies": [{
    "ip": "123.45.67.89", "port": "8080",
    "protocol": "http", "delay": 234, "grade": "s",
    "region": "CN", "location": "浙江 杭州",
    "source": "kuaidaili"
  }]
}
```

### GET `/api/stats`

```json
{"total":8234, "grades":{"s":1200,"a":2800,"b":3200,"c":1034}, "china":3400}
```

## 🎯 评分机制

| 事件 | 分数 |
|------|:--:|
| 新增 | 20 |
| 校验通过 | +5 |
| 403 | -20 |
| 502 | -30 |
| 超时 | -30 |
| 上限 | 100 |
| 归零 | 移入黑名单 |

## 📊 采集源

### new_fetcher.py (19源)

| 分类 | 源 |
|------|------|
| 直爬API | ProxyScrape, docip, 89ip, OpenProxyList |
| GitHub | MuRongPIG, VMHeaven, jetkai, proxifly-gh, clarketm, Thordata, hookzof |
| HTML表格 | Free-Proxy-List, SSLProxies, US-Proxy, Socks-Proxy |
| 国内 | 快代理, ip3366, 积流, 齐云 |

### import_github_proxies.py (35源)

从 TheSpeedX、ShiftyTR、sunny9577、roosterkid、monosans 等 35 个 GitHub 仓库批量导入。

> 完整列表见 [proxy-pool-tools](https://github.com/abclq/proxy-pool-tools)

## 📁 文件

```
├── dashboard.py        # 启动器
├── frontend.py         # :5050 Web面板
├── backend.py          # :5051 API
├── geo.py              # GeoIP
├── validator.py        # 校验
├── new_fetcher.py      # 19源采集
├── ip2region.xdb       # IP地域库
├── static/             # 前端
└── Dockerfile
```

## 🙏 参考

- [jhao104/proxy_pool](https://github.com/jhao104/proxy_pool) — 原版（23.4k⭐）
- [ip2region](https://github.com/lionsoul2014/ip2region)
- [abclq/proxy-pool-tools](https://github.com/abclq/proxy-pool-tools) — 增强工具
