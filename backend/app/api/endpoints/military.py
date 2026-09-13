"""战备机库、载具与异步远征接口（模块 G，代码结构稿 §4.5）。

* `GET  /vehicle/list`     —— 逐辆载具的三层血条、模块、乘员与状态
* `POST /vehicle/assemble` —— 组装新车（校验机库机位 / 资源 / 空闲乘员猫）
* `POST /vehicle/modify`   —— 改名 / 退役拆解（返还 50% 材料）
* `POST /vehicle/repair`   —— 维修（扣原造价 50%，耗时 60 秒 × 车型系数）
* `POST /military/dispatch`—— 派遣异步远征（写绝对到期时间戳）
* `POST /military/collect` —— 收取战利品与战报
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import balance as B
from app.core.database import get_session
from app.core.errors import BadRequest
from app.schemas.military import (
    AmbushConvoyRequest,
    FinalAssaultRequest,
    ExpeditionCollectRequest,
    ExpeditionDispatchRequest,
    MilitaryEnvelope,
    MissileRequest,
    RaidInterceptRequest,
    TacticalActionRequest,
    VehicleAssembleRequest,
    VehicleModifyRequest,
    VehicleRepairRequest,
)
from app.services import boss_service, combat_service

router = APIRouter(tags=["military"])


@router.get("/vehicle/list", response_model=MilitaryEnvelope)
async def get_vehicle_list(
    slot: int = Query(default=1, ge=1, le=3),
    planet_id: int = Query(default=B.HOME_PLANET_ID, ge=0, le=3),
    session: AsyncSession = Depends(get_session),
) -> MilitaryEnvelope:
    data = await combat_service.list_hangar(session, slot_id=slot, planet_id=planet_id)
    return MilitaryEnvelope(code=200, data=data)


@router.post("/vehicle/assemble", response_model=MilitaryEnvelope)
async def post_vehicle_assemble(
    payload: VehicleAssembleRequest,
    session: AsyncSession = Depends(get_session),
) -> MilitaryEnvelope:
    data = await combat_service.assemble_vehicle(
        session,
        unit_type=payload.unit_type,
        nickname=payload.nickname,
        slot_id=payload.slot,
        planet_id=payload.planet_id,
    )
    return MilitaryEnvelope(code=200, data=data)


@router.post("/vehicle/modify", response_model=MilitaryEnvelope)
async def post_vehicle_modify(
    payload: VehicleModifyRequest,
    session: AsyncSession = Depends(get_session),
) -> MilitaryEnvelope:
    if payload.action == "SCRAP":
        data = await combat_service.scrap_vehicle(
            session, payload.unit_id, slot_id=payload.slot, planet_id=payload.planet_id
        )
    elif payload.action in {"EQUIP", "UNEQUIP"}:
        if not payload.module_id:
            raise BadRequest("BAD_REQUEST", "装配/拆卸需要提供 module_id")
        handler = (
            combat_service.equip_module if payload.action == "EQUIP" else combat_service.unequip_module
        )
        data = await handler(
            session, payload.unit_id, payload.module_id, slot_id=payload.slot, planet_id=payload.planet_id
        )
        await session.commit()
    else:
        from app.core.errors import NotFound
        from app.models import VehicleUnit

        unit = await session.get(VehicleUnit, payload.unit_id)
        if unit is None:
            raise NotFound("VEHICLE_NOT_FOUND", f"载具 {payload.unit_id} 不存在")
        if payload.nickname:
            unit.nickname = payload.nickname
        await session.commit()
        data = {"unit_id": payload.unit_id, "nickname": unit.nickname}
    return MilitaryEnvelope(code=200, data=data)


@router.post("/vehicle/repair", response_model=MilitaryEnvelope)
async def post_vehicle_repair(
    payload: VehicleRepairRequest,
    session: AsyncSession = Depends(get_session),
) -> MilitaryEnvelope:
    data = await combat_service.repair_vehicle(
        session, payload.unit_id, slot_id=payload.slot, planet_id=payload.planet_id
    )
    return MilitaryEnvelope(code=200, data=data)


@router.post("/military/dispatch", response_model=MilitaryEnvelope)
async def post_military_dispatch(
    payload: ExpeditionDispatchRequest,
    session: AsyncSession = Depends(get_session),
) -> MilitaryEnvelope:
    data = await combat_service.start_expedition(
        session,
        target_id=payload.target_id,
        unit_ids=payload.unit_ids,
        slot_id=payload.slot,
        planet_id=payload.planet_id,
    )
    return MilitaryEnvelope(code=200, data=data)


@router.post("/military/collect", response_model=MilitaryEnvelope)
async def post_military_collect(
    payload: ExpeditionCollectRequest,
    session: AsyncSession = Depends(get_session),
) -> MilitaryEnvelope:
    data = await combat_service.collect_expedition(
        session,
        expedition_id=payload.expedition_id,
        slot_id=payload.slot,
        planet_id=payload.planet_id,
    )
    return MilitaryEnvelope(code=200, data=data)


@router.post("/military/tactical-action", response_model=MilitaryEnvelope)
async def post_tactical_action(
    payload: TacticalActionRequest,
    session: AsyncSession = Depends(get_session),
) -> MilitaryEnvelope:
    data = await combat_service.tactical_action(
        session, command=payload.command, slot_id=payload.slot, planet_id=payload.planet_id
    )
    return MilitaryEnvelope(code=200, data=data)


@router.post("/military/ambush-convoy", response_model=MilitaryEnvelope)
async def post_ambush_convoy(
    payload: AmbushConvoyRequest,
    session: AsyncSession = Depends(get_session),
) -> MilitaryEnvelope:
    data = await combat_service.ambush_convoy(
        session, unit_ids=payload.unit_ids, slot_id=payload.slot, planet_id=payload.planet_id
    )
    return MilitaryEnvelope(code=200, data=data)


@router.post("/military/assemble-missile", response_model=MilitaryEnvelope)
async def post_assemble_missile(
    payload: MissileRequest,
    session: AsyncSession = Depends(get_session),
) -> MilitaryEnvelope:
    data = await combat_service.assemble_missile(
        session, slot_id=payload.slot, planet_id=payload.planet_id
    )
    return MilitaryEnvelope(code=200, data=data)


@router.post("/military/launch-missile", response_model=MilitaryEnvelope)
async def post_launch_missile(
    payload: MissileRequest,
    session: AsyncSession = Depends(get_session),
) -> MilitaryEnvelope:
    data = await combat_service.launch_missile(
        session, slot_id=payload.slot, planet_id=payload.planet_id
    )
    return MilitaryEnvelope(code=200, data=data)


@router.post("/military/intercept-raid", response_model=MilitaryEnvelope)
async def post_intercept_raid(
    payload: RaidInterceptRequest,
    session: AsyncSession = Depends(get_session),
) -> MilitaryEnvelope:
    data = await boss_service.intercept_raid(
        session, unit_ids=payload.unit_ids, slot_id=payload.slot, planet_id=payload.planet_id
    )
    return MilitaryEnvelope(code=200, data=data)


@router.post("/military/final-assault", response_model=MilitaryEnvelope)
async def post_final_assault(
    payload: FinalAssaultRequest,
    session: AsyncSession = Depends(get_session),
) -> MilitaryEnvelope:
    data = await boss_service.final_assault(
        session, stage=payload.stage, unit_ids=payload.unit_ids, slot_id=payload.slot, planet_id=payload.planet_id
    )
    return MilitaryEnvelope(code=200, data=data)
