"""Decision Engine + Opportunity Brief（0.4）。

铁律（任务书第四/五节）：
- Decision / Fair Range / Opening / Target / Walk-away 全部来自确定性规则，LLM 无权决定
- 规则可测试、可复算：同一输入永远同一输出
- DeepSeek 只贡献理由摘要、Evidence 摘要、风险解释、Next Action 文案（本版 Next Action 用确定性模板）

Decision 四态：
    BUY        证据充分，价格明显处于合理甚至便宜区间
    NEGOTIATE  东西值得买，但报价偏高或存在可谈空间
    WATCH      证据不足 / 价格无法可靠判断 / 关键风险未解决
    PASS       价格明显不合理 / 对象质量差 / 重大风险或市场证据不支持
"""
from typing import List, Optional, Tuple

from .models import (
    DECISION_BUY,
    DECISION_NEGOTIATE,
    DECISION_PASS,
    DECISION_WATCH,
    SEV_CRITICAL,
    SEV_HIGH,
    SEV_LOW,
    SEV_MEDIUM,
    AnalysisInput,
    BriefItem,
    OpportunityBrief,
    PriceAnalysis,
    RiskItem,
    fmt_money,
)

# 中文结论 <-> 英文 Decision（conclusion 保留中文四态兼容 0.1-0.3）
CONCLUSION_ZH = {
    DECISION_BUY: "值得买",
    DECISION_NEGOTIATE: "值得谈",
    DECISION_WATCH: "观察",
    DECISION_PASS: "放弃",
}


def decide(
    inp: AnalysisInput, price: PriceAnalysis, high: int, medium: int
) -> Tuple[str, str, str]:
    """确定性 Decision Engine。返回 (decision_en, conclusion_zh, reason)。

    规则顺序（第一条命中即返回）：
    1. 无报价           -> WATCH
    2. 无合格 SOLD 可比 -> WATCH
    3. Confidence < 40  -> WATCH（数据不足，宁可不给判断）
    4. Asking > walk-away * 1.15 -> PASS（报价显著高于离场价且无特殊稀缺证据）
    5. Asking <= fair 下限 -> BUY
    6. Asking <= fair 上限 * 1.15 -> NEGOTIATE（可谈范围内）
    7. 其余 -> NEGOTIATE（高于区间但低于离场价，仍有谈判空间）
    """
    cur = inp.currency
    if inp.asking_price is None:
        return DECISION_WATCH, CONCLUSION_ZH[DECISION_WATCH], "未提供当前报价，无法进行价格比较"

    if price.ai_fair_range_low is None or price.ai_fair_range_high is None:
        return (
            DECISION_WATCH, CONCLUSION_ZH[DECISION_WATCH],
            "无可验证的合格 SOLD 可比。此时给出的任何\"合理价\"都是编造——"
            "先收集带来源、口径明确的成交，再谈价格。",
        )

    lo, hi = price.ai_fair_range_low, price.ai_fair_range_high
    walk = price.ai_max_price or hi
    conf = price.confidence
    a = inp.asking_price
    evidence_line = f"（HIGH 可比 {high} 条、MEDIUM {medium} 条，置信度 {conf}/100）"

    if conf < 40:
        return (
            DECISION_WATCH, CONCLUSION_ZH[DECISION_WATCH],
            f"置信度 {conf}/100 不足以支撑可靠判断（可比证据不足）{evidence_line}——"
            "先补证据，再谈价格。",
        )
    if a > walk * 1.15:
        return (
            DECISION_PASS, CONCLUSION_ZH[DECISION_PASS],
            f"报价 {fmt_money(a, cur)} 显著高于可验证离场价 {fmt_money(walk, cur)}"
            f"（超 15% 以上）{evidence_line}——除非存在未计入的特殊稀缺性证据，建议放弃。",
        )
    if a <= lo:
        return (
            DECISION_BUY, CONCLUSION_ZH[DECISION_BUY],
            f"报价 {fmt_money(a, cur)} 不高于可验证净价区间下限 {fmt_money(lo, cur)}"
            f"{evidence_line}——按当前证据可以直接出手。",
        )
    if a <= hi * 1.15:
        return (
            DECISION_NEGOTIATE, CONCLUSION_ZH[DECISION_NEGOTIATE],
            f"报价 {fmt_money(a, cur)} 高于可验证净价区间 {fmt_money(lo, cur)}–{fmt_money(hi, cur)}"
            f"{evidence_line}，差距在谈判可弥合范围内。",
        )
    return (
        DECISION_NEGOTIATE, CONCLUSION_ZH[DECISION_NEGOTIATE],
        f"报价 {fmt_money(a, cur)} 高于区间 {fmt_money(lo, cur)}–{fmt_money(hi, cur)}"
        f"但仍低于离场价 {fmt_money(walk, cur)}{evidence_line}——值得谈，但要做好离场准备。",
    )


