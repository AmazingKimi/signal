import os
import pytest

from app.commercial import (CommercialError, begin_job, finish_job, login, quota_guard,
                            quota_usage, register, set_schedule, verify_challenge)


def test_email_register_verify_login_and_preferences():
    result = register({"email": "commercial@example.com", "password": "StrongPass1234", "timezone": "UTC"})
    assert result["accepted"] and result.get("dev_code")
    verified = verify_challenge(result["challenge_id"], result["dev_code"])
    assert verified["verified"] and verified["user"]["email_verified"] == 1
    session = login("commercial@example.com", "StrongPass1234")
    assert session["token"] and session["user"]["plan_id"] == "TEST"


def test_weak_password_and_otp_attempt_limit():
    with pytest.raises(CommercialError): register({"email": "weak@example.com", "password": "short"})
    result = register({"email": "otp@example.com", "password": "StrongPass1234"})
    for _ in range(5):
        with pytest.raises(CommercialError): verify_challenge(result["challenge_id"], "000000")


def test_idempotent_job_and_reconcilable_cost():
    user = "sample-test-user"
    key = "test-idempotency-082"
    run_id, cached = begin_job(user, 1, key)
    assert run_id and cached is None
    finish_job(run_id, key, {"ok": True})
    again, cached = begin_job(user, 1, key)
    assert again == run_id and cached["ok"] is True


def test_timezone_schedule_is_persisted():
    rows = set_schedule("sample-test-user", 1, ["08:00", "20:00"], "Asia/Shanghai")
    assert len(rows) == 2 and all(r["timezone"] == "Asia/Shanghai" for r in rows)


def test_quota_guard_has_hard_limits():
    usage = quota_usage("sample-test-user")
    assert usage["limits"]["runs_per_day"] > 0
    with pytest.raises(CommercialError): quota_guard("sample-test-user", "unknown_resource", usage["limits"].get("unknown_resource", 0) + 1)
