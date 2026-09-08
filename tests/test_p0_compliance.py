from app.radar import (
    GenericSearchAdapter,
    adapter_for,
    create_radar,
    run_radar,
    source_registry,
)


class Provider:
    def search(self, query, max_results=5):
        return []


def test_all_sources_have_compliance_fields():
    required = {"compliance_reviewed", "compliance_mode", "compliance_checked_at",
                "compliance_evidence_url", "compliance_note", "tos_checked", "robots_checked",
                "health_status", "last_check", "last_success", "success_rate"}
    rows = source_registry()
    assert len(rows) == 126
    assert all(required <= set(row) for row in rows)
    assert all(row["compliance_reviewed"] and row["compliance_evidence_url"] and row["compliance_note"] for row in rows)


def test_unreviewed_source_never_direct_runs():
    source = {"domain": "unreviewed.test", "compliance_reviewed": False,
              "compliance_mode": "DIRECT_OK", "robots_checked": True, "tos_checked": True}
    radar = create_radar("Unreviewed Source")
    result = run_radar(radar["id"], Provider(), [source], send_email=False)
    assert result["planned_sources"] == 0


def test_search_only_never_direct_scrapes():
    source = {"domain": "search.test", "compliance_reviewed": True, "compliance_mode": "SEARCH_ONLY"}
    assert isinstance(adapter_for(source, Provider()), GenericSearchAdapter)


def test_login_required_skipped():
    radar = create_radar("Login Source")
    source = {"domain": "login.test", "compliance_reviewed": True, "compliance_mode": "LOGIN_REQUIRED"}
    assert run_radar(radar["id"], Provider(), [source], send_email=False)["planned_sources"] == 0


def test_do_not_automate_skipped():
    radar = create_radar("Blocked Source")
    source = {"domain": "blocked.test", "compliance_reviewed": True, "compliance_mode": "DO_NOT_AUTOMATE"}
    assert run_radar(radar["id"], Provider(), [source], send_email=False)["planned_sources"] == 0
