"""Opportunity Orchestrator —— 分析主管线（0.2）。

设计原则（0.1 继承 + 0.2 强化）：
- 价格区间 / 结论 / 谈判数字：全部确定性计算，可复算可审计
- LLM 只做研究与文字；Search Provider 独立于 LLM（换搜索服务不重写业务层）
- 三池分离：SOLD / ASKING / ESTIMATE 绝不混算，挂牌价不是市场价
- 口径不明 = PRICE BASIS UNVERIFIED，不进定价
- 搜索失败诚实呈现：Found 17 / Verified 5 / High 0 -> 结论「观察」，绝不强行报价
"""
from typing import List, Optional

from .comparability import classify_pool, evaluate_comparability
from .evidence import EvidenceStore
from .models import (
    BASIS_HAMMER,
    BASIS_INCLUDING_PREMIUM,
    BASIS_NET,
    BASIS_UNKNOWN,
    SALE_ASKING,
    SALE_ESTIMATE,
    SALE_SOLD,
    SALE_UNKNOWN,
    AnalysisInput,
    Comparable,
    NegotiationStrategy,
    PriceAnalysis,
    Report,
    ResearchSummary,
    convert_currency,
    fmt_money,
    now_iso,
)
from .research import source_tier


# ---------------------------------------------------------------- 价格分析

def build_price_analysis(
    comps: List[Comparable],
    currency: str,
    pools: Optional[dict] = None,
    asking_price: Optional[float] = None,
) -> PriceAnalysis:
    """comps 传入的是「已通过定价池准入」的可比。"""
    pa = PriceAnalysis(comparables=comps)
    notes = pa.notes
    cur = (currency or "EUR").upper()

    if pools:
        if pools.get("asking"):
            notes.append(
                f"{pools['asking']} 条挂牌价（ASKING）单独陈列——挂牌价是卖方愿望，不是市场成交，"
                "绝不参与合理价计算"
            )
        if pools.get("estimate"):
            notes.append(
                f"{pools['estimate']} 条拍卖估价（ESTIMATE）单独陈列——估价是预测，不是成交"
            )
        if pools.get("basis_unknown"):
            notes.append(
                f"{pools['basis_unknown']} 条记录金额口径不明（PRICE BASIS UNVERIFIED），已排除出定价"
            )
        if pools.get("tier_gate"):
            notes.append(
                f"{pools['tier_gate']} 条记录被 Evidence Gate 排除出定价"
                "（来源等级不足或缺少明确原始成交引用）——论坛/聚合等 Tier 3 来源只能作线索，不能作成交证据"
            )

    if not comps:
        notes.append(
            "无合格 SOLD 可比进入定价池：Observed Market Range 无法计算。"
            "此时给出的任何\"合理价\"都是编造——先补带来源的成交证据。"
        )
        pa.confidence = 0
        return pa

    nets = []
    converted = False
    for c in comps:
        px = convert_currency(c.price, c.currency, cur)
        if px is None:
            continue
        if (c.currency or "EUR").upper() != cur:
            converted = True
        if c.price_basis == "INCLUDING_PREMIUM":
            nets.append(px / 1.25)
        else:
            nets.append(px)
    if not nets:
        notes.append("可比币种无法换算，无法计算区间")
        pa.confidence = 0
        return pa
    observed_low = min(convert_currency(c.price, c.currency, cur) for c in comps
                       if convert_currency(c.price, c.currency, cur) is not None)
    observed_high = max(convert_currency(c.price, c.currency, cur) for c in comps
                        if convert_currency(c.price, c.currency, cur) is not None)
    pa.observed_market_range = f"{fmt_money(observed_low, cur)} – {fmt_money(observed_high, cur)}"

    lo, hi = min(nets), max(nets)
    pa.ai_fair_range_low = round(lo)
    pa.ai_fair_range_high = round(hi)
    fair_mid = (lo + hi) / 2
    if asking_price is not None and asking_price > 0:
        pa.ai_opening_offer = round(min(asking_price, max(lo, min(fair_mid, asking_price * 0.90))))
    else:
        pa.ai_opening_offer = round(fair_mid * 0.90)
    pa.ai_max_price = round(hi)
    notes.append(
        "拍卖可比按含约 25% 买家佣金折算为净价口径（AI Analysis 口径换算，非成交事实）"
    )
    if converted:
        notes.append(
            "部分可比为 USD/GBP 等外币成交，按固定汇率折算（EUR:USD=1:0.92, EUR:GBP=1:1.17，"
            "AI Analysis 假设，非实时汇率）"
        )
    return pa


