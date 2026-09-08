"""LLM provider settings API (public Settings screen, BYOK).

Covers: GET default state, save round-trip without key leakage,
invalid provider rejection, test-endpoint guard when unconfigured.
"""
import pytest
from fastapi.testclient import TestClient

from app.llm import LLM_CONFIG_PATH, _write_llm_config
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_llm_config():
    """Each test starts from a clean provider config (no key residue)."""
    _write_llm_config({})
    yield
    _write_llm_config({})


def test_llm_settings_default_state():
    r = client.get("/api/settings/llm")
    assert r.status_code == 200
    d = r.json()
    assert d["provider"] == "deepseek"
    assert d["key_present"] is False
    # The API key must never be serialized, even when present.
    assert "api_key" not in d
    assert "secret" not in r.text


def test_llm_settings_save_round_trip_never_leaks_key():
    r = client.post("/api/settings/llm", json={"provider": "openai", "api_key": "sk-test-secret-123"})
    assert r.status_code == 200
    d = r.json()
    assert d["provider"] == "openai"
    assert d["key_present"] is True
    assert "sk-test-secret-123" not in r.text
    # Persisted: a fresh GET reflects the saved provider with key masked.
    r2 = client.get("/api/settings/llm")
    assert r2.json()["provider"] == "openai"
    assert r2.json()["key_present"] is True
    # Restore default so other tests are not affected.
    client.post("/api/settings/llm", json={"provider": "deepseek", "api_key": ""})


def test_llm_settings_invalid_provider_rejected():
    r = client.post("/api/settings/llm", json={"provider": "bogus-llm", "api_key": "x"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "INVALID_PROVIDER"


def test_llm_settings_api_key_required_for_keyed_providers():
    r = client.post("/api/settings/llm", json={"provider": "anthropic", "api_key": ""})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "API_KEY_REQUIRED"


def test_llm_settings_alias_normalized():
    client.post("/api/settings/llm", json={"provider": "claude", "api_key": "sk-alias-1"})
    d = client.get("/api/settings/llm").json()
    assert d["provider"] == "anthropic"
    client.post("/api/settings/llm", json={"provider": "deepseek", "api_key": ""})


def test_llm_test_guard_when_unconfigured():
    client.post("/api/settings/llm", json={"provider": "deepseek", "api_key": ""})
    r = client.post("/api/settings/llm/test")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "LLM_NOT_CONFIGURED"


def test_llm_ollama_requires_no_key():
    r = client.post("/api/settings/llm", json={"provider": "ollama", "api_key": ""})
    assert r.status_code == 200
    assert r.json()["key_present"] is False
    client.post("/api/settings/llm", json={"provider": "deepseek", "api_key": ""})
