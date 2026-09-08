"""BACKTEST MODE —— Takis / Signals Series 1 No.123

铁律：GROUND_TRUTH 绝不进入 analyze() 的输入。
模型只看到决策当时真实公开可得的信息（画廊报价 + 公开拍卖纪录）。
成交结果只用于事后对比。
"""
from typing import Optional

from .llm import LLMProvider
from .models import BASIS_INCLUDING_PREMIUM, AnalysisInput, Comparable, Report, fmt_money
from .orchestrator import analyze

# ---- 决策当时可获得的信息（不含最终成交价）----

TAKIS_INPUT = AnalysisInput(
    artist="Takis (Panayiotis Vassilakis, 1925–2019)",
    artwork="Signals, Series 1 No.123 (Ed. 123)",
    asking_price=16000,
    currency="EUR",
    year="1968",
    medium="Steel, electrical system",
    dimensions="197 × 21 × 24 cm",
    seller="Galerie Loevenbruck, Paris",
    notes=(
        "画廊报价 €16,000（私洽净价）。底座铭文：takis signals 1968, series 1 no 123, ed. 123。"
        "以下为决策时点公开可查的拍卖纪录。"
    ),
    comparables=[
        Comparable(
            title="Signal (series 1 n°66)", year="1968", price=14432, currency="EUR",
            sold_at="Artcurial Paris, Limited Edition, 2023-11", is_auction=True,
            source_name="Artcurial（via art.salon 拍卖纪录聚合）",
            source_url="http://art.salon/artwork/vassilakis-takis_signal-series-1-ndeg-66-1968_AID1185695",
            price_basis=BASIS_INCLUDING_PREMIUM,
        ),
        Comparable(
            title="Signals (serie 2 n°76)", year="1968", price=17712, currency="EUR",
            sold_at="Artcurial Paris, Limited Edition, 2023-11", is_auction=True,
            source_name="Artcurial（via art.salon 拍卖纪录聚合）",
            source_url="https://www.art.salon/artwork/vassilakis-takis_signals-serie-2-ndeg-76-1968_AID1185694",
            price_basis=BASIS_INCLUDING_PREMIUM,
        ),
        Comparable(
            title="Signals, series 2 no 121", year="1968", price=8750, currency="GBP",
            sold_at="Christie's First Open Online, 2020-02", is_auction=True,
            source_name="Christie's",
            source_url="https://www.christies.com.cn/en/lot/lot-6252056",
            price_basis=BASIS_INCLUDING_PREMIUM,
        ),
        Comparable(
            title="Signal Lights (two parts)", year="1968", price=21590, currency="GBP",
            sold_at="Phillips Modern & Contemporary Art Day Sale", is_auction=True,
            source_name="Phillips",
            source_url="https://www.phillips.com/detail/franz-west/UK010624/188",
            price_basis=BASIS_INCLUDING_PREMIUM,
        ),
    ],
)

# ---- 事后对照（绝不作为模型输入）----

GROUND_TRUTH = {
    "accepted_selling_price": 12800,   # 发票 Accepted selling price
    "currency": "EUR",
    "shipping": 3114,                  # 发票运费
    "vat": 622.80,                     # 发票 VAT
    "total_cost": 16536.80,            # 发票总成本
    "source": "Galerie Loevenbruck 发票（2026-08）",
    "actual_opening_offer": None,      # 未记录——教训：谈判过程也要留痕
}


def run_backtest(llm: Optional[LLMProvider] = None) -> dict:
    report: Report = analyze(TAKIS_INPUT, llm=llm)
    ns = report.negotiation
    gt_price = GROUND_TRUTH["accepted_selling_price"]
    cur = GROUND_TRUTH["currency"]

    comparison = {
        "ai_fair_range": (
            f"{fmt_money(report.price.ai_fair_range_low, cur)} – "
            f"{fmt_money(report.price.ai_fair_range_high, cur)}（AI Analysis）"
            if report.price.ai_fair_range_low is not None else "无（数据不足）"
        ),
        "ai_opening_offer": (
            fmt_money(ns.opening_offer, cur) if ns and ns.opening_offer else "无"
        ),
        "ai_walk_away": (
            fmt_money(ns.walk_away, cur) if ns and ns.walk_away else "无"
        ),
        "ai_conclusion": report.conclusion,
        "actual_purchase_price": f"{fmt_money(gt_price, cur)}（发票）",
        "actual_total_cost": f"{fmt_money(GROUND_TRUTH['total_cost'], cur)}（含运费+VAT）",
        "actual_opening_offer": GROUND_TRUTH["actual_opening_offer"] or "未记录",
        "verdict": None,  # 由下面计算
        "notes": [],
    }

    if ns and ns.walk_away:
        in_walk_away = gt_price <= ns.walk_away
        in_target = (
            ns.target_low is not None and ns.target_high is not None
            and ns.target_low <= gt_price <= ns.target_high
        )
        if in_target:
            comparison["verdict"] = "实际成交价落在 AI 目标区间内——回测通过"
        elif in_walk_away:
            comparison["verdict"] = "实际成交价低于 AI walk-away 但高于目标区间——AI 建议仍可接受"
        else:
            comparison["verdict"] = "实际成交价高于 AI walk-away——按 AI 建议应放弃，需人工复盘"

    comparison["notes"].append(
        "附加成本教训：运费 €3,114 + VAT €622.80 = 总成本 €16,536.80（占净价 29%）。"
        "0.1 版已把『附加成本纳入总成本比较』写入风险清单。"
    )
    comparison["notes"].append(
        "记忆教训：人工回忆的成交价（约 €10,000）与发票（€12,800）偏差 22%——"
        "数字必须钉在纸上，这正是证据系统存在的理由。"
    )

    return {
        "report": report.model_dump(),
        "ground_truth": GROUND_TRUTH,
        "comparison": comparison,
        "human_scoring": {
            "价格判断（1-5）": None,
            "研究质量（1-5）": None,
            "谈判建议（1-5）": None,
            "是否会改变决策（YES/NO）": None,
        },
    }
