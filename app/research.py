"""Research Layer —— 主动联网研究（0.2 第三节）。

架构（任务书第十四节：LLM ≠ 互联网，Research Layer 独立于模型）：

    Official Search API（Tavily / Brave，必须显式配置）
          ↓
    URL Discovery（多组 Query 生成，含同义词归一）
          ↓
    Search Result Document（title / snippet / url / metadata）
          ↓
    Local Extraction（确定性正则抽取：金额 + 成交性质 + 口径）
          ↓
    Evidence Records（带 source_tier / sale_type / price_basis）

硬约束：
- 抽不出来就是抽不出来——宁可 0 条，不编 1 条
- Tier 3 来源（聚合站/论坛）不单独构成定价依据（由 comparability/定价池把控）
- 全程捕获异常：搜索失败 = 报告里如实写失败，不阻塞确定性分析
"""
import json
import os
import platform
import re
from html.parser import HTMLParser
import time
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

from .models import (
    BASIS_HAMMER,
    BASIS_INCLUDING_PREMIUM,
    BASIS_NET,
    BASIS_UNKNOWN,
    SALE_ASKING,
    SALE_ESTIMATE,
    SALE_SOLD,
    Comparable,
    now_iso,
)

# ---------------------------------------------------------------- Query 生成

_SALE_WORDS = ["sold", "auction result", "price", "sale"]
_QUERY_TEMPLATES = [
    '"{subject}" sold price auction',
    '"{subject}" price realised past auctions',
    '"{subject}" auction result sold lot',
    "{subject} for sale price",
]


def generate_queries(artist: str, artwork: str, year: Optional[str]) -> List[str]:
    base = " ".join(x for x in [artist.strip(), artwork.strip()] if x).strip()
    if not base:
        return []
    queries = [t.format(subject=base) for t in _QUERY_TEMPLATES]
    if year:
        queries.append(f"{year} {base} auction")
    alt = base.replace("N-GT", "NGT").replace("n-gt", "ngt")
    if alt != base:
        queries.append(f"{alt} sold")
    seen, out = set(), []
    for q in queries:
        k = q.lower()
        if k not in seen:
            seen.add(k)
            out.append(q)
    return out[:6]


# ---------------------------------------------------------------- 来源分级

_TIER1_DOMAINS = (
    "christies.", "sothebys.", "phillips.", "artcurial.", "bonhams.",
    "rmsothebys.", "gooding", "bringatrailer.", "porsche.", "maker.",
    "gallery", "galerie", "museum",
)
_TIER3_DOMAINS = (
    "forum", "forums.", "reddit", "facebook", "instagram", "blog", "news",
    "wikipedia", "rennlist", "ferrarichat", "pelicanparts", "6speedonline",
)


def source_tier(url: str) -> int:
    host = urlparse(url or "").netloc.lower()
    if not host:
        return 3
    if any(k in host for k in _TIER1_DOMAINS):
        return 1
    if any(k in host for k in _TIER3_DOMAINS):
        return 3
    return 2


# ---------------------------------------------------------------- Search Provider

class SearchProvider:
    name = "none"
    available = False

    def search(self, query: str, max_results: int = 8) -> List[dict]:
        raise NotImplementedError


class SearchProviderNotConfigured(SearchProvider):
    name = "none"
    available = False
    status = "SEARCH_PROVIDER_NOT_CONFIGURED"
    technical_detail = "Configure Tavily or Brave in SIGNAL Settings."

    def search(self, query: str, max_results: int = 8) -> List[dict]:
        raise RuntimeError(self.status)


class ProductionProviderNotAllowed(RuntimeError):
    pass


def check_searxng_endpoint(endpoint: str) -> str:
    import httpx
    try:
        response = httpx.get(endpoint.rstrip("/") + "/", timeout=2.5)
        response.raise_for_status(); body = response.text[:20000].lower()
        return "READY" if "searxng" in body else "INVALID_RESPONSE"
    except Exception:
        return "UNAVAILABLE"


