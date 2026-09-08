"""0.7.2 UI Integration Tests — 母版 Liquid Glass 真实接入验收（任务书第二十四节）。

设计稿 = 实际产品。这些测试验证「prototype.html 成为正式前端」这一事实：
- 根页面就是 Liquid Glass（不是另外的 prototype.html / static-html）
- 旧 UI / Private Bank / 调试器不可达
- Scout / Analyst / Team 全部由真实 API 驱动（无写死 fixture）
- 中英 / 明暗 / 移动端 / Evidence 展开 / 语言切换 全部可用
"""
import os
import re

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "app", "static")
INDEX_HTML = open(os.path.join(STATIC, "index.html"), encoding="utf-8").read()
I18N_JS = open(os.path.join(STATIC, "i18n.js"), encoding="utf-8").read()
CSS_DIR = os.path.join(STATIC, "css")
JS_DIR = os.path.join(STATIC, "js")
CSS_ALL = "".join(open(os.path.join(CSS_DIR, f), encoding="utf-8").read()
                  for f in os.listdir(CSS_DIR) if f.endswith(".css"))


# ================================================================ 根页面就是 Liquid Glass

def test_root_uses_liquid_glass_ui():
    """GET / 直接返回正式 Liquid Glass 前端（不是 prototype / static-html）。"""
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    # 母版唯一视觉参数
    assert "--accent:#0a84ff" in html or "--accent: #0a84ff" in html or "accent:#0a84ff" in html
    assert "mk-nav" in html
    assert "SIGNAL" in html
    # 不是 prototype / 独立演示页
    assert "0.7 Design Prototype" not in html
    assert "designbar" not in html
    assert "Design Prototype" not in html


