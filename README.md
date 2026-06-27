# Proxy Pool Dashboard v2

高性能代理池管理面板，支持 **69 个采集源**、多线程验证、信用评分、GeoIP 定位。

## 特性

- **多源采集** — GitHub 仓库 + API + HTML 表格 + 国内代理站，支持代理轮换反爬
- **异步验证** — 200 线程并发检测，每轮跳过已验代理，S/A/B/C 信用评级
- **GeoIP 定位** — 离线 ip2region 数据库，中国 IP 细化到城市
- **实时代理转发** — 验证引擎可直接使用池内代理爬取被墙源
- **Web 仪表盘** — 暗色紧凑 UI，地区筛选/排序/分页/导出

## 架构

```
19 采集源 (new_fetcher.py) ──┐
35 GitHub 源 (import_github_proxies.py) ──┤
15 国内站 (main.py fetchers) ──┤
                                  ├── Redis ZSET ── backend.py (:5051) ── frontend.py (:5050) ── 浏览器
验证引擎 (validator.py) ─────────┘                    geo.py (ip2region)
```

## 快速开始

```bash
# 1. 下载 ip2region 数据库
mkdir -p data && cd data
wget https://github.com/lionsoul2014/ip2region/raw/master/data/ip2region.xdb

# 2. 启动
cd ..
docker-compose up -d
```

Dashboard: http://localhost:5050

## 手动运行（无 Docker）

```bash
pip install redis
python3 dashboard.py
```

需先启动 Redis（默认连接 `proxy-redis:6379`，可通过 `REDIS_HOST` 环境变量修改）。

## 采集源

| 类别 | 数量 | 示例 |
|------|------|------|
| GitHub 文本 | 39 | MuRongPIG, hproxy, zevtyardt, r00tee, VMHeaven |
| 国际 API | 8 | ProxyScrape, OpenProxyList, docip, proxifly |
| 国际 HTML | 4 | Free-Proxy-List, SSLProxies, US-Proxy, Socks-Proxy |
| 国内站 | 18 | 快代理, ip3366, 89ip, 积流, 齐云, ihuan, goubanjia |

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `REDIS_HOST` | `proxy-redis` | Redis 主机地址 |

## 致谢

- [jhao104/proxy_pool](https://github.com/jhao104/proxy_pool) — 代理池基础框架
- [lionsoul2014/ip2region](https://github.com/lionsoul2014/ip2region) — 离线 IP 定位库
