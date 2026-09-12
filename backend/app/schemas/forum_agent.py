"""LLM 论坛做局裁判的 Pydantic 边界防火墙（代码结构稿 §6.3 防火墙 1）。"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ForumSentiment(str, Enum):
    BULL = "BULL"
    BEAR = "BEAR"
    NEUTRAL = "NEUTRAL"


class ForumJudgement(BaseModel):
    """LLM 对玩家发帖的语义裁判结果（参数全部夹死在安全区间）。"""

    model_config = ConfigDict(extra="ignore")

    target_stock: str | None = Field(default=None, max_length=32)
    sentiment: ForumSentiment = ForumSentiment.NEUTRAL
    persuasiveness: int = Field(default=1, ge=1, le=5, description="煽动星级 1~5")
    suspicion_risk: int = Field(default=0, ge=0, le=40, description="判定增加的暴露度")

    @field_validator("target_stock")
    @classmethod
    def normalize_stock(cls, value: str | None) -> str | None:
        return value.strip().upper() if value else None


class ForumPostRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    slot: int = Field(default=1, ge=1, le=3)
    title: str = Field(min_length=1, max_length=60)
    content: str = Field(min_length=2, max_length=500)


class StockTradeRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    slot: int = Field(default=1, ge=1, le=3)
    stock_id: str = Field(min_length=1, max_length=16)
    action: str = Field(description="BUY_LONG / SELL_LONG")
    shares: float = Field(gt=0, le=100000)


class ShortRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    slot: int = Field(default=1, ge=1, le=3)
    stock_id: str = Field(min_length=1, max_length=16)
    shares: float = Field(gt=0, le=100000)
    leverage: int = Field(default=2, ge=1, le=10)


class MarketTradeRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    slot: int = Field(default=1, ge=1, le=3)
    item_id: str = Field(min_length=1, max_length=32)
    side: str = Field(description="BUY / SELL")
    qty: float = Field(gt=0, le=10000)


class RerollIdRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    slot: int = Field(default=1, ge=1, le=3)
    custom_id: str | None = Field(default=None, max_length=64)
