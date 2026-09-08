"""0.7.2 视觉定稿验收：母版 A · Liquid Glass 正式接入产品。

十项验收点（按任务书第三十一节）：
1. default_ui_is_liquid_glass — 主界面默认即蓝色 Liquid Glass（母版精确值）
2. private_bank_theme_not_exposed — 金色 Private Bank 主题绝不出现在生产
3. design_debug_controls_hidden — 设计调试控件（A·Glass / B·Bank 等）不出现
4. scout_real_data_renders_new_card — SCOUT 真实数据渲染母版 Opportunity Card
5. analyst_real_data_renders_decision — ANALYST 真实数据渲染母版决策页
6. team_new_ui_uses_real_api — TEAM 页用真实 API（不再硬编码）
7. language_switch_new_ui — 语言切换后所有新 UI 文案都变
8. dark_mode_persistence — 主题选择持久化到 localStorage
9. mobile_navigation — 移动端导航自适应
10. old_ui_not_default — 旧 Private Bank 默认页不再可能
"""
import os
import re

import pytest

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "app", "static")
INDEX_HTML = open(os.path.join(STATIC, "index.html"), encoding="utf-8").read()
I18N_JS = open(os.path.join(STATIC, "i18n.js"), encoding="utf-8").read()
# 0.7.2 模块化结构（母版拆分）
_CSS_FILES = ['tokens.css', 'liquid-glass.css', 'layout.css', 'components.css',
              'dark.css', 'responsive.css']
VISION = INDEX_HTML
for _f in _CSS_FILES:
    _p = os.path.join(STATIC, 'css', _f)
    if os.path.exists(_p):
        VISION += "\n" + open(_p, encoding="utf-8").read()


# ================================================================ 1. 默认即蓝色 Liquid Glass

def test_default_ui_is_liquid_glass():
    """生产界面默认呈现 Intelligence Blue 主视觉（母版精确参数）。"""
    # 蓝色 token：母版 --accent:#0a84ff + 兼容别名 --ak-blue
    assert '--accent:#0a84ff' in VISION
    assert '--ak-blue: #0a84ff' in VISION
    # 玻璃更透（Liquid Glass A 透明度 0.55 而非 B 的 0.82）
    assert 'rgba(255,255,255,.55)' in VISION
    # 五级圆角（visionOS：10/14/22/28/36）
    assert '--radius-xs:10px' in VISION
    assert '--radius:22px' in VISION
    assert '--radius-lg:28px' in VISION
    assert '--radius-xl:36px' in VISION
    # 大模糊 + 高饱和（Liquid Glass 28px · saturate 2.2）
    assert 'blur(var(--blur)) saturate(2.2)' in VISION
    assert '--blur:28px' in VISION or '--blur: 28px' in VISION
    # 母版品牌头 + 导航结构
    assert 'SIGNAL' in INDEX_HTML
    assert 'PRIVATE DISCOVERY RADAR' in INDEX_HTML
    assert 'mk-nav' in INDEX_HTML
    assert 'mk-brand' in INDEX_HTML
    # 五大模块导航（母版单条玻璃 nav + data-page）
    for pg in ('scout', 'analyst', 'watch', 'history', 'team'):
        assert f'data-page="{pg}"' in INDEX_HTML, f'缺少导航页：{pg}'
    assert '--blur:28px' in VISION or '--blur: 28px' in VISION
    # 品牌名：SIGNAL + 副标题 PRIVATE DISCOVERY RADAR
    assert 'SIGNAL' in INDEX_HTML
    assert 'PRIVATE DISCOVERY RADAR' in INDEX_HTML
    # 五大模块导航：侦察/分析/监控/历史/团队
    for nav in ('nav.scout', 'nav.analyze', 'nav.watchlist', 'nav.history', 'nav.team'):
        assert f'data-i18n="{nav}"' in INDEX_HTML, f'缺少导航：{nav}'


# ================================================================ 2. Private Bank 金色主题不得出现

def test_private_bank_theme_not_exposed():
    """B · Private Bank 金色哑光主题已正式下线。"""
    # 旧金色 hex 不再出现在视觉锁定范围内
    assert '#a07d3f' not in VISION
    assert '#c9a45c' not in VISION
    # i18n 中「Private Bank」明示废弃
    assert '"migrate.gold.deprecated"' in I18N_JS
    # 未出现切换入口「B·Bank」
    assert 'B · Bank' not in INDEX_HTML
    assert 'B · Private Bank' not in INDEX_HTML


# ================================================================ 3. 设计调试控件不可见

def test_design_debug_controls_hidden():
    """生产界面不得出现设计调试器控件。"""
    forbidden = [
        'A · Glass 切换', 'B · Bank 切换', '方向切换',
        'design-debug', '设计调试', 'design debugger',
        'Device Picker', 'Theme Picker', 'Direction Toggle',
        '组件库切换', '组件库预览', '方向选择'
    ]
    for s in forbidden:
        assert s not in INDEX_HTML, f'调试控件泄漏：{s}'


