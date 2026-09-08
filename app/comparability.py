"""Comparability Engine —— 判断"这条记录和目标机会到底像不像"（0.2 第六/七/八节）。

核心区分（任务书第六节）：
- Verification（VERIFIED/UNVERIFIED/CONFLICTING）：这条信息是真是假
- Comparability（HIGH/MEDIUM/LOW/NOT_COMPARABLE）：即使是真的，像不像
两者正交：Christie's €280,000 VERIFIED + LOW COMPARABILITY 完全正常。

规则按 Category 分派：
- CLASSIC_CAR：车型代际 / 版本（N-GT, Competition）/ 年份 / 里程 / Option Code
- ART：艺术家 / 系列 / 编号 / 年份
"""
import re
from typing import Optional, Tuple

from .models import (
    HIGH,
    LOW,
    MEDIUM,
    NOT_COMPARABLE,
    AnalysisInput,
    Comparable,
)

# ---------------------------------------------------------------- 归一化

# 常见同义词归一（不能因为网站写 NGT、用户写 N-GT 就漏掉/错配）
_NORMALIZE_MAP = [
    (r"\bn[\s\-/]?gt\b", " n-gt "),
    (r"\bcarrera\s+rs\s+competition\b", " rs-competition "),
    (r"\brs\s+competition\b", " rs-competition "),
    (r"\boption\s*[:#]?\s*m?0*3\b", " option003 "),
    (r"\bm003\b", " option003 "),
]


def normalize_text(s: Optional[str]) -> str:
    s = (s or "").lower()
    for pat, rep in _NORMALIZE_MAP:
        s = re.sub(pat, rep, s)
    return re.sub(r"\s+", " ", s).strip()


def tokens(s: Optional[str]) -> set:
    return set(re.findall(r"[a-z0-9]+", normalize_text(s)))


def _detect(text_norm: str, *keys: str) -> bool:
    return any(k in text_norm for k in keys)


def _year_num(year: Optional[str]) -> Optional[int]:
    m = re.search(r"(19|20)\d{2}", str(year or ""))
    return int(m.group(0)) if m else None


# ---------------------------------------------------------------- CLASSIC_CAR

# 保时捷 911 代际（不同代际 = 不同车型，绝不混算）
_PORSCHE_GENERATIONS = ["964", "993", "996", "997", "991", "992", "930", "g-model"]


def _car_flags(text: str) -> dict:
    return {
        "ngt": _detect(text, "n-gt", "rs-competition", "competition"),
        "rs": _detect(text, "rs"),
        "turbo": _detect(text, "turbo"),
        "cup": _detect(text, "cup", "gt3", "gt2", "rsr"),
    }


