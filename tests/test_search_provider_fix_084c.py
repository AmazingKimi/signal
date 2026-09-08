import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import research
from app.main import app
from app.radar import create_radar, list_runs, run_radar


client = TestClient(app)


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    path = tmp_path / "search_provider_config.json"
    monkeypatch.setattr(research, "SEARCH_CONFIG_PATH", path)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEV_SEARCH_PROVIDER", "disabled")
    for key in ("SEARCH_PROVIDER", "SEARCH_API_KEY", "TAVILY_API_KEY", "BRAVE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    return path


def test_old_duckduckgo_env_migrates_to_unconfigured(isolated_config, monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "duckduckgo")
    monkeypatch.setenv("SEARCH_API_KEY", "legacy-secret")
    state = research.search_provider_settings()
    assert state["configured"] is False and state["key_present"] is False
    assert isinstance(research.build_search_provider(), research.SearchProviderNotConfigured)


def test_settings_search_provider_get(isolated_config):
    data = client.get("/api/settings/search-provider").json()
    assert data["configured"] is False and "api_key" not in data


@pytest.mark.parametrize("name,key_field", [("tavily", "tavily_api_key"), ("brave", "brave_api_key")])
def test_settings_save_provider_key(isolated_config, name, key_field):
    response = client.post("/api/settings/search-provider", json={"provider": name, "api_key": "secret-value"})
    assert response.status_code == 200 and response.json()["key_present"] is True
    assert "secret-value" not in response.text
    assert json.loads(isolated_config.read_text())[key_field] == "secret-value"


def test_tavily_test_connection_success(isolated_config, monkeypatch):
    class Response:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"results": [{"title":"Result", "url":"https://example.com", "content":"ok"}]}
    monkeypatch.setattr("httpx.post", lambda *a, **k: Response())
    response = client.post("/api/settings/search-provider/test", json={"provider":"tavily", "api_key":"tvly-secret"})
    assert response.status_code == 200 and response.json()["test_status"] == "OK"
    assert "tvly-secret" not in response.text


def test_brave_test_connection_success(isolated_config, monkeypatch):
    class Response:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"web":{"results":[{"title":"Result", "url":"https://example.com", "description":"ok"}]}}
    monkeypatch.setattr("httpx.get", lambda *a, **k: Response())
    response = client.post("/api/settings/search-provider/test", json={"provider":"brave", "api_key":"brave-secret"})
    assert response.status_code == 200 and response.json()["test_status"] == "OK"


def test_invalid_key_returns_friendly_error(isolated_config, monkeypatch):
    import httpx
    request = httpx.Request("POST", "https://api.tavily.com/search")
    response = httpx.Response(401, request=request)
    monkeypatch.setattr("httpx.post", lambda *a, **k: (_ for _ in ()).throw(httpx.HTTPStatusError("unauthorized", request=request, response=response)))
    result = client.post("/api/settings/search-provider/test", json={"provider":"tavily", "api_key":"bad"})
    assert result.status_code == 400 and result.json()["detail"]["code"] == "INVALID_API_KEY"
    assert "bad" not in result.text and "traceback" not in result.text.lower()
    provider = research.build_search_provider()
    assert provider.available is False and provider.status == "INVALID_API_KEY"


def test_missing_provider_does_not_create_failed_run(isolated_config):
    radar = create_radar("Provider gate")
    before = len(list_runs(radar["id"]))
    with pytest.raises(RuntimeError, match="SEARCH_PROVIDER_NOT_CONFIGURED"):
        run_radar(radar["id"], provider=research.SearchProviderNotConfigured(), sources=[])
    assert len(list_runs(radar["id"])) == before


def test_tavily_and_brave_use_separate_keys(isolated_config, monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-only")
    monkeypatch.setenv("BRAVE_API_KEY", "brave-only")
    monkeypatch.setenv("SEARCH_PROVIDER", "tavily")
    assert research.build_search_provider().api_key == "tavily-only"
    monkeypatch.setenv("SEARCH_PROVIDER", "brave")
    assert research.build_search_provider().api_key == "brave-only"


def test_no_duckduckgo_fallback_in_production():
    production = "\n".join(Path(p).read_text(encoding="utf-8") for p in
                            ["app/research.py", "app/main.py", ".env.example"])
    assert "ProductionProviderNotAllowed" in production and "SEARCH_API_KEY" not in production


def test_settings_ui_has_real_save_and_test_flow():
    js = Path("app/static/radar-app.js").read_text(encoding="utf-8")
    assert "/api/settings/search-provider/test" in js
    assert "type=\"password\"" in js and "saveAndTestSearchProvider" in js
    assert "settings.searchNeedsCheck" in js
