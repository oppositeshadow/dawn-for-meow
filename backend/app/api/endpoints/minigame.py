"""星球特色小游戏接口（模块 O，代码结构稿 §4.11）。

* `GET  /minigame/list`   —— 小游戏清单、配额、最好成绩与情报层级
* `POST /minigame/action` —— 小游戏内操作（密电译码：SUBMIT_GUESS）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.schemas.minigame import MinigameActionRequest, MinigameEnvelope
from app.services import minigame_service

router = APIRouter(tags=["minigame"])


@router.get("/minigame/list", response_model=MinigameEnvelope)
async def get_minigame_list(
    slot: int = Query(default=1, ge=1, le=3),
    session: AsyncSession = Depends(get_session),
) -> MinigameEnvelope:
    data = await minigame_service.list_games(session, slot_id=slot)
    return MinigameEnvelope(code=200, data=data)


@router.post("/minigame/action", response_model=MinigameEnvelope)
async def post_minigame_action(
    payload: MinigameActionRequest,
    session: AsyncSession = Depends(get_session),
) -> MinigameEnvelope:
    data = await minigame_service.act(
        session,
        minigame_id=payload.minigame_id,
        action=payload.action,
        payload=payload.payload,
        slot_id=payload.slot,
        planet_id=payload.planet_id,
    )
    return MinigameEnvelope(code=200, data=data)
