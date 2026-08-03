---
title: "管理员密码与会话门禁"
weight: 3
description: "Jellyfish 当前应用级管理员密码登录、Session Cookie 与 API 保护边界。"
---

Jellyfish 使用单管理员密码门禁保护生产部署。它不是多用户或 RBAC 系统；当前只解决“公网访问者不能直接操作应用”的基础访问控制。

## 会话模型

```text
浏览器提交管理员密码
  → POST /api/v1/auth/login
  → 后端校验 AUTH_ADMIN_PASSWORD
  → 写入 HttpOnly、签名 Session Cookie
  → 前端通过 /api/v1/auth/session 恢复会话
  → 认证中间件允许访问业务 API
```

- 密码不写入浏览器存储。
- 前端不保存 JWT 或可复用 Token。
- Cookie 由 Starlette `SessionMiddleware` 签名，生产环境设置 `Secure` 与 `SameSite=Lax`。
- `AUTH_ENABLED=false` 时跳过认证，供本地开发和历史测试使用。

## API 边界

公开端点：

- `/health`
- `/api/v1/health`
- `/docs`、`/redoc`、`/openapi.json`
- `/api/v1/auth/login`、`/api/v1/auth/logout`、`/api/v1/auth/session`

其他 API 在 `AUTH_ENABLED=true` 时必须携带已认证 Session；未登录时返回统一的 `401 Authentication required` 响应壳。

## 生产配置

生产环境必须设置：

```text
AUTH_ENABLED=true
AUTH_ADMIN_PASSWORD=<强随机密码>
AUTH_SESSION_SECRET=<独立强随机会话密钥>
AUTH_COOKIE_SECURE=true
```

VPS 单机部署在首次运行时将生成 `AUTH_ADMIN_PASSWORD` 和 `AUTH_SESSION_SECRET`，仅保存在权限为 `600` 的 `/opt/jellyfish/.env`。管理员应通过受控 SSH 登录读取初始密码，并及时替换为自主管理的强密码。

## 当前边界

- 只有一个管理员身份，用户名展示为 `Admin`。
- 无注册、找回密码、成员管理、审计日志或项目级权限。
- `AUTH_ADMIN_PASSWORD` 以受限主机环境变量保存；后续引入真实用户体系时，应迁移为经过强哈希的用户凭据并加入登录限速、审计和恢复流程。
