from pathlib import Path

import pytest

from app import research


SCRIPT = Path("scripts/start_searxng.sh").read_text(encoding="utf-8")
START = Path("start.command").read_text(encoding="utf-8")


def test_searxng_autostart_when_not_running():
    assert '"$SEARXNG_PYTHON" -m searx.webapp' in SCRIPT
    assert "nohup env SEARXNG_SETTINGS_PATH" in SCRIPT
    assert 'echo $! > "$SEARXNG_PID_FILE"' in SCRIPT


def test_searxng_not_started_twice():
    assert SCRIPT.index("if is_ready") < SCRIPT.index("nohup env")
    assert 'kill -0 "$OLD_PID"' in SCRIPT


def test_searxng_existing_instance_reused():
    assert '[SearXNG] 已运行' in SCRIPT and "exit 0" in SCRIPT


def test_searxng_missing_install_does_not_crash_app():
    assert "未检测到本地搜索服务安装" in SCRIPT and "exit 2" in SCRIPT
    assert "bash scripts/start_searxng.sh || true" in START


def test_searxng_health_timeout():
    assert "seq 1 20" in SCRIPT and "sleep 1" in SCRIPT
    assert "本地搜索服务启动失败" in SCRIPT


def test_searxng_uses_venv_python():
    assert 'SEARXNG_PYTHON="$SEARXNG_HOME/.venv/bin/python"' in SCRIPT
    assert "source .venv/bin/activate" not in SCRIPT
    assert "python3 -m searx.webapp" not in SCRIPT


def test_start_command_calls_searxng_bootstrap():
    assert "[2/7] 检查本地搜索服务" in START
    assert "scripts/start_searxng.sh" in START


def test_searxng_failure_degrades_search_only(monkeypatch):
    monkeypatch.setattr("httpx.get", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    provider = research.SearXNGProvider("http://127.0.0.1:8888")
    assert provider.available is False and provider.status == "SEARCH_PROVIDER_UNAVAILABLE"


def test_searxng_valid_health_and_mapping(monkeypatch):
    class Response:
        status_code = 200
        text = "<title>SearXNG</title>"
        def raise_for_status(self): pass
        def json(self): return {"results":[{"title":"Takis", "url":"https://target.invalid/item", "content":"Signal EUR 12,000", "engine":"bing"}]}
    calls=[]
    monkeypatch.setattr("httpx.get", lambda url,*a,**k: calls.append(str(url)) or Response())
    provider=research.SearXNGProvider("http://127.0.0.1:8888")
    rows=provider.search("Takis",1)
    assert provider.available and rows[0]["snippet"].startswith("Signal")
    assert calls == ["http://127.0.0.1:8888/", "http://127.0.0.1:8888/search"]
    assert "https://target.invalid/item" not in calls


def test_searxng_invalid_response(monkeypatch):
    class Response:
        status_code = 200
        text = "<title>Other</title>"
        def raise_for_status(self): pass
        def json(self): return {"unexpected":[]}
    monkeypatch.setattr("httpx.get", lambda *a,**k: Response())
    provider=research.SearXNGProvider("http://127.0.0.1:8888")
    assert provider.status == "INVALID_RESPONSE" and not provider.available


def test_port_collision_is_not_killed():
    assert "8888 端口已被其他程序占用" in SCRIPT
    assert "kill" not in SCRIPT.split("8888 端口已被其他程序占用")[1]


def test_no_install_upgrade_docker_or_sudo():
    for forbidden in ("git pull", "pip install", "docker", "brew ", "sudo"):
        assert forbidden not in SCRIPT.lower()


def test_local_settings_have_no_visible_api_key_form(monkeypatch):
    monkeypatch.setattr(research, "resolve_searxng_endpoint", lambda:{"endpoint":"http://configured.test","status":"READY","platform":"Windows"})
    state=research.search_provider_settings()
    assert state["mode"] == "LOCAL_TEST_ONLY" and state["key_present"] is False