class _SearXNGHTMLResults(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.rows=[]; self.current=None; self.in_title=False; self.in_content=False
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs); classes=set((attrs.get("class") or "").split())
        if tag == "article" and "result" in classes: self.current={"title":"", "url":"", "content":""}
        elif self.current is not None and tag == "h3": self.in_title=True
        elif self.current is not None and self.in_title and tag == "a" and not self.current["url"]: self.current["url"]=attrs.get("href","")
        elif self.current is not None and tag == "p" and "content" in classes: self.in_content=True
    def handle_endtag(self, tag):
        if tag == "h3": self.in_title=False
        elif tag == "p": self.in_content=False
        elif tag == "article" and self.current is not None:
            if self.current["title"] and self.current["url"]: self.rows.append(self.current)
            self.current=None; self.in_title=False; self.in_content=False
    def handle_data(self, data):
        if self.current is None: return
        if self.in_title: self.current["title"] += data
        elif self.in_content: self.current["content"] += data


def resolve_searxng_endpoint(system: str | None = None) -> dict:
    system = system or platform.system()
    explicit = (os.getenv("SEARXNG_BASE_URL") or "").strip().rstrip("/")
    fallback = (os.getenv("SEARXNG_FALLBACK_URL") or "").strip().rstrip("/")
    candidates = []
    if explicit: candidates.append(explicit)
    if system == "Darwin": candidates.append("http://127.0.0.1:8888")
    if fallback: candidates.append(fallback)
    seen = set(); last_status = "UNAVAILABLE"
    for endpoint in candidates:
        if endpoint in seen: continue
        seen.add(endpoint); last_status = check_searxng_endpoint(endpoint)
        if last_status == "READY": return {"endpoint":endpoint, "status":"READY", "platform":system}
    return {"endpoint":explicit or fallback or None, "status":last_status, "platform":system}


class SearXNGProvider(SearchProvider):
    """LOCAL_TEST_ONLY SearXNG JSON provider. Never requests result URLs."""
    name = "searxng_local"

    def __init__(self, base_url: str | None = None, check_health: bool = True, use_cache: bool = False):
        if base_url:
            os_endpoint = base_url.rstrip("/")
            resolved = {"endpoint":os_endpoint, "status":check_searxng_endpoint(os_endpoint), "platform":platform.system()}
        else:
            resolved = resolve_searxng_endpoint()
        self.base_url = resolved.get("endpoint")
        self.resolved_status = resolved.get("status", "UNAVAILABLE")
        self.environment = resolved.get("platform", platform.system())
        self.available = False; self.status = "SEARCH_PROVIDER_UNAVAILABLE"
        self.technical_detail = "SearXNG health not checked"
        if self.resolved_status == "READY":
            self.available = True; self.status = "OK"; self.technical_detail = ""; return
        if self.resolved_status == "INVALID_RESPONSE":
            self.status = "INVALID_RESPONSE"; self.technical_detail = "JSON results missing"; return
        if not self.base_url or not check_health: return
        cached = _SEARXNG_HEALTH_CACHE.get(self.base_url) if use_cache else None
        if cached and time.monotonic() - cached[0] < 10:
            self.available, self.status, self.technical_detail = cached[1:]
        elif check_health:
            self.health_check()
            if use_cache:
                _SEARXNG_HEALTH_CACHE[self.base_url] = (time.monotonic(), self.available, self.status, self.technical_detail)

    def health_check(self) -> str:
        result = check_searxng_endpoint(self.base_url) if self.base_url else "UNAVAILABLE"
        self.available = result == "READY"
        self.status = "OK" if self.available else result if result == "INVALID_RESPONSE" else "SEARCH_PROVIDER_UNAVAILABLE"
        self.technical_detail = "" if self.available else result
        return result

    def search(self, query: str, max_results: int = 8) -> List[dict]:
        import httpx
        try:
            response = httpx.get(f"{self.base_url}/search",
                                 params={"q":query, "format":"json", "categories":"general"}, timeout=20)
            if response.status_code == 403:
                response = httpx.get(f"{self.base_url}/search", params={"q":query, "categories":"general"}, timeout=20)
                response.raise_for_status(); parser=_SearXNGHTMLResults(); parser.feed(response.text); rows=parser.rows
            else:
                response.raise_for_status(); payload = response.json()
                rows = payload.get("results") if isinstance(payload, dict) else None
                if not isinstance(rows, list):
                    self.status = "INVALID_RESPONSE"; raise RuntimeError(self.status)
            self.available = True; self.status = "OK"
            return [{"title": r.get("title", ""), "url": r.get("url", ""),
                     "snippet": r.get("content", ""), "provider": self.name,
                     "thumbnail": r.get("thumbnail") or r.get("img_src") or "",
                     "provider_metadata": {"engine": r.get("engine"), "mode":"LOCAL_TEST_ONLY"},
                     "query": query, "retrieved_at": now_iso()} for r in rows[:max_results]]
        except RuntimeError: raise
        except Exception as exc:
            self.available = False; self.status = "SEARCH_PROVIDER_UNAVAILABLE"
            self.technical_detail = safe_provider_detail(exc)
            raise RuntimeError(self.status) from exc