# ---------------------------------------------------------------- 风险分级

def grade_risks(inp: AnalysisInput, price: PriceAnalysis, extra: List[str]) -> List[RiskItem]:
    """确定性风险分级（0.4：CRITICAL/HIGH/MEDIUM/LOW，供 Top 3 Risks 排序）。"""
    risks: List[RiskItem] = []
    cur = (inp.currency or "EUR").upper()

    if price.ai_fair_range_low is None:
        risks.append(RiskItem(
            text="无合格 SOLD 可比进入定价池——当前任何价格判断都缺乏证据支撑",
            severity=SEV_CRITICAL,
        ))
    else:
        high = sum(1 for c in price.comparables
                   if c.comparability in ("HIGH", "") and c.sale_type == "SOLD")
        if high < 2:
            risks.append(RiskItem(
                text=f"高可比性成交不足（当前 {high} 条，建议 ≥2）——价格区间置信度有限",
                severity=SEV_MEDIUM,
            ))

    risks.append(RiskItem(
        text="版数/系列规模或产量未确认——稀缺性待验证（要求卖方提供证明）",
        severity=SEV_MEDIUM,
    ))

    if inp.category == "CLASSIC_CAR":
        risks.append(RiskItem(
            text="Matching Numbers、事故史、原厂程度未核验——要求提供检测报告与 build sheet",
            severity=SEV_HIGH,
        ))
        if inp.mileage:
            risks.append(RiskItem(
                text=f"里程 {inp.mileage}：里程对经典车价值影响极大，需核实记录链",
                severity=SEV_MEDIUM,
            ))

    if inp.medium and any(k in (inp.medium or "").lower()
                          for k in ("electric", "électr", "电气")):
        risks.append(RiskItem(
            text="作品含电气/机械系统：运输、安装与长期维护成本需计入总持有成本",
            severity=SEV_MEDIUM,
        ))

    risks.append(RiskItem(
        text="运输、保险、进口税费若未包含在报价中，应纳入总成本比较（历史案例附加成本曾占净价近 30%）",
        severity=SEV_LOW,
    ))
    risks.append(RiskItem(
        text="Provenance 完整度未核验——要求卖方提供完整来源链与证书",
        severity=SEV_HIGH,
    ))

    for t in extra:
        risks.append(RiskItem(text=str(t), severity=SEV_MEDIUM))
    return risks


# ---------------------------------------------------------------- Opportunity Brief

