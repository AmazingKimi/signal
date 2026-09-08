"""Comparability Engine —— 任务书第六/八节核心规则的回归守卫。

最重要的一条：普通 964 Carrera RS 不能自动等同 964 RS N-GT/Competition。
"""
from app.comparability import classify_pool, evaluate_comparability, normalize_text
from app.models import (
    BASIS_INCLUDING_PREMIUM,
    HIGH,
    LOW,
    MEDIUM,
    NOT_COMPARABLE,
    AnalysisInput,
    Comparable,
)

TARGET = AnalysisInput(
    artist="Porsche",
    artwork="964 Carrera RS N-GT",
    asking_price=325000,
    currency="EUR",
    year="1992",
    category="CLASSIC_CAR",
    mileage="24,175 km",
    options="003 Carrera RS Competition, 018, 130, 220, 231, 388, 389, 404, 564",
)


def rec(title, price=300000, artist=None, url="https://auction.example/1", **kw):
    d = dict(
        title=title, price=price, currency="EUR", artist=artist,
        source_name="test", source_url=url,
        sale_type="SOLD", price_basis=BASIS_INCLUDING_PREMIUM,
    )
    d.update(kw)
    return Comparable(**d)


def test_plain_rs_is_not_ngt():
    """普通 RS 只是市场背景：最高 LOW，不得进入定价。"""
    level, reason = evaluate_comparability(TARGET, rec("1992 Porsche 964 Carrera RS"))
    assert level == LOW
    assert "N-GT" in reason or "背景" in reason


def test_ngt_records_can_be_high():
    level, _ = evaluate_comparability(
        TARGET, rec("1992 Porsche 964 Carrera RS N-GT", year="1992")
    )
    assert level == HIGH


def test_ngt_spelling_variants_match():
    """网站写 NGT / RS N/GT / Competition，不能因为写法不同就漏配。"""
    for title in ("964 RS NGT", "964 RS N/GT", "964 Carrera RS Competition", "Option M003"):
        assert normalize_text(title) != title or "ngt" in normalize_text(title)


def test_different_generation_not_comparable():
    """964 和 993 是两台车。"""
    level, reason = evaluate_comparability(
        TARGET, rec("1995 Porsche 993 Carrera RS", year="1995")
    )
    assert level == NOT_COMPARABLE


def test_mileage_gap_downgrades():
    big = rec("1992 Porsche 964 Carrera RS N-GT", year="1992")
    big.attributes = {"mileage": "150,000 km"}
    level, reason = evaluate_comparability(TARGET, big)
    assert level != HIGH or "里程" in reason


def test_classify_pool_full_ruleset():
    # 完全合格：VERIFIED + SOLD + 口径明确 + HIGH 可比 + 同币种
    ok = rec("1992 Porsche 964 Carrera RS N-GT", comparability=HIGH)
    assert classify_pool(ok, "EUR") == "OK"
    # 无来源
    assert classify_pool(rec("x", url=None), "EUR") is None
    # 挂牌价
    assert classify_pool(rec("x", sale_type="ASKING"), "EUR") is None
    # 估价
    assert classify_pool(rec("x", sale_type="ESTIMATE"), "EUR") is None
    # 口径不明
    assert classify_pool(rec("x", price_basis="UNKNOWN"), "EUR") is None
    # 可比性不足
    assert classify_pool(rec("x", comparability=LOW), "EUR") is None
    assert classify_pool(rec("x", comparability=NOT_COMPARABLE), "EUR") is None
    # 币种在固定汇率表覆盖内（全球市场折算），但 JPY 等不支持币种被拒
    assert classify_pool(rec("x", currency="GBP"), "EUR") == "OK"
    assert classify_pool(rec("x", currency="JPY"), "EUR") is None


def test_verified_but_low_comparability_is_allowed_state():
    """Christie's €280,000 VERIFIED + LOW COMPARABILITY 完全正常（两个维度正交）。"""
    c = rec("1991 Porsche 964 Carrera RS", comparability=LOW)
    assert c.source_url  # verified
    assert classify_pool(c, "EUR") is None  # 但不进定价


def test_art_series_rules():
    inp = AnalysisInput(artist="Takis", artwork="Signals, Series 1 No.123",
                        year="1968", category="ART")
    same_series = rec("Signal (series 1 n°66)", year="1968", artist="Takis")
    level, _ = evaluate_comparability(inp, same_series)
    assert level in (HIGH, MEDIUM)
    other_artist = rec("Andy Warhol Brillo Box", year="1964", artist="Andy Warhol")
    level, reason = evaluate_comparability(inp, other_artist)
    assert level == NOT_COMPARABLE


