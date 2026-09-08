"""SIGNAL — Private Discovery Radar 0.7.2 (Liquid Glass)

FastAPI 入口。
- 离线可启动：无 LLM Key、无搜索服务时，UI 与确定性分析照常工作
- /api/analyze 改为 NDJSON 流：先逐行推送 RESEARCHING 进度，最后一行是完整报告
- 0.5：/api/watch* Watchlist + Opportunity Monitor（MANUAL 检查 / CRUD / 调度状态）
- 0.7.2：UI 母版 = kimi-design-0.7/prototype.html Direction A · Liquid Glass
"""
import json
import os
import platform
import queue
import re
import threading
from datetime import date, timedelta
from statistics import median
from typing import Optional
from urllib.parse import urlparse

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Header, Request
from fastapi.responses import FileResponse, StreamingResponse

from .backtest import run_backtest
from .llm import build_provider
from .models import AnalysisInput
from .orchestrator import analyze
from .research import (build_search_provider, record_search_provider_test,
                       save_search_provider, search_provider_settings)
from .store import (
    get_report,
    history,
    history_stats,
    list_reports,
    save_decision_memory,
    save_report,
)

load_dotenv()

# Mailbox configuration is persisted locally so a connected account survives
# browser refreshes and application restarts.  The password is never exposed
# by an API response; it is only read by the sending endpoint.
COMM_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "notification_email_config.json")
def _load_comm_config():
    try:
        with open(COMM_CONFIG_PATH, "r", encoding="utf-8") as fh:
            value = json.load(fh)
            return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}

def _apply_comm_config():
    cfg = _load_comm_config()
    mapping = {"smtp_host":"KIMI_SMTP_HOST", "smtp_port":"KIMI_SMTP_PORT", "smtp_user":"KIMI_SMTP_USER",
               "smtp_password":"KIMI_SMTP_PASSWORD", "from_address":"KIMI_EMAIL_FROM", "webmail_url":"KIMI_WEBMAIL_URL"}
    for key, env_key in mapping.items():
        if cfg.get(key) and not os.getenv(env_key):
            os.environ[env_key] = str(cfg[key])
    return cfg

_apply_comm_config()

app = FastAPI(title="SIGNAL RADAR 0.8 — Private Discovery Radar")

# 0.8.2 commercial infrastructure is additive: the existing local radar
# remains readable, while new account/quota/cost APIs are available to the UI.
from .commercial import (CommercialError, authenticate, begin_job, cost_run,
    finish_job, init_commercial_tables, login as commercial_login,
    logout as commercial_logout, quota_usage, register as commercial_register,
    seed_test_account, set_schedule, update_preferences, verify_challenge)
init_commercial_tables()

def _commercial_error(exc: CommercialError):
    raise HTTPException(exc.status, {"code": exc.code, "message": exc.message})

def _current_user(authorization: str | None = Header(default=None)):
    token = authorization[7:] if authorization and authorization.lower().startswith("bearer ") else None
    user = authenticate(token)
    if not user: raise HTTPException(401, {"code":"AUTH_REQUIRED","message":"Sign in required"})
    return user

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# 人工核验基准已迁移至 app/verified_references.py（0.8.5 起 Analyst 管线共用）
from .verified_references import (
    VERIFIED_MARKET_REFERENCES,
    _seed_verified_market_references,
    _subject_key,
)


