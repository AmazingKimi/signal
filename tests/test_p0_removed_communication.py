from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_no_communication_send_route():
    assert client.post("/api/communication/send", json={}).status_code == 404


def test_no_communication_draft_route():
    assert client.post("/api/communication/draft", json={}).status_code == 404


def test_notification_email_is_user_only(tmp_path, monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, "COMM_CONFIG_PATH", str(tmp_path / "notification_email_config.json"))
    result = main.api_notification_email_config({"smtp_host": "smtp.example", "smtp_user": "user@example.com",
                                                  "smtp_password": "app-password"})
    assert result["connected"] is True
    assert "recipient" not in result and "seller" not in result
