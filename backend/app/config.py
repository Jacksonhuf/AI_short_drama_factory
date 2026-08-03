"""应用配置，从环境变量加载。"""

import json
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = BACKEND_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_name: str = "Jellyfish API"
    debug: bool = False

    # API
    api_v1_prefix: str = "/api/v1"

    # Database
    database_url: str = "sqlite+aiosqlite:///./jellyfish.db"

    # Redis / Celery Broker
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str | None = None
    celery_broker_url: str | None = None

    # CORS：环境变量中建议使用逗号分隔（更贴近 docker-compose 用法）
    # 也兼容 JSON 数组：'["http://a","http://b"]'
    cors_origins: str = "http://localhost:7788,http://127.0.0.1:7788,http://[::1]:7788"

    # 应用级管理员门禁：本地开发默认关闭，生产部署必须显式提供密码和会话密钥。
    auth_enabled: bool = False
    auth_admin_password: str | None = None
    auth_session_secret: str = "unsafe-local-development-session-secret"
    auth_session_max_age_seconds: int = 60 * 60 * 12
    auth_cookie_secure: bool = False

    @property
    def cors_origins_list(self) -> list[str]:
        s = (self.cors_origins or "").strip()
        if not s:
            return []
        if s.startswith("["):
            loaded = json.loads(s)
            if isinstance(loaded, list):
                return [str(x).strip() for x in loaded if str(x).strip()]
            return []
        return [x.strip() for x in s.split(",") if x.strip()]

    # S3 / 对象存储（用于素材文件）
    s3_endpoint_url: str | None = None
    s3_region_name: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_bucket_name: str | None = None
    # 内部 S3 兼容服务通常不支持 bucket.endpoint 形式，需使用 path 寻址。
    s3_addressing_style: Literal["virtual", "path", "auto"] = "virtual"
    # 可选：统一前缀，方便按环境/项目隔离，如 "jellyfish/dev"
    s3_base_path: str = ""
    # 可选：对外访问基址（CDN 或自定义域名），为空则使用 S3 自带 URL 或预签名 URL
    s3_public_base_url: str | None = None

    def model_post_init(self, __context: object) -> None:
        if not self.celery_broker_url or not str(self.celery_broker_url).strip():
            password_part = f":{self.redis_password}@" if self.redis_password else ""
            self.celery_broker_url = f"redis://{password_part}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    def validate_auth_configuration(self) -> None:
        """确保启用认证的部署不会以缺失或开发期会话密钥启动。"""
        if not self.auth_enabled:
            return
        if not self.auth_admin_password:
            raise RuntimeError("AUTH_ADMIN_PASSWORD must be configured when AUTH_ENABLED=true")
        if self.auth_session_secret == "unsafe-local-development-session-secret":
            raise RuntimeError("AUTH_SESSION_SECRET must be configured when AUTH_ENABLED=true")


settings = Settings()
