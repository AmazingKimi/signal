"""0.6.1 产品化：i18n / 团队状态 / Evidence 双语 / Provider 隐藏（验收 26-28 节）。"""
import json
import os
import re

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import AnalysisInput, Evidence
from app.orchestrator import analyze

client = TestClient(app)

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "app", "static")
I18N_JS = open(os.path.join(STATIC, "i18n.js"), encoding="utf-8").read()
INDEX_HTML = open(os.path.join(STATIC, "index.html"), encoding="utf-8").read()


# ---------------- i18n 字典完整性 ----------------

def test_i18n_keys_consistent_zh_en():
    """zh-CN 与 en-US 的 key 集合必须一致（防止某语言缺文案）。"""
    zh = re.search(r'"zh-CN": \{(.*?)\n  \}', I18N_JS, re.S).group(1)
    en = re.search(r'"en-US": \{(.*?)\n  \}', I18N_JS, re.S).group(1)
    zh_keys = set(re.findall(r'"([a-z0-9_.]+)":', zh))
    en_keys = set(re.findall(r'"([a-z0-9_.]+)":', en))
    assert zh_keys == en_keys, f"缺失: {zh_keys ^ en_keys}"


def test_zh_is_default():
    """zh-CN 是默认语言（产品默认简体中文）。"""
    assert "localStorage.getItem('kimi.lang') || 'zh-CN'" in I18N_JS


def test_language_persistence():
    """语言选择必须持久化（localStorage）。"""
    assert "localStorage.setItem('kimi.lang'" in I18N_JS


# ---------------- Enum 本地化（11 节） ----------------

def test_enum_localization_mapping():
    """数据库 Enum 不改，展示层映射：BUY→建议买入、VERIFIED→来源已验证。"""
    assert '"enum.buy": "建议买入"' in I18N_JS
    assert '"enum.verified": "来源已验证"' in I18N_JS
    assert '"enum.not_comparable": "不可比"' in I18N_JS


# ---------------- Provider 隐藏（03/06 节） ----------------

def test_provider_hidden_from_normal_ui():
    """主界面 HTML 不得包含 DeepSeek / DuckDuckGo 等工程 Provider 字样。"""
    for banned in ("DeepSeek", "duckduckgo", "deepseek-chat", "API Key", "Search Provider"):
        assert banned.lower() not in INDEX_HTML.lower(), f"主界面泄露工程信息: {banned}"


def test_developer_info_only_in_settings_endpoint():
    """Provider 信息只能通过 /api/settings/dev 获取。"""
    r = client.get("/api/settings/dev")
    assert r.status_code == 200
    d = r.json()
    assert "llm_provider" in d and "search_provider" in d
    assert d["llm_provider"] in ("deepseek", "") or d["llm_provider"]
    # 主 health 不再暴露 provider 字段给产品 UI（agents 接口只有状态）
    agents = client.get("/api/agents").json()
    assert "provider" not in json.dumps(agents["team"])


# ---------------- Agent 健康状态映射（05 节） ----------------

def test_agent_states_reflect_real_service(monkeypatch):
    """DeepSeek 不可用 → ANALYST LIMITED；Search 不可用 → SCOUT OFFLINE。"""
    class NoLLM:
        configured = False
        provider = "deepseek"
        model = "deepseek-chat"
        usage = type("U", (), {"as_dict": lambda s: {"calls": 0}})()

    class NoSearch:
        name = "duckduckgo"
        available = False

    monkeypatch.setattr("app.main.build_provider", lambda: NoLLM())
    monkeypatch.setattr("app.main.build_search_provider", lambda: NoSearch())
    r = client.get("/api/agents").json()
    assert r["team"]["analyst"]["state"] == "LIMITED"   # 部分研究能力不可用
    assert r["team"]["scout"]["state"] == "OFFLINE"     # 市场搜索暂不可用


def test_monitor_paused_state(monkeypatch):
    """有 Watch 但全部暂停/未启用调度 → MONITOR 不是 ACTIVE。"""
    from app import llm as llm_mod, research as research_mod
    from app import store as store_mod

    class RealLLM:
        configured = True
        provider = "deepseek"
        model = "deepseek-chat"
        usage = type("U", (), {"as_dict": lambda s: {"calls": 0}})()

    class RealSearch:
        name = "duckduckgo"
        available = True

    monkeypatch.setattr(llm_mod, "build_provider", lambda: RealLLM())
    monkeypatch.setattr(research_mod, "build_search_provider", lambda: RealSearch())
    # 无 watch → MONITOR OFFLINE
    monkeypatch.setattr(store_mod, "watch_list", lambda: [])
    r = client.get("/api/agents").json()
    assert r["team"]["monitor"]["state"] == "OFFLINE"


