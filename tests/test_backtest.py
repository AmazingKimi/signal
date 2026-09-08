"""验收标准 C：Takis 回测——合理价格必须能解释，并与实际结果比较。"""
from app.backtest import GROUND_TRUTH, TAKIS_INPUT, run_backtest


def test_ground_truth_not_in_model_input():
    """铁律：最终成交价绝不能出现在模型输入里。"""
    dump = TAKIS_INPUT.model_dump_json()
    assert "12800" not in dump
    assert "12,800" not in dump


def test_backtest_runs_offline():
    bt = run_backtest(llm=None)
    assert bt["report"]["conclusion"] in ("值得买", "值得谈", "观察")

    ns = bt["report"]["negotiation"]
    gt = GROUND_TRUTH["accepted_selling_price"]

    # AI 目标区间必须覆盖或接近实际成交价（€12,800）
    assert ns["target_low"] <= gt <= ns["target_high"] * 1.05, (
        f"AI 目标区间 {ns['target_low']}–{ns['target_high']} 偏离实际成交 {gt}"
    )
    # walk-away 必须高于实际成交（否则 AI 会建议放弃一笔实际合理的交易）
    assert ns["walk_away"] >= gt


def test_backtest_comparables_all_sourced():
    """回测用的每条可比都必须有来源——证据纪律对内置数据同样生效。"""
    for c in TAKIS_INPUT.comparables:
        assert c.source_url, f"可比 {c.title} 缺少来源"


def test_backtest_comparison_structure():
    bt = run_backtest(llm=None)
    cmp = bt["comparison"]
    for key in ("ai_fair_range", "ai_opening_offer", "ai_walk_away",
                "actual_purchase_price", "verdict"):
        assert key in cmp
    assert bt["human_scoring"]["价格判断（1-5）"] is None  # 人工评分留给 Kimi
