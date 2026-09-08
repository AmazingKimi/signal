"""0.4 验收：Decision Engine + Opportunity Brief + Decision Memory（任务书第十六节）。"""
from app.decision import build_brief, decide, grade_risks
from app.models import (
    BASIS_INCLUDING_PREMIUM,
    DECISION_BUY,
    DECISION_NEGOTIATE,
    DECISION_PASS,
    DECISION_WATCH,
    AnalysisInput,
    Comparable,
)
from app.orchestrator import analyze
from app.store import (
    get_report,
    history,
    history_stats,
    save_decision_memory,
    save_report,
)


def comp(price, currency="EUR", url="https://x.example/1", sale_type="SOLD",
         price_basis=BASIS_INCLUDING_PREMIUM, comparability="HIGH"):
    return Comparable(title="Signals", price=price, currency=currency,
                      source_name="测试来源", source_url=url, is_auction=True,
                      sale_type=sale_type, price_basis=price_basis,
                      comparability=comparability)


# ---- Test 1：没有 verified comparables -> WATCH ----
def test_no_comparables_is_watch():
    inp = AnalysisInput(artist="X", asking_price=10000, currency="EUR")
    report = analyze(inp)
    assert report.decision == DECISION_WATCH
    assert report.conclusion == "观察"


# ---- Test 2：Asking > Walk-away -> PASS / NEGOTIATE ----
def test_asking_above_walkaway_is_pass():
    inp = AnalysisInput(
        artist="X", asking_price=500000, currency="EUR",
        comparables=[comp(14432), comp(17712)],  # fair ~8k-14k, walk ~14k
    )
    report = analyze(inp)
    assert report.decision == DECISION_PASS
    assert report.conclusion == "放弃"


# ---- Test 3：Fair Range 内 + 高 Confidence -> BUY/NEGOTIATE ----
def test_fair_range_high_conf_buy():
    inp = AnalysisInput(
        artist="X", asking_price=10000, currency="EUR",
        comparables=[comp(14432), comp(17712), comp(20000)],
    )
    report = analyze(inp)
    assert report.decision in (DECISION_BUY, DECISION_NEGOTIATE)
    assert report.price.confidence >= 60  # 3 条 HIGH -> 65


def test_asking_in_range_is_negotiate():
    inp = AnalysisInput(
        artist="X", asking_price=15000, currency="EUR",
        comparables=[comp(14432), comp(17712)],
    )
    report = analyze(inp)
    assert report.decision == DECISION_NEGOTIATE


# ---- Brief：Top3 证据 + Top3 风险 + Next Action ----
def test_brief_structure():
    inp = AnalysisInput(
        artist="X", artwork="964 Carrera RS N-GT", asking_price=15000, currency="EUR",
        category="CLASSIC_CAR", mileage="24,175 km",
        comparables=[comp(14432), comp(17712)],
    )
    report = analyze(inp)
    b = report.brief
    assert b is not None
    assert b.decision == report.decision
    assert len(b.top_evidence) >= 1          # 至少有定价池可比
    assert len(b.top_risks) >= 1
    assert b.next_action                      # Next Action 不能为空
    # Next Action 必须具体：不含"建议进一步研究"这类空话
    assert "进一步研究" not in b.next_action
    # 风险按严重程度排序（第一个 <= 第二个的权重）
    assert b.top_risks[0].order <= b.top_risks[-1].order
    # 有经典车风险（Matching Numbers）
    assert any("Matching Numbers" in r.text for r in report.risks)


def test_next_action_mentions_opening_offer_when_negotiate():
    inp = AnalysisInput(
        artist="X", artwork="964 Carrera RS N-GT", asking_price=15000, currency="EUR",
        comparables=[comp(14432), comp(17712)],
    )
    report = analyze(inp)
    if report.decision == DECISION_NEGOTIATE:
        low = report.brief.next_action.lower()
        assert "opening" in low or "开价" in low