def _eval_classic_car(inp: AnalysisInput, rec: Comparable) -> Tuple[str, str]:
    target_norm = normalize_text(f"{inp.artwork} {inp.notes} {inp.options or ''}")
    rec_norm = normalize_text(
        f"{rec.title} {rec.sold_at or ''} {rec.evidence_excerpt or ''} "
        f"{rec.attributes.get('text', '') if rec.attributes else ''}"
    )

    t_gen = next((g for g in _PORSCHE_GENERATIONS if g in target_norm), None)
    r_gen = next((g for g in _PORSCHE_GENERATIONS if g in rec_norm), None)
    t_flags, r_flags = _car_flags(target_norm), _car_flags(rec_norm)

    # 1) 代际不同 -> NOT COMPARABLE（964 和 993 是两台车）
    if t_gen and r_gen and t_gen != r_gen:
        return NOT_COMPARABLE, f"代际不符：目标 {t_gen}，记录 {r_gen}"
    if t_gen and not r_gen:
        # 例外：N/GT（RS Competition）版本历史上仅 964 代生产——
        # 版本命中而代际未标注时按 MEDIUM 处理，不整条丢弃
        if t_flags["ngt"] and r_flags["ngt"]:
            return MEDIUM, "记录未标明代际，但 N/GT 版本仅产自 964 代——按 MEDIUM 处理"
        return NOT_COMPARABLE, "记录未体现目标代际，无法确认车型"

    # 2) 目标是 N-GT/Competition，记录不是 -> 最高 LOW（普通 RS 只是市场背景）
    if t_flags["ngt"] and not r_flags["ngt"]:
        return LOW, "普通 RS（非 N-GT/Competition 版本）——只能作市场背景，不得进入定价"

    # 3) 记录是 N-GT/Competition，目标不是 -> 降级
    if not t_flags["ngt"] and r_flags["ngt"]:
        return LOW, "记录为 N-GT/Competition 版本，规格高于目标"

    # 4) 版本错位（RS vs Turbo vs Cup）-> NOT COMPARABLE
    for k in ("turbo", "cup"):
        if t_flags[k] != r_flags[k] and (t_flags[k] or r_flags[k]):
            return NOT_COMPARABLE, f"版本不符（{k} 标志错位）"

    # 5) 代际一致 + 版本一致 -> 按年份/里程细分级
    t_year, r_year = _year_num(inp.year), _year_num(rec.year)
    reasons = []
    level = MEDIUM
    if t_year and r_year:
        if abs(t_year - r_year) <= 1:
            level = HIGH
            reasons.append("同年/相邻年款")
        elif abs(t_year - r_year) <= 3:
            reasons.append("年款相差 2–3 年")
            level = MEDIUM
        else:
            reasons.append("年款相差较大")
            level = LOW

    # 里程信息（记录有且差距极大 -> 降级）
    t_mile = _parse_mileage(inp.mileage)
    r_mile = _parse_mileage(rec.attributes.get("mileage") if rec.attributes else None)
    if t_mile and r_mile and max(t_mile, r_mile) > 2.5 * min(t_mile, r_mile):
        if level == HIGH:
            level = MEDIUM
        reasons.append(f"里程差距大（目标 {t_mile:,} km / 记录 {r_mile:,} km）")

    if not reasons:
        reasons.append("代际与版本一致")
    return level, "；".join(reasons)


def _parse_mileage(v) -> Optional[int]:
    m = re.search(r"([\d,.\s]+)\s*km", str(v or "").lower())
    if not m:
        return None
    num = re.sub(r"[^\d]", "", m.group(1))
    return int(num) if num else None


# ---------------------------------------------------------------- ART

def _eval_art(inp: AnalysisInput, rec: Comparable) -> Tuple[str, str]:
    target_series = tokens(inp.artwork) - tokens(inp.artist)
    rec_text = tokens(f"{rec.title} {rec.evidence_excerpt or ''}")
    rec_artist = tokens(rec.artist or rec.title)

    artist_hit = tokens(inp.artist) & rec_artist if tokens(inp.artist) else set()
    if tokens(inp.artist) and not artist_hit:
        return NOT_COMPARABLE, "艺术家不符"

    # 系列关键词命中比例（series、编号、主题词）
    overlap = target_series & rec_text if target_series else set()
    ratio = len(overlap) / len(target_series) if target_series else 0

    t_year, r_year = _year_num(inp.year), _year_num(rec.year)

    if ratio >= 0.5:
        if t_year and r_year and abs(t_year - r_year) > 15:
            return MEDIUM, "系列相符但创作时期相差较大"
        return HIGH, "同系列/同主题，关键词高度命中"
    if ratio > 0:
        return MEDIUM, "部分系列关键词命中"
    if artist_hit:
        return LOW, "同艺术家、不同系列——仅作市场背景"
    return NOT_COMPARABLE, "无充分匹配依据"


# ---------------------------------------------------------------- 入口

_EVALUATORS = {
    "CLASSIC_CAR": _eval_classic_car,
    "ART": _eval_art,
}


