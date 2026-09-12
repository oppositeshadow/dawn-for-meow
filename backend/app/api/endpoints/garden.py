"""猫草水培实验室接口（模块 H，代码结构稿 §4.6）。

* `GET  /garden/state`  —— 7×7 格盘、图鉴、在田光环与扩建造价（本次新增）
* `POST /garden/action` —— 播种 / 采摘 / 换培养液 / 扩建 / 机械臂托管
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import balance as B
from app.core.database import get_session
from app.core.errors import BadRequest
from app.schemas.garden import GardenActionRequest, GardenEnvelope
from app.services import garden_service

router = APIRouter(tags=["garden"])


@router.get("/garden/state", response_model=GardenEnvelope)
async def get_garden_state(
    slot: int = Query(default=1, ge=1, le=3),
    planet_id: int = Query(default=B.HOME_PLANET_ID, ge=0, le=3),
    session: AsyncSession = Depends(get_session),
) -> GardenEnvelope:
    data = await garden_service.state_view(session, slot_id=slot, planet_id=planet_id)
    return GardenEnvelope(code=200, data=data)


@router.post("/garden/action", response_model=GardenEnvelope)
async def post_garden_action(
    payload: GardenActionRequest,
    session: AsyncSession = Depends(get_session),
) -> GardenEnvelope:
    action = payload.action.upper()
    common = {"slot_id": payload.slot, "planet_id": payload.planet_id}

    if action == "PLANT":
        if payload.x is None or payload.y is None or not payload.seed_id:
            raise BadRequest("BAD_REQUEST", "播种需要 x / y / seed_id")
        data = await garden_service.plant_seed(
            session, x=payload.x, y=payload.y, seed_id=payload.seed_id, **common
        )
    elif action == "HARVEST":
        if payload.x is None or payload.y is None:
            raise BadRequest("BAD_REQUEST", "采摘需要 x / y")
        data = await garden_service.harvest(session, x=payload.x, y=payload.y, **common)
    elif action == "MEDIUM":
        if not payload.medium:
            raise BadRequest("BAD_REQUEST", "切换培养液需要 medium")
        data = await garden_service.set_medium(session, payload.medium, **common)
    elif action == "EXPAND":
        data = await garden_service.expand(session, **common)
    elif action == "ARM":
        data = await garden_service.set_mechanical_arm(
            session,
            enabled=payload.enabled,
            auto_protect_unknown=payload.auto_protect_unknown,
            **common,
        )
    else:
        raise BadRequest("BAD_REQUEST", f"未知 action={payload.action}")

    return GardenEnvelope(code=200, data=data)
