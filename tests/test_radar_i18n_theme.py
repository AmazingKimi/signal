import json
from pathlib import Path

from app.radar import create_radar, normalize_result

STATIC = Path(__file__).parents[1] / "app" / "static"
HTML = (STATIC / "radar.html").read_text(encoding="utf-8")
JS = (STATIC / "radar-app.js").read_text(encoding="utf-8")
ZH = json.loads((STATIC / "locales" / "zh-CN.json").read_text(encoding="utf-8"))
EN = json.loads((STATIC / "locales" / "en-US.json").read_text(encoding="utf-8"))


def test_default_locale_is_zh_cn():
    assert "localStorage.getItem('ak.locale')||'zh-CN'" in JS


def test_switch_to_en_us():
    assert "setLocale('en-US')" in HTML and EN["nav.settings"] == "SETTINGS"


def test_locale_persists_after_reload():
    assert "localStorage.setItem('ak.locale',locale)" in JS


def test_source_compliance_localized_zh():
    assert ZH["compliance.SEARCH_ONLY"] == "搜索发现"
    assert ZH["compliance.LOGIN_REQUIRED"] == "需要授权"


def test_source_compliance_localized_en():
    assert EN["compliance.SEARCH_ONLY"] == "Search Discovery"
    assert EN["compliance.PAUSED"] == "Do Not Automate"


def test_original_source_text_preserved():
    radar = create_radar("Takis")
    row = normalize_result({"title":"Takis Signal €12,000", "url":"https://example.test/x",
                            "original_text":"Takis Signal, sold in Paris", "original_language":"en",
                            "translated_text":"Takis 信号灯，巴黎成交"}, radar)
    assert row["original_text"] == "Takis Signal, sold in Paris"
    assert row["original_language"] == "en"


def test_translation_does_not_modify_structured_fields():
    radar = create_radar("Takis")
    base = {"title":"Takis Signal 1974 €12,000", "url":"https://example.test/x",
            "original_language":"en"}
    original = normalize_result(base, radar)
    translated = normalize_result(dict(base, translated_text="完全不同的展示摘要"), radar)
    for key in ("source_url", "asking_price", "currency", "year", "source_name", "maker", "object_name"):
        assert translated[key] == original[key]


def test_theme_system_default():
    assert "localStorage.getItem('ak.theme')||'system'" in JS


def test_theme_light():
    assert '[data-theme="light"]' in HTML and "setTheme('light')" in HTML


def test_theme_dark():
    assert '[data-theme="dark"]' in HTML and "setTheme('dark')" in HTML
    assert "--bg-c:#07111f" in HTML


def test_theme_persistence():
    assert "localStorage.setItem('ak.theme',chosen)" in JS


def test_prefers_color_scheme_supported():
    assert "prefers-color-scheme: dark" in JS


def _combination(locale, theme):
    assert locale in ("zh-CN", "en-US") and theme in ("light", "dark")
    assert f"setLocale('{locale}')" in HTML and f"setTheme('{theme}')" in HTML
    dictionary = ZH if locale == "zh-CN" else EN
    for key in ("nav.radar", "nav.discovered", "nav.history", "nav.settings", "settings.sources",
                "settings.email", "discovered.empty", "error.generic"):
        assert dictionary[key]


def test_zh_light_combination(): _combination("zh-CN", "light")
def test_zh_dark_combination(): _combination("zh-CN", "dark")
def test_en_light_combination(): _combination("en-US", "light")
def test_en_dark_combination(): _combination("en-US", "dark")
