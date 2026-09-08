"""SIGNAL RADAR 0.8 — deterministic cross-source discovery layer.

The legacy analyst/price/decision code remains untouched.  This module owns the
smaller 0.8 surface: Radar rules, source coverage, deduplication, discoveries,
run reports, feedback and notification deduplication.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import smtplib
import time
import unicodedata
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlparse, urlunparse

from .models import now_iso
from .research import build_search_provider
from .store import _conn
from . import changes
from .source_urls import require_source_url


LEGACY_SOURCES = [
    ("Artcurial", "artcurial.com", "ART", "FR", "fr", "PUBLIC_PAGE", "A"),
    ("Bonhams", "bonhams.com", "MULTI", "GB", "en", "PUBLIC_PAGE", "A"),
    ("Christie's", "christies.com", "MULTI", "GB", "en", "PUBLIC_PAGE", "A"),
    ("Sotheby's", "sothebys.com", "MULTI", "US", "en", "PUBLIC_PAGE", "A"),
    ("Phillips", "phillips.com", "ART", "GB", "en", "PUBLIC_PAGE", "A"),
    ("RM Sotheby's", "rmsothebys.com", "CLASSIC_CAR", "CA", "en", "PUBLIC_PAGE", "A"),
    ("Bring a Trailer", "bringatrailer.com", "CLASSIC_CAR", "US", "en", "PUBLIC_PAGE", "A"),
    ("Car & Classic", "carandclassic.com", "CLASSIC_CAR", "GB", "en", "PUBLIC_PAGE", "B"),
    ("Collecting Cars", "collectingcars.com", "CLASSIC_CAR", "GB", "en", "PUBLIC_PAGE", "B"),
    ("Piasa", "piasa.fr", "ART", "FR", "fr", "PUBLIC_PAGE", "B"),
    ("Drouot", "drouot.com", "MULTI", "FR", "fr", "PUBLIC_PAGE", "B"),
    ("LiveAuctioneers", "liveauctioneers.com", "MULTI", "US", "en", "PUBLIC_PAGE", "B"),
    ("Invaluable", "invaluable.com", "MULTI", "US", "en", "PUBLIC_PAGE", "B"),
    ("1stDibs", "1stdibs.com", "DESIGN", "US", "en", "PUBLIC_PAGE", "B"),
    ("The Saleroom", "the-saleroom.com", "MULTI", "GB", "en", "PUBLIC_PAGE", "B"),
    ("Classic Driver", "classicdriver.com", "CLASSIC_CAR", "CH", "en", "PUBLIC_PAGE", "B"),
    ("Classic.com", "classic.com", "CLASSIC_CAR", "US", "en", "PUBLIC_PAGE", "B"),
    ("Auction.fr", "auction.fr", "MULTI", "FR", "fr", "PUBLIC_PAGE", "B"),
]

REGISTRY_FILE = Path(__file__).with_name("source_registry.json")
SOURCE_COLUMNS = {
    "source_type": "TEXT DEFAULT 'AGGREGATOR'", "region": "TEXT DEFAULT ''",
    "languages_json": "TEXT DEFAULT '[]'", "official_url": "TEXT DEFAULT ''",
    "access_mode": "TEXT DEFAULT 'SEARCH_ONLY'", "requires_login": "INTEGER DEFAULT 0",
    "robots_checked": "INTEGER DEFAULT 0", "tos_checked": "INTEGER DEFAULT 0",
    "js_rendered": "INTEGER DEFAULT 0", "anti_bot_level": "TEXT DEFAULT 'UNKNOWN'",
    "searchable_by_external_engine": "INTEGER DEFAULT 1", "enabled": "INTEGER DEFAULT 1",
    "last_check": "TEXT", "avg_response_ms": "INTEGER", "last_new_discovery": "TEXT",
    "failure_reason": "TEXT DEFAULT ''", "health_status": "TEXT DEFAULT 'CONFIGURED'",
    "priority": "INTEGER DEFAULT 50", "rotation_bucket": "INTEGER DEFAULT 0",
    "compliance_reviewed": "INTEGER DEFAULT 0", "compliance_mode": "TEXT DEFAULT 'SEARCH_ONLY'",
    "compliance_checked_at": "TEXT", "last_checked_at": "TEXT",
    "compliance_evidence_url": "TEXT DEFAULT ''", "compliance_note": "TEXT DEFAULT ''",
}

RUN_COLUMNS = {
    # Deprecated source-check fields remain for historical database compatibility only.
    "registered_sources": "INTEGER DEFAULT 0", "reviewed_eligible_sources": "INTEGER DEFAULT 0",
    "successfully_checked_sources": "INTEGER DEFAULT 0", "skipped_unreviewed": "INTEGER DEFAULT 0",
    "skipped_login_required": "INTEGER DEFAULT 0", "skipped_do_not_automate": "INTEGER DEFAULT 0",
    "skipped_review_expired": "INTEGER DEFAULT 0", "skipped_paused": "INTEGER DEFAULT 0",
    "failed_timeout": "INTEGER DEFAULT 0", "failed_blocked": "INTEGER DEFAULT 0",
    "failed_network": "INTEGER DEFAULT 0", "failed_parser": "INTEGER DEFAULT 0",
    "failed_other": "INTEGER DEFAULT 0", "coverage_ratio_eligible": "REAL DEFAULT 0",
    "coverage_ratio_run": "REAL DEFAULT 0", "run_status": "TEXT DEFAULT 'FAILED'",
    "coverage_snapshot_json": "TEXT DEFAULT '{}'", "integrity_status": "TEXT DEFAULT 'OK'",
    "source_details_json": "TEXT DEFAULT '[]'",
    "search_provider_status": "TEXT DEFAULT 'SEARCH_PROVIDER_NOT_CONFIGURED'",
    "search_provider_name": "TEXT DEFAULT 'none'", "provider_detail": "TEXT DEFAULT ''",
    "queries_planned": "INTEGER DEFAULT 0", "queries_completed": "INTEGER DEFAULT 0",
    "queries_failed": "INTEGER DEFAULT 0", "search_results_received": "INTEGER DEFAULT 0",
    "search_results_processed": "INTEGER DEFAULT 0", "search_results_deduplicated": "INTEGER DEFAULT 0",
    "target_market_sources_registered": "INTEGER DEFAULT 0",
    "relevant_results": "INTEGER DEFAULT 0", "actionable_results": "INTEGER DEFAULT 0",
    "discarded_sold": "INTEGER DEFAULT 0", "discarded_past_auction": "INTEGER DEFAULT 0",
    "discarded_museum": "INTEGER DEFAULT 0", "discarded_article": "INTEGER DEFAULT 0",
    "discarded_duplicate": "INTEGER DEFAULT 0",
    "queries_added_dynamically": "INTEGER DEFAULT 0", "historical_results": "INTEGER DEFAULT 0",
    "already_seen_results": "INTEGER DEFAULT 0", "rejected_results": "INTEGER DEFAULT 0",
    "current_query": "TEXT DEFAULT ''",
}

DISCOVERY_COLUMNS = {
    "candidate_id": "INTEGER",
    "availability": "TEXT DEFAULT 'UNKNOWN'", "actionability_score": "INTEGER DEFAULT 0",
    "freshness_score": "INTEGER DEFAULT 0", "discovery_eligible": "INTEGER DEFAULT 0",
    "historical_context": "INTEGER DEFAULT 0", "discard_reason": "TEXT DEFAULT ''",
    "same_object_cluster_id": "TEXT DEFAULT ''",
    "source_priority": "TEXT DEFAULT 'STANDARD'",
    "discovered_at": "TEXT", "run_id": "INTEGER", "query_id": "TEXT DEFAULT ''",
    "stream_sequence": "INTEGER DEFAULT 0",
}

P0_ART_DOMAINS = (
    "sothebys.com", "christies.com", "artsy.net", "phillips.com",
    "liveauctioneers.com", "wright20.com",
)
FURNITURE_P0_DOMAINS = ("1stdibs.com", "pamono.com", "design-market.eu",
                        "selency.fr", "vinterior.co", "wright20.com", "phillips.com",
                        "sothebys.com", "christies.com", "liveauctioneers.com")

DOMAIN_ONTOLOGY = {
    "ART": {
        "types": ("painting", "sculpture", "drawing", "print", "photograph", "photography", "ceramic",
                  "installation", "artwork", "canvas", "portrait", "版画", "绘画", "油画", "雕塑", "摄影", "艺术品", "作品"),
        "attributes": ("oil", "acrylic", "watercolor", "bronze", "steel", "wood", "paper", "canvas",
                       "edition", "signed", "abstract", "figurative", "油彩", "水彩", "青铜", "纸本", "签名", "限量"),
    },
    "FURNITURE": {
        "types": ("lounge chair", "easy chair", "dining chair", "armchair", "chair", "table", "desk", "stool",
                  "cabinet", "sideboard", "bookcase", "fauteuil", "chaise longue", "sofa", "lamp", "light",
                  "椅", "桌", "沙发", "柜", "灯", "家具"),
        "attributes": ("wood", "steel", "aluminium", "aluminum", "leather", "fabric", "glass", "marble",
                       "vintage", "edition", "original", "木", "钢", "铝", "皮革", "玻璃", "大理石", "原版"),
    },
    "CLASSIC_CAR": {
        "types": ("coupe", "coupé", "convertible", "roadster", "sedan", "saloon", "wagon", "targa", "spider",
                  "fastback", "classic car", "vintage car", "跑车", "敞篷", "轿跑", "老爷车", "经典车"),
        "attributes": ("manual", "automatic", "engine", "transmission", "matching numbers", "left hand drive",
                       "right hand drive", "competition", "race", "road", "carbureted", "fuel injection",
                       "手动", "自动", "发动机", "变速箱", "赛车", "公路版"),
    },
}


def _fold(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value or "") if not unicodedata.combining(c)).lower()


def _ensure_columns(conn, table: str, columns: dict[str, str]) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, ddl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def _registry_seed() -> list[dict]:
    if REGISTRY_FILE.exists():
        return json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    return [{"name": x[0], "domain": x[1], "category": x[2], "country": x[3],
             "languages": [x[4]], "access_mode": "SEARCH_ONLY", "tier": x[6]}
            for x in LEGACY_SOURCES]


def init_radar_tables() -> None:
    conn = _conn()
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS radars (
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
          query_original TEXT NOT NULL, rules_json TEXT NOT NULL,
          sources_json TEXT DEFAULT '[]', languages_json TEXT DEFAULT '[]',
          schedule TEXT DEFAULT 'TWICE_DAILY', enabled INTEGER DEFAULT 1,
          notify_unknown_price INTEGER DEFAULT 1, created_at TEXT NOT NULL,
          last_run_at TEXT, next_run_at TEXT
        );
        CREATE TABLE IF NOT EXISTS source_registry (
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
          domain TEXT NOT NULL UNIQUE, category TEXT DEFAULT 'MULTI',
          country TEXT DEFAULT '', language TEXT DEFAULT 'en',
          access_method TEXT DEFAULT 'SEARCH', tier TEXT DEFAULT 'B',
          status TEXT DEFAULT 'ACTIVE', last_success TEXT, success_count INTEGER DEFAULT 0,
          failure_count INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS radar_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT, radar_id INTEGER NOT NULL,
          started_at TEXT NOT NULL, finished_at TEXT,
          planned_sources INTEGER DEFAULT 0, successful_sources INTEGER DEFAULT 0,
          failed_sources INTEGER DEFAULT 0, raw_results INTEGER DEFAULT 0,
          normalized_results INTEGER DEFAULT 0, duplicates_removed INTEGER DEFAULT 0,
          matches INTEGER DEFAULT 0, new_discoveries INTEGER DEFAULT 0,
          notifications_sent INTEGER DEFAULT 0, failures_json TEXT DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS discoveries (
          id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT NOT NULL UNIQUE,
          radar_id INTEGER NOT NULL, title TEXT NOT NULL, maker TEXT DEFAULT '',
          object_name TEXT DEFAULT '', year TEXT, asking_price REAL, currency TEXT DEFAULT 'EUR',
          source_name TEXT DEFAULT '', source_url TEXT DEFAULT '', image_url TEXT DEFAULT '',
          first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, match_level TEXT DEFAULT 'MATCH',
          match_reasons_json TEXT DEFAULT '[]', possible_duplicate INTEGER DEFAULT 0,
          status TEXT DEFAULT 'NEW', useful INTEGER, notified_at TEXT, raw_json TEXT DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS discovery_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, discovery_id INTEGER NOT NULL,
          event_type TEXT NOT NULL, old_value TEXT DEFAULT '', new_value TEXT DEFAULT '',
          created_at TEXT NOT NULL, notified INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS radar_run_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL,
          event_type TEXT NOT NULL, query_id TEXT DEFAULT '', payload_json TEXT DEFAULT '{}',
          created_at TEXT NOT NULL
        );
        """)
        _ensure_columns(conn, "source_registry", SOURCE_COLUMNS)
        _ensure_columns(conn, "radar_runs", RUN_COLUMNS)
        _ensure_columns(conn, "radars", {"deleted_at": "TEXT"})
        _ensure_columns(conn, "discoveries", {
            "also_seen_json": "TEXT DEFAULT '[]'", "primary_source_type": "TEXT DEFAULT 'AGGREGATOR'",
            "original_text": "TEXT DEFAULT ''", "original_language": "TEXT DEFAULT 'und'",
            "translated_text": "TEXT",
            "evidence_level": "TEXT DEFAULT 'SEARCH_RESULT'",
            **DISCOVERY_COLUMNS,
        })
        registry_seed = _registry_seed()
        for src in registry_seed:
            languages = src.get("languages") or [src.get("language") or "en"]
            access = src.get("access_mode") or "SEARCH_ONLY"
            mode = src.get("compliance_mode") or ("LOGIN_REQUIRED" if src.get("requires_login") else
                   "DO_NOT_AUTOMATE" if access == "MANUAL_ONLY" else "SEARCH_ONLY")
            health = src.get("health_status") or ("LOGIN_REQUIRED" if mode == "LOGIN_REQUIRED" else
                     "BLOCKED" if mode == "DO_NOT_AUTOMATE" else "SEARCH_ONLY")
            values = (
                src["name"], src["domain"], src.get("category", "BOTH"), src.get("country", ""),
                languages[0], access, src.get("tier", "B"), src.get("source_type", "AGGREGATOR"),
                src.get("region", ""), json.dumps(languages, ensure_ascii=False),
                src.get("official_url") or f"https://{src['domain']}", access,
                int(bool(src.get("requires_login"))), int(bool(src.get("robots_checked"))),
                int(bool(src.get("tos_checked"))), int(bool(src.get("js_rendered"))),
                src.get("anti_bot_level", "UNKNOWN"), int(bool(src.get("searchable_by_external_engine", True))),
                int(bool(src.get("enabled", True))), health, int(src.get("priority", 50)),
                int(src.get("rotation_bucket", 0)), src.get("notes", ""),
                int(bool(src.get("compliance_reviewed"))), mode, src.get("compliance_checked_at"),
                src.get("compliance_evidence_url", ""), src.get("compliance_note", ""),
                src.get("last_check"), src.get("last_success"),
            )
            conn.execute("""INSERT INTO source_registry
              (name,domain,category,country,language,access_method,tier,source_type,region,languages_json,
               official_url,access_mode,requires_login,robots_checked,tos_checked,js_rendered,anti_bot_level,
               searchable_by_external_engine,enabled,health_status,priority,rotation_bucket,failure_reason,
               compliance_reviewed,compliance_mode,compliance_checked_at,compliance_evidence_url,compliance_note,
               last_check,last_success)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(domain) DO UPDATE SET name=excluded.name,category=excluded.category,country=excluded.country,
               language=excluded.language,access_method=excluded.access_method,tier=excluded.tier,
               source_type=excluded.source_type,region=excluded.region,languages_json=excluded.languages_json,
               official_url=excluded.official_url,access_mode=excluded.access_mode,requires_login=excluded.requires_login,
               robots_checked=excluded.robots_checked,tos_checked=excluded.tos_checked,js_rendered=excluded.js_rendered,
               anti_bot_level=excluded.anti_bot_level,searchable_by_external_engine=excluded.searchable_by_external_engine,
               enabled=excluded.enabled,health_status=excluded.health_status,priority=excluded.priority,
               rotation_bucket=excluded.rotation_bucket,compliance_reviewed=excluded.compliance_reviewed,
               compliance_mode=excluded.compliance_mode,compliance_checked_at=excluded.compliance_checked_at,
               compliance_evidence_url=excluded.compliance_evidence_url,compliance_note=excluded.compliance_note,
               last_check=excluded.last_check""", values)
        domains = [src["domain"] for src in registry_seed]
        conn.execute("UPDATE source_registry SET priority=0 WHERE domain IN (?,?,?,?,?,?)", P0_ART_DOMAINS)
        if REGISTRY_FILE.exists() and domains:
            placeholders = ",".join("?" for _ in domains)
            conn.execute(f"DELETE FROM source_registry WHERE domain NOT IN ({placeholders})", domains)
        conn.commit()
    finally:
        conn.close()


