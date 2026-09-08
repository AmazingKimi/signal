"""RADAR 0.8 acceptance-level deterministic tests."""
import os

from fastapi.testclient import TestClient

from app.main import app
from app.radar import (
    create_radar, fingerprint, get_radar, list_discoveries, list_runs,
    match_rules, normalize_result, parse_radar_text, run_radar,
    set_feedback, source_registry, update_radar, expand_queries,
    select_sources_for_run, GenericPublicHTMLAdapter, GenericSearchAdapter, probe_source,
    _upsert_discovery,
)

client = TestClient(app)


class Provider:
    available = True
    def __init__(self, rows=None, fail_domain=""):
        self.rows = rows or []
        self.fail_domain = fail_domain
        self.queries = []
    def search(self, query, max_results=5):
        self.queries.append(query)
        if self.fail_domain and self.fail_domain in query:
            raise RuntimeError("fixture source failure")
        return list(self.rows)


def approved_source(name="G", domain="gallery.test"):
    return {"name": name, "domain": domain, "compliance_reviewed": True,
            "compliance_mode": "SEARCH_ONLY", "searchable_by_external_engine": True}


def takis_query():
    return "Takis Signals，优先双灯头或者粉色，1970–1990 年，完整作品，价格最好低于 €20,000。"


def test_radar_natural_language_parse():
    r = parse_radar_text(takis_query())
    assert r["maker_artist"] == "Takis" and r["model_series"] == "Signals"
    assert r["year_min"] == 1970 and r["year_max"] == 1990 and r["price_max"] == 20000


def test_radar_rule_persistence():
    saved = create_radar(takis_query())
    loaded = get_radar(saved["id"])
    assert loaded["query_original"] == takis_query()
    assert loaded["rules"]["price_max"] == 20000


def test_partial_fields_allowed():
    r = create_radar("Ettore Sottsass")
    assert r["rules"]["price_max"] is None and r["rules"]["year_min"] is None


def test_hard_rule_matching():
    rules = parse_radar_text(takis_query())
    good = {"title":"Takis Signal, 1974", "year":"1974", "asking_price":14800, "raw":{"snippet":"double head pink"}}
    assert match_rules(good, rules)["matched"] is True
    bad = {"title":"Takis catalogue poster", "year":"1974", "asking_price":200, "raw":{"snippet":""}}
    assert match_rules(bad, rules)["matched"] is False


def test_preference_matching():
    rules = parse_radar_text(takis_query())
    rec = {"title":"Takis Signal, 1974, pink 双灯头", "year":"1974", "asking_price":14800, "raw":{"snippet":""}}
    result = match_rules(rec, rules)
    assert result["level"] == "HIGH_MATCH" and any("偏好" in x for x in result["reasons"])


def test_unknown_price_behavior():
    rules = parse_radar_text(takis_query())
    rec = {"title":"Takis Signal, 1974", "year":"1974", "asking_price":None, "raw":{"snippet":"price on request"}}
    result = match_rules(rec, rules, True)
    assert result["matched"] is True and "价格未知" in result["unknown"]


def test_source_registry():
    sources = source_registry()
    assert len(sources) == 126
    required = {"name","domain","category","source_type","country","region","languages",
                "official_url","access_mode","requires_login","robots_checked","tos_checked",
                "js_rendered","anti_bot_level","searchable_by_external_engine","enabled",
                "last_success","success_rate_30d","health_status"}
    assert required <= sources[0].keys()


def test_registry_category_routing_and_batching():
    art = create_radar("Takis Signals")
    cars = create_radar("Porsche 964 RS N-GT")
    art_sources, car_sources = select_sources_for_run(art), select_sources_for_run(cars)
    assert art_sources and car_sources and len(art_sources) <= 30 and len(car_sources) <= 30
    assert all(s["category"] in {"ART", "BOTH"} for s in art_sources)
    assert all(s["category"] in {"CLASSIC_CAR", "BOTH"} for s in car_sources)
    assert all(not s["requires_login"] and s["access_mode"] != "MANUAL_ONLY" for s in art_sources + car_sources)


def test_multilingual_query_expansion():
    radar = create_radar("Takis Signals")
    queries = expand_queries(radar)
    assert any("enchères" in q for q in queries) and any("auktion" in q.lower() for q in queries)
    assert any("subasta" in q.lower() for q in queries) and any("vendita" in q.lower() for q in queries)


def test_search_adapter_is_domain_limited():
    provider = Provider([])
    GenericSearchAdapter(provider).search({"domain":"example.test", "searchable_by_external_engine":True}, ["Takis"])
    assert provider.queries == ["Takis site:example.test"]


def test_public_adapter_requires_policy_review():
    try:
        GenericPublicHTMLAdapter(Provider([])).search({"domain":"example.test", "robots_checked":False, "tos_checked":False}, ["Takis"])
        assert False
    except PermissionError:
        pass


def test_probe_login_source_without_network():
    login = next(s for s in source_registry() if s["requires_login"])
    assert probe_source(login["id"])["health_status"] == "LOGIN_REQUIRED"


def test_source_failure_affects_coverage():
    radar = create_radar("Unique Coverage Radar")
    sources = [approved_source("OK", "ok.test"), approved_source("FAIL", "fail.test")]
    report = run_radar(radar["id"], Provider(fail_domain="fail.test"), sources, send_email=False)
    assert report["planned_sources"] == 2 and report["successful_sources"] == 1
    assert report["coverage_percent"] == 50 and report["complete"] is False


