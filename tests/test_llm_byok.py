from app.llm import LLMProvider
from app.llm_transport import normalize_provider, parse_response, request_spec


def test_provider_aliases_and_defaults(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "claude")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    p = LLMProvider()
    assert p.provider == "anthropic"
    assert p.configured is True
    assert p.base_url == "https://api.anthropic.com/v1"


def test_ollama_requires_no_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    p = LLMProvider()
    assert p.configured is True
    assert p.base_url.startswith("http://127.0.0.1:11434")


def test_anthropic_request_is_native_messages_api():
    url, headers, payload = request_spec(
        "anthropic", "https://api.anthropic.com/v1", "claude-test", "secret",
        [{"role": "system", "content": "rules"}, {"role": "user", "content": "hello"}], 0.2,
    )
    assert url.endswith("/messages")
    assert headers["x-api-key"] == "secret"
    assert payload["system"] == "rules"
    assert payload["messages"][0]["role"] == "user"


def test_gemini_request_and_response_are_native():
    url, headers, payload = request_spec(
        "gemini", "https://generativelanguage.googleapis.com/v1beta", "gemini-test", "secret",
        [{"role": "system", "content": "rules"}, {"role": "user", "content": "hello"}], 0.2,
    )
    assert ":generateContent?key=secret" in url
    assert payload["systemInstruction"]["parts"][0]["text"] == "rules"
    text, usage = parse_response("gemini", {
        "candidates": [{"content": {"parts": [{"text": "{\"ok\":true}"}]}}],
        "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2},
    })
    assert text == '{"ok":true}'
    assert usage == {"prompt_tokens": 3, "completion_tokens": 2}


def test_openrouter_uses_openai_compatible_endpoint():
    url, headers, payload = request_spec(
        "openrouter", "https://openrouter.ai/api/v1", "provider/model", "secret",
        [{"role": "user", "content": "hello"}], 0.2,
    )
    assert url.endswith("/chat/completions")
    assert headers["Authorization"] == "Bearer secret"
    assert headers["X-Title"] == "SIGNAL"
    assert payload["model"] == "provider/model"


def test_anthropic_response_normalization():
    text, usage = parse_response("anthropic", {
        "content": [{"type": "text", "text": "{\"ok\":true}"}],
        "usage": {"input_tokens": 7, "output_tokens": 4},
    })
    assert text == '{"ok":true}'
    assert usage == {"prompt_tokens": 7, "completion_tokens": 4}


def test_alias_normalization():
    assert normalize_provider("google") == "gemini"
    assert normalize_provider("claude") == "anthropic"