def _rows(sql: str, args=()) -> list[dict]:
    conn = _conn()
    try:
        cur = conn.execute(sql, args)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def parse_natural_target(text: str) -> dict:
    raw = (text or "").strip()
    low, folded = raw.lower(), _fold(raw)
    head = _fold(re.split(r"[,，;；]", raw, maxsplit=1)[0])
    scores = {category: (sum(1 for term in data["types"] if _fold(term) in head) +
                         sum(1 for term in data["attributes"] if _fold(term) in folded))
              for category, data in DOMAIN_ONTOLOGY.items()}
    explicit = {"art":"ART", "艺术":"ART", "furniture":"FURNITURE", "家具":"FURNITURE",
                "classic car":"CLASSIC_CAR", "经典车":"CLASSIC_CAR", "老爷车":"CLASSIC_CAR"}
    category = next((value for key, value in explicit.items()
                     if re.search(rf"(?<![a-z]){re.escape(key)}(?![a-z])", folded)), max(scores, key=scores.get))
    if not max(scores.values()) and re.match(r"\s*(?:19|20)\d{2}\b", raw) and len(re.findall(r"[A-Za-z0-9]+", raw)) >= 3:
        category = "CLASSIC_CAR"
    elif not max(scores.values()):
        leading_tokens = re.findall(r"[A-Za-zÀ-ž0-9][A-Za-zÀ-ž0-9+./-]*", re.split(r"[,，;；]", raw, maxsplit=1)[0])[:4]
        if len(leading_tokens) >= 3 and any(re.search(r"\d", token) for token in leading_tokens[1:]):
            category = "CLASSIC_CAR"
    if not scores[category] and category not in explicit.values(): category = "ART"
    target = {"category":category, "original_input":raw, "confidence":0.86 if scores[category] else 0.55,
              "aliases":[], "unclassified_terms":[]}
    period = re.search(r"\b(?:19|20)\d0s\b", low)
    if period: target["period"] = period.group(0)
    year = re.search(r"\b(?:19|20)\d{2}\b", low)
    if year: target["year"] = int(year.group(0))
    currency = "GBP" if "£" in raw or "gbp" in low else "USD" if "$" in raw or "usd" in low else "EUR"
    price = re.search(r"(?:under|below|max(?:imum)?|低于|不超过|最高)\s*[€£$]?\s*([\d,.]+)\s*([kK万]?)", raw, re.I)
    if price:
        value = float(price.group(1).replace(",", ""))
        if price.group(2).lower() == "k": value *= 1000
        if price.group(2) == "万": value *= 10000
        target.update(price_max=value, currency=currency)
    type_terms = DOMAIN_ONTOLOGY[category]["types"]
    object_type = next((term for term in sorted(type_terms, key=len, reverse=True) if _fold(term) in folded), "unknown")
    attributes = [term for term in DOMAIN_ONTOLOGY[category]["attributes"] if _fold(term) in folded]
    color_terms = ("red", "blue", "green", "yellow", "black", "white", "pink", "orange", "purple", "brown",
                   "silver", "gold", "grey", "gray", "红色", "蓝色", "绿色", "黄色", "黑色", "白色", "粉色", "银色", "金色")
    color = next((term for term in color_terms if term in low or term in raw), "unknown")
    if category == "FURNITURE":
        by = re.search(r"(?:\bby\s+|设计(?:师)?[：:]?)(.+?)(?=\s+(?:for|made by|manufactured by)\s+|[,，]|$)", raw, re.I)
        manufacturer_m = re.search(r"(?:\bfor\s+|made by\s+|manufactured by\s+|制造商[：:]?|品牌[：:]?)([^,，]+)", raw, re.I)
        model_m = re.search(r"\b(?:model\s+)?([A-Z]{1,5}[- ]?\d{1,5}[A-Z-]*)\b", raw)
        series_m = re.search(r"\b((?:first|second|third|limited|prototype)\s+(?:series|edition))\b", raw, re.I)
        designer = by.group(1).strip() if by else "unknown"
        target.update(designer=designer, manufacturer=manufacturer_m.group(1).strip() if manufacturer_m else "unknown",
                      brand=manufacturer_m.group(1).strip() if manufacturer_m else "unknown",
                      model=model_m.group(1) if model_m else "unknown", object_type=object_type,
                      series=series_m.group(1) if series_m else "unknown", material=attributes[0] if attributes else "unknown",
                      edition=next((x for x in attributes if x in ("edition", "original", "vintage", "原版")), "unknown"),
                      color=color)
        if designer != "unknown" and _fold(designer) != designer.lower(): target["aliases"] = [designer, _fold(designer).title()]
    elif category == "CLASSIC_CAR":
        clean = re.sub(r"\b(?:19|20)\d{2}\b", " ", raw)
        clean = re.sub(r"[€£$][\d,.]+.*$", " ", clean)
        words = re.findall(r"[A-Za-zÀ-ž0-9][A-Za-zÀ-ž0-9+./-]*", clean)
        marque = words[0] if words else "unknown"
        codes = [x for x in words[1:] if re.search(r"\d", x)]
        option = next((x for x in codes if re.fullmatch(r"[A-Za-z]+[- ]?\d{2,5}", x)), "unknown")
        model_words = words[1:4] if len(words) > 1 else []
        variant_words = words[4:] if len(words) > 4 else []
        target.update(marque=marque, model=" ".join(model_words) or "unknown",
                      variant=" ".join(variant_words) or "unknown", generation=codes[0] if codes else "unknown",
                      chassis_code=option, option_code=option, engine=next((x for x in attributes if "engine" in x), "unknown"),
                      transmission=next((x for x in attributes if x in ("manual", "automatic", "手动", "自动")), "unknown"),
                      body_style=object_type, specification=" ".join(attributes) or "unknown")
    else:
        feature_terms = ("double-head", "double head", "single-head", "single head", "complete", "完整", "双灯头", "单灯头")
        boundary_terms = type_terms + DOMAIN_ONTOLOGY["ART"]["attributes"] + color_terms + feature_terms
        boundary = min([folded.find(_fold(x)) for x in boundary_terms if _fold(x) in folded] or [len(raw)])
        prefix = raw[:boundary].strip(" ,，:-")
        prefix = re.split(r"[,，]|\b(?:under|below|max(?:imum)?)\b|低于|不超过|最高", prefix, maxsplit=1, flags=re.I)[0].strip()
        prefix = re.sub(r"^(?:art|artist|艺术家)[：:]?\s*", "", prefix, flags=re.I)
        prefix_words = re.findall(r"[A-Za-zÀ-ž][A-Za-zÀ-ž'.-]*", prefix)
        first_punctuation = min([raw.find(x) for x in (",", "，") if x in raw] or [len(raw)])
        if len(prefix_words) >= 3:
            artist_words, inferred_words = prefix_words[:2], prefix_words[2:]
        elif boundary < first_punctuation:
            artist_words, inferred_words = prefix_words, []
        else:
            artist_words, inferred_words = prefix_words[:1], prefix_words[1:]
        artist = " ".join(artist_words) if artist_words else (prefix or "unknown")
        title_m = re.search(r"(?:titled?|series|作品|系列)[：:'\" ]+([^,，]+)", raw, re.I)
        later_title = next((x for x in re.findall(r"\b[A-Z][A-Za-z0-9'-]+\b", raw[boundary:])
                            if x.lower() not in {"usd", "eur", "gbp"}), "")
        inferred_work = " ".join(inferred_words) or later_title or object_type
        feature = next((term for term in feature_terms if term in low or term in raw), " ".join(attributes) or "unknown")
        target.update(artist=artist, maker=artist, work=title_m.group(1).strip(" '\"") if title_m else inferred_work,
                      series=title_m.group(1).strip(" '\"") if title_m and "series" in title_m.group(0).lower() else "unknown",
                      title=title_m.group(1).strip(" '\"") if title_m else "unknown", medium=object_type,
                      material=" ".join(attributes) or "unknown", edition=next((x for x in attributes if x in ("edition", "限量")), "unknown"),
                      dimensions="unknown", color=color, feature=feature)
    recognized_values = [value for key, value in target.items()
                         if key not in {"original_input", "unclassified_terms", "confidence", "category"}]
    known_tokens = set(_fold(x) for x in re.findall(r"[A-Za-zÀ-ž\u4e00-\u9fff][A-Za-zÀ-ž\u4e00-\u9fff0-9-]+",
                                                    " ".join(str(v) for v in recognized_values)))
    target["unclassified_terms"] = [x for x in re.findall(r"[A-Za-zÀ-ž\u4e00-\u9fff][A-Za-zÀ-ž\u4e00-\u9fff0-9-]+", raw)
                                      if _fold(x) not in known_tokens and not re.fullmatch(r"(?:under|below|max|by|for|model|the|a|an)", x, re.I)][:20]
    return target


