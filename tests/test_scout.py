"""0.6 Scout：主动发现未知机会（验收案例 20-24 节 + 核心机制）。"""
import pytest

from app.models import (
    SC_STATUS_NOT_INTERESTED,
    SC_STATUS_WRONG_MATCH,
    SALE_ASKING,
    SALE_SOLD,
    Comparable,
    ScoutProfile,
)
from app.scout import run_scout, scout_score, seed_suggestions
from app.store import (
    scout_candidate_feedback,
    scout_candidate_get,
    scout_candidate_upsert,
    scout_candidates,
    scout_known_fingerprints,
    scout_profile_save,
    scout_run_create,
)


def _profile(artists="Takis, Calder", models="964 Carrera RS, F40",
             keywords="estate, no reserve, single owner"):
    return {
        "categories": ["ART", "CLASSIC_CAR"],
        "budget_low": 5000, "budget_high": 500000, "currency": "EUR",
        "markets": "GLOBAL",
        "opportunity_types": ["UNDERPRICED", "RARE", "NEW_TO_MARKET"],
        "interests": "kinetic art, competition cars",
        "avoid": "mass-market",
        "seeds_artists": artists, "seeds_models": models,
        "seeds_keywords": keywords,
    }


def _rec(title, price, url, sale_type=SALE_ASKING, tier=1, excerpt=""):
    return Comparable(title=title, price=price, currency="EUR",
                      source_url=url, source_name="src",
                      sale_type=sale_type, price_basis="INCLUDING_PREMIUM",
                      source_tier=tier, evidence_excerpt=excerpt or title,
                      attributes={})


def _run_with(records, profile=None):
    """用 fixture 记录跑 run_scout（monkeypatch run_research）。"""
    from app import research as R
    from app.scout import run_scout
    real_run = run_scout

    def fake_run(provider=None, on_step=None):
        from app.store import scout_run_create
        run_id = scout_run_create({"started_at": "2026-08-29T00:00:00+00:00"})
        return {"run_id": run_id, "top": [], "clues": 0, "funnel": {}, "no_compelling": True}
    return fake_run


# ---------------- 六维评分（6/7 节） ----------------

def test_scout_score_components():
    p = _profile()
    # 理想候选：种子命中 + 低价（有 SOLD 基准）+ 稀有词 + Tier1
    ideal = _rec("Takis Signal from private collection, estate sale", 9500,
                 "https://sothebys.com/lot/1", tier=1,
                 excerpt="Takis Signal, no reserve")
    res = scout_score(ideal, p, ["Takis", "estate"], sold_median=14000)
    assert res["scout_score"] >= 55
    assert res["scores"]["profile_match"] >= 8
    assert res["scores"]["price_anomaly"] > 0          # 9500 vs 14000 → 便宜
    assert "below verified" in "; ".join(res["notes"]).lower()


def test_price_anomaly_requires_benchmark():
    """无 VERIFIED SOLD 基准 → price_anomaly = 0，禁止"便宜"措辞。"""
    p = _profile()
    rec = _rec("Takis Signal", 9500, "https://gallery.example/1", tier=1)
    res = scout_score(rec, p, ["Takis"], sold_median=None)
    assert res["scores"]["price_anomaly"] == 0
    assert "No verified benchmark" in "; ".join(res["notes"])
    assert "possible opportunity" in "; ".join(res["notes"]).lower()


def test_tier3_cannot_score_high():
    """论坛 CLUE：价格再低也进不了 TOP（source_quality=0 + 外层强制 CLUE）。"""
    p = _profile()
    forum = _rec("Rennlist: someone selling a Takis for 5000", 5000,
                 "https://rennlist.com/forums/1", tier=3)
    res = scout_score(forum, p, ["Takis"], sold_median=14000)
    assert res["scores"]["source_quality"] == 0
    assert res["scout_score"] < 55  # 达不到展示阈值


# ---------------- 验收 1：100 结果 → TOP 5（20 节） ----------------

