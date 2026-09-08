"""Verified market references — human-curated auction benchmarks shared by
the price surface (/api/prices), the radar homepage and the Analyst pipeline.

Why this module exists:
- /api/prices and the homepage used these records for display only;
- the Analyst pipeline (orchestrator.analyze) did not, so a user who entered
  "Takis Signal Lamp" with an asking price got "no verifiable data" while the
  same system held a verified benchmark for that exact family.

Rules (inherited from the original implementation):
- Every record is a real auction lot page with an explicit sold price, a date
  and one price basis. Nothing here is inferred or estimated.
- Only curated families are present. A family match is conservative:
  Takis Signal requires artist takis/vassilakis AND artwork signal/lamp.
- These records are SOLD, Tier 1, INCLUDING_PREMIUM. USD lots are converted
  with the sale-date ECB reference rate kept on the record, so the injected
  comparables share the same EUR-normalized basis as /api/prices.
"""
import re
from typing import List, Optional

from .models import BASIS_INCLUDING_PREMIUM, SALE_SOLD, Comparable, now_iso

VERIFIED_MARKET_REFERENCES = [
    {
        "key": "takis_signal_lamp_1968",
        "artist_terms": ("takis", "vassilakis"),
        "object_terms": ("signal",),
        "scope": "Takis 单灯 Signal / Signal Lamp · 开放版 · 约 195–220 cm",
        "currency": "EUR",
        "price_basis": "成交价含买家佣金；美元成交按拍卖日 ECB USD/EUR 参考汇率换算",
        "records": [
            {
                "title": "Signal Lamp, Series 1, 1968", "lot": "79040",
                "sold_at": "2023-10-26", "price": 6562.50, "currency": "USD",
                "fx_usd_per_eur": 1.054, "auction_house": "Heritage Auctions",
                "source_name": "Heritage Auctions",
                "source_url": "https://fineart.ha.com/c/search/results.zx?archive_state=5327&art_category=2792&dept=1544&layout=gallery&mode=archive&sold_status=1526~1524",
            },
            {
                "title": "Signal Lamp, Series 2, 1968", "lot": "63015",
                "sold_at": "2025-05-15", "price": 9375, "currency": "USD",
                "fx_usd_per_eur": 1.1185, "auction_house": "Heritage Auctions",
                "source_name": "Heritage Auctions",
                "source_url": "https://fineart.ha.com/itm/lighting/vassilakis-takis-signal-lamp-series-2-greece-1968-painted-aluminum-crackle-lacquered-aluminum-chrome-plated/a/8217-63015.s",
            },
            {
                "title": "Signal Lamp, Series 3, 1968", "lot": "63016",
                "sold_at": "2025-05-15", "price": 9375, "currency": "USD",
                "fx_usd_per_eur": 1.1185, "auction_house": "Heritage Auctions",
                "source_name": "Heritage Auctions",
                "source_url": "https://fineart.ha.com/itm/lighting/vassilakis-takis-signal-lamp-series-3-greece-1968-painted-aluminum-crackle-lacquered-aluminum-chrome-plated/a/8217-63016.s",
            },
            {
                "title": "Signal Lamp, Series 1, 1968", "lot": "63017",
                "sold_at": "2025-05-15", "price": 6250, "currency": "USD",
                "fx_usd_per_eur": 1.1185, "auction_house": "Heritage Auctions",
                "source_name": "Heritage Auctions",
                "source_url": "https://fineart.ha.com/itm/lighting/vassilakis-takis-signal-lamp-series-1-greece-1968-chrome-plated-steel-enameled-and-powdercoated/a/8217-63017.s",
            },
            {
                "title": "Signal Lamp, Series 1, 1968", "lot": "79059",
                "sold_at": "2025-10-22", "price": 6250, "currency": "USD",
                "fx_usd_per_eur": 1.1587, "auction_house": "Heritage Auctions",
                "source_name": "Heritage Auctions",
                "source_url": "https://fineart.ha.com/c/search/results.zx?archive_state=5327&art_region_country=1965&dept=1544&layout=gallery&mode=archive&sold_status=1526~1524",
            },
            {
                "title": "Signal Lamp, Series 3, 1968", "lot": "106",
                "sold_at": "2026-02-26", "price": 12700, "currency": "USD",
                "fx_usd_per_eur": 1.1814, "auction_house": "Wright",
                "source_name": "Wright / LiveAuctioneers",
                "source_url": "https://www.liveauctioneers.com/en-gb/catalog/407028_design/",
            },
            {
                "title": "Signal Lamp, Series 2, 1968", "lot": "123",
                "sold_at": "2026-02-26", "price": 21590, "currency": "USD",
                "fx_usd_per_eur": 1.1814, "auction_house": "Wright",
                "source_name": "Wright / LiveAuctioneers",
                "source_url": "https://www.liveauctioneers.com/en-gb/catalog/407028_design/",
            },
            {
                "title": "Signal Lamp, Series 1, 1968", "lot": "121",
                "sold_at": "2026-04-28", "price": 5586, "currency": "USD",
                "fx_usd_per_eur": 1.168, "auction_house": "Wright",
                "source_name": "Wright / LiveAuctioneers",
                "source_url": "https://www.liveauctioneers.com/catalog/414116_design/",
            },
            {
                "title": "Signal, circa 1970", "lot": "187",
                "sold_at": "2026-05-12", "price": 10496, "currency": "EUR",
                "auction_house": "Piasa", "source_name": "Piasa",
                "source_url": "https://www.piasa.fr/en/auctions/contemporary-art-abstraction-figuration",
            },
            {
                "title": "Signal, circa 1970", "lot": "190",
                "sold_at": "2026-05-12", "price": 10496, "currency": "EUR",
                "auction_house": "Piasa", "source_name": "Piasa",
                "source_url": "https://www.piasa.fr/en/auctions/contemporary-art-abstraction-figuration",
            },
        ],
    },
]