def parse_radar_text(text: str) -> dict:
    """Parse a natural-language target into a persisted domain schema."""
    raw = (text or "").strip(); low = raw.lower(); target = parse_natural_target(raw)
    currency = "EUR"
    if "£" in raw or "gbp" in low:
        currency = "GBP"
    elif "$" in raw or "usd" in low:
        currency = "USD"
    amount = None
    m = re.search(r"(?:低于|不超过|最高|under|below|max(?:imum)?|≤)\s*[€£$]?\s*([\d,.]+)\s*([kK万]?)", raw, re.I)
    if m:
        amount = float(m.group(1).replace(",", ""))
        if m.group(2).lower() == "k": amount *= 1000
        if m.group(2) == "万": amount *= 10000
    years = [int(x) for x in re.findall(r"\b(?:19|20)\d{2}\b", raw)]
    year_min = min(years) if years else None
    year_max = max(years) if years else None
    if target["category"] == "FURNITURE":
        maker, series, category = target.get("designer", "unknown"), target.get("model", "unknown"), "FURNITURE"
        include = [x for x in (maker, series, target.get("manufacturer"), target.get("object_type")) if x and x != "unknown"]
        name = " ".join(include[:4]) or raw[:60]
    elif target["category"] == "CLASSIC_CAR":
        maker, series, category = target.get("marque", "unknown"), target.get("model", "unknown"), "CLASSIC_CAR"
        include = [x for x in (maker, series, target.get("variant"), target.get("chassis_code")) if x and x != "unknown"]
        name = " ".join(include[:3]) or raw[:60]
    else:
        maker, series, category = target.get("artist", "unknown"), target.get("work", "unknown"), "ART"
        include = [x for x in (maker, series, target.get("medium"), target.get("material")) if x and x != "unknown"]
        name = " ".join(include[:4]) or raw[:60] or "Untitled Radar"
    prefer = [x for x in (target.get("color"), target.get("feature"), target.get("material"), target.get("specification"))
              if x and x != "unknown"]
    exclude = ["复制品", "海报", "书籍", "replica", "poster", "book", "print"]
    return {
        "name": name, "category": category, "maker_artist": maker,
        "model_series": series, "include_keywords": include,
        "prefer_keywords": list(dict.fromkeys(prefer)), "exclude_keywords": exclude,
        "year_min": year_min, "year_max": year_max, "price_min": None,
        "price_max": amount, "currency": currency, "target": target,
        "aliases": target.get("aliases") or [], "unclassified_terms": target.get("unclassified_terms") or [],
    }


