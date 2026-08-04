"""film/tasks_images 接口响应壳测试。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from types import SimpleNamespace

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.v1.routes.film import tasks_images as route
from app.dependencies import get_db
from app.main import app


class _FakeTaskRecord:
    def __init__(self, task_id: str) -> None:
        self.id = task_id


class _FakeTaskManager:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    async def create(self, *_args, **_kwargs) -> _FakeTaskRecord:
        return _FakeTaskRecord("prompt-task-1")


class _FakeDB:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.committed = False

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.committed = True


async def _async_noop(*_args, **_kwargs) -> None:
    return None


def _override_db(db: _FakeDB):
    async def _get_db() -> AsyncGenerator[_FakeDB, None]:
        yield db

    return _get_db


def test_create_shot_frame_prompt_task_returns_created_envelope(client: TestClient, monkeypatch) -> None:
    db = _FakeDB()

    async def _fake_create(*_args, **_kwargs):
        return SimpleNamespace(task_id="prompt-task-1")

    monkeypatch.setattr(route, "create_frame_prompt_task", _fake_create)
    monkeypatch.setattr(route, "dispatch_staged_task", lambda task_id: SimpleNamespace(task_id=task_id))
    app.dependency_overrides[get_db] = _override_db(db)
    try:
        response = client.post(
            "/api/v1/film/tasks/shot-frame-prompts",
            json={"shot_id": "shot-1", "frame_type": "first"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    body = response.json()
    assert body["code"] == 201
    assert body["message"] == "success"
    assert body["data"]["task_id"] == "prompt-task-1"
    assert body["meta"] is None
    assert db.committed is True


def test_create_shot_frame_prompt_task_invalid_frame_type_returns_api_response(
    client: TestClient, monkeypatch
) -> None:
    db = _FakeDB()

    async def _fake_create(*_args, **_kwargs):
        raise HTTPException(status_code=400, detail="frame_type must be one of: first/last/key")

    monkeypatch.setattr(route, "create_frame_prompt_task", _fake_create)
    app.dependency_overrides[get_db] = _override_db(db)
    try:
        response = client.post(
            "/api/v1/film/tasks/shot-frame-prompts",
            json={"shot_id": "shot-1", "frame_type": "middle"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json() == {
        "code": 400,
        "message": "frame_type must be one of: first/last/key",
        "data": None,
        "meta": None,
    }


def test_create_shot_frame_prompt_task_missing_shot_detail_returns_api_response(
    client: TestClient, monkeypatch
) -> None:
    db = _FakeDB()

    async def _fake_create(*_args, **_kwargs):
        raise HTTPException(status_code=404, detail="ShotDetail not found")

    monkeypatch.setattr(route, "create_frame_prompt_task", _fake_create)
    app.dependency_overrides[get_db] = _override_db(db)
    try:
        response = client.post(
            "/api/v1/film/tasks/shot-frame-prompts",
            json={"shot_id": "shot-1", "frame_type": "first"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json() == {"code": 404, "message": "ShotDetail not found", "data": None, "meta": None}


def test_create_shot_frame_prompt_task_validation_error_returns_api_response(client: TestClient) -> None:
    db = _FakeDB()
    app.dependency_overrides[get_db] = _override_db(db)
    try:
        response = client.post(
            "/api/v1/film/tasks/shot-frame-prompts",
            json={"frame_type": "first"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == 422
    assert body["data"] is None
    assert "shot_id" in body["message"]
