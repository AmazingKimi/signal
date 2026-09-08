"""LLM Provider —— 可插拔，OpenAI 兼容协议（0.3 接入 DeepSeek）。

职责边界（严格执行）：
- LLM 只做「理解、抽取、分类、解释」：识别对象、读网页正文、判断可比性并给理由
- LLM 不产出任何价格判断数字——Fair Value / Opening Offer / Walk-away / Target
  全部由 orchestrator 的确定性 Pricing Engine 计算
- LLM confidence ≠ VERIFIED：VERIFIED 仍取决于原始来源 URL

配置（.env）：
    LLM_PROVIDER=deepseek          # deepseek / openai / 其他（用 LLM_BASE_URL）
    LLM_API_KEY=xxx                # 只存在 .env，绝不写源码、绝不进前端
    LLM_MODEL=deepseek-chat        # 可省略，deepseek 默认 deepseek-chat

失败策略：
- 未配置 Key -> configured=False，全离线（自动降级 0.2 规则模式）
- 超时 / 429 / 5xx -> 指数退避重试 2 次，仍失败则记录日志、返回 None
- 单页解析失败不影响其他页面
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import time
from typing import Dict, List, Optional

from .models import AnalysisInput

logger = logging.getLogger("kimi.llm")

SYSTEM_PROMPT = """你是收藏品投资研究助理。你的职责是研究，不是定价。

硬性规则：
1. 你返回的每一条市场事实（成交价、展览、馆藏、报价）都必须带 source_url。
   给不出真实 URL 的，source_url 必须填 null——绝不许编造链接或编造数字。
2. 你不输出任何"合理价格"判断（Fair Value / Opening / Walk-away / Target）。
   价格区间由系统确定性算法计算。
3. 找不到的数据就承认找不到，不许为了报告完整而补数字。

返回严格 JSON（不要 markdown 代码块，不要多余文字）：
{
  "claims": [
    {"claim": "事实描述", "value": "取值", "source_url": "https://... 或 null",
     "source_name": "来源名", "evidence": "一句话依据"}
  ],
  "comparables": [
    {"title": "作品名+系列+编号", "artist": "Takis", "year": "1968",
     "price": 14432.0, "currency": "EUR", "sold_at": "Artcurial Paris, 2023-11",
     "is_auction": true, "source_url": "https://...", "source_name": "Artcurial"}
  ],
  "artist_analysis": "艺术家市场地位/重要展览/机构收藏/画廊体系的简述（中文，200字内）",
  "risks": ["风险1", "风险2"]
}"""

_PROVIDER_DEFAULTS = {
    "deepseek": {"base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
}

# DeepSeek 官方计价（每百万 token，人民币；仅用于成本估算展示）
_COST_INPUT_PER_M = 2.0
_COST_OUTPUT_PER_M = 8.0

READ_PAGES_SYSTEM = """你是收藏品市场研究助手（艺术品 / 经典车 / 设计品 / 收藏品通用）。
你的职责是【读懂证据】，不是定价。

任务：阅读给定的网页正文，抽取其中与【目标对象】相关的市场记录。

硬性规则：
1. 只输出网页里真实存在的信息。正文没有的内容，字段填 null / UNKNOWN，绝不编造。
2. 价格口径必须给出判断依据（price_basis_reason 引用原文片段）。
3. 网页确认不了口径 -> price_basis = "UNKNOWN"（绝不能猜）。
4. 你不得输出 Fair Value / Recommended Opening Offer / Walk-away / Target Price
   中的任何一个——价格判断由系统算法完成，你无权定价。
5. evidence_excerpt 必须是页面原文的英文/原文字符串（支持判断的原句），不能自己改写。
6. 普通 964 Carrera RS 与 964 RS N-GT / Competition / M003 是不同版本：
   普通 RS 只能 comparability="LOW"，reason 里写明差异。
7. 如果页面与目标对象无关或全是挂牌信息，records 返回空数组。
8. 每条记录必须给 llm_confidence：0-1 的浮点数，表示你对"这条抽取和判断"的把握
   （0.9 以上=页面写得非常明确；0.5=部分推断；低把握就写 0.5 以下，别硬撑）。

