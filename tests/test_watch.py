"""0.5 Watchlist + Opportunity Monitor：验收案例（27-32 节）与核心机制。"""
import pytest

from app.models import (
    ALERT_ACTION,
    ALERT_INFO,
    ALERT_INTERESTING,
    ALERT_IGNORE,
    EV_NEW_LISTING,
    EV_NEW_SOLD,
    EV_PRICE_DROP,
    EV_REMOVED,
    SALE_ASKING,
    SALE_SOLD,
    WATCH_STATUS_ACTIVE,
    WATCH_STATUS_PAUSED,
    Comparable,
)
from app.store import (
    watch_create,
    watch_delete,
    watch_event_add,
    watch_get,
    watch_item_upsert,
    watch_events,
    watch_items,
    watch_price_history,
    watch_update,
)
from app.watch import (
    alert_level,
    make_fingerprint,
    opportunity_score,
    run_watch,
)


def _watch(category="CLASSIC_CAR", maker="Porsche", target="964 Carrera RS N-GT",
           keywords="N-GT, NGT, M003, Carrera RS Competition",
           price_low=200000, price_high=350000, frequency="MANUAL"):
    return {
        "category": category, "maker": maker, "target": target, "keywords": keywords,
        "price_low": price_low, "price_high": price_high, "currency": "EUR",
        "markets": "GLOBAL", "frequency": frequency, "notes": "Only genuine N-GT / M003 cars.",
        "status": WATCH_STATUS_ACTIVE,
    }


def rec(title, price, url="https://rmsothebys.com/lot/1", sale_type=SALE_ASKING,
        year="1992", tier=1, excerpt=""):
    return Comparable(title=title, price=price, currency="EUR", year=year,
                      source_url=url, source_name="source",
                      sale_type=sale_type, price_basis="INCLUDING_PREMIUM",
                      source_tier=tier, evidence_excerpt=excerpt or title,
                      attributes={})


# ---------------- CRUD（32 节） ----------------

def test_watch_crud():
    wid = watch_create(_watch())
    w = watch_get(wid)
    assert w["target"] == "964 Carrera RS N-GT"
    assert w["status"] == WATCH_STATUS_ACTIVE
    assert w["baseline"] == {}  # 尚未 baseline
    watch_update(wid, {"status": WATCH_STATUS_PAUSED, "notes": "paused"})
    assert watch_get(wid)["status"] == WATCH_STATUS_PAUSED
    watch_delete(wid)
    assert watch_get(wid) is None


# ---------------- Baseline（05 节） ----------------

def test_baseline_creation(monkeypatch):
    wid = watch_create(_watch())
    from app import research as R
    monkeypatch.setattr(R, "run_research", lambda inp, provider, max_queries=4, on_step=None: {
        "records": [rec("1992 Porsche 964 Carrera RS N-GT", 325000, url="https://a.example/1"),
                    rec("1992 Porsche 964 Carrera RS N-GT", 300000, url="https://b.example/2"),
                    rec("1992 Porsche 964 Carrera RS N/GT", 310000, url="https://c.example/3")],
        "summary": None,
    })
    s = run_watch(wid)
    assert s["mode"] == "BASELINE"
    w = watch_get(wid)
    assert w["baseline_at"] is not None
    assert w["baseline"]["known_listings"] == 3
    assert w["baseline"]["median"] == 310000  # 排序后中位数
    assert len(s["events"]) == 0  # baseline 不产生变化事件
    assert s["event_count"] == 0
    assert s["new_items"] == 3
    assert watch_items(wid)  # items 已入库


# ---------------- New Listing（27 节验收 1） ----------------

def test_new_listing_detected(monkeypatch):
    wid = watch_create(_watch())
    from app import research as R
    baseline_recs = [rec("1992 Porsche 964 Carrera RS N-GT", 325000, url="https://a.example/1")]
    monkeypatch.setattr(R, "run_research", lambda inp, provider, max_queries=4, on_step=None: {
        "records": baseline_recs, "summary": None})
    run_watch(wid)  # baseline
    # 第二次：出现一辆新的 N-GT €290,000
    new_recs = baseline_recs + [rec("NEW 1992 Porsche 964 Carrera RS N-GT", 290000,
                                    url="https://d.example/4")]
    monkeypatch.setattr(R, "run_research", lambda inp, provider, max_queries=4, on_step=None: {
        "records": new_recs, "summary": None})
    s = run_watch(wid)
    assert s["mode"] == "CHECK"
    assert s["new_items"] == 1
    assert s["event_count"] == len(s["events"])
    types = [e["event_type"] for e in s["events"]]
    assert EV_NEW_LISTING in types
    # 新 listing 是 N-GT + 低价 + Tier1 → 应该够 INTERESTING 以上
    ev = [e for e in s["events"] if e["event_type"] == EV_NEW_LISTING][0]
    assert ev["alert_level"] in (ALERT_INTERESTING, ALERT_ACTION)


