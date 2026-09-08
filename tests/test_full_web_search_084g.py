from pathlib import Path

from app.radar import (GenericSearchAdapter, P0_ART_DOMAINS, classify_availability,
                       create_radar, dedupe_queries, expand_queries, normalize_result,
                       run_radar, score_actionability, source_registry)


ROOT = Path(__file__).parents[1]
HTML = (ROOT / "app/static/radar.html").read_text(encoding="utf-8")


def test_settings_hides_search_service():
    assert "searchProviderPanel" not in HTML and "API Key" not in HTML and "Tavily" not in HTML


def test_settings_hides_unknown_price_block():
    assert 'data-i18n="settings.unknownPrice"' not in HTML


def test_settings_hides_legacy_module():
    assert 'href="/legacy"' not in HTML and 'data-i18n="settings.legacy"' not in HTML


def test_art_priority_sources_count_is_6():
    assert len(P0_ART_DOMAINS) == 6
    rows = {x["domain"]: x for x in source_registry()}
    assert all(rows[x]["priority"] == 0 for x in P0_ART_DOMAINS)


def test_query_planner_has_no_fixed_12_query_cap():
    assert len(expand_queries(create_radar("Takis Signals"))) > 12


def test_query_planner_can_generate_more_than_90_queries():
    assert len(expand_queries(create_radar("Takis Signals"))) > 90


def test_query_deduplicates_exact_duplicates():
    assert dedupe_queries(["Takis  Signal auction", "takis signal auction", "Takis Signal sale"]) == [
        "Takis Signal auction", "Takis Signal sale"]


def test_query_plan_includes_p0_general_and_multilingual():
    queries = expand_queries(create_radar("Takis Signals"))
    assert all(any(f"site:{domain}" in q for q in queries) for domain in P0_ART_DOMAINS)
    assert any("site:" not in q and "for sale" in q for q in queries)
    assert any(any(word in q for word in ("enchères", "Auktion", "vendita")) for q in queries)


def test_long_query_plan_runs_in_batches_without_truncation():
    class Provider:
        name = "searxng_local"
        def __init__(self): self.queries = []
        def search(self, query, max_results=5): self.queries.append(query); return []
    provider = Provider(); queries = [f"valuable angle {i}" for i in range(120)]
    GenericSearchAdapter(provider).search({"domain":"full-web.local", "searchable_by_external_engine":True}, queries)
    assert provider.queries == queries


def test_partial_run_preserves_unfinished_counts():
    class Provider:
        name = "fixture"; status = "OK"
        def __init__(self): self.calls = 0
        def search(self, query, max_results=5):
            self.calls += 1
            if self.calls == 41: raise RuntimeError("network unavailable")
            return []
    radar = create_radar("Takis Signals")
    source = {"name":"Full web", "domain":"full-web.local", "compliance_reviewed":True,
              "compliance_mode":"SEARCH_ONLY", "health_status":"SEARCH_ONLY",
              "searchable_by_external_engine":True}
    report = run_radar(radar["id"], Provider(), [source], send_email=False)
    assert report["queries_planned"] > 90 and report["queries_completed"] == 40
    assert report["queries_failed"] == report["queries_planned"] - 40 and report["run_status"] == "PARTIAL"


def test_p0_result_ranks_above_equal_standard_result():
    radar = create_radar("Takis Signals")
    p0 = normalize_result({"title":"Takis Signal available", "url":"https://artsy.net/artwork/1"}, radar)
    std = normalize_result({"title":"Takis Signal available", "url":"https://gallery.test/work/1"}, radar)
    match = {"matched":True, "reasons":["符合 takis", "符合 signal"]}
    assert score_actionability(p0, match, "FOR_SALE")["actionability_score"] > score_actionability(std, match, "FOR_SALE")["actionability_score"]


def test_past_sale_not_classified_as_live_opportunity():
    record = {"title":"Takis Signal sold for EUR 50,000", "raw":{"snippet":"auction result"}, "source_url":"https://example.test/lot"}
    assert classify_availability(record) == "SOLD"


def test_dated_auction_and_directory_pages_are_not_live_opportunities():
    dated = {"title":"Contemporary Art Sale", "raw":{"snippet":"Takis Signal EUR 70,000"},
             "source_url":"https://sothebys.com/en/auctions/2007/contemporary-art-sale.html", "asking_price":70000}
    directory = {"title":"Upcoming Auctions & Past Catalogs", "raw":{"snippet":"Takis Signal"},
                 "source_url":"https://liveauctioneers.com/auctioneer/6428/example/"}
    biography = {"title":"Takis Biografie", "raw":{"snippet":"Takis Signal available"},
                 "source_url":"https://example.com/artist/takis"}
    assert classify_availability(dated) == "PAST_AUCTION"
    assert classify_availability(directory) == "REFERENCE"
    assert classify_availability(biography) == "ARTICLE"
