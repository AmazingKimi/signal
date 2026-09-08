"""Research Layer：Query 生成、来源分级、确定性抽取的诚实规则。"""
from app.models import SALE_ASKING, SALE_SOLD
from app.research import (
    extract_comparables,
    generate_queries,
    source_tier,
)


def test_query_generation_multiple_and_synonym():
    qs = generate_queries("Porsche", "964 Carrera RS N-GT", "1992")
    assert len(qs) >= 5
    # 必须包含同义词变体：N-GT -> NGT
    assert any("NGT" in q and "-" not in q.split("NGT")[0][-2:] for q in qs)
    # 必须含年份 query
    assert any(q.startswith("1992") for q in qs)


def test_source_tier_classification():
    assert source_tier("https://www.christies.com/lot/1") == 1
    assert source_tier("https://www.sothebys.com/en/buy/auction/1") == 1
    assert source_tier("https://www.artcurial.com/en/1") == 1
    assert source_tier("https://www.porsche.com/1") == 1
    assert source_tier("https://www.classic-trader.com/en/1") == 2
    assert source_tier("https://www.reddit.com/r/1") == 3
    assert source_tier("https://news.example.com/1") == 3


def test_extraction_finds_sold_price():
    text = ("1992 Porsche 964 Carrera RS N-GT sold for EUR 310,000 at auction "
            "including premium. Lot 45.")
    recs = extract_comparables("https://auction.example/1", "RS N-GT result", text)
    assert len(recs) >= 1
    r = recs[0]
    assert r.price == 310000
    assert r.currency == "EUR"
    assert r.sale_type == SALE_SOLD
    assert r.source_url == "https://auction.example/1"
    assert r.source_tier == 2
    assert r.source_name == "auction.example"


def test_extraction_detects_asking():
    text = "1992 Porsche 964 Carrera RS for sale, asking price €325,000."
    recs = extract_comparables("https://dealer.example/1", "RS for sale", text)
    assert recs and recs[0].sale_type == SALE_ASKING


def test_extraction_no_money_no_records():
    """抽不出来就是抽不出来——宁可 0 条，不编 1 条。"""
    recs = extract_comparables("https://x.example/1", "no price here",
                               "This page has no price information at all.")
    assert recs == []


def test_extraction_ignores_year_like_numbers():
    text = "Produced in 1992, model year 1992, chassis 003."
    recs = extract_comparables("https://x.example/1", "specs", text)
    assert all(r.price != 1992 for r in recs)


def test_extraction_symbol_and_code_forms():
    recs = extract_comparables(
        "https://x.example/1", "t", "Result: £8,750 hammer price")
    assert any(r.price == 8750 and r.currency == "GBP" for r in recs)
    recs2 = extract_comparables(
        "https://x.example/2", "t", "Sold for 12,800 EUR at Artcurial")
    assert any(r.price == 12800 and r.currency == "EUR" for r in recs2)


def test_classic_auctioneer_sold_convention():
    """传统拍行官方成交页：sold 价按行业惯例推断为含佣金口径。"""
    text = "Lot sold for EUR 250,000. Geneva, 2024."
    recs = extract_comparables("https://www.sothebys.com/en/buy/lot/1", "lot", text)
    assert recs and recs[0].price_basis == "INCLUDING_PREMIUM"


def test_online_platform_basis_stays_unknown():
    """BaT / PCARMARKET 等线上平台 sold 价口径不明，不猜。"""
    text = "Sold for $305,000 on BaT."
    recs = extract_comparables("https://bringatrailer.com/listing/x", "baT", text)
    assert recs and recs[0].price_basis == "UNKNOWN"


def test_extract_context_keeps_price_window():
    from app.research import extract_relevant_context
    text = ("Intro text about the model and its history. " * 50
            + "This example sold for EUR 310,000 at auction including premium. "
            + "More filler text after. " * 50)
    out = extract_relevant_context(text)
    assert "310,000" in out          # 金额窗口被保留
    assert "sold" in out
    assert len(out) <= 3000


def test_extract_context_returns_head_when_no_keywords():
    from app.research import extract_relevant_context
    text = "单纯描述没有价格信息 " * 200
    out = extract_relevant_context(text)
    assert 0 < len(out) <= 3000


def test_extract_context_budget_respects_max():
    from app.research import extract_relevant_context
    text = ("The N-GT version with M003 option sold for EUR 500,000 in 2023. " * 30)
    out = extract_relevant_context(text, max_chars=2000)
    assert len(out) <= 2000


def test_raw_search_and_page_body_cache_removed():
    import app.store as store
    for name in ("search_cache_get", "search_cache_put", "page_cache_get", "page_cache_put"):
        assert not hasattr(store, name)