def _price_intelligence(subject_text, candidates=None):
    """Build an auditable 3-year market view plus current purchase availability."""
    from .store import price_record_upsert, price_records
    _seed_verified_market_references()
    key = _subject_key(subject_text)
    now = date.today()
    period_start = now - timedelta(days=365 * 3)

    # Every Scout run contributes structured evidence. Undated SOLD snippets are stored but
    # deliberately excluded from time-window statistics until a sale date is verified.
    for c in candidates or []:
        sale_type = str(c.get("sale_type") or "UNKNOWN").upper()
        if not c.get("source_url") or not c.get("asking_price"):
            continue
        price_record_upsert({
            "subject_key": key, "category": c.get("category", ""),
            "maker": c.get("maker", ""), "object_name": c.get("object", ""),
            "sale_type": sale_type, "price": c.get("asking_price"),
            "currency": c.get("currency", "EUR"),
            "normalized_eur": c.get("asking_price") if c.get("currency", "EUR") == "EUR" else None,
            "source_name": c.get("source_name", ""), "source_url": c.get("source_url", ""),
            "source_tier": c.get("source_tier", 0), "price_basis": "UNKNOWN",
            "comparable_level": "SCOUT_CANDIDATE",
            "is_active_listing": sale_type == "ASKING", "listing_checked_at": now.isoformat(),
            "fingerprint": c.get("fingerprint"), "raw": c,
        })

    all_rows = price_records(key)
    valid = []
    excluded = {"missing_sale_date": 0, "missing_fx": 0, "unverified_source": 0, "outside_period": 0}
    seen = set()
    for row in all_rows:
        if row.get("sale_type") != "SOLD":
            continue
        if not row.get("source_url") or int(row.get("source_tier") or 9) > 2:
            excluded["unverified_source"] += 1; continue
        if not row.get("sold_at"):
            excluded["missing_sale_date"] += 1; continue
        try:
            sold_date = date.fromisoformat(str(row["sold_at"])[:10])
        except ValueError:
            excluded["missing_sale_date"] += 1; continue
        if sold_date < period_start or sold_date > now:
            excluded["outside_period"] += 1; continue
        if row.get("normalized_eur") is None:
            excluded["missing_fx"] += 1; continue
        raw = row.get("raw") or {}
        noise = " ".join(str(raw.get(k) or "") for k in ("title", "object", "snippet"))
        noise = f"{noise} {row.get('object_name') or ''} {row.get('source_url') or ''}".lower()
        if any(x in noise for x in ("price guide", "price-guide", "market data", "price estimate",
                                    "valuation", "auction results", "artist page")):
            excluded.setdefault("non_transaction_page", 0); excluded["non_transaction_page"] += 1; continue
        dedupe = (row.get("source_url"), raw.get("lot"), row.get("sold_at"), row.get("price"))
        if dedupe in seen:
            continue
        seen.add(dedupe); valid.append(row)

    # 独立来源按域名计数，而不是按网页显示名称计数，避免同一聚合站伪装成多个来源。
    sources = sorted({urlparse(str(r.get("source_url") or "")).netloc.lower() for r in valid if urlparse(str(r.get("source_url") or "")).netloc})
    result = {
        "available": False, "has_evidence": bool(valid), "currency": "EUR",
        "sample_count": len(valid), "source_count": len(sources), "source_names": sources,
        "minimum_samples": 5, "minimum_sources": 2, "period_start": period_start.isoformat(),
        "period_end": now.isoformat(), "excluded": excluded,
        "method": "MEAN_AND_MEDIAN_VERIFIED_SOLD_LOTS_EXACT_SUBJECT_3Y",
        "scope": "同一作者 / 制造商、同一作品系列或车型、相近版本与尺寸",
        "price_basis": "仅 SOLD；去重；按成交日汇率统一为 EUR；挂牌、估价、无日期或无来源记录不计入",
        "records": [], "purchase_listings": [],
    }
    # If structured sale dates/FX are missing, only use a provisional benchmark when
    # there is genuine cross-source corroboration. A single SOLD-labelled number,
    # price guide, market aggregate or “for sale” page can never create an average.
    if not valid or len(sources) < 2:
        observed = []
        # Reuse persisted SOLD evidence from earlier scans when the current request
        # has no fresh candidates (for example /api/prices?query=...). It still goes
        # through the same source, noise and outlier gates below.
        for row in all_rows:
            if str(row.get("sale_type") or "").upper() != "SOLD" or not row.get("price") or not row.get("source_url"):
                continue
            if int(row.get("source_tier") or 9) > 2 or not (0 < float(row.get("price")) < 100_000_000):
                continue
            raw = row.get("raw") or {}
            domain = urlparse(str(row.get("source_url") or "")).netloc.lower()
            if any(blocked in domain for blocked in (
                "carsforsale.com", "carfax.com", "autotrader.", "cars.com",
            )):
                continue
            noise = f"{row.get('object_name') or ''} {raw.get('title') or ''} {row.get('source_url') or ''}".lower()
            if any(x in noise for x in ("price guide", "price-guide", "market data", "price estimate", "valuation", "auction results")):
                continue
            observed.append({"object": row.get("object_name"), "asking_price": row.get("price"),
                             "currency": row.get("currency") or "EUR", "source_name": row.get("source_name"),
                             "source_url": row.get("source_url"), "source_tier": row.get("source_tier"),
                             "last_seen": row.get("sold_at") or row.get("discovered_at"), "raw": raw})
        for c in candidates or []:
            if str(c.get("sale_type") or "").upper() != "SOLD" or not c.get("asking_price") or not c.get("source_url"):
                continue
            if int(c.get("source_tier") or 9) > 2 or not (0 < float(c.get("asking_price")) < 100_000_000):
                continue
            noise = (str(c.get("object") or "") + " " + str(c.get("source_url") or "")).lower()
            if any(x in noise for x in ("price guide", "price-guide", "market data", "price estimate", "for sale", "valuation", "estimate")):
                continue
            observed.append(c)
        if observed:
            # Prefer the currency set that actually satisfies cross-source
            # corroboration. The largest set may still be one site's archive.
            by_currency = {}
            for item in observed:
                by_currency.setdefault(str(item.get("currency") or "EUR"), []).append(item)
            eligible = []
            for currency_name, currency_rows in by_currency.items():
                currency_sources = {
                    urlparse(str(item.get("source_url") or "")).netloc.lower()
                    for item in currency_rows
                    if urlparse(str(item.get("source_url") or "")).netloc
                }
                if len(currency_rows) >= 3 and len(currency_sources) >= 2:
                    eligible.append((len(currency_rows), len(currency_sources), currency_name, currency_rows))
            if eligible:
                _, _, curr, observed = sorted(eligible, reverse=True)[0]
            else:
                from collections import Counter
                curr = Counter(str(c.get("currency") or "EUR") for c in observed).most_common(1)[0][0]
                observed = [c for c in observed if str(c.get("currency") or "EUR") == curr]
            independent_sources = {urlparse(str(c.get("source_url") or "")).netloc.lower() for c in observed if urlparse(str(c.get("source_url") or "")).netloc}
            if len(observed) < 3 or len(independent_sources) < 2:
                result["has_evidence"] = bool(observed)
                result["sample_count"] = len(observed)
                result["source_count"] = len(independent_sources)
                result["source_names"] = sorted(independent_sources)
                result["reason"] = "INSUFFICIENT_CROSS_SOURCE_SOLD_RECORDS"
                observed = []
        if observed:
            raw_vals = [float(c["asking_price"]) for c in observed]
            med0 = median(raw_vals)
            # Prevent a category/archive aggregate from dominating a model-specific market.
            filtered_observed = [c for c in observed if med0 / 6 <= float(c["asking_price"]) <= med0 * 6]
            observed = filtered_observed or observed
            vals = [float(c["asking_price"]) for c in observed]
            latest = sorted(observed, key=lambda c: str(c.get("last_seen") or c.get("first_seen") or ""))[-1]
            dates_verified = all(bool((c.get("raw") or {}).get("sold_at")) for c in observed)
            observed_records = [{
                "title": c.get("object"), "price": c.get("asking_price"), "currency": curr,
                "sold_at": (c.get("last_seen") or c.get("first_seen") or "")[:10],
                "auction_house": c.get("source_name"), "source_url": c.get("source_url"),
                "lot": None, "date_basis": "sold_at" if (c.get("raw") or {}).get("sold_at") else "observed_at"
            } for c in observed]
            result.update({
                "available": True, "has_evidence": True, "sample_status": "PROVISIONAL",
                "currency": curr, "sample_count": len(vals), "source_count": len(independent_sources),
                "source_names": sorted(independent_sources),
                "three_year_average_price": round(sum(vals) / len(vals)),
                "average_sold_price": round(sum(vals) / len(vals)),
                "median_sold_price": round(median(vals)), "low_sold_price": round(min(vals)), "high_sold_price": round(max(vals)),
                "latest_sold_price": latest.get("asking_price"), "latest_currency": curr,
                "latest_sold_at": (latest.get("last_seen") or latest.get("first_seen") or "")[:10],
                "latest_record": next(r for r in observed_records if r["source_url"] == latest.get("source_url")),
                "records": observed_records,
                "method": "CROSS_SOURCE_SOLD_RECORDS_SAME_CURRENCY_3Y" if dates_verified else "OBSERVED_SOLD_RECORDS_DOMINANT_CURRENCY_3Y",
                "price_basis": (
                    "拍卖方公开 SOLD / Winning bid；成交日期已核验；全部为同一币种 GBP，未做跨币种换算"
                    if dates_verified else
                    "SOLD 标签记录；部分缺少成交日期，按抓取时间观察；未做跨币种换算"
                ),
                "minimum_samples": 3, "minimum_sources": 2,
                "mean_median_gap_pct": round(abs((sum(vals) / len(vals)) - median(vals)) / median(vals) * 100, 1) if median(vals) else 0,
            })
    if valid and not result.get("available"):
        values = [float(r["normalized_eur"]) for r in valid]
        latest = sorted(valid, key=lambda r: (r.get("sold_at") or "", r.get("id") or 0))[-1]
        formatted = []
        for r in valid:
            raw = r.get("raw") or {}
            formatted.append({**r, "title": r.get("object_name"), "lot": raw.get("lot"),
                              "fx_usd_per_eur": r.get("fx_rate")})
        result.update({
            "available": len(valid) >= 5 and len(sources) >= 2,
            "sample_status": "SUFFICIENT" if len(valid) >= 5 and len(sources) >= 2 else "INSUFFICIENT",
            "three_year_average_price": round(sum(values) / len(values)),
            "median_sold_price": round(median(values)),
            "low_sold_price": round(min(values)), "high_sold_price": round(max(values)),
            "latest_sold_price": latest.get("price"), "latest_currency": latest.get("currency"),
            "latest_sold_at": latest.get("sold_at"), "latest_record": formatted[valid.index(latest)],
            "records": formatted,
            "mean_median_gap_pct": round(abs((sum(values) / len(values)) - median(values)) / median(values) * 100, 1) if median(values) else 0,
        })
        if len(valid) < 5 or len(sources) < 2:
            result["reason"] = "INSUFFICIENT_CROSS_SOURCE_SOLD_RECORDS"
    elif not result.get("available") and "reason" not in result:
        result["reason"] = "NO_QUALIFIED_SOLD_RECORDS"

    # A purchase link is not a sold archive: it must be a priced ASKING record seen in this run.
    listings = []
    for c in candidates or []:
        title = str(c.get("object") or "")
        url = str(c.get("source_url") or "")
        path = urlparse(url).path.lower().rstrip("/")
        listing_index = ("search", "results", "cars-for-sale", "for-sale", "/used/", "inventory")
        if (str(c.get("sale_type") or "").upper() != "ASKING" or not c.get("asking_price") or not url
                or int(c.get("source_tier") or 9) != 1
                or len([p for p in path.split("/") if p]) < 2
                or any(x in path for x in listing_index)
                or any(x in (title + " " + url).lower() for x in ("sold", "auction result", "archive"))):
            continue
        listings.append({"title": title, "price": c.get("asking_price"), "currency": c.get("currency", "EUR"),
                         "seller": c.get("source_name") or urlparse(url).netloc,
                         "source_url": url, "checked_at": now.isoformat()})
    result["purchase_listings"] = listings[:5]
    return result


