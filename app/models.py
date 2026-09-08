"""数据模型 —— Kimi Intelligence / Opportunity Analyst 0.2

铁律（0.1 继承 + 0.2 新增）：
- 没有来源 = UNVERIFIED
- 多个来源互相矛盾 = CONFLICTING
- AI 自己推断出来的数字不能标 VERIFIED
- VERIFIED ≠ 可以拿来定价：还要过 Comparability（HIGH/MEDIUM/LOW/NOT_COMPARABLE）
- SOLD / ASKING / ESTIMATE 三池分离，绝不混算
- 金额口径（HAMMER / INCLUDING_PREMIUM / NET / UNKNOWN）不明 = PRICE BASIS UNVERIFIED，不进定价
"""
from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field

VERIFIED = "VERIFIED"
UNVERIFIED = "UNVERIFIED"
CONFLICTING = "CONFLICTING"

# 可比性等级（0.2 第六节：即使成交是真的，也未必像）
HIGH = "HIGH"
MEDIUM = "MEDIUM"
LOW = "LOW"
NOT_COMPARABLE = "NOT_COMPARABLE"

# 品类（0.2 第七节：不同品类用不同可比规则）
CATEGORIES = ["ART", "CLASSIC_CAR", "DESIGN", "COLLECTIBLE", "OTHER"]

# 成交性质（0.2 第九节：三池分离）
SALE_SOLD = "SOLD"            # 真实成交（含拍卖成交）
SALE_ASKING = "ASKING"        # 挂牌价/要价——只是愿望，不是市场
SALE_ESTIMATE = "ESTIMATE"    # 拍卖估价——预测，不是成交
SALE_UNKNOWN = "UNKNOWN"

# 金额口径（0.2 第十节）
BASIS_HAMMER = "HAMMER"                    # 落槌价（不含佣金）
BASIS_INCLUDING_PREMIUM = "INCLUDING_PREMIUM"  # 含买家佣金成交价
BASIS_NET = "NET"                          # 私洽净价
BASIS_UNKNOWN = "UNKNOWN"                  # 口径不明 -> PRICE BASIS UNVERIFIED

CURRENCY_SYMBOL = {"EUR": "€", "GBP": "£", "USD": "$", "CNY": "¥", "CHF": "CHF "}

# 固定汇率（确定性换算，AI Analysis 假设——不实时抓汇率，保证同输入同输出）
FX_TO_EUR = {"EUR": 1.0, "USD": 0.92, "GBP": 1.17, "CHF": 1.05, "CNY": 0.13}


def convert_currency(price: float, frm: str, to: str = "EUR") -> Optional[float]:
    """固定汇率换算。不支持的币种返回 None（绝不猜汇率）。"""
    frm = (frm or "EUR").upper()
    to = (to or "EUR").upper()
    if frm == to:
        return price
    if frm not in FX_TO_EUR or to not in FX_TO_EUR:
        return None
    return price * FX_TO_EUR[frm] / FX_TO_EUR[to]


def sym(currency: str) -> str:
    return CURRENCY_SYMBOL.get((currency or "EUR").upper(), "")


def fmt_money(v: Optional[float], currency: str = "EUR") -> str:
    if v is None:
        return "未验证"
    return f"{sym(currency)}{v:,.0f}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Evidence(BaseModel):
    """一条外部事实。verification_status 只有三种取值。

    0.6.1：双语展示——original_text/translated_text 永久保存；
    翻译只属于 Presentation Layer，绝不进入 Pricing Engine（09/10 节）。
    """
    claim: str
    value: str
    source_url: Optional[str] = None
    source_name: Optional[str] = None
    retrieved_at: str = Field(default_factory=now_iso)
    evidence: Optional[str] = None
    verification_status: str = UNVERIFIED

    # ---- 0.6.1：Evidence 双语（original 永不被翻译覆盖）----
    original_text: Optional[str] = None       # 海外 Evidence 原始内容（永久保存）
    original_language: str = "en"
    translated_text: Optional[str] = None     # 中文摘要（由翻译接口填充）
    translated_language: str = "zh-CN"
    translation_provider: Optional[str] = None
    translated_at: Optional[str] = None


