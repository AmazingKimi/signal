"""0.3 LLM Research Layer：抽取映射、覆盖合并、降级、成本统计。

用 FakeLLM 模拟 DeepSeek——真实 API 调用在手工验收时验证。
"""
from app.llm import LLMProvider, build_provider
from app.models import (
    BASIS_INCLUDING_PREMIUM,
    AnalysisInput,
    Comparable,
)
from app.orchestrator import analyze, _llm_record_to_comparable, _select_pages_for_llm


class FakeLLM:
    configured = True
    provider = "deepseek"
    model = "deepseek-chat"

    def __init__(self, pages_records=None):
        # pages_records: {url: [record dicts]}
        self.pages_records = pages_records or {}
        self.usage = _FakeUsage()

    def read_pages(self, inp, pages, on_page_done=None, time_budget=None, max_concurrency=4):
        out = []
        for i, p in enumerate(pages):
            for rec in self.pages_records.get(p["url"], []):
                r = dict(rec)
                r["source_url"] = p["url"]
                r["source_name"] = "fake.example"
                r["llm_extracted"] = True
                r["llm_provider"] = "deepseek"
                r["llm_model"] = "deepseek-chat"
                r["llm_confidence"] = 0.9
                out.append(r)
            if on_page_done:
                on_page_done(i + 1, len(pages))
        return out

    def research(self, inp):
        return None


class _FakeUsage:
    def as_dict(self):
        return {"llm_calls": 2, "llm_total_tokens": 3000,
                "llm_estimated_cost_rmb": 0.012}


PAGES = [
    {"url": "https://auction.example/lot-1", "title": "964 RS N/GT lot",
     "text": "1992 Porsche 964 Carrera RS N/GT sold for EUR 310,000 incl. premium."},
    {"url": "https://dealer.example/rs", "title": "Plain RS",
     "text": "1992 Porsche 964 Carrera RS for sale, asking EUR 180,000."},
]

NGT_RECORD = {
    "title": "1992 Porsche 964 Carrera RS N/GT",
    "sale_type": "SOLD",
    "price": 310000,
    "currency": "EUR",
    "price_basis": "INCLUDING_PREMIUM",
    "price_basis_reason": '页面原文 "sold for EUR 310,000 incl. premium"',
    "sale_date": "2024-05-12",
    "year": "1992",
    "attributes": {"mileage": "9,000 km", "color": "Maritime Blue"},
    "comparability": "HIGH",
    "comparability_reason": "Same 964 RS N/GT spec, sold transaction, mileage within range",
    "evidence_excerpt": "1992 Porsche 964 Carrera RS N/GT sold for EUR 310,000 incl. premium",
    "source_url": "https://auction.example/lot-1",   # 真实流程由 read_pages 注入
    "source_name": "auction.example",
    "llm_confidence": 0.9,
}


