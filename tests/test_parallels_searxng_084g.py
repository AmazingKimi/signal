from pathlib import Path

from app import research


def test_searxng_explicit_url_priority(monkeypatch):
    monkeypatch.setenv("SEARXNG_BASE_URL", "http://configured.test:8888")
    monkeypatch.setenv("SEARXNG_FALLBACK_URL", "http://fallback.test:8888")
    calls=[]; monkeypatch.setattr(research,"check_searxng_endpoint",lambda url:calls.append(url) or "READY")
    result=research.resolve_searxng_endpoint("Darwin")
    assert result["endpoint"] == "http://configured.test:8888" and calls == ["http://configured.test:8888"]


def test_searxng_localhost_on_mac(monkeypatch):
    monkeypatch.delenv("SEARXNG_BASE_URL",raising=False);monkeypatch.delenv("SEARXNG_FALLBACK_URL",raising=False)
    monkeypatch.setattr(research,"check_searxng_endpoint",lambda url:"READY")
    assert research.resolve_searxng_endpoint("Darwin")["endpoint"] == "http://127.0.0.1:8888"


def test_windows_requires_configured_remote_url(monkeypatch):
    monkeypatch.delenv("SEARXNG_BASE_URL",raising=False);monkeypatch.delenv("SEARXNG_FALLBACK_URL",raising=False)
    assert research.resolve_searxng_endpoint("Windows")["endpoint"] is None


def test_windows_never_runs_mac_autostart():
    cmd=Path("start.cmd").read_text(encoding="utf-8")
    for forbidden in ("~/searxng","searx.webapp","start_searxng.sh","bash "):
        assert forbidden not in cmd


def test_mac_can_autostart_local_searxng():
    script=Path("scripts/start_searxng.sh").read_text(encoding="utf-8")
    assert 'SYSTEM_NAME" != "Darwin' in script
    assert '"$SEARXNG_PYTHON" -m searx.webapp' in script


def test_endpoint_health_check(monkeypatch):
    class Response:
        text = "<title>SearXNG</title>"
        def raise_for_status(self): pass
        def json(self): return {"results":[]}
    monkeypatch.setattr("httpx.get",lambda *a,**k:Response())
    assert research.check_searxng_endpoint("http://search.test") == "READY"


def test_endpoint_failover(monkeypatch):
    monkeypatch.setenv("SEARXNG_BASE_URL","http://primary.test")
    monkeypatch.setenv("SEARXNG_FALLBACK_URL","http://fallback.test")
    monkeypatch.setattr(research,"check_searxng_endpoint",lambda url:"READY" if "fallback" in url else "UNAVAILABLE")
    assert research.resolve_searxng_endpoint("Windows")["endpoint"] == "http://fallback.test"


def test_endpoint_not_exposed_to_normal_ui():
    js=Path("app/static/radar-app.js").read_text(encoding="utf-8")
    html=Path("app/static/radar.html").read_text(encoding="utf-8")
    assert "SEARXNG_BASE_URL" not in js+html and "resolved_endpoint" not in js+html


def test_provider_uses_resolved_endpoint(monkeypatch):
    monkeypatch.setattr(research,"resolve_searxng_endpoint",lambda:{"endpoint":"http://resolved.test:8888","status":"READY","platform":"Windows"})
    provider=research.SearXNGProvider()
    assert provider.base_url == "http://resolved.test:8888" and provider.available


def test_no_hardcoded_mac_ip():
    import re
    production="\n".join(Path(p).read_text(encoding="utf-8") for p in ["app/research.py","app/main.py","app/static/radar-app.js","start.command","start.cmd"])
    assert not re.search(r"192\.168\.\d{1,3}\.\d{1,3}", production)


def test_no_target_site_fetch_regression():
    source=Path("app/research.py").read_text(encoding="utf-8")
    assert "fetch_text" not in source and "requests.get" not in source


def test_html_only_searxng_fallback_never_fetches_result(monkeypatch):
    html='''<html><title>SearXNG</title><article class="result result-default"><h3><a href="https://target.invalid/item">Takis Signal</a></h3><p class="content">Signal EUR 12,000</p></article></html>'''
    class Response:
        def __init__(self,status,text): self.status_code=status;self.text=text
        def raise_for_status(self):
            if self.status_code >= 400: raise RuntimeError(str(self.status_code))
        def json(self): return {}
    calls=[]
    def fake_get(url,*a,**k):
        calls.append(url)
        return Response(403,"") if len(calls)==1 else Response(200,html)
    monkeypatch.setattr("httpx.get",fake_get)
    monkeypatch.setattr(research,"resolve_searxng_endpoint",lambda:{"endpoint":"http://resolved.test","status":"READY","platform":"Windows"})
    rows=research.SearXNGProvider().search("Takis",1)
    assert rows[0]["url"] == "https://target.invalid/item"
    assert calls == ["http://resolved.test/search","http://resolved.test/search"]


def test_developer_api_only_contains_resolved_endpoint():
    main=Path("app/main.py").read_text(encoding="utf-8")
    assert '"resolved_endpoint"' in main and "/api/settings/dev" in main
