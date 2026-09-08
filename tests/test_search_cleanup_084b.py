import json
from pathlib import Path

from app import research, store
from app.radar import create_radar, run_radar


ROOT = Path(__file__).parents[1]
JS = (ROOT / "app/static/radar-app.js").read_text(encoding="utf-8")
HTML = (ROOT / "app/static/radar.html").read_text(encoding="utf-8")
ZH = json.loads((ROOT / "app/static/locales/zh-CN.json").read_text(encoding="utf-8"))
EN = json.loads((ROOT / "app/static/locales/en-US.json").read_text(encoding="utf-8"))


class QueryProvider(research.SearchProvider):
    name = "fixture"; available = True; status = "OK"; technical_detail = ""
    def __init__(self, fail_domain="", results=1): self.fail_domain=fail_domain; self.results=results
    def search(self, query, max_results=5):
        if self.fail_domain and self.fail_domain in query: raise RuntimeError("SEARCH_PROVIDER_UNAVAILABLE")
        return [{"title":f"Result {i}", "snippet":"public result", "url":f"https://target.invalid/{i}"} for i in range(self.results)]


def source(domain):
    return {"name":domain, "domain":domain, "compliance_reviewed":True, "compliance_mode":"SEARCH_ONLY",
            "health_status":"SEARCH_ONLY", "searchable_by_external_engine":True}


def test_provider_not_configured_is_inline_state():
    assert "provider-inline" in HTML and "renderProviderState" in JS
    assert ZH["provider.inline.SEARCH_PROVIDER_NOT_CONFIGURED.title"] == "搜索服务尚未连接"
    assert EN["provider.openSettings"] == "Open Settings"


def test_provider_not_configured_no_black_error_toast():
    run_fn = JS[JS.index("async function runRadar"):JS.index("function money")]
    assert "toast(r.search_provider_status" not in run_fn
    assert "if(r.search_provider_status==='OK')toast" in run_fn


def test_dead_fetch_text_removed_or_unreachable():
    assert not hasattr(research, "fetch_text")
    assert "fetch_text(" not in (ROOT / "app/research.py").read_text(encoding="utf-8")


def test_search_cache_removed_for_raw_results():
    assert not hasattr(store, "search_cache_get") and not hasattr(store, "search_cache_put")
    assert not hasattr(store, "page_cache_get") and not hasattr(store, "page_cache_put")


def test_user_ui_does_not_show_checked_source_ratio():
    for old in ("successfully_checked_sources", "reviewed_eligible_sources", "planned_sources", "coverage_ratio_run"):
        assert old not in JS


def test_user_ui_shows_query_count():
    assert "queries_completed" in JS and "queries_planned" in JS


def test_user_ui_shows_processed_result_count():
    assert "search_results_processed" in JS


def test_user_ui_shows_registered_market_scope():
    assert "target_market_sources_registered" in JS and ZH["search.targetScope"] == "目标市场范围"


def test_complete_based_on_queries():
    radar=create_radar("Query Complete"); result=run_radar(radar["id"],QueryProvider(),[source("a.test")],False)
    assert result["queries_completed"] == result["queries_planned"] and result["run_status"] == "COMPLETE"


def test_partial_based_on_queries():
    radar=create_radar("Query Partial"); result=run_radar(radar["id"],QueryProvider("fail.test"),[source("ok.test"),source("fail.test")],False)
    assert 0 < result["queries_completed"] < result["queries_planned"] and result["run_status"] == "PARTIAL"


def test_failed_based_on_queries():
    import pytest
    radar=create_radar("Query Failed")
    with pytest.raises(RuntimeError, match="SEARCH_PROVIDER_NOT_CONFIGURED"):
        run_radar(radar["id"],research.SearchProviderNotConfigured(),[source("a.test")],False)


def test_empty_state_uses_search_result_context():
    assert "search.emptyContext" in JS and "search_results_processed" in JS


def test_global_source_network_explains_registry_is_scope():
    assert "不会因为某个来源被登记，就自动访问该网站" in ZH["settings.sourceNote"]
    assert "does not cause SIGNAL to visit" in EN["settings.sourceNote"]


def test_network_audit_no_target_domain_requests(monkeypatch):
    import httpx
    calls=[]
    monkeypatch.setattr(httpx,"get",lambda url,*a,**k: calls.append(str(url)))
    monkeypatch.setattr(httpx,"post",lambda url,*a,**k: calls.append(str(url)))
    radar=create_radar("No Target Request"); run_radar(radar["id"],QueryProvider(),[source("a.test")],False)
    assert calls == []
