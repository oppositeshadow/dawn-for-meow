"""智械匿名深网接口（模块 I，代码结构稿 §4.7）。

* `GET  /darknet/state`      —— 大盘 / 持仓 / 做空合约 / 黑市 / 暴露度 / 论坛（本次新增）
* `POST /darknet/stock/trade`—— 现货做多买/卖（手续费 + 点差 + 交易反噬）
* `POST /darknet/short`      —— 借券做空（10 分钟合约 / 利息 / 爆仓价）
* `POST /darknet/market/trade`—— 黑市大宗买卖（5 分钟空投）与违禁品即买即用
* `GET  /darknet/forum/feed` —— 论坛帖子流（完全匿名 + 三条线索）
* `POST /darknet/forum/post` —— 玩家自由发帖做局（LLM 裁判 + 10 分钟冷却）
* `POST /darknet/reroll-id`  —— 烧录新身份洗白暴露度
* `POST /darknet/data-eye`   —— 购买 4S 深度数据眼
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.schemas.forum_agent import (
    ForumPostRequest,
    MarketTradeRequest,
    RerollIdRequest,
    ShortRequest,
    StockTradeRequest,
)
from app.schemas.military import MilitaryEnvelope
from app.services import darknet_service

router = APIRouter(tags=["darknet"])


@router.get("/darknet/state", response_model=MilitaryEnvelope)
async def get_darknet_state(
    slot: int = Query(default=1, ge=1, le=3),
    session: AsyncSession = Depends(get_session),
) -> MilitaryEnvelope:
    await darknet_service.ensure_darknet(session, slot)
    data = await darknet_service.state_view(session, slot_id=slot)
    return MilitaryEnvelope(code=200, data=data)


@router.post("/darknet/stock/trade", response_model=MilitaryEnvelope)
async def post_stock_trade(
    payload: StockTradeRequest,
    session: AsyncSession = Depends(get_session),
) -> MilitaryEnvelope:
    data = await darknet_service.trade_stock(
        session, stock_id=payload.stock_id, action=payload.action, shares=payload.shares, slot_id=payload.slot
    )
    return MilitaryEnvelope(code=200, data=data)


@router.post("/darknet/short", response_model=MilitaryEnvelope)
async def post_short(
    payload: ShortRequest,
    session: AsyncSession = Depends(get_session),
) -> MilitaryEnvelope:
    data = await darknet_service.open_short(
        session, stock_id=payload.stock_id, shares=payload.shares, leverage=payload.leverage, slot_id=payload.slot
    )
    return MilitaryEnvelope(code=200, data=data)


@router.post("/darknet/market/trade", response_model=MilitaryEnvelope)
async def post_market_trade(
    payload: MarketTradeRequest,
    session: AsyncSession = Depends(get_session),
) -> MilitaryEnvelope:
    data = await darknet_service.market_trade(
        session, item_id=payload.item_id, side=payload.side, qty=payload.qty, slot_id=payload.slot
    )
    return MilitaryEnvelope(code=200, data=data)


@router.get("/darknet/forum/feed", response_model=MilitaryEnvelope)
async def get_forum_feed(
    slot: int = Query(default=1, ge=1, le=3),
    limit: int = Query(default=20, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
) -> MilitaryEnvelope:
    await darknet_service.ensure_darknet(session, slot)
    data = await darknet_service.forum_feed(session, slot_id=slot, limit=limit)
    return MilitaryEnvelope(code=200, data=data)


@router.post("/darknet/forum/post", response_model=MilitaryEnvelope)
async def post_forum_post(
    payload: ForumPostRequest,
    session: AsyncSession = Depends(get_session),
) -> MilitaryEnvelope:
    data = await darknet_service.post_rumor(
        session, title=payload.title, content=payload.content, slot_id=payload.slot
    )
    return MilitaryEnvelope(code=200, data=data)


@router.post("/darknet/reroll-id", response_model=MilitaryEnvelope)
async def post_reroll_id(
    payload: RerollIdRequest,
    session: AsyncSession = Depends(get_session),
) -> MilitaryEnvelope:
    data = await darknet_service.reroll_id(session, custom_id=payload.custom_id, slot_id=payload.slot)
    return MilitaryEnvelope(code=200, data=data)


@router.post("/darknet/data-eye", response_model=MilitaryEnvelope)
async def post_data_eye(
    payload: RerollIdRequest,
    session: AsyncSession = Depends(get_session),
) -> MilitaryEnvelope:
    data = await darknet_service.buy_data_eye(session, slot_id=payload.slot)
    return MilitaryEnvelope(code=200, data=data)
