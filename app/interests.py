"""Interest Parser — 自然语言 Interests → 结构化兴趣 → Discovery Seeds → Search Queries（0.6.2）。

核心原则（0.6.2 任务书 04/05/06/07 节）：
    用户只需要告诉 Scout「我想找什么」，
    剩下的搜索词、Seed、Query 应由系统自己生成。

流程：
    user interests (natural language)
        ↓ parse_interest()
    StructuredInterest {artist/series/maker/model/aliases/attributes/region/budget/category}
        ↓ generate_seeds()
    Discovery Seeds {artists, models, keywords}
        ↓ generate_search_plan()
    Search Queries

铁律：
- LLM 优先解析（带缓存，Profile 未变化复用）；LLM 失败/无 Key → deterministic fallback（25 节）
- 解析失败不装死（12 节）：返回 understood 宽条件，允许宽搜索
- 数字/金额/型号原样保留，禁止 LLM 改写（10 节）
- 解析结果只用于生成搜索，绝不参与定价
"""
import hashlib
import json
import logging
import re
from typing import List, Optional

logger = logging.getLogger("kimi.interests")

# ---------------------------------------------------------------- 确定性词典

_KNOWN_ARTISTS = {
    "takis": "Panayiotis Vassilakis",
    "panayiotis vassilakis": "Panayiotis Vassilakis",
    "calder": "Alexander Calder",
    "alexander calder": "Alexander Calder",
    "fontana": "Lucio Fontana",
    "lucio fontana": "Lucio Fontana",
    "kusama": "Yayoi Kusama",
    "yayoi kusama": "Yayoi Kusama",
    "warhol": "Andy Warhol",
    "andy warhol": "Andy Warhol",
    "basquiat": "Jean-Michel Basquiat",
    "richter": "Gerhard Richter",
    "gerhard richter": "Gerhard Richter",
    "picasso": "Pablo Picasso",
    "pablo picasso": "Pablo Picasso",
    "miro": "Joan Miró",
    "pollock": "Jackson Pollock",
    "klein": "Yves Klein",
    "yves klein": "Yves Klein",
    "dubuffet": "Jean Dubuffet",
    "soto": "Jesús Rafael Soto",
    "jesus rafael soto": "Jesús Rafael Soto",
    "tinguely": "Jean Tinguely",
    "jean tinguely": "Jean Tinguely",
    "koons": "Jeff Koons",
    "hirst": "Damien Hirst",
}

_CAR_ALIASES = {
    "964 rs n-gt": "964 Carrera RS N-GT",
    "964 rs ngt": "964 Carrera RS N-GT",
    "964 rs n/gt": "964 Carrera RS N-GT",
    "964 n-gt": "964 Carrera RS N-GT",
    "964 ngt": "964 Carrera RS N-GT",
    "carrera rs competition": "964 Carrera RS N-GT",
    "rs n-gt": "964 Carrera RS N-GT",
    "rs ngt": "964 Carrera RS N-GT",
    "964 carrera rs": "964 Carrera RS",
    "964 rs": "964 Carrera RS",
    "964 turbo s": "964 Turbo S",
    "f40": "Ferrari F40",
    "300sl": "Mercedes 300SL",
    "300 sl": "Mercedes 300SL",
    "911 r": "911 R",
    "993 rs": "993 Carrera RS",
}

_VERSION_ALIASES = {
    "ngt": "N-GT",
    "n-gt": "N-GT",
    "n/gt": "N-GT",
    "m003": "M003",
    "m 003": "M003",
    "competition": "Carrera RS Competition",
}

_SERIES_WORDS = {
    "signals": "Signals",
    "signal": "Signals",
    "infinity nets": "Infinity Nets",
    "infinity net": "Infinity Nets",
    "telemagnetic": "Telemagnetic",
    "telepaintings": "Telepaintings",
    "hydromagnetic": "Hydromagnetic",
    "lumiere": "Lumière",
    "lumières": "Lumière",
    "brillo box": "Brillo Box",
    "flower": "Flowers",
    "dot": "Dots",
    "pumpkin": "Pumpkin",
    "campbell": "Campbell's Soup",
    "black paintings": "Black Paintings",
    "cut-out": "Cut-outs",
    "concetto spaziale": "Concetto Spaziale",
}

