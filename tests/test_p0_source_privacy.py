import os

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)
FORBIDDEN = {"name", "domain", "url", "official_url", "robots_checked", "tos_checked", "adapter", "compliance_mode"}


def test_normal_user_hides_source_names():
    rows = client.get("/api/sources", headers={"Authorization": "Bearer customer"}).json()
    assert rows and all("name" not in row for row in rows)


def test_normal_user_hides_source_domains():
    assert all("domain" not in row for row in client.get("/api/sources").json())


def test_normal_user_hides_source_urls():
    rows = client.get("/api/sources").json()
    assert all(not (set(row) & FORBIDDEN) for row in rows)


def test_developer_source_registry_details(monkeypatch):
    monkeypatch.setenv("SIGNAL_DEVELOPER_KEY", "test-key")
    rows = client.get("/api/developer/sources", headers={"X-Developer-Key": "test-key"}).json()
    assert rows and {"name", "domain", "official_url"} <= set(rows[0])
