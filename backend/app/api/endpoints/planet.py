"""星区星图接口（模块 K3，代码结构稿 §4.8）。

* `GET  /planet/state`  —— 星球解锁进度、生态标签、在途航线
* `POST /planet/switch` —— 切换当前操作星球（母星永不删档，永远可用）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import balance as B
from app.core.database import get_session
from app.core.errors import BadRequest
from app.models import PlanetState, SaveSlot
from app.schemas.military import MilitaryEnvelope
from app.services.game_init_service import now_timestamp

router = APIRouter(tags=["planet"])


class PlanetSwitchRequest(BaseModel):
    slot: int = Field(default=1, ge=1, le=3)
    planet_id: int = Field(ge=0, le=3)


@router.get("/planet/state", response_model=MilitaryEnvelope)
async def get_planet_state(
    slot: int = Query(default=1, ge=1, le=3),
    session: AsyncSession = Depends(get_session),
) -> MilitaryEnvelope:
    rows = (
        await session.execute(
            select(PlanetState).where(PlanetState.slot_id == slot).order_by(PlanetState.planet_id)
        )
    ).scalars().all()
    return MilitaryEnvelope(
        code=200,
        data={
            "slot_id": slot,
            "home_planet_id": B.HOME_PLANET_ID,
            "planets": [
                {
                    "planet_id": row.planet_id,
                    "name": B.PLANETS.get(row.planet_id, str(row.planet_id)),
                    "unlocked": bool(row.unlocked),
                    "is_active": bool(row.is_active),
                    "unlocked_at": row.unlocked_at,
                    "biome_tag": row.biome_tag,
                    "logistics_routes": list(row.logistics_routes or []),
                }
                for row in rows
            ],
            "note": "母星不删档：升空后仍作星系后勤大本营（跨星空投航线见 logistics_routes）",
        },
    )


@router.post("/planet/switch", response_model=MilitaryEnvelope)
async def post_planet_switch(
    payload: PlanetSwitchRequest,
    session: AsyncSession = Depends(get_session),
) -> MilitaryEnvelope:
    target = await session.get(PlanetState, (payload.slot, payload.planet_id))
    if target is None:
        raise BadRequest("BAD_REQUEST", f"未知星球 planet_id={payload.planet_id}")
    if not target.unlocked:
        raise BadRequest("PLANET_LOCKED", f"【{B.PLANETS.get(payload.planet_id, payload.planet_id)}】尚未解锁")
    rows = (
        await session.execute(select(PlanetState).where(PlanetState.slot_id == payload.slot))
    ).scalars().all()
    for row in rows:
        row.is_active = row.planet_id == payload.planet_id
    save = await session.get(SaveSlot, payload.slot)
    if save is not None:
        save.active_planet_id = payload.planet_id
    await session.commit()
    return MilitaryEnvelope(
        code=200,
        data={
            "planet_id": payload.planet_id,
            "name": B.PLANETS.get(payload.planet_id, str(payload.planet_id)),
            "switched_at": now_timestamp(),
        },
    )