class Comparable(BaseModel):
    """一条可比记录。verified 的唯一标准：有 source_url。

    0.2 新增字段全部有默认值——0.1 的手动可比（含 Takis 回测）无需改动即可兼容，
    默认 sale_type=SOLD（手动提供即用户断言其为成交）。
    """
    title: str
    artist: Optional[str] = None
    year: Optional[str] = None
    price: float
    currency: str = "EUR"
    sold_at: Optional[str] = None
    is_auction: bool = True
    source_name: Optional[str] = None
    source_url: Optional[str] = None

    # ---- 0.2：三池分离 + 口径 + 来源分级 + 可比性 ----
    sale_type: str = SALE_SOLD
    price_basis: str = BASIS_UNKNOWN          # 手动可比若不注明口径则 UNKNOWN
    source_tier: int = 0                     # 0=未知；1/2/3 按任务书第五节
    comparability: str = ""                  # 空 = 手动提供、用户断言可比（0.1 兼容）
    comparability_reason: str = ""
    evidence_excerpt: Optional[str] = None
    retrieved_at: Optional[str] = None
    attributes: dict = Field(default_factory=dict)

    # ---- 0.3：LLM 精读层（LLM confidence ≠ VERIFIED）----
    llm_extracted: bool = False
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    llm_confidence: Optional[float] = None
    price_basis_reason: str = ""             # LLM 判断口径的依据（引原文）

    @property
    def verified(self) -> bool:
        return bool(self.source_url)

    @property
    def net_price(self) -> Optional[float]:
        """折算为私洽净价口径（AI Analysis 口径换算，非事实）。
        口径不明返回 None——绝不猜。"""
        if self.price_basis == BASIS_INCLUDING_PREMIUM:
            return self.price / 1.25
        if self.price_basis in (BASIS_HAMMER, BASIS_NET):
            return self.price
        return None

    @property
    def basis_label(self) -> str:
        return {
            BASIS_HAMMER: "落槌价",
            BASIS_INCLUDING_PREMIUM: "含佣金",
            BASIS_NET: "净价",
            BASIS_UNKNOWN: "口径不明",
        }.get(self.price_basis, self.price_basis)

    @property
    def sale_label(self) -> str:
        return {
            SALE_SOLD: "成交",
            SALE_ASKING: "挂牌",
            SALE_ESTIMATE: "估价",
            SALE_UNKNOWN: "性质不明",
        }.get(self.sale_type, self.sale_type)

    def display(self) -> str:
        src = self.source_name or "未验证"
        return f"{self.title}（{self.year or '年份未知'}）— {fmt_money(self.price, self.currency)}｜{src}"


class ResearchSummary(BaseModel):
    """RESEARCH SUMMARY —— 搜索漏斗数字，全部如实展示（0.2 第十六节）。"""
    enabled: bool = False
    provider: str = ""
    queries: List[str] = []
    queries_executed: int = 0
    candidates: int = 0
    verified: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    not_comparable: int = 0
    rejected: int = 0
    sold_pool: int = 0
    asking_pool: int = 0
    estimate_pool: int = 0
    errors: List[str] = []

    # ---- 0.3：LLM 精读层统计（成本透明）----
    llm_pages_analyzed: int = 0              # LLM 精读的页面数
    llm_records_extracted: int = 0           # LLM 抽出的记录数
    llm_rejected: int = 0                    # LLM 拒绝（无关页面）数
    llm_calls: int = 0
    llm_total_tokens: int = 0
    llm_estimated_cost_rmb: float = 0.0


class PriceAnalysis(BaseModel):
    comparables: List[Comparable] = []
    observed_market_range: Optional[str] = None
    ai_fair_range_low: Optional[float] = None
    ai_fair_range_high: Optional[float] = None
    ai_opening_offer: Optional[float] = None
    ai_max_price: Optional[float] = None
    confidence: int = 0
    notes: List[str] = []


class NegotiationStrategy(BaseModel):
    asking_price: Optional[float] = None
    currency: str = "EUR"
    opening_offer: Optional[float] = None
    target_low: Optional[float] = None
    target_high: Optional[float] = None
    walk_away: Optional[float] = None
    reason_zh: str = ""
    rationale_en: str = ""
    email_draft_en: str = ""