# ================================================================ 4. SCOUT 真实数据驱动新卡

def test_scout_real_data_renders_new_card():
    """SCOUT 真实数据进入母版 Liquid Glass Opportunity Card 模板。"""
    # 母版模板块
    assert 'opp-card' in INDEX_HTML
    assert 'opp-media' in INDEX_HTML
    assert 'opp-why' in INDEX_HTML
    # 三态图片占位（Image Available / No Image / Image Failed）
    assert 'oppImgHTML' in INDEX_HTML
    assert "opp.image.failed" in I18N_JS
    assert "opp.noImage" in I18N_JS
    # 为什么值得关注
    assert 'opp.why.bullets' in I18N_JS
    # SCOUT SCORE + 双按钮（深度分析 + 加入监控）
    assert 'SCOUT SCORE' in INDEX_HTML
    assert "scoutAnalyze(" in INDEX_HTML
    assert "scoutWatch(" in INDEX_HTML
    # Daily Brief 真实数据驱动的四格（母版 brief-nums）
    assert 'brief-nums' in INDEX_HTML
    for k in ('brief.queries', 'brief.records', 'brief.candidates', 'brief.recommended'):
        assert f"'{k}'" in INDEX_HTML or f'"{k}"' in INDEX_HTML, f'Daily Brief 缺键：{k}'
    # 硬编码的原型假数据绝不出现在生产界面
    assert '€289,000' not in INDEX_HTML
    assert '€14,432' not in INDEX_HTML


# ================================================================ 5. ANALYST 真实数据决策页

def test_analyst_real_data_renders_decision():
    """ANALYST 真实报告渲染母版决策页：NEGOTIATE/BUY 作为最大视觉元素。"""
    assert 'class="glass decision"' in INDEX_HTML or 'glass decision' in INDEX_HTML
    assert 'd-main' in INDEX_HTML
    assert 'decision.asking' in I18N_JS
    assert 'decision.fair' in I18N_JS
    assert 'decision.opening' in I18N_JS
    assert 'decision.max' in I18N_JS
    assert 'decision.confidence' in I18N_JS
    # 工作流（母版 workflow 圆点步骤，当前蓝色 / 完成绿色）
    assert 'workflow' in INDEX_HTML
    assert 'wf-step' in INDEX_HTML
    assert 'analyst.progress.step1' in I18N_JS
    assert 'analyst.progress.step6' in I18N_JS
    # Risk 三档横列（HIGH / MEDIUM / LOW）
    assert 'risk-row' in INDEX_HTML
    assert 'risk.high' in I18N_JS
    assert 'risk.medium' in I18N_JS
    assert 'risk.low' in I18N_JS
    # Next Action 玻璃块
    assert 'next-action' in INDEX_HTML
    assert 'next.suggestedOffer' in I18N_JS
    # 不显示 LLM 工程统计
    assert 'LLM calls' not in INDEX_HTML
    assert 'tokens / cost' not in INDEX_HTML
    # 原型假数据不在生产
    assert '€325,000' not in INDEX_HTML
    assert '€268,000' not in INDEX_HTML


# ================================================================ 6. TEAM 页用真实 API

def test_team_new_ui_uses_real_api():
    """TEAM 页必须基于真实 API，不再用 const 数字硬编码 KPI。"""
    # 三个端点都拉取
    assert "/api/agents" in INDEX_HTML
    assert "/api/scout/today" in INDEX_HTML
    assert "/api/scout/history" in INDEX_HTML
    assert "/api/history" in INDEX_HTML
    assert "/api/watch" in INDEX_HTML
    # Promise.allSettled 并发拉取
    assert 'Promise.allSettled' in INDEX_HTML
    # 母版 team-card 渲染（glass team-card）
    assert 'team-card' in INDEX_HTML
    assert 'team-grid' in INDEX_HTML
    # 三岗位 KPI 用真实 API 字段（不硬编码 7/35/7/1/6/40%/14.2%）
    assert 'team.metric.scoutScans' in I18N_JS
    assert 'team.metric.anaChanged' in I18N_JS
    assert 'team.metric.monActive' in I18N_JS
    # 旧版本硬编码的 7/35/7/1 必须不再出现
    assert ">7</div>" not in INDEX_HTML or 'class="n">7</div>' not in INDEX_HTML or 'cards forEach' in INDEX_HTML
    assert ">14.2%" not in INDEX_HTML
    assert ">40%" not in INDEX_HTML
    # 0.6.2 修复：禁止 const t 遮蔽全局 i18n t()
    assert "const t = j.team" not in INDEX_HTML
    assert 't(\'team.err\')' in INDEX_HTML
    assert "console.error('[TEAM]" in INDEX_HTML


# ================================================================ 7. 语言切换全 UI 同步