def evaluate_comparability(inp: AnalysisInput, rec: Comparable) -> Tuple[str, str]:
    """返回 (等级, 机器可读理由)。空 comparability（手动可比）不在此处理——
    手动证据是用户断言，保留 0.1 语义直接进定价池。"""
    fn = _EVALUATORS.get((inp.category or "ART").upper(), _eval_art)
    try:
        level, reason = fn(inp, rec)
    except Exception as exc:  # 可比性评估失败绝不崩溃，降为 NOT_COMPARABLE
        return NOT_COMPARABLE, f"评估失败：{exc}"

    # 量级守卫：研究抽取的价格若与目标报价差一个数量级，
    # 大概率是市场聚合页的总市值/其他车辆数据——最高 LOW，仅作背景
    if inp.asking_price and rec.price and level in (HIGH, MEDIUM):
        ratio = rec.price / inp.asking_price
        if ratio > 3 or ratio < 0.1:
            return LOW, (
                f"价格量级明显偏离目标（记录 {rec.price:,.0f} {rec.currency} vs "
                f"报价 {inp.asking_price:,.0f} {inp.currency}）——疑似聚合页/其他标的噪声"
            )
    return level, reason


# ---- 0.4.1 Evidence Gate：来源等级决定定价资格 ----

# 明确"原始成交"的语言信号（Tier 2 准入门槛）
_SOLD_EVIDENCE_WORDS = (
    "sold for", "sold at", "realized", "hammer", "winning bid", "final bid",
    "sale result", "price incl", "incl. premium", "including premium",
    "成交", "落槌", "含佣金",
)


def _has_explicit_sale_reference(rec: Comparable) -> bool:
    """Tier 2 记录是否"引用了原始成交"（内容层面验证，LLM 理解的结果）。

    口径本身明确（HAMMER/INCLUDING_PREMIUM 意味着看到了成交语言）
    或证据原文含成交关键词，才算"明确原始成交引用"。
    """
    if rec.price_basis in ("HAMMER", "INCLUDING_PREMIUM"):
        return True
    text = f"{rec.evidence_excerpt or ''} {rec.price_basis_reason or ''}".lower()
    return any(w in text for w in _SOLD_EVIDENCE_WORDS)


def classify_pool(rec: Comparable, currency: str) -> Optional[str]:
    """进入定价池的资格判定（0.2 第七节 + 0.4.1 Evidence Gate）。

    必须同时满足：
    1. verified（有 source_url）
    2. 币种可换算（固定汇率表覆盖内——全球市场，USD/GBP 成交按固定汇率折算，不整条丢弃）
    3. sale_type == SOLD（三池分离：挂牌/估价绝不进定价）
    4. 口径明确（price_basis != UNKNOWN——口径不明 = PRICE BASIS UNVERIFIED）
    5. comparability 为空（手动断言）或 HIGH/MEDIUM
    6. **Evidence Gate（0.4.1）**：
       - Tier 3（论坛/新闻/聚合/社交）→ 永远禁止进定价池——只能是线索，不能是成交证据
       - Tier 2（专业数据库/行业媒体）→ 只有明确引用原始成交（口径明确或证据原文含成交语言）才能进
       - Tier 1（官方拍行/制造商/画廊/机构）→ 优先，按现有规则
       - Tier 0（手动可比）→ 用户断言，按现有规则
    7. **来源等级由确定性域名判断，LLM 无权提升**——LLM 只理解内容，不决定来源可信度
    """
    from .models import FX_TO_EUR
    if not rec.verified:
        return None
    if (rec.currency or "EUR").upper() not in FX_TO_EUR:
        return None
    if rec.sale_type != "SOLD":
        return None
    if rec.price_basis == "UNKNOWN":
        return None
    if rec.comparability in ("LOW", NOT_COMPARABLE):
        return None

    # Evidence Gate（0.4.1）
    tier = rec.source_tier or 0
    if tier >= 3:
        return None  # 论坛/聚合/社交：永不作核心价格证据
    if tier == 2 and not _has_explicit_sale_reference(rec):
        return None  # 专业媒体：没有明确原始成交引用就不算成交事实
    return "OK"