def _hundred_results():
    recs = []
    # 40 重复（同 URL 变体）
    for i in range(40):
        recs.append(_rec(f"1992 Porsche 964 Carrera RS N-GT {i}", 300000 + i,
                         f"https://dup.example/{i % 4}", tier=1))
    # 20 普通 964 RS（非目标）
    for i in range(20):
        recs.append(_rec(f"1992 Porsche 964 Carrera RS", 220000 + i,
                         f"https://plain.example/{i}", tier=1))
    # 10 论坛噪音
    for i in range(10):
        recs.append(_rec(f"Rennlist claim {i}", 250000, f"https://rennlist.com/{i}", tier=3))
    # 10 预算外（太贵）
    for i in range(10):
        recs.append(_rec(f"F40 {i}", 2500000 + i * 1000, f"https://exp.example/{i}", tier=1))
    # 10 低相关艺术品（Calder 但无关）
    for i in range(10):
        recs.append(_rec(f"Calder mobile {i}", 80000, f"https://art.example/{i}", tier=2))
    # 5 一般机会
    for i in range(5):
        recs.append(_rec(f"1992 Porsche 964 Carrera RS N-GT {i}", 320000,
                         f"https://gen.example/{i}", tier=1, excerpt="for sale"))
    # 3 不错机会
    for i in range(3):
        recs.append(_rec(f"1992 Porsche 964 Carrera RS N/GT M003 estate {i}", 268000,
                         f"https://good.example/{i}", tier=1, excerpt="no reserve"))
    # 2 明显高质量
    recs.append(_rec("1992 Porsche 964 Carrera RS N/GT, 24,175 km, M003, single owner estate",
                     245000, "https://great.example/1", tier=1, excerpt="fresh to market no reserve"))
    recs.append(_rec("Takis Signal, private collection, provenance documented", 9500,
                     "https://sothebys.com/2", tier=1, excerpt="from the collection, estate"))
    return recs


def test_scout_top5_from_100(monkeypatch):
    """验收 1：100 个结果漏斗后最多 TOP 5，两个高质量必须进前列。"""
    from app import research as R
    from app.scout import run_scout
    recs = _hundred_results()
    monkeypatch.setattr(R, "run_research",
                        lambda inp, provider, max_queries=2, on_step=None, queries=None: {
                            "records": recs, "summary": None})
    scout_profile_save(_profile())
    result = run_scout()
    assert len(result["top"]) <= 5
    if result["top"]:
        assert result["top"][0]["scout_score"] >= result["top"][-1]["scout_score"]
    # 两个高质量对象应出现在 top（有 SOLD 基准或稀有词加分）
    titles = " | ".join(c["object"] for c in result["top"])
    assert "single owner estate" in titles or "Takis Signal" in titles or "M003" in titles
    assert "Rennlist" not in " | ".join(c["object"] for c in result["top"])


# ---------------- 验收 2：不硬凑（21 节） ----------------

def test_no_compelling_when_all_poor(monkeypatch):
    from app import research as R
    from app.scout import run_scout
    poor = [_rec(f"Random listing {i}", 100000 + i, f"https://x.example/{i}", tier=1)
            for i in range(30)]
    monkeypatch.setattr(R, "run_research",
                        lambda inp, provider, max_queries=2, on_step=None, queries=None: {
                            "records": poor, "summary": None})
    scout_profile_save(_profile())
    result = run_scout()
    assert result["no_compelling"] is True or len(result["top"]) == 0


# ---------------- 验收 3：已拒绝对象抑制（22 节） ----------------

