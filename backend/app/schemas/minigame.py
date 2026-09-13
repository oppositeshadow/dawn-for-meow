"""小游戏接口契约（模块 O）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MinigameActionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    slot: int = Field(default=1, ge=1, le=3)
    planet_id: int | None = Field(default=None, ge=0, le=3)
    minigame_id: str = Field(min_length=1, max_length=32)
    action: str = Field(default="SUBMIT_GUESS", max_length=32)
    payload: dict[str, Any] = Field(default_factory=dict)


class MinigameEnvelope(BaseModel):
    code: int = 200
    data: dict[str, Any]
