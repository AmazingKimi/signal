import inspect

from app.models import AnalysisInput
from app import research
from app.radar import create_radar, run_radar


TARGET = "https://example-target.invalid/item/123"


class SearchOnlyProvider(research.SearchProvider):
    name = "fixture"
    available = True
    status = "OK"
    technical_detail = ""
    def search(self, query, max_results=8):
        return [{"title":"Takis Signals", "snippet":"Pink signal sculpture EUR 12,000",
                 "url":TARGET, "provider":self.name, "provider_metadata":{}, "query":query}]


class FailedProvider(SearchOnlyProvider):
    status = "SEARCH_PROVIDER_UNAVAILABLE"
    def search(self, query, max_results=8):
        raise RuntimeError(self.status)


def inp():
    return AnalysisInput(artist="Takis", artwork="Signals", asking_price=10000, currency="EUR")


def test_search_only_never_fetches_result_url(monkeypatch):
    result = research.run_research(inp(), SearchOnlyProvider(), queries=["Takis Signals"])
    assert result["status"] == "OK" and result["pages"][0]["url"] == TARGET
    assert result["pages"][0]["evidence_level"] == "SEARCH_RESULT"


def test_search_only_no_target_domain_http(monkeypatch):
    import httpx
    calls = []
    def forbidden(url, *args, **kwargs):
        calls.append(str(url)); raise AssertionError(f"unexpected outbound HTTP: {url}")
    monkeypatch.setattr(httpx, "get", forbidden)
    monkeypatch.setattr(httpx, "post", forbidden)
    research.run_research(inp(), SearchOnlyProvider(), queries=["Takis Signals"])
    assert not calls


def test_provider_failure_never_falls_back_to_target(monkeypatch):
    result = research.run_research(inp(), FailedProvider(), queries=["Takis Signals"])
    assert result["records"] == [] and result["status"] == "SEARCH_PROVIDER_UNAVAILABLE"


def test_missing_provider_never_uses_ddgs(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEV_SEARCH_PROVIDER", "disabled")
    monkeypatch.delenv("SEARCH_PROVIDER", raising=False); monkeypatch.delenv("SEARCH_API_KEY", raising=False)
    provider = research.build_search_provider()
    result = research.run_research(inp(), provider, queries=["Takis Signals"])
    assert isinstance(provider, research.SearchProviderNotConfigured)
    assert result["status"] == "SEARCH_PROVIDER_NOT_CONFIGURED"


def test_radar_missing_provider_is_blocked_before_run():
    radar = create_radar("Missing Search Provider")
    source = {"name":"Fixture", "domain":"fixture.test", "compliance_reviewed":True,
              "compliance_mode":"SEARCH_ONLY", "health_status":"SEARCH_ONLY",
              "searchable_by_external_engine":True}
    import pytest
    with pytest.raises(RuntimeError, match="SEARCH_PROVIDER_NOT_CONFIGURED"):
        run_radar(radar["id"], provider=research.SearchProviderNotConfigured(), sources=[source], send_email=False)


def test_raw_provider_results_are_not_persisted(monkeypatch):
    import app.store as store
    assert not hasattr(store, "search_cache_put")
    result = research.run_research(inp(), SearchOnlyProvider(), queries=["Takis Signals"])
    assert result["pages"] and result["records"]


def test_search_only_has_no_fetch_or_cache_call_path():
    source = inspect.getsource(research.run_research)
    for forbidden in ("fetch_text(", "_fetch_with_cache", "search_cache_put", "page_cache_put"):
        assert forbidden not in source


def test_insufficient_snippet_keeps_price_unknown():
    provider = SearchOnlyProvider()
    provider.search = lambda query, max_results=8: [{"title":"Takis Signals", "snippet":"Pink signal sculpture", "url":TARGET}]
    result = research.run_research(inp(), provider, queries=["Takis Signals"])
    assert result["records"] == []
