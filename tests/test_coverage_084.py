from app.radar import create_radar, get_run, run_radar, source_registry
import pytest


@pytest.fixture(autouse=True)
def focused_coverage_query(monkeypatch):
    # These tests exercise coverage bookkeeping, independent of planner size.
    monkeypatch.setattr('app.radar.expand_queries',lambda r:['coverage fixture'])


class Provider:
    def __init__(self, fail=""):
        self.fail = fail
    def search(self, query, max_results=5):
        if self.fail and self.fail in query:
            raise TimeoutError("timeout")
        return []


def source(domain):
    return {"name": domain, "domain": domain, "compliance_reviewed": True,
            "compliance_mode": "SEARCH_ONLY", "health_status": "SEARCH_ONLY",
            "searchable_by_external_engine": True}


def run(sources, fail=""):
    radar = create_radar("Coverage 084 Fixture")
    return run_radar(radar["id"], Provider(fail), sources, send_email=False)


def test_coverage_registered_count():
    assert run([source("ok.test")])["registered_sources"] == len(source_registry()) == 126


def test_coverage_eligible_count():
    result = run([source("ok.test")])
    assert result["reviewed_eligible_sources"] == sum(1 for s in source_registry() if s["compliance_reviewed"] and s["compliance_mode"] in {"DIRECT_OK", "SEARCH_ONLY", "OFFICIAL_ALERT"} and s["health_status"] not in {"BROKEN", "PAUSED"})


def test_coverage_planned_count():
    assert run([source("a.test"), source("b.test")])["planned_sources"] == 2


def test_coverage_success_count():
    assert run([source("a.test"), source("b.test")])["successfully_checked_sources"] == 2


def test_success_not_greater_than_planned():
    result = run([source("ok.test")]); assert result["successfully_checked_sources"] <= result["planned_sources"]


def test_planned_not_greater_than_eligible():
    result = run([source("ok.test")]); assert result["planned_sources"] <= result["reviewed_eligible_sources"]


def test_complete_run_status():
    assert run([source("ok.test")])["run_status"] == "COMPLETE"


def test_partial_run_status():
    result = run([source("ok.test"), source("timeout.test")], "timeout.test")
    assert result["run_status"] == "PARTIAL" and result["failed_timeout"] == 1


def test_failed_run_status():
    assert run([source("timeout.test")], "timeout.test")["run_status"] == "FAILED"


def test_search_only_zero_results_counts_as_success():
    assert run([source("empty.test")])["successfully_checked_sources"] == 1


def test_timeout_not_success():
    result = run([source("timeout.test")], "timeout.test")
    assert result["successfully_checked_sources"] == 0


def test_login_required_not_eligible():
    item = source("login.test"); item["compliance_mode"] = "LOGIN_REQUIRED"
    result = run([item]); assert result["planned_sources"] == 0 and result["skipped_login_required"] >= 1


def test_do_not_automate_not_eligible():
    item = source("manual.test"); item["compliance_mode"] = "DO_NOT_AUTOMATE"
    result = run([item]); assert result["planned_sources"] == 0 and result["skipped_do_not_automate"] >= 1


def test_unreviewed_not_eligible():
    item = source("new.test"); item["compliance_reviewed"] = False
    result = run([item]); assert result["planned_sources"] == 0 and result["skipped_unreviewed"] == 1


def test_coverage_snapshot_persisted():
    result = run([source("ok.test")])
    snapshot = result["coverage_snapshot"]
    assert snapshot["queries_planned"] == 1 and snapshot["queries_completed"] == 1
    assert snapshot["results_processed"] == 0 and snapshot["target_scope"] == 126
    assert snapshot["timestamp"] == result["finished_at"]


def test_historical_coverage_not_recomputed():
    result = run([source("ok.test")]); before = dict(result["coverage_snapshot"])
    assert get_run(result["id"])["coverage_snapshot"] == before
