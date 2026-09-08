from fastapi.testclient import TestClient

from app.main import app
from app.radar import create_radar, delete_radar, get_radar, list_discoveries, list_radars, list_runs, run_radar, update_radar


class Provider:
    def search(self, query, max_results=5):
        return []


def approved_source():
    return {"name": "Fixture", "domain": "fixture.test", "compliance_reviewed": True,
            "compliance_mode": "SEARCH_ONLY", "searchable_by_external_engine": True}


def test_radar_edit():
    r = create_radar("Editable Radar")
    assert update_radar(r["id"], {"name": "Edited Radar"})["name"] == "Edited Radar"


def test_radar_pause():
    r = create_radar("Pause Lifecycle")
    assert update_radar(r["id"], {"enabled": False})["enabled"] is False


def test_radar_resume():
    r = create_radar("Resume Lifecycle"); update_radar(r["id"], {"enabled": False})
    assert update_radar(r["id"], {"enabled": True})["enabled"] is True


def test_radar_delete():
    r = create_radar("Delete Lifecycle")
    assert delete_radar(r["id"])
    assert all(x["id"] != r["id"] for x in list_radars())


def test_radar_delete_preserves_history():
    r = create_radar("Delete History")
    run_radar(r["id"], Provider(), [approved_source()], send_email=False)
    assert delete_radar(r["id"])
    assert list_runs(r["id"])


def test_similar_radar_warning():
    for radar in list_radars():
        update_radar(radar["id"], {"enabled": False})
    client = TestClient(app)
    payload = {"query_original": "P0 Uniquely Similar 1971 under EUR 12345"}
    assert client.post("/api/radars", json=payload).status_code == 200
    response = client.post("/api/radars", json=payload)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "SIMILAR_RADAR"
    assert client.post("/api/radars", json={**payload, "create_anyway": True}).status_code == 200