# ---------------- Price Drop（28 节验收 2） ----------------

def test_price_drop_detected(monkeypatch):
    wid = watch_create(_watch())
    from app import research as R
    first = [rec("1992 Porsche 964 Carrera RS N-GT", 325000, url="https://a.example/1")]
    monkeypatch.setattr(R, "run_research", lambda inp, provider, max_queries=4, on_step=None: {
        "records": first, "summary": None})
    run_watch(wid)
    # 第二次：同一辆车降价到 €295,000
    second = [rec("1992 Porsche 964 Carrera RS N-GT", 295000, url="https://a.example/1")]
    monkeypatch.setattr(R, "run_research", lambda inp, provider, max_queries=4, on_step=None: {
        "records": second, "summary": None})
    s = run_watch(wid)
    drops = [e for e in s["events"] if e["event_type"] == EV_PRICE_DROP]
    assert len(drops) == 1
    assert drops[0]["old_value"] == "325,000"
    assert drops[0]["new_value"] == "295,000"
    # 不会生成两个 Opportunity（同一 fingerprint）
    assert len(watch_items(wid)) == 1
    # 价格历史 append-only
    hist = watch_price_history(watch_items(wid)[0]["id"])
    assert any(h["old"] == "325,000" and h["new"] == "295,000" for h in hist)


# ---------------- Listing Removed（06 节） ----------------

def test_removed_detected(monkeypatch):
    wid = watch_create(_watch())
    from app import research as R
    two = [rec("1992 Porsche 964 Carrera RS N-GT", 325000, url="https://a.example/1"),
           rec("1992 Porsche 964 Carrera RS N-GT", 289000, url="https://b.example/2")]
    monkeypatch.setattr(R, "run_research", lambda inp, provider, max_queries=4, on_step=None: {
        "records": two, "summary": None})
    run_watch(wid)
    # 第二次：一辆消失
    one = [rec("1992 Porsche 964 Carrera RS N-GT", 325000, url="https://a.example/1")]
    monkeypatch.setattr(R, "run_research", lambda inp, provider, max_queries=4, on_step=None: {
        "records": one, "summary": None})
    s = run_watch(wid)
    removed = [e for e in s["events"] if e["event_type"] == EV_REMOVED]
    assert len(removed) == 1
    assert "reason unknown" in removed[0]["new_value"].lower()  # 下架 ≠ 成交


# ---------------- Fingerprint / 去重（07 节） ----------------

def test_fingerprint_dedupes_cross_site():
    """同一辆车（同 lot）被不同站收录 → 同一指纹。"""
    a = rec("1992 Porsche 964 Carrera RS N-GT - Lot 108", 310000, url="https://rmsothebys.com/1")
    b = rec("Porsche 964 RS N/GT, lot 108 result", 310000, url="https://classic.com/2")
    assert make_fingerprint(a, _watch()) == make_fingerprint(b, _watch())


def test_fingerprint_stable_for_same_item():
    w = _watch()
    a = rec("1992 Porsche 964 Carrera RS N-GT", 325000, url="https://a.example/1")
    b = rec("1992 Porsche 964 Carrera RS N-GT", 295000, url="https://a.example/1")
    assert make_fingerprint(a, w) == make_fingerprint(b, w)  # 降价前后同一对象


def test_fingerprint_distinguishes_different_items():
    w = _watch()
    a = rec("1992 Porsche 964 Carrera RS N-GT", 325000, url="https://a.example/1")
    b = rec("1993 Porsche 964 Carrera RS N-GT", 325000, url="https://b.example/2")
    assert make_fingerprint(a, w) != make_fingerprint(b, w)


# ---------------- Tier 3 限制（29 节验收 3） ----------------