def create_radar(query_original: str, rules: Optional[dict] = None, **options) -> dict:
    init_radar_tables()
    parsed = dict(rules or parse_radar_text(query_original))
    name = options.get("name") or parsed.get("name") or query_original[:60]
    conn = _conn()
    try:
        cur = conn.execute("""INSERT INTO radars
          (name,query_original,rules_json,sources_json,languages_json,schedule,enabled,
           notify_unknown_price,created_at,next_run_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
          (name, query_original, json.dumps(parsed, ensure_ascii=False),
           json.dumps(options.get("sources") or [], ensure_ascii=False),
           json.dumps(options.get("languages") or ["en", "fr"], ensure_ascii=False),
           options.get("schedule", "TWICE_DAILY"), 1, int(options.get("notify_unknown_price", True)),
           now_iso(), options.get("next_run_at")))
        conn.commit(); rid = cur.lastrowid
    finally:
        conn.close()
    return get_radar(rid)


def _decode_radar(row: dict) -> dict:
    row = dict(row)
    row["rules"] = json.loads(row.pop("rules_json") or "{}")
    row["sources"] = json.loads(row.pop("sources_json") or "[]")
    row["languages"] = json.loads(row.pop("languages_json") or "[]")
    row["enabled"] = bool(row["enabled"])
    row["notify_unknown_price"] = bool(row["notify_unknown_price"])
    return row


def get_radar(radar_id: int) -> Optional[dict]:
    rows = _rows("SELECT * FROM radars WHERE id=?", (radar_id,))
    return _decode_radar(rows[0]) if rows else None


def list_radars() -> list[dict]:
    init_radar_tables()
    return [_decode_radar(x) for x in _rows("SELECT * FROM radars WHERE deleted_at IS NULL ORDER BY enabled DESC,id DESC")]


def update_radar(radar_id: int, fields: dict) -> Optional[dict]:
    allowed = {"name", "schedule", "enabled", "notify_unknown_price", "query_original"}
    parts, vals = [], []
    for key in allowed:
        if key in fields:
            parts.append(f"{key}=?"); vals.append(int(fields[key]) if key in {"enabled", "notify_unknown_price"} else fields[key])
    if parts:
        conn = _conn()
        try:
            conn.execute(f"UPDATE radars SET {','.join(parts)} WHERE id=?", vals + [radar_id]); conn.commit()
        finally: conn.close()
    return get_radar(radar_id)


def delete_radar(radar_id: int) -> bool:
    """Soft-delete the Radar while leaving runs, discoveries and feedback intact."""
    init_radar_tables(); conn = _conn()
    try:
        cur = conn.execute("UPDATE radars SET deleted_at=?,enabled=0 WHERE id=? AND deleted_at IS NULL",
                           (now_iso(), radar_id)); conn.commit()
        return cur.rowcount == 1
    finally: conn.close()


def _similarity_key(rules: dict) -> tuple:
    keywords = tuple(sorted({re.sub(r"[^a-z0-9]+", "", str(x).lower()) for x in
                             (rules.get("include_keywords") or []) + (rules.get("aliases") or []) if x}))
    return (rules.get("category"), str(rules.get("maker_artist") or "").lower(),
            str(rules.get("model_series") or "").lower(), keywords, rules.get("year_min"),
            rules.get("year_max"), rules.get("price_min"), rules.get("price_max"), rules.get("currency"))


def find_similar_radar(rules: dict) -> Optional[dict]:
    target = _similarity_key(rules)
    for radar in list_radars():
        candidate = _similarity_key(radar["rules"])
        exact = sum(a == b for a, b in zip(target, candidate))
        if exact >= 7 and target[0:3] == candidate[0:3]:
            return radar
    return None


def source_registry(category: str = "", health_status: str = "", enabled: Optional[bool] = None) -> list[dict]:
    init_radar_tables()
    where, args = [], []
    if category:
        where.append("category IN (?, 'BOTH')"); args.append(category.upper())
    if health_status:
        where.append("health_status=?"); args.append(health_status.upper())
    if enabled is not None:
        where.append("enabled=?"); args.append(int(enabled))
    sql = "SELECT * FROM source_registry" + (" WHERE " + " AND ".join(where) if where else "")
    rows = _rows(sql + " ORDER BY priority,name", tuple(args))
    for row in rows:
        total = row["success_count"] + row["failure_count"]
        row["success_rate_30d"] = round(100 * row["success_count"] / total) if total else None
        row["success_rate"] = row["success_rate_30d"]
        row["languages"] = json.loads(row.get("languages_json") or "[]")
        for key in ("requires_login", "robots_checked", "tos_checked", "js_rendered", "compliance_reviewed",
                    "searchable_by_external_engine", "enabled"):
            row[key] = bool(row.get(key))
    return rows


def dedupe_queries(queries: Iterable[str]) -> list[str]:
    """Remove semantic no-op duplicates without imposing a quantity ceiling."""
    out, seen = [], set()
    for query in queries:
        clean = re.sub(r"\s+", " ", str(query or "").strip())
        key = re.sub(r"[^a-z0-9:]+", " ", clean.lower())
        key = re.sub(r"\s+", " ", key).strip()
        if clean and key not in seen: seen.add(key); out.append(clean)
    return out


def expand_queries(radar: dict) -> list[str]:
    """Build a schema-driven plan without entity-specific templates or a quantity cap."""
    rules = radar.get("rules") or {}
    maker = rules.get("maker_artist") or rules.get("make") or radar.get("name") or ""
    model = rules.get("model_series") or ""
    target = rules.get("target") or {}
    category = str(rules.get("category") or target.get("category") or "ART").upper()
    fields_by_category = {
        "ART": ("artist", "maker", "work", "series", "title", "medium", "material", "color", "feature", "edition"),
        "FURNITURE": ("designer", "manufacturer", "brand", "model", "series", "object_type", "material", "color", "edition"),
        "CLASSIC_CAR": ("marque", "model", "variant", "generation", "chassis_code", "engine", "transmission", "body_style", "specification", "option_code"),
    }
    values = [str(target.get(field)) for field in fields_by_category.get(category, ())
              if target.get(field) and target.get(field) != "unknown"]
    values.extend(str(x) for x in target.get("unclassified_terms") or [] if x)
    base = " ".join(dict.fromkeys(values)) or " ".join(x for x in (str(maker), str(model)) if x).strip()
    if not base: base = str(radar.get("query_original") or radar.get("name") or "").strip()
    if not base: return []
    aliases = dedupe_queries([base, _fold(base)] + list(target.get("aliases") or []) + list(rules.get("aliases") or []))
    names = dedupe_queries(aliases + [" ".join(values[:n]) for n in range(2, min(len(values), 6) + 1)])
    ontology = DOMAIN_ONTOLOGY.get(category, DOMAIN_ONTOLOGY["ART"])
    features = dedupe_queries(list(rules.get("prefer_keywords") or []) + list(ontology["types"]) + list(ontology["attributes"]))
    priority_domains = (FURNITURE_P0_DOMAINS if category == "FURNITURE" else
                        ("classicdriver.com", "classic.com", "rmsothebys.com", "bringatrailer.com")
                        if category == "CLASSIC_CAR" else P0_ART_DOMAINS)
    current = ["for sale", "available", "buy", "private sale", "inquire", "gallery", "dealer"]
    auction = ["auction", "lot", "upcoming auction", "estimate", "current auction"]
    history = ["sold", "auction result", "realized price", "past lot", "previous sale", "archive"]
    languages = ["vente", "enchères", "à vendre", "Auktion", "zu verkaufen", "asta", "vendita", "subasta"]
    years = []
    if rules.get("year_min") and rules.get("year_max"):
        years = [str(y) for y in range(int(rules["year_min"]), int(rules["year_max"]) + 1, 5)]
    queries = []
    # P0 searches are deliberately first, while general-web coverage remains.
    for domain in priority_domains:
        for name in names: queries.append(f"site:{domain} {name}")
    for name in names:
        queries.append(name)
        queries.extend(f"{name} {term}" for term in current + auction + history + languages + features + years)
    for name in names:
        queries.extend(f"{name} {term}" for term in ("marketplace", "collection", "catalogue", "art fair", "price", "work available"))
    return dedupe_queries(queries)


def query_plan_summary(radar: dict) -> dict:
    queries = expand_queries(radar)
    category = str(radar.get("rules", {}).get("category") or "ART").upper()
    priority_domains = FURNITURE_P0_DOMAINS if category == "FURNITURE" else P0_ART_DOMAINS
    p0 = [q for q in queries if any(f"site:{d}" in q.lower() for d in priority_domains)]
    multilingual_terms = ("vente", "enchères", "à vendre", "auktion", "zu verkaufen", "asta", "vendita", "subasta")
    historical_terms = (" sold", "auction result", "realized price", "past lot", "previous sale", " archive")
    multilingual = [q for q in queries if any(x in q.lower() for x in multilingual_terms)]
    historical = [q for q in queries if any(x in q.lower() for x in historical_terms)]
    return {"total_queries_planned":len(queries), "p0_source_queries":len(p0),
            "general_web_queries":len([q for q in queries if "site:" not in q.lower()]),
            "multilingual_queries":len(multilingual), "historical_price_queries":len(historical),
            "queries":queries}


def select_sources_for_run(radar: dict) -> list[dict]:
    all_sources = [s for s in source_registry(category=radar["rules"].get("category") or "ART", enabled=True)
                   if s.get("compliance_reviewed") and s.get("compliance_mode") not in {"LOGIN_REQUIRED", "DO_NOT_AUTOMATE"}]
    wanted = set(radar.get("sources") or [])
    if wanted:
        return [s for s in all_sources if s["domain"] in wanted or s["name"] in wanted]
    run_count = len(list_runs(radar["id"]))
    bucket = run_count % 4
    # Registry priority is a rank (1 = core, 2 = long tail), not a claim that
    # every registered site is contacted. Keep the core compact and rotate the rest.
    priority = [s for s in all_sources if int(s.get("priority") if s.get("priority") is not None else 99) <= 1][:12]
    rotation = [s for s in all_sources if int(s.get("priority") or 99) > 1 and int(s.get("rotation_bucket") or 0) == bucket]
    selected = priority + rotation[:18]
    return selected[:30]


class SourceAdapter:
    def __init__(self, provider): self.provider = provider
    def search(self, source: dict, queries: list[str]) -> list[dict]: raise NotImplementedError


class GenericSearchAdapter(SourceAdapter):
    def search(self, source: dict, queries: list[str], on_results=None, on_expand=None, on_queries_added=None,
               on_query_start=None) -> list[dict]:
        if not source.get("searchable_by_external_engine", True): return []
        rows = []
        dev_mode = getattr(self.provider, "name", "") == "searxng_local"
        batch_size, offset, planned = 30, 0, list(queries)
        while offset < len(planned):
            current = planned[offset:offset + batch_size]
            for position, query in enumerate(current, start=offset + 1):
                effective = query if dev_mode or "site:" in query.lower() or source.get("domain") == "full-web.local" else f'{query} site:{source["domain"]}'
                if on_query_start: on_query_start(effective, position)
                batch = self.provider.search(effective, max_results=5) or []
                rows.extend(batch)
                if on_results: on_results(batch, effective, position)
                if on_expand:
                    additions = dedupe_queries(on_expand(batch, effective) or [])
                    known = {_fold(x) for x in planned}
                    fresh = [x for x in additions if _fold(x) not in known]
                    if fresh:
                        planned.extend(fresh)
                        if on_queries_added: on_queries_added(fresh)
            offset += len(current)
        return rows


class SearchMetricsProvider:
    """Run-local counter around an official provider; never persists raw responses."""
    def __init__(self, provider):
        self.provider = provider; self.completed = 0; self.failed = 0; self.results_received = 0
    def search(self, query: str, max_results=5):
        try:
            rows = self.provider.search(query, max_results=max_results) or []
            self.completed += 1; self.results_received += len(rows)
            return rows
        except Exception:
            self.failed += 1
            raise
    def __getattr__(self, name): return getattr(self.provider, name)


class GenericPublicHTMLAdapter(SourceAdapter):
    def search(self, source: dict, queries: list[str]) -> list[dict]:
        if not (source.get("robots_checked") and source.get("tos_checked")):
            raise PermissionError("direct access not approved: robots.txt and terms must be checked")
        # A source-specific public adapter can be introduced only after review.
        return GenericSearchAdapter(self.provider).search(source, queries)


def adapter_for(source: dict, provider) -> SourceAdapter:
    if source.get("compliance_reviewed") is not True:
        raise PermissionError("source compliance review is required")
    mode = source.get("compliance_mode")
    if mode in {"LOGIN_REQUIRED", "DO_NOT_AUTOMATE"}:
        raise PermissionError("source requires user login or manual access")
    if mode in {"SEARCH_ONLY", "OFFICIAL_ALERT"}:
        return GenericSearchAdapter(provider)
    if mode == "DIRECT_OK":
        return GenericPublicHTMLAdapter(provider)
    raise PermissionError("unsupported compliance mode")


def probe_source(source_id: int, client=None) -> dict:
    sources = [s for s in source_registry() if s["id"] == source_id]
    if not sources: raise ValueError("Source not found")
    source = sources[0]
    if source["requires_login"] or source["access_mode"] == "USER_LOGIN_REQUIRED":
        status, reason, elapsed = "LOGIN_REQUIRED", "用户登录后方可访问", None
    elif source["access_mode"] == "MANUAL_ONLY":
        status, reason, elapsed = "PAUSED", "仅登记，未启用自动访问", None
    else:
        started = time.perf_counter()
        try:
            if client is None:
                from urllib.request import Request, urlopen
                req = Request(source["official_url"], method="HEAD", headers={"User-Agent":"AmazingKimiRadar/0.8 source-health-check"})
                with urlopen(req, timeout=8) as response: code = response.status
            else:
                response = client.head(source["official_url"], timeout=8); code = response.status_code
            elapsed = round((time.perf_counter() - started) * 1000)
            status = "SEARCH_ONLY" if source["access_mode"] == "SEARCH_ONLY" else "HEALTHY"
            reason = "" if code < 400 else f"HTTP {code}"
            if code >= 400: status = "BLOCKED" if code in (401, 403, 429) else "BROKEN"
        except Exception as exc:
            elapsed, status, reason = round((time.perf_counter() - started) * 1000), "BROKEN", str(exc)[:160]
    conn = _conn()
    try:
        conn.execute("UPDATE source_registry SET last_check=?,avg_response_ms=?,health_status=?,failure_reason=? WHERE id=?",
                     (now_iso(), elapsed, status, reason, source_id)); conn.commit()
    finally: conn.close()
    return [s for s in source_registry() if s["id"] == source_id][0]


def canonical_url(url: str) -> str:
    try:
        p = urlparse(url)
        return urlunparse((p.scheme.lower(), p.netloc.lower().replace("www.", ""), p.path.rstrip("/"), "", "", ""))
    except Exception:
        return url or ""


def fingerprint(record: dict) -> str:
    # Prefer object identity over source identity so mirrors can deduplicate.
    title = str(record.get("title") or "")
    # Price changes must remain the same object, even when a search title embeds
    # the current amount.
    title = _MONEY.sub(" ", title)
    text = "|".join((str(record.get("maker") or ""), title,
                     str(record.get("year") or ""), str(record.get("seller") or "")))
    norm = re.sub(r"[^a-z0-9]+", "", text.lower())
    if len(norm) < 12:
        norm = canonical_url(record.get("source_url", ""))
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


_MONEY = re.compile(r"(?:(€|£|\$|EUR|GBP|USD)\s?([\d][\d,. ]{2,14})|([\d][\d,. ]{2,14})\s?(EUR|GBP|USD))", re.I)


def normalize_result(raw: dict, radar: dict) -> dict:
    text = f"{raw.get('title','')} {raw.get('snippet','')}"
    price, currency = None, radar["rules"].get("currency") or "EUR"
    m = _MONEY.search(text)
    if m:
        token, number = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
        currency = {"€":"EUR", "£":"GBP", "$":"USD"}.get((token or "").upper(), (token or currency).upper())
        try:
            number=number.strip().replace(' ','')
            if re.search(r'[.,]\d{2}$',number):
                price=float(re.sub(r'[^\d]','',number[:-3])+'.'+number[-2:])
            else: price=float(re.sub(r'[^\d]','',number))
        except ValueError: price = None
    year_m = re.search(r"\b(?:19|20)\d{2}\b", text)
    url = raw.get("url") or raw.get("source_url") or ""
    original_text = str(raw.get("original_text") or raw.get("title") or "UNKNOWN")
    original_language = str(raw.get("original_language") or raw.get("language") or "und")
    product_raw = {key: raw.get(key) for key in ("title", "snippet", "url", "provider", "query", "retrieved_at") if raw.get(key) is not None}
    domain = urlparse(url).netloc.lower().replace("www.", "")
    priority = "P0" if any(domain == x or domain.endswith("." + x) for x in P0_ART_DOMAINS) else "STANDARD"
    return {
        "category": radar["rules"].get("category") or "ART",
        "title": raw.get("title") or "UNKNOWN", "maker": radar["rules"].get("maker_artist") or "UNKNOWN",
        "object_name": radar["rules"].get("model_series") or "UNKNOWN",
        "year": year_m.group(0) if year_m else None, "asking_price": price, "currency": currency,
        "source_name": (urlparse(url).netloc.replace("www.", "") if raw.get("source_name") == "SearXNG Local Search"
                        else raw.get("source_name")) or urlparse(url).netloc.replace("www.", "") or "UNKNOWN",
        "source_url": url, "image_url": raw.get("image_url") or raw.get("thumbnail") or raw.get("img_src") or "", "seller": raw.get("seller") or "",
        "original_text": original_text, "original_language": original_language,
        "translated_text": raw.get("translated_text"), "raw": product_raw,
        "evidence_level": "SEARCH_RESULT",
        "source_priority": priority,
        "search_query": raw.get("query") or "",
        "source": domain or "UNKNOWN",
        "discovery_status": "CANDIDATE",
    }


POSITIVE_AVAILABILITY = {"FOR_SALE", "INQUIRE", "UPCOMING_AUCTION", "LIVE_AUCTION"}


def classify_availability(record: dict) -> str:
    text = f"{record.get('title','')} {record.get('raw',{}).get('snippet','')} {record.get('source_url','')}".lower()
    path = urlparse(record.get("source_url") or "").path.lower().rstrip("/")
    current_year = datetime.now(timezone.utc).year
    if any(x in text for x in ("museum", "collection object", "moma.org", "tate.org.uk", "metmuseum.org")): return "MUSEUM"
    if any(x in text for x in ("wikipedia", "biography", "biografie", "exhibition", "news", "article", "interview")): return "ARTICLE"
    if re.search(r'\b(sold|unavailable|removed)\b',text):
        return 'SOLD' if re.search(r'\bsold\b',text) else 'REMOVED'
    if any(x in text for x in ("sold lot", "lot sold", "sold for", "auction result", "price realised", "price realized")): return "SOLD"
    if any(x in text for x in ("past auction", "auction archive", "previously sold", "lot archive", "results/")): return "PAST_AUCTION"
    auction_years = [int(y) for y in re.findall(r"/(20\d{2})(?:/|$)", path)]
    if "/auction" in path and auction_years and max(auction_years) < current_year: return "PAST_AUCTION"
    if any(x in path for x in ("/auctioneer/", "/kuenstlerverzeichnis/", "/ovr/")): return "REFERENCE"
    if re.search(r"/artists?/", path) and not any(x in path for x in ("/works/", "/artworks/", "/lots/")): return "REFERENCE"
    if any(x in text for x in ("live auction", "bid now")): return "LIVE_AUCTION"
    if any(x in text for x in ("upcoming auction", "current lot", "auction date")): return "UPCOMING_AUCTION"
    if any(x in text for x in ("price on request", "price upon request", "inquire", "enquire", "contact gallery", "contact dealer")): return "INQUIRE"
    if any(x in text for x in ("for sale", "buy now", "available", "in stock", "listing")): return "FOR_SALE"
    if any(x in text for x in ("catalogue", "catalog", "reference", "artist page", "artwork details")): return "REFERENCE"
    # A stated asking price on a gallery/dealer result is itself current-sale
    # evidence; historical result language above always takes precedence.
    if record.get("asking_price") is not None: return "FOR_SALE"
    return "UNKNOWN"


def freshness_score(record: dict) -> int:
    text = f"{record.get('title','')} {record.get('raw',{}).get('snippet','')}"
    years = [int(x) for x in re.findall(r"\b20\d{2}\b", text)]
    current = datetime.now(timezone.utc).year
    if any(y >= current for y in years): return 15
    if any(y == current - 1 for y in years): return 12
    if years and max(years) < current - 2: return 2
    return 8


def score_actionability(record: dict, match: dict, availability: str) -> dict:
    fresh = freshness_score(record)
    negative = availability in {"SOLD", "PAST_AUCTION", "MUSEUM", "ARTICLE", "REFERENCE"}
    availability_points = {"FOR_SALE":45, "INQUIRE":42, "UPCOMING_AUCTION":43, "LIVE_AUCTION":45}.get(availability, 0)
    match_points = min(25, 10 + 5 * len([x for x in match.get("reasons", []) if "未知" not in x])) if match.get("matched") else 0
    price_points = 10 if record.get("asking_price") is not None else 7 if availability == "INQUIRE" else 3
    source_points = 7 if record.get("source_url") and record.get("source_name") else 2
    priority_points = 7 if record.get("source_priority") == "P0" else 0
    score = min(100, availability_points + match_points + fresh + price_points + source_points + priority_points)
    if negative: score = min(score, 20 if availability in {"SOLD", "PAST_AUCTION"} else 8)
    eligible = bool(match.get("matched") and availability in POSITIVE_AVAILABILITY and score >= 60)
    return {"actionability_score": score, "freshness_score": fresh, "discovery_eligible": eligible,
            "historical_context": availability in {"SOLD", "PAST_AUCTION"},
            "discard_reason": "" if eligible else availability.lower() if availability != "UNKNOWN" else "not_actionable"}


def same_object_cluster_id(record: dict) -> str:
    title = _MONEY.sub(" ", str(record.get("title") or ""))
    title = re.sub(r"\b(?:EUR|GBP|USD)\s*[\d][\d,. ]{2,14}", " ", title, flags=re.I)
    title = re.sub(r"\b(?:EUR|GBP|USD)\b", " ", title, flags=re.I)
    title = re.sub(r"\b(for sale|available|auction|inquire|listing|sold|lot\s*\d+)\b", " ", title, flags=re.I)
    identity = "|".join((str(record.get("maker") or ""), title, str(record.get("year") or ""), str(record.get("object_name") or "")))
    return hashlib.sha256(re.sub(r"[^a-z0-9]+", "", identity.lower()).encode()).hexdigest()[:24]


def match_rules(record: dict, rules: dict, notify_unknown_price: bool = True) -> dict:
    text = _fold(f"{record.get('title','')} {record.get('raw',{}).get('snippet','')}")
    reasons, preferences, unknown = [], [], []
    for kw in rules.get("exclude_keywords") or []:
        if _fold(kw) in text:
            return {"matched": False, "level": "NO_MATCH", "reasons": [f"排除词：{kw}"], "unknown": []}
    canonical_text = re.sub(r"[^a-z0-9]+", "", text)
    for kw in rules.get("include_keywords") or []:
        folded_kw = _fold(kw)
        if folded_kw in {'作品','艺术品','artwork','furniture','家具','经典车','classic car'}: continue
        # Ordinary English plurals and punctuation variants, independent of entity.
        tokens = re.findall(r'\w+',folded_kw)
        observed = {token.removesuffix('s') for token in re.findall(r'\w+',text)}
        if tokens and all(token.removesuffix('s') in observed for token in tokens):
            reasons.append(f'符合 {kw}'); continue
        if folded_kw not in text and re.sub(r"[^a-z0-9]+", "", folded_kw) not in canonical_text:
            return {"matched": False, "level": "NO_MATCH", "reasons": [f"缺少必要条件：{kw}"], "unknown": []}
        reasons.append(f"符合 {kw}")
    year = int(record["year"]) if str(record.get("year") or "").isdigit() else None
    if rules.get("year_min") or rules.get("year_max"):
        if year is None: unknown.append("年份未知")
        elif rules.get("year_min") and year < rules["year_min"]: return {"matched": False, "level":"NO_MATCH", "reasons":["年份低于范围"], "unknown":[]}
        elif rules.get("year_max") and year > rules["year_max"]: return {"matched": False, "level":"NO_MATCH", "reasons":["年份高于范围"], "unknown":[]}
        else: reasons.append(f"年份 {year} 在范围内")
    if rules.get("price_max") is not None:
        if record.get("asking_price") is None: unknown.append("价格未知")
        elif record["asking_price"] > rules["price_max"]: return {"matched": False, "level":"NO_MATCH", "reasons":["超过最高价格"], "unknown":[]}
        else: reasons.append(f"价格不高于 {rules['currency']} {rules['price_max']:,.0f}")
    for kw in rules.get("prefer_keywords") or []:
        synonyms={'粉色':'pink','红色':'red','蓝色':'blue','黑色':'black','白色':'white','完整':'complete'}
        if kw.lower() in text or synonyms.get(kw,kw).lower() in text: preferences.append(kw)
    matched = not unknown or (notify_unknown_price and unknown == ["价格未知"]) or bool(reasons)
    level = "HIGH_MATCH" if matched and (preferences or len(reasons) >= 3) and not unknown else "PARTIAL" if unknown else "MATCH"
    return {"matched": matched, "level": level, "reasons": reasons + [f"偏好：{x}" for x in preferences] + unknown, "unknown": unknown}


def _upsert_discovery(radar: dict, rec: dict, match: dict) -> tuple[dict, bool, bool]:
    require_source_url(rec.get('source_url'))
    # One object can legitimately match more than one Radar. Deduplicate across
    # sources inside a Radar without stealing it from another Radar.
    quality_defaults = {"availability":"FOR_SALE", "actionability_score":60, "freshness_score":8,
                        "discovery_eligible":True, "historical_context":False, "discard_reason":""}
    for key, value in quality_defaults.items(): rec.setdefault(key, value)
    rec.setdefault("same_object_cluster_id", same_object_cluster_id(rec))
    fp = (f"candidate::{rec['candidate_id']}" if rec.get('candidate_id') else
          hashlib.sha256(f"{radar['id']}|{fingerprint(rec)}".encode("utf-8")).hexdigest())
    now = now_iso(); conn = _conn()
    try:
        source_row = conn.execute("SELECT source_type FROM source_registry WHERE domain=?",
                                  (urlparse(rec.get("source_url") or "").netloc.replace("www.", ""),)).fetchone()
        new_type = source_row[0] if source_row else rec.get("source_type") or "AGGREGATOR"
        old = conn.execute("""SELECT id,asking_price,currency,status,notified_at,source_name,source_url,
                            primary_source_type,also_seen_json FROM discoveries WHERE fingerprint=?""", (fp,)).fetchone()
        if old:
            did, old_price, old_currency, status, notified_at, old_name, old_url, old_type, also_json = old
            price_changed = bool(old_price and rec.get("asking_price") and old_price != rec["asking_price"])
            rank = {"AUCTION_HOUSE": 1, "DEALER": 2, "MARKETPLACE": 3, "CLASSIFIEDS": 4, "AGGREGATOR": 5}
            promote = rank.get(new_type, 9) < rank.get(old_type or "AGGREGATOR", 9)
            also_seen = json.loads(also_json or "[]")
            seen_item = {"source_name": rec["source_name"], "source_url": rec["source_url"], "source_type": new_type}
            if rec["source_url"] != old_url and not any(x.get("source_url") == rec["source_url"] for x in also_seen):
                also_seen.append(seen_item)
            if promote and old_url and not any(x.get("source_url") == old_url for x in also_seen):
                also_seen.append({"source_name": old_name, "source_url": old_url, "source_type": old_type or "AGGREGATOR"})
            source_name, source_url, primary_type = (rec["source_name"], rec["source_url"], new_type) if promote else (old_name, old_url, old_type)
            conn.execute("""UPDATE discoveries SET last_seen=?,source_url=?,source_name=?,asking_price=?,currency=?,
              match_level=?,match_reasons_json=?,raw_json=?,primary_source_type=?,also_seen_json=?,
              original_text=?,original_language=?,translated_text=?,evidence_level=?,image_url=?,availability=?,
              actionability_score=?,freshness_score=?,discovery_eligible=?,historical_context=?,discard_reason=?,
              same_object_cluster_id=?,source_priority=? WHERE id=?""",
              (now, source_url, source_name, rec.get("asking_price"), rec["currency"],
               match["level"], json.dumps(match["reasons"], ensure_ascii=False), json.dumps(rec["raw"], ensure_ascii=False),
               primary_type, json.dumps(also_seen, ensure_ascii=False), rec.get("original_text") or rec["title"],
               rec.get("original_language") or "und", rec.get("translated_text"), rec.get("evidence_level") or "SEARCH_RESULT",
               rec.get("image_url") or "", rec["availability"], rec["actionability_score"], rec["freshness_score"],
               int(rec["discovery_eligible"]), int(rec["historical_context"]), rec["discard_reason"], rec["same_object_cluster_id"],
               rec.get("source_priority") or "STANDARD", did))
            if price_changed:
                conn.execute("INSERT INTO discovery_events(discovery_id,event_type,old_value,new_value,created_at) VALUES(?,?,?,?,?)",
                             (did, "PRICE_CHANGE", f"{old_price:g} {old_currency}", f"{rec['asking_price']:g} {rec['currency']}", now))
            conn.commit(); is_new = False
        else:
            cur = conn.execute("""INSERT INTO discoveries
              (fingerprint,radar_id,title,maker,object_name,year,asking_price,currency,source_name,source_url,
               image_url,first_seen,last_seen,match_level,match_reasons_json,raw_json,primary_source_type,also_seen_json,
               original_text,original_language,translated_text,evidence_level)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (fp, radar["id"], rec["title"], rec["maker"], rec["object_name"], rec.get("year"),
               rec.get("asking_price"), rec["currency"], rec["source_name"], rec["source_url"], rec["image_url"],
               now, now, match["level"], json.dumps(match["reasons"], ensure_ascii=False), json.dumps(rec["raw"], ensure_ascii=False),
               new_type, "[]", rec.get("original_text") or rec["title"], rec.get("original_language") or "und",
               rec.get("translated_text"), rec.get("evidence_level") or "SEARCH_RESULT"))
            did = cur.lastrowid
            conn.execute("""UPDATE discoveries SET availability=?,actionability_score=?,freshness_score=?,
              discovery_eligible=?,historical_context=?,discard_reason=?,same_object_cluster_id=?,source_priority=? WHERE id=?""",
              (rec["availability"], rec["actionability_score"], rec["freshness_score"], int(rec["discovery_eligible"]),
               int(rec["historical_context"]), rec["discard_reason"], rec["same_object_cluster_id"],
               rec.get("source_priority") or "STANDARD", did))
            did = cur.lastrowid
            conn.execute("INSERT INTO discovery_events(discovery_id,event_type,new_value,created_at) VALUES(?,?,?,?)",
                         (did, "NEW_DISCOVERY", rec["title"], now)); conn.commit(); is_new, price_changed = True, False
        conn.execute('UPDATE discoveries SET candidate_id=?,title=? WHERE id=?', (rec.get('candidate_id'),rec['title'],did))
        conn.execute("""UPDATE discoveries SET discovered_at=COALESCE(discovered_at,?),run_id=COALESCE(run_id,?),
                     query_id=CASE WHEN query_id='' THEN ? ELSE query_id END,
                     stream_sequence=CASE WHEN stream_sequence=0 THEN ? ELSE stream_sequence END WHERE id=?""",
                     (now, rec.get("run_id"), str(rec.get("query_id") or ""), int(rec.get("stream_sequence") or 0), did))
        conn.commit()
    finally: conn.close()
    return get_discovery(did), is_new, price_changed


