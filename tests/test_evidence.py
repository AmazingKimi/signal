"""验收标准 A + B：数据可信、没有幻觉价格。"""
from app.evidence import EvidenceStore
from app.models import VERIFIED, UNVERIFIED, CONFLICTING


def test_no_source_means_unverified():
    store = EvidenceStore()
    e = store.add(claim="2024 年某作品成交", value="€18,000", source_name="AI 说它记得")
    assert e.verification_status == UNVERIFIED  # 没有来源 = 未验证


def test_with_url_means_verified():
    store = EvidenceStore()
    e = store.add(
        claim="某作品成交价", value="€14,432",
        source_url="http://art.salon/artwork/xxx",
        source_name="Artcurial",
    )
    assert e.verification_status == VERIFIED


def test_conflicting_values_with_sources():
    store = EvidenceStore()
    e1 = store.add(claim="作品创作年份", value="1968",
                   source_url="https://a.example/1", source_name="来源A")
    e2 = store.add(claim="作品创作年份", value="1970",
                   source_url="https://b.example/2", source_name="来源B")
    assert e1.verification_status == CONFLICTING
    assert e2.verification_status == CONFLICTING


def test_same_value_twice_not_conflicting():
    store = EvidenceStore()
    e1 = store.add(claim="作品创作年份", value="1968",
                   source_url="https://a.example/1", source_name="来源A")
    e2 = store.add(claim="作品创作年份", value="1968",
                   source_url="https://b.example/2", source_name="来源B")
    assert e1.verification_status == VERIFIED
    assert e2.verification_status == VERIFIED
