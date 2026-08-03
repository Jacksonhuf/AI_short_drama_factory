"""S3 兼容对象存储客户端配置测试。"""

from app.config import settings
from app.core.storage import _build_s3_client


def test_build_s3_client_respects_path_addressing(monkeypatch) -> None:
    """内部 RustFS endpoint 必须使用 path-style，避免将 bucket 拼进主机名。"""
    monkeypatch.setattr(settings, "s3_bucket_name", "jellyfish-assets")
    monkeypatch.setattr(settings, "s3_endpoint_url", "http://rustfs:9000")
    monkeypatch.setattr(settings, "s3_region_name", "us-east-1")
    monkeypatch.setattr(settings, "s3_access_key_id", "test-access-key")
    monkeypatch.setattr(settings, "s3_secret_access_key", "test-secret-key")
    monkeypatch.setattr(settings, "s3_addressing_style", "path")

    client = _build_s3_client()

    assert client.meta.config.s3 == {"addressing_style": "path"}