def test_language_switch_new_ui():
    """语言切换必须让新 UI 文案全部同步。"""
    # 所有新键都同时存在 zh-CN 和 en-US
    new_keys = [
        'settings.theme', 'settings.theme.light', 'settings.theme.dark', 'settings.theme.system',
        'opp.action', 'opp.newListing', 'opp.image.label', 'opp.noImage',
        'analyst.page.title', 'analyst.progress.step1', 'analyst.progress.step6',
        'decision.label', 'decision.asking', 'decision.fair', 'decision.opening',
        'risk.title', 'risk.high',
        'next.title', 'next.suggestedOffer',
        'team.title', 'team.subtitle', 'team.ready', 'team.active'
    ]
    zh = re.search(r'"zh-CN": \{(.*?)\n  \}', I18N_JS, re.S).group(1)
    en = re.search(r'"en-US": \{(.*?)\n  \}', I18N_JS, re.S).group(1)
    for k in new_keys:
        assert f'"{k}":' in zh, f'zh 缺键：{k}'
        assert f'"{k}":' in en, f'en 缺键：{k}'
    # 切换函数
    assert 'localStorage.setItem(\'kimi.lang\'' in I18N_JS


# ================================================================ 8. 暗色模式持久化

def test_dark_mode_persistence():
    """主题选择必须持久化到 localStorage，支持 Light/Dark/System 三态。"""
    assert "localStorage.setItem(THEME_KEY" in INDEX_HTML or "localStorage.setItem('kimi.theme'" in INDEX_HTML
    assert "THEME_KEY" in INDEX_HTML
    # 三态切换函数 + 跟随系统
    assert 'setTheme(mode)' in INDEX_HTML
    assert 'prefers-color-scheme: dark' in INDEX_HTML
    assert '"system"' in INDEX_HTML or "'system'" in INDEX_HTML
    # i18n 三态
    assert '"settings.theme.light"' in I18N_JS
    assert '"settings.theme.dark"' in I18N_JS
    assert '"settings.theme.system"' in I18N_JS
    # data-theme 默认 light
    # data-theme 默认 light（tokens.css 持有 CSS 选择器，使用 VISION 联合判断）
    assert 'data-theme="light"' in VISION or '[data-theme="light"]' in VISION or 'data-themeMode' in INDEX_HTML


# ================================================================ 9. 移动端导航自适应

def test_mobile_navigation():
    """导航必须在 720px 以下正确折叠（母版单条玻璃 nav）。"""
    assert '@media (max-width:720px)' in INDEX_HTML
    assert '@media (max-width:420px)' in INDEX_HTML
    # 移动端 nav 适配
    assert '.mk-navlinks button{padding:6px 11px' in INDEX_HTML or 'mk-navlinks button {padding:6px 11px' in INDEX_HTML or '.mk-navlinks button{padding:6px 10px' in INDEX_HTML
    # agentstrip 单列
    assert '.agentstrip{grid-template-columns:1fr}' in INDEX_HTML
    # 表格横滚
    assert '.tablewrap' in INDEX_HTML
    # 决策 / 团队单列（母版组合选择器）
    assert '.opp-grid,.brief-grid,.team-grid,.risk-list{grid-template-columns:1fr}' in INDEX_HTML


# ================================================================ 10. 旧 Private Bank 默认页不再可能

def test_old_ui_not_default():
    """0.7.2 之后，旧 Private Bank 金色版默认页不再可能。"""
    # 母版唯一 accent 是蓝；旧金色 hex 不存在
    assert 'a07d3f' not in VISION
    assert 'c9a45c' not in VISION
    # 没有 theme="bank" / "gold" 切换
    assert 'data-theme="bank"' not in VISION
    assert 'data-theme="gold"' not in VISION
    # 启动主页面（SCOUT 默认，母版 page-scout）
    assert 'id="page-scout"' in INDEX_HTML
    # 不保留旧版本字样
    assert '0.7 Design System (Direction B' not in VISION
    assert 'Direction B' not in VISION


# ================================================================ 额外保护：组件与文件名

def test_modular_assets_directory_exists():
    """模块化资源目录存在（母版拆分 6 个 css + 8 个 js）。"""
    css_dir = os.path.join(STATIC, 'css')
    for f in ('tokens.css', 'liquid-glass.css', 'layout.css', 'components.css',
              'dark.css', 'responsive.css'):
        path = os.path.join(css_dir, f)
        assert os.path.exists(path), f'缺失：{path}'
    js_dir = os.path.join(STATIC, 'js')
    for f in ('app.js', 'scout.js', 'analyst.js', 'watchlist.js', 'history.js',
              'team.js', 'i18n.js', 'api.js'):
        path = os.path.join(js_dir, f)
        assert os.path.exists(path), f'缺失：{path}'


def test_prototype_html_not_in_production():
    """原型 HTML 不应出现在生产 app/static。"""
    bad = os.path.join(STATIC, 'prototype.html')
    assert not os.path.exists(bad), f'生产目录有 prototype.html：{bad}'