_CATEGORY_KEYWORDS = {
    "CLASSIC_CAR": ("车", "car", "porsche", "ferrari", "mercedes", "bmw", "911", "964",
                    "f40", "300sl", "车型", "homologation"),
    "DESIGN": ("设计", "design", "furniture", "家具", "灯具", "lamp", "椅", "chair"),
    "COLLECTIBLE": ("收藏品", "collectible", "watch", "表", "手办", "wine", "酒"),
    "ART": ("艺术", "art", "作品", "雕塑", "sculpture", "画", "painting", "画廊", "gallery",
            "艺术家", "artist", "kinetic", "动态"),
}

_REGION_KEYWORDS = {
    "Europe": ("欧洲", "europe", "european", "german", "德国", "italian", "意大利",
               "french", "法国", "swiss", "瑞士", "uk", "英国", "brussels", "巴黎", "伦敦"),
    "Asia": ("亚洲", "asia", "japan", "日本", "china", "中国", "hk", "香港"),
    "North America": ("美国", "usa", "us market", "北美", "america"),
}

_BUDGET_PATTERNS = [
    re.compile(r"(?:€|eur|euros?|usd|\$)?\s*([\d][\d,]*(?:\.\d+)?)\s*(k|千|万)?", re.I),
]

_PRICE_CLAUSE = re.compile(
    r"(?:低于|少于|不超过|以内|below|under|less than|max|预算|budget)"
    r"\s*(?:€|eur|usd|\$)?\s*([\d][\d,]*(?:\.\d+)?)\s*(k|千|万)?",
    re.I,
)
_PRICE_CLAUSE_TAIL = re.compile(
    r"([\d][\d,]*(?:\.\d+)?)\s*(万|千|k)?\s*(欧元|欧|eur|euros?|€|usd|\$)?\s*(以内|以下|以内|左右|不超过|below|under|max)",
    re.I,
)


def _to_number(raw: str, unit: str) -> Optional[float]:
    """'20' + 'k' → 20000；'30' + '万' → 300000；'300,000' → 300000。"""
    if raw is None:
        return None
    try:
        n = float(raw.replace(",", "").replace("，", "").strip())
    except ValueError:
        return None
    if unit:
        u = unit.strip().lower()
        if u == "k":
            n *= 1000
        elif u == "万":
            n *= 10000
        elif u == "千":
            n *= 1000
    return n


def _find_budget(text: str) -> Optional[float]:
    m = _PRICE_CLAUSE.search(text)
    if m:
        return _to_number(m.group(1), m.group(2))
    m = _PRICE_CLAUSE_TAIL.search(text)
    if m:
        return _to_number(m.group(1), m.group(2) or "")
    return None


def _pick_category(text: str) -> Optional[str]:
    low = text.lower()
    best, best_hits = None, 0
    for cat, kws in _CATEGORY_KEYWORDS.items():
        hits = sum(1 for k in kws if k in low)
        if hits > best_hits:
            best, best_hits = cat, hits
    return best if best_hits > 0 else None


def _pick_region(text: str) -> Optional[str]:
    low = text.lower()
    for region, kws in _REGION_KEYWORDS.items():
        if any(k in low for k in kws):
            return region
    return None


# ---------------------------------------------------------------- 确定性解析