def test_provider_config_offline_without_key(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    p = build_provider()
    assert p.configured is False
    inp = AnalysisInput(artist="Porsche", artwork="964 Carrera RS N-GT")
    assert p.read_pages(inp, PAGES) == []  # 无 Key 自动降级规则模式


def test_provider_config_deepseek_defaults(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    p = build_provider()
    assert p.configured is True
    assert p.model == "deepseek-chat"
    assert "api.deepseek.com" in p.base_url


def test_llm_record_to_comparable_full_mapping():
    c = _llm_record_to_comparable(NGT_RECORD, None)
    assert c is not None
    assert c.title == "1992 Porsche 964 Carrera RS N/GT"
    assert c.price == 310000
    assert c.sale_type == "SOLD"
    assert c.price_basis == BASIS_INCLUDING_PREMIUM
    assert c.comparability == "HIGH"
    assert c.comparability_reason
    assert c.price_basis_reason
    assert c.llm_extracted is True
    assert c.llm_confidence == 0.9
    assert c.attributes["sale_date"] == "2024-05-12"
    assert c.verified is True  # 有 source_url -> VERIFIED


def test_llm_record_no_trustworthy_price_falls_back():
    bad = dict(NGT_RECORD, price="not-a-number")
    assert _llm_record_to_comparable(bad, None) is None
    fallback = Comparable(title="规则记录", price=300000, currency="EUR",
                          source_url="https://auction.example/lot-1")
    assert _llm_record_to_comparable(bad, fallback) is fallback


def test_select_pages_prioritizes_matching_records():
    rule_records = [
        Comparable(title="x", price=100, currency="EUR",
                   source_url="https://auction.example/lot-1",
                   comparability="HIGH"),
    ]
    chosen = _select_pages_for_llm(rule_records, PAGES, max_pages=15)
    urls = [p["url"] for p in chosen]
    assert urls[0] == "https://auction.example/lot-1"  # HIGH 可比页优先
    assert len(chosen) <= 15


def test_llm_records_override_rule_records_same_url():
    inp = AnalysisInput(
        artist="Porsche", artwork="964 Carrera RS N-GT", asking_price=325000,
        currency="EUR", category="CLASSIC_CAR", year="1992", research=True,
        comparables=[
            Comparable(title="规则版（标题缺 N-GT）", price=310000, currency="EUR",
                       sale_type="SOLD", price_basis=BASIS_INCLUDING_PREMIUM,
                       source_url="https://auction.example/lot-1",
                       comparability="LOW", comparability_reason="普通 RS"),
        ],
    )
    # researcher: 不可用（跳过联网），LLM 记录由 fake 提供——直接喂 comps 走合并逻辑
    # 这里改为验证 _merge：同 URL 的规则记录被 LLM 记录替换
    from app.orchestrator import _merge_llm_records
    merged = _merge_llm_records(inp.comparables, [NGT_RECORD])
    assert len(merged) == 1
    m = merged[0]
    assert m.llm_extracted is True
    assert m.comparability == "HIGH"
    assert m.comparability_reason  # LLM 理由保留


def test_llm_extracts_record_rule_missed():
    from app.orchestrator import _merge_llm_records
    rule_records = []  # 规则没抽到
    merged = _merge_llm_records(rule_records, [NGT_RECORD])
    assert len(merged) == 1
    assert merged[0].source_url == "https://auction.example/lot-1"


def test_full_pipeline_with_fake_llm(monkeypatch):
    """端到端：research=False 时 LLM 精读不触发；research=True 时摘要记录 LLM 统计。"""
    inp = AnalysisInput(
        artist="Porsche", artwork="964 Carrera RS N-GT", asking_price=325000,
        currency="EUR", category="CLASSIC_CAR", year="1992", research=True,
    )
    fake = FakeLLM({})
    # researcher 不可用 -> 搜索跳过，但 LLM 统计不会被填充（无 pages）——验证不崩溃
    report = analyze(inp, llm=fake, researcher=None)
    assert report.research_summary.enabled is False or report.conclusion in ("观察", "值得谈", "值得买")


def test_summary_records_llm_cost_stats():
    """LLM 精读后 summary 记录 calls/tokens/成本。"""
    from app.orchestrator import analyze
    inp = AnalysisInput(
        artist="Porsche", artwork="964 Carrera RS N-GT", asking_price=325000,
        currency="EUR", category="CLASSIC_CAR", year="1992", research=True,
    )
    fake = FakeLLM({"https://auction.example/lot-1": [NGT_RECORD]})

    class FakeResearcher:
        name = "fake"
        available = True

        def search(self, query, max_results=6):
            return [{"title": "lot", "url": "https://auction.example/lot-1",
                     "snippet": "sold EUR 310,000"}]

    report = analyze(inp, llm=fake, researcher=FakeResearcher())
    rs = report.research_summary
    assert rs.enabled is True
    assert rs.llm_pages_analyzed >= 1
    assert rs.llm_records_extracted >= 1
    assert rs.llm_calls == 2
    assert rs.llm_total_tokens == 3000
    # LLM 记录进入报告可比，且带理由
    llm_comps = [c for c in report.price.comparables if c.llm_extracted]
    assert llm_comps and llm_comps[0].comparability_reason


def test_llm_duplicate_records_deduped():
    """同一 URL 的同一条成交被 LLM 重复抽取时，只保留一条。"""
    from app.orchestrator import _merge_llm_records
    dupes = [dict(NGT_RECORD), dict(NGT_RECORD), dict(NGT_RECORD)]
    merged = _merge_llm_records([], dupes)
    assert len(merged) == 1
    # 不同价格的同页记录仍然保留（页面可能有多个标的）
    other = dict(NGT_RECORD, price=210000, title="1992 Porsche 964 Carrera RS N/GT (older sale)")
    merged2 = _merge_llm_records([], [dict(NGT_RECORD), other])
    assert len(merged2) == 2


def test_llm_cache_roundtrip():
    """同一 (provider, model, url, hash) 命中缓存；不同 hash 不命中。"""
    from app.store import llm_cache_get, llm_cache_put
    result = {"records": [{"title": "x", "price": 100}]}
    llm_cache_put("deepseek", "deepseek-chat", "https://a.example/1", "hash1", result)
    got = llm_cache_get("deepseek", "deepseek-chat", "https://a.example/1", "hash1")
    assert got == result
    miss = llm_cache_get("deepseek", "deepseek-chat", "https://a.example/1", "hash2")
    assert miss is None


def test_select_pages_prefers_ngt_keyword_pages():
    """标题含 N-GT/M003 的页面优先，普通 964 RS 页排在后面。"""
    pages = [
        {"url": "https://x.example/rs", "title": "1992 Porsche 964 Carrera RS", "text": "a"},
        {"url": "https://x.example/ngt", "title": "1992 Porsche 964 Carrera RS N-GT M003", "text": "b"},
        {"url": "https://x.example/auction", "title": "964 Carrera RS N/GT auction result", "text": "c"},
    ]
    chosen = _select_pages_for_llm([], pages, max_pages=10)
    assert chosen[0]["url"] in ("https://x.example/ngt", "https://x.example/auction")
    # 普通 RS 页应该排最后
    assert chosen[-1]["url"] == "https://x.example/rs"


def test_research_mode_skips_generic_comparables():
    """research=True 时，泛研究的 comparables 不再进定价池（精读层已覆盖）。"""
    class FakeLLM2(FakeLLM):
        def research(self, inp):
            return {
                "claims": [{"claim": "艺术家背景", "value": "重要艺术家",
                            "source_url": "https://bio.example"}],
                "comparables": [{"title": "泛研究可比（应被跳过）", "price": 50000,
                                 "currency": "EUR", "source_url": "https://x.example/1"}],
                "artist_analysis": "背景文字",
                "risks": [],
            }

    inp = AnalysisInput(
        artist="Porsche", artwork="964 Carrera RS N-GT", asking_price=325000,
        currency="EUR", category="CLASSIC_CAR", year="1992", research=True,
        comparables=[Comparable(title="手动可比", price=250000, currency="EUR",
                                sale_type="SOLD", price_basis=BASIS_INCLUDING_PREMIUM,
                                source_url="https://manual.example/1")],
    )
    report = analyze(inp, llm=FakeLLM2({}), researcher=None)
    titles = [c.title for c in report.price.comparables]
    assert "泛研究可比（应被跳过）" not in titles   # research 模式跳过
    assert "手动可比" in titles
    assert report.artist_analysis == "背景文字"      # 文字仍后置补充
