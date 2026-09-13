"""读档接口（代码结构稿 §4.2）。

`GET /game/load` 与 `GET /colony/state` 共用同一套离线补算内核，区别只有两条语义：

* **不自动建档**：槽位为空时报 `404 SAVE_NOT_FOUND`（`/colony/state` 是单机开箱即用的自动建档入口）；
* **读档即激活**：把目标星球设为该槽位的活跃星球（多槽位 + 多星球切换的入口）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import balance as B
from app.core.database import get_session
from app.core.errors import BadRequest, NotFound
from app.models import PlanetState, SaveSlot
from app.schemas.colony import ColonyStateEnvelope
from app.services import colony_service, planet_service

router = APIRouter(tags=["game"])


@router.get("/game/load", response_model=ColonyStateEnvelope)
async def get_game_load(
    slot: int = Query(default=1, ge=1, le=3, description="存档槽位 1~3"),
    planet_id: int | None = Query(default=None, ge=0, le=3, description="目标星球，缺省取存档的活跃星球"),
    session: AsyncSession = Depends(get_session),
) -> ColonyStateEnvelope:
    # 读档语义的第 3 条：**不假装**——先把基地行建好再读档（《数值平衡表》§15.3 第①步）。
    # 建行是幂等的：已有基地行则不动，锚点只在首次建行时写"解锁那一刻"。
    save = await session.get(SaveSlot, slot)
    if save is None:
        raise NotFound("SAVE_NOT_FOUND", f"槽位 {slot} 还没有存档")
    target = int(save.active_planet_id if planet_id is None else planet_id)
    planet = await session.get(PlanetState, (slot, target))
    if planet is None:
        raise BadRequest("BAD_REQUEST", f"未知星球 planet_id={target}")
    if not planet.unlocked:
        raise BadRequest("PLANET_LOCKED", f"【{B.PLANETS.get(target, target)}】尚未解锁")
    await planet_service.ensure_star_colony(session, slot, target)

    data = await colony_service.load_state(
        session, slot_id=slot, planet_id=planet_id, create_if_missing=False
    )
    target_planet = int(data["planet_id"])

    # 读档即激活该星球（与 /planet/switch 同语义，但不需要先解锁检查——load_state 已经校验过）
    rows = (
        await session.execute(select(PlanetState).where(PlanetState.slot_id == slot))
    ).scalars().all()
    for row in rows:
        row.is_active = row.planet_id == target_planet
    save = await session.get(SaveSlot, slot)
    if save is not None and save.active_planet_id != target_planet:
        save.active_planet_id = target_planet
    # LLM 场景 1/2：登录外星球时补生态标签与特化科技树（幂等：已生成则 0 Token）
    if target_planet != B.HOME_PLANET_ID:
        await planet_service.ensure_biome(session, slot, target_planet)
    await session.commit()
    return ColonyStateEnvelope(code=200, data=data)