# ---------------------------------------------------------------- 谈判模块

def build_negotiation(inp: AnalysisInput, price: PriceAnalysis) -> NegotiationStrategy:
    cur = (inp.currency or "EUR").upper()
    ns = NegotiationStrategy(asking_price=inp.asking_price, currency=cur)

    if price.ai_fair_range_low is None:
        ns.reason_zh = (
            "无可验证市场数据支撑谈判锚点。建议：先收集至少 2 条带来源、口径明确的"
            "同类成交，再向卖方发起价格讨论。没有证据的压价只是砍价，有证据的压价才是谈判。"
        )
        ns.rationale_en = (
            "No verifiable market data available to anchor a negotiation. "
            "Collect at least 2 sourced comparable results before opening price discussions."
        )
        return ns

    lo, hi = price.ai_fair_range_low, price.ai_fair_range_high
    fair_mid = (lo + hi) / 2
    if ns.asking_price is not None and ns.asking_price > 0:
        ns.opening_offer = round(min(ns.asking_price, max(lo, min(fair_mid, ns.asking_price * 0.90))))
    else:
        ns.opening_offer = round(fair_mid * 0.90)
    ns.target_low = lo
    ns.target_high = round((lo + hi) / 2)
    ns.walk_away = price.ai_max_price

    ns.reason_zh = (
        f"已验证 SOLD 可比净价区间 {fmt_money(lo, cur)}–{fmt_money(hi, cur)}"
        f"（置信度 {price.confidence}/100）。"
        f"当前报价 {fmt_money(inp.asking_price, cur)}。"
        f"开价 {fmt_money(ns.opening_offer, cur)} 以合理区间中位锚点并保留约 10% 谈判空间，"
        f"成交目标 {fmt_money(ns.target_low, cur)}–{fmt_money(ns.target_high, cur)}，"
        f"超过 {fmt_money(ns.walk_away, cur)} 即离场。"
    )
    ns.rationale_en = (
        f"Verified SOLD comparable results (net-of-premium basis) place this series at "
        f"{fmt_money(lo, cur)}–{fmt_money(hi, cur)} (confidence {price.confidence}/100). "
        f"The current asking price is {fmt_money(inp.asking_price, cur)}. "
        f"I propose opening at {fmt_money(ns.opening_offer, cur)}, targeting "
        f"{fmt_money(ns.target_low, cur)}–{fmt_money(ns.target_high, cur)}, "
        f"and walking away above {fmt_money(ns.walk_away, cur)}."
    )
    ns.email_draft_en = _draft_email(inp, price, ns, cur)
    return ns


def _draft_email(
    inp: AnalysisInput, price: PriceAnalysis, ns: NegotiationStrategy, cur: str
) -> str:
    work = inp.artwork or "the work"
    lines = [
        f"Subject: Inquiry — {work} by {inp.artist}",
        "",
        "Dear Sir or Madam,",
        "",
        f"Thank you for the details on {work} by {inp.artist}"
        + (f" ({inp.year})" if inp.year else "") + ".",
        "",
    ]
    if price.comparables:
        lines.append("I have been following the market for this model. Publicly recorded results for comparable works include:")
        lines.append("")
        for c in price.comparables:
            src = c.source_name or "public auction record"
            lines.append(f"- {c.title} — {fmt_money(c.price, c.currency)} ({src})")
        lines.append("")
        lines.append(
            f"Based on these recorded results, I would like to propose "
            f"{fmt_money(ns.opening_offer, cur)} as a starting point. "
            f"I am a serious buyer and can move quickly on payment and logistics "
            f"once we agree on terms."
        )
    else:
        lines.append(
            "Before discussing price, I would appreciate documentation on "
            "provenance, condition and history of the item, together with "
            "any comparable market references you can share."
        )
    lines.append("")
    lines.append("I look forward to your thoughts.")
    lines.append("")
    lines.append("Best regards,")
    lines.append("Kimi")
    return "\n".join(lines)


