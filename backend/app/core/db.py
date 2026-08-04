"""SQLAlchemy 异步引擎与会话。"""

from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

# 这些表在 MySQL 生产环境由 backend/sql 增量脚本创建，避免 ORM create_all
# 与既有表字符集/外键定义不一致导致 1215 错误。
MYSQL_MIGRATION_MANAGED_TABLES = frozenset(
    {
        "task_dispatch_outbox",
        "script_task_applications",
        "chapter_production_runs",
        "chapter_production_run_steps",
        "chapter_production_run_step_items",
        "production_run_task_bindings",
        "production_run_transitions",
    }
)


def uses_mysql_database(database_url: str) -> bool:
    """判断连接串是否指向 MySQL/MariaDB。"""
    normalized = database_url.lower()
    return normalized.startswith("mysql") or normalized.startswith("mariadb")


def _mysql_engine_connect_args(database_url: str) -> dict[str, Any]:
    """为 MySQL 连接统一 utf8mb4，减少 ORM 建表与既有库字符集不一致的风险。"""
    if uses_mysql_database(database_url):
        return {"charset": "utf8mb4"}
    return {}


def _build_engine() -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        echo=settings.debug,
        future=True,
        connect_args=_mysql_engine_connect_args(settings.database_url),
    )


def _build_session_maker(bind_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )


class _AsyncSessionMakerProxy:
    """可重绑定的 sessionmaker 代理。

    Celery prefork 模式下，worker 子进程不能继续复用父进程里初始化的
    async engine / sessionmaker。这里保持导入对象稳定，同时允许在子进程
    启动后重新绑定底层 sessionmaker。
    """

    def __init__(self, maker: async_sessionmaker[AsyncSession]) -> None:
        self._maker = maker

    def configure(self, maker: async_sessionmaker[AsyncSession]) -> None:
        self._maker = maker

    def __call__(self, *args: Any, **kwargs: Any) -> AsyncSession:
        return self._maker(*args, **kwargs)


engine = _build_engine()
async_session_maker = _AsyncSessionMakerProxy(_build_session_maker(engine))


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""

    pass


def uses_sqlite_database(database_url: str) -> bool:
    """判断连接串是否指向 SQLite，用于限制本地开发专用的建表行为。"""
    return database_url.lower().startswith("sqlite")


async def init_db() -> None:
    """创建所有表（开发/迁移用）。"""
    # 确保 ORM 模型已导入，从而注册到 Base.metadata
    import app.models.llm  # noqa: F401  # pylint: disable=unused-import
    import app.models.studio  # noqa: F401
    import app.models.task  # noqa: F401
    import app.models.task_dispatch_outbox  # noqa: F401
    import app.models.task_links  # noqa: F401
    import app.models.script_task_application  # noqa: F401
    import app.models.production_runs  # noqa: F401

    if uses_sqlite_database(settings.database_url):
        tables = None
    elif uses_mysql_database(settings.database_url):
        tables = [
            table
            for name, table in Base.metadata.tables.items()
            if name not in MYSQL_MIGRATION_MANAGED_TABLES
        ]
    else:
        tables = None

    async with engine.begin() as conn:
        if tables is None:
            await conn.run_sync(Base.metadata.create_all)
        else:
            await conn.run_sync(Base.metadata.create_all, tables=tables)


async def close_db() -> None:
    """关闭数据库连接。"""
    await engine.dispose()


def reset_db_runtime() -> None:
    """在 Celery worker 子进程中重建 engine 与 sessionmaker。

    这样可以避免 prefork 继承父进程中的 async engine，导致连接对象和事件循环
    绑定错乱，触发 Future attached to a different loop。
    """

    global engine

    engine = _build_engine()
    async_session_maker.configure(_build_session_maker(engine))
