"""管理员密码登录与业务 API 门禁测试。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import settings


@pytest.fixture
def protected_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """在单个测试中开启门禁，避免改变无认证历史 API 测试的默认前提。"""
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "auth_admin_password", "correct-password")
    monkeypatch.setattr(settings, "auth_session_secret", "test-session-secret")


def test_password_login_protects_business_routes(client: TestClient, protected_auth: None) -> None:
    """未登录请求被拒绝，正确密码建立 Session Cookie，登出后门禁恢复。"""
    protected = client.get("/api/v1/studio/projects")
    assert protected.status_code == 401
    assert protected.json()["message"] == "Authentication required"

    invalid = client.post("/api/v1/auth/login", json={"password": "wrong-password"})
    assert invalid.status_code == 401
    assert invalid.json()["message"] == "Invalid administrator password"

    login = client.post("/api/v1/auth/login", json={"password": "correct-password"})
    assert login.status_code == 200
    assert login.json()["data"] == {"authenticated": True, "username": "Admin"}

    session = client.get("/api/v1/auth/session")
    assert session.status_code == 200
    assert session.json()["data"] == {"authenticated": True, "username": "Admin"}

    logout = client.post("/api/v1/auth/logout")
    assert logout.status_code == 200

    protected_again = client.get("/api/v1/studio/projects")
    assert protected_again.status_code == 401
