---
title: "通过 GitHub Actions 部署到单机 VPS"
weight: 7
description: "将 Jellyfish 全栈部署到 CentOS Stream VPS，并通过单域名提供前端与 API。"
---

本指南对应仓库中的 `.github/workflows/deploy-vps.yml` 和 `deploy/vps/`。它将前端、FastAPI、Celery Worker、MySQL、Redis、RustFS 与 Caddy 部署到同一台 VPS。

> 该方案适合低频使用或演示环境。`1 vCPU / 2GB RAM / 20GB SSD` 容量紧张：工作流会创建 2GB swap、限制容器内存并将 Celery 并发固定为 1，但不适合高并发或长期保存大量视频。

## 域名与网络

使用单域名同源部署：

```text
https://drama.xclawapi.com        前端
https://drama.xclawapi.com/api/* FastAPI
```

Caddy 在容器中监听宿主机的 80/443，并自动申请 TLS 证书。MySQL、Redis、RustFS、后端端口不会映射到公网。

内部 RustFS 使用 S3 path-style 寻址，即请求形如 `http://rustfs:9000/jellyfish-assets/...`；不能使用会将 bucket 拼到主机名的 virtual-host 寻址。

在 Cloudflare 中配置：

1. 添加 `A` 记录，将 `drama.xclawapi.com` 指向 VPS 公网 IPv4。
2. 首次部署使用 **DNS only**（灰云），确保 80/443 可从公网访问。
3. 证书签发成功后，如需 Cloudflare 代理，将 SSL/TLS 模式设为 **Full (strict)**。

## 必需 GitHub Secrets

在仓库 **Settings → Secrets and variables → Actions** 中创建：

| Secret | 示例 | 说明 |
| --- | --- | --- |
| `VPS_HOST` | `203.0.113.10` | VPS 公网 IP 或主机名。 |
| `VPS_USER` | `root` | 首次部署可用 root；长期建议切换为最小权限 deploy 用户。 |
| `VPS_SSH_PRIVATE_KEY` | `-----BEGIN ...` | 能登录 VPS 的完整 PEM/OpenSSH 私钥。 |
| `VPS_SSH_PORT` | `22` | SSH 端口。 |

不要把私钥或运行时 `.env` 提交到仓库。

## 工作流做什么

每次向 `main` 推送后，只要改动涉及前端、后端、Docker 文件、VPS 配置或该工作流，Actions 会：

1. 在 GitHub Runner 构建后端和前端镜像，避免 2GB VPS 上构建时内存不足。
2. 经 SSH 复制 `deploy/vps` 配置与数据库初始化 SQL。
3. 初始化 CentOS Docker、Compose、firewalld 和 2GB swap。
4. 将镜像流式传输到 VPS 并加载。
5. 在 `/opt/jellyfish/.env` 首次生成 MySQL、RustFS 等本地随机密码。
6. 启动服务并等待后端 `/health` 健康检查通过。

生产运行时 `.env` 仅保留在 VPS 的 `/opt/jellyfish/.env`，后续部署不会覆盖其中的密码或 API Key。

## 管理员密码

VPS 部署默认启用应用级管理员密码门禁。首次部署会在 `/opt/jellyfish/.env` 生成 `AUTH_ADMIN_PASSWORD` 与 `AUTH_SESSION_SECRET`；通过受控 SSH 登录后可查看初始密码：

```bash
grep '^AUTH_ADMIN_PASSWORD=' /opt/jellyfish/.env
```

不要将该值粘贴到聊天、代码库或 GitHub Actions 日志。若需替换密码，编辑该文件中的 `AUTH_ADMIN_PASSWORD`，然后重启后端和 Worker：

```bash
cd /opt/jellyfish
docker compose --env-file .env -f vps/compose.yml up -d backend celery-worker
```

## 首次部署后的检查

```bash
# 在 VPS 上查看容器状态
cd /opt/jellyfish
docker compose --env-file .env -f vps/compose.yml ps

# 检查后端
docker compose --env-file .env -f vps/compose.yml exec backend \
  curl -fsS http://127.0.0.1:8000/health

# 查看失败日志
docker compose --env-file .env -f vps/compose.yml logs --tail=100 backend celery-worker caddy
```

浏览器验证：

- `https://drama.xclawapi.com` 能打开项目页面；
- `https://drama.xclawapi.com/health` 返回成功响应；
- 浏览器 API 请求使用同源 `/api/v1/...`，不出现 CORS 错误。

## 配置真实 AI 生成

部署本身不需要模型 Key；未配置时仍可运行管理界面和基础数据流程，但需要 AI 能力的接口会失败。

在 VPS 上编辑：

```bash
vi /opt/jellyfish/.env
```

填入所需的 `OPENAI_API_KEY` 或其他已支持供应商的运行参数，然后重启对应服务：

```bash
cd /opt/jellyfish
docker compose --env-file .env -f vps/compose.yml up -d backend celery-worker
```

## 回滚

Actions 每次使用 Git commit SHA 作为镜像标签。若新版本异常，可在 VPS 上将 `.env` 中的 `JELLYFISH_IMAGE_TAG` 改回上一个成功提交 SHA，再执行：

```bash
cd /opt/jellyfish
docker compose --env-file .env -f vps/compose.yml up -d
```

数据库和 RustFS 卷不会因镜像回滚而自动回滚；涉及 schema 或数据变更时，必须先备份 `mysql_data` 和 `rustfs_data`。
