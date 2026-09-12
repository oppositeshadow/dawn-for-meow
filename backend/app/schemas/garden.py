"""水培实验室接口契约（模块 H）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GardenActionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    slot: int = Field(default=1, ge=1, le=3)
    planet_id: int = Field(default=0, ge=0, le=3)
    action: str = Field(description="PLANT / HARVEST / MEDIUM / EXPAND / ARM")
    x: int | None = Field(default=None, ge=0, le=6)
    y: int | None = Field(default=None, ge=0, le=6)
    seed_id: str | None = None
    medium: str | None = None
    enabled: bool | None = None
    auto_protect_unknown: bool | None = None


class GardenEnvelope(BaseModel):
    code: int = 200
    data: dict[str, Any]