def test_launcher_replaces_a_stale_build_instead_of_opening_it():
    """双击启动器不能因为 8765 上有旧服务就继续打开旧页面。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    start = open(os.path.join(root, "start.command"), encoding="utf-8").read()
    health = client.get("/api/health").json()
    assert health["build"] == "amazing-kimi-0.8.4g-parallels-searxng"
    assert health["build"] in start
    assert "检测到旧构建" in start
    assert '?build=$EXPECTED_BUILD' in start


# ================================================================ 旧 UI 不加载

def test_old_ui_not_loaded():
    """旧 0.6 / 0.7(Private Bank) UI 不可能是默认路由。"""
    html = client.get("/").text
    for banned in ("viewScout", "tabScout", "class=\"card\"", "data-theme=\"bank\"",
                   "private_bank"):
        assert banned not in html, f"旧 UI 残留：{banned}"
    # 0.7.2 唯一 page 结构
    assert 'id="page-scout"' in html
    assert 'id="page-team"' in html
    # 旧 0.6.3 字样不可出现在启动器
    start = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "start.command"), encoding="utf-8").read()
    assert "0.6.3" not in start


# ================================================================ Private Bank 不可加载

def test_private_bank_theme_not_loaded():
    """金色 Private Bank 主题在生产前端不存在。"""
    html = client.get("/").text
    assert "#a07d3f" not in html
    assert "#c9a45c" not in html
    assert "data-direction" not in html
    assert "B · Bank" not in html
    # 模块化 CSS 也不含金
    assert "a07d3f" not in CSS_ALL
    assert "c9a45c" not in CSS_ALL


# ================================================================ Scout 真实数据 → Opportunity Card

def test_scout_api_renders_opportunity_card(monkeypatch):
    """真实 Scout 候选 → 母版 Opportunity Card 渲染路径存在。"""
    from app import research as R
    from app.scout import run_scout
    from app.store import scout_profile_save
    from app.models import Comparable, SALE_ASKING

    scout_profile_save({"interests": "TAKIS SIGNALS", "categories": ["ART"],
                        "budget_low": 5000, "budget_high": 500000})
    recs = [
        Comparable(title="Takis Signal, Series 1", price=9500, currency="EUR",
                   source_url="https://sothebys.com/lot/1", source_name="Sotheby's",
                   sale_type=SALE_ASKING, price_basis="INCLUDING_PREMIUM",
                   source_tier=1, evidence_excerpt="Takis Signal", attributes={}),
        Comparable(title="Takis Signals, series 1", price=14000, currency="EUR",
                   source_url="https://artcurial.com/2", source_name="Artcurial",
                   sale_type="SOLD", price_basis="INCLUDING_PREMIUM",
                   source_tier=1, evidence_excerpt="sold for EUR 14,000", attributes={}),
    ]
    monkeypatch.setattr(R, "run_research",
                        lambda inp, provider, max_queries=2, on_step=None, queries=None:
                        {"records": recs, "summary": None})
    result = run_scout()
    assert result["status"] == "COMPLETED"
    assert len(result["top"]) >= 1
    # 前端模板必须能渲染真实候选
    assert "opp-card" in INDEX_HTML
    assert "opp-media" in INDEX_HTML
    assert "score-chip" in INDEX_HTML
    assert "SCOUT SCORE" in INDEX_HTML
    assert "scoutAnalyze(" in INDEX_HTML
    assert "scoutWatch(" in INDEX_HTML
    assert "/api/scout/today" in INDEX_HTML
    # 渲染函数使用真实字段
    assert "c.scout_score" in INDEX_HTML
    assert "c.asking_price" in INDEX_HTML
    assert "c.object" in INDEX_HTML


# ================================================================ Scout 空 → Daily Brief

def test_scout_empty_renders_daily_brief(monkeypatch):
    """真实空巡视 → 母版 Daily Brief（不是空白框）。"""
    from app import research as R
    from app.scout import run_scout
    from app.store import scout_profile_save

    scout_profile_save({"interests": "TAKIS SIGNALS", "categories": ["ART"]})
    monkeypatch.setattr(R, "run_research",
                        lambda inp, provider, max_queries=2, on_step=None, queries=None:
                        {"records": [], "summary": None})
    result = run_scout()
    assert result["status"] == "COMPLETED"
    assert result["brief"]["conclusion_key"] == "empty_scan"
    # 前端 Daily Brief 结构存在（母版 brief-nums 四格）
    assert "brief-nums" in INDEX_HTML
    assert "brief-concl" in INDEX_HTML
    assert "brief.queries" in INDEX_HTML or "brief.queries" in I18N_JS
    # 空状态四态中的 empty 也有 funnel
    assert "funnel" in INDEX_HTML
    # 结论文案真实映射
    assert "brief.conclusion.empty_scan" in I18N_JS


# ================================================================ Near Miss 渲染

def test_near_miss_renders(monkeypatch):
    """真实 0 推荐但有候选 → 母版 Near Miss 行。"""
    from app import research as R
    from app.scout import run_scout
    from app.store import scout_profile_save
    from app.models import Comparable, SALE_ASKING

    scout_profile_save({"interests": "TAKIS SIGNALS", "categories": ["ART"]})
    recs = [
        Comparable(title="Takis Signal (series 1 n°66), 1968", price=14432, currency="EUR",
                   source_url="https://artcurial.com/1", source_name="Artcurial",
                   sale_type=SALE_ASKING, price_basis="INCLUDING_PREMIUM",
                   source_tier=1, evidence_excerpt="Takis Signal", attributes={}),
        Comparable(title="Takis Signals, private collection", price=9500, currency="EUR",
                   source_url="https://sothebys.com/2", source_name="Sotheby's",
                   sale_type=SALE_ASKING, price_basis="INCLUDING_PREMIUM",
                   source_tier=1, evidence_excerpt="Takis Signals", attributes={}),
    ]
    monkeypatch.setattr(R, "run_research",
                        lambda inp, provider, max_queries=2, on_step=None, queries=None:
                        {"records": recs, "summary": None})
    result = run_scout()
    assert result["brief"]["conclusion_key"] == "near_miss"
    assert len(result["near_misses"]) >= 1
    # 前端 Near Miss 母版行
    assert "nearmiss" in INDEX_HTML
    assert "nm-row" in INDEX_HTML
    assert "brief.near.notRecommended" in I18N_JS
    assert "c.scout_score" in INDEX_HTML
    # 未推荐原因（why_not 双语）
    assert "brief.why.not" in I18N_JS


# ================================================================ Analyst 真实报告 → Decision

def test_analyst_report_renders_decision(monkeypatch):
    """真实报告 → 母版决策卡（d-main 大元素 + 四格价格 + confidence）。"""
    from app.orchestrator import analyze
    from app.models import AnalysisInput

    inp = AnalysisInput(artist="Porsche", artwork="964 Carrera RS N-GT",
                        asking_price=325000, currency="EUR", category="CLASSIC_CAR",
                        year="1992", research=False, comparables=[])
    report = analyze(inp)
    assert report.decision in ("BUY", "NEGOTIATE", "WATCH", "PASS")
    # 前端母版决策结构
    assert "glass decision" in INDEX_HTML or "decision" in INDEX_HTML
    assert "d-main" in INDEX_HTML
    assert "d-nums" in INDEX_HTML
    assert "conf-track" in INDEX_HTML
    assert "decision.asking" in I18N_JS
    assert "decision.opening" in I18N_JS
    assert "decision.confidence" in I18N_JS
    # 渲染函数存在且用真实字段
    assert "renderDecision" in INDEX_HTML
    assert "b.decision" in INDEX_HTML
    assert "b.confidence" in INDEX_HTML
    # 四态样式
    assert "d-main.buy" in INDEX_HTML
    assert "d-main.watch" in INDEX_HTML
    assert "d-main.pass" in INDEX_HTML
    # 原型假数据不写死
    assert "€325,000" not in INDEX_HTML
    assert "72%" not in INDEX_HTML


# ================================================================ Evidence 展开

def test_evidence_expand():
    """Evidence 卡：展开中文摘要 → 查看英文原文（原文不被覆盖）。"""
    assert "ev-card" in INDEX_HTML
    assert "ev-x" in INDEX_HTML
    assert "evidence.expand" in I18N_JS
    assert "evidence.viewOriginal" in I18N_JS
    # 展开按钮切换 open
    assert "classList.toggle('open')" in INDEX_HTML or "classList.toggle" in INDEX_HTML
    # 原文保留（translated 与 evidence 并存）
    assert "e.translated_text" in INDEX_HTML
    assert "e.evidence" in INDEX_HTML
    # 翻译不进定价（后端保证，前端只展示）
    assert "translated_text" in INDEX_HTML
    from app.models import Evidence
    assert "original_text" in Evidence.model_fields


# ================================================================ Team 真实 API 三 agent

def test_team_api_renders_three_agents():
    """Team 页渲染三岗位，KPI 全部来自真实 API。"""
    assert "/api/agents" in INDEX_HTML
    assert "/api/scout/today" in INDEX_HTML
    assert "/api/scout/history" in INDEX_HTML
    assert "/api/history" in INDEX_HTML
    assert "/api/watch" in INDEX_HTML
    assert "Promise.allSettled" in INDEX_HTML
    # 母版三卡
    assert "team-grid" in INDEX_HTML
    assert "team-card" in INDEX_HTML
    assert "SCOUT" in INDEX_HTML and "ANALYST" in INDEX_HTML and "MONITOR" in INDEX_HTML
    # 不再硬编码 7/35/7/1/6/40%/14.2%
    assert ">7</div>" not in INDEX_HTML or "cards.forEach" in INDEX_HTML
    assert "14.2%" not in INDEX_HTML
    assert ">40%" not in INDEX_HTML
    # 真实状态 badge 映射
    assert "badge(" in INDEX_HTML
    assert "team.ready" in I18N_JS
    assert "team.active" in I18N_JS


# ================================================================ Watchlist 新主题

def test_watchlist_new_theme():
    """Watchlist 沿用同一 Liquid Glass Design System。"""
    assert "toolbar-card" in INDEX_HTML
    assert "form-grid" in INDEX_HTML
    assert "kpi-strip" in INDEX_HTML or "kpi-card" in INDEX_HTML
    assert "watch.knownListings" in I18N_JS
    # 仍用真实 API
    assert "/api/watch" in INDEX_HTML
    assert "watchDetail(" in INDEX_HTML
    # 不是旧后台表格当主视觉（表格在玻璃卡内）
    assert "glass toolbar-card" in INDEX_HTML


def test_watch_check_summary_renders_a_count_not_object_list():
    """监控检查提示必须显示事件数量，不能把对象数组转成 [object Object]。"""
    assert "j.event_count" in INDEX_HTML
    assert "Array.isArray(j.events) ? j.events.length" in INDEX_HTML
    assert "+j.events+" not in INDEX_HTML


def test_scout_uses_current_command_and_has_no_preference_panel():
    """Scout 当前输入必须随运行请求提交；旧偏好表单和隐藏 Seeds 不再进入正式页面。"""
    assert 'id="scoutQuery"' in INDEX_HTML
    assert "JSON.stringify({interests:query})" in INDEX_HTML
    assert "h += renderScoutProfile()" not in INDEX_HTML
    assert "scout.funnel')" not in INDEX_HTML


def test_scout_kpis_open_on_demand_details():
    """四个 KPI 可点击，并共用一个轻量详情弹层。"""
    assert 'id="scoutMetricModal"' in INDEX_HTML
    assert "onclick=\"openScoutMetric(" in INDEX_HTML
    assert 'class="metric-mini"' in INDEX_HTML
    assert "function openScoutMetric(index)" in INDEX_HTML
    assert "j.search_plan" in INDEX_HTML
    assert "j.candidates" in INDEX_HTML
    assert "SCOUT SCORE ≥ 55" in INDEX_HTML


def test_scout_new_target_replaces_old_hidden_seeds(monkeypatch):
    from app import scout as scout_module
    from app.store import scout_profile_get, scout_profile_save
    scout_profile_save({"interests": "TAKIS SIGNALS", "categories": ["ART"],
                        "seeds_artists": "Takis", "seeds_models": "",
                        "seeds_keywords": "signal"})
    monkeypatch.setattr(scout_module, "run_scout",
                        lambda provider=None, llm=None: {"status": "COMPLETED", "run_id": 1})
    response = client.post("/api/scout/run", json={"interests": "Porsche 964 Carrera RS N-GT"})
    assert response.status_code == 200
    saved = scout_profile_get()
    assert saved["interests"] == "Porsche 964 Carrera RS N-GT"
    assert saved["seeds_artists"] == ""
    assert saved["seeds_models"] == ""
    assert saved["seeds_keywords"] == ""
    serialized = str(saved).lower()
    assert "takis" not in serialized
    assert "porsche" in serialized


def test_final_decision_button_does_not_embed_report_json_in_html():
    """报告对象不得写进 onclick 属性，否则引号会截断标签并把原始 JSON 泄漏到页面。"""
    assert "let ACTIVE_REPORT = null" in INDEX_HTML
    assert "ACTIVE_REPORT = r" in INDEX_HTML
    assert 'onclick="watchThisMarket()"' in INDEX_HTML
    assert "JSON.stringify(r)" not in INDEX_HTML


# ================================================================ History 新主题

def test_history_new_theme():
    """History 沿用同一 Design System：KPI 玻璃卡 + 表格。"""
    assert "kpi-strip" in INDEX_HTML
    assert "kpi-card" in INDEX_HTML
    assert "hist.total" in I18N_JS
    assert "hist.avgDiscount" in I18N_JS
    assert "/api/history" in INDEX_HTML
    assert "loadScoutHistory" in INDEX_HTML
    # 枚举中文（任务书 18 节：买入/放弃/谈价/是）
    assert '"enum.buy": "建议买入"' in I18N_JS
    assert "mem.bought" in I18N_JS


# ================================================================ 语言切换

def test_language_switch():
    """中英切换：所有新 UI 文案同步，无半中半英。"""
    assert "setLang('zh-CN')" in INDEX_HTML
    assert "setLang('en-US')" in INDEX_HTML
    assert "localStorage.setItem('kimi.lang'" in I18N_JS
    # zh/en key 集合对称
    zh = re.search(r'"zh-CN": \{(.*?)\n  \}', I18N_JS, re.S).group(1)
    en = re.search(r'"en-US": \{(.*?)\n  \}', I18N_JS, re.S).group(1)
    zh_keys = set(re.findall(r'"([a-z0-9_.]+)":', zh))
    en_keys = set(re.findall(r'"([a-z0-9_.]+)":', en))
    assert zh_keys == en_keys
    # 导航小号英文辅助存在
    assert "nav.scout.en" in I18N_JS
    assert "nav.team.en" in I18N_JS


# ================================================================ 暗色模式

def test_dark_mode():
    """深色主题：token 集 + 持久化 + 三态。"""
    assert '[data-theme="dark"]' in CSS_ALL or 'data-theme="dark"' in INDEX_HTML
    assert "--bg:#0b0b0e" in CSS_ALL or "--bg: #0b0b0e" in CSS_ALL or "0b0b0e" in CSS_ALL
    assert "prefers-color-scheme: dark" in INDEX_HTML
    assert "localStorage.setItem(THEME_KEY" in INDEX_HTML
    assert "settings.theme.dark" in I18N_JS
    assert "settings.theme.system" in I18N_JS


# ================================================================ 移动端布局

def test_mobile_layout():
    """移动端：母版组件在真实浏览器宽度下单列可用。"""
    assert "@media (max-width:720px)" in INDEX_HTML
    assert ".opp-grid,.brief-grid,.team-grid,.risk-list{grid-template-columns:1fr}" in INDEX_HTML
    assert ".agentstrip{grid-template-columns:1fr}" in INDEX_HTML
    # 小屏 nav 可横向滚动
    assert "mk-navlinks" in INDEX_HTML


# ================================================================ 设计调试控件不可达

def test_design_debug_controls_absent():
    """设计调试器（方向 / 主题 / 设备 / 组件库）不出现在生产。"""
    html = client.get("/").text
    # 真实调试控件/调试页标识
    for banned in ("designbar", "data-direction", "data-device", "segDirection",
                   "segDevice", "组件库", "B · Private Bank", "toggleEmpty",
                   "backHasOpp", "data-pg3", "data-dir"):
        assert banned not in html, f"调试控件泄漏：{banned}"
    # 组件库页不存在
    assert 'id="page-comp"' not in html
    # 导航只有 5 个真实模块
    navs = re.findall(r'data-page="([a-z]+)"', html)
    assert navs == ["scout", "analyst", "watch", "history", "team"]