def deterministic_parse(text: str) -> dict:
    low = text.lower()
    out = {
        "understood": [], "artist": None, "artist_full": None, "series": None,
        "maker": None, "model": None, "aliases": [], "attributes": [],
        "region": None, "category": None, "budget_max": None,
        "confidence": 0.0, "fallback_used": True,
    }

    for key, full in _KNOWN_ARTISTS.items():
        if key in low:
            out["artist"] = key.title()
            out["artist_full"] = full
            out["understood"].append(f"艺术家 {out['artist']}")
            out["confidence"] += 0.5
            break

    if not out["artist"] or out["model"] is None:
        for key, canon in _SERIES_WORDS.items():
            if key in low:
                out["series"] = canon
                out["understood"].append(f"系列 {canon}")
                out["confidence"] += 0.3
                break
        if not out["series"] and out["artist"]:
            m = re.search(r"(?:takis|calder|fontana|kusama|warhol|richter|picasso|miro|pollock|klein)\s*的\s*([\u4e00-\u9fffA-Za-z][A-Za-z\u4e00-\u9fff /&-]{1,24})", text, re.I)
            if m:
                cand = m.group(1).strip()
                out["series"] = cand.title()
                out["understood"].append(f"系列 {cand.title()}")
                out["confidence"] += 0.3
        if not out["series"] and out["artist"]:
            toks = [t for t in re.findall(r"[A-Za-z][A-Za-z\-/&]{1,25}", text)
                    if t.lower() not in (out["artist"] or "").lower()
                    and t.lower() not in ("porsche", "ferrari", "mercedes", "n-gt", "ngt", "m003")]
            if toks:
                out["series"] = toks[0].title()
                out["understood"].append(f"系列 {toks[0].title()}")
                out["confidence"] += 0.25

    for alias, model in _CAR_ALIASES.items():
        if alias in low:
            out["model"] = model
            if "porsche" in low:
                out["maker"] = "Porsche"
            elif "ferrari" in low or "f40" in low:
                out["maker"] = "Ferrari"
            elif "mercedes" in low or "300sl" in low or "300 sl" in low:
                out["maker"] = "Mercedes"
            out["understood"].append(f"车型 {model}")
            out["confidence"] += 0.5
            break

    for key, canon in _VERSION_ALIASES.items():
        if key in low and canon not in out["aliases"]:
            out["aliases"].append(canon)

    out["category"] = _pick_category(text)
    if out["category"]:
        out["understood"].append(f"类别 {out['category']}")
    out["region"] = _pick_region(text)
    if out["region"]:
        out["understood"].append(f"地区 {out['region']}")
    out["budget_max"] = _find_budget(text)
    if out["budget_max"]:
        out["understood"].append(f"预算上限 €{out['budget_max']:,.0f}")

    _ATTR_PATTERNS = [
        ("double light", ("双灯", "双灯头", "double light", "two lights", "double-lamp")),
        ("pink", ("粉色", "粉", "pink")),
        ("early works", ("早期", "early")),
        ("small format", ("小尺寸", "small format", "petit format")),
        ("kinetic sculpture", ("会动", "动态", "kinetic", "kinetische", "活动雕塑")),
        ("light sculpture", ("带灯", "灯光", "light sculpture", "illuminated", "light art")),
        ("1960-1970s", ("六七十年代", "60年代", "70年代", "1960", "1970", "1960s", "1970s",
                        "sixties", "seventies", "post-war")),
        ("sculpture", ("雕塑", "sculpture")),
        ("unique piece", ("孤品", "unique", "one-off")),
    ]
    for canon, kws in _ATTR_PATTERNS:
        if any(k in low for k in kws):
            out["attributes"].append(canon)
            out["understood"].append(canon)

    out["confidence"] = min(0.95, out["confidence"])
    if not out["artist"] and not out["model"]:
        if out["attributes"] or out["region"] or out["category"]:
            out["understood"] = out["understood"] or ["1960-1970 年代", "欧洲", "动态 / 灯光雕塑"]
    return out


# ---------------------------------------------------------------- LLM 解析

_LLM_PARSE_SYSTEM = """你是 Kimi Intelligence 的「兴趣解析器」。把用户用自然语言描述的收藏/购买兴趣，
解析为结构化的搜索对象。规则（严格遵守）：

1. 只做理解与抽取，不做任何价格判断。
2. 不确定的字段必须给 null，绝对禁止编造。
3. 数字、金额、型号代码（M003/N-GT/964 等）必须原样保留，禁止换算、改写。
4. 用 understood 列出你确定理解的要点（2-5 条，简短）。
5. 输出严格 JSON（不要 markdown 代码块）：
{
  "understood": ["1960-1970年代", "欧洲", "动态/灯光雕塑"],
  "artist": "Takis" | null,
  "artist_full": "Panayiotis Vassilakis" | null,
  "series": "Signals" | null,
  "maker": "Porsche" | null,
  "model": "964 Carrera RS N-GT" | null,
  "aliases": ["N-GT","NGT","M003","Carrera RS Competition"],
  "attributes": ["double light","pink","early works"],
  "region": "Europe" | null,
  "category": "ART" | "CLASSIC_CAR" | "DESIGN" | "COLLECTIBLE" | null,
  "budget_max": 20000 | null,
  "confidence": 0.0
}"""


def _llm_parse(text: str, llm) -> Optional[dict]:
    """LLM 解析（失败返回 None，由调用方降级 deterministic）。"""
    try:
        parsed = llm.chat(
            [
                {"role": "system", "content": _LLM_PARSE_SYSTEM},
                {"role": "user", "content": f"用户的兴趣描述：\n{text}\n\n请解析为结构化 JSON。"},
            ],
            temperature=0.1,
        )
    except Exception as exc:
        logger.warning("interest LLM parse failed: %s", exc)
        return None
    if not isinstance(parsed, dict):
        return None
    out = {
        "understood": [str(x) for x in (parsed.get("understood") or [])],
        "artist": parsed.get("artist") or None,
        "artist_full": parsed.get("artist_full") or None,
        "series": parsed.get("series") or None,
        "maker": parsed.get("maker") or None,
        "model": parsed.get("model") or None,
        "aliases": [str(x) for x in (parsed.get("aliases") or [])],
        "attributes": [str(x) for x in (parsed.get("attributes") or [])],
        "region": parsed.get("region") or None,
        "category": parsed.get("category") or None,
        "budget_max": parsed.get("budget_max"),
        "confidence": float(parsed.get("confidence") or 0.3),
        "fallback_used": False,
    }
    return out