# ---------------------------------------------------------------- 0.3 LLM 精读辅助

def _host_of(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url or "").netloc or ""


def _select_pages_for_llm(records: List[Comparable], pages: List[dict], max_pages: int = 10) -> List[dict]:
    """规则初筛：LLM 只精读最相关的页面（0.3.1 更聪明地排序）。"""
    page_map = {p["url"]: p for p in pages}

    def page_score(p: dict) -> int:
        t = (p.get("title") or "").lower()
        s = 0
        if any(k in t for k in ("n-gt", "ngt", "n/gt", "competition", "m003", "option 003")):
            s += 4
        if any(k in t for k in ("auction", "sold", "result", "lot")):
            s += 1
        if source_tier(p["url"]) == 1:
            s += 2
        return s

    order = {"HIGH": 6, "MEDIUM": 5, "": 4, "LOW": 3, "NOT_COMPARABLE": 2}
    scored = []
    for rec in records:
        u = rec.source_url
        if u and u in page_map:
            scored.append((order.get(rec.comparability, 1) + page_score(page_map[u]), u))

    seen, ranked = set(), []
    for _, u in sorted(scored, key=lambda x: x[0], reverse=True):
        if u not in seen:
            seen.add(u)
            ranked.append(page_map[u])
    rest = [p for p in pages if p["url"] not in seen]
    rest.sort(key=page_score, reverse=True)
    ranked.extend(rest)
    return ranked[:max_pages]


def _llm_record_to_comparable(
    lr: dict, fallback: Optional[Comparable]
) -> Optional[Comparable]:
    """LLM 精读 JSON -> Comparable。"""
    try:
        price = float(str(lr.get("price") or "").replace(",", "").replace(" ", ""))
    except (TypeError, ValueError):
        price = 0.0
    if price <= 0:
        return fallback

    sale_type = str(lr.get("sale_type") or "").upper() or SALE_UNKNOWN
    if sale_type not in (SALE_SOLD, SALE_ASKING, SALE_ESTIMATE, SALE_UNKNOWN):
        sale_type = SALE_UNKNOWN
    basis = str(lr.get("price_basis") or "").upper() or BASIS_UNKNOWN
    if basis not in (BASIS_HAMMER, BASIS_INCLUDING_PREMIUM, BASIS_NET, BASIS_UNKNOWN):
        basis = BASIS_UNKNOWN
    url = lr.get("source_url") or ""
    attrs = dict(lr.get("attributes") or {})
    if lr.get("sale_date"):
        attrs["sale_date"] = lr["sale_date"]

    return Comparable(
        title=str(lr.get("title") or "")[:120],
        year=str(lr["year"]) if lr.get("year") else None,
        price=price,
        currency=str(lr.get("currency") or "EUR").upper(),
        sold_at=_host_of(url),
        is_auction=(sale_type == SALE_SOLD),
        source_name=str(lr.get("source_name") or "")[:80],
        source_url=url,
        sale_type=sale_type,
        price_basis=basis,
        source_tier=source_tier(url),
        comparability=str(lr.get("comparability") or "").upper() or "",
        comparability_reason=str(lr.get("comparability_reason") or ""),
        evidence_excerpt=str(lr.get("evidence_excerpt") or "")[:300] or None,
        attributes=attrs,
        retrieved_at=now_iso(),
        llm_extracted=True,
        llm_provider=lr.get("llm_provider"),
        llm_model=lr.get("llm_model"),
        llm_confidence=lr.get("llm_confidence"),
        price_basis_reason=str(lr.get("price_basis_reason") or ""),
    )


def _merge_llm_records(
    rule_records: List[Comparable], llm_records: List[dict]
) -> List[Comparable]:
    """LLM 记录按 URL 覆盖规则记录；LLM 抽到而规则漏掉的记录补充进来。"""
    llm_by_url: dict = {}
    for lr in llm_records:
        u = lr.get("source_url")
        if u:
            llm_by_url.setdefault(u, []).append(lr)

    def key_of(c: Comparable) -> tuple:
        return (c.source_url or "", round(c.price, 2), (c.title or "")[:60])

    merged: List[Comparable] = []
    seen: set = set()
    covered: set = set()
    for rec in rule_records:
        lrs = llm_by_url.get(rec.source_url, [])
        if lrs:
            added = False
            for lr in lrs:
                conv = _llm_record_to_comparable(lr, None)
                if conv is not None:
                    k = key_of(conv)
                    if k not in seen:
                        seen.add(k)
                        merged.append(conv)
                        added = True
            if added:
                covered.add(rec.source_url)
            else:
                merged.append(rec)
        else:
            merged.append(rec)

    for lr in llm_records:
        conv = _llm_record_to_comparable(lr, None)
        if conv is not None:
            k = key_of(conv)
            if k not in seen:
                seen.add(k)
                merged.append(conv)
    return merged


