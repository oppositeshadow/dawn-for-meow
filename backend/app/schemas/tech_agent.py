"""LLM 场景 2 的 Pydantic 防火墙（代码结构稿 §6.3 防火墙 1）：特化科技卡。

LLM 只被允许"填空命题"——名称、风味文案、机制类型与载荷；**数值成本由 Python 夹紧**
（`balance.STAR_TECH_TIER_COSTS`），LLM 无权改成本、改阶梯或改 DAG 结构。
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: 机制类型枚举（与 `tech_records.mechanic_type` 的注释一致）
MECHANIC_TYPES: tuple[str, ...] = ("PASSIVE_BUFF", "CONVERSION", "TRADE_OFF", "UNLOCK_ABILITY")

#: 载荷里不允许出现的键：这些会直接改结算或改结构，必须由 Python 决定
FORBIDDEN_PAYLOAD_KEYS = ("target_cost", "tier", "parent_ids", "tech_id", "status")

_NAME_PATTERN = re.compile(r"[\u4e00-\u9fffA-Za-z0-9·\-]{2,12}")


class TechCard(BaseModel):
    """单张特化科技卡（LLM 输出 → 落库前的最后一道闸）。"""

    model_config = ConfigDict(extra="ignore")

    tech_name: str = Field(min_length=2, max_length=12)
    flavor_text: str = Field(min_length=2, max_length=80)
    mechanic_type: str
    buff_payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tech_name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        text = value.strip()
        if not _NAME_PATTERN.fullmatch(text):
            raise ValueError("tech_name 只能由 2~12 个中英文/数字字符组成")
        return text

    @field_validator("flavor_text")
    @classmethod
    def _check_flavor(cls, value: str) -> str:
        text = value.strip()
        if any(token in text for token in ("http", "```", "<script")):
            raise ValueError("flavor_text 含可疑内容")
        return text

    @field_validator("mechanic_type")
    @classmethod
    def _check_mechanic(cls, value: str) -> str:
        text = value.strip().upper()
        if text not in MECHANIC_TYPES:
            raise ValueError(f"mechanic_type 必须是 {MECHANIC_TYPES} 之一")
        return text

    @field_validator("buff_payload")
    @classmethod
    def _check_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 8:
            raise ValueError("buff_payload 键过多")
        for key in value:
            if key in FORBIDDEN_PAYLOAD_KEYS:
                raise ValueError(f"buff_payload 不允许携带 {key}（数值与结构由后端夹紧）")
        return value


class TechCardBatch(BaseModel):
    """一次生成的整棵特化科技树（≤ 节点数），数量由后端裁剪。"""

    model_config = ConfigDict(extra="ignore")

    cards: list[TechCard] = Field(default_factory=list)