def get_discovery(discovery_id: int) -> Optional[dict]:
    rows = _rows("SELECT * FROM discoveries WHERE id=?", (discovery_id,))
    if not rows: return None
    row = rows[0]; row["match_reasons"] = json.loads(row.pop("match_reasons_json") or "[]")
    row["also_seen"] = json.loads(row.pop("also_seen_json") or "[]")
    row["possible_duplicate"] = bool(row["possible_duplicate"])
    if row.get('candidate_id'): row.update(changes.details(row['candidate_id']))
    return row


def list_discoveries(status: str = "", eligible_only: bool = True) -> list[dict]:
    init_radar_tables()
    clauses, values = [], []
    if status: clauses.append("status=?"); values.append(status)
    if eligible_only: clauses.append("discovery_eligible=1")
    sql = "SELECT * FROM discoveries" + (" WHERE " + " AND ".join(clauses) if clauses else "")
    rows = _rows(sql + " ORDER BY CASE source_priority WHEN 'P0' THEN 0 ELSE 1 END,actionability_score DESC,freshness_score DESC,first_seen DESC", tuple(values))
    out = []
    for row in rows:
        if row.get('candidate_id'): row.update(changes.details(row['candidate_id']))
        row["match_reasons"] = json.loads(row.pop("match_reasons_json") or "[]")
        row["also_seen"] = json.loads(row.pop("also_seen_json") or "[]"); out.append(row)
    return out


