# Proxy Pool Dashboard v2

基于 [jhao104/proxy_pool](https://github.com/jhao104/proxy_pool) 的自定义增强面板。

## 特性

- **信用评分系统** — 代理按成功率自动升降级 (S/A/B/C)
- **粘性会话** — 同一目标站点保持相同代理
- **站点隔离** — 按目标站点分组代理质量
- **源质关闸** — 低质量代理源自动屏蔽
- **HTTP/SOCKS5 转发** — 内置代理服务器 (8080/1080)
- **Web 仪表盘** — 实时代理池状态可视化

## 架构

```
jhao104/proxy_pool (Harvester/Validator) → Redis → dashboard-v2 (评分/路由/面板/转发)
```

## 运行

```bash
docker-compose up -d
```

Dashboard: http://localhost:5050
HTTP Proxy: localhost:8080
SOCKS5 Proxy: localhost:1080
