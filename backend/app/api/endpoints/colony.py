"""基地核心状态与工位调度接口（代码结构稿 §4.3）。

Milestone 1 阶段 1 + 阶段 3（服务端）落地：
* `GET  /colony/state`     —— 完整状态（含离线结算与《离线休整报表》）
* `POST /colony/snapshot`  —— 15 秒静默快照（后端重算 + 对账告警）
* `POST /colony/scavenge`  —— 冷启动手点废墟（+1 废铁，5 次够造第一座纸箱窝）
* `POST /colony/dispatch`  —— 工位调度（工位上限 + 空闲猫口双重校验，可写迟滞换班策略）
* `POST /facilities/build` —— 建造 / 升级设施（造价曲线扣费，第一座纸箱窝带来第一只猫）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.schemas.colony import (
    BuildEnvelope,
    BuildRequest,
    ColonyStateEnvelope,
    DispatchEnvelope,
    DispatchRequest,
    ScavengeEnvelope,
    SnapshotRequest,
    SnapshotResponse,
)
from app.services import colony_service

router = APIRouter(tags=["colony"])


@router.get("/colony/state", response_model=ColonyStateEnvelope)
async def get_colony_state(
    slot: int = Query(default=1, ge=1, le=3, description="存档槽位 1~3"),
    planet_id: int | None = Query(default=None, description="星球 ID，缺省取存档的活跃星球"),
    create_if_missing: bool = Query(
        default=True, description="槽位为空时自动执行新游戏初始化（单机开箱即用）"
    ),
    session: AsyncSession = Depends(get_session),
) -> ColonyStateEnvelope:
    data = await colony_service.load_state(
        session, slot_id=slot, planet_id=planet_id, create_if_missing=create_if_missing
    )
    return ColonyStateEnvelope(code=200, data=data)


@router.post("/colony/snapshot", response_model=SnapshotResponse)
async def post_colony_snapshot(
    payload: SnapshotRequest,
    session: AsyncSession = Depends(get_session),
) -> SnapshotResponse:
    body, _warnings = await colony_service.persist_snapshot(session, payload)
    return SnapshotResponse(**body)


@router.post("/colony/scavenge", response_model=ScavengeEnvelope)
async def post_colony_scavenge(
    slot: int = Query(default=1, ge=1, le=3),
    planet_id: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> ScavengeEnvelope:
    data = await colony_service.manual_scavenge(session, slot_id=slot, planet_id=planet_id)
    return ScavengeEnvelope(code=200, data=data)


@router.post("/colony/dispatch", response_model=DispatchEnvelope)
async def post_colony_dispatch(
    payload: DispatchRequest,
    session: AsyncSession = Depends(get_session),
) -> DispatchEnvelope:
    data = await colony_service.dispatch_labor(
        session,
        role=payload.role,
        delta=payload.delta,
        slot_id=payload.slot,
        planet_id=payload.planet_id,
        policy=payload.policy.model_dump() if payload.policy else None,
    )
    return DispatchEnvelope(code=200, data=data)


@router.post("/facilities/build", response_model=BuildEnvelope)
async def post_facilities_build(
    payload: BuildRequest,
    session: AsyncSession = Depends(get_session),
) -> BuildEnvelope:
    data = await colony_service.build_facility(
        session,
        facility_id=payload.facility_id,
        count=payload.count,
        slot_id=payload.slot,
        planet_id=payload.planet_id,
    )
    return BuildEnvelope(code=200, data=data)