def set_feedback(discovery_id: int, feedback: str) -> Optional[dict]:
    mapping = {"useful": (1, "SAVED"), "irrelevant": (0, "IGNORED"), "bought": (1, "BOUGHT"),
               "viewed": (None, "VIEWED"), "contacted": (None, "CONTACTED")}
    if feedback not in mapping: raise ValueError("invalid feedback")
    useful, status = mapping[feedback]; conn = _conn()
    try:
        conn.execute("UPDATE discoveries SET useful=?,status=? WHERE id=?", (useful, status, discovery_id)); conn.commit()
    finally: conn.close()
    return get_discovery(discovery_id)


def _email_new_discovery(radar: dict, d: dict, coverage: Optional[dict] = None) -> bool:
    host, user, password = os.getenv("KIMI_SMTP_HOST"), os.getenv("KIMI_SMTP_USER"), os.getenv("KIMI_SMTP_PASSWORD")
    recipient = os.getenv("KIMI_NOTIFICATION_EMAIL") or user
    if not (host and user and password and recipient): return False
    msg = EmailMessage(); msg["Subject"] = f"SIGNAL · 发现新的 {radar['name']}"
    msg["From"] = os.getenv("KIMI_EMAIL_FROM") or user; msg["To"] = recipient
    price = "价格未知" if d.get("asking_price") is None else f"{d['currency']} {d['asking_price']:,.0f}"
    reasons = "\n".join(f"• {x}" for x in d.get("match_reasons") or [])
    coverage = coverage or {}
    coverage_text = (f"\n\n本次搜索\n{coverage.get('queries_completed', 0)} / {coverage.get('queries_planned', 0)} 组查询完成"
                     f"\n{coverage.get('results_processed', 0)} 条公开搜索结果已处理"
                     f"\n目标市场范围 {coverage.get('target_scope', 0)} 个已登记来源（不代表逐站访问）")
    msg.set_content(f"{d['maker']}\n{d['title']}\n\n{price}\n\n来源\n{d['source_name']}\n\n为什么提醒你\n{reasons}\n\n首次发现\n{d['first_seen']}\n\n查看原始页面 → {d['source_url']}\n\n它符合你自己设定的条件。{coverage_text}")
    with smtplib.SMTP(host, int(os.getenv("KIMI_SMTP_PORT") or 587), timeout=15) as smtp:
        smtp.starttls(); smtp.login(user, password); smtp.send_message(msg)
    return True


