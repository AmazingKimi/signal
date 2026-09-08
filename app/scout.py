"""Scout — 主动发现未知机会（0.6）。

三个岗位彻底分开：
    Scout = 找东西（本模块）   Analyst = 判断东西（0.4）   Monitor = 盯东西（0.5）

铁律：
- Scout 不是全互联网乱搜：只按 Scout Profile 的 Discovery Seeds 定向巡视
- 纯确定性（0 次 LLM 调用）：漏斗、六维评分、抑制全走规则
- Price Anomaly 谨慎（7 节）：只有存在 VERIFIED SOLD 基准才能说"便宜"；
  无基准只标 POSSIBLE OPPORTUNITY，禁止"低估/捡漏"措辞
- Known Object Suppression（14 节）：NOT INTERESTED 不重复推荐，除非价格降 ≥20%（MATERIAL CHANGE）
- 论坛/Tier 3 只能作 CLUE，绝不能形成"便宜"判断（23 节）
- 不自动修改 Profile（3 节）：只在 run 结果里附"种子建议"，由用户确认
"""
import re
import time
from typing import List, Optional, Tuple

from .models import (
    SC_STATUS_MATERIAL_CHANGE,
    SC_STATUS_NEW,
    SC_STATUS_NOT_INTERESTED,
    SC_STATUS_WRONG_MATCH,
    SALE_ASKING,
    SALE_SOLD,
    Comparable,
    now_iso,
)
from .research import generate_queries, source_tier

_SCORE_MIN_SHOW = 55
_TOP_LIMIT = 5
_SCARCITY_WORDS = (
    "estate", "private collection", "no reserve", "fresh to market", "single owner",
    "competition", "prototype", "archive", "one of", "rare", "museum", "provenance",
    "from the collection", "deaccession", "inherited",
)
_PROFILE_KEYWORDS = (
    "kinetic", "kinetische", "museum-level", "post-war", "contemporary",
    "homologation", "competition car", "important design",
)


