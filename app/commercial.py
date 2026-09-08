"""0.8.2 commercial guardrails: identity, quotas, schedules and real run costs.

The module deliberately uses only the Python standard library.  Secrets are
hashed before persistence and all public errors are stable product messages.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .models import now_iso
from .store import _conn

OTP_TTL_SECONDS = 600
OTP_ATTEMPTS = 5
SESSION_DAYS = 30
DEFAULT_PLAN = "TEST"
SAFE_TEXT = re.compile(r"^[^\x00-\x08\x0b\x0c\x0e-\x1f]{1,500}$")

PLAN_LIMITS = {
    "TEST": {"active_radars": 3, "runs_per_day": 8, "runs_per_month": 80,
             "sources_per_run": 20, "search_requests_per_run": 45,
             "pages_processed_per_run": 80, "llm_input_tokens": 12000,
             "llm_output_tokens": 3000, "notifications_per_day": 15,
             "schedules_per_radar": 2},
    "PAUSED": {"active_radars": 0, "runs_per_day": 0, "runs_per_month": 0,
               "sources_per_run": 0, "search_requests_per_run": 0,
               "pages_processed_per_run": 0, "llm_input_tokens": 0,
               "llm_output_tokens": 0, "notifications_per_day": 0,
               "schedules_per_radar": 0},
}

PROVIDER_PRICING = {
    "tavily": {"search_request": 0.008, "currency": "USD"},
    "brave": {"search_request": 0.005, "currency": "USD"},
    "email": {"message": 0.0001, "currency": "USD"},
    "desktop": {"message": 0.0, "currency": "USD"},
}


class CommercialError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message); self.code, self.message, self.status = code, message, status


def init_commercial_tables() -> None:
    conn = _conn()
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
          user_id TEXT PRIMARY KEY, email TEXT UNIQUE, email_verified INTEGER DEFAULT 0,
          phone_country_code TEXT, phone TEXT UNIQUE, phone_verified INTEGER DEFAULT 0,
          password_hash TEXT NOT NULL, locale TEXT DEFAULT 'zh-CN', theme TEXT DEFAULT 'system',
          timezone TEXT DEFAULT 'Asia/Shanghai', plan_id TEXT DEFAULT 'TEST',
          subscription_status TEXT DEFAULT 'ACTIVE', created_at TEXT NOT NULL, last_login_at TEXT
        );
        CREATE TABLE IF NOT EXISTS verification_challenges (
          id TEXT PRIMARY KEY, user_id TEXT NOT NULL, channel TEXT NOT NULL, target_masked TEXT NOT NULL,
          purpose TEXT NOT NULL, secret_hash TEXT NOT NULL, expires_at TEXT NOT NULL,
          attempts INTEGER DEFAULT 0, consumed_at TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
          id TEXT PRIMARY KEY, user_id TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
          expires_at TEXT NOT NULL, revoked_at TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rate_limits (
          bucket TEXT NOT NULL, window_start INTEGER NOT NULL, hits INTEGER DEFAULT 0,
          PRIMARY KEY(bucket,window_start)
        );
        CREATE TABLE IF NOT EXISTS job_idempotency (
          idempotency_key TEXT PRIMARY KEY, user_id TEXT NOT NULL, radar_id INTEGER,
          status TEXT DEFAULT 'RUNNING', run_id TEXT, result_json TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS cost_runs (
          run_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, radar_id INTEGER, status TEXT DEFAULT 'RUNNING',
          search_provider TEXT DEFAULT '', search_provider_cost REAL DEFAULT 0,
          search_requests INTEGER DEFAULT 0, pages_fetched INTEGER DEFAULT 0,
          pages_processed INTEGER DEFAULT 0, llm_provider TEXT DEFAULT '', llm_model TEXT DEFAULT '',
          input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0, llm_cost REAL DEFAULT 0,
          email_cost REAL DEFAULT 0, sms_cost REAL DEFAULT 0, other_external_cost REAL DEFAULT 0,
          total_cost REAL DEFAULT 0, currency TEXT DEFAULT 'USD', started_at TEXT NOT NULL,
          finished_at TEXT, error_code TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS radar_schedules (
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, radar_id INTEGER NOT NULL,
          local_time TEXT NOT NULL, timezone TEXT NOT NULL, enabled INTEGER DEFAULT 1,
          next_run_at TEXT, last_trigger_key TEXT, UNIQUE(user_id,radar_id,local_time)
        );
        CREATE TABLE IF NOT EXISTS notification_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, discovery_id INTEGER,
          event_type TEXT NOT NULL, channel TEXT NOT NULL, dedupe_key TEXT NOT NULL UNIQUE,
          status TEXT NOT NULL, cost REAL DEFAULT 0, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS provider_circuits (
          provider TEXT PRIMARY KEY, state TEXT DEFAULT 'CLOSED', failure_count INTEGER DEFAULT 0,
          opened_at TEXT, reason TEXT DEFAULT ''
        );
        """)
        # Existing 0.8.1 databases are migrated in place; legacy rows belong to
        # the local sample account until an authenticated owner is assigned.
        for table in ("radars", "radar_runs", "discoveries"):
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                continue
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            if "user_id" not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id TEXT")
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='radars'").fetchone():
            conn.execute("UPDATE radars SET user_id=COALESCE(user_id,'sample-test-user')")
        conn.commit()
    finally: conn.close()