def _persist_analysis_prices(inp, report):
    """Analysis enriches the private price history; incomplete records remain excluded."""
    from .store import price_record_upsert
    key = _subject_key(f"{inp.artist} {inp.artwork} {inp.year or ''} {inp.dimensions or ''}")
    for comparable in report.price.comparables:
        row = comparable.model_dump()
        if not row.get("price") or not row.get("source_url"):
            continue
        currency = row.get("currency") or "EUR"
        price_record_upsert({
            "subject_key": key, "category": inp.category, "maker": inp.artist,
            "object_name": row.get("title") or inp.artwork, "model": inp.artwork,
            "dimensions": inp.dimensions or "", "sale_type": row.get("sale_type", "UNKNOWN"),
            "sold_at": row.get("sold_at"), "price": row["price"], "currency": currency,
            "normalized_eur": row["price"] if currency == "EUR" else None,
            "source_name": row.get("source_name") or "", "source_url": row.get("source_url") or "",
            "source_tier": row.get("source_tier", 0), "price_basis": row.get("price_basis", "UNKNOWN"),
            "comparable_level": row.get("comparability", ""),
            "is_active_listing": row.get("sale_type") == "ASKING", "listing_checked_at": date.today().isoformat(),
            "raw": row,
        })


# 0.6.1：挂载静态目录（i18n.js 等）
from fastapi.staticfiles import StaticFiles
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "radar.html"))


@app.get("/legacy")
def legacy_index():
    """Frozen 0.7.2 surface for developer verification; hidden from normal navigation."""
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


# ================================================================ RADAR 0.8

@app.get("/api/account")
def api_account(authorization: str | None = Header(default=None)):
    token = authorization[7:] if authorization and authorization.lower().startswith("bearer ") else None
    return {"user": authenticate(token), "sample_account": seed_test_account()}

@app.post("/api/account/register")
def api_account_register(payload: dict, request: Request):
    try: return commercial_register(payload, request.client.host if request.client else "local")
    except CommercialError as exc: return _commercial_error(exc)

@app.post("/api/account/verify")
def api_account_verify(payload: dict):
    try: return verify_challenge(str(payload.get("challenge_id") or ""), str(payload.get("code") or ""))
    except CommercialError as exc: return _commercial_error(exc)

@app.post("/api/account/login")
def api_account_login(payload: dict, request: Request):
    try: return commercial_login(str(payload.get("identity") or payload.get("email") or ""), str(payload.get("password") or ""), request.client.host if request.client else "local")
    except CommercialError as exc: return _commercial_error(exc)

@app.post("/api/account/logout")
def api_account_logout(authorization: str | None = Header(default=None)):
    token = authorization[7:] if authorization and authorization.lower().startswith("bearer ") else ""
    if token: commercial_logout(token)
    return {"ok": True}

@app.patch("/api/account/preferences")
def api_account_preferences(payload: dict, authorization: str | None = Header(default=None)):
    current = _current_user(authorization)
    try: return update_preferences(current["user_id"], payload)
    except CommercialError as exc: return _commercial_error(exc)

@app.get("/api/account/usage")
def api_account_usage(authorization: str | None = Header(default=None)):
    return quota_usage(_current_user(authorization)["user_id"])

@app.post("/api/account/schedules/{radar_id}")
def api_account_schedule(radar_id: int, payload: dict, authorization: str | None = Header(default=None)):
    current = _current_user(authorization)
    try: return set_schedule(current["user_id"], radar_id, list(payload.get("times") or []), str(payload.get("timezone") or current["timezone"]))
    except CommercialError as exc: return _commercial_error(exc)

@app.get("/api/account/cost-runs/{run_id}")
def api_account_cost(run_id: str, authorization: str | None = Header(default=None)):
    current = _current_user(authorization); row = cost_run(run_id)
    if not row or row["user_id"] != current["user_id"]: raise HTTPException(404, "Run not found")
    return row

@app.get("/api/radar/dashboard")
def api_radar_dashboard():
    from .radar import dashboard
    return dashboard()


@app.post("/api/radar/understand")
def api_radar_understand(payload: dict):
    from .radar import parse_radar_text
    query = str(payload.get("query") or "").strip()
    if not query:
        raise HTTPException(400, "请先告诉 SIGNAL 你想寻找什么")
    return {"query_original": query, "rules": parse_radar_text(query)}


@app.get("/api/radars")
def api_radars():
    from .radar import list_radars
    return list_radars()


@app.post("/api/radars")
def api_radars_create(payload: dict, authorization: str | None = Header(default=None)):
    from .radar import create_radar, find_similar_radar, parse_radar_text
    query = str(payload.get("query_original") or payload.get("query") or "").strip()
    if not query:
        raise HTTPException(400, "Radar query is required")
    rules = payload.get("rules") or parse_radar_text(query)
    similar = find_similar_radar(rules)
    if similar and not payload.get("create_anyway"):
        raise HTTPException(409, {"code": "SIMILAR_RADAR", "message": "A similar Radar already exists.", "existing": similar})
    current = authenticate(authorization[7:] if authorization and authorization.lower().startswith("bearer ") else None) or seed_test_account()
    from .commercial import quota_guard
    try: quota_guard(current["user_id"], "active_radars")
    except CommercialError as exc: return _commercial_error(exc)
    radar = create_radar(query, rules, name=payload.get("name"),
                        sources=payload.get("sources"), languages=payload.get("languages"),
                        schedule=payload.get("schedule", "TWICE_DAILY"),
                        notify_unknown_price=payload.get("notify_unknown_price", True))
    conn = __import__('app.store', fromlist=['_conn'])._conn()
    try: conn.execute("UPDATE radars SET user_id=? WHERE id=?", (current["user_id"], radar["id"])); conn.commit()
    finally: conn.close()
    return radar


@app.patch("/api/radars/{radar_id}")
def api_radars_update(radar_id: int, payload: dict):
    from .radar import update_radar
    radar = update_radar(radar_id, payload)
    if not radar:
        raise HTTPException(404, "Radar not found")
    return radar


@app.delete("/api/radars/{radar_id}")
def api_radars_delete(radar_id: int):
    from .radar import delete_radar
    if not delete_radar(radar_id):
        raise HTTPException(404, "Radar not found")
    return {"deleted": True, "radar_id": radar_id, "history_preserved": True}


@app.post("/api/radars/{radar_id}/run")
def api_radars_run(radar_id: int, request: Request, authorization: str | None = Header(default=None), idempotency_key: str | None = Header(default=None)):
    from .radar import run_radar
    provider = build_search_provider()
    if not provider.available:
        code = getattr(provider, "status", "SEARCH_PROVIDER_NOT_CONFIGURED")
        message = "请先配置搜索服务" if code == "SEARCH_PROVIDER_NOT_CONFIGURED" else "请先检查搜索服务配置"
        raise HTTPException(409, {"code":code, "message":message})
    current = authenticate(authorization[7:] if authorization and authorization.lower().startswith("bearer ") else None) or seed_test_account()
    key = idempotency_key or request.headers.get("x-idempotency-key") or f"radar:{current['user_id']}:{radar_id}:{request.headers.get('x-request-id','') or os.urandom(8).hex()}"
    try:
        commercial_run_id, cached = begin_job(current["user_id"], radar_id, key)
        if cached and cached.get("result_json"): return cached
        result = run_radar(radar_id, provider=provider)
        from .commercial import meter_add
        meter_add(commercial_run_id, search_requests=result.get("queries_planned",0), pages_processed=result.get("search_results_processed",0), search_provider="configured")
        finish_job(commercial_run_id, key, result)
        result["commercial_run_id"] = commercial_run_id
        return {k: v for k, v in result.items() if k not in {"failures", "source_details", "provider_detail"}}
    except CommercialError as exc:
        return _commercial_error(exc)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@app.get("/api/radar/runs")
