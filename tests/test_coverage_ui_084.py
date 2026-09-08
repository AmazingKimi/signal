import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.radar import _email_new_discovery


ROOT = Path(__file__).parents[1]
HTML = (ROOT / "app/static/radar.html").read_text(encoding="utf-8")
JS = (ROOT / "app/static/radar-app.js").read_text(encoding="utf-8")
ZH = json.loads((ROOT / "app/static/locales/zh-CN.json").read_text(encoding="utf-8"))
EN = json.loads((ROOT / "app/static/locales/en-US.json").read_text(encoding="utf-8"))
client = TestClient(app)


def test_empty_state_contains_coverage():
    assert "search.emptyContext" in JS and all(x in JS for x in ("queries_completed", "search_results_processed", "target_market_sources_registered"))


def test_coverage_i18n_zh():
    assert ZH["search.targetScope"] == "目标市场范围"
    assert ZH["search.status.PARTIAL"] == "本次搜索部分完成"


def test_coverage_i18n_en():
    assert EN["search.resultsProcessed"] == "public search results processed"
    assert EN["search.status.FAILED"] == "Search run failed"


def test_coverage_light_dark():
    assert '[data-theme="light"]' in HTML and '[data-theme="dark"]' in HTML
    assert "coverage-detail" in HTML and "var(--surface)" in HTML


def test_normal_user_hides_source_names():
    assert "/api/developer/runs/" not in JS
    assert "c.failures" not in JS


def test_normal_coverage_api_hides_source_names():
    rows = client.get("/api/radar/runs").json()
    assert all("failures" not in row and "source_details" not in row for row in rows)


def test_search_transparency_api_has_query_and_result_metrics():
    rows = client.get("/api/radar/runs").json()
    if rows:
        run_id = rows[0]["id"]
        result = client.get(f"/api/runs/{run_id}/coverage").json()
        assert {"queries_planned", "queries_completed", "queries_failed", "search_results_received",
                "search_results_processed", "target_market_sources_registered"} <= set(result)
        assert "source_details" not in result


def test_developer_coverage_has_source_details(monkeypatch):
    rows = client.get("/api/radar/runs").json()
    if rows:
        monkeypatch.setenv("SIGNAL_DEVELOPER_KEY", "developer-test")
        result = client.get(f"/api/developer/runs/{rows[0]['id']}/coverage", headers={"X-Developer-Key":"developer-test"}).json()
        assert "source_details" in result


def test_desktop_notification_contains_coverage():
    assert "desktopCoverage" in JS and "search.notificationBody" in JS


def test_discovery_email_contains_coverage(monkeypatch):
    sent = []
    class SMTP:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def starttls(self): pass
        def login(self, *args): pass
        def send_message(self, msg): sent.append(msg)
    monkeypatch.setattr("app.radar.smtplib.SMTP", SMTP)
    for key, value in {"KIMI_SMTP_HOST":"smtp.test", "KIMI_SMTP_USER":"user@test", "KIMI_SMTP_PASSWORD":"x"}.items():
        monkeypatch.setenv(key, value)
    discovery = {"maker":"Takis", "title":"Signal", "currency":"EUR", "asking_price":1000,
                 "source_name":"Source", "match_reasons":[], "first_seen":"now", "source_url":"https://example.test"}
    assert _email_new_discovery({"name":"Takis"}, discovery, {"queries_completed":12, "queries_planned":12,
                                                                 "results_processed":27, "target_scope":126})
    body = sent[0].get_content()
    assert "12 / 12" in body and "27" in body and "126" in body