def run_radar(radar_id: int, provider=None, sources: Optional[Iterable[dict]] = None, send_email=True) -> dict:
    radar = get_radar(radar_id)
    if not radar: raise ValueError("Radar not found")
    provider = provider or build_search_provider()
    provider_status = getattr(provider, "status", "OK")
    # Provider Gate precedes source selection, query generation and run insertion.
    # A missing provider is configuration state, never a failed 0/N Search Run.
    if provider_status != "OK":
        raise RuntimeError(provider_status)
    metrics_provider = SearchMetricsProvider(provider)
    registry = source_registry(); registered = len(registry)
    eligible_rows = [s for s in registry if s.get("compliance_reviewed") is True and
                     s.get("compliance_mode") in {"DIRECT_OK", "SEARCH_ONLY", "OFFICIAL_ALERT"} and
                     s.get("health_status") not in {"BROKEN", "PAUSED"}]
    eligible = len(eligible_rows)
    candidates = list(sources) if sources is not None else [{"name":"Full Web Search", "domain":"full-web.local",
                  "compliance_reviewed":True, "compliance_mode":"SEARCH_ONLY", "health_status":"SEARCH_ONLY",
                  "searchable_by_external_engine":True}]
    skip = {
        "unreviewed": sum(1 for s in registry if s.get("compliance_reviewed") is not True),
        "login_required": sum(1 for s in registry if s.get("compliance_mode") == "LOGIN_REQUIRED"),
        "do_not_automate": sum(1 for s in registry if s.get("compliance_mode") == "DO_NOT_AUTOMATE"),
        "review_expired": sum(1 for s in registry if s.get("compliance_mode") == "REVIEW_EXPIRED"),
        "paused": sum(1 for s in registry if s.get("health_status") == "PAUSED" and s.get("compliance_mode") not in {"LOGIN_REQUIRED", "DO_NOT_AUTOMATE"}),
    }
    registry_domains = {s.get("domain") for s in registry}
    selected = []
    for source in candidates:
        mode, health = source.get("compliance_mode"), source.get("health_status")
        extra = source.get("domain") not in registry_domains
        if source.get("compliance_reviewed") is not True:
            if extra: skip["unreviewed"] += 1
        elif mode == "LOGIN_REQUIRED":
            if extra: skip["login_required"] += 1
        elif mode == "DO_NOT_AUTOMATE":
            if extra: skip["do_not_automate"] += 1
        elif mode == "REVIEW_EXPIRED":
            if extra: skip["review_expired"] += 1
        elif health == "PAUSED":
            if extra: skip["paused"] += 1
        elif mode in {"DIRECT_OK", "SEARCH_ONLY", "OFFICIAL_ALERT"}: selected.append(source)
    wanted = set(radar.get("sources") or [])
    if wanted: selected = [s for s in selected if s.get("domain") in wanted or s.get("name") in wanted]
    if getattr(provider, "name", "") == "searxng_local":
        selected = [{"name":"SearXNG Local Search", "domain":"searxng.local",
                     "compliance_reviewed":True, "compliance_mode":"SEARCH_ONLY",
                     "health_status":"SEARCH_ONLY", "searchable_by_external_engine":True}]
    queries = expand_queries(radar)
    queries_planned = sum(len(queries) for s in selected if s.get("searchable_by_external_engine", True))
    start = now_iso(); conn = _conn()
    try:
        cur = conn.execute("""INSERT INTO radar_runs(radar_id,started_at,planned_sources,queries_planned,run_status)
                            VALUES(?,?,?,?,?)""", (radar_id, start, len(selected), queries_planned, "RUNNING"))
        run_id = cur.lastrowid; conn.commit()
    finally: conn.close()
    if not queries:
        conn = _conn()
        try:
            conn.execute("""UPDATE radar_runs SET finished_at=?,run_status='FAILED',provider_detail='QUERY_PLAN_EMPTY'
                          WHERE id=?""", (now_iso(), run_id)); conn.commit()
        finally: conn.close()
        return get_run(run_id)
    raw, failures, successes, source_details = [], [], 0, []
    failure_counts = {"timeout": 0, "blocked": 0, "network": 0, "parser": 0, "other": 0}
    normalized, seen, seen_urls, clusters = [], set(), set(), set()
    stats = {"duplicate":0, "matched":0, "actionable":0, "new":0, "sequence":0,
             "sold":0, "past_auction":0, "museum":0, "article":0, "historical":0, "rejected":0}
    pending_emails = []
    def emit(event_type: str, query_id="", payload=None):
        conn = _conn()
        try:
            conn.execute("INSERT INTO radar_run_events(run_id,event_type,query_id,payload_json,created_at) VALUES(?,?,?,?,?)",
                         (run_id, event_type, str(query_id), json.dumps(payload or {}, ensure_ascii=False), now_iso())); conn.commit()
        finally: conn.close()
    emit("RUN_STARTED", payload={"queries_planned":queries_planned})
    def query_started(query, query_index):
        emit("QUERY_STARTED", query_index, {"query":query})
        conn = _conn()
        try:
            conn.execute("UPDATE radar_runs SET current_query=? WHERE id=?", (query, run_id)); conn.commit()
        finally: conn.close()
    def accept_results(batch, query, query_index, source):
        emit("QUERY_COMPLETED", query_index, {"query":query, "results":len(batch)})
        for item in batch:
            item.setdefault("source_name", source.get("name")); raw.append(item)
            rec = normalize_result(item, radar); fp = fingerprint(rec); url_key = canonical_url(rec.get("source_url") or "")
            try: require_source_url(rec.get('source_url'))
            except ValueError:
                stats['rejected'] += 1
                continue
            emit("RESULT_RECEIVED", query_index, {"url":rec.get("source_url"), "title":rec.get("title")})
            if (url_key and url_key in seen_urls) or fp in seen: stats["duplicate"] += 1; continue
            if url_key: seen_urls.add(url_key)
            seen.add(fp); normalized.append(rec)
            outcome = match_rules(rec, radar["rules"], radar["notify_unknown_price"])
            if not outcome["matched"] and not changes.tracked(radar_id,url_key): stats["rejected"] += 1; continue
            stats["matched"] += 1; emit("CANDIDATE_CREATED", query_index, {"title":rec["title"]})
            availability = classify_availability(rec); quality = score_actionability(rec, outcome, availability)
            rec.update(quality, availability=availability, same_object_cluster_id=same_object_cluster_id(rec))
            cid, change_events = changes.observe(radar_id,run_id,rec,url_key,notifications=send_email)
            rec['candidate_id'] = cid
            meaningful = any(e['event_type'] in {'NEW_LISTING','PRICE_ADDED','PRICE_CHANGED','STATUS_CHANGED','RELISTED'} for e in change_events)
            known = _rows('SELECT id FROM discoveries WHERE candidate_id=?',(cid,))
            if known and not rec['discovery_eligible']:
                # Sold observations still update the known item and surface its change.
                rec['discovery_eligible'] = True
            if not rec["discovery_eligible"]:
                key = availability.lower()
                if key in stats: stats[key] += 1
                elif availability == "REFERENCE": stats["article"] += 1
                if rec["historical_context"]: stats["historical"] += 1
                stats["rejected"] += 1; continue
            if rec["same_object_cluster_id"] in clusters: stats["duplicate"] += 1; continue
            clusters.add(rec["same_object_cluster_id"]); stats["actionable"] += 1; stats["sequence"] += 1
            rec.update(run_id=run_id, query_id=str(query_index), stream_sequence=stats["sequence"])
            d, is_new, price_changed = _upsert_discovery(radar, rec, outcome)
            if meaningful: stats["new"] += 1; emit("DISCOVERY_CREATED", query_index, {"discovery_id":d["id"], "sequence":stats["sequence"]})
            else: emit("DISCOVERY_UPDATED", query_index, {"discovery_id":d["id"]})
            # Changes mark the existing queue only; no additional notification channel.
        conn = _conn()
        try:
            conn.execute("""UPDATE radar_runs SET queries_completed=?,queries_failed=?,raw_results=?,normalized_results=?,
                         duplicates_removed=?,matches=?,relevant_results=?,actionable_results=?,new_discoveries=?,
                         historical_results=?,already_seen_results=?,rejected_results=?,current_query=? WHERE id=?""",
                         (metrics_provider.completed, metrics_provider.failed, len(raw), len(normalized), stats["duplicate"],
                          stats["matched"], stats["matched"], stats["actionable"], stats["new"], stats["historical"],
                          stats["duplicate"], stats["rejected"], query, run_id)); conn.commit()
        finally: conn.close()
    adaptive_seen_domains = set()
    def adaptive_expand(batch, query):
        core = " ".join(str(x) for x in (radar["rules"].get("maker_artist"), radar["rules"].get("model_series")) if x and x != "unknown")
        additions = []
        for item in batch:
            domain = urlparse(item.get("url") or item.get("source_url") or "").netloc.lower().replace("www.", "")
            if domain and domain not in adaptive_seen_domains:
                adaptive_seen_domains.add(domain); additions.append(f"site:{domain} {core} available")
        return additions
    def queries_added(fresh):
        nonlocal queries_planned
        queries_planned += len(fresh)
        conn = _conn()
        try:
            conn.execute("""UPDATE radar_runs SET queries_planned=?,queries_added_dynamically=queries_added_dynamically+?
                          WHERE id=?""", (queries_planned, len(fresh), run_id)); conn.commit()
        finally: conn.close()
    for source in selected:
        source_started = time.perf_counter()
        try:
            source.setdefault("access_mode", source.get("access_method") or "SEARCH_ONLY")
            source.setdefault("searchable_by_external_engine", True)
            adapter = adapter_for(source, metrics_provider)
            if isinstance(adapter, GenericSearchAdapter):
                found = adapter.search(source, queries, lambda batch, query, idx: accept_results(batch, query, idx, source),
                                       adaptive_expand, queries_added, query_started)
            else:
                found = adapter.search(source, queries)
                accept_results(found, "direct-approved", metrics_provider.completed, source)
            provider_status = getattr(provider, "status", "OK")
            successes += 1
            source_details.append({"source_name": source.get("name"), "domain": source.get("domain"),
                                   "outcome": "SUCCESSFUL", "reason": "", "compliance_mode": source.get("compliance_mode"),
                                   "health": source.get("health_status"), "latency_ms": round((time.perf_counter()-source_started)*1000)})
            conn = _conn()
            try:
                conn.execute("""UPDATE source_registry SET last_success=?,last_check=?,success_count=success_count+1,
                  health_status=CASE WHEN access_mode='SEARCH_ONLY' THEN 'SEARCH_ONLY' ELSE 'HEALTHY' END,
                  failure_reason='' WHERE domain=?""", (now_iso(), now_iso(), source["domain"])); conn.commit()
            finally: conn.close()
        except Exception as exc:
            provider_status = getattr(provider, "status", "SEARCH_PROVIDER_UNAVAILABLE")
            error_text = str(exc).lower()
            reason = "timeout" if "timeout" in error_text else "blocked" if any(x in error_text for x in ("403", "blocked", "forbidden")) else "network" if any(x in error_text for x in ("network", "connection", "dns")) else "parser" if any(x in error_text for x in ("parse", "parser")) else "other"
            failure_counts[reason] += 1
            source_details.append({"source_name": source.get("name"), "domain": source.get("domain"),
                                   "outcome": "FAILED", "reason": reason, "compliance_mode": source.get("compliance_mode"),
                                   "health": source.get("health_status"), "latency_ms": round((time.perf_counter()-source_started)*1000)})
            failures.append({"source": source.get("name"), "domain": source.get("domain"), "error": str(exc)[:160]})
            conn = _conn()
            try:
                state = "LOGIN_REQUIRED" if "login" in str(exc).lower() else "DEGRADED"
                conn.execute("""UPDATE source_registry SET failure_count=failure_count+1,last_check=?,
                  health_status=?,failure_reason=? WHERE domain=?""",
                  (now_iso(), state, str(exc)[:160], source["domain"])); conn.commit()
            finally: conn.close()
    coverage = {"registered": registered, "eligible": eligible, "planned": len(selected), "successful": successes}
    duplicate_count, matched, actionable, new_count, sent = (stats["duplicate"], stats["matched"], stats["actionable"], stats["new"], 0)
    discarded = {k:stats[k] for k in ("sold", "past_auction", "museum", "article", "duplicate")}
    coverage.update({"queries_completed": metrics_provider.completed, "queries_planned": queries_planned,
                     "results_processed": len(normalized), "target_scope": registered})
    for d in pending_emails:
        try:
            if _email_new_discovery(radar, d, coverage):
                sent += 1; conn = _conn()
                try:
                    conn.execute("UPDATE discoveries SET notified_at=? WHERE id=?", (now_iso(), d["id"])); conn.commit()
                finally: conn.close()
        except Exception as exc: failures.append({"source":"EMAIL", "error":str(exc)[:160]})
    finished = now_iso(); conn = _conn()
    try:
        queries_completed = metrics_provider.completed
        queries_failed = max(metrics_provider.failed, queries_planned - queries_completed)
        status = "COMPLETE" if queries_planned > 0 and queries_completed == queries_planned else "PARTIAL" if queries_completed > 0 else "FAILED"
        integrity = "OK" if successes <= len(selected) <= eligible <= registered else "DATA_INTEGRITY_ERROR"
        if integrity != "OK": status = "DATA_INTEGRITY_ERROR"
        if integrity != "OK":
            print(f"[SIGNAL coverage] DATA_INTEGRITY_ERROR run={run_id} successful={successes} planned={len(selected)} eligible={eligible} registered={registered}", flush=True)
        snapshot = {**coverage, "skipped": sum(skip.values()), "failed": sum(failure_counts.values()), "timestamp": finished}
        conn.execute("""UPDATE radar_runs SET finished_at=?,successful_sources=?,failed_sources=?,raw_results=?,
          normalized_results=?,duplicates_removed=?,matches=?,new_discoveries=?,notifications_sent=?,failures_json=?,
          registered_sources=?,reviewed_eligible_sources=?,successfully_checked_sources=?,skipped_unreviewed=?,
          skipped_login_required=?,skipped_do_not_automate=?,skipped_review_expired=?,skipped_paused=?,failed_timeout=?,
          failed_blocked=?,failed_network=?,failed_parser=?,failed_other=?,coverage_ratio_eligible=?,coverage_ratio_run=?,
          run_status=?,coverage_snapshot_json=?,integrity_status=?,source_details_json=?,search_provider_status=?,
          search_provider_name=?,provider_detail=?,queries_planned=?,queries_completed=?,queries_failed=?,
          search_results_received=?,search_results_processed=?,search_results_deduplicated=?,
          target_market_sources_registered=?,relevant_results=?,actionable_results=?,discarded_sold=?,
          discarded_past_auction=?,discarded_museum=?,discarded_article=?,discarded_duplicate=? WHERE id=?""",
          (finished, successes, len(failures), len(raw), len(normalized), duplicate_count, matched, new_count, sent,
           json.dumps(failures, ensure_ascii=False), registered, eligible, successes, skip["unreviewed"],
           skip["login_required"], skip["do_not_automate"], skip["review_expired"], skip["paused"],
           failure_counts["timeout"], failure_counts["blocked"], failure_counts["network"], failure_counts["parser"],
           failure_counts["other"], eligible / registered if registered else 0, successes / len(selected) if selected else 0,
           status, json.dumps(snapshot, ensure_ascii=False), integrity, json.dumps(source_details, ensure_ascii=False),
           provider_status, getattr(provider, "name", "none"), getattr(provider, "technical_detail", ""),
           queries_planned, queries_completed, queries_failed, metrics_provider.results_received, len(normalized),
           duplicate_count, registered, matched, actionable, discarded["sold"], discarded["past_auction"],
           discarded["museum"], discarded["article"], discarded["duplicate"], run_id))
        conn.execute("UPDATE radars SET last_run_at=? WHERE id=?", (finished, radar_id)); conn.commit()
    finally: conn.close()
    emit("RUN_COMPLETED", payload={"status":status, "queries_completed":queries_completed, "new_discoveries":new_count})
    return get_run(run_id)


