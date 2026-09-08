"""价格分析：口径换算、币种纪律、三池分离、无数据时的克制。"""
from app.models import AnalysisInput, Comparable, BASIS_INCLUDING_PREMIUM
from app.orchestrator import build_price_analysis, analyze
from app.main import _price_intelligence


def comp(price, currency="EUR", url="https://x.example/1", auction=True,
         sale_type="SOLD", price_basis=BASIS_INCLUDING_PREMIUM):
    return Comparable(title="Signals", price=price, currency=currency,
                      source_name="测试来源", source_url=url, is_auction=auction,
                      sale_type=sale_type, price_basis=price_basis)


def test_auction_premium_conversion():
    pa = build_price_analysis([comp(14432), comp(17712)], "EUR")
    # 14432/1.25 = 11545.6 -> 11546
    assert pa.ai_fair_range_low == 11546
    assert pa.ai_fair_range_high == round(17712 / 1.25)
    assert pa.observed_market_range == "€14,432 – €17,712"


def test_fx_conversion_includes_global_records():
    """全球市场：GBP 成交按固定汇率折算后参与区间，不再整条丢弃。"""
    inp = AnalysisInput(artist="X", asking_price=12000, currency="EUR",
                        comparables=[comp(14432), comp(8750, currency="GBP")])
    report = analyze(inp)
    # GBP 8750 -> EUR 10237.5 -> 净价 /1.25 = 8190；EUR 14432 净价 11546
    assert report.price.ai_fair_range_low == 8190
    assert report.price.ai_fair_range_high == 11546
    assert any("固定汇率" in n for n in report.price.notes)


def test_unsupported_currency_excluded():
    inp = AnalysisInput(artist="X", asking_price=12000, currency="EUR",
                        comparables=[comp(14432), comp(1000000, currency="JPY")])
    report = analyze(inp)
    assert report.price.ai_fair_range_low == 11546  # JPY 不在汇率表内，被拒


def test_unverified_comps_excluded():
    inp = AnalysisInput(artist="X", asking_price=12000, currency="EUR",
                        comparables=[comp(14432, url=None)])
    report = analyze(inp)
    assert report.price.ai_fair_range_low is None
    assert report.price.confidence == 0
    assert any("编造" in n or "无合格" in n for n in report.price.notes)


def test_asking_price_never_enters_pricing():
    """三池分离：三条 €400k 挂牌绝不能推导出市场成交 €400k。"""
    inp = AnalysisInput(
        artist="Porsche", artwork="964 Carrera RS N-GT", asking_price=325000,
        currency="EUR", category="CLASSIC_CAR",
        comparables=[
            comp(400000, sale_type="ASKING"),
            comp(410000, sale_type="ASKING"),
            comp(395000, sale_type="ASKING"),
        ],
    )
    report = analyze(inp)
    assert report.price.ai_fair_range_low is None  # 无 SOLD -> 不报价
    assert report.conclusion == "观察"
    assert any("挂牌" in n for n in report.price.notes)


def test_estimate_price_never_enters_pricing():
    inp = AnalysisInput(
        artist="Porsche", artwork="964 Carrera RS N-GT", asking_price=325000,
        currency="EUR", category="CLASSIC_CAR",
        comparables=[comp(380000, sale_type="ESTIMATE")],
    )
    report = analyze(inp)
    assert report.price.ai_fair_range_low is None


def test_unknown_basis_never_enters_pricing():
    """口径不明 = PRICE BASIS UNVERIFIED，不进定价。"""
    inp = AnalysisInput(
        artist="Porsche", artwork="964 Carrera RS N-GT", asking_price=325000,
        currency="EUR", category="CLASSIC_CAR",
        comparables=[comp(310000, price_basis="UNKNOWN")],
    )
    report = analyze(inp)
    assert report.price.ai_fair_range_low is None
    assert any("口径不明" in n for n in report.price.notes)


def test_no_data_conclusion_is_watch():
    inp = AnalysisInput(artist="X", asking_price=10000, currency="EUR")
    report = analyze(inp)
    assert report.conclusion == "观察"
    assert "编造" in report.conclusion_reason or "无可验证" in report.conclusion_reason


def test_deterministic_same_input_same_output():
    inp = AnalysisInput(artist="Takis", artwork="Signals", asking_price=16000,
                        currency="EUR", comparables=[comp(14432), comp(17712)])
    r1, r2 = analyze(inp), analyze(inp)
    assert r1.price.model_dump() == r2.price.model_dump()
    assert r1.conclusion == r2.conclusion
    assert r1.negotiation.model_dump() == r2.negotiation.model_dump()


def test_asking_below_range_is_buy():
    inp = AnalysisInput(artist="X", asking_price=10000, currency="EUR",
                        comparables=[comp(14432), comp(17712)])
    report = analyze(inp)
    assert report.conclusion == "值得买"