def _median(xs: List[float]) -> Optional[float]:
    return round(sorted(xs)[len(xs) // 2]) if xs else None


def _domain(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url or "").netloc.replace("www.", "")[:40]


# ---------------------------------------------------------------- 六维评分（6 节）

def scout_score(
    rec: Comparable,
    profile: dict,
    seed_hits: List[str],
    sold_median: Optional[float],
) -> dict:
    """确定性六维评分，返回 {scores: {...}, scout_score, notes: [...]}。"""
    title = f"{rec.title} {rec.evidence_excerpt or ''}".lower()
    s = 0
    notes: List[str] = []

    pm = 0
    cats = [c.lower() for c in (profile.get("categories") or [])]
    cat_hit = any(c in title or c in seed_hits for c in cats)
    if cat_hit:
        pm += 8
        notes.append("Matches your categories")
    if seed_hits:
        pm += min(8, len(seed_hits) * 4)
        notes.append("Matches seeds: " + ", ".join(seed_hits[:3]))
    interests = (profile.get("interests") or "").lower()
    if any(kw in title or kw in interests for kw in _PROFILE_KEYWORDS):
        pm += 4
        notes.append("Matches your stated interests")
    s += min(20, pm)

    pa = 0
    if rec.price and sold_median and sold_median > 0 and rec.sale_type == SALE_ASKING:
        diff = (sold_median - rec.price) / sold_median
        if diff > 0.05:
            pa = min(25, int(10 + diff * 100))
            notes.append(f"Asking below verified recent market ({diff*100:.0f}%)")
        elif diff < -0.2:
            notes.append("Asking above verified market — not a price opportunity")
    if pa == 0:
        notes.append("No verified benchmark — possible opportunity only, not a confirmed bargain")
    s += pa

    sc = 0
    for w in _SCARCITY_WORDS:
        if w in title:
            sc += 5
    sc = min(20, sc)
    if sc:
        notes.append("Scarcity signals present")
    s += sc

    tier = rec.source_tier or source_tier(rec.source_url or "")
    s += {1: 15, 2: 8, 3: 0}.get(tier, 5)

    fr = 3
    try:
        from datetime import datetime
        seen = datetime.fromisoformat(rec.retrieved_at or rec.sold_at or "").timestamp()
        age_h = (time.time() - seen) / 3600
        if age_h < 24:
            fr = 10
            notes.append("Fresh listing (<24h)")
        elif age_h < 72:
            fr = 7
        elif age_h < 168:
            fr = 4
        else:
            fr = 1
    except Exception:
        pass
    s += fr

    ev = 0
    if sold_median:
        ev = 10
        notes.append("Verified comparable evidence available")
    elif rec.verified:
        ev = 5
        notes.append("Sourced from original listing")
    s += ev

    return {"scores": {"profile_match": min(20, pm), "price_anomaly": pa,
                       "scarcity": min(20, sc), "source_quality": {1: 15, 2: 8, 3: 0}.get(
                           rec.source_tier or source_tier(rec.source_url or ""), 5),
                       "freshness": fr, "evidence_strength": ev},
            "scout_score": min(100, s), "notes": notes}


# ---------------------------------------------------------------- Funnel（5 节）

def run_scout(provider=None, on_step=None, llm=None) -> dict:
    """执行一次 Scout 巡视。返回 {run_id, top: [...], funnel: {...}, no_compelling: bool}。"""
    from .interests import generate_search_plan, generate_seeds, parse_interest
    from .models import ScoutProfile
    from .store import (
        scout_candidate_upsert,
        scout_known_fingerprints,
        scout_latest_run,
        scout_profile_get,
        scout_run_create,
    )

    def step(msg: str):
        if on_step:
            try:
                on_step(msg)
            except Exception:
                pass

    profile = scout_profile_get() or ScoutProfile().model_dump()
    known = scout_known_fingerprints()
    t0 = time.time()
    funnel = {"results_raw": 0, "duplicates_removed": 0, "known_removed": 0,
              "profile_removed": 0, "source_removed": 0, "weak_removed": 0,
              "candidates": 0, "top_shown": 0}
    step("SCOUT 开始（市场巡视）")

    interests = (profile.get("interests") or "").strip()
    structured = profile.get("structured_interest") or {}
    generated_seeds = profile.get("generated_seeds") or {}
    parse_info = {"interests": interests[:200], "ok": False, "fallback_used": True,
                  "understood": [], "artist": None, "series": None,
                  "maker": None, "model": None, "budget_max": None, "error": ""}

    if structured:
        parse_info.update({
            "understood": structured.get("understood") or [],
            "artist": structured.get("artist"), "series": structured.get("series"),
            "maker": structured.get("maker"), "model": structured.get("model"),
            "budget_max": structured.get("budget_max"),
            "fallback_used": bool(structured.get("fallback_used")),
            "ok": bool(structured.get("artist") or structured.get("model")
                       or structured.get("maker") or structured.get("understood")),
        })

    if interests and not structured:
        structured = parse_interest(interests, llm=llm)
        if structured and not structured.get("error"):
            generated_seeds = generate_seeds(structured)
            parse_info.update({
                "understood": structured.get("understood") or [],
                "artist": structured.get("artist"), "series": structured.get("series"),
                "maker": structured.get("maker"), "model": structured.get("model"),
                "budget_max": structured.get("budget_max"),
                "fallback_used": bool(structured.get("fallback_used")),
                "ok": bool(structured.get("artist") or structured.get("model")
                           or structured.get("understood")),
            })
            profile["structured_interest"] = structured
            profile["generated_seeds"] = generated_seeds
            try:
                from .store import scout_profile_save
                scout_profile_save(profile)
            except Exception:
                pass

    manual_artists = [a.strip() for a in (profile.get("seeds_artists") or "").split(",") if a.strip()]
    manual_models = [m.strip() for m in (profile.get("seeds_models") or "").split(",") if m.strip()]
    manual_kws = [k.strip() for k in (profile.get("seeds_keywords") or "").split(",") if k.strip()]

    seeds = {
        "artists": (manual_artists or []) + (generated_seeds.get("artists") or []),
        "models": (manual_models or []) + (generated_seeds.get("models") or []),
        "keywords": (manual_kws or []) + (generated_seeds.get("keywords") or []),
    }
    def _uniq(xs):
        seen, out = set(), []
        for x in xs:
            if x and x.lower() not in seen:
                seen.add(x.lower())
                out.append(x)
        return out
    seeds = {k: _uniq(v) for k, v in seeds.items()}

    has_any_seed = bool(seeds["artists"] or seeds["models"] or seeds["keywords"])
    if not has_any_seed:
        if interests:
            parse_info["ok"] = False
            parse_info["error"] = "关注条件未能成功解析，请补充艺术家/车型，或直接填写搜索对象。"
        else:
            parse_info["error"] = "尚未设置关注方向。先填写「我关注什么」或手动搜索对象。"
        run_id = scout_run_create({
            "started_at": now_iso(), "status": "PENDING", "parse_info": parse_info,
            "search_plan": [], "errors": [parse_info["error"]],
        })
        step(f"SCOUT 未运行：{parse_info['error']}")
        return {"run_id": run_id, "status": "PENDING", "top": [], "clues": 0,
                "funnel": funnel, "no_compelling": True, "parse_info": parse_info,
                "error": parse_info["error"], "duration_ms": int((time.time() - t0) * 1000)}

    parse_info["ok"] = True
    parse_info["understood"] = structured.get("understood") or []
    parse_info["artist"] = structured.get("artist") or (seeds["artists"][0] if seeds["artists"] else None)
    parse_info["series"] = structured.get("series")
    parse_info["maker"] = structured.get("maker")
    parse_info["model"] = structured.get("model")

    search_plan = generate_search_plan(structured, seeds)
    if not search_plan:
        search_plan = [f"{s} for sale" for s in (seeds["artists"] + seeds["models"])[:6]]
    step(f"已理解关注方向，生成 {len(search_plan)} 组搜索策略")

    if provider is not None and not getattr(provider, "available", False):
        run_id = scout_run_create({
            "started_at": now_iso(), "status": "FAILED", "parse_info": parse_info,
            "search_plan": search_plan,
            "errors": ["搜索服务暂不可用（网络或配置问题）"],
        })
        step("SCOUT 失败：无法连接市场")
        return {"run_id": run_id, "status": "FAILED", "top": [], "clues": 0,
                "funnel": funnel, "no_compelling": True, "parse_info": parse_info,
                "error": "搜索服务暂不可用", "duration_ms": int((time.time() - t0) * 1000)}

    from .research import run_research
    from .models import AnalysisInput
    all_recs: List[Comparable] = []
    seen_urls: set = set()
    cat = (profile.get("categories") or ["ART"])[0] if profile.get("categories") else "ART"
    for q in search_plan:
        inp = AnalysisInput(
            artist=structured.get("artist") or (seeds["artists"][0] if seeds["artists"] else ""),
            artwork=structured.get("model") or (seeds["models"][0] if seeds["models"] else "")
                    or q.split(" for sale")[0],
            category=structured.get("category") or cat,
            asking_price=structured.get("budget_max") or profile.get("budget_high"),
        )
        try:
            result = run_research(inp, provider, queries=[q], on_step=None)
        except Exception:
            continue
        for rec in result["records"]:
            if rec.source_url and rec.source_url not in seen_urls:
                seen_urls.add(rec.source_url)
                all_recs.append(rec)
            elif rec.source_url:
                funnel["duplicates_removed"] += 1
    funnel["results_raw"] = len(all_recs) + funnel["duplicates_removed"]
    step(f"发现市场记录 {len(all_recs)} 条（去重 {funnel['duplicates_removed']}）")

    unique: List[Comparable] = []
    fps_here: set = set()
    for rec in all_recs:
        fp = _scout_fp(rec, profile)
        if fp in fps_here:
            funnel["duplicates_removed"] += 1
            continue
        fps_here.add(fp)
        unique.append(rec)
    step(f"去重后 {len(unique)}（去 {funnel['duplicates_removed']}）")

    fresh: List[Comparable] = []
    for rec in unique:
        fp = _scout_fp(rec, profile)
        if fp in known:
            funnel["known_removed"] += 1
            continue
        fresh.append(rec)
    step(f"已知对象排除后 {len(fresh)}")

    budget_lo = profile.get("budget_low")
    budget_hi = profile.get("budget_high")
    match_terms = seeds["artists"] + seeds["models"] + seeds["keywords"]
    in_profile: List[Tuple[Comparable, List[str]]] = []
    for rec in fresh:
        if rec.price and budget_hi and rec.price > budget_hi * 1.5:
            funnel["profile_removed"] += 1
            continue
        if rec.price and budget_lo and rec.price < budget_lo * 0.1:
            funnel["profile_removed"] += 1
            continue
        hits = [sd for sd in match_terms if sd.lower() in f"{rec.title} {rec.evidence_excerpt or ''}".lower()]
        if not hits:
            funnel["profile_removed"] += 1
            continue
        in_profile.append((rec, hits))
    step(f"Profile 过滤后 {len(in_profile)}（去 {funnel['profile_removed']}）")

    tier_ok: List[Tuple[Comparable, List[str]]] = []
    clues: List[Tuple[Comparable, List[str]]] = []
    for rec, hits in in_profile:
        tier = rec.source_tier or source_tier(rec.source_url or "")
        if tier >= 3:
            clues.append((rec, hits))
            funnel["source_removed"] += 1
        else:
            tier_ok.append((rec, hits))
    step(f"Tier 3 CLUE {len(clues)}（仅线索，不参与评分推荐）")

    solds = [rec.price for rec, _ in tier_ok if rec.sale_type == SALE_SOLD and rec.price]
    sold_median = _median(solds)

    scored: List[dict] = []
    for rec, hits in tier_ok:
        res = scout_score(rec, profile, hits, sold_median)
        if res["scout_score"] < 25:
            funnel["weak_removed"] += 1
            continue
        scored.append({
            "fingerprint": _scout_fp(rec, profile),
            "category": profile.get("categories", ["ART"])[0],
            "maker": "", "object": rec.title[:160],
            "asking_price": rec.price, "currency": rec.currency or "EUR",
            "source_url": rec.source_url or "", "source_name": rec.source_name or _domain(rec.source_url or ""),
            "source_tier": rec.source_tier or source_tier(rec.source_url or ""),
            "sale_type": rec.sale_type,
            **res["scores"], "scout_score": res["scout_score"],
            "notes": "; ".join(res["notes"]),
        })
    scored.sort(key=lambda x: x["scout_score"], reverse=True)
    funnel["candidates"] = len(scored)
    funnel["below_threshold"] = 0
    step(f"候选 {len(scored)} 个")

    top = [c for c in scored if c["scout_score"] >= _SCORE_MIN_SHOW][:_TOP_LIMIT]
    funnel["top_shown"] = len(top)
    funnel["below_threshold"] = len(scored) - len(top)

    near_misses = [_decorate_near_miss(c) for c in scored
                   if _SCORE_MIN_SHOW > c["scout_score"] >= 25][:3]

    brief = build_brief(funnel, parse_info, len(top), len(near_misses),
                        queries_count=len(search_plan))

    run_id = scout_run_create({
        "started_at": now_iso(), "results_raw": funnel["results_raw"],
        "duplicates_removed": funnel["duplicates_removed"],
        "known_removed": funnel["known_removed"],
        "profile_removed": funnel["profile_removed"],
        "source_removed": funnel["source_removed"],
        "weak_removed": funnel["weak_removed"],
        "candidates": len(scored), "top_shown": len(top),
        "llm_calls": 0, "llm_tokens": 0,
        "estimated_cost_rmb": 0.0, "status": "COMPLETED",
        "parse_info": parse_info, "search_plan": search_plan,
        "brief": brief, "near_misses": near_misses,
    })
    for c in scored:
        c["run_id"] = run_id
        scout_candidate_upsert(c)

    run_data = {
        "run_id": run_id, "top": top, "clues": len(clues),
        "near_misses": near_misses, "brief": brief,
        "funnel": funnel, "sold_median": sold_median,
        "no_compelling": len(top) == 0,
        "status": "COMPLETED", "parse_info": parse_info, "search_plan": search_plan,
        "duration_ms": int((time.time() - t0) * 1000),
    }
    step(f"SCOUT 完成：{len(top)} 个值得看" if top else f"SCOUT 完成：0 推荐，{len(near_misses)} 个 near miss")
    return run_data


def _decorate_near_miss(c: dict) -> dict:
    scores = c.get("scores") or {}
    price_anomaly = scores.get("price_anomaly", 0) or c.get("price_anomaly", 0)
    source_quality = scores.get("source_quality", 0) or c.get("source_quality", 0)
    evidence = scores.get("evidence_strength", 0) or c.get("evidence_strength", 0)
    scarcity = scores.get("scarcity", 0) or c.get("scarcity", 0)
    profile_match = scores.get("profile_match", 0) or c.get("profile_match", 0)

    why_not: List[dict] = []
    if price_anomaly < 10:
        why_not.append({"zh": "价格优势证据不足（无可靠 SOLD 基准或差价不明显）",
                        "en": "No verified price advantage (no reliable SOLD benchmark)"})
    if source_quality < 10:
        why_not.append({"zh": "来源等级不足（非一级来源）", "en": "Source tier insufficient (not Tier 1)"})
    if evidence < 5:
        why_not.append({"zh": "缺乏可靠 SOLD comparable", "en": "Lacks reliable SOLD comparable"})
    if scarcity < 10:
        why_not.append({"zh": "稀缺性信号不足", "en": "Weak scarcity signals"})
    if profile_match < 10:
        why_not.append({"zh": "与兴趣方向匹配度一般", "en": "Moderate match to your interests"})
    if not why_not:
        why_not.append({"zh": "综合评分低于推荐门槛（55 分）", "en": "Below recommendation threshold (55)"})

    missing: List[dict] = []
    if not c.get("asking_price"):
        missing.append({"zh": "缺价格信息", "en": "Missing price info"})
    if (c.get("source_tier") or 0) > 1:
        missing.append({"zh": "缺官方拍行/画廊一级来源", "en": "Missing Tier 1 source"})
    if evidence < 5:
        missing.append({"zh": "缺成交价佐证", "en": "Missing sale evidence"})
    if not missing:
        missing.append({"zh": "缺更明确的版本/系列信息", "en": "Missing more specific model/series info"})

    out = dict(c)
    out["status"] = "NOT_RECOMMENDED"
    out["why_not"] = why_not
    out["missing"] = missing
    return out


def build_brief(funnel: dict, parse_info: dict, recommended: int, near_miss_count: int,
                queries_count: int = 0) -> dict:
    subject = (parse_info.get("artist") or parse_info.get("model") or
               parse_info.get("series") or "market")[:40]
    return {
        "subject": subject,
        "queries": queries_count,
        "records": funnel.get("results_raw", 0),
        "candidates": funnel.get("candidates", 0),
        "recommended": recommended,
        "near_miss_count": near_miss_count,
        "conclusion_key": ("found" if recommended > 0
                           else "near_miss" if funnel.get("candidates", 0) > 0
                           else "empty_scan"),
    }


def _scout_fp(rec: Comparable, profile: dict) -> str:
    from .watch import make_fingerprint
    return make_fingerprint(rec, {"maker": "", "target": ""})


# ---------------------------------------------------------------- Seed 建议（3 节）

def seed_suggestions() -> dict:
    from .store import history, watch_list
    artists: dict = {}
    models: dict = {}
    for row in history():
        obj = row.get("object") or ""
        if not obj:
            continue
        first = obj.split()[0] if obj.split() else ""
        artists[first] = artists.get(first, 0) + 1
    for w in watch_list():
        mk = (w.get("maker") or "").strip()
        if mk:
            models[mk] = models.get(mk, 0) + 1
    top_artists = [a for a, n in sorted(artists.items(), key=lambda x: -x[1])[:3] if n >= 2]
    top_models = [m for m, n in sorted(models.items(), key=lambda x: -x[1])[:3] if n >= 1]
    return {
        "suggested_artists": top_artists,
        "suggested_models": top_models,
        "note": "这些来自你的分析历史与 Watchlist——加入 Scout Interests 前请确认。",
    }