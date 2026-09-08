"""SIGNAL LLM layer with local BYOK provider selection.

LLMs only understand, extract, classify and explain evidence. Pricing remains
fully deterministic elsewhere in SIGNAL. No API key is bundled with the repo;
keys come from the user's local environment only.
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import time
from typing import List, Optional

from .llm_transport import (
    PROVIDER_DEFAULTS,
    async_request,
    normalize_provider,
    parse_response,
    provider_requires_key,
    sync_request,
)
from .models import AnalysisInput

logger = logging.getLogger("kimi.llm")

SYSTEM_PROMPT = """你是收藏品投资研究助理。你的职责是研究，不是定价。
硬性规则：
1. 每条市场事实必须带 source_url；没有真实 URL 就填 null，绝不编造。
2. 不输出 Fair Value / Opening / Walk-away / Target Price 等价格判断。
3. 找不到的数据就承认找不到，不为了完整而补数字。
返回严格 JSON：
{
  "claims": [{"claim":"事实描述","value":"取值","source_url":"https://... 或 null","source_name":"来源名","evidence":"一句话依据"}],
  "comparables": [{"title":"作品名+系列+编号","artist":"Takis","year":"1968","price":14432.0,"currency":"EUR","sold_at":"Artcurial Paris, 2023-11","is_auction":true,"source_url":"https://...","source_name":"Artcurial"}],
  "artist_analysis":"市场地位/重要展览/机构收藏/画廊体系简述",
  "risks":["风险1","风险2"]
}"""

READ_PAGES_SYSTEM = """你是收藏品市场研究助手（艺术品 / 经典车 / 设计品 / 收藏品通用）。
你的职责是读懂证据，不是定价。只输出网页真实存在的信息；不确定就填 null / UNKNOWN。
价格口径必须给出原文依据；不得输出 Fair Value / Opening / Walk-away / Target Price。
evidence_excerpt 必须是页面原文。返回严格 JSON，顶层字段为 records。"""

# Legacy display-only estimate retained for backward compatibility with existing reports.
_COST_INPUT_PER_M = 2.0
_COST_OUTPUT_PER_M = 8.0


class LLMUsage:
    def __init__(self) -> None:
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.estimated_cost_rmb = 0.0

    def add(self, prompt: int, completion: int) -> None:
        self.calls += 1
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.total_tokens += prompt + completion
        self.estimated_cost_rmb += (
            prompt * _COST_INPUT_PER_M + completion * _COST_OUTPUT_PER_M
        ) / 1_000_000

    def as_dict(self) -> dict:
        return {
            "llm_calls": self.calls,
            "llm_prompt_tokens": self.prompt_tokens,
            "llm_completion_tokens": self.completion_tokens,
            "llm_total_tokens": self.total_tokens,
            "llm_estimated_cost_rmb": round(self.estimated_cost_rmb, 4),
        }


class LLMProvider:
    """BYOK provider facade.

    Supported provider values:
    deepseek, openai, anthropic (or claude), gemini (or google), openrouter,
    ollama, and custom OpenAI-compatible endpoints.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
    ):
        self.provider = normalize_provider(provider or os.getenv("LLM_PROVIDER") or "deepseek")
        defaults = PROVIDER_DEFAULTS.get(self.provider, PROVIDER_DEFAULTS["custom"])
        self.base_url = (base_url or os.getenv("LLM_BASE_URL") or defaults.get("base_url") or "").rstrip("/")
        self.api_key = api_key or os.getenv("LLM_API_KEY") or ""
        self.model = model or os.getenv("LLM_MODEL") or defaults.get("model") or ""
        key_ok = bool(self.api_key) or not provider_requires_key(self.provider)
        self.configured = bool(self.model and self.base_url and key_ok)
        self.usage = LLMUsage()

    def _record_usage(self, usage: dict) -> None:
        self.usage.add(int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0))

    def chat(
        self,
        messages: List[dict],
        temperature: float = 0.2,
        timeout: float = 90.0,
        max_retries: int = 2,
    ) -> Optional[dict]:
        if not self.configured:
            return None
        import httpx

        for attempt in range(max_retries + 1):
            try:
                resp = sync_request(
                    httpx, self.provider, self.base_url, self.model, self.api_key,
                    messages, temperature, timeout,
                )
                if resp.status_code == 429 or resp.status_code >= 500:
                    if attempt < max_retries:
                        time.sleep(2 ** attempt)
                        continue
                resp.raise_for_status()
                content, usage = parse_response(self.provider, resp.json())
                self._record_usage(usage)
                parsed = self._parse_json(content)
                if parsed is None:
                    logger.warning("LLM response was not valid JSON: provider=%s model=%s", self.provider, self.model)
                return parsed
            except Exception as exc:
                logger.warning("LLM call failed (%d/%d): %s", attempt + 1, max_retries + 1, exc)
                if attempt < max_retries:
                    time.sleep(2 ** attempt)
                    continue
                return None
        return None

    def read_pages(
        self,
        inp: AnalysisInput,
        pages: List[dict],
        on_page_done=None,
        time_budget: Optional[float] = None,
        max_concurrency: int = 4,
    ) -> List[dict]:
        if not self.configured or not pages:
            return []

        target_desc = (
            f"目标对象：\n- Maker/Artist: {inp.artist}\n- Model/Work: {inp.artwork or '（未填）'}\n"
            f"- Year: {inp.year or '未知'}\n- Category: {inp.category}\n"
            f"- Mileage: {inp.mileage or '未知'}\n- Color: {inp.color or '未知'}\n"
            f"- Options: {inp.options or '未知'}\n- Seller: {inp.seller or '未知'}\n"
            f"- 当前报价: {inp.asking_price} {inp.currency}"
        )

        async def _run() -> List[dict]:
            import httpx
            sem = asyncio.Semaphore(max_concurrency)
            deadline = time.monotonic() + time_budget if time_budget else None
            done_count = 0
            lock = asyncio.Lock()

            async def _mark_done():
                nonlocal done_count
                async with lock:
                    done_count += 1
                    if on_page_done:
                        try:
                            on_page_done(done_count, len(pages))
                        except Exception:
                            pass

            async def _one(page: dict) -> List[dict]:
                async with sem:
                    url = page.get("url", "")
                    text = (page.get("text") or "")[:20000]
                    if not text:
                        await _mark_done()
                        return []
                    from .research import extract_relevant_context
                    cropped = extract_relevant_context(text)
                    text_hash = hashlib.sha256(cropped.encode("utf-8")).hexdigest()[:16]
                    from .store import llm_cache_get, llm_cache_put
                    cached = llm_cache_get(self.provider, self.model, url, text_hash)
                    if cached is not None:
                        await _mark_done()
                        return self._cached_to_records(cached, url)
                    user_prompt = (
                        f"{target_desc}\n\n网页 URL：{url}\n网页标题：{page.get('title') or ''}\n\n"
                        f"网页正文（关键词裁剪版）：\n{cropped}\n\n请按规则抽取记录（严格 JSON）。"
                    )
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        parsed = await self._chat_async(
                            client,
                            [
                                {"role": "system", "content": READ_PAGES_SYSTEM},
                                {"role": "user", "content": user_prompt},
                            ],
                        )
                    await _mark_done()
                    if not parsed:
                        return []
                    llm_cache_put(self.provider, self.model, url, text_hash, parsed)
                    return self._cached_to_records(parsed, url)

            pending = [asyncio.create_task(_one(p)) for p in pages]
            results: List[dict] = []
            try:
                for fut in asyncio.as_completed(pending):
                    if deadline and time.monotonic() > deadline:
                        for task in pending:
                            task.cancel()
                        break
                    try:
                        results.extend(await fut)
                    except asyncio.CancelledError:
                        break
                    except Exception as exc:
                        logger.warning("LLM page extraction failed: %s", exc)
            finally:
                for task in pending:
                    if not task.done():
                        task.cancel()
            return results

        try:
            return asyncio.run(_run())
        except Exception as exc:
            logger.warning("LLM async layer failed: %s", exc)
            return []

    def _cached_to_records(self, parsed: dict, url: str) -> List[dict]:
        records: List[dict] = []
        for rec in parsed.get("records") or []:
            if not isinstance(rec, dict) or not rec.get("title"):
                continue
            rec["source_url"] = url
            rec["source_name"] = _host_of(url)
            rec["llm_extracted"] = True
            rec["llm_provider"] = self.provider
            rec["llm_model"] = self.model
            rec["llm_confidence"] = _extract_confidence(rec)
            records.append(rec)
        return records

    async def _chat_async(
        self,
        client,
        messages: List[dict],
        temperature: float = 0.1,
        max_retries: int = 1,
    ) -> Optional[dict]:
        for attempt in range(max_retries + 1):
            try:
                resp = await async_request(
                    client, self.provider, self.base_url, self.model, self.api_key,
                    messages, temperature,
                )
                if resp.status_code == 429 or resp.status_code >= 500:
                    if attempt < max_retries:
                        await asyncio.sleep(1.5 if attempt == 0 else 3.0)
                        continue
                resp.raise_for_status()
                content, usage = parse_response(self.provider, resp.json())
                self._record_usage(usage)
                return self._parse_json(content)
            except Exception as exc:
                logger.warning("LLM async call failed (%d/%d): %s", attempt + 1, max_retries + 1, exc)
                if attempt < max_retries:
                    await asyncio.sleep(1.5)
                    continue
                return None
        return None

    def research(self, inp: AnalysisInput) -> Optional[dict]:
        if not self.configured:
            return None
        cache_key = f"{inp.artist}|{inp.artwork}|{inp.year}|{inp.category}|{inp.asking_price}|{inp.currency}"
        cache_hash = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()[:16]
        from .store import llm_cache_get, llm_cache_put
        cached = llm_cache_get(self.provider, self.model, f"research::{cache_hash}", "gen")
        if cached is not None:
            return cached
        user_prompt = f"""研究这个购买机会（只返回 JSON）：
{inp.artist} / {inp.artwork} / {inp.year or '未知'} / {inp.category}
当前报价：{inp.asking_price} {inp.currency}
备注：{inp.notes or '无'}
请研究：(1) 艺术家/制造商市场地位 (2) 同系列/同类型可比成交（必须带来源）(3) 其他关键市场事实。"""
        result = self.chat([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ])
        if result is not None:
            llm_cache_put(self.provider, self.model, f"research::{cache_hash}", "gen", result)
        return result

    @staticmethod
    def _parse_json(content: str) -> Optional[dict]:
        content = (content or "").strip()
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.S)
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", content, flags=re.S)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    return None
            return None


def _host_of(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url or "").netloc or ""


def _extract_confidence(rec: dict) -> Optional[float]:
    try:
        value = float(rec.get("llm_confidence"))
        return round(min(max(value, 0.0), 1.0), 2)
    except (TypeError, ValueError):
        return None


def build_provider() -> LLMProvider:
    return LLMProvider()
