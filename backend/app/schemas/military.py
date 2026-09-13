"""军备与远征接口契约（模块 G）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class VehicleAssembleRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    slot: int = Field(default=1, ge=1, le=3)
    planet_id: int = Field(default=0, ge=0, le=3)
    unit_type: str = Field(min_length=1, max_length=32)
    nickname: str | None = Field(default=None, max_length=32)


class VehicleModifyRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    slot: int = Field(default=1, ge=1, le=3)
    planet_id: int = Field(default=0, ge=0, le=3)
    unit_id: int
    nickname: str | None = Field(default=None, max_length=32)
    action: str | None = Field(default=None, description="SCRAP = 退役拆解（返还 50% 材料）")
    module_id: str | None = Field(default=None, max_length=32, description="EQUIP / UNEQUIP 时的模块 ID")


class VehicleRepairRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    slot: int = Field(default=1, ge=1, le=3)
    planet_id: int = Field(default=0, ge=0, le=3)
    unit_id: int


class ExpeditionDispatchRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    slot: int = Field(default=1, ge=1, le=3)
    planet_id: int = Field(default=0, ge=0, le=3)
    target_id: str = Field(min_length=1, max_length=32)
    unit_ids: list[int] = Field(min_length=1, max_length=8)


class ExpeditionCollectRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    slot: int = Field(default=1, ge=1, le=3)
    planet_id: int = Field(default=0, ge=0, le=3)
    expedition_id: str = Field(min_length=1, max_length=64)


class MilitaryEnvelope(BaseModel):
    code: int = 200
    data: dict[str, Any]


class TacticalActionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    slot: int = Field(default=1, ge=1, le=3)
    planet_id: int = Field(default=0, ge=0, le=3)
    command: str = Field(description="OVERCLOCK / EMP / EJECT")


class AmbushConvoyRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    slot: int = Field(default=1, ge=1, le=3)
    planet_id: int = Field(default=0, ge=0, le=3)
    unit_ids: list[int] = Field(min_length=1, max_length=8)


class MissileRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    slot: int = Field(default=1, ge=1, le=3)
    planet_id: int = Field(default=0, ge=0, le=3)


class RaidInterceptRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    slot: int = Field(default=1, ge=1, le=3)
    planet_id: int = Field(default=0, ge=0, le=3)
    unit_ids: list[int] = Field(min_length=1, max_length=8)


class FinalAssaultRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    slot: int = Field(default=1, ge=1, le=3)
    planet_id: int = Field(default=0, ge=0, le=3)
    stage: int = Field(ge=1, le=3)
    unit_ids: list[int] = Field(min_length=1, max_length=8)