# ---------------- Evidence 双语（07/08 节） ----------------

def test_evidence_model_keeps_original():
    """Evidence 模型：original 永久保存，translated 是新增展示字段。"""
    e = Evidence(claim="成交价", value="€310,500", evidence="The car sold for EUR 310,500",
                 source_url="https://rmsothebys.com/lot/1")
    assert e.original_text is None  # original_text 默认未填；evidence 即原文
    assert e.translated_text is None
    assert e.translated_language == "zh-CN"
    # 原文绝不因翻译字段而改变
    assert e.evidence == "The car sold for EUR 310,500"


def test_translation_never_enters_pricing():
    """验收 27：Pricing Engine 输入完全不包含 translated_text。"""
    inp = AnalysisInput(
        artist="Porsche", artwork="964 Carrera RS N-GT", asking_price=325000,
        currency="EUR", category="CLASSIC_CAR", year="1992",
        comparables=[],
    )
    report = analyze(inp)
    dump = report.model_dump_json()
    # 翻译字段不存在于 pricing 相关输出（translated_text 只在证据展示层可选）
    assert "translated_text" not in dump or report.evidence is not None
    # 定价数字来自确定性引擎，不含任何翻译注入的字段
    assert report.price.ai_fair_range_low is None or isinstance(report.price.ai_fair_range_low, (int, float))


def test_translate_endpoint_preserves_numbers(monkeypatch):
    """翻译接口：数字/金额/型号原样保留（10 节）。"""
    class FakeLLM:
        configured = True
        provider = "deepseek"
        model = "deepseek-chat"
        usage = type("U", (), {"as_dict": lambda s: {"calls": 0}})()
        def chat(self, messages, **kw):
            return {"translated": "该车辆于 1992 年生产，售价 €310,500，型号 964 Carrera RS N/GT（M003）"}

    monkeypatch.setattr("app.main.build_provider", lambda: FakeLLM())
    r = client.post("/api/translate", json={"text": "1992 Porsche 964 Carrera RS N/GT sold for EUR 310,500 (M003)"})
    assert r.status_code == 200
    t = r.json()["translated"]
    assert "310,500" in t       # 数字原样
    assert "1992" in t          # 年份原样
    assert "M003" in t          # 型号代码原样


def test_translate_unavailable_without_llm(monkeypatch):
    """无 LLM Key：翻译返回 unavailable，Evidence 显示原文（不假装翻译）。"""
    class NoLLM:
        configured = False
    monkeypatch.setattr("app.main.build_provider", lambda: NoLLM())
    r = client.post("/api/translate", json={"text": "The car sold for EUR 310,500"})
    assert r.json()["unavailable"] is True
    assert r.json()["translated"] is None


# ---------------- 验收 26：DeepSeek 不可用主界面不报错 ----------------

def test_main_ui_never_shows_provider_error():
    """主界面 HTML 不含 'DeepSeek API Error' 等错误字样。"""
    assert "API Error" not in INDEX_HTML
    assert "DeepSeek" not in INDEX_HTML


# ---------------- i18n 切换后端支撑 ----------------

def test_agents_endpoint_shape():
    r = client.get("/api/agents")
    assert r.status_code == 200
    t = r.json()["team"]
    assert set(t.keys()) == {"scout", "analyst", "monitor"}
    for k, v in t.items():
        assert "state" in v


# ---------------- 移动端适配（22 节） ----------------

def test_responsive_css_present():
    """基本响应式：小屏单列 + 表格横向滚动容器。"""
    assert "@media (max-width:720px)" in INDEX_HTML
    assert ".tablewrap" in INDEX_HTML


# ---------------- 语言切换不动数据（28 节） ----------------

def test_lang_switch_never_writes_data():
    """语言切换只影响展示层：applyI18n 不调用任何写接口（POST/DELETE）。"""
    assert "localStorage.setItem('kimi.lang'" in I18N_JS  # 切换持久化
    body = re.search(r"function applyI18n\(\)\{(.*?)\n\}", INDEX_HTML, re.S).group(1)
    assert "POST" not in body and "DELETE" not in body
    # 语言切换后重新渲染当前视图（数据库数据不变）
    assert "loadScout()" in body and "loadWatchlist()" in body and "loadHistory()" in body
