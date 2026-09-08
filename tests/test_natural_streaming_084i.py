import threading

from app.radar import (create_radar, expand_queries, list_discoveries, list_runs,
                       list_run_events, match_rules, parse_natural_target,
                       parse_radar_text, run_radar)


def test_furniture_natural_language_schema():
    text = "First Series Model D80 Lounge Chair by Jean Prouvé for Tecta, 1980s"
    t = parse_natural_target(text)
    assert t["category"] == "FURNITURE"
    assert t["designer"] == "Jean Prouvé" and t["model"] == "D80"
    assert t["manufacturer"] == "Tecta" and t["object_type"] == "lounge chair"
    assert t["period"] == "1980s" and t["series"] == "First Series"


def test_takis_natural_language_schema():
    r = parse_radar_text("Takis pink double-head Signal under €20,000")
    t = r["target"]
    assert t["category"] == "ART" and t["artist"] == "Takis" and t["work"] == "Signal"
    assert t["color"] == "pink" and t["feature"] == "double-head"
    assert r["price_max"] == 20000 and r["currency"] == "EUR"


def test_classic_car_natural_language_schema():
    t = parse_natural_target("1992 Porsche 964 Carrera RS N-GT M003")
    assert t["category"] == "CLASSIC_CAR" and t["marque"] == "Porsche"
    assert "964 Carrera RS" in t["model"] and "N-GT" in t["variant"]
    assert t["chassis_code"] == "M003" and t["year"] == 1992


def test_furniture_tree_has_aliases_languages_markets_and_full_web():
    radar = create_radar("First Series Model D80 Lounge Chair by Jean Prouvé for Tecta, 1980s")
    q = expand_queries(radar)
    assert len(q) > 1 and any("Prouvé" in x for x in q) and any("Prouve" in x for x in q)
    assert any("site:1stdibs.com" in x for x in q) and any("site:pamono.com" in x for x in q)
    assert any("site:" not in x for x in q) and any("enchères" in x or "à vendre" in x for x in q)


def test_accent_insensitive_and_unknown_price_candidate():
    rules = parse_radar_text("Jean Prouvé lounge chair")
    record = {"title":"Jean Prouve lounge chair available", "year":None, "asking_price":None,
              "raw":{"snippet":"Mobilier vintage à vendre"}}
    assert match_rules(record, rules, True)["matched"]


def test_discovery_streams_after_third_query_before_run_finishes(monkeypatch):
    entered_fourth, release = threading.Event(), threading.Event()
    class Provider:
        name = "fixture"; status = "OK"
        def __init__(self): self.calls = 0
        def search(self, query, max_results=5):
            self.calls += 1
            if self.calls == 3:
                return [{"title":"Jean Prouve D80 Tecta lounge chair available", "snippet":"à vendre price on request",
                         "url":"https://dealer.test/d80"}]
            if self.calls == 4: entered_fourth.set(); release.wait(5)
            return []
    radar = create_radar("First Series Model D80 Lounge Chair by Jean Prouvé for Tecta, 1980s")
    monkeypatch.setattr("app.radar.expand_queries", lambda _: [f"query {i}" for i in range(50)])
    source = {"name":"Full web", "domain":"full-web.local", "compliance_reviewed":True,
              "compliance_mode":"SEARCH_ONLY", "health_status":"SEARCH_ONLY", "searchable_by_external_engine":True}
    thread = threading.Thread(target=run_radar, args=(radar["id"], Provider(), [source], False), daemon=True)
    thread.start(); assert entered_fourth.wait(5)
    rows = [row for row in list_discoveries() if row["radar_id"] == radar["id"]]
    assert thread.is_alive() and len(rows) == 1 and rows[0]["stream_sequence"] == 1
    assert list_runs(radar["id"])[0]["run_status"] == "RUNNING"
    release.set(); thread.join(10); assert not thread.is_alive()


def test_adaptive_query_is_counted_and_event_stream_is_ordered(monkeypatch):
    class Provider:
        name = "fixture"; status = "OK"
        def __init__(self): self.calls = 0
        def search(self, query, max_results=5):
            self.calls += 1
            if self.calls == 1:
                return [{"title":"Jean Prouve D80 Tecta lounge chair available",
                         "snippet":"price on request", "url":"https://newdealer.test/d80"}]
            return []
    radar = create_radar("Jean Prouvé D80 lounge chair by Jean Prouvé for Tecta")
    monkeypatch.setattr("app.radar.expand_queries", lambda _: ["initial query"])
    source = {"name":"Full web", "domain":"full-web.local", "compliance_reviewed":True,
              "compliance_mode":"SEARCH_ONLY", "health_status":"SEARCH_ONLY", "searchable_by_external_engine":True}
    run = run_radar(radar["id"], Provider(), [source], False)
    assert run["run_status"] == "COMPLETE"
    assert run["queries_planned"] == run["queries_completed"] == 2
    assert run["queries_added_dynamically"] == 1
    events = [event["event_type"] for event in list_run_events(run["id"])]
    assert events[0] == "RUN_STARTED" and "QUERY_STARTED" in events
    assert "RESULT_RECEIVED" in events and events[-1] == "RUN_COMPLETED"