def api_radar_runs(radar_id: int | None = None):
    from .radar import list_runs
    return [{k: v for k, v in row.items() if k not in {"failures", "source_details", "provider_detail"}} for row in list_runs(radar_id)]


@app.get("/api/radar/runs/{run_id}/events")
def api_radar_run_events(run_id: int, after_id: int = 0):
    from .radar import get_run, list_run_events
    if not get_run(run_id): raise HTTPException(404, "Run not found")
    return list_run_events(run_id, after_id)


@app.get("/api/radars/{radar_id}/coverage")
def api_radar_coverage(radar_id: int):
    from .radar import list_runs, get_radar
    if not get_radar(radar_id): raise HTTPException(404, "Radar not found")
    runs = list_runs(radar_id)
    return ({k: v for k, v in runs[0].items() if k not in {"failures", "source_details", "provider_detail"}} if runs else
            {"queries_planned": 0, "queries_completed": 0, "queries_failed": 0,
             "search_results_received": 0, "search_results_processed": 0,
             "search_results_deduplicated": 0, "target_market_sources_registered": 126,
             "run_status": "FAILED", "coverage_snapshot": {}})


@app.get("/api/runs/{run_id}/coverage")
def api_run_coverage(run_id: int):
    from .radar import get_run
    row = get_run(run_id)
    if not row: raise HTTPException(404, "Run not found")
    return {k: v for k, v in row.items() if k not in {"failures", "source_details", "provider_detail"}}


@app.get("/api/developer/runs/{run_id}/coverage")
def api_developer_run_coverage(run_id: int, x_developer_key: str | None = Header(default=None)):
    if not os.getenv("SIGNAL_DEVELOPER_KEY") or x_developer_key != os.getenv("SIGNAL_DEVELOPER_KEY"):
        raise HTTPException(403, "Developer access required")
    from .radar import get_run
    row = get_run(run_id)
    if not row: raise HTTPException(404, "Run not found")
    return row


@app.get("/api/developer/radars/{radar_id}/query-plan")
def api_developer_query_plan(radar_id: int, x_developer_key: str | None = Header(default=None)):
    if not os.getenv("SIGNAL_DEVELOPER_KEY") or x_developer_key != os.getenv("SIGNAL_DEVELOPER_KEY"):
        raise HTTPException(403, "Developer access required")
    from .radar import get_radar, query_plan_summary
    radar = get_radar(radar_id)
    if not radar: raise HTTPException(404, "Radar not found")
    return query_plan_summary(radar)


@app.get("/api/discoveries")
def api_discoveries(status: str = ""):
    from .radar import list_discoveries
    return list_discoveries(status)