def _subject_key(subject_text):
    """Conservative subject identity: records only aggregate inside one comparable family."""
    text = (subject_text or "").lower()
    if ("takis" in text or "vassilakis" in text) and "signal" in text:
        return "art:takis:signal:single-open-edition"
    tokens = re.findall(r"[a-z0-9]+", text)
    stop = {"for", "sale", "sold", "price", "auction", "result", "the", "and"}
    return "generic:" + "-".join([x for x in tokens if x not in stop][:12])


def _seed_verified_market_references():
    """Import curated evidence into the same generic store used by every category."""
    from .store import price_record_upsert
    for ref in VERIFIED_MARKET_REFERENCES:
        for source_row in ref["records"]:
            row = dict(source_row)
            if row["currency"] == "USD":
                normalized = round(float(row["price"]) / float(row["fx_usd_per_eur"]), 2)
            elif row["currency"] == "EUR":
                normalized = round(float(row["price"]), 2)
            else:
                normalized = None
            price_record_upsert({
                "subject_key": ref.get("subject_key", "art:takis:signal:single-open-edition"),
                "category": ref.get("category", "ART"),
                "maker": ref.get("maker", "Takis"), "object_name": row["title"],
                "series": ref.get("series", "Signal"),
                "sale_type": "SOLD", "sold_at": row["sold_at"], "price": row["price"],
                "currency": row["currency"], "normalized_eur": normalized,
                "fx_rate": row.get("fx_usd_per_eur"), "fx_source": "ECB sale-date reference" if row.get("fx_usd_per_eur") else "",
                "auction_house": row["auction_house"], "source_name": row["source_name"],
                "source_url": row["source_url"], "source_tier": 1,
                "price_basis": "INCLUDING_PREMIUM", "comparable_level": "EXACT_FAMILY",
                "lot": row.get("lot"), "raw": row,
            })


def match_reference(inp) -> List[Comparable]:
    """Return curated SOLD comparables for the analysis input, if any family matches.

    Conservative on purpose: a match requires the artist AND the object terms of
    a curated family. USD records are normalized to EUR with their recorded
    sale-date ECB rate so the pricing pipeline sees one consistent basis.
    """
    artist = (getattr(inp, "artist", "") or "").lower()
    artwork = (getattr(inp, "artwork", "") or "").lower()
    subject = f"{artist} {artwork}"
    for ref in VERIFIED_MARKET_REFERENCES:
        artist_hit = any(t in subject for t in ref.get("artist_terms", ()))
        object_hit = any(t in subject for t in ref.get("object_terms", ()))
        if not (artist_hit and object_hit):
            continue
        out: List[Comparable] = []
        for row in ref["records"]:
            price = float(row["price"])
            currency = str(row["currency"] or "EUR").upper()
            if currency == "USD" and row.get("fx_usd_per_eur"):
                price = round(price / float(row["fx_usd_per_eur"]), 2)
                currency = "EUR"
            excerpt = f"Lot {row.get('lot') or '—'}, {row.get('auction_house') or ''}, {row.get('sold_at') or ''}".strip()
            out.append(Comparable(
                title=str(row["title"])[:120],
                year=None,
                price=price,
                currency=currency,
                sold_at=str(row.get("sold_at") or "")[:10],
                is_auction=True,
                source_name=str(row.get("source_name") or row.get("auction_house") or "")[:80],
                source_url=str(row.get("source_url") or ""),
                sale_type=SALE_SOLD,
                price_basis=BASIS_INCLUDING_PREMIUM,
                source_tier=1,
                comparability="HIGH",
                comparability_reason="人工逐条核验的拍卖 lot 基准（同一作品系列/车型）",
                evidence_excerpt=excerpt[:300] or None,
                attributes={"origin": "verified_reference", "lot": row.get("lot"),
                            "auction_house": row.get("auction_house")},
                retrieved_at=now_iso(),
            ))
        return out
    return []