# ---------------------------------------------------------------- 主管线

def analyze(
    inp: AnalysisInput,
    llm=None,
    researcher=None,
    on_step=None,
) -> Report:
    def step(msg: str):
        if on_step:
            try:
                on_step(msg)
            except Exception:
                pass

    warnings: List[str] = []
    store = EvidenceStore()
    llm_used = False
    summary = ResearchSummary(enabled=False)

    user_facts = [
        ("当前报价", f"{fmt_money(inp.asking_price, inp.currency)}（{inp.seller or '卖方未提供'}）" if inp.asking_price is not None else None),
        ("作品媒介", inp.medium),
        ("作品尺寸", inp.dimensions),
        ("创作年份", inp.year),
        ("卖方", inp.seller),
    ]
    for claim, value in user_facts:
        if value:
            store.add(claim=claim, value=str(value), source_name="用户输入（Kimi 提供）")

    comps: List[Comparable] = list(inp.comparables)
    artist_analysis = (
        "（离线模式：未调用 LLM。艺术家市场地位、展览与机构收藏研究缺席——"
        "配置 .env 中的 LLM_API_KEY 后自动补充。）"
    )
    extra_risks: List[str] = []

    if inp.research:
        if researcher is None or not getattr(researcher, "available", False):
            summary.errors.append("Research Mode 已勾选但搜索服务不可用（见 .env 配置说明）")
            warnings.append("主动研究未执行：搜索服务不可用，本次报告仅基于手动提供的证据")
        else:
            from .research import run_research
            result = run_research(inp, researcher, on_step=on_step)
            summary = result["summary"]
            research_records = result["records"]
            pages = result.get("pages") or []

            if llm is not None and getattr(llm, "configured", False):
                candidates = _select_pages_for_llm(research_records, pages, max_pages=10)
                step(f"LLM 精读 0/{len(candidates)} completed（{llm.provider}/{llm.model}）")
                llm_records = llm.read_pages(
                    inp,
                    candidates,
                    on_page_done=lambda done, total: step(
                        f"LLM 精读 {done}/{total} completed"
                    ),
                    time_budget=35.0,
                    max_concurrency=4,
                ) or []
                summary.llm_pages_analyzed = len(candidates)
                summary.llm_records_extracted = len(llm_records)
                llm_urls = {r.get("source_url") for r in llm_records}
                summary.llm_rejected = len(candidates) - len(llm_urls)
                usage = llm.usage.as_dict()
                summary.llm_calls = usage["llm_calls"]
                summary.llm_total_tokens = usage["llm_total_tokens"]
                summary.llm_estimated_cost_rmb = usage["llm_estimated_cost_rmb"]

                if llm_records:
                    research_records = _merge_llm_records(research_records, llm_records)
                    llm_used = True

            step(f"Evaluating comparability（{len(research_records)} 条记录）")
            for rec in research_records:
                if rec.llm_extracted and rec.comparability:
                    level, reason = evaluate_comparability(inp, rec)
                    if level == "LOW" and "量级" in reason:
                        rec.comparability = level
                        rec.comparability_reason = reason
                else:
                    level, reason = evaluate_comparability(inp, rec)
                    rec.comparability = level
                    rec.comparability_reason = reason

            summary.verified = sum(1 for r in research_records if r.verified)
            summary.high = sum(1 for r in research_records if r.comparability == "HIGH")
            summary.medium = sum(1 for r in research_records if r.comparability == "MEDIUM")
            summary.low = sum(1 for r in research_records if r.comparability == "LOW")
            summary.not_comparable = sum(1 for r in research_records if r.comparability == "NOT_COMPARABLE")

            comps.extend(research_records)
            for rec in research_records[:10]:
                llm_note = "（LLM 精读）" if rec.llm_extracted else ""
                store.add(
                    claim=f"市场记录：{rec.title[:60]}{llm_note}",
                    value=f"{fmt_money(rec.price, rec.currency)}（{rec.sale_label}，{rec.basis_label}，Tier {rec.source_tier}）",
                    source_url=rec.source_url,
                    source_name=rec.source_name,
                    evidence=(rec.evidence_excerpt or "")[:150],
                )

    cur = (inp.currency or "EUR").upper()
    sold_pool, asking_pool, estimate_pool = [], [], []
    pricing_pool: List[Comparable] = []
    basis_unknown = 0

    for c in comps:
        if c.sale_type == "ASKING":
            asking_pool.append(c)
        elif c.sale_type == "ESTIMATE":
            estimate_pool.append(c)
        elif c.sale_type == "UNKNOWN":
            pass
        else:
            sold_pool.append(c)

    tier_gate = 0
    for c in comps:
        if classify_pool(c, cur) == "OK":
            pricing_pool.append(c)
        elif c.sale_type == "SOLD" and c.verified and c.price_basis != "UNKNOWN" \
                and c.comparability in ("HIGH", "MEDIUM", "") and (c.source_tier or 0) >= 2:
            tier_gate += 1
        elif c.sale_type == "SOLD" and c.verified and c.price_basis == "UNKNOWN":
            basis_unknown += 1

    high = sum(1 for c in pricing_pool if c.comparability in ("HIGH", ""))
    medium = sum(1 for c in pricing_pool if c.comparability == "MEDIUM")

    pools = {
        "sold": len(sold_pool),
        "asking": len(asking_pool),
        "estimate": len(estimate_pool),
        "basis_unknown": basis_unknown,
        "tier_gate": tier_gate,
    }
    summary.sold_pool = len(sold_pool)
    summary.asking_pool = len(asking_pool)
    summary.estimate_pool = len(estimate_pool)
    summary.rejected = max(
        0, summary.candidates - summary.verified
    ) + sum(1 for c in comps if c.comparability in ("LOW", "NOT_COMPARABLE"))

    price = build_price_analysis(pricing_pool, cur, pools, inp.asking_price)
    step("Running pricing engine")
    if pricing_pool:
        if high >= 1:
            price.confidence = min(85, 20 + 15 * high + 8 * medium)
        else:
            price.confidence = min(40, 25 + 8 * medium)
            price.notes.append(
                "⚠ 无 HIGH 可比性成交，仅 MEDIUM 支撑——本区间仅具参考价值，置信度有意压低"
            )

    if llm is not None and getattr(llm, "configured", False):
        try:
            research = llm.research(inp)
            if research:
                llm_used = True
                for c in research.get("claims", []) or []:
                    if isinstance(c, dict) and c.get("claim") and c.get("value") is not None:
                        store.add(
                            claim=str(c["claim"]),
                            value=str(c["value"]),
                            source_url=c.get("source_url"),
                            source_name=c.get("source_name"),
                            evidence=c.get("evidence"),
                        )
                if not inp.research:
                    for c in research.get("comparables", []) or []:
                        try:
                            if isinstance(c, dict) and c.get("price") and c.get("title"):
                                comps.append(Comparable(**{
                                    k: v for k, v in c.items()
                                    if k in Comparable.model_fields
                                }))
                        except Exception:
                            continue
                artist_analysis = research.get("artist_analysis") or artist_analysis
                extra_risks = [str(r) for r in (research.get("risks") or []) if r]
        except Exception as exc:
            warnings.append(f"LLM 研究失败，已回退离线模式：{exc}")

    from .decision import build_brief, decide, grade_risks

    risks = grade_risks(inp, price, extra_risks)
    decision, conclusion, reason = decide(inp, price, high, medium)
    negotiation = build_negotiation(inp, price)

    price.comparables = comps

    brief = build_brief(inp, price, risks, store.items, decision, negotiation)

    return Report(
        input=inp,
        conclusion=conclusion,
        conclusion_reason=reason,
        decision=decision,
        brief=brief,
        artist_analysis=artist_analysis,
        evidence=store.items,
        price=price,
        risks=risks,
        negotiation=negotiation,
        research_summary=summary,
        llm_used=llm_used,
        warnings=warnings,
    )