class AnalysisInput(BaseModel):
    artist: str                              # 艺术家 / Maker
    artwork: str = ""                        # 作品 / Model
    asking_price: Optional[float] = None
    currency: str = "EUR"
    year: Optional[str] = None
    medium: Optional[str] = None
    dimensions: Optional[str] = None
    seller: Optional[str] = None
    notes: str = ""
    comparables: List[Comparable] = []

    # ---- 0.2 ----
    category: str = "ART"
    research: bool = False                   # 勾选后主动联网搜索
    mileage: Optional[str] = None            # CLASSIC_CAR
    color: Optional[str] = None
    options: Optional[str] = None            # 选装代码（如 003 Carrera RS Competition）

    # ---- 0.4：Before Analysis（点 ANALYZE 前记录，锁定不可改）----
    initial_decision: Optional[str] = None   # BUY / NEGOTIATE / WATCH / PASS / UNSURE
    initial_expected_fair: Optional[float] = None
    initial_planned_offer: Optional[float] = None


# ---- 0.4：Decision 四态 + 风险分级 ----
DECISION_BUY = "BUY"
DECISION_NEGOTIATE = "NEGOTIATE"
DECISION_WATCH = "WATCH"
DECISION_PASS = "PASS"
DECISIONS = [DECISION_BUY, DECISION_NEGOTIATE, DECISION_WATCH, DECISION_PASS]

SEV_CRITICAL = "CRITICAL"
SEV_HIGH = "HIGH"
SEV_MEDIUM = "MEDIUM"
SEV_LOW = "LOW"
_SEV_ORDER = {SEV_CRITICAL: 0, SEV_HIGH: 1, SEV_MEDIUM: 2, SEV_LOW: 3}


class RiskItem(BaseModel):
    """结构化风险：文案 + 严重程度（0.4 用于 Top 3 Risks 排序）。"""
    text: str
    severity: str = SEV_MEDIUM

    @property
    def order(self) -> int:
        return _SEV_ORDER.get(self.severity, 2)


class BriefItem(BaseModel):
    """Top Evidence 条目（可展开到完整 Evidence）。"""
    title: str
    detail: str = ""
    source_url: Optional[str] = None
    source_name: Optional[str] = None
    kind: str = "COMPARABLE"   # COMPARABLE / FACT / USER_CLAIM


class OpportunityBrief(BaseModel):
    """一页老板决策卡（0.4 第二节）。"""
    decision: str = DECISION_WATCH
    asking_price: Optional[float] = None
    currency: str = "EUR"
    fair_range_low: Optional[float] = None
    fair_range_high: Optional[float] = None
    opening_offer: Optional[float] = None
    target_low: Optional[float] = None
    target_high: Optional[float] = None
    walk_away: Optional[float] = None
    confidence: int = 0
    top_evidence: List[BriefItem] = []
    top_risks: List[RiskItem] = []
    next_action: str = ""


class Report(BaseModel):
    id: Optional[int] = None
    created_at: str = Field(default_factory=now_iso)
    input: AnalysisInput
    conclusion: str = "观察"                 # 中文四态：值得买/值得谈/观察/放弃（0.1-0.3 兼容）
    conclusion_reason: str = ""
    artist_analysis: str = ""
    evidence: List[Evidence] = []
    price: PriceAnalysis = Field(default_factory=PriceAnalysis)
    risks: List[RiskItem] = []               # 0.4：结构化风险（text + severity）
    negotiation: Optional[NegotiationStrategy] = None
    research_summary: Optional[ResearchSummary] = None
    llm_used: bool = False
    warnings: List[str] = []

    # ---- 0.4 ----
    decision: str = DECISION_WATCH           # 英文四态（Deterministic Decision Engine 产出）
    brief: Optional[OpportunityBrief] = None