# ---- Test 4：Decision Memory 不覆盖原始报告 ----
def test_decision_memory_does_not_overwrite_report():
    inp = AnalysisInput(
        artist="X", asking_price=15000, currency="EUR",
        comparables=[comp(14432), comp(17712)],
        initial_decision="BUY", initial_planned_offer=9000,
    )
    report = analyze(inp)
    assert report.decision == DECISION_NEGOTIATE
    saved = save_report(report)
    rid = saved.id

    dm = {
        "report_id": rid,
        "initial_decision": "BUY",
        "initial_offer": 9000,
        "analyst_decision": saved.decision,
        "analyst_opening": saved.negotiation.opening_offer,
        "analyst_target_low": saved.negotiation.target_low,
        "analyst_target_high": saved.negotiation.target_high,
        "analyst_walkaway": saved.negotiation.walk_away,
        "final_decision": "PASS",
        "final_offer": None,
        "final_transaction_price": None,
        "transaction_status": "PASSED",
        "decision_changed": "YES",
        "notes": "分析后放弃",
    }
    save_decision_memory(dm)

    # 原始报告必须原样可读（decision 仍是 NEGOTIATE，不是被 final 覆盖成 PASS）
    raw = get_report(rid)
    assert raw["decision"] == saved.decision
    assert raw["decision"] == DECISION_NEGOTIATE
    assert raw["input"]["initial_decision"] == "BUY"   # Before Analysis 锁定字段保留


# ---- Test 5：Decision Changed 正确记录 ----
def test_decision_changed_recorded():
    inp = AnalysisInput(
        artist="X", asking_price=15000, currency="EUR",
        comparables=[comp(14432), comp(17712)],
        initial_decision="BUY", initial_planned_offer=9000,
    )
    report = analyze(inp)
    assert report.decision == DECISION_NEGOTIATE
    saved = save_report(report)
    dm = {
        "report_id": saved.id,
        "initial_decision": "BUY",
        "initial_offer": 9000,
        "analyst_decision": saved.decision,
        "final_decision": "PASS",
        "transaction_status": "PASSED",
        "decision_changed": "YES",
        "notes": "",
    }
    save_decision_memory(dm)
    mem = [r for r in history() if r["report_id"] == saved.id][0]
    assert mem["decision_changed"] == "YES"
    assert mem["final_decision"] == "PASS"
    assert mem["analyst_decision"] == saved.decision


# ---- Test 6：History 展示与统计 ----
def test_history_and_stats():
    inp = AnalysisInput(
        artist="X", artwork="Test Work", asking_price=12000, currency="EUR",
        comparables=[comp(14432), comp(17712)],
        initial_decision="WATCH", initial_planned_offer=11000,
    )
    report = analyze(inp)
    saved = save_report(report)
    save_decision_memory({
        "report_id": saved.id,
        "initial_decision": "WATCH",
        "initial_offer": 11000,
        "analyst_decision": saved.decision,
        "final_decision": "BUY",
        "final_offer": 9500,
        "final_transaction_price": 9500,
        "transaction_status": "BOUGHT",
        "decision_changed": "YES",
        "notes": "",
    })
    rows = history()
    assert any(r["report_id"] == saved.id for r in rows)
    row = [r for r in rows if r["report_id"] == saved.id][0]
    assert row["object"] == "X Test Work"
    assert row["analyst_decision"] == saved.decision
    assert row["final_decision"] == "BUY"

    stats = history_stats()
    assert stats["total_opportunities"] >= 1
    assert stats["decisions_completed"] >= 1
    assert stats["decision_changed"] >= 1
    assert stats["bought"] >= 1
    assert stats["avg_negotiated_discount"] is not None  # (11000-9500)/11000 ≈ 13.6%


# ---- 确定性：同输入同输出（Decision Engine 可复算）----
def test_decision_deterministic():
    inp = AnalysisInput(
        artist="X", asking_price=10000, currency="EUR",
        comparables=[comp(14432), comp(17712)],
    )
    r1, r2 = analyze(inp), analyze(inp)
    assert r1.decision == r2.decision
    assert r1.brief.model_dump() == r2.brief.model_dump()