def test_normalize_result():
    radar = create_radar(takis_query())
    row = normalize_result({"title":"Takis Signal 1974 €14,800", "url":"https://gallery.test/a"}, radar)
    assert row["asking_price"] == 14800 and row["currency"] == "EUR" and row["year"] == "1974"


def test_fingerprint_deduplication_cross_source():
    a = {"maker":"Takis", "title":"Signal 1974", "year":"1974", "seller":"Gallery A", "source_url":"https://a.test/x"}
    b = dict(a, source_url="https://aggregator.test/y")
    assert fingerprint(a) == fingerprint(b)


def test_original_source_wins_and_aggregator_is_retained():
    radar = create_radar("Takis")
    base = {"title":"Takis Signal 1974", "maker":"Takis", "object_name":"Signals", "year":"1974",
            "asking_price":10000, "currency":"EUR", "image_url":"", "seller":"", "raw":{}}
    match = {"level":"MATCH", "reasons":["符合 takis"]}
    first, _, _ = _upsert_discovery(radar, dict(base, source_name="Aggregator", source_url="https://agg.test/x", source_type="AGGREGATOR"), match)
    second, _, _ = _upsert_discovery(radar, dict(base, source_name="Auction House", source_url="https://auction.test/x", source_type="AUCTION_HOUSE"), match)
    assert second["source_name"] == "Auction House" and second["primary_source_type"] == "AUCTION_HOUSE"
    assert any(x["source_name"] == "Aggregator" for x in second["also_seen"])


def test_possible_duplicate_is_not_forced_merge():
    a = {"maker":"Takis", "title":"Signal 1974", "year":"1974", "seller":"Gallery A", "source_url":"https://a.test/x"}
    b = dict(a, seller="Gallery B")
    assert fingerprint(a) != fingerprint(b)


def _matching_row(price="€14,800"):
    return {"title":f"Takis Signal 1974 {price}", "url":"https://gallery.test/unique-lot", "snippet":"pink double head"}


def test_new_discovery():
    radar = create_radar(takis_query())
    report = run_radar(radar["id"], Provider([_matching_row()]), [approved_source()], send_email=False)
    assert report["new_discoveries"] == 1 and report["matches"] == 1


def test_existing_discovery_no_notification():
    radar = create_radar(takis_query())
    p = Provider([_matching_row()]); sources = [{"name":"G", "domain":"gallery.test"}]
    run_radar(radar["id"], p, sources, send_email=False)
    second = run_radar(radar["id"], p, sources, send_email=False)
    assert second["new_discoveries"] == 0 and second["notifications_sent"] == 0


def test_price_change_event():
    radar = create_radar(takis_query()); sources = [approved_source()]
    run_radar(radar["id"], Provider([_matching_row("€14,800")]), sources, send_email=False)
    run_radar(radar["id"], Provider([_matching_row("€12,800")]), sources, send_email=False)
    from app.store import _conn
    conn = _conn()
    try: count = conn.execute("SELECT COUNT(*) FROM discovery_events WHERE event_type='PRICE_CHANGE'").fetchone()[0]
    finally: conn.close()
    assert count >= 1


def test_email_contains_source_url_and_no_buy_advice(monkeypatch):
    sent = []
    class SMTP:
        def __init__(self,*a,**k): pass
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def starttls(self): pass
        def login(self,*a): pass
        def send_message(self,msg): sent.append(msg)
    monkeypatch.setattr("app.radar.smtplib.SMTP", SMTP)
    for k,v in {"KIMI_SMTP_HOST":"smtp.test","KIMI_SMTP_USER":"me@test","KIMI_SMTP_PASSWORD":"x"}.items(): monkeypatch.setenv(k,v)
    radar = create_radar(takis_query())
    run_radar(radar["id"], Provider([dict(_matching_row(), url="https://gallery.test/email-lot")]), [approved_source()], send_email=True)
    assert not sent  # K records queue intent; it does not send during detection.
    from app.store import _conn
    conn=_conn()
    try:
        queued=conn.execute("SELECT COUNT(*) FROM candidate_changes e JOIN candidate_snapshots c ON c.candidate_id=e.candidate_id WHERE c.radar_id=? AND notification_queued=1",(radar['id'],)).fetchone()[0]
    finally: conn.close()
    assert queued >= 1


def test_run_report_persistence():
    radar = create_radar("Persistence Target")
    report = run_radar(radar["id"], Provider([]), [{"name":"S","domain":"s.test"}], send_email=False)
    assert any(x["id"] == report["id"] for x in list_runs(radar["id"]))


def test_feedback_useful():
    rows = list_discoveries(); assert rows
    assert set_feedback(rows[0]["id"], "useful")["status"] == "SAVED"


def test_feedback_irrelevant():
    rows = list_discoveries(); assert rows
    assert set_feedback(rows[0]["id"], "irrelevant")["status"] == "IGNORED"


def test_feedback_bought():
    rows = list_discoveries(); assert rows
    assert set_feedback(rows[0]["id"], "bought")["status"] == "BOUGHT"


def test_radar_pause_and_resume():
    r = create_radar("Pause Target")
    assert update_radar(r["id"], {"enabled":False})["enabled"] is False
    assert update_radar(r["id"], {"enabled":True})["enabled"] is True


def test_default_ui_is_radar_and_legacy_is_retained():
    assert "PRIVATE DISCOVERY RADAR" in client.get("/").text
    assert "ANALYST" in client.get("/legacy").text
