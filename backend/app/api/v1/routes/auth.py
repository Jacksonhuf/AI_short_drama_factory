"""管理员密码登录与会话接口。"""

from __future__ import annotations

from hmac import compare_digest

from fastapi import APIRouter, HTTPException, Request

from app.config import settings
from app.schemas.auth import AdminLoginRequest, AdminSessionRead
from app.schemas.common import success_response

router = APIRouter(prefix="/auth")


def _session_read(request: Request) -> AdminSessionRead:
    """将当前请求的签名会话转换为前端可消费的认证状态。"""
    authenticated = not settings.auth_enabled or request.session.get("authenticated") is True
    return AdminSessionRead(
        authenticated=authenticated,
        username="Admin" if authenticated else None,
    )


@router.post("/login")
async def login(payload: AdminLoginRequest, request: Request):
    """校验部署配置的管理员密码，并在成功后建立 HttpOnly 签名会话。"""
    configured_password = settings.auth_admin_password
    if settings.auth_enabled and (
        not configured_password or not compare_digest(payload.password, configured_password)
    ):
        raise HTTPException(status_code=401, detail="Invalid administrator password")

    request.session.clear()
    request.session["authenticated"] = True
    return success_response(_session_read(request))


@router.post("/logout")
async def logout(request: Request):
    """清除当前浏览器的管理员会话。"""
    request.session.clear()
    return success_response(AdminSessionRead(authenticated=False, username=None))


@router.get("/session")
async def session(request: Request):
    """返回当前浏览器会话状态，供前端路由守卫恢复登录状态。"""
    return success_response(_session_read(request))