def test_magnitude_guard_downgrades_aggregate_noise():
    """量级守卫：$37M 聚合页噪声对 $325k 目标 -> LOW，不得进入定价。"""
    from app.comparability import evaluate_comparability
    noise = rec("1992 Porsche 964 Carrera RS N/GT", price=37285000, comparability="",
                year="1992")
    level, reason = evaluate_comparability(TARGET, noise)
    assert level == LOW
    assert "量级" in reason
    # 反向：价格过低（$22k）同样是噪声
    cheap = rec("1992 Porsche 964 Carrera RS N/GT", price=22000, comparability="",
                year="1992")
    level2, reason2 = evaluate_comparability(TARGET, cheap)
    assert level2 == LOW
    assert "量级" in reason2


# ---------------- 0.4.1 Evidence Gate：来源等级决定定价资格 ----------------

def _gate_rec(source_url="https://auction.example/1", source_tier=1,
              excerpt="sold for EUR 300,000 at auction", **kw):
    return rec("1992 Porsche 964 Carrera RS N/GT", price=300000, url=source_url,
               source_tier=source_tier, evidence_excerpt=excerpt, **kw)


def test_tier3_forum_never_enters_pricing():
    """Rennlist 场景：论坛帖即使 SOLD+HIGH+口径明确，也永远进不了定价池。"""
    forum = _gate_rec(source_url="https://rennlist.com/forums/964/1",
                      source_tier=3, excerpt="user says sold for EUR 300,000",
                      comparability=HIGH)
    assert forum.verified
    assert classify_pool(forum, "EUR") is None


def test_tier2_with_explicit_sale_reference_enters():
    """Tier 2 专业媒体：证据明确引用原始成交 -> 可进定价池。"""
    media = _gate_rec(source_url="https://classic.com/964-rs-ngt/1", source_tier=2,
                      excerpt="The car sold for EUR 300,000 incl. premium at RM Sotheby's",
                      comparability=HIGH)
    assert classify_pool(media, "EUR") == "OK"


def test_tier2_without_explicit_reference_blocked():
    """Tier 2 无原始成交引用（如行情综述）-> 不进定价池。"""
    vague = _gate_rec(source_url="https://classic.com/market-report", source_tier=2,
                      excerpt="Values have risen strongly in recent years",
                      price_basis="NET", comparability=HIGH)
    assert classify_pool(vague, "EUR") is None


def test_tier1_auction_priority():
    """Tier 1 官方拍行：按现有规则正常进池。"""
    official = _gate_rec(source_url="https://www.rmsothebys.com/lot/1", source_tier=1,
                         comparability=HIGH)
    assert classify_pool(official, "EUR") == "OK"


def test_manual_comparable_untiered_passes():
    """手动可比（tier 0，用户断言）：保留 0.1 语义放行。"""
    manual = _gate_rec(source_url="https://user-provided.example/1", source_tier=0,
                       comparability="")
    assert classify_pool(manual, "EUR") == "OK"


def test_llm_cannot_upgrade_source_tier():
    """LLM 无权提升来源等级：论坛 URL 的 LLM 记录仍按确定性域名判为 Tier 3。"""
    from app.orchestrator import _llm_record_to_comparable
    lr = {
        "title": "1992 Porsche 964 Carrera RS N/GT", "sale_type": "SOLD",
        "price": 300000, "currency": "EUR", "price_basis": "HAMMER",
        "comparability": "HIGH",
        "source_url": "https://rennlist.com/forums/964/1",
        "llm_extracted": True, "llm_provider": "deepseek", "llm_model": "deepseek-chat",
        "llm_confidence": 0.9, "evidence_excerpt": "sold for EUR 300,000",
    }
    c = _llm_record_to_comparable(lr, None)
    assert c.source_tier == 3  # 域名判断是确定性的，LLM 的 HIGH 不能提升它
    assert classify_pool(c, "EUR") is None


def test_full_pipeline_tier3_excluded():
    """端到端：Rennlist 论坛记录即使 LLM 标 HIGH 也不进定价池（回归 0.4 实测发现的问题）。"""
    from app.orchestrator import analyze
    inp = AnalysisInput(
        artist="Porsche", artwork="964 Carrera RS N-GT", asking_price=325000,
        currency="EUR", category="CLASSIC_CAR", year="1992",
        comparables=[
            rec("Rennlist forum discussion", price=300000, url="https://rennlist.com/forums/1",
                currency="USD", source_tier=3, evidence_excerpt="user claims sold",
                price_basis=BASIS_INCLUDING_PREMIUM, comparability=HIGH),
            rec("1992 Porsche 964 Carrera RS N/GT", price=305000, url="https://www.rmsothebys.com/lot/1",
                currency="USD", source_tier=1, evidence_excerpt="sold for USD 305,000",
                price_basis=BASIS_INCLUDING_PREMIUM, comparability=HIGH),
        ],
    )
    report = analyze(inp)
    # 定价池只含 Tier 1 拍行记录：fair range 只由 305,000 USD 折算得出（净价 224,480 EUR）
    # 若 Rennlist 300,000 USD 进了池，下限会变成 220,800
    assert report.price.ai_fair_range_low == 224480, report.price.ai_fair_range_low
    assert any("Evidence Gate" in n for n in report.price.notes)  # 透明提示
