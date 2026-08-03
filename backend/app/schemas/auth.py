"""管理员会话认证接口的请求与响应模型。"""

from pydantic import BaseModel, Field


class AdminLoginRequest(BaseModel):
    """登录请求，仅接受部署环境配置的管理员密码。"""

    password: str = Field(..., min_length=1, max_length=512, description="管理员密码")


class AdminSessionRead(BaseModel):
    """前端恢复会话时使用的最小认证状态。"""

    authenticated: bool = Field(..., description="当前浏览器是否已通过管理员认证")
    username: str | None = Field(None, description="已认证时显示的管理员名称")