def get_run(run_id: int) -> Optional[dict]:
    rows = _rows("SELECT * FROM radar_runs WHERE id=?", (run_id,))
    if not rows: return None
    row = rows[0]; row["failures"] = json.loads(row.pop("failures_json") or "[]")
    row["coverage_snapshot"] = json.loads(row.pop("coverage_snapshot_json") or "{}")
    row["source_details"] = json.loads(row.pop("source_details_json") or "[]")
    row["coverage_percent"] = round(100 * row["successful_sources"] / row["planned_sources"]) if row["planned_sources"] else 0
    row["complete"] = row["run_status"] == "COMPLETE"
    conn=_conn()
    try:
        changes.init(conn)
        for name,kind in [('new_listings','NEW_LISTING'),('price_changes','PRICE_CHANGED'),('status_changes','STATUS_CHANGED'),('relisted','RELISTED')]:
            row[name]=conn.execute('SELECT COUNT(*) FROM candidate_changes WHERE run_id=? AND event_type=?',(run_id,kind)).fetchone()[0]
    finally: conn.close()
    return row


def list_runs(radar_id: Optional[int] = None) -> list[dict]:
    init_radar_tables(); rows = _rows("SELECT * FROM radar_runs" + (" WHERE radar_id=?" if radar_id else "") + " ORDER BY id DESC", (radar_id,) if radar_id else ())
    return [get_run(x["id"]) for x in rows]


def list_run_events(run_id: int, after_id: int = 0) -> list[dict]:
    rows = _rows("SELECT * FROM radar_run_events WHERE run_id=? AND id>? ORDER BY id", (run_id, after_id))
    for row in rows: row["payload"] = json.loads(row.pop("payload_json") or "{}")
    return rows


def dashboard() -> dict:
    radars = list_radars(); runs = list_runs(); latest = runs[0] if runs else None
    latest_by_radar = {}
    for run in runs: latest_by_radar.setdefault(run["radar_id"], run)
    for radar in radars: radar["coverage"] = latest_by_radar.get(radar["id"])
    discoveries = list_discoveries()
    viewed = [d for d in discoveries if d["status"] != "NEW"]
    useful = [d for d in viewed if d.get("useful") == 1]
    best_by_radar = {}
    for item in discoveries: best_by_radar.setdefault(item["radar_id"], item)
    return {"radars": radars, "active_count": sum(1 for r in radars if r["enabled"]), "latest_run": latest,
            "discoveries": discoveries, "today_discoveries": list(best_by_radar.values()),
            "new_count": sum(1 for d in discoveries if d["status"] == "NEW"),
            "kpis": {"coverage": latest["coverage_percent"] if latest else None,
                     "precision": round(100*len(useful)/len(viewed)) if viewed else None,
                     "new_discoveries": len(discoveries)}}
