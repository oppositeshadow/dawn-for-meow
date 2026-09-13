"""星系法典政令接口（《数值平衡表》§15.2，代码结构稿 §4.11）。

* `GET  /doctrine/list`   —— 政令清单（含是否已点亮、凝聚力是否够、哪些效果已接线）
* `POST /doctrine/unlock` —— 消耗文明凝聚力点亮一条政令
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.schemas.military import MilitaryEnvelope
from app.services import doctrine_service

router = APIRouter(tags=["doctrine"])


class DoctrineUnlockRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    slot: int = Field(default=1, ge=1, le=3)
    doctrine_id: str = Field(min_length=1, max_length=32)


@router.get("/doctrine/list", response_model=MilitaryEnvelope)
async def get_doctrine_list(
    slot: int = Query(default=1, ge=1, le=3),
    session: AsyncSession = Depends(get_session),
) -> MilitaryEnvelope:
    data = await doctrine_service.list_view(session, slot_id=slot)
    return MilitaryEnvelope(code=200, data=data)


@router.post("/doctrine/unlock", response_model=MilitaryEnvelope)
async def post_doctrine_unlock(
    payload: DoctrineUnlockRequest,
    session: AsyncSession = Depends(get_session),
) -> MilitaryEnvelope:
    data = await doctrine_service.unlock(session, payload.doctrine_id, slot_id=payload.slot)
    await session.commit()
    return MilitaryEnvelope(code=200, data=data)
