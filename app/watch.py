"""Watchlist + Opportunity Monitor（0.5）。

核心链路（01 节）：
    WATCHLIST → Scheduled Check → Search → Evidence Gate → Change Detection
    → Opportunity Scoring → Only meaningful changes → ALERT / NO ALERT

铁律：
- Monitor 不是 Scout：只盯用户指定的对象，不漫无目的找
- Monitor Pass 纯确定性（0 次 LLM 调用）：变化检测、指纹、评分全走规则；
  DeepSeek 只在用户点 ANALYZE OPPORTUNITY 时介入（Full Analyst Pass，复用 0.4 管线）
- Evidence Gate（0.4.1）继承：Tier 3 永远不能成为价格证据，告警最高 INFO
- 价格历史 append-only：watch_events 记录 325 → 315 → 289，绝不覆盖
"""
import hashlib
import re
import time
from typing import List, Optional, Tuple

from .models import (
    ALERT_ACTION,
    ALERT_IGNORE,
    ALERT_INFO,
    ALERT_INTERESTING,
    EV_INFO_CHANGE,
    EV_NEW_LISTING,
    EV_NEW_SOLD,
    EV_PRICE_DROP,
    EV_PRICE_INCREASE,
    EV_REMOVED,
    SALE_ASKING,
    SALE_ESTIMATE,
    SALE_SOLD,
    WATCH_STATUS_ACTIVE,
    WATCH_STATUS_PAUSED,
    AnalysisInput,
    Comparable,
    now_iso,
)
from .research import extract_comparables, generate_queries, source_tier

_STRONG_VERSION_WORDS = ("n-gt", "ngt", "n/gt", "competition", "m003", "option 003", "m3", "gt3")


# ---------------------------------------------------------------- Fingerprint（07 节）

def make_fingerprint(rec: Comparable, watch: dict) -> str:
    """去重指纹。优先唯一 ID（lot/VIN/chassis），否则属性组合归一化哈希。

    同一辆车被 RM Sotheby's 和 Classic.com 收录 → 同一 fingerprint → 不生成两个 Opportunity。
    """
    text = f"{rec.title} {rec.evidence_excerpt or ''}"
    lot = re.search(r"\blot\s*[#: ]?\s*([a-z0-9][a-z0-9\-]{2,12})", text, re.I)
    if lot:
        return f"lot::{re.sub(r'[^a-z0-9]', '', lot.group(1).lower())}"
    vin = re.search(r"\b[0-9a-hj-npr-z]{17}\b", rec.title.lower() or "")
    if vin:
        return f"vin::{vin.group(0)}"
    attrs = rec.attributes or {}
    if attrs.get("canonical_url"):
        from urllib.parse import urlsplit, urlunsplit
        p = urlsplit(attrs["canonical_url"])
        identity = urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip('/'), '', ''))
        return "listing::" + hashlib.sha256(identity.encode()).hexdigest()
    parts = [
        watch.get("maker", ""), watch.get("target", ""),
        rec.year or "", str(attrs.get("color") or ""), str(attrs.get("mileage") or ""),
        _domain(rec.source_url or ""),
    ]
    norm = re.sub(r"[^a-z0-9]+", "", " ".join(parts).lower())
    return f"attr::{hashlib.md5(norm.encode()).hexdigest()[:16]}"


def _domain(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url or "").netloc.replace("www.", "")[:40]


# ---------------------------------------------------------------- 相关性过滤（11 节）

def _relevance_score(rec: Comparable, watch: dict) -> int:
    title = f"{rec.title} {rec.evidence_excerpt or ''}".lower()
    s = 0
    target_tokens = set(re.findall(r"[a-z0-9]+", (watch.get("target") or "").lower()))
    maker = (watch.get("maker") or "").lower()
    if maker and maker in title:
        s += 30
    if target_tokens:
        hit = sum(1 for t in target_tokens if t in title)
        s += min(40, hit * 20)
    kws = [k.strip().lower() for k in (watch.get("keywords") or "").split(",") if k.strip()]
    for kw in kws:
        if kw and kw in title:
            s += 10
    return min(100, s)


# ---------------------------------------------------------------- Opportunity Score（09 节）