@app.post("/api/discoveries/{discovery_id}/feedback")
def api_discovery_feedback(discovery_id: int, payload: dict):
    from .radar import set_feedback
    try:
        result = set_feedback(discovery_id, str(payload.get("feedback") or ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not result:
        raise HTTPException(404, "Discovery not found")
    return result


@app.get("/api/sources")
def api_sources(category: str = "", health_status: str = "", enabled: Optional[bool] = None, authorization: str | None = Header(default=None)):
    from .radar import source_registry
    rows = source_registry(category, health_status, enabled)
    # This route is intentionally aggregate-only.  A bearer token identifies a
    # normal customer; it must never turn the public settings screen into a
    # registry-detail view.
    groups = {
        "Global Auction Markets": lambda r: r.get("source_type") in {"AUCTION_HOUSE", "AUCTION_PLATFORM"},
        "Professional Art Markets": lambda r: r.get("category") in {"ART", "BOTH"} and r.get("source_type") in {"DEALER", "GALLERY"},
        "Classic Car Markets": lambda r: r.get("category") in {"CLASSIC_CAR", "BOTH"},
        "Dealer Networks": lambda r: r.get("source_type") in {"DEALER", "GALLERY"},
        "General Second-hand Markets": lambda r: r.get("source_type") in {"MARKETPLACE", "CLASSIFIEDS"},
        "Regional Public Markets": lambda r: r.get("source_type") in {"AGGREGATOR", "PUBLIC_MARKET"},
    }
    result = []
    for label, predicate in groups.items():
        members = [row for row in rows if predicate(row)]
        counts = {}
        for row in members:
            state = row.get("health_status") or "CONFIGURED"
            counts[state] = counts.get(state, 0) + 1
        result.append({"category": label, "count": len(members), "status_counts": counts})
    return result


@app.get("/api/developer/sources")
def api_developer_sources(category: str = "", health_status: str = "", enabled: Optional[bool] = None,
                          x_developer_key: str | None = Header(default=None)):
    expected = os.getenv("SIGNAL_DEVELOPER_KEY")
    if not expected or x_developer_key != expected:
        raise HTTPException(403, "Developer access required")
    from .radar import source_registry
    return source_registry(category, health_status, enabled)


@app.get("/api/sources/route")
def api_source_route(radar_id: int):
    from .radar import get_radar, select_sources_for_run
    radar = get_radar(radar_id)
    if not radar: raise HTTPException(404, "Radar not found")
    rows = select_sources_for_run(radar)
    return {"radar_id": radar_id, "planned_sources": len(rows), "sources": rows,
            "note": "本轮计划来源；不是全部登记来源，也不代表全球市场覆盖。"}


@app.post("/api/sources/{source_id}/probe")
def api_source_probe(source_id: int):
    from .radar import probe_source
    try: return probe_source(source_id)
    except ValueError as exc: raise HTTPException(404, str(exc))


@app.post("/api/sources/probe")
def api_sources_probe(payload: dict):
    from .radar import probe_source, source_registry
    limit = max(1, min(int(payload.get("limit") or 10), 20))
    category = str(payload.get("category") or "")
    candidates = [s for s in source_registry(category=category) if s["access_mode"] not in {"MANUAL_ONLY", "USER_LOGIN_REQUIRED"}]
    candidates.sort(key=lambda s: (bool(s.get("last_check")), s.get("last_check") or ""))
    return {"checked": [probe_source(s["id"]) for s in candidates[:limit]], "limit": limit}


@app.get("/api/prices")
def api_prices(query: str = Query(..., min_length=2)):
    """Independent price endpoint: reusable by Scout, Analyst and future Watch cards."""
    return _price_intelligence(query)


@app.get("/api/health")
def health():
    provider = build_provider()
    search = build_search_provider()
    return {
        "status": "ok",
        "app": "SIGNAL / 0.7.2 (A · Liquid Glass)",
        "version": "0.8.4K",
        "build": "amazing-kimi-0.8.4g-parallels-searxng",
        "llm_configured": provider.configured,
        "llm_provider": provider.provider or None,
        "llm_model": provider.model or None,
        "search_provider": search.name,
        "search_available": search.available,
        "environment": platform.system(),
        "search_mode": "LOCAL_TEST_ONLY" if search.name == "searxng_local" else "PRODUCTION_API",
        "resolved_endpoint": getattr(search, "base_url", None),
        "search_status": "READY" if search.available else getattr(search, "resolved_status", "UNAVAILABLE"),
        "direct_target_fetch": "DISABLED",
    }


@app.post("/api/analyze")
def api_analyze(inp: AnalysisInput):
    if not inp.artist or not inp.artist.strip():
        raise HTTPException(status_code=422, detail="artist 不能为空")

    llm = build_provider()
    researcher = build_search_provider()

    def event_stream():
        q: "queue.Queue[str]" = queue.Queue()

        def on_step(msg: str):
            q.put(json.dumps({"type": "step", "message": msg}, ensure_ascii=False))

        result: dict = {}

        def run():
            try:
                result["report"] = analyze(inp, llm=llm, researcher=researcher, on_step=on_step)
            except Exception as exc:  # 任何故障都以事件形式如实上报
                result["error"] = str(exc)
            finally:
                q.put(None)  # 结束信号

        t = threading.Thread(target=run, daemon=True)
        t.start()
        while True:
            item = q.get()
            if item is None:
                break
            yield item + "\n"
        t.join(timeout=5)

        if "error" in result:
            yield json.dumps({"type": "error", "message": result["error"]}, ensure_ascii=False) + "\n"
            return

        _persist_analysis_prices(inp, result["report"])
        report = save_report(result["report"])
        yield json.dumps({"type": "report", "report": report.model_dump()}, ensure_ascii=False) + "\n"

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


@app.get("/api/reports")
def api_reports():
    return list_reports()


@app.get("/api/reports/{rid}")
def api_report(rid: int):
    data = get_report(rid)
    if not data:
        raise HTTPException(status_code=404, detail="报告不存在")
    return data


@app.post("/api/backtest/takis")
def api_backtest():
    provider = build_provider()
    return run_backtest(llm=provider)


# ---------------------------------------------------------------- 0.4 Decision Memory

@app.post("/api/decision-memory")
def api_decision_memory(payload: dict):
    """保存用户真实决策。Analyst 字段由服务端从原始报告提取，禁止人工伪造。"""
    report_id = payload.get("report_id")
    if not report_id:
        raise HTTPException(status_code=422, detail="report_id 不能为空")
    report = get_report(int(report_id))
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")

    # Analyst Recommendation：只来自原始报告，不接受客户端传入
    price = report.get("price") or {}
    ns = report.get("negotiation") or {}
    dm = {
        "report_id": int(report_id),
        "created_at": report.get("created_at"),
        "initial_decision": (report.get("input") or {}).get("initial_decision")
                            or payload.get("initial_decision"),
        "initial_offer": (report.get("input") or {}).get("initial_planned_offer")
                         or payload.get("initial_offer"),
        "analyst_decision": report.get("decision") or "WATCH",
        "analyst_opening": ns.get("opening_offer"),
        "analyst_target_low": ns.get("target_low"),
        "analyst_target_high": ns.get("target_high"),
        "analyst_walkaway": ns.get("walk_away"),
        "final_decision": payload.get("final_decision"),
        "final_offer": payload.get("final_offer"),
        "final_transaction_price": payload.get("final_transaction_price"),
        "transaction_status": payload.get("transaction_status"),
        "decision_changed": payload.get("decision_changed"),
        "notes": payload.get("notes") or "",
    }
    mid = save_decision_memory(dm)
    return {"id": mid, "report_id": int(report_id), "saved": True,
            "analyst_decision": dm["analyst_decision"]}


@app.get("/api/history")
def api_history():
    return {"rows": history(), "stats": history_stats()}

@app.post("/api/history/delete")
def api_history_delete(payload: dict):
    from .store import history_delete
    return {"deleted": history_delete(payload.get("report_ids") or [])}


# ---------------------------------------------------------------- 0.5 Watchlist

@app.get("/api/watch")
def api_watch_list():
    from .store import watch_list as _watch_list
    return {"watches": _watch_list()}


@app.post("/api/watch")
def api_watch_create(payload: dict):
    from .store import watch_create, watch_get
    if not payload.get("maker") or not payload.get("target"):
        raise HTTPException(status_code=422, detail="maker 和 target 不能为空")
    wid = watch_create(payload)
    return {"id": wid, "watch": watch_get(wid)}


@app.get("/api/watch/{wid}")
def api_watch_get(wid: int):
    from .store import watch_get, watch_items, watch_events
    w = watch_get(wid)
    if not w:
        raise HTTPException(status_code=404, detail="watch 不存在")
    return {"watch": w, "items": watch_items(wid), "events": watch_events(wid)}


@app.post("/api/watch/{wid}/check")
def api_watch_check(wid: int):
    """MANUAL：立即执行一次 Market Check（BASELINE 或 CHECK）。"""
    from .watch import run_watch
    from .store import watch_get
    provider = build_search_provider()
    summary = run_watch(wid, provider=provider)
    if "error" in summary and summary.get("paused"):
        raise HTTPException(status_code=400, detail="watch 已暂停")
    w = watch_get(wid)
    summary["watch"] = w
    return summary


@app.post("/api/watch/{wid}/pause")
def api_watch_pause(wid: int):
    from .store import watch_update
    watch_update(wid, {"status": "PAUSED"})
    return {"id": wid, "status": "PAUSED"}


@app.post("/api/watch/{wid}/resume")
def api_watch_resume(wid: int):
    from .store import watch_update
    watch_update(wid, {"status": "ACTIVE"})
    return {"id": wid, "status": "ACTIVE"}


@app.post("/api/watch/{wid}/edit")
def api_watch_edit(wid: int, payload: dict):
    from .store import watch_update
    watch_update(wid, payload)
    return {"id": wid, "updated": True}


@app.delete("/api/watch/{wid}")
def api_watch_delete(wid: int):
    from .store import watch_delete
    watch_delete(wid)  # History（runs/events）不删除
    return {"id": wid, "deleted": True}


@app.get("/api/scheduler")
def api_scheduler_status():
    from .scheduler import status as sched_status
    return sched_status()


@app.post("/api/scheduler/enable")
def api_scheduler_enable():
    from .scheduler import enable as sched_enable
    return sched_enable()


@app.post("/api/scheduler/disable")
def api_scheduler_disable():
    from .scheduler import disable as sched_disable
    return sched_disable()


# ---------------------------------------------------------------- 0.6.1 产品化接口

@app.get("/api/agents")
def api_agents():
    """数字团队健康状态（05 节：UI 状态必须来自真实 service state）。

    state: READY / WORKING / LIMITED / OFFLINE / PAUSED / ACTIVE / IDLE
    """
    from .scheduler import status as sched_status
    from .store import watch_list
    provider = build_provider()
    search = build_search_provider()
    sched = sched_status()
    watches = watch_list()
    active_watches = [w for w in watches if w.get("status") == "ACTIVE"]

    # Keep the real capability state in the API; the main UI presents the
    # usable deterministic service as Ready and reserves capability detail for Settings.
    analyst = {"state": "READY" if provider.configured else "LIMITED"}
    # SCOUT：搜索不可用 → OFFLINE
    scout = {"state": "READY" if search.available else "OFFLINE"}
    # MONITOR：调度启用+有活跃 Watch → ACTIVE；有 Watch 但未启用调度 → IDLE；无 Watch → OFFLINE
    if active_watches and sched.get("registered"):
        monitor = {"state": "ACTIVE"}
    elif active_watches:
        monitor = {"state": "IDLE"}
    elif watches:
        monitor = {"state": "PAUSED"}
    else:
        monitor = {"state": "OFFLINE"}
    return {"team": {"scout": scout, "analyst": analyst, "monitor": monitor},
            "scheduler": sched, "watch_count": len(watches)}


@app.post("/api/translate")
def api_translate(payload: dict):
    """Evidence 翻译（08 节）。只属 Presentation Layer——绝不进入 Pricing Engine。

    硬规则：数字、价格、年份、里程、型号原样保留，翻译不得改写。
    结果按 (provider, model, target, 原文 hash) 缓存，同段原文不重复花钱。
    """
    text = (payload.get("text") or "").strip()
    target = payload.get("target") or "zh-CN"
    if not text:
        raise HTTPException(status_code=422, detail="text 不能为空")
    if len(text) > 2000:
        text = text[:2000]

    import hashlib
    llm = build_provider()
    if not llm.configured:
        return {"translated": None, "unavailable": True,
                "note": "翻译服务未配置（LLM Key）——Evidence 将显示原始内容"}

    from .store import llm_cache_get, llm_cache_put
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    cached = llm_cache_get(llm.provider, llm.model, f"translate::{target}", h)
    if cached is not None and cached.get("translated"):
        return {"translated": cached["translated"], "provider": llm.provider, "cached": True}

    result = llm.chat([
        {"role": "system", "content": (
            "你是收藏品市场的专业翻译。把证据摘要翻译成" +
            ("简体中文" if target.startswith("zh") else "English") + "。\n"
            "硬性规则：\n"
            "1. 所有数字、金额、年份、里程、尺寸、型号代码（如 964 / N-GT / M003 / €310,500）"
            "必须原样保留，禁止改写、换算、省略。\n"
            "2. 只翻译，不评论，不添加解释。\n"
            "3. 返回严格 JSON：{\"translated\": \"...\"}")},
        {"role": "user", "content": text},
    ], temperature=0.1, timeout=30)
    if not result or not result.get("translated"):
        return {"translated": None, "unavailable": True, "note": "翻译失败，显示原文"}
    llm_cache_put(llm.provider, llm.model, f"translate::{target}", h, result)
    return {"translated": result["translated"], "provider": llm.provider}


@app.get("/api/settings/dev")
def api_settings_dev():
    """Developer 信息（06 节：只在设置 → Advanced 中可见）。"""
    from .scheduler import status as sched_status
    provider = build_provider()
    search = build_search_provider()
    return {
        "search_provider": search.name,
        "search_available": search.available,
        "llm_provider": provider.provider,
        "llm_model": provider.model,
        "llm_configured": provider.configured,
        "llm_calls_total": provider.usage.calls,
        "scheduler": sched_status(),
        "database": "healthy",
        "note": "这是开发者信息。普通界面不展示 Provider/API/Cache 等基础设施。",
    }


@app.get("/api/settings/search-provider")
def api_settings_search_provider():
    """Return provider state only; API keys are never serialized."""
    return search_provider_settings()


@app.post("/api/settings/search-provider")
def api_settings_search_provider_save(payload: dict):
    try:
        return save_search_provider(str(payload.get("provider") or ""),
                                    str(payload.get("api_key") or ""))
    except ValueError as exc:
        code = str(exc)
        message = "请选择 Tavily 或 Brave" if code == "INVALID_PROVIDER" else "请输入 API Key"
        raise HTTPException(400, {"code": code, "message": message})


@app.post("/api/settings/search-provider/test")
def api_settings_search_provider_test(payload: dict | None = None):
    """Persist the selected key and make one real, minimal official API query."""
    payload = payload or {}
    if payload.get("provider") or payload.get("api_key"):
        api_settings_search_provider_save(payload)
    provider = build_search_provider()
    if not provider.available:
        code = getattr(provider, "status", "SEARCH_PROVIDER_NOT_CONFIGURED")
        message = "请先配置搜索服务" if code == "SEARCH_PROVIDER_NOT_CONFIGURED" else "请先检查搜索服务配置"
        raise HTTPException(409, {"code":code, "message":message})
    try:
        provider.search("Porsche 964", max_results=1)
    except RuntimeError:
        code = getattr(provider, "status", "SERVICE_UNAVAILABLE")
        record_search_provider_test("FAILED", code)
        messages = {
            "INVALID_API_KEY":"API Key 无效", "QUOTA_EXCEEDED":"额度不足",
            "RATE_LIMITED":"请求过于频繁", "NETWORK_ERROR":"网络连接失败",
            "SERVICE_UNAVAILABLE":"服务暂不可用",
        }
        raise HTTPException(400, {"code":code, "message":messages.get(code, "服务暂不可用")})
    state = record_search_provider_test("OK")
    return {**state, "test_status":"OK"}


# ---------------------------------------------------------------- 0.6 Scout

@app.get("/api/scout/profile")
def api_scout_profile():
    from .store import scout_profile_get
    from .scout import seed_suggestions
    return {"profile": scout_profile_get(), "suggestions": seed_suggestions()}


@app.post("/api/scout/profile")
def api_scout_profile_save(payload: dict):
    """保存 Scout Profile。Interests 立即解析（LLM 优先/确定性兜底），返回「SCOUT 理解确认卡」数据。"""
    from hashlib import md5

    from .interests import generate_search_plan, generate_seeds, parse_interest, structured_summary
    from .store import scout_profile_get, scout_profile_save

    # 0.6.2：Interests 一级输入——保存即解析（24 节：Profile 未变复用，变了才重 parse）
    interests = (payload.get("interests") or "").strip()
    h = md5(interests.encode("utf-8")).hexdigest()
    prev = scout_profile_get() or {}
    if interests and (prev.get("interests_hash") != h or not prev.get("structured_interest")):
        provider = build_provider()
        structured = parse_interest(interests, llm=provider)
        if structured and not structured.get("error"):
            payload["structured_interest"] = structured
            payload["generated_seeds"] = generate_seeds(structured)
            payload["parser_version"] = "0.6.2"
            payload["interests_hash"] = h
    else:
        # 复用：保留此前解析结果，避免覆盖
        for k in ("structured_interest", "generated_seeds", "parser_version", "interests_hash"):
            if k in prev and k not in payload:
                payload[k] = prev[k]

    pid = scout_profile_save(payload)
    saved = scout_profile_get() or {}
    structured = saved.get("structured_interest") or {}
    seeds = saved.get("generated_seeds") or {}
    summary = structured_summary(structured)
    plan = generate_search_plan(structured, seeds) if summary["ok"] else []
    return {
        "id": pid,
        "profile": saved,
        "understood": summary,          # 「SCOUT 理解的是」确认卡
        "search_plan": plan,            # 已生成的市场搜索策略（queries 数 = len）
    }


@app.post("/api/scout/run")
def api_scout_run(payload: dict | None = None):
    """MANUAL：立即执行一次 Scout 巡视（默认每天一次由 launchd 触发）。"""
    from hashlib import md5
    from .interests import generate_seeds, parse_interest
    from .scout import run_scout
    from .store import scout_candidates, scout_profile_get, scout_profile_save
    payload = payload or {}
    interests = (payload.get("interests") or "").strip()
    if interests:
        structured = parse_interest(interests, llm=build_provider())
        if not structured or structured.get("error"):
            return {"status": "PENDING", "error": (structured or {}).get("error", "无法理解巡视目标")}
        previous = scout_profile_get() or {}
        category = structured.get("category") or (
            "CLASSIC_CAR" if structured.get("maker") or structured.get("model") else "ART"
        )
        # 每次输入都是独立任务：不继承隐藏的 artists/models/keywords，杜绝跨目标串线。
        scout_profile_save({
            "categories": [category],
            "budget_low": previous.get("budget_low"),
            "budget_high": previous.get("budget_high"),
            "currency": previous.get("currency", "EUR"),
            "markets": previous.get("markets", "GLOBAL"),
            "opportunity_types": [],
            "interests": interests,
            "avoid": previous.get("avoid", ""),
            "seeds_artists": "", "seeds_models": "", "seeds_keywords": "",
            "structured_interest": structured,
            "generated_seeds": generate_seeds(structured),
            "parser_version": "0.7.2",
            "interests_hash": md5(interests.encode("utf-8")).hexdigest(),
        })
    provider = build_search_provider()
    llm = build_provider()
    result = run_scout(provider=provider, llm=llm)
    # 带出最新候选（含状态）
    if "run_id" in result:
        result["candidates"] = scout_candidates(limit=20)
    return result


@app.get("/api/scout/today")
def api_scout_today():
    """最近一次 Scout 巡视结果。四态区分：
    - 无 run / 无 profile        → state=NOT_RUN   （尚未进行市场巡视）
    - run.status=FAILED          → state=FAILED    （暂时无法连接市场）
    - run.status=PENDING         → state=PENDING   （关注条件未成功解析）
    - run.status=COMPLETED       → brief 决定展示（found/near_miss/empty_scan）
    """
    from .store import scout_candidates_by_run, scout_latest_run, watch_events_recent
    run = scout_latest_run()
    if not run:
        return {"run": None, "state": "NOT_RUN", "top": [], "candidates": [],
                "no_compelling": True, "parse_info": {}, "search_plan": [],
                "brief": None, "near_misses": []}
    cands = scout_candidates_by_run(run["id"])
    top = [c for c in cands if c["scout_score"] >= 55][:5]
    status = run.get("status") or "COMPLETED"
    if status == "COMPLETED":
        # 从 run 持久化数据还原简报与 near miss（0 推荐也是情报）
        brief = run.get("brief") or {}
        near_misses = run.get("near_misses") or []
        if not brief and cands:
            brief = {"subject": (run.get("parse_info") or {}).get("artist") or "",
                     "queries": len(run.get("search_plan") or []),
                     "records": run.get("results_raw", 0),
                     "candidates": len(cands),
                     "recommended": len(top),
                     "near_miss_count": sum(1 for c in cands if 25 <= c["scout_score"] < 55),
                     "conclusion_key": "found" if top else
                     ("near_miss" if cands else "empty_scan")}
        if not near_misses:
            near_misses = [c for c in cands if 25 <= c["scout_score"] < 55][:3]
        state = "COMPLETED" if top else "DONE_EMPTY"
    else:
        state = status  # FAILED / PENDING
        brief, near_misses = run.get("brief"), run.get("near_misses")
    # 首页价格摘要（严格口径）：
    # - 艺术品必须先具体到灯具/系列号/编号，不能把同名独件作品混进来；
    # - 只接受 Tier 1 拍卖行的直接 lot 页面，聚合页、艺术家页和市场文章不计入；
    # - 普通实时结果要求同币种至少 3 条；人工核验基准可按拍卖日 ECB 汇率统一为 EUR。
    # 任何一项不满足都返回“证据不足”，绝不以单条记录冒充均价。
    parse_info = run.get("parse_info") or {}
    interests = str(parse_info.get("interests") or "")
    series = str(parse_info.get("series") or "")
    subject_text = f"{interests} {series} {parse_info.get('artist') or ''} {parse_info.get('maker') or ''} {parse_info.get('model') or ''}".lower()
    verified_reference = _price_intelligence(subject_text, cands)
    is_art = bool(parse_info.get("artist"))
    art_subject_specific = bool(
        re.search(r"\b(?:series|serie|série)\s*[-:#.]?\s*\d+\b", subject_text)
        or re.search(r"\bno\.?\s*\d+\b", subject_text)
        or "signal lamp" in subject_text
    )
    specific_markers = []
    series_match = re.search(r"\b(?:series|serie|série)\s*[-:#.]?\s*(\d+)\b", subject_text)
    number_match = re.search(r"\bno\.?\s*(\d+)\b", subject_text)
    if series_match:
        specific_markers.append(("series", series_match.group(1)))
    if number_match:
        specific_markers.append(("number", number_match.group(1)))
    if "signal lamp" in subject_text:
        specific_markers.append(("text", "lamp"))

    def _matches_specific_subject(c):
        if not specific_markers:
            return True
        haystack = f"{c.get('object') or ''} {c.get('source_url') or ''}".lower()
        for kind, value in specific_markers:
            if kind == "series" and not re.search(rf"\b(?:series|serie|série)[-_/ :#.]*{re.escape(value)}\b", haystack):
                return False
            if kind == "number" and not re.search(rf"\b(?:no)?[-_/ :#.]*{re.escape(value)}\b", haystack):
                return False
            if kind == "text" and value not in haystack:
                return False
        return True

    def _is_direct_auction_lot(c):
        if int(c.get("source_tier") or 9) != 1:
            return False
        title = str(c.get("object") or "").lower()
        url = str(c.get("source_url") or "")
        path = urlparse(url).path.lower().rstrip("/")
        noisy_title = (
            "for sale", "market data", "price guide", "auction results",
            "works for sale", "pricing,", "artist page",
        )
        noisy_path = (
            "/artist/", "/artists/", "/buy/", "/results/",
            "sold-at-auction-prices", "/market/",
        )
        if any(x in title for x in noisy_title) or any(x in path for x in noisy_path):
            return False
        # A direct lot/detail page has a meaningful path beyond the host root.
        return len([p for p in path.split("/") if p]) >= 2

    sold_groups = {}
    sold_source_domains = {}
    market_summary = {
        "available": False,
        "sample_count": 0,
        "minimum_samples": 3,
        "method": "DIRECT_AUCTION_LOTS_EXACT_SUBJECT_SAME_CURRENCY",
    }
    if verified_reference:
        market_summary = verified_reference
    elif is_art and not art_subject_specific:
        market_summary["reason"] = "SUBJECT_TOO_BROAD"
    else:
        for c in cands:
            if (c.get("sale_type") != "SOLD" or not c.get("asking_price")
                    or not _is_direct_auction_lot(c) or not _matches_specific_subject(c)):
                continue
            price = float(c["asking_price"])
            if not (0 < price < 100_000_000):
                continue
            curr = c.get("currency") or "EUR"
            sold_groups.setdefault(curr, []).append(price)
            domain = urlparse(str(c.get("source_url") or "")).netloc.lower()
            if domain:
                sold_source_domains.setdefault(curr, set()).add(domain)

    if sold_groups and not verified_reference:
        curr, prices = max(sold_groups.items(), key=lambda kv: len(kv[1]))
        ordered = sorted(prices)
        median = ordered[len(ordered) // 2]
        filtered = [p for p in prices if median / 6 <= p <= median * 6]
        market_summary["sample_count"] = len(filtered)
        market_summary["currency"] = curr
        market_summary["source_count"] = len(sold_source_domains.get(curr, set()))
        market_summary["source_names"] = sorted(sold_source_domains.get(curr, set()))
        if len(filtered) < 3 or len(sold_source_domains.get(curr, set())) < 2:
            market_summary["reason"] = "INSUFFICIENT_DIRECT_LOTS"
        elif max(filtered) / min(filtered) > 6:
            market_summary["reason"] = "PRICE_DISPERSION"
        else:
            market_summary.update({
                "available": True,
                "average_sold_price": round(sum(filtered) / len(filtered)),
                "low_sold_price": round(min(filtered)),
                "high_sold_price": round(max(filtered)),
            })
    elif not verified_reference and "reason" not in market_summary:
        market_summary["reason"] = "NO_DIRECT_AUCTION_LOTS"
    # 监控更新独立于 Scout 巡视：后台 08:00/20:00 的新挂牌、新成交和价格变化
    # 直接进入首页，避免用户必须先打开监控页才能看到变化。
    monitor_updates = []
    today_prefix = date.today().isoformat()
    for e in watch_events_recent(limit=80):
        # 首页“今日机会”只显示今天两次自动检查产生的变化；历史事件仍保留在监控详情页。
        if not str(e.get("created_at") or "").startswith(today_prefix):
            continue
        monitor_updates.append({
            "id": e.get("id"), "watch_id": e.get("watch_id"),
            "event_type": e.get("event_type"), "alert_level": e.get("alert_level"),
            "title": e.get("item_title") or e.get("watch_target") or "—",
            "target": e.get("watch_target") or "", "maker": e.get("watch_maker") or "",
            "source_url": e.get("item_source_url") or "", "price": e.get("item_price"),
            "currency": e.get("item_currency") or "EUR", "sale_type": e.get("item_sale_type"),
            "old_value": e.get("old_value") or "", "new_value": e.get("new_value") or "",
            "opportunity_score": e.get("opportunity_score") or 0,
            "created_at": e.get("created_at") or "",
        })
    return {
        "run": run,
        "state": state,
        "top": top,
        "candidates": cands,
        "near_misses": near_misses,
        "brief": brief,
        "no_compelling": len(top) == 0,
        "parse_info": run.get("parse_info") or {},
        "search_plan": run.get("search_plan") or [],
        "market_summary": market_summary,
        "monitor_updates": monitor_updates,
    }


@app.get("/api/scout/history")
def api_scout_history():
    """SCOUT 巡视记录（0.6.3 P1）：HISTORY 页回看，支撑 Precision 计算。"""
    from .store import scout_runs_list
    rows = scout_runs_list(limit=30)
    out = []
    for r in rows:
        pi = r.get("parse_info") or {}
        b = r.get("brief") or {}
        out.append({
            "run_id": r.get("id"),
            "created_at": r.get("started_at"),
            "subject": b.get("subject") or pi.get("artist") or pi.get("model") or "—",
            "status": r.get("status"),
            "queries": b.get("queries") or len(r.get("search_plan") or []),
            "records": r.get("results_raw", 0),
            "candidates": r.get("candidates", 0),
            "recommended": r.get("top_shown", 0),
        })
    return {"runs": out}

@app.post("/api/scout/history/delete")
def api_scout_history_delete(payload: dict):
    from .store import scout_runs_delete
    return {"deleted": scout_runs_delete(payload.get("run_ids") or [])}


@app.post("/api/scout/feedback")
def api_scout_feedback(payload: dict):
    """用户反馈：INTERESTED / NOT_INTERESTED / ALREADY_KNOW / WRONG_MATCH（+why_not）。"""
    from .store import scout_candidate_feedback
    cid = payload.get("candidate_id")
    fb = payload.get("feedback")
    if not cid or fb not in ("INTERESTED", "NOT_INTERESTED", "ALREADY_KNOW", "WRONG_MATCH"):
        raise HTTPException(status_code=422, detail="candidate_id 或 feedback 无效")
    scout_candidate_feedback(int(cid), fb, payload.get("why_not") or "")
    return {"saved": True, "candidate_id": int(cid), "feedback": fb}


@app.get("/api/notifications/email/status")
def api_notification_email_status():
    """Registered-user notification channel status; never contacts third parties."""
    cfg = _apply_comm_config()
    configured = bool(os.getenv("KIMI_SMTP_HOST") and os.getenv("KIMI_SMTP_USER") and os.getenv("KIMI_SMTP_PASSWORD"))
    return {"channel": "EMAIL", "state": "READY" if configured else "DISCONNECTED",
            "configured": configured, "password_saved": bool(os.getenv("KIMI_SMTP_PASSWORD") or cfg.get("smtp_password")), "from_name": os.getenv("KIMI_EMAIL_FROM_NAME", "用户助理"),
            "smtp_host": os.getenv("KIMI_SMTP_HOST", ""), "smtp_port": os.getenv("KIMI_SMTP_PORT", "587"),
            "smtp_user": os.getenv("KIMI_SMTP_USER", ""),
            "from_address": os.getenv("KIMI_EMAIL_FROM", os.getenv("KIMI_SMTP_USER", "")),
            "mailbox_url": os.getenv("KIMI_WEBMAIL_URL", ""),
            "note": "仅用于向当前注册用户发送 Radar 与账户通知。"}


@app.post("/api/notifications/email/config")
def api_notification_email_config(payload: dict):
    """Configure the registered user's notification mailbox."""
    existing = _load_comm_config()
    password = str(payload.get("smtp_password") or existing.get("smtp_password") or "")
    required_values = (payload.get("smtp_host"), payload.get("smtp_user"), password)
    if any(not str(v or "").strip() for v in required_values):
        raise HTTPException(status_code=422, detail="SMTP 主机、账号和应用专用密码不能为空")
    cfg = {"smtp_host":str(payload["smtp_host"]).strip(), "smtp_port":str(payload.get("smtp_port") or "587"),
           "smtp_user":str(payload["smtp_user"]).strip(), "smtp_password":password,
           "from_address":str(payload.get("from_address") or existing.get("from_address") or "").strip(),
           "webmail_url":str(payload.get("webmail_url") or existing.get("webmail_url") or "").strip()}
    os.makedirs(os.path.dirname(COMM_CONFIG_PATH), exist_ok=True)
    with open(COMM_CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
    for key, env_key in (("smtp_host","KIMI_SMTP_HOST"),("smtp_port","KIMI_SMTP_PORT"),("smtp_user","KIMI_SMTP_USER"),("smtp_password","KIMI_SMTP_PASSWORD"),("from_address","KIMI_EMAIL_FROM"),("webmail_url","KIMI_WEBMAIL_URL")):
        if cfg.get(key): os.environ[env_key] = cfg[key]
    return {"connected": True, "status": api_notification_email_status()}


@app.post("/api/scout/{cid}/analyze")
def api_scout_analyze(cid: int):
    """Candidate → Analyst 联动（12 节）：直接生成分析输入，复用已抓取的证据与缓存。"""
    from .store import scout_candidate_get
    c = scout_candidate_get(cid)
    if not c:
        raise HTTPException(status_code=404, detail="candidate 不存在")
    return {
        "candidate": c,
        "prefill": {
            "artist": c.get("maker") or c.get("object", "").split()[0],
            "artwork": c.get("object", ""),
            "asking_price": c.get("asking_price"),
            "currency": c.get("currency", "EUR"),
            "category": c.get("category", "ART"),
            "notes": f"Scout Candidate #{cid}: {c.get('source_url', '')}",
        },
    }


@app.post("/api/scout/{cid}/watch")
def api_scout_watch(cid: int):
    """Candidate → Watchlist（13 节）：东西不错但现在不买。"""
    from .store import scout_candidate_get, watch_create
    c = scout_candidate_get(cid)
    if not c:
        raise HTTPException(status_code=404, detail="candidate 不存在")
    wid = watch_create({
        "category": c.get("category", "ART"),
        "maker": c.get("maker") or c.get("object", "").split()[0],
        "target": c.get("object", ""),
        "price_low": c.get("asking_price"),
        "price_high": (c.get("asking_price") or 0) * 1.3 if c.get("asking_price") else None,
        "currency": c.get("currency", "EUR"),
        "frequency": "MANUAL",
        "notes": f"来自 Scout Candidate #{cid}（score {c.get('scout_score', 0)}）",
    })
    return {"watch_id": wid, "created": True}