def test_tier3_forum_never_action():
    """Rennlist 论坛："Someone says one sold for €250k" → 最多 INFO，绝无 ACTION。"""
    forum = rec("Rennlist: Someone says a 964 RS N-GT sold for 250000", 250000,
                url="https://rennlist.com/forums/1", sale_type=SALE_SOLD, tier=3)
    score, _ = opportunity_score(forum, _watch(), baseline_median=300000)
    assert alert_level(score, 3) == ALERT_INFO or alert_level(score, 3) == ALERT_IGNORE
    assert alert_level(score, 3) != ALERT_ACTION
    # 即使价格极低（吸引力满分），Tier 3 也压死在 INFO
    cheap = rec("Rennlist: sold 100000", 100000, url="https://rennlist.com/forums/2",
                sale_type=SALE_SOLD, tier=3)
    s2, _ = opportunity_score(cheap, _watch(), baseline_median=300000)
    assert alert_level(s2, 3) == ALERT_INFO


def test_tier3_never_in_pricing_pool():
    """0.4.1 Evidence Gate 回归：论坛记录不进定价池（29 节）。"""
    from app.comparability import classify_pool
    forum = rec("Rennlist claim", 250000, url="https://rennlist.com/forums/1",
                sale_type=SALE_SOLD, tier=3)
    assert classify_pool(forum, "EUR") is None


# ---------------- 普通 964 RS 不 ACTION（31 节验收 5） ----------------

def test_plain_rs_not_action():
    """Watch 是 N-GT，出现普通 964 Carrera RS → 最多 INFO。"""
    plain = rec("1992 Porsche 964 Carrera RS", 200000, url="https://dealer.example/1",
                sale_type=SALE_ASKING, tier=1)
    score, _ = opportunity_score(plain, _watch(), baseline_median=300000)
    assert alert_level(score, 1) in (ALERT_IGNORE, ALERT_INFO)
    assert alert_level(score, 1) != ALERT_ACTION


# ---------------- 新拍卖成交（30 节验收 4） ----------------

def test_new_sold_tier1_interesting_plus():
    sold = rec("1992 Porsche 964 Carrera RS N/GT - RM Sotheby's Paris 2025", 307500,
               url="https://rmsothebys.com/lot/108", sale_type=SALE_SOLD, tier=1)
    score, _ = opportunity_score(sold, _watch(), baseline_median=320000)
    lvl = alert_level(score, 1)
    assert lvl in (ALERT_INTERESTING, ALERT_ACTION)
    assert lvl != ALERT_IGNORE


# ---------------- 评分与告警阈值（09/10 节） ----------------

def test_opportunity_score_components():
    w = _watch()
    # 理想机会：目标版本 + 低价 + Tier1 + 字段齐全
    ideal = rec("1992 Porsche 964 Carrera RS N/GT M003, Maritime Blue, 24,175 km", 278000,
                url="https://rmsothebys.com/lot/9", sale_type=SALE_ASKING, tier=1)
    ideal.attributes = {"color": "Maritime Blue", "mileage": "24,175 km"}
    score, why = opportunity_score(ideal, w, baseline_median=320000)
    assert score >= 60
    assert "版本命中" in why or "N-GT" in why
    # 普通车：低分
    poor = rec("1991 Porsche 911 Carrera", 400000, url="https://x.example/1",
               sale_type=SALE_ASKING, tier=2)
    s2, _ = opportunity_score(poor, w, baseline_median=320000)
    assert s2 < 45


def test_alert_thresholds():
    assert alert_level(80, 1) == ALERT_ACTION
    assert alert_level(60, 1) == ALERT_INTERESTING
    assert alert_level(30, 1) == ALERT_INFO
    assert alert_level(10, 1) == ALERT_IGNORE
    assert alert_level(80, 3) == ALERT_INFO  # Tier 3 强制降级
    assert alert_level(80, 2) == ALERT_ACTION


# ---------------- Pause（25 节） ----------------

def test_paused_watch_not_running(monkeypatch):
    wid = watch_create(_watch())
    watch_update(wid, {"status": WATCH_STATUS_PAUSED})
    s = run_watch(wid)
    assert s.get("paused") is True


# ---------------- Monitor → Analyst Handoff（14 节） ----------------

def test_monitor_to_analyst_handoff():
    """Watch 发现的对象能直接进 0.4 分析管线（ANALYZE OPPORTUNITY）。"""
    from app.orchestrator import analyze
    from app.models import AnalysisInput
    inp = AnalysisInput(
        artist="Porsche", artwork="964 Carrera RS N-GT", asking_price=289000,
        currency="EUR", category="CLASSIC_CAR", year="1992", research=False,
        comparables=[rec("1992 Porsche 964 Carrera RS N/GT", 310000,
                         url="https://rmsothebys.com/lot/1", sale_type=SALE_SOLD)],
    )
    report = analyze(inp)
    assert report.decision in ("BUY", "NEGOTIATE", "WATCH", "PASS")
    assert report.brief is not None  # 完整 Brief