class TavilyProvider(SearchProvider):
    name = "tavily"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.available = bool(api_key)
        self.status = "OK" if self.available else "SEARCH_PROVIDER_NOT_CONFIGURED"
        self.technical_detail = ""

    def search(self, query: str, max_results: int = 8) -> List[dict]:
        import httpx
        try:
            resp = httpx.post(
                "https://api.tavily.com/search",
                json={"api_key": self.api_key, "query": query,
                      "max_results": max_results, "search_depth": "basic"},
                timeout=20,
            )
            resp.raise_for_status()
            return [
                {"title": r.get("title", ""), "url": r.get("url", ""),
                 "snippet": r.get("content", ""), "provider": self.name,
                 "provider_metadata": {"score": r.get("score")}, "query": query,
                 "retrieved_at": now_iso()}
                for r in resp.json().get("results", [])
            ]
        except Exception as exc:
            self.status = classify_provider_error(exc); self.technical_detail = safe_provider_detail(exc)
            raise RuntimeError(self.status) from exc


class BraveProvider(SearchProvider):
    name = "brave"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.available = bool(api_key)
        self.status = "OK" if self.available else "SEARCH_PROVIDER_NOT_CONFIGURED"
        self.technical_detail = ""

    def search(self, query: str, max_results: int = 8) -> List[dict]:
        import httpx
        try:
            resp = httpx.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={"X-Subscription-Token": self.api_key},
                params={"q": query, "count": max_results},
                timeout=20,
            )
            resp.raise_for_status()
            return [
                {"title": r.get("title", ""), "url": r.get("url", ""),
                 "snippet": r.get("description", ""), "provider": self.name,
                 "provider_metadata": {"age": r.get("age")}, "query": query,
                 "retrieved_at": now_iso()}
                for r in resp.json().get("web", {}).get("results", [])
            ]
        except Exception as exc:
            self.status = classify_provider_error(exc); self.technical_detail = safe_provider_detail(exc)
            raise RuntimeError(self.status) from exc


SEARCH_CONFIG_PATH = Path(os.getenv("SIGNAL_SEARCH_CONFIG_PATH") or
                          (Path(__file__).resolve().parent.parent / "data" / "search_provider_config.json"))
ALLOWED_SEARCH_PROVIDERS = {"tavily", "brave"}
_SEARXNG_HEALTH_CACHE: dict[str, tuple] = {}


