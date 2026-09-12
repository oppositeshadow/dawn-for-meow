"""LLM 语料批处理的 Pydantic 边界防火墙（代码结构稿 §6.3 防火墙 1）。"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.template_engine import SLOT_NAMES


class TemplateCategory(str, Enum):
    RADIO_NEWS = "RADIO_NEWS"
    BBS_POST = "BBS_POST"
    DISASTER_ALERT = "DISASTER_ALERT"


class TemplateCard(BaseModel):
    """单条语料。参数全部夹死在安全区间内，越界直接判不合格。"""

    model_config = ConfigDict(extra="ignore")

    category: TemplateCategory
    template_text: str = Field(min_length=6, max_length=60)
    impact_stock: str | None = Field(default=None, max_length=32)
    impact_val: float = Field(default=0.0, ge=-0.05, le=0.05)

    @field_validator("template_text")
    @classmethod
    def must_have_slot(cls, value: str) -> str:
        """必须含至少一个槽位（否则本地拼装引擎没法给它加味道）。"""
        if not any(f"{{{name}}}" in value for name in SLOT_NAMES):
            raise ValueError("语料必须包含 {cat} / {building} / {resource} / {stock} 槽位之一")
        return value.strip()

    @field_validator("impact_stock")
    @classmethod
    def normalize_stock(cls, value: str | None) -> str | None:
        return value.strip().upper() if value else None


class TemplateBatch(BaseModel):
    """一批语料（单次批处理上限见 LLM_BATCH_SIZE_MAX，默认 200）。"""

    model_config = ConfigDict(extra="ignore")

    templates: list[TemplateCard] = Field(default_factory=list, max_length=200)
