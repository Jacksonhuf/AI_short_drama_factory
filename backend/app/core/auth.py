"""应用级管理员会话门禁。"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import settings
from app.schemas.common import ApiResponse


class AdminSessionMiddleware(BaseHTTPMiddleware):
    """保护业务 API，仅允许携带已签名管理员会话的请求进入业务路由。"""

    _public_paths = {
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/api/v1/health",
    }

    async def dispatch(self, request: Request, call_next) -> Response:
        """跳过公开端点与认证端点，其余请求校验 SessionMiddleware 提供的会话。"""
        path = request.url.path
        if (
            not settings.auth_enabled
            or request.method == "OPTIONS"
            or path in self._public_paths
            or path.startswith("/api/v1/auth/")
        ):
            return await call_next(request)
        if request.session.get("authenticated") is True:
            return await call_next(request)

        body = ApiResponse[None](
            code=401,
            message="Authentication required",
            data=None,
            meta=None,
        ).model_dump()
        return JSONResponse(status_code=401, content=body)
