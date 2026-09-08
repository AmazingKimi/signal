"""0.6.3 验收修复：TEAM ERR + Daily Brief + Near Miss + 漏斗拆分 + 巡视历史。

五项最终验收：
1. TEAM 页面彻底消灭 ERR（变量遮蔽修复）
2. Scout 找到 ≥1 高质量机会 → 正常 Opportunity Card
3. 找到候选但 0 个过线 → Daily Brief + Near Miss
4. 搜索完成但完全无候选 → Daily Brief 正确解释
5. 搜索故障 → 明确失败，绝不伪装成"今天没有机会"
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import SALE_ASKING, SALE_SOLD, Comparable
from app.store import scout_profile_save

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static")
INDEX_HTML = open(os.path.join(STATIC, "index.html"), encoding="utf-8").read()


@pytest.fixture(autouse=True)
def _clean_tables():
    import sqlite3
    from app.store import DB_PATH
    yield
    try:
        conn = sqlite3.connect(DB_PATH)
        for t in ("scout_feedback", "scout_candidates", "scout_runs", "scout_profiles"):
            conn.execute(f"DELETE FROM {t}")
        conn.commit()
        conn.close()
    except Exception:
        pass


def _rec(title, price, url, tier=1, sale_type=SALE_ASKING, excerpt=None):
    return Comparable(title=title, price=price, currency="EUR", source_url=url,
                      source_name="src", sale_type=sale_type,
                      price_basis="INCLUDING_PREMIUM", source_tier=tier,
                      evidence_excerpt=excerpt or title, attributes={})


def _takis(monkeypatch, records):
    from app import research as R
    monkeypatch.setattr(R, "run_research",
                        lambda inp, provider, max_queries=2, on_step=None, queries=None:
                        {"records": records, "summary": None})
    scout_profile_save({"interests": "TAKIS SIGNALS", "categories": ["ART"],
                        "budget_low": 5000, "budget_high": 500000})
    from app.scout import run_scout
    return run_scout()


# ================================================================ 验收 1：TEAM ERR 消灭

def test_team_page_variable_shadowing_fixed():
    """P0：loadTeamPage 不得用 const t 遮蔽全局 i18n t()。"""
    assert "const t = j.team" not in INDEX_HTML       # 遮蔽根源已移除
    assert "const team = j.team||{}" in INDEX_HTML or "const team = j.team || {}" in INDEX_HTML
    assert "t('team.err')" in INDEX_HTML              # 用户可读错误文案
    assert "console.error('[TEAM]" in INDEX_HTML     # 具体错误进 console


def test_no_bare_ERR_in_team_render():
    """TEAM 渲染异常不得只显示 ERR。"""
    assert "innerHTML='<p class=\"warn\">ERR</p>'" not in INDEX_HTML


# ================================================================ 验收 2：有机会 → Opportunity Card

def test_recommended_opportunity_card(monkeypatch):
    """找到 ≥1 高质量机会 → top 有卡、brief=found。"""
    recs = [
        _rec("Takis Signal from private collection, estate sale", 9500,
             "https://sothebys.com/lot/1", tier=1,
             excerpt="Takis Signal, no reserve, single owner"),
        _rec("Takis Signals, series 1", 14000, "https://artcurial.com/2", tier=1,
             sale_type=SALE_SOLD),
    ]
    result = _takis(monkeypatch, recs)
    assert result["status"] == "COMPLETED"
    assert len(result["top"]) >= 1
    assert result["brief"]["conclusion_key"] == "found"
    assert result["brief"]["recommended"] >= 1


# ================================================================ 验收 3：0 推荐但有候选 → Brief + Near Miss

def test_zero_recommended_shows_near_miss(monkeypatch):
    """验收 3：候选存在但全 <55 → brief=near_miss + Near Miss Top3（NOT_RECOMMENDED）。"""
    from app.scout import _SCORE_MIN_SHOW
    recs = [
        _rec("Takis Signal (series 1 n°66), 1968", 14432, "https://artcurial.com/1"),
        _rec("Takis Signals, private collection", 9500, "https://sothebys.com/2"),
        _rec("Panayiotis Vassilakis Signal sculpture", 12000, "https://galerie.example/3"),
        _rec("Takis kinetic artwork", 8000, "https://1stdibs.com/4", tier=2),
    ]
    result = _takis(monkeypatch, recs)
    assert result["status"] == "COMPLETED"
    assert len(result["top"]) == 0
    assert result["brief"]["conclusion_key"] == "near_miss"
    # Near Miss：分数全部低于门槛，且带 NOT_RECOMMENDED 与原因
    assert len(result["near_misses"]) >= 1
    for nm in result["near_misses"]:
        assert nm["scout_score"] < _SCORE_MIN_SHOW
        assert nm["status"] == "NOT_RECOMMENDED"
        assert nm["why_not"] and isinstance(nm["why_not"][0], dict)  # 双语原因
        assert nm["missing"]
    # Near Miss 不计入推荐数
    assert result["brief"]["recommended"] == 0


def test_near_miss_never_in_top(monkeypatch):
    """Near Miss 绝不当成机会展示。"""
    recs = [
        _rec("Takis Signals, private collection", 9500, "https://sothebys.com/2"),
        _rec("Takis Signal (series 1 n°66), 1968", 14432, "https://artcurial.com/1"),
    ]
    result = _takis(monkeypatch, recs)
    assert len(result["top"]) == 0
    for t in result["top"]:
        assert t["scout_score"] >= 55


# ================================================================ 验收 4：完全无候选 → Brief 解释

def test_empty_scan_brief(monkeypatch):
    """验收 4：搜索完成但无候选 → brief=empty_scan，不伪装。"""
    result = _takis(monkeypatch, [])
    assert result["status"] == "COMPLETED"
    assert result["brief"]["conclusion_key"] == "empty_scan"
    assert result["brief"]["records"] == 0
    assert result["brief"]["candidates"] == 0


def test_funnel_breakdown_reported(monkeypatch):
    """漏斗淘汰原因拆分：重复/已知/兴趣不匹配/来源不足/证据不足/低于门槛。"""
    from app.models import SC_STATUS_NOT_INTERESTED
    from app.store import scout_candidate_upsert
    # 已知对象（已拒绝）
    scout_candidate_upsert({"fingerprint": "attr::known1", "category": "ART",
                            "maker": "", "object": "old", "asking_price": 100,
                            "status": SC_STATUS_NOT_INTERESTED})
    recs = [
        _rec("Takis Signal, same url dup", 9500, "https://sothebys.com/dup"),
        _rec("Takis Signal, same url dup", 9500, "https://sothebys.com/dup"),
        _rec("Takis forum chatter", 6000, "https://rennlist.com/forums/1", tier=3),
        _rec("Random vase", 9999, "https://random.example/1"),
    ]
    result = _takis(monkeypatch, recs)
    f = result["funnel"]
    assert f["duplicates_removed"] >= 1
    assert f["source_removed"] >= 1            # Tier 3 → 来源不足
    assert f["profile_removed"] >= 1           # 不匹配
    assert "below_threshold" in f
    assert f["below_threshold"] == f["candidates"] - f["top_shown"]


# ================================================================ 验收 5：故障态独立

def test_failure_state_never_disguised():
    """验收 5：搜索故障 → FAILED，绝不伪装成"今天没有机会"。"""
    from app.scout import run_scout

    class NoSearch:
        name = "duckduckgo"
        available = False

    scout_profile_save({"interests": "TAKIS SIGNALS", "categories": ["ART"]})
    result = run_scout(provider=NoSearch())
    assert result["status"] == "FAILED"
    assert "brief" not in result or not result.get("brief")


# ================================================================ P1：巡视历史持久化

def test_scout_run_persisted_with_brief(monkeypatch):
    """每一次 Scout Run 持久化：brief + near_misses + funnel + search_plan + timestamp。"""
    from app.store import scout_latest_run
    recs = [
        _rec("Takis Signals, private collection", 9500, "https://sothebys.com/2"),
        _rec("Takis Signal (series 1 n°66), 1968", 14432, "https://artcurial.com/1"),
    ]
    result = _takis(monkeypatch, recs)
    run = scout_latest_run()
    assert run is not None
    assert run["status"] == "COMPLETED"
    assert run.get("brief") and run["brief"]["conclusion_key"] == "near_miss"
    assert run.get("near_misses")
    assert run.get("search_plan")
    assert run.get("started_at")


def test_scout_history_endpoint(monkeypatch):
    """GET /api/scout/history 返回巡视摘要列表（支撑 Precision 计算）。"""
    from app.main import app
    from starlette.testclient import TestClient
    _takis(monkeypatch, [_rec("Takis Signal", 9500, "https://sothebys.com/2")])
    client = TestClient(app)
    j = client.get("/api/scout/history").json()
    assert len(j["runs"]) >= 1
    r = j["runs"][0]
    assert "created_at" in r and "records" in r and "candidates" in r
    assert "recommended" in r and "subject" in r


def test_today_endpoint_returns_brief_and_near_misses(monkeypatch):
    """today 接口带出 brief + near_misses（前端渲染依据）。"""
    from app.main import app
    from starlette.testclient import TestClient
    _takis(monkeypatch, [_rec("Takis Signal", 9500, "https://sothebys.com/2")])
    client = TestClient(app)
    j = client.get("/api/scout/today").json()
    assert j["state"] in ("COMPLETED", "DONE_EMPTY")
    assert j["brief"] is not None
    assert "near_misses" in j
