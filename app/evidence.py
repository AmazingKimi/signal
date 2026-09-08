"""证据系统 —— 本项目最高优先级。

规则（不可违反）：
1. 没有来源 = UNVERIFIED
2. 同一 claim 出现不同 value 且各自有来源 = 双方转 CONFLICTING
3. AI 自己推断的数字永远不进 EvidenceStore 的 VERIFIED 状态
"""
import re
from typing import List, Optional

from .models import CONFLICTING, UNVERIFIED, VERIFIED, Evidence, now_iso


def _claim_key(claim: str) -> str:
    """归一化 claim 文本，用于冲突检测。"""
    return re.sub(r"[\s\d\W]+", "", (claim or "").lower())


class EvidenceStore:
    def __init__(self) -> None:
        self._items: List[Evidence] = []
        self._index = {}  # claim_key -> list[Evidence]

    def add(
        self,
        claim: str,
        value: str,
        source_url: Optional[str] = None,
        source_name: Optional[str] = None,
        evidence: Optional[str] = None,
    ) -> Evidence:
        """添加一条事实。来源 URL 是 VERIFIED 的唯一通行证。"""
        if not claim or value is None:
            raise ValueError("claim 和 value 不能为空")

        item = Evidence(
            claim=claim.strip(),
            value=str(value).strip(),
            source_url=(source_url or "").strip() or None,
            source_name=(source_name or "").strip() or None,
            retrieved_at=now_iso(),
            evidence=evidence,
            verification_status=VERIFIED if (source_url or "").strip() else UNVERIFIED,
        )

        key = _claim_key(claim)
        for existing in self._index.get(key, []):
            # 同一事实、不同取值、且双方都有来源 -> 冲突
            if existing.value != item.value and existing.verification_status == VERIFIED \
                    and item.verification_status == VERIFIED:
                existing.verification_status = CONFLICTING
                item.verification_status = CONFLICTING

        self._items.append(item)
        self._index.setdefault(key, []).append(item)
        return item

    @property
    def items(self) -> List[Evidence]:
        return list(self._items)

    def count_by_status(self, status: str) -> int:
        return sum(1 for e in self._items if e.verification_status == status)
