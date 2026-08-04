"""init_db 在不同数据库方言下的建表策略测试。"""

from app.core.db import MYSQL_MIGRATION_MANAGED_TABLES, uses_mysql_database, uses_sqlite_database


def test_mysql_init_db_managed_tables_include_workflow_schema() -> None:
    """MySQL 增量脚本负责的表不应由 ORM create_all 直接创建。"""
    assert "task_dispatch_outbox" in MYSQL_MIGRATION_MANAGED_TABLES
    assert "script_task_applications" in MYSQL_MIGRATION_MANAGED_TABLES
    assert "chapter_production_runs" in MYSQL_MIGRATION_MANAGED_TABLES
    assert "production_run_transitions" in MYSQL_MIGRATION_MANAGED_TABLES


def test_database_dialect_detection() -> None:
    """方言检测用于区分 SQLite 本地开发与 MySQL 生产部署。"""
    assert uses_sqlite_database("sqlite+aiosqlite:///./dev.db")
    assert uses_mysql_database("mysql+aiomysql://user:pass@mysql:3306/jellyfish")
    assert uses_mysql_database("mariadb+pymysql://user:pass@mysql:3306/jellyfish")
    assert not uses_mysql_database("sqlite+aiosqlite:///./dev.db")
