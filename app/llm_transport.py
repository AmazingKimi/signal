"""Provider-specific LLM HTTP transport for SIGNAL BYOK.

No provider key is bundled with SIGNAL. Callers pass keys from the local
process environment only. This module normalizes responses into the existing
OpenAI-like ``(text, usage)`` shape consumed by ``app.llm``.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote


PROVIDER_DEFAULTS = {
    "deepseek": {"base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    "anthropic": {"base_url": "https://api.anthropic.com/v1", "model": "claude-sonnet-4-20250514"},
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta", "model": "gemini-2.5-flash"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "model": "openai/gpt-4o-mini"},
    "ollama": {"base_url": "http://127.0.0.1:11434/v1", "model": "llama3.2"},
    "custom": {"base_url": "", "model": ""},
}

ALIASES = {
    "claude": "anthropic",
    "google": "gemini",
    "google-gemini": "gemini",
    "open-router": "openrouter",
    "local": "ollama",
}


def normalize_provider(value: str | None) -> str:
    provider = (value or "deepseek").strip().lower()
    return ALIASES.get(provider, provider)


def provider_requires_key(provider: str) -> bool:
    return normalize_provider(provider) != "ollama"


def _openai_payload(model: str, messages: list[dict], temperature: float) -> dict:
    return {"model": model, "temperature": temperature, "messages": messages}


def _anthropic_payload(model: str, messages: list[dict], temperature: float) -> dict:
    system_parts = [m.get("content", "") for m in messages if m.get("role") == "system"]
    conversation = [
        {"role": "assistant" if m.get("role") == "assistant" else "user", "content": m.get("content", "")}
        for m in messages
        if m.get("role") != "system"
    ]
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": 4096,
        "temperature": temperature,
        "messages": conversation,
    }
    if system_parts:
        payload["system"] = "\n\n".join(system_parts)
    return payload


def _gemini_payload(messages: list[dict], temperature: float) -> dict:
    system_parts = [m.get("content", "") for m in messages if m.get("role") == "system"]
    contents = []
    for message in messages:
        if message.get("role") == "system":
            continue
        role = "model" if message.get("role") == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": message.get("content", "")} ]})
    payload: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {"temperature": temperature},
    }
    if system_parts:
        payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
    return payload


def request_spec(provider: str, base_url: str, model: str, api_key: str, messages: list[dict], temperature: float):
    provider = normalize_provider(provider)
    base = (base_url or "").rstrip("/")
    if provider == "anthropic":
        return (
            f"{base}/messages",
            {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            _anthropic_payload(model, messages, temperature),
        )
    if provider == "gemini":
        return (
            f"{base}/models/{quote(model, safe='')}:generateContent?key={quote(api_key, safe='')}",
            {"content-type": "application/json"},
            _gemini_payload(messages, temperature),
        )
    headers = {"content-type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if provider == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/AmazingKimi/signal"
        headers["X-Title"] = "SIGNAL"
    return f"{base}/chat/completions", headers, _openai_payload(model, messages, temperature)


def parse_response(provider: str, data: dict) -> tuple[str, dict]:
    provider = normalize_provider(provider)
    if provider == "anthropic":
        blocks = data.get("content") or []
        text = "".join(block.get("text", "") for block in blocks if isinstance(block, dict) and block.get("type") == "text")
        usage = data.get("usage") or {}
        return text, {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
        }
    if provider == "gemini":
        candidates = data.get("candidates") or []
        parts = (((candidates[0] if candidates else {}).get("content") or {}).get("parts") or [])
        text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
        usage = data.get("usageMetadata") or {}
        return text, {
            "prompt_tokens": usage.get("promptTokenCount", 0),
            "completion_tokens": usage.get("candidatesTokenCount", 0),
        }
    usage = data.get("usage") or {}
    choices = data.get("choices") or []
    text = (((choices[0] if choices else {}).get("message") or {}).get("content") or "")
    return text, {
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
    }


def sync_request(httpx_module, provider: str, base_url: str, model: str, api_key: str,
                 messages: list[dict], temperature: float, timeout: float):
    url, headers, payload = request_spec(provider, base_url, model, api_key, messages, temperature)
    response = httpx_module.post(url, headers=headers, json=payload, timeout=timeout)
    return response


async def async_request(client, provider: str, base_url: str, model: str, api_key: str,
                        messages: list[dict], temperature: float):
    url, headers, payload = request_spec(provider, base_url, model, api_key, messages, temperature)
    return await client.post(url, headers=headers, json=payload)