def _hash_secret(value: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", value.encode(), salt, 210_000)
    return f"pbkdf2_sha256$210000${salt.hex()}${digest.hex()}"


def _verify_secret(value: str, encoded: str) -> bool:
    try:
        _, rounds, salt_hex, expected = encoded.split("$", 3)
        got = hashlib.pbkdf2_hmac("sha256", value.encode(), bytes.fromhex(salt_hex), int(rounds)).hex()
        return hmac.compare_digest(got, expected)
    except Exception: return False


def _row(sql: str, args=()):
    conn = _conn()
    try:
        cur = conn.execute(sql, args); names = [d[0] for d in cur.description]
        item = cur.fetchone(); return dict(zip(names, item)) if item else None
    finally: conn.close()


def _rows(sql: str, args=()):
    conn = _conn()
    try:
        cur = conn.execute(sql, args); names = [d[0] for d in cur.description]
        return [dict(zip(names, x)) for x in cur.fetchall()]
    finally: conn.close()


def public_user(row: dict) -> dict:
    return {k: row.get(k) for k in ("user_id","email","email_verified","phone_country_code","phone",
            "phone_verified","locale","theme","timezone","plan_id","subscription_status","created_at","last_login_at")}


def validate_text(value: str, field: str, max_length=500) -> str:
    value = (value or "").strip()
    if not value or len(value) > max_length or not SAFE_TEXT.match(value):
        raise CommercialError("INVALID_INPUT", f"Invalid {field}")
    return value


def rate_limit(bucket: str, limit: int, window_seconds: int) -> None:
    now = int(time.time()); start = now - now % window_seconds
    conn = _conn()
    try:
        row = conn.execute("SELECT hits FROM rate_limits WHERE bucket=? AND window_start=?", (bucket,start)).fetchone()
        if row and row[0] >= limit: raise CommercialError("RATE_LIMITED", "Too many attempts. Try again later.", 429)
        conn.execute("INSERT INTO rate_limits(bucket,window_start,hits) VALUES(?,?,1) ON CONFLICT(bucket,window_start) DO UPDATE SET hits=hits+1", (bucket,start)); conn.commit()
    finally: conn.close()


def register(payload: dict, ip: str = "local") -> dict:
    init_commercial_tables(); rate_limit(f"register:{ip}", 8, 3600)
    email = str(payload.get("email") or "").strip().lower() or None
    phone = re.sub(r"\D", "", str(payload.get("phone") or "")) or None
    country = str(payload.get("phone_country_code") or "+86").strip()
    password = str(payload.get("password") or "")
    if not email and not phone: raise CommercialError("IDENTITY_REQUIRED", "Email or phone is required")
    if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email): raise CommercialError("INVALID_EMAIL", "Invalid email")
    if phone and not (6 <= len(phone) <= 15): raise CommercialError("INVALID_PHONE", "Invalid phone")
    if len(password) < 10 or not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise CommercialError("WEAK_PASSWORD", "Password must contain letters and numbers and be at least 10 characters")
    if _row("SELECT user_id FROM users WHERE email=? OR (phone=? AND phone IS NOT NULL)", (email,phone)):
        return {"accepted": True, "message": "If this account can be registered, a verification message will be sent."}
    uid = str(uuid.uuid4()); now = now_iso()
    conn = _conn()
    try:
        conn.execute("""INSERT INTO users(user_id,email,phone_country_code,phone,password_hash,locale,theme,timezone,plan_id,subscription_status,created_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (uid,email,country if phone else None,phone,_hash_secret(password),
          payload.get("locale") if payload.get("locale") in {"zh-CN","en-US"} else "zh-CN",
          payload.get("theme") if payload.get("theme") in {"system","light","dark"} else "system",
          payload.get("timezone") or "Asia/Shanghai",DEFAULT_PLAN,"PENDING_VERIFICATION",now)); conn.commit()
    finally: conn.close()
    challenge = issue_verification(uid, "email" if email else "phone", "REGISTER", ip)
    return {"accepted": True, "user_id": uid, **challenge}


def issue_verification(user_id: str, channel: str, purpose: str, ip="local") -> dict:
    user = _row("SELECT * FROM users WHERE user_id=?", (user_id,))
    if not user: raise CommercialError("ACCOUNT_NOT_FOUND", "Unable to send verification")
    target = user.get("email") if channel == "email" else user.get("phone")
    rate_limit(f"otp:{ip}:{user_id}:{channel}", 5, 3600)
    code = f"{secrets.randbelow(1_000_000):06d}"; cid = str(uuid.uuid4())
    expires = (datetime.now(timezone.utc)+timedelta(seconds=OTP_TTL_SECONDS)).isoformat(timespec="seconds")
    masked = (target[:2] + "***" + target[-2:]) if target else "***"
    conn = _conn()
    try:
        conn.execute("INSERT INTO verification_challenges VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (cid,user_id,channel,masked,purpose,_hash_secret(code),expires,0,None,now_iso())); conn.commit()
    finally: conn.close()
    # Local developer delivery: code is returned only when explicitly enabled.
    out = {"challenge_id":cid,"channel":channel,"target_masked":masked,"expires_in":OTP_TTL_SECONDS}
    if os.getenv("AK_DEV_OTP", "1") == "1": out["dev_code"] = code
    return out


def verify_challenge(challenge_id: str, code: str) -> dict:
    row = _row("SELECT * FROM verification_challenges WHERE id=?", (challenge_id,))
    if not row or row["consumed_at"]: raise CommercialError("INVALID_CODE", "Invalid or expired verification code")
    if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc): raise CommercialError("INVALID_CODE", "Invalid or expired verification code")
    if row["attempts"] >= OTP_ATTEMPTS: raise CommercialError("CODE_LOCKED", "Verification temporarily locked", 429)
    conn = _conn()
    try:
        conn.execute("UPDATE verification_challenges SET attempts=attempts+1 WHERE id=?", (challenge_id,)); conn.commit()
    finally: conn.close()
    if not _verify_secret(str(code), row["secret_hash"]): raise CommercialError("INVALID_CODE", "Invalid or expired verification code")
    field = "email_verified" if row["channel"] == "email" else "phone_verified"
    conn = _conn()
    try:
        conn.execute("UPDATE verification_challenges SET consumed_at=? WHERE id=?", (now_iso(),challenge_id))
        conn.execute(f"UPDATE users SET {field}=1,subscription_status='ACTIVE' WHERE user_id=?", (row["user_id"],)); conn.commit()
    finally: conn.close()
    return {"verified":True,"user":public_user(_row("SELECT * FROM users WHERE user_id=?", (row["user_id"],)))}


def login(identity: str, password: str, ip="local") -> dict:
    init_commercial_tables(); identity = identity.strip().lower(); rate_limit(f"login:{ip}:{identity}", 10, 900)
    phone = re.sub(r"\D", "", identity)
    user = _row("SELECT * FROM users WHERE lower(email)=? OR phone=?", (identity,phone))
    if not user or not _verify_secret(password, user["password_hash"]): raise CommercialError("LOGIN_FAILED", "Email/phone or password is incorrect", 401)
    if not (user["email_verified"] or user["phone_verified"]): raise CommercialError("VERIFICATION_REQUIRED", "Verify your account before signing in", 403)
    raw = secrets.token_urlsafe(40); sid = str(uuid.uuid4())
    conn = _conn()
    try:
        conn.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?)", (sid,user["user_id"],hashlib.sha256(raw.encode()).hexdigest(),
          (datetime.now(timezone.utc)+timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds"),None,now_iso()))
        conn.execute("UPDATE users SET last_login_at=? WHERE user_id=?", (now_iso(),user["user_id"])); conn.commit()
    finally: conn.close()
    return {"token":raw,"expires_in":SESSION_DAYS*86400,"user":public_user(user)}


def authenticate(token: str | None) -> dict | None:
    if not token: return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    row = _row("""SELECT u.* FROM sessions s JOIN users u ON u.user_id=s.user_id
      WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at>?""", (digest,now_iso()))
    return public_user(row) if row else None


def logout(token: str) -> None:
    conn = _conn()
    try: conn.execute("UPDATE sessions SET revoked_at=? WHERE token_hash=?", (now_iso(),hashlib.sha256(token.encode()).hexdigest())); conn.commit()
    finally: conn.close()


def update_preferences(user_id: str, payload: dict) -> dict:
    fields, values = [], []
    for key, allowed in (("locale",{"zh-CN","en-US"}),("theme",{"system","light","dark"})):
        if payload.get(key) in allowed: fields.append(f"{key}=?"); values.append(payload[key])
    if payload.get("timezone"):
        try: ZoneInfo(payload["timezone"])
        except Exception: raise CommercialError("INVALID_TIMEZONE", "Invalid timezone")
        fields.append("timezone=?"); values.append(payload["timezone"])
    if fields:
        conn = _conn()
        try: conn.execute(f"UPDATE users SET {','.join(fields)} WHERE user_id=?", values+[user_id]); conn.commit()
        finally: conn.close()
    return public_user(_row("SELECT * FROM users WHERE user_id=?", (user_id,)))


def quota_usage(user_id: str) -> dict:
    user = _row("SELECT * FROM users WHERE user_id=?", (user_id,)); plan = PLAN_LIMITS.get(user["plan_id"], PLAN_LIMITS[DEFAULT_PLAN])
    today = datetime.now(timezone.utc).date().isoformat(); month = today[:7]
    day = _row("SELECT count(*) n FROM cost_runs WHERE user_id=? AND started_at LIKE ?", (user_id,today+"%"))["n"]
    monthly = _row("SELECT count(*) n FROM cost_runs WHERE user_id=? AND started_at LIKE ?", (user_id,month+"%"))["n"]
    radar_row = _row("SELECT count(*) n FROM radars WHERE enabled=1 AND COALESCE(user_id,?)=?", (user_id,user_id)) if _row("SELECT 1 FROM sqlite_master WHERE type='table' AND name='radars'") else {"n":0}
    radars = radar_row["n"]
    cost = _row("SELECT COALESCE(sum(total_cost),0) n FROM cost_runs WHERE user_id=? AND started_at LIKE ?", (user_id,month+"%"))["n"]
    return {"plan_id":user["plan_id"],"limits":plan,"today_runs":day,"monthly_runs":monthly,"active_radars":radars,
            "monthly_cost":round(cost,6),"currency":"USD","next_reset":(datetime.now(timezone.utc).replace(day=1)+timedelta(days=32)).replace(day=1).date().isoformat()}


def quota_guard(user_id: str, resource: str, requested=1) -> dict:
    usage = quota_usage(user_id); limits = usage["limits"]
    checks = {"run_day":("runs_per_day",usage["today_runs"]), "run_month":("runs_per_month",usage["monthly_runs"]),
              "active_radars":("active_radars",usage["active_radars"])}
    if resource in checks:
        limit_key, used = checks[resource]; limit = limits[limit_key]
        if used + requested > limit: raise CommercialError("QUOTA_EXCEEDED", "Plan quota reached. Review Plan & Usage.", 429)
    elif requested > limits.get(resource, 0): raise CommercialError("QUOTA_EXCEEDED", "Plan quota reached. Review Plan & Usage.", 429)
    return usage


def begin_job(user_id: str, radar_id: int, idempotency_key: str) -> tuple[str, dict | None]:
    init_commercial_tables(); idempotency_key = validate_text(idempotency_key, "idempotency key", 120)
    existing = _row("SELECT * FROM job_idempotency WHERE idempotency_key=?", (idempotency_key,))
    if existing:
        return existing.get("run_id") or "", json.loads(existing["result_json"]) if existing.get("result_json") else {"status":existing["status"],"run_id":existing.get("run_id")}
    quota_guard(user_id,"run_day"); quota_guard(user_id,"run_month")
    run_id = str(uuid.uuid4()); conn = _conn()
    try:
        conn.execute("INSERT INTO job_idempotency VALUES(?,?,?,?,?,?,?)", (idempotency_key,user_id,radar_id,"RUNNING",run_id,None,now_iso()))
        conn.execute("INSERT INTO cost_runs(run_id,user_id,radar_id,started_at) VALUES(?,?,?,?)", (run_id,user_id,radar_id,now_iso())); conn.commit()
    finally: conn.close()
    return run_id, None


def meter_add(run_id: str, **metrics) -> None:
    allowed = {"search_requests","pages_fetched","pages_processed","input_tokens","output_tokens",
               "search_provider_cost","llm_cost","email_cost","sms_cost","other_external_cost"}
    numeric = {k:v for k,v in metrics.items() if k in allowed and isinstance(v,(int,float))}
    named = {k:v for k,v in metrics.items() if k in {"search_provider","llm_provider","llm_model"}}
    parts = [f"{k}={k}+?" for k in numeric] + [f"{k}=?" for k in named]
    if not parts: return
    conn = _conn()
    try: conn.execute(f"UPDATE cost_runs SET {','.join(parts)} WHERE run_id=?", list(numeric.values())+list(named.values())+[run_id]); conn.commit()
    finally: conn.close()


def finish_job(run_id: str, idempotency_key: str, result: dict | None, error_code="") -> dict:
    conn = _conn()
    try:
        conn.execute("""UPDATE cost_runs SET status=?,finished_at=?,error_code=?,
          total_cost=ROUND(search_provider_cost+llm_cost+email_cost+sms_cost+other_external_cost,8) WHERE run_id=?""",
          ("FAILED" if error_code else "COMPLETED",now_iso(),error_code,run_id))
        conn.execute("UPDATE job_idempotency SET status=?,result_json=? WHERE idempotency_key=?",
          ("FAILED" if error_code else "COMPLETED",json.dumps(result,ensure_ascii=False) if result else None,idempotency_key)); conn.commit()
    finally: conn.close()
    return _row("SELECT * FROM cost_runs WHERE run_id=?", (run_id,))


def cost_run(run_id: str) -> dict | None: return _row("SELECT * FROM cost_runs WHERE run_id=?", (run_id,))


def set_schedule(user_id: str, radar_id: int, times: list[str], timezone_name: str) -> list[dict]:
    try: zone = ZoneInfo(timezone_name)
    except Exception: raise CommercialError("INVALID_TIMEZONE", "Invalid timezone")
    limits = quota_usage(user_id)["limits"]
    times = list(dict.fromkeys(times))
    if len(times) > limits["schedules_per_radar"]: raise CommercialError("QUOTA_EXCEEDED", "Schedule quota reached", 429)
    parsed=[]
    for value in times:
        if not re.match(r"^(?:[01]\d|2[0-3]):[0-5]\d$", value): raise CommercialError("INVALID_TIME", "Use HH:MM")
        hour,minute=map(int,value.split(":")); local=datetime.now(zone).replace(hour=hour,minute=minute,second=0,microsecond=0)
        if local <= datetime.now(zone): local += timedelta(days=1)
        parsed.append((value,local.astimezone(timezone.utc).isoformat(timespec="seconds")))
    conn=_conn()
    try:
        conn.execute("DELETE FROM radar_schedules WHERE user_id=? AND radar_id=?",(user_id,radar_id))
        for value,next_at in parsed: conn.execute("INSERT INTO radar_schedules(user_id,radar_id,local_time,timezone,next_run_at) VALUES(?,?,?,?,?)",(user_id,radar_id,value,timezone_name,next_at))
        conn.commit()
    finally: conn.close()
    return _rows("SELECT * FROM radar_schedules WHERE user_id=? AND radar_id=? ORDER BY local_time",(user_id,radar_id))


def seed_test_account() -> dict:
    init_commercial_tables(); email=os.getenv("AK_SAMPLE_EMAIL","demo@amazing-kimi.local")
    existing=_row("SELECT * FROM users WHERE email=?",(email,))
    if existing: return public_user(existing)
    uid="sample-test-user"; conn=_conn()
    try:
        conn.execute("""INSERT INTO users(user_id,email,email_verified,password_hash,locale,theme,timezone,plan_id,subscription_status,created_at)
          VALUES(?,?,1,?,?,?,?,?,?,?)""",(uid,email,_hash_secret(os.getenv("AK_SAMPLE_PASSWORD","AmazingKimi2026")),"zh-CN","system","Asia/Shanghai","TEST","ACTIVE",now_iso())); conn.commit()
    finally: conn.close()
    return public_user(_row("SELECT * FROM users WHERE user_id=?",(uid,)))


init_commercial_tables()
seed_test_account()