def opportunity_score(rec: Comparable, watch: dict, baseline_median: Optional[float]) -> Tuple[int, str]:
    """确定性 0-100。不是估值，是"这条变化值不值得打扰老板"。"""
    s = 0
    reasons: List[str] = []
    title = f"{rec.title} {rec.evidence_excerpt or ''}".lower()

    if rec.price and baseline_median and baseline_median > 0:
        diff = (baseline_median - rec.price) / baseline_median
        s += max(0, min(30, int(15 + diff * 100)))
        if diff > 0.05:
            reasons.append(f"价格低于观察中位数 {abs(diff)*100:.0f}%")
    elif rec.price:
        s += 8

    strong = [w for w in _STRONG_VERSION_WORDS if w in title]
    if strong:
        s += 20
        reasons.append("目标版本命中（" + "/".join(strong) + "）")
    else:
        reasons.append("同车系/同系列（非目标版本）")

    tier = rec.source_tier or source_tier(rec.source_url or "")
    s += {1: 15, 2: 8, 3: 0}.get(tier, 5)

    kws = [k.strip().lower() for k in (watch.get("keywords") or "").split(",") if k.strip()]
    if kws:
        hit = sum(1 for kw in kws if kw and kw in title)
        s += int(15 * hit / len(kws))

    if rec.price:
        s += 5
    if rec.year:
        s += 3
    if (rec.attributes or {}) and any((rec.attributes or {}).values()):
        s += 2

    try:
        from datetime import datetime, timezone
        seen = datetime.fromisoformat(rec.retrieved_at or rec.sold_at or "").timestamp()
        age_h = (time.time() - seen) / 3600
        if age_h < 24:
            s += 10
        elif age_h < 72:
            s += 7
        elif age_h < 168:
            s += 4
        else:
            s += 1
    except Exception:
        s += 3

    if not strong:
        s = min(s, 35)
    if rec.sale_type == SALE_ESTIMATE:
        s = min(s, 30)
    if rec.price and watch.get("price_high"):
        if rec.price > watch["price_high"] * 10 or rec.price < watch["price_high"] * 0.02:
            s = min(s, 35)
    return min(100, s), "; ".join(reasons) if reasons else ""


# ---------------------------------------------------------------- Alert Level（10 节）

def alert_level(score: int, tier: int) -> str:
    """四级告警。Tier 3（论坛/聚合）强制降级——最多 INFO，绝无 ACTION。"""
    if tier >= 3:
        return ALERT_INFO if score >= 20 else ALERT_IGNORE
    if score >= 70:
        return ALERT_ACTION
    if score >= 45:
        return ALERT_INTERESTING
    if score >= 20:
        return ALERT_INFO
    return ALERT_IGNORE


# ---------------------------------------------------------------- Monitor Run（15 节）