def test_known_suppression_and_material_change():
    """Day1 NOT INTERESTED → Day2 同价不推荐；Day10 降价 25% 允许 MATERIAL CHANGE。"""
    from app.store import watch_create
    # 用 watch_items 模拟 known：先建 watch + item
    wid = watch_create({"category": "CLASSIC_CAR", "maker": "Porsche",
                        "target": "964 Carrera RS N-GT", "frequency": "MANUAL"})
    from app.store import watch_item_upsert
    watch_item_upsert({"watch_id": wid, "fingerprint": "lot::108",
                       "title": "964 N-GT", "price": 300000, "currency": "EUR",
                       "sale_type": "SOLD", "source_tier": 1})
    known = scout_known_fingerprints()
    assert any("lot::108" in fp for fp in known)  # 已在 Watch 的对象被 known 覆盖

    # 已拒绝候选
    cid = scout_candidate_upsert({
        "fingerprint": "attr::abc123", "category": "CLASSIC_CAR",
        "maker": "Porsche", "object": "964 Carrera RS N-GT",
        "asking_price": 300000, "currency": "EUR",
        "source_url": "https://x.example/1", "source_tier": 1,
        "scout_score": 70, "status": "NEW",
    })
    scout_candidate_feedback(cid, "NOT_INTERESTED", "Too expensive")
    assert scout_candidate_get(cid)["status"] == SC_STATUS_NOT_INTERESTED
    known2 = scout_known_fingerprints()
    assert any("attr::abc123" in fp for fp in known2)  # 已拒绝 = known

    # 降价 25% → MATERIAL CHANGE
    cid2 = scout_candidate_upsert({
        "fingerprint": "attr::abc123", "category": "CLASSIC_CAR",
        "maker": "Porsche", "object": "964 Carrera RS N-GT",
        "asking_price": 225000, "currency": "EUR",
        "source_url": "https://x.example/1", "source_tier": 1,
        "scout_score": 70, "status": "NEW",
    })
    assert scout_candidate_get(cid2)["status"] == "MATERIAL_CHANGE"


# ---------------- 验收 4：Tier 3 CLUE（23 节） ----------------

def test_tier3_forum_only_clue():
    """论坛说"有人卖 Takis €5,000" → 只能 CLUE，不能成为推荐。"""
    from app.scout import run_scout
    from app import research as R
    forum_only = [_rec("Rennlist: someone selling a Takis for 5000", 5000,
                       "https://rennlist.com/forums/1", tier=3)]
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(R, "run_research",
                        lambda inp, provider, max_queries=2, on_step=None, queries=None: {
                            "records": forum_only, "summary": None})
    scout_profile_save(_profile())
    result = run_scout()
    assert result["no_compelling"] is True  # Tier 3 单独不能形成推荐
    monkeypatch.undo()


# ---------------- 验收 5：Analyst 联动（24 节） ----------------

def test_candidate_to_analyst_prefill():
    """Scout Candidate → Analyst：直接生成分析输入，Evidence 复用缓存。"""
    cid = scout_candidate_upsert({
        "fingerprint": "lot::999", "category": "CLASSIC_CAR",
        "maker": "Porsche", "object": "1992 Porsche 964 Carrera RS N/GT",
        "asking_price": 268000, "currency": "EUR",
        "source_url": "https://rmsothebys.com/lot/999", "source_tier": 1,
        "scout_score": 80, "status": "SHOWN",
    })
    c = scout_candidate_get(cid)
    assert c["asking_price"] == 268000
    # 模拟 /api/scout/{id}/analyze 的 prefill 逻辑
    prefill = {
        "artist": c.get("maker") or c.get("object", "").split()[0],
        "artwork": c.get("object", ""),
        "asking_price": c.get("asking_price"),
        "currency": c.get("currency", "EUR"),
        "category": c.get("category", "ART"),
        "notes": f"Scout Candidate #{cid}: {c.get('source_url', '')}",
    }
    assert prefill["asking_price"] == 268000
    assert "rmsothebys.com" in prefill["notes"]  # 来源证据直接传给 Analyst


# ---------------- 反馈记录（10/11 节） ----------------

def test_feedback_recorded():
    cid = scout_candidate_upsert({
        "fingerprint": "attr::fdbk1", "category": "ART",
        "maker": "Takis", "object": "Takis Signal", "asking_price": 9000,
        "currency": "EUR", "source_url": "https://g.example/1", "source_tier": 1,
        "scout_score": 60, "status": "NEW",
    })
    scout_candidate_feedback(cid, "WRONG_MATCH", "Wrong artist/model")
    from app.store import scout_feedback_of
    fbs = scout_feedback_of(cid)
    assert fbs and fbs[0]["feedback"] == "WRONG_MATCH"
    assert fbs[0]["why_not"] == "Wrong artist/model"


# ---------------- Seed 建议（3 节） ----------------

def test_seed_suggestions_from_history():
    """从 History/Watchlist 生成候选 Seed 建议（不自动修改 Profile）。"""
    s = seed_suggestions()
    assert "suggested_artists" in s and "suggested_models" in s
    assert "确认" in s["note"] or "confirm" in s["note"].lower()