def classify_provider_error(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status in {401, 403}: return "INVALID_API_KEY"
    if status == 402: return "QUOTA_EXCEEDED"
    if status == 429: return "RATE_LIMITED"
    if status is not None and status >= 500: return "SERVICE_UNAVAILABLE"
    text = str(exc).lower()
    if any(x in text for x in ("connect", "network", "dns", "timeout")): return "NETWORK_ERROR"
    return "SERVICE_UNAVAILABLE"


def safe_provider_detail(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return f"HTTP {status}" if status is not None else exc.__class__.__name__


def _load_search_config() -> dict:
    try:
        value = json.loads(SEARCH_CONFIG_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _write_search_config(value: dict) -> None:
    SEARCH_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = SEARCH_CONFIG_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(SEARCH_CONFIG_PATH)


def search_provider_settings() -> dict:
    app_env = (os.getenv("APP_ENV") or "development").strip().lower()
    if app_env in {"development", "dev", "local", "test"} and (os.getenv("DEV_SEARCH_PROVIDER") or "searxng").lower() == "searxng":
        provider = SearXNGProvider(use_cache=True)
        return {"provider":"searxng_local", "configured":provider.available, "key_present":False,
                "mode":"LOCAL_TEST_ONLY", "last_tested_at":None,
                "last_test_status":"OK" if provider.available else "FAILED",
                "last_error_code":None if provider.available else provider.status}
    cfg = _load_search_config()
    selected = str(cfg.get("provider") or os.getenv("SEARCH_PROVIDER") or "").lower()
    if selected not in ALLOWED_SEARCH_PROVIDERS: selected = ""
    tavily_key = str(cfg.get("tavily_api_key") or os.getenv("TAVILY_API_KEY") or "")
    brave_key = str(cfg.get("brave_api_key") or os.getenv("BRAVE_API_KEY") or "")
    key = tavily_key if selected == "tavily" else brave_key if selected == "brave" else ""
    return {
        "provider": selected,
        "configured": bool(selected and key),
        "key_present": bool(key),
        "last_tested_at": cfg.get("last_tested_at"),
        "last_test_status": cfg.get("last_test_status"),
        "last_error_code": cfg.get("last_error_code"),
    }


def save_search_provider(provider: str, api_key: str) -> dict:
    provider = (provider or "").strip().lower()
    if provider not in ALLOWED_SEARCH_PROVIDERS: raise ValueError("INVALID_PROVIDER")
    cfg = _load_search_config()
    if api_key:
        cfg[f"{provider}_api_key"] = api_key.strip()
        cfg.update({"last_tested_at": None, "last_test_status": None, "last_error_code": None})
    existing = cfg.get(f"{provider}_api_key") or os.getenv(f"{provider.upper()}_API_KEY") or ""
    if not existing: raise ValueError("API_KEY_REQUIRED")
    cfg["provider"] = provider
    _write_search_config(cfg)
    return search_provider_settings()


def record_search_provider_test(status: str, error_code: str | None = None) -> dict:
    cfg = _load_search_config()
    cfg.update({"last_tested_at": now_iso(), "last_test_status": status,
                "last_error_code": error_code})
    _write_search_config(cfg)
    return search_provider_settings()


def build_search_provider() -> SearchProvider:
    app_env = (os.getenv("APP_ENV") or "development").strip().lower()
    dev_kind = (os.getenv("DEV_SEARCH_PROVIDER") or "searxng").strip().lower()
    if app_env in {"development", "dev", "local", "test"} and dev_kind == "searxng":
        return SearXNGProvider(use_cache=True)
    if app_env == "production" and dev_kind == "searxng" and not (os.getenv("SEARCH_PROVIDER") or "").strip():
        raise ProductionProviderNotAllowed("Local SearXNG mode is not allowed in production")
    cfg = _load_search_config()
    kind = str(cfg.get("provider") or os.getenv("SEARCH_PROVIDER") or "").lower()
    if kind not in ALLOWED_SEARCH_PROVIDERS:
        return SearchProviderNotConfigured()
    key = str(cfg.get(f"{kind}_api_key") or os.getenv(f"{kind.upper()}_API_KEY") or "")
    if kind == "tavily" and key:
        provider = TavilyProvider(key)
        if cfg.get("last_test_status") == "FAILED":
            provider.available = False; provider.status = cfg.get("last_error_code") or "SERVICE_UNAVAILABLE"
        return provider
    if kind == "brave" and key:
        provider = BraveProvider(key)
        if cfg.get("last_test_status") == "FAILED":
            provider.available = False; provider.status = cfg.get("last_error_code") or "SERVICE_UNAVAILABLE"
        return provider
    return SearchProviderNotConfigured()


# ---------------------------------------------------------------- 抓取与抽取

_MONEY_RE = re.compile(
    r"(?:(€|£|\$)\s?([\d][\d.,\s]{2,14}\d)"
    r"|([\d][\d.,\s]{2,14}\d)\s?(EUR|GBP|USD|CHF|€|£|\$)"
    r"|(EUR|GBP|USD|CHF)\s?([\d][\d.,\s]{2,14}\d))",
    re.I,
)

_SOLD_WORDS = ("sold for", "sold at", "realized", "hammer price", "result", "成交")
_ASKING_WORDS = ("asking", "for sale", "offer", "price:", "listed", "挂牌", "要价")
_ESTIMATE_WORDS = ("estimate", "est.", "estimated", "schätzung", "估价")


def _currency_of(sym_or_code: str) -> str:
    s = (sym_or_code or "").strip().upper()
    return {"€": "EUR", "£": "GBP", "$": "USD", "EUR": "EUR", "GBP": "GBP",
            "USD": "USD", "CHF": "CHF"}.get(s, "EUR")


def _parse_amount(raw: str) -> Optional[float]:
    num = re.sub(r"[^\d]", "", raw)
    if not num or len(num) > 12:
        return None
    v = float(num)
    if 1900 <= v <= 2099 and re.fullmatch(r"(19|20)\d{2}", num):
        return None
    return v if v >= 100 else None


_CLASSIC_AUCTIONEERS = ("christies", "sothebys", "phillips", "artcurial", "bonhams")


def _infer_basis(url: str, ctx: str, sale_type: str, is_auction: bool) -> str:
    if "hammer" in ctx:
        return BASIS_HAMMER
    if any(w in ctx for w in ("premium", "incl", "including", "含佣金")):
        return BASIS_INCLUDING_PREMIUM
    if any(w in ctx for w in ("net", "private", "私洽")):
        return BASIS_NET
    host = urlparse(url or "").netloc.lower()
    if sale_type == SALE_SOLD and is_auction and any(k in host for k in _CLASSIC_AUCTIONEERS):
        return BASIS_INCLUDING_PREMIUM
    return BASIS_UNKNOWN


def extract_comparables(
    url: str, title: str, page_text: Optional[str], snippet: str = ""
) -> List[Comparable]:
    """确定性抽取：从页面文本找「金额 + 附近上下文判定成交性质」。"""
    if not url:
        return []
    text = page_text or snippet
    if not text:
        return []
    out: List[Comparable] = []
    seen_prices = set()

    for m in _MONEY_RE.finditer(text):
        sym_cur = m.group(1) or m.group(4) or m.group(5)
        raw_num = m.group(2) or m.group(3) or m.group(6)
        price = _parse_amount(raw_num or "")
        if price is None:
            continue
        cur = _currency_of(sym_cur)
        key = (round(price), cur)
        if key in seen_prices:
            continue
        seen_prices.add(key)

        start = max(0, m.start() - 160)
        ctx = text[start: m.end() + 160].lower()

        sale_type = SALE_ASKING if any(w in ctx for w in _ASKING_WORDS) else SALE_SOLD
        if any(w in ctx for w in _ESTIMATE_WORDS) and "sold" not in ctx:
            sale_type = SALE_ESTIMATE
        if any(w in ctx for w in _SOLD_WORDS):
            sale_type = SALE_SOLD

        is_auction = any(k in ctx for k in ("auction", "sale", "lot", "hammer"))
        basis = _infer_basis(url, ctx, sale_type, is_auction)

        year_m = re.search(r"\b(19[5-9]\d|20[0-2]\d)\b", text[start:m.end() + 60])
        excerpt = text[start: m.end() + 90].strip()

        out.append(Comparable(
            title=(title or url)[:120],
            year=year_m.group(1) if year_m else None,
            price=price,
            currency=cur,
            sold_at=urlparse(url).netloc,
            is_auction=is_auction,
            source_name=urlparse(url).netloc,
            source_url=url,
            sale_type=sale_type,
            price_basis=basis,
            source_tier=source_tier(url),
            evidence_excerpt=excerpt[:200],
            retrieved_at=now_iso(),
            attributes={"origin": "search_result", "evidence_level": "SEARCH_RESULT"},
        ))
        if len(out) >= 4:
            break
    return out


# ---------------------------------------------------------------- 正文关键词裁剪

_CONTEXT_KEYWORDS = [
    "sold", "price", "hammer", "premium", "realized", "winning bid", "bid",
    "asking", "estimate", "auction", "lot ", "mileage", "km", "n-gt", "ngt",
    "n/gt", "m003", "option 003", "competition", "matching numbers", "original",
    "provenance", "accident", "restored", "color", "euro", "usd", "gbp",
    "成交", "落槌", "估价", "含佣金",
]


def extract_relevant_context(
    text: Optional[str],
    window: int = 750,
    max_chars: int = 3000,
    min_chars: int = 1500,
) -> str:
    if not text:
        return ""
    low = text.lower()
    hits: List[int] = []
    for kw in _CONTEXT_KEYWORDS:
        k = kw.lower()
        start = 0
        while True:
            i = low.find(k, start)
            if i < 0:
                break
            hits.append(i)
            start = i + max(1, len(k))
    if not hits:
        return text[:max_chars]

    spans: List[tuple] = []
    for i in sorted(set(hits)):
        s, e = max(0, i - window), min(len(text), i + window)
        if spans and s <= spans[-1][1]:
            spans[-1] = (spans[-1][0], e)
        else:
            spans.append((s, e))

    def _has_money(seg: str) -> bool:
        return bool(_MONEY_RE.search(seg))

    money_spans = [sp for sp in spans if _has_money(text[sp[0]:sp[1]])]
    other_spans = [sp for sp in spans if not _has_money(text[sp[0]:sp[1]])]

    out = ""
    for sp in money_spans + other_spans:
        seg = text[sp[0]:sp[1]]
        if len(out) + len(seg) + 4 <= max_chars:
            out += seg + "\n…\n"
        else:
            remain = max_chars - len(out)
            if remain > 300:
                out += seg[:remain]
            break
    out = out.strip()
    if len(out) < min_chars and len(text) > min_chars:
        out = (text[:min_chars] + "\n…\n" + out)[:max_chars]
    return out[:max_chars]


# ---------------------------------------------------------------- Research 主流程

def run_research(inp, provider: SearchProvider, max_queries: int = 6, on_step=None,
                 queries: Optional[List[str]] = None) -> dict:
    """执行搜索漏斗。返回 {"records": [...], "summary": ResearchSummary, "pages": [...]}。"""
    from .models import ResearchSummary

    def step(msg: str):
        if on_step:
            try:
                on_step(msg)
            except Exception:
                pass

    summary = ResearchSummary(enabled=True, provider=provider.name)
    if queries is None:
        queries = generate_queries(inp.artist, inp.artwork, inp.year)[:max_queries]
    summary.queries = queries
    step(f"Generating search queries（{len(queries)} 组）")

    records: List[Comparable] = []
    pages: List[dict] = []
    errors: List[str] = []
    seen_urls = set()

    if not provider.available:
        status = getattr(provider, "status", "SEARCH_PROVIDER_NOT_CONFIGURED")
        summary.errors.append(status)
        step("Search provider not configured" if status == "SEARCH_PROVIDER_NOT_CONFIGURED" else "Search provider unavailable")
        return {"records": [], "summary": summary, "pages": [], "status": status}

    for q in queries:
        try:
            step(f"Searching: {q}")
            results = provider.search(q, max_results=5)
        except Exception as exc:
            errors.append(getattr(provider, "status", "SEARCH_PROVIDER_UNAVAILABLE"))
            continue
        summary.queries_executed += 1

        for r in results:
            url = (r.get("url") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            summary.candidates += 1
            document = {"url": url, "title": r.get("title") or "", "text": r.get("snippet") or "",
                        "snippet": r.get("snippet") or "", "provider": r.get("provider") or provider.name,
                        "provider_metadata": r.get("provider_metadata") or {}, "query": r.get("query") or q,
                        "retrieved_at": r.get("retrieved_at") or now_iso(), "evidence_level": "SEARCH_RESULT"}
            pages.append(document)
            recs = extract_comparables(url, document["title"], None, document["snippet"])
            records.extend(recs)
        step(f"Candidate records so far: {len(records)}")

    step(f"Verifying sources（候选 {summary.candidates} 个 URL）")
    summary.errors = errors
    status = "OK" if summary.queries_executed else getattr(provider, "status", "SEARCH_PROVIDER_UNAVAILABLE")
    return {"records": records, "summary": summary, "pages": pages, "status": status}