class DecisionMemory(BaseModel):
    """用户真实决策记录（0.4 第七节）。Analyst 字段系统自动写入，不可人工修改。"""
    id: Optional[int] = None
    report_id: int
    created_at: str = Field(default_factory=now_iso)

    # My Initial View（分析前锁定）
    initial_decision: Optional[str] = None
    initial_offer: Optional[float] = None

    # Analyst Recommendation（系统自动记录，不可改）
    analyst_decision: str = DECISION_WATCH
    analyst_opening: Optional[float] = None
    analyst_target_low: Optional[float] = None
    analyst_target_high: Optional[float] = None
    analyst_walkaway: Optional[float] = None

    # My Final Decision（用户填写）
    final_decision: Optional[str] = None     # BUY / NEGOTIATE / WATCH / PASS
    final_offer: Optional[float] = None
    final_transaction_price: Optional[float] = None
    transaction_status: Optional[str] = None # BOUGHT / NOT_BOUGHT / STILL_NEGOTIATING / PASSED

    decision_changed: Optional[str] = None   # YES / NO
    notes: str = ""


# ================================================================ 0.5 Watchlist

ALERT_IGNORE = "IGNORE"
ALERT_INFO = "INFO"
ALERT_INTERESTING = "INTERESTING"
ALERT_ACTION = "ACTION"
ALERT_LEVELS = [ALERT_IGNORE, ALERT_INFO, ALERT_INTERESTING, ALERT_ACTION]

# Watch 事件类型（06 节）
EV_NEW_LISTING = "NEW_LISTING"
EV_PRICE_DROP = "PRICE_DROP"
EV_PRICE_INCREASE = "PRICE_INCREASE"
EV_NEW_SOLD = "NEW_SOLD"
EV_REMOVED = "REMOVED"
EV_INFO_CHANGE = "INFO_CHANGE"

FREQ_MANUAL = "MANUAL"
FREQ_DAILY = "DAILY"
FREQ_WEEKLY = "WEEKLY"

WATCH_STATUS_ACTIVE = "ACTIVE"
WATCH_STATUS_PAUSED = "PAUSED"


class Watch(BaseModel):
    """长期监控对象（与 Opportunity/Report 完全分离，04 节）。"""
    id: Optional[int] = None
    category: str = "ART"
    maker: str = ""
    target: str = ""
    keywords: str = ""               # 逗号分隔：N-GT, NGT, M003, Carrera RS Competition
    price_low: Optional[float] = None
    price_high: Optional[float] = None
    currency: str = "EUR"
    markets: str = "GLOBAL"
    frequency: str = FREQ_MANUAL
    notes: str = ""
    status: str = WATCH_STATUS_ACTIVE
    created_at: str = Field(default_factory=now_iso)
    last_checked: Optional[str] = None
    baseline_at: Optional[str] = None
    # baseline 快照（06 节：Known listings / sold / estimates / median）
    baseline: dict = Field(default_factory=dict)


class WatchItem(BaseModel):
    """一个被监控到的具体对象（Opportunity）。fingerprint 合并转载。"""
    id: Optional[int] = None
    watch_id: int
    fingerprint: str
    title: str
    source_url: str = ""
    source_name: str = ""
    seller: str = ""
    price: Optional[float] = None
    currency: str = "EUR"
    sale_type: str = SALE_UNKNOWN       # SOLD / ASKING / ESTIMATE / UNKNOWN
    year: Optional[str] = None
    attributes: dict = Field(default_factory=dict)
    source_tier: int = 0
    first_seen: str = Field(default_factory=now_iso)
    last_seen: str = Field(default_factory=now_iso)
    # 最近一次 run 的评分（append-only 历史在 watch_events）
    opportunity_score: int = 0
    alert_level: str = ALERT_IGNORE


class WatchEvent(BaseModel):
    """一次变化（append-only，价格历史 325 → 315 → 289 靠它）。"""
    id: Optional[int] = None
    watch_id: int
    item_id: Optional[int] = None
    event_type: str = EV_NEW_LISTING
    old_value: str = ""
    new_value: str = ""
    opportunity_score: int = 0
    alert_level: str = ALERT_IGNORE
    created_at: str = Field(default_factory=now_iso)


class WatchRun(BaseModel):
    """一次 Market Check 的运行记录。"""
    id: Optional[int] = None
    watch_id: int
    started_at: str = Field(default_factory=now_iso)
    finished_at: Optional[str] = None
    mode: str = "BASELINE"              # BASELINE / CHECK
    results_seen: int = 0
    new_items: int = 0
    events: int = 0
    action_alerts: int = 0
    duration_ms: int = 0
    errors: List[str] = []