# ---------------- 端到端：两次 run 全链路（27 节完整验收） ----------------

def test_full_monitor_cycle(monkeypatch):
    wid = watch_create(_watch())
    from app import research as R
    # Baseline：3 条已知
    monkeypatch.setattr(R, "run_research", lambda inp, provider, max_queries=4, on_step=None: {
        "records": [rec("1992 Porsche 964 Carrera RS N-GT", 325000, url="https://a.example/1"),
                    rec("1992 Porsche 964 Carrera RS N-GT", 310000, url="https://b.example/2")],
        "summary": None})
    b = run_watch(wid)
    assert b["mode"] == "BASELINE" and len(b["events"]) == 0
    # CHECK：新 N-GT €290k + 旧车降价 + 论坛噪音 + 普通 RS
    monkeypatch.setattr(R, "run_research", lambda inp, provider, max_queries=4, on_step=None: {
        "records": [
            rec("1992 Porsche 964 Carrera RS N-GT", 325000, url="https://a.example/1"),
            rec("1992 Porsche 964 Carrera RS N-GT", 290000, url="https://c.example/3"),   # 新 listing 低价
            rec("Rennlist forum says sold 250k", 250000, url="https://rennlist.com/forums/1",
                sale_type=SALE_SOLD, tier=3),                                             # 论坛噪音
            rec("1992 Porsche 964 Carrera RS", 220000, url="https://d.example/4"),        # 普通 RS
        ],
        "summary": None})
    s = run_watch(wid)
    types = [e["event_type"] for e in s["events"]]
    assert EV_NEW_LISTING in types
    action = [e for e in s["events"] if e["alert_level"] == ALERT_ACTION]
    # 论坛噪音绝不能 ACTION
    forum_ev = [e for e in s["events"] if e.get("item_id") and
                any(it["source_url"] for it in watch_items(wid)
                    if it["id"] == e["item_id"] and "rennlist" in it["source_url"])]
    assert all(e["alert_level"] != ALERT_ACTION for e in forum_ev)
    # 普通 RS 不 ACTION
    assert all(e["alert_level"] != ALERT_ACTION for e in s["events"]
               if "Carrera RS\"" in str(e.get("new_value", "")) or
               any(it["id"] == e.get("item_id") and "Carrera RS" in it["title"]
                   and "N-GT" not in it["title"] for it in watch_items(wid)))


# ---------------- 噪声守卫（实测发现的真实问题） ----------------

def test_noise_price_jump_not_a_change(monkeypatch):
    """同一对象价格跳变 10 倍以上（页面杂音数字）→ 不产生 PRICE 事件。"""
    wid = watch_create(_watch())
    from app import research as R
    first = [rec("1992 Porsche 964 Carrera RS N-GT", 368000, url="https://a.example/1")]
    monkeypatch.setattr(R, "run_research", lambda inp, provider, max_queries=4, on_step=None: {
        "records": first, "summary": None})
    run_watch(wid)
    # 第二次：同一 URL 抽到杂音数字 3,680,001
    second = [rec("1992 Porsche 964 Carrera RS N-GT", 3680001, url="https://a.example/1")]
    monkeypatch.setattr(R, "run_research", lambda inp, provider, max_queries=4, on_step=None: {
        "records": second, "summary": None})
    s = run_watch(wid)
    assert all(e["event_type"] not in ("PRICE_DROP", "PRICE_INCREASE") for e in s["events"])


def test_estimate_noise_not_action():
    """ESTIMATE（估价）记录评分封顶 30 → 不可能 ACTION。"""
    est = rec("1992 Porsche 964 Carrera RS N/GT estimate", 7500,
              url="https://bringatrailer.com/1", sale_type="ESTIMATE", tier=1)
    score, _ = opportunity_score(est, _watch(), baseline_median=300000)
    assert score <= 30
    assert alert_level(score, 1) == ALERT_INFO


def test_magnitude_noise_capped():
    """价格量级离谱（聚合页总市值）→ 评分封顶 35。"""
    junk = rec("Porsche 964 Carrera RS - market page", 37285000,
               url="https://classic.com/market", sale_type=SALE_ASKING, tier=2)
    score, _ = opportunity_score(junk, _watch(), baseline_median=300000)
    assert score <= 35
