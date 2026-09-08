from app.radar import classify_availability, score_actionability, same_object_cluster_id


def rec(title, snippet="", url="https://example.com/item", price=None):
    return {"title": title, "raw": {"snippet": snippet}, "source_url": url,
            "source_name": "Example", "asking_price": price, "maker": "Takis",
            "object_name": "Signal", "year": "1974"}


def test_availability_negatives_win():
    assert classify_availability(rec("Takis Signal sold for EUR 50,000", "available in auction archive")) == "SOLD"
    assert classify_availability(rec("Takis exhibition", url="https://tate.org.uk/art")) == "MUSEUM"
    assert classify_availability(rec("Takis biography article")) == "ARTICLE"


def test_current_actionable_statuses_pass_threshold():
    match = {"matched": True, "reasons": ["符合 Takis", "符合 Signal"]}
    for title, expected in [("Takis Signal for sale EUR 50,000", "FOR_SALE"),
                            ("Takis Signal price on request", "INQUIRE"),
                            ("Takis Signal upcoming auction bid now", "LIVE_AUCTION")]:
        item = rec(title, price=50000 if "EUR" in title else None)
        status = classify_availability(item)
        quality = score_actionability(item, match, status)
        assert status == expected
        assert quality["discovery_eligible"] and quality["actionability_score"] >= 60


def test_historical_is_not_discovery():
    item = rec("Takis Signal sold lot")
    quality = score_actionability(item, {"matched": True, "reasons": ["符合 Takis"]}, "SOLD")
    assert not quality["discovery_eligible"]
    assert quality["historical_context"]


def test_artist_index_is_reference_even_if_snippet_has_price():
    item = rec("Takis - Phillips Auction", "Auction estimate GBP 12,000",
               "https://phillips.com/artist/1108/takis-panayiotis-vassilakis", 12000)
    assert classify_availability(item) == "REFERENCE"


def test_cluster_ignores_market_boilerplate_and_price():
    a = rec("Takis Signal for sale EUR 50,000")
    b = rec("Takis Signal listing EUR 55,000")
    assert same_object_cluster_id(a) == same_object_cluster_id(b)
