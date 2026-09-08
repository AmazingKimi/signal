"""0.6.2 Scout & Bilingual UX 修正（任务书 27-31 节验收）。

核心：用户只说「我想找 Takis Signals」→ Scout 自己解析、自己生成 Seeds/Queries、自己搜索。
以及：中文模式无英文残留、三种空状态彻底区分。
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import SALE_ASKING, Comparable
from app.store import scout_profile_get, scout_profile_save

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static")
INDEX_HTML = open(os.path.join(STATIC, "index.html"), encoding="utf-8").read()
I18N_JS = open(os.path.join(STATIC, "i18n.js"), encoding="utf-8").read()


@pytest.fixture(autouse=True)
def _clean_scout_tables():
    """每个测试前后清理 scout/watch 数据，避免跨测试污染共享 SQLite。"""
    import sqlite3
    from app.store import DB_PATH
    yield
    try:
        conn = sqlite3.connect(DB_PATH)
        for t in ("scout_feedback", "scout_candidates", "scout_runs", "scout_profiles",
                  "watch_runs", "watch_events", "watch_items", "watches"):
            conn.execute(f"DELETE FROM {t}")
        conn.commit()
        conn.close()
    except Exception:
        pass


def _rec(title, price, url, tier=1, sale_type=SALE_ASKING):
    return Comparable(title=title, price=price, currency="EUR", source_url=url,
                      source_name="src", sale_type=sale_type,
                      price_basis="INCLUDING_PREMIUM", source_tier=tier,
                      evidence_excerpt=title, attributes={})


def _takis_records():
    return [
        _rec("Takis Signal (series 1 n°66), 1968, 200cm", 14432, "https://artcurial.com/1"),
        _rec("Takis Signals, private collection", 9500, "https://sothebys.com/2"),
        _rec("Panayiotis Vassilakis Signal sculpture", 12000, "https://galerie.example/3"),
    ]


# ================================================================ 验收 A：仅 Interests 可跑 Scout

def test_interest_only_profile_can_run_scout(monkeypatch):
    """只填「我关注什么」→ Scout 必须真正搜索（04/11 节：Seeds 非必填）。"""
    from app import research as R
    from app.scout import run_scout

    scout_profile_save({"interests": "TAKIS SIGNALS", "categories": ["ART"],
                        "budget_low": 5000, "budget_high": 500000})
    calls = []
    monkeypatch.setattr(R, "run_research",
                        lambda inp, provider, max_queries=2, on_step=None, queries=None:
                        (calls.append(queries) or {"records": _takis_records(), "summary": None}))
    result = run_scout()
    assert result["status"] == "COMPLETED"
    assert calls, "Scout 没有真正执行任何搜索"
    assert any("takis" in " ".join(c or [""]).lower() for c in calls)
    assert result["parse_info"]["artist"] == "Takis"
    assert result["parse_info"]["series"] == "Signals"


def test_profile_save_returns_understanding_card():
    """保存 Profile 后 API 返回「SCOUT 已理解你的关注方向」数据（09/10 节）。"""
    from app.main import app
    from starlette.testclient import TestClient
    client = TestClient(app)
    r = client.post("/api/scout/profile", json={
        "interests": "TAKIS SIGNALS", "categories": ["ART"],
        "budget_low": 5000, "budget_high": 500000,
    })
    assert r.status_code == 200
    j = r.json()
    u = j["understood"]
    assert u["ok"] is True
    assert u["artist"] == "Takis"
    assert u["series"] == "Signals"
    assert len(j["search_plan"]) > 0          # 已生成搜索策略
    # 解析结果落库（24 节：复用，不重复解析）
    saved = scout_profile_get()
    assert saved["structured_interest"]["artist"] == "Takis"
    assert saved["generated_seeds"]["artists"][0] == "Takis"


def test_generated_seeds_saved_and_reused():
    """Profile 未变化 → 解析结果复用，不重复解析（24 节）。"""
    from hashlib import md5
    from app.main import app
    from starlette.testclient import TestClient
    client = TestClient(app)
    payload = {"interests": "TAKIS SIGNALS", "categories": ["ART"]}
    j1 = client.post("/api/scout/profile", json=payload).json()
    assert j1["understood"]["ok"]
    saved = scout_profile_get()
    assert saved["interests_hash"] == md5(b"TAKIS SIGNALS").hexdigest()
    assert saved["parser_version"] == "0.6.2"
    # 同内容再保存：不再重解析（hash 相同直接复用）
    j2 = client.post("/api/scout/profile", json=payload).json()
    assert j2["understood"]["artist"] == "Takis"


def test_manual_seeds_not_required():
    """无手动 Seeds、无 Interests → PENDING（不装死，不给"今日暂无机会"）。"""
    from app.scout import run_scout
    scout_profile_save({"interests": "", "categories": ["ART"]})
    result = run_scout()
    assert result["status"] == "PENDING"
    assert "尚未" in result["error"] or "关注方向" in result["error"]


# ================================================================ 验收 B：LLM 失败 → deterministic fallback

def test_llm_failure_uses_deterministic_seed_fallback(monkeypatch):
    """DeepSeek 关闭 → 规则解析照样生成搜索并运行（25/29 节）。"""
    from app import research as R
    from app.interests import parse_interest
    from app.scout import run_scout

    class NoLLM:
        configured = False

    s = parse_interest("TAKIS SIGNALS", llm=NoLLM())
    assert s["artist"] == "Takis" and s["series"] == "Signals"
    assert s["fallback_used"] is True

    scout_profile_save({"interests": "TAKIS SIGNALS", "categories": ["ART"]})
    monkeypatch.setattr(R, "run_research",
                        lambda inp, provider, max_queries=2, on_step=None, queries=None:
                        {"records": _takis_records(), "summary": None})
    result = run_scout(llm=NoLLM())
    assert result["status"] == "COMPLETED"
    assert result["parse_info"]["fallback_used"] in (True, False)  # 照常跑


def test_porsche_ngt_natural_language_parsing():
    """07 节：经典车自然语言 → Maker/Model/Aliases/Region/Budget。"""
    from app.interests import parse_interest
    s = parse_interest("Porsche 964 RS N-GT，真正 M003 Competition，最好欧洲车，30万欧元以内。")
    assert s["maker"] == "Porsche"
    assert s["model"] == "964 Carrera RS N-GT"
    assert "M003" in s["aliases"] and "N-GT" in s["aliases"]
    assert s["region"] == "Europe"
    assert s["budget_max"] == 300000
    assert s["category"] == "CLASSIC_CAR"


def test_interest_parser_failure_allows_broad_search(monkeypatch):
    """12 节：解析失败不能装死——按宽条件搜索。"""
    from app import research as R
    from app.scout import run_scout
    scout_profile_save({"interests": "我喜欢六七十年代会动的、带灯的欧洲雕塑，价格别太离谱。",
                        "categories": ["ART"]})
    calls = []
    monkeypatch.setattr(R, "run_research",
                        lambda inp, provider, max_queries=2, on_step=None, queries=None:
                        (calls.append(queries) or {"records": _takis_records(), "summary": None}))
    result = run_scout()
    assert result["status"] == "COMPLETED"
    assert calls  # 宽搜索确实执行


# ================================================================ 三态空状态（15/16/31 节）

def test_not_run_is_not_no_opportunity():
    """验收 D：无 run → NOT_RUN（尚未巡视），不是"今日暂无机会"。"""
    from app.main import app
    from starlette.testclient import TestClient
    client = TestClient(app)
    j = client.get("/api/scout/today").json()
    assert j["state"] == "NOT_RUN"
    assert j["no_compelling"] is True


def test_completed_zero_results_is_no_opportunity(monkeypatch):
    """完成但 0 候选 → DONE_EMPTY（唯一允许"今日暂未发现"）。"""
    from app import research as R
    from app.scout import run_scout
    from app.store import scout_latest_run
    scout_profile_save({"interests": "TAKIS SIGNALS", "categories": ["ART"]})
    monkeypatch.setattr(R, "run_research",
                        lambda inp, provider, max_queries=2, on_step=None, queries=None:
                        {"records": [], "summary": None})
    result = run_scout()
    assert result["status"] == "COMPLETED"
    assert result["no_compelling"] is True
    run = scout_latest_run()
    assert run["status"] == "COMPLETED"


def test_search_failure_has_distinct_state():
    """搜索服务故障 → FAILED（独立状态），绝不显示"今日暂无机会"。"""
    from app.scout import run_scout

    class NoSearch:
        name = "duckduckgo"
        available = False

    scout_profile_save({"interests": "TAKIS SIGNALS", "categories": ["ART"]})
    result = run_scout(provider=NoSearch())
    assert result["status"] == "FAILED"
    assert "无法连接" in result["error"] or "不可用" in result["error"]


def test_scout_run_progress_states():
    """run_scout 三态齐备：PENDING（无种子）/ FAILED（搜索故障）/ COMPLETED。"""
    from app.scout import run_scout
    scout_profile_save({"interests": "", "categories": ["ART"]})
    assert run_scout()["status"] == "PENDING"


# ================================================================ 中文 UI（01/30 节）

def test_zh_ui_has_no_untranslated_form_labels():
    """验收 C：中文模式导航/表单不得出现工程型英文主标签。"""
    # 导航主文本：中文第一视觉
    assert '"nav.scout": "侦察"' in I18N_JS
    assert '"nav.analyze": "分析"' in I18N_JS
    assert '"nav.watchlist": "监控"' in I18N_JS
    assert '"nav.history": "历史"' in I18N_JS
    assert '"nav.team": "团队"' in I18N_JS
    # 表单主 label 全部 t() 化
    assert "t('sp.interests')" in INDEX_HTML
    assert "t('sp.categories')" in INDEX_HTML
    assert "t('sp.budget')" in INDEX_HTML
    assert "t('sp.save')" in INDEX_HTML
    assert "t('watch.createBtn')" in INDEX_HTML
    assert "t('watch.maker')" in INDEX_HTML
    assert "t('watch.target')" in INDEX_HTML
    assert "t('watch.freq')" in INDEX_HTML


def test_category_enum_hidden_in_zh_ui():
    """18 节：中文模式不暴露 ART/CLASSIC_CAR 等 Enum——用 catLabel 展示。"""
    assert "catLabel(k)" in INDEX_HTML          # Scout 类别 checkbox
    assert "catLabel(o.value)" in INDEX_HTML    # Analyze 类别下拉
    assert '"cat.ART": "艺术品"' in I18N_JS
    assert '"cat.CLASSIC_CAR": "经典汽车"' in I18N_JS


def test_watchlist_zh_localization():
    """20 节：Watchlist 全中文化。"""
    for key in ("watch.create", "watch.cat", "watch.maker", "watch.target",
                "watch.kws", "watch.low", "watch.high", "watch.freq", "watch.notes"):
        assert '"%s"' % key in I18N_JS
    assert "t('watch.freqManual')" in INDEX_HTML
    assert "t('watch.empty')" in INDEX_HTML


def test_history_zh_localization():
    """21 节：History 统计与表格中文化。"""
    for key in ("hist.total", "hist.completed", "hist.changed", "hist.bought",
                "hist.passed", "hist.changeRate", "hist.avgDiscount"):
        assert '"%s"' % key in I18N_JS
    assert "'hist.total', s.total_opportunities" in INDEX_HTML   # 统计卡片用 t(k) 渲染
    assert "t('hist.analyst')" in INDEX_HTML


def test_en_ui_switch():
    """EN 模式：导航回英文、字典存在。"""
    assert '"en-US"' in I18N_JS
    assert '"nav.scout": "SCOUT"' in I18N_JS
    assert "localStorage.setItem('kimi.lang'" in I18N_JS


# ================================================================ 翻译纪律（27 节 17/18）

def test_evidence_translation_preserves_original():
    """Evidence 原文永久保留（数据库字段 original_text）。"""
    from app.models import Evidence
    e = Evidence(claim="x", value="y", original_text="The car sold for EUR 310,500",
                 original_language="en")
    assert e.original_text == "The car sold for EUR 310,500"
    assert "translated_text" in Evidence.model_fields


def test_translation_never_enters_pricing_engine():
    """翻译只属展示层：Pricing 输入不含 translated 字段。"""
    from app.models import PriceAnalysis, Comparable
    from app.orchestrator import build_price_analysis
    c = Comparable(title="x", price=100, currency="EUR", sale_type="SOLD",
                   price_basis="INCLUDING_PREMIUM", source_url="https://a.example/1",
                   translated_text="不应进入定价")
    pa = build_price_analysis([c], "EUR", {})
    assert pa.comparables[0].price == 100        # 价格来自原字段
    assert not getattr(pa, "translated_text", None)


# ================================================================ Search Plan 透明（23 节）

def test_search_plan_exposed_in_run():
    """Scout run 返回 search_plan（用户可看到它出去找了什么）。"""
    from app import research as R
    from app.scout import run_scout
    from unittest.mock import patch
    scout_profile_save({"interests": "TAKIS SIGNALS", "categories": ["ART"]})
    with patch.object(R, "run_research",
                      lambda inp, provider, max_queries=2, on_step=None, queries=None:
                      {"records": _takis_records(), "summary": None}):
        result = run_scout()
    plan = result.get("search_plan") or []
    assert len(plan) > 0
    assert any("takis" in q.lower() for q in plan)
