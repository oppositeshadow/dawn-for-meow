"""公频电台接口（模块 M）。

* `GET  /radio/feed`    —— 取一段公频播报（本地填槽 + LRU，0 Token）
* `POST /radio/generate` —— 时代语料批处理（LLM 场景 3；失败/超预算自动走兜底池）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.services import radio_service

router = APIRouter(tags=["radio"])


class RadioGenerateRequest(BaseModel):
    slot: int = Field(default=1, ge=1, le=3)
    phase_id: str = radio_service.DEFAULT_PHASE
    count: int = Field(default=radio_service.DEFAULT_BATCH_COUNT, ge=1, le=200)


@router.get("/radio/feed")
async def get_radio_feed(
    slot: int = Query(default=1, ge=1, le=3),
    phase_id: str = Query(default=radio_service.DEFAULT_PHASE),
    limit: int = Query(default=12, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
) -> dict:
    data = await radio_service.feed(session, slot_id=slot, phase_id=phase_id, limit=limit)
    return {"code": 200, "data": data}


@router.post("/radio/generate")
async def post_radio_generate(
    payload: RadioGenerateRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    data = await radio_service.generate_batch(
        session, slot_id=payload.slot, phase_id=payload.phase_id, count=payload.count
    )
    return {"code": 200, "data": data}