返回严格 JSON（不要 markdown 代码块，不要多余文字）：
{
  "records": [
    {
      "title": "记录标题（车型/作品名）",
      "sale_type": "SOLD | ASKING | ESTIMATE | UNKNOWN",
      "price": 310000,
      "currency": "USD",
      "price_basis": "HAMMER | INCLUDING_PREMIUM | NET | UNKNOWN",
      "price_basis_reason": "依据正文哪句话判断，引原文",
      "sale_date": "2024-03-15 或 null",
      "year": "1992 或 null",
      "llm_confidence": 0.9,
      "attributes": {
        "mileage": "9,000 km 或 null",
        "color": "Maritime Blue 或 null",
        "matching_numbers": true/false/null,
        "provenance": "来源链描述或 null",
        "originality_restoration": "原厂程度/修复描述或 null",
        "accident": "事故史描述或 null"
      },
      "comparability": "HIGH | MEDIUM | LOW | NOT_COMPARABLE",
      "comparability_reason": "为什么像/为什么不像，必须具体",
      "evidence_excerpt": "支持上述判断的页面原文"
    }
  ]
}"""


class LLMUsage:
    """一次分析会话的 LLM 调用成本统计。"""
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
    """OpenAI 兼容 /chat/completions。未配置 Key 时 configured=False，全离线。"""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
    ):
        provider = (provider or os.getenv("LLM_PROVIDER") or "deepseek").strip().lower()
        defaults = _PROVIDER_DEFAULTS.get(provider, {})
        self.provider = provider or (defaults.get("name") or "custom")
        self.base_url = (
            (base_url or os.getenv("LLM_BASE_URL") or defaults.get("base_url") or "").rstrip("/")
        )
        self.api_key = api_key or os.getenv("LLM_API_KEY") or ""
        self.model = model or os.getenv("LLM_MODEL") or defaults.get("model") or ""
        self.configured = bool(self.api_key and self.model and self.base_url)
        self.usage = LLMUsage()

    # ---------------------------------------------------------------- 底层调用

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

        payload = {
            "model": self.model,
            "temperature": temperature,
            "messages": messages,
        }
        for attempt in range(max_retries + 1):
            try:
                resp = httpx.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                    timeout=timeout,
                )
                if resp.status_code == 429 or resp.status_code >= 500:
                    wait = 2 ** attempt
                    logger.warning(
                        "LLM %s 返回 %s（第 %d 次重试，等待 %ds）",
                        self.model, resp.status_code, attempt + 1, wait,
                    )
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                usage = data.get("usage") or {}
                self.usage.add(
                    int(usage.get("prompt_tokens") or 0),
                    int(usage.get("completion_tokens") or 0),
                )
                content = data["choices"][0]["message"]["content"]
                parsed = self._parse_json(content)
                if parsed is None:
                    logger.warning("LLM 返回内容无法解析为 JSON（第 %d 次尝试）", attempt + 1)
                return parsed
            except Exception as exc:
                logger.warning("LLM 调用失败（第 %d 次）：%s", attempt + 1, exc)
                if attempt < max_retries:
                    time.sleep(2 ** attempt)
                    continue
                return None
        return None

    # ---------------------------------------------------------------- 页面精读（0.3.1 并发版）

    def read_pages(
        self,
        inp: AnalysisInput,
        pages: List[dict],
        on_page_done=None,
        time_budget: Optional[float] = None,
        max_concurrency: int = 4,
    ) -> List[dict]:
        """LLM 精读候选页面（并发 + 限流 + 缓存 + 时间预算）。"""
        if not self.configured or not pages:
            return []

        target_desc = (
            f"目标对象：\n"
            f"- Maker/Artist: {inp.artist}\n"
            f"- Model/Work: {inp.artwork or '（未填）'}\n"
            f"- Year: {inp.year or '未知'}\n"
            f"- Category: {inp.category}\n"
            f"- Mileage: {inp.mileage or '未知'}\n"
            f"- Color: {inp.color or '未知'}\n"
            f"- Options: {inp.options or '未知'}\n"
            f"- Seller: {inp.seller or '未知'}\n"
            f"- 当前报价: {inp.asking_price} {inp.currency}"
        )

        async def _run() -> List[dict]:
            import httpx

            sem = asyncio.Semaphore(max_concurrency)
            deadline = time.monotonic() + time_budget if time_budget else None
            done_count = 0
            lock = asyncio.Lock()

            async def _one(page: dict) -> List[dict]:
                nonlocal done_count
                async with sem:
                    url = page.get("url", "")
                    text = (page.get("text") or "")[:20000]
                    if not text:
                        await _mark_done()
                        return []
                    from .research import extract_relevant_context
                    cropped = extract_relevant_context(text)
                    text_hash = hashlib.sha256(cropped.encode("utf-8")).hexdigest()[:16]

                    from .store import llm_cache_get
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
                    from .store import llm_cache_put
                    llm_cache_put(self.provider, self.model, url, text_hash, parsed)
                    return self._cached_to_records(parsed, url)

            async def _mark_done():
                nonlocal done_count
                async with lock:
                    done_count += 1
                    if on_page_done:
                        try:
                            on_page_done(done_count, len(pages))
                        except Exception:
                            pass

            results: List[dict] = []
            pending = [asyncio.create_task(_one(p)) for p in pages]
            try:
                for fut in asyncio.as_completed(pending):
                    if deadline and time.monotonic() > deadline:
                        for f in pending:
                            f.cancel()
                        break
                    try:
                        results.extend(await fut)
                    except asyncio.CancelledError:
                        break
                    except Exception as exc:
                        logger.warning("页面精读失败（降级）: %s", exc)
            finally:
                for f in pending:
                    if not f.done():
                        f.cancel()
            return results

        try:
            return asyncio.run(_run())
        except Exception as exc:
            logger.warning("LLM 精读并发层异常: %s", exc)
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
        payload = {"model": self.model, "temperature": temperature, "messages": messages}
        for attempt in range(max_retries + 1):
            try:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                )
                if resp.status_code == 429 or resp.status_code >= 500:
                    wait = 1.5 if attempt == 0 else 3.0
                    logger.warning("LLM %s 返回 %s（重试 %d/%d）", self.model, resp.status_code, attempt + 1, max_retries + 1)
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                usage = data.get("usage") or {}
                self.usage.add(
                    int(usage.get("prompt_tokens") or 0),
                    int(usage.get("completion_tokens") or 0),
                )
                content = data["choices"][0]["message"]["content"]
                parsed = self._parse_json(content)
                if parsed is None:
                    logger.warning("LLM 返回内容无法解析为 JSON")
                return parsed
            except Exception as exc:
                logger.warning("LLM 异步调用失败（%d/%d）：%s", attempt + 1, max_retries + 1, exc)
                if attempt < max_retries:
                    await asyncio.sleep(1.5)
                    continue
                return None
        return None

    # ---------------------------------------------------------------- 艺术家研究

    def research(self, inp: AnalysisInput) -> Optional[dict]:
        """泛研究（艺术家/制造商背景）。0.3.1：按输入 hash 缓存——同对象重跑不再重复花钱。"""
        if not self.configured:
            return None

        cache_key = (
            f"{inp.artist}|{inp.artwork}|{inp.year}|{inp.category}|"
            f"{inp.asking_price}|{inp.currency}"
        )
        cache_hash = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()[:16]
        from .store import llm_cache_get, llm_cache_put
        cached = llm_cache_get(
            self.provider, self.model, f"research::{cache_hash}", "gen"
        )
        if cached is not None:
            return cached

        user_prompt = f"""研究这个购买机会（只返回 JSON）：
{inp.artist} / {inp.artwork} / {inp.year or '未知'} / {inp.category}
当前报价：{inp.asking_price} {inp.currency}
备注：{inp.notes or '无'}

请研究：(1) 艺术家/制造商市场地位 (2) 同系列/同类型可比成交（必须带来源）
(3) 其他关键市场事实。"""
        result = self.chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]
        )
        if result is not None:
            llm_cache_put(
                self.provider, self.model, f"research::{cache_hash}", "gen", result
            )
        return result

    # ---------------------------------------------------------------- 工具

    @staticmethod
    def _parse_json(content: str) -> Optional[dict]:
        content = (content or "").strip()
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.S)
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", content, flags=re.S)
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    return None
            return None


def _host_of(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url or "").netloc or ""


def _extract_confidence(rec: dict) -> Optional[float]:
    c = rec.get("llm_confidence")
    try:
        v = float(c)
        return round(min(max(v, 0.0), 1.0), 2)
    except (TypeError, ValueError):
        return None


def build_provider() -> LLMProvider:
    return LLMProvider()