"""生涯统计与成就接口（模块 N3，代码结构稿 §4.9）。

* `GET /stats/career`       —— 生涯统计大盘
* `GET /stats/achievements` —— 成就徽章进度与解锁时间（读取时顺带结算进度）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.schemas.military import MilitaryEnvelope
from app.services import stats_service

router = APIRouter(tags=["stats"])


@router.get("/stats/career", response_model=MilitaryEnvelope)
async def get_career(
    slot: int = Query(default=1, ge=1, le=3),
    session: AsyncSession = Depends(get_session),
) -> MilitaryEnvelope:
    data = await stats_service.career_view(session, slot_id=slot)
    return MilitaryEnvelope(code=200, data=data)


@router.get("/stats/achievements", response_model=MilitaryEnvelope)
async def get_achievements(
    slot: int = Query(default=1, ge=1, le=3),
    session: AsyncSession = Depends(get_session),
) -> MilitaryEnvelope:
    data = await stats_service.achievements_view(session, slot_id=slot)
    return MilitaryEnvelope(code=200, data=data)