def run_watch(wid: int, provider=None, on_step=None) -> dict:
    """一次 Market Check（BASELINE 或 CHECK）。纯确定性，0 次 LLM 调用。"""
    from .store import (
        watch_event_add,
        watch_get,
        watch_item_upsert,
        watch_run_create,
        watch_update,
    )

    def step(msg: str):
        if on_step:
            try:
                on_step(msg)
            except Exception:
                pass

    watch = watch_get(wid)
    if not watch:
        return {"error": "watch 不存在"}
    if watch.get("status") == WATCH_STATUS_PAUSED:
        return {"error": "watch 已暂停", "paused": True}

    mode = "BASELINE" if not watch.get("baseline_at") else "CHECK"
    t0 = time.time()
    errors: List[str] = []
    run = {"watch_id": wid, "started_at": now_iso(), "mode": mode, "errors": errors}
    step(f"[{watch['target']}] {mode} SCAN 开始")

    from .research import run_research
    inp = AnalysisInput(
        artist=watch.get("maker", ""),
        artwork=watch.get("target", ""),
        category=watch.get("category", "ART"),
        asking_price=watch.get("price_high"),
        currency=watch.get("currency", "EUR"),
        notes=watch.get("notes") or "",
    )
    try:
        result = run_research(inp, provider, max_queries=4, on_step=on_step)
    except Exception as exc:
        errors.append(f"搜索失败: {exc}")
        result = {"records": [], "summary": None}
    recs: List[Comparable] = result["records"]
    run["results_seen"] = len(recs)
    step(f"原始抽取 {len(recs)} 条记录")

    events: List[dict] = []
    new_items = 0
    action_alerts = 0
    seen_fps: set = set()
    baseline_prices: List[float] = []
    baseline_solds: List[float] = []
    baseline_asking: List[float] = []
    baseline_estimates = 0

    from .store import watch_items as _watch_items
    existing = {it["fingerprint"]: it for it in _watch_items(wid)}

    for rec in recs:
        if _relevance_score(rec, watch) < 25:
            continue
        fp = make_fingerprint(rec, watch)
        seen_fps.add(fp)
        tier = rec.source_tier or source_tier(rec.source_url or "")
        score, why = opportunity_score(rec, watch, watch.get("baseline", {}).get("median"))
        lvl = alert_level(score, tier)
        item = {
            "watch_id": wid, "fingerprint": fp,
            "title": rec.title[:160], "source_url": rec.source_url or "",
            "source_name": rec.source_name or _domain(rec.source_url or ""),
            "seller": rec.sold_at or "", "price": rec.price,
            "currency": rec.currency or watch.get("currency", "EUR"),
            "sale_type": rec.sale_type, "year": rec.year,
            "attributes": dict(rec.attributes or {}), "source_tier": tier,
            "opportunity_score": score, "alert_level": lvl,
        }
        item_id, is_new, old_price, old_sale = watch_item_upsert(item)
        if is_new:
            new_items += 1
            et = EV_NEW_SOLD if rec.sale_type == SALE_SOLD else EV_NEW_LISTING
            if rec.sale_type == SALE_SOLD:
                baseline_solds.append(rec.price)
            events.append({
                "watch_id": wid, "item_id": item_id, "event_type": et,
                "old_value": "", "new_value": f"{rec.price:,.0f} {rec.currency}",
                "opportunity_score": score, "alert_level": lvl,
            })
        else:
            if old_price and rec.price and rec.sale_type == SALE_ASKING:
                ratio = max(rec.price, old_price) / min(rec.price, old_price) if min(rec.price, old_price) > 0 else 0
                if ratio > 10:
                    pass
                elif rec.price < old_price * 0.995:
                    events.append({
                        "watch_id": wid, "item_id": item_id,
                        "event_type": EV_PRICE_DROP,
                        "old_value": f"{old_price:,.0f}", "new_value": f"{rec.price:,.0f}",
                        "opportunity_score": score, "alert_level": lvl,
                    })
                elif rec.price > old_price * 1.005:
                    events.append({
                        "watch_id": wid, "item_id": item_id,
                        "event_type": EV_PRICE_INCREASE,
                        "old_value": f"{old_price:,.0f}", "new_value": f"{rec.price:,.0f}",
                        "opportunity_score": score, "alert_level": lvl,
                    })
            if old_sale == SALE_ASKING and rec.sale_type == SALE_SOLD:
                events.append({
                    "watch_id": wid, "item_id": item_id, "event_type": EV_NEW_SOLD,
                    "old_value": "ASKING", "new_value": f"{rec.price:,.0f} {rec.currency}",
                    "opportunity_score": score, "alert_level": lvl,
                })
                baseline_solds.append(rec.price)

        if rec.sale_type == SALE_ASKING and rec.price:
            baseline_asking.append(rec.price)
        elif rec.sale_type == SALE_SOLD and rec.price:
            baseline_solds.append(rec.price)
        elif rec.sale_type == SALE_ESTIMATE:
            baseline_estimates += 1

    for fp, it in existing.items():
        if fp not in seen_fps:
            events.append({
                "watch_id": wid, "item_id": it["id"], "event_type": EV_REMOVED,
                "old_value": it.get("title", "")[:80], "new_value": "Listing removed, reason unknown",
                "opportunity_score": 0, "alert_level": ALERT_INFO,
            })

    if mode == "BASELINE":
        events = []

    for ev in events:
        watch_event_add(ev)
        if ev["alert_level"] == ALERT_ACTION:
            action_alerts += 1
    run["new_items"] = new_items
    run["events"] = len(events)
    run["action_alerts"] = action_alerts

    if mode == "BASELINE" or not watch.get("baseline", {}).get("median"):
        def _median(xs):
            return round(sorted(xs)[len(xs) // 2]) if xs else None
        baseline = {
            "known_listings": len(baseline_asking),
            "known_solds": len(baseline_solds),
            "known_estimates": baseline_estimates,
            "median": _median(baseline_asking) or _median(baseline_solds),
            "last_sold": baseline_solds[-1] if baseline_solds else None,
            "built_at": now_iso(),
        }
        watch_update(wid, {"baseline": baseline, "baseline_at": now_iso(), "last_checked": now_iso()})
    else:
        watch_update(wid, {"last_checked": now_iso()})

    run["duration_ms"] = int((time.time() - t0) * 1000)
    run["finished_at"] = now_iso()
    run_id = watch_run_create(run)

    step(f"{mode} SCAN 完成：{len(events)} 个变化，{action_alerts} 个 ACTION")
    return {
        "run_id": run_id, "mode": mode, "results_seen": len(recs),
        "new_items": new_items, "event_count": len(events), "action_alerts": action_alerts,
        "duration_ms": run["duration_ms"], "errors": errors,
        "events": events,
    }


# ---------------------------------------------------------------- 通知适配（20/21 节）

def notify_action(watch: dict, event: dict) -> bool:
    """Mac 本地通知。只有 ACTION 发系统通知；INTERESTING 只显示 badge；INFO 只记录。"""
    try:
        import subprocess
        title = f"Kimi Intelligence — ACTION: {watch.get('target', '')}"
        msg = event.get("new_value", "")[:120] or "新机会"
        subprocess.run([
            "osascript", "-e",
            f'display notification "{msg}" with title "{title}" sound name "Glass"',
        ], capture_output=True, timeout=10)
        return True
    except Exception:
        return False