"""科技研发接口（模块 E，代码结构稿 §4.4）。

* `GET  /tech/tree`     —— 19 节点 DAG 面板数据（本次新增接口）
* `POST /tech/research` —— 开始研发（防火墙 2：DAG 前置校验 + 单线程研发）
* `POST /tech/reroll`   —— 手动重 Roll（仅 LLM 生成的节点开放）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import balance as B
from app.core.database import get_session
from app.schemas.tech import (
    TechActionEnvelope,
    TechRerollRequest,
    TechResearchRequest,
    TechTreeEnvelope,
)
from app.services import tech_service

router = APIRouter(tags=["tech"])


@router.get("/tech/tree", response_model=TechTreeEnvelope)
async def get_tech_tree(
    slot: int = Query(default=1, ge=1, le=3),
    planet_id: int = Query(default=B.HOME_PLANET_ID, ge=0, le=3),
    session: AsyncSession = Depends(get_session),
) -> TechTreeEnvelope:
    data = await tech_service.tree(session, slot_id=slot, planet_id=planet_id)
    return TechTreeEnvelope(code=200, data=data)


@router.post("/tech/research", response_model=TechActionEnvelope)
async def post_tech_research(
    payload: TechResearchRequest,
    session: AsyncSession = Depends(get_session),
) -> TechActionEnvelope:
    data = await tech_service.start_research(
        session, payload.tech_id, slot_id=payload.slot, planet_id=payload.planet_id
    )
    return TechActionEnvelope(code=200, data=data)


@router.post("/tech/reroll", response_model=TechActionEnvelope)
async def post_tech_reroll(
    payload: TechRerollRequest,
    session: AsyncSession = Depends(get_session),
) -> TechActionEnvelope:
    data = await tech_service.reroll_tech(
        session, payload.tech_id, slot_id=payload.slot, planet_id=payload.planet_id
    )
    return TechActionEnvelope(code=200, data=data)
