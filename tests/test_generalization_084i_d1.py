from pathlib import Path

import pytest

from app.radar import create_radar, expand_queries, list_discoveries, parse_natural_target, parse_radar_text, run_radar


ART = [
    "Yayoi Kusama Infinity Nets oil on canvas 1998 under $50,000",
    "Louise Bourgeois bronze spider sculpture",
    "David Hockney swimming pool print signed edition",
    "赵无极 抽象油画 1985 低于50万",
    "Banksy Girl with Balloon screenprint red",
    "Georgia O'Keeffe flower watercolor on paper",
    "Ansel Adams Yosemite photograph 1942",
    "草间弥生 南瓜 雕塑 黄色",
    "Gerhard Richter Abstraktes Bild oil canvas",
    "Sonia Delaunay textile gouache geometric",
]
FURNITURE = [
    "Eames Lounge Chair by Charles and Ray Eames for Herman Miller, 1970s leather",
    "Finn Juhl Model 45 armchair by Finn Juhl for Niels Vodder teak",
    "Arco floor lamp by Achille Castiglioni for Flos marble",
    "George Nakashima Conoid dining table walnut 1960s",
    "Pierre Paulin Ribbon chair by Pierre Paulin for Artifort orange",
    "Hans Wegner CH24 dining chair for Carl Hansen oak",
    "Mario Bellini Camaleonda sofa for B&B Italia brown leather",
    "柳宗理 蝴蝶凳 天童木工 胶合板",
    "Gio Ponti sideboard by Gio Ponti for Singer & Sons walnut",
    "Gaetano Pesce UP5 armchair for B&B Italia red",
]
CARS = [
    "1967 Ford Mustang Shelby GT500 manual fastback",
    "1973 BMW 3.0 CSL Batmobile manual coupe",
    "1961 Jaguar E-Type Series 1 roadster",
    "1957 Chevrolet Bel Air V8 automatic coupe",
    "1984 Audi Quattro turbo manual coupe",
    "1971 Datsun 240Z manual left hand drive",
    "1969 Alfa Romeo 1750 GTV carbureted",
    "1988 Lancia Delta Integrale 8V manual",
    "1976 Lotus Esprit Series 1 road car",
    "1964 Aston Martin DB5 manual saloon",
]


@pytest.mark.parametrize("category,sample", [("ART", x) for x in ART] + [("FURNITURE", x) for x in FURNITURE] + [("CLASSIC_CAR", x) for x in CARS])
def test_random_generalization_set_parses_and_plans(category, sample):
    target = parse_natural_target(sample)
    radar = {"name": sample, "query_original": sample, "rules": parse_radar_text(sample)}
    assert target["category"] == category
    assert target["original_input"] == sample and "unclassified_terms" in target
    queries = expand_queries(radar)
    assert len(queries) >= 3 and any("site:" not in query for query in queries)


def test_parser_handles_unknown_entity_and_preserves_unknown_terms():
    text = "Aurelia Voss mysterious resin sculpture moon-dust finish under €9000"
    target = parse_natural_target(text)
    assert target["category"] == "ART" and target["artist"] != "unknown"
    assert {"mysterious", "moon-dust"} & {x.lower() for x in target["unclassified_terms"]}


@pytest.mark.parametrize("sample", [ART[1], FURNITURE[2], CARS[4]])
def test_unseen_target_can_search_and_create_candidate(monkeypatch, sample):
    class Provider:
        name = "fixture"; status = "OK"
        def search(self, query, max_results=5):
            return [{"title": sample + " available", "snippet": "price on request available", "url": "https://market.example/item"}]
    radar = create_radar(sample)
    monkeypatch.setattr("app.radar.expand_queries", lambda _: [sample])
    source = {"name":"Full web", "domain":"full-web.local", "compliance_reviewed":True,
              "compliance_mode":"SEARCH_ONLY", "health_status":"SEARCH_ONLY", "searchable_by_external_engine":True}
    result = run_radar(radar["id"], Provider(), [source], False)
    assert result["queries_completed"] >= 1 and result["raw_results"] >= 1
    assert any(row["radar_id"] == radar["id"] for row in list_discoveries())


def test_query_generation_from_schema_not_entity_name():
    a = parse_radar_text("Unknownmaker QX71 armchair by Unknownmaker for Genericworks steel")
    b = parse_radar_text("Anothermaker QX71 armchair by Anothermaker for Genericworks steel")
    qa = expand_queries({"name":"A", "query_original":"A", "rules":a})
    qb = expand_queries({"name":"B", "query_original":"B", "rules":b})
    assert len(qa) == len(qb) and any("QX71" in q for q in qa) and any("QX71" in q for q in qb)


def test_no_object_special_cases_in_radar_runtime():
    source = (Path(__file__).parents[1] / "app" / "radar.py").read_text(encoding="utf-8").lower()
    for forbidden in ("takis", "prouvé", "prouve", "964 n-gt", "m003", "jean prouvé", "leboncoin"):
        assert forbidden not in source


def test_no_special_case_takis():
    test_no_object_special_cases_in_radar_runtime()


def test_no_special_case_jean_prouve():
    test_no_object_special_cases_in_radar_runtime()


def test_no_special_case_porsche_964():
    test_no_object_special_cases_in_radar_runtime()