# ================================================================ 0.6 Scout

SC_FEEDBACK_INTERESTED = "INTERESTED"
SC_FEEDBACK_NOT_INTERESTED = "NOT_INTERESTED"
SC_FEEDBACK_ALREADY_KNOW = "ALREADY_KNOW"
SC_FEEDBACK_WRONG_MATCH = "WRONG_MATCH"

# Candidate 状态
SC_STATUS_NEW = "NEW"                 # 未展示
SC_STATUS_SHOWN = "SHOWN"             # 已展示
SC_STATUS_INTERESTED = "INTERESTED"
SC_STATUS_NOT_INTERESTED = "NOT_INTERESTED"
SC_STATUS_ALREADY_KNOW = "ALREADY_KNOW"
SC_STATUS_WRONG_MATCH = "WRONG_MATCH"
SC_STATUS_MATERIAL_CHANGE = "MATERIAL_CHANGE"  # 曾被拒但价格大幅变化后重新出现


class ScoutProfile(BaseModel):
    """Scout 偏好（2 节：不硬编码，可修改）。"""
    id: Optional[int] = None
    categories: List[str] = ["ART", "CLASSIC_CAR", "DESIGN"]
    budget_low: Optional[float] = 10000
    budget_high: Optional[float] = 500000
    currency: str = "EUR"
    markets: str = "GLOBAL"
    opportunity_types: List[str] = ["UNDERPRICED", "RARE", "NEW_TO_MARKET",
                                     "PRICE_DROP", "POORLY_MARKETED", "UNUSUAL_PROVENANCE"]
    interests: str = ""                # 自然语言：kinetic art / museum-level artists ...
    avoid: str = ""                    # 自然语言：mass-market collectibles ...
    seeds_artists: str = ""            # 逗号分隔：Takis, Calder, Fontana
    seeds_models: str = ""             # 逗号分隔：964 RS, 964 N-GT, F40
    seeds_keywords: str = ""           # 逗号分隔：estate, private collection, no reserve ...
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


class ScoutCandidate(BaseModel):
    """一个被 Scout 发现的候选（6 节：六维分数全部确定性）。"""
    id: Optional[int] = None
    run_id: Optional[int] = None
    fingerprint: str
    category: str = "ART"
    maker: str = ""
    object: str = ""
    asking_price: Optional[float] = None
    currency: str = "EUR"
    source_url: str = ""
    source_name: str = ""
    source_tier: int = 0
    sale_type: str = SALE_UNKNOWN

    profile_match: int = 0      # 0-20
    price_anomaly: int = 0      # 0-25
    scarcity: int = 0           # 0-20
    source_quality: int = 0     # 0-15
    freshness: int = 0          # 0-10
    evidence_strength: int = 0  # 0-10
    scout_score: int = 0        # 0-100

    status: str = SC_STATUS_NEW
    notes: str = ""             # WHY YOU'RE SEEING THIS（确定性拼接）
    first_seen: str = Field(default_factory=now_iso)
    last_seen: str = Field(default_factory=now_iso)


class ScoutFeedback(BaseModel):
    """用户反馈（10/11 节：先记录，不自动训练）。"""
    id: Optional[int] = None
    candidate_id: int
    feedback: str = SC_FEEDBACK_INTERESTED
    why_not: str = ""            # WRONG MATCH 子选项
    created_at: str = Field(default_factory=now_iso)


class ScoutRun(BaseModel):
    """一次 Scout 巡视。记录漏斗数字与成本（16 节）。"""
    id: Optional[int] = None
    started_at: str = Field(default_factory=now_iso)
    finished_at: Optional[str] = None
    results_raw: int = 0
    duplicates_removed: int = 0
    known_removed: int = 0
    profile_removed: int = 0
    source_removed: int = 0
    weak_removed: int = 0
    candidates: int = 0
    top_shown: int = 0
    llm_calls: int = 0
    llm_tokens: int = 0
    estimated_cost_rmb: float = 0.0
    duration_ms: int = 0
    errors: List[str] = []