# ---------------------------------------------------------------- 主入口 + 缓存

def parse_interest(text: str, llm=None, cache_get=None, cache_put=None) -> dict:
    text = (text or "").strip()
    if not text:
        return {"error": "empty"}
    h = hashlib.md5(text.encode("utf-8")).hexdigest()
    if cache_get:
        try:
            hit = cache_get(h)
            if hit:
                return hit
        except Exception:
            pass

    result = None
    if llm is not None and getattr(llm, "configured", False):
        result = _llm_parse(text, llm)
    if result is None:
        result = deterministic_parse(text)

    if cache_put:
        try:
            cache_put(h, result)
        except Exception:
            pass
    return result


def structured_summary(s: dict) -> dict:
    if not s or s.get("error"):
        return {"understood": [], "artist": None, "series": None,
                "maker": None, "model": None, "category": None,
                "budget_max": None, "fallback_used": True, "ok": False}
    return {
        "understood": s.get("understood") or [],
        "artist": s.get("artist"), "artist_full": s.get("artist_full"),
        "series": s.get("series"), "maker": s.get("maker"), "model": s.get("model"),
        "category": s.get("category"), "budget_max": s.get("budget_max"),
        "fallback_used": bool(s.get("fallback_used")),
        "ok": bool(s.get("artist") or s.get("model") or s.get("understood")),
    }


# ---------------------------------------------------------------- Seeds / Queries

def generate_seeds(structured: dict) -> dict:
    artists, models, kws = [], [], []
    if structured.get("artist"):
        artists.append(structured["artist"])
    if structured.get("artist_full") and structured["artist_full"] not in artists:
        artists.append(structured["artist_full"])
    if structured.get("model"):
        models.append(structured["model"])
    if structured.get("maker") and not structured.get("model"):
        models.append(structured["maker"])
    for a in structured.get("aliases") or []:
        models.append(a)
    for att in structured.get("attributes") or []:
        kws.append(att)
    if structured.get("series"):
        kws.append(structured["series"])
    if structured.get("region"):
        kws.append(structured["region"])
    def _uniq(xs):
        seen, out = set(), []
        for x in xs:
            if x and x.lower() not in seen:
                seen.add(x.lower())
                out.append(x)
        return out
    return {"artists": _uniq(artists), "models": _uniq(models), "keywords": _uniq(kws)}


def generate_search_plan(structured: dict, seeds: dict) -> List[str]:
    from .research import generate_queries

    plan: List[str] = []
    seen: set = set()

    def _add(q):
        k = q.lower()
        if q and k not in seen:
            seen.add(k)
            plan.append(q)

    if structured.get("artist"):
        for q in generate_queries(structured["artist"], structured.get("series") or "",
                                  None):
            _add(q)
        if structured.get("artist_full") and structured["artist_full"] != structured["artist"]:
            for q in generate_queries(structured["artist_full"],
                                      structured.get("series") or "", None)[:3]:
                _add(q)
    if structured.get("model"):
        for q in generate_queries(structured.get("maker") or "", structured["model"], None):
            _add(q)
        for alias in (structured.get("aliases") or [])[:3]:
            _add(f"{structured.get('maker') or ''} {alias} for sale".strip())
            _add(f"{structured.get('maker') or ''} {alias} auction result".strip())
    if not structured.get("artist") and not structured.get("model"):
        parts = []
        if structured.get("category"):
            parts.append(structured["category"].lower())
        for att in (structured.get("attributes") or [])[:2]:
            parts.append(att)
        if structured.get("region"):
            parts.append(structured["region"])
        if parts:
            subject = " ".join(parts)
            for q in generate_queries("", subject, None):
                _add(q)
    for kw in (seeds.get("keywords") or [])[:4]:
        if kw.lower() not in " ".join(plan).lower():
            _add(f"{kw} for sale")
            _add(f"{kw} auction")
    return plan[:14]
