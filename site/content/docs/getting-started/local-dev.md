---
title: "本地开发"
weight: 2
description: "启动前后端并完成本地联调。"
---

## 启动后端

```bash
cd backend
cp .env.example .env
uv sync --group dev
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

默认 SQLite 数据库会在后端启动时自动创建表；使用 MySQL 或 PostgreSQL 时，仍应通过部署或迁移流程初始化数据库。若需手动创建 SQLite 表，可执行 `uv run python init_db.py`。

## 启动前端

```bash
cd front
pnpm install
pnpm dev
```

## 默认端口

- 前端：`http://localhost:7788`
- 后端：`http://localhost:8000`
- Swagger：`http://localhost:8000/docs`

## OpenAPI 更新

```bash
cd front
pnpm run openapi:update
```

## 官网与文档站本地预览

```bash
cd site
hugo mod tidy
hugo server --buildDrafts --disableFastRender
```

## 推荐的联调顺序

1. 启动后端，确认 `/docs` 和 `/health` 正常。
2. 启动前端，确认页面能访问并能请求后端。默认 CORS 同时支持 `localhost`、`127.0.0.1` 和 IPv6 本地地址 `[::1]`。
3. 执行验证：后端运行 `uv run pytest -q`，前端运行 `pnpm exec tsc --noEmit`。
4. 如果修改了接口定义，再执行 `openapi:update`。
5. 如果同时在维护官网，再单独启动 `site/` 预览。
