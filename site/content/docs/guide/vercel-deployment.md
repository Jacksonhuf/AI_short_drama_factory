---
title: "通过 GitHub Actions 部署前端到 Vercel"
weight: 6
description: "配置 元风 前端在推送 main 后自动部署到 Vercel。"
---

元风 的 Vercel 工作流只部署 `front/` 中的 Vite 单页应用。FastAPI、Celery Worker、MySQL、Redis 和对象存储需要部署在支持常驻服务的独立环境中，不能由此工作流替代。

## 工作流行为

`.github/workflows/deploy-vercel-frontend.yml` 会在以下条件执行：

- 推送到 `main`，且变更包含 `front/**` 或工作流自身；
- 在 GitHub Actions 页面手动运行。

工作流按以下顺序执行：

1. 使用 Node.js 22 与 pnpm 9.15.9 安装前端依赖；
2. 读取 Vercel production 项目配置；
3. 使用 `VITE_BACKEND_URL` 构建 Vite 前端；
4. 将预构建产物部署到 Vercel production。

`front/vercel.json` 同时为 SPA 路由提供回退到 `index.html` 的 rewrite，确保直接访问项目、章节或工作室 URL 不会得到 404。

## 前置条件

1. 在 Vercel 创建一个项目，Root Directory 设置为 `front`。
2. 准备一个可从浏览器访问的后端 HTTPS 地址，例如 `https://api.example.com`。
3. 在后端的 `CORS_ORIGINS` 中加入 Vercel 的生产域名，例如 `https://app.example.com`。
4. 确保后端与 Worker、MySQL、Redis、对象存储已单独部署且互相可连通。

不要将 `http://localhost:8000` 配置为生产后端地址；浏览器中的 localhost 指向访问者自己的电脑，而不是云端 API。

## GitHub Actions Secrets

在仓库 **Settings → Secrets and variables → Actions** 中添加以下 repository secrets：

| Secret | 来源 | 用途 |
| --- | --- | --- |
| `VERCEL_TOKEN` | Vercel Account Settings → Tokens | 允许 GitHub Actions 调用 Vercel CLI。 |
| `VERCEL_ORG_ID` | 在本地执行 `vercel link` 后生成的 `.vercel/project.json` | 标识 Vercel 团队或个人空间。 |
| `VERCEL_FRONTEND_PROJECT_ID` | 同一 `.vercel/project.json` 的 `projectId` | 标识 元风 前端 Vercel 项目。 |
| `VITE_BACKEND_URL` | 已部署后端的 HTTPS 基址 | 在前端构建时注入 API 地址。 |

`.vercel/project.json` 包含项目标识，不应提交到仓库。可在已登录 Vercel CLI 的本机、`front/` 目录执行：

```bash
pnpm exec vercel link
cat .vercel/project.json
```

将 JSON 中的 `orgId` 和 `projectId` 分别复制到 GitHub Secrets。`VITE_BACKEND_URL` 不要以 `/api` 结尾，例如：

```text
https://api.example.com
```

## 首次部署

1. 完成前置条件和 Secrets 配置。
2. 推送包含 `front/` 变更的提交到 `main`，或在 Actions 页面手动运行 **Deploy Frontend to Vercel**。
3. 在 Vercel Dashboard 检查 production deployment URL。
4. 打开前端并验证：
   - `/projects` 可加载；
   - 直接访问深层路由不会 404；
   - 浏览器 Network 面板中的 API 请求指向 `VITE_BACKEND_URL`；
   - 后端响应包含允许该 Vercel 域名的 CORS 头。

## 失败排查

| 现象 | 检查项 |
| --- | --- |
| 工作流在配置校验步骤失败 | GitHub Secrets 是否全部存在且名称完全一致。 |
| Vercel 提示找不到项目 | `VERCEL_ORG_ID`、`VERCEL_FRONTEND_PROJECT_ID` 是否来自同一 Vercel 项目。 |
| 部署后 API 指向 localhost | `VITE_BACKEND_URL` 是否设置；确认重新触发了构建。 |
| 浏览器提示 CORS | 后端 `CORS_ORIGINS` 是否包含 Vercel 的实际 production 域名。 |
| 深层链接 404 | 确认 Vercel 项目使用仓库中的 `front/vercel.json` 且 Root Directory 为 `front`。 |

## 回滚

在 Vercel Dashboard 将上一个成功部署 Promote 为 Production，或将 `main` 回退到上一个稳定提交后重新触发工作流。前端回滚不会回滚 API、数据库、Worker 或对象存储的变更，应分别执行后端部署方案中的回滚步骤。