def build_brief(
    inp: AnalysisInput,
    price: PriceAnalysis,
    risks: List[RiskItem],
    evidence_items,
    decision: str,
    ns,
) -> OpportunityBrief:
    cur = (inp.currency or "EUR").upper()
    brief = OpportunityBrief(
        decision=decision,
        asking_price=inp.asking_price,
        currency=cur,
        fair_range_low=price.ai_fair_range_low,
        fair_range_high=price.ai_fair_range_high,
        opening_offer=price.ai_opening_offer,
        walk_away=price.ai_max_price,
        confidence=price.confidence,
        top_risks=sorted(risks, key=lambda r: r.order)[:3],
    )
    if ns:
        brief.target_low = ns.target_low
        brief.target_high = ns.target_high

    # ---- Top 3 Evidence：定价池 HIGH/MEDIUM 可比优先，再补关键事实 ----
    top: List[BriefItem] = []
    pool = [c for c in price.comparables
            if c.sale_type == "SOLD" and c.verified
            and c.price_basis not in ("UNKNOWN",)
            and c.comparability in ("HIGH", "MEDIUM", "")]
    pool.sort(key=lambda c: 0 if c.comparability in ("HIGH", "") else 1)
    for c in pool[:2]:
        top.append(BriefItem(
            title=f"{c.title} — SOLD {fmt_money(c.price, c.currency)}",
            detail=(
                f"{c.comparability or '手动断言'} Comparable · {c.sale_label} · "
                f"{c.basis_label} · Tier {c.source_tier or '?'}"
                + (f"\n{c.comparability_reason}" if c.comparability_reason else "")
            ),
            source_url=c.source_url,
            source_name=c.source_name,
            kind="COMPARABLE",
        ))

    for e in evidence_items:
        if len(top) >= 3:
            break
        if e.verification_status == "VERIFIED" and e.source_url:
            top.append(BriefItem(
                title=e.claim,
                detail=e.value,
                source_url=e.source_url,
                source_name=e.source_name,
                kind="FACT",
            ))
    for e in evidence_items:
        if len(top) >= 3:
            break
        if "报价" in (e.claim or "") or "卖方" in (e.claim or ""):
            top.append(BriefItem(
                title=e.claim,
                detail=e.value,
                kind="USER_CLAIM",
            ))
    brief.top_evidence = top

    # ---- Next Action：确定性模板（拒绝"建议进一步研究"式的空话）----
    brief.next_action = _next_action(inp, price, risks, decision, ns, cur)
    return brief


def _next_action(inp, price, risks, decision, ns, cur) -> str:
    if decision == DECISION_PASS:
        return (
            "放弃本轮。除非卖方大幅让价（低于 "
            f"{fmt_money(price.ai_max_price, cur)}）或出现新的稀缺性证据，"
            "不建议继续投入谈判时间。可将该对象加入观察清单，市场变化后再评估。"
        )

    # 从风险清单推导"报价前必须索取的材料"
    request_items = []
    for r in risks:
        t = r.text
        if "Matching Numbers" in t or "build sheet" in t:
            request_items.append("Porsche Certificate / build sheet")
        elif "事故史" in t or "Accident" in t or "restoration" in t or "修复" in t:
            request_items.append("Accident / repaint history")
        elif "Provenance" in t or "来源链" in t:
            request_items.append("Provenance 完整来源链与证书")
        elif "里程" in t:
            request_items.append("Service history（含里程记录链）")
        elif "电气" in t or "机械" in t:
            request_items.append("电气/机械系统检测报告")
        elif "可比" in t and "不足" in t:
            request_items.append("同规格可比成交纪录（至少 2 条带来源）")

    if decision == DECISION_WATCH:
        if request_items:
            return "报价前先补齐证据：" + "、".join(dict.fromkeys(request_items)) + "。证据到齐后再重新分析定价。"
        return "先确认对象身份与配置（build sheet / 证书），并补充至少 2 条带来源、口径明确的同规格成交，再进入价格讨论。"

    # NEGOTIATE / BUY
    lines = []
    if ns and ns.opening_offer is not None:
        lines.append(f"Recommended opening offer: {fmt_money(ns.opening_offer, cur)}")
    if ns and ns.walk_away is not None:
        lines.append(f"Do not exceed: {fmt_money(ns.walk_away, cur)}")
    anchor = []
    if price.ai_fair_range_low is not None:
        anchor.append(
            f"Verified comparables place the fair range at "
            f"{fmt_money(price.ai_fair_range_low, cur)}–{fmt_money(price.ai_fair_range_high, cur)}"
        )
    if request_items:
        anchor.append("unresolved " + " / ".join(dict.fromkeys(request_items)))
    if anchor:
        lines.append("Negotiation anchor: " + "; ".join(anchor) + ".")
    if decision == DECISION_BUY:
        lines.append("价格已到位——确认 provenance 文件与运输/税费后尽快锁定。")
    return "\n".join(lines)
