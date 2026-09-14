"""智械匿名深网服务（模块 I）：行情推进 / 现货与做空 / 黑市空投 / 论坛做局 / 暴露度。

口径来源：数值平衡表 §11、GDD §11、WBS 模块 I。要点：

* 行情用 `core/stock_engine`（Bitburner 模型），离线按 6 秒 tick 批量补算；
* 交易成本 = 固定手续费 5 算力币 + 点差，成交后按"每 100 股收缩 0.006"反噬趋势；
* 做空合约 10 分钟到期，利息 0.1%/分钟，到期强制按市价买回交割（爆仓=保证金全损）；
* 黑市大宗 5 分钟空投送达（在途入库，离线同样生效）；违禁品即买即用（零背包）；
* 玩家发帖由 LLM 裁判煽动星级（Pydantic 防火墙 + 本地关键词兜底），星级 × 0.6% 注入动量；
* 暴露度四段危机：25/50/75/100（手续费上调 / 图灵质询 / 追踪信标 / 封号）。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import balance as B
from app.core.errors import BadRequest, Conflict, InsufficientResource, NotFound
from app.core.stock_engine import (
    advance_market,
    apply_trade_impact,
    initial_quotes,
    short_liquidation_price,
    trade_price,
)
from app.models import ColonyState, DarknetState, ForumPost
from app.models.darknet import ForumSentiment, ReactionPattern
from app.schemas.forum_agent import ForumJudgement
from app.services.game_init_service import now_timestamp
from app.services.llm_service import LlmScene, get_llm_service

logger = logging.getLogger("dawn_meow.darknet")

RESOURCE_PRECISION = 2

#: 黑市大宗通货（围绕基准价 ±30% 波动）
MARKET_ITEMS: dict[str, dict] = {
    "catnip": {"name": "猫薄荷空投箱", "base_price": 1.0, "resource": "catnip", "unit": 50},
    "scrap": {"name": "废铁箱", "base_price": 1.2, "resource": "scrap", "unit": 50},
    "chips": {"name": "芯片匣", "base_price": 12.0, "resource": "chips", "unit": 5},
    "battery": {"name": "高能电池组", "base_price": 30.0, "resource": "battery", "unit": 2},
}

#: 违禁品（即买即用，无背包）
CONTRABAND: dict[str, dict] = {
    "burner_card": {"name": "洗白卡", "price": 120.0, "effect": "EXPOSURE_ZERO"},
    "seed_capsule": {"name": "种子胶囊", "price": 90.0, "effect": "SEED_UNLOCK"},
    "armor_plate": {"name": "装甲模块", "price": 150.0, "effect": "ARMOR_PLUS"},
    "high_grade_lube": {"name": "高阶机油", "price": 80.0, "effect": "LUBE_EXPORT"},
    "antique_disc": {"name": "古董光盘", "price": 200.0, "effect": "TECH_HINT"},
}

FORUM_KEYWORDS = {
    "BULL": ("炸膛", "停产", "利好", "缺货", "抢购", "暴涨", "投产", "中标"),
    "BEAR": ("爆炸", "事故", "宕机", "暴跌", "清算", "召回", "停摆", "破产"),
}


async def _darknet(session: AsyncSession, slot_id: int) -> DarknetState:
    row = await session.get(DarknetState, slot_id)
    if row is None:
        raise NotFound("DARKNET_NOT_FOUND", f"槽位 {slot_id} 没有深网状态")
    return row


def _stock(darknet: DarknetState, stock_id: str) -> dict[str, Any]:
    for quote in darknet.stocks_data or []:
        if quote.get("stock_id") == stock_id:
            return dict(quote)
    raise BadRequest("BAD_REQUEST", f"未知标的 stock_id={stock_id}")


def _write_stocks(darknet: DarknetState, stocks: list[dict[str, Any]]) -> None:
    # JSON 列必须整条替换（就地改 dict 不会被 SQLAlchemy 侦测到）
    darknet.stocks_data = [dict(stock) for stock in stocks]


def exposure_band(exposure: float) -> str:
    if exposure >= B.EXPOSURE_BAND_BAN:
        return "BANNED"
    if exposure >= B.EXPOSURE_BAND_TRACKER[0]:
        return "TRACKED"
    if exposure >= B.EXPOSURE_BAND_INTERROGATION[0]:
        return "INTERROGATION"
    if exposure >= B.EXPOSURE_BAND_FEE[0]:
        return "SURCHARGED"
    return "CLEAN"


def fee_multiplier(exposure: float) -> float:
    band = exposure_band(exposure)
    if band in ("SURCHARGED", "INTERROGATION", "TRACKED", "BANNED"):
        return B.EXPOSURE_FEE_MULTIPLIER
    return 1.0


async def state_view(session: AsyncSession, *, slot_id: int = B.DEFAULT_SLOT_ID) -> dict[str, Any]:
    darknet = await _darknet(session, slot_id)
    posts = (
        await session.execute(
            select(ForumPost).where(ForumPost.slot_id == slot_id).order_by(ForumPost.id.desc()).limit(20)
        )
    ).scalars().all()
    return {
        "byte_credits": round(float(darknet.byte_credits), RESOURCE_PRECISION),
        "exposure": round(float(darknet.exposure), 2),
        "exposure_band": exposure_band(float(darknet.exposure)),
        "burner_id": darknet.burner_id,
        "has_4s_data": bool(darknet.has_4s_data),
        "last_post_time": darknet.last_post_time,
        "post_cooldown_left": max(0, int(darknet.last_post_time or 0) + B.FORUM_POST_COOLDOWN_SECONDS - now_timestamp()),
        "stocks": [
            {
                **quote,
                "ask": trade_price(quote, "BUY"),
                "bid": trade_price(quote, "SELL"),
                "trend_strength": float(quote.get("trend_strength", 0.0)),
            }
            for quote in (darknet.stocks_data or [])
        ],
        "positions": list(darknet.positions or []),
        "short_contracts": list(darknet.short_contracts or []),
        "pending_deliveries": list(darknet.pending_deliveries or []),
        "whale_events": list(darknet.whale_events or [])[-10:],
        "market_items": {
            item_id: {**spec, "current_price": round(float(spec["base_price"]) * (1 + 0.1), 2)}
            for item_id, spec in MARKET_ITEMS.items()
        },
        "contraband": CONTRABAND,
        "forum": [
            {
                "id": post.id,
                "author_id": post.author_id,
                "is_player": bool(post.is_player),
                "content": post.content,
                "telemetry_code": post.telemetry_code,
                "author_post_count": int(post.author_post_count),
                "author_hit_rate": float(post.author_hit_rate),
                "target_stock": post.target_stock,
                "sentiment": post.sentiment.value if post.sentiment else None,
                "persuasiveness": post.persuasiveness,
                "reaction_pattern": post.reaction_pattern.value if post.reaction_pattern else None,
            }
            for post in posts
        ],
    }


async def advance_darknet(
    session: AsyncSession,
    *,
    slot_id: int = B.DEFAULT_SLOT_ID,
    planet_id: int = B.HOME_PLANET_ID,
    seconds: float,
    now: int | None = None,
) -> dict[str, Any]:
    """离线推进：行情批量 tick、现货/做空到期结算、空投送达、暴露度。"""
    if seconds <= 0:
        return {"ticks": 0, "events": []}
    darknet = await _darknet(session, slot_id)
    now = now or now_timestamp()
    events: list[str] = []
    colony = await session.get(ColonyState, (slot_id, planet_id))

    result = advance_market(darknet.stocks_data or [], seconds=seconds, seed=slot_id * 1000 + now)
    _write_stocks(darknet, result["stocks"])
    darknet.last_tick_time = now

    # 做空合约到期：按市价强制买回交割（利息 0.1%/分钟），或提前爆仓
    contracts = [dict(item) for item in (darknet.short_contracts or [])]
    open_contracts: list[dict[str, Any]] = []
    for contract in contracts:
        quote = next((q for q in result["stocks"] if q["stock_id"] == contract.get("stock_id")), None)
        if quote is None:
            continue
        price = float(quote["price"])
        entry = float(contract.get("entry_price", price))
        shares = float(contract.get("shares", 0.0))
        leverage = int(contract.get("leverage", 1))
        profit = (entry - price) * shares
        interest = entry * shares * B.SHORT_INTEREST_PER_MINUTE * (seconds / 60.0)
        liquidated = price >= float(contract.get("liquidation_price", 0.0) or 0) > 0
        expired = now >= int(contract.get("ends_at", now))
        if liquidated or expired:
            darknet.byte_credits = round(float(darknet.byte_credits) + profit - interest, RESOURCE_PRECISION)
            events.append(
                f"做空 {contract.get('stock_id')} {'爆仓强平' if liquidated else '到期交割'}："
                f"{'盈利' if profit >= 0 else '亏损'} {abs(profit):.1f} 算力币，利息 {interest:.1f}"
            )
        else:
            open_contracts.append(contract)
    darknet.short_contracts = open_contracts

    # 在途空投送达
    deliveries = [dict(item) for item in (darknet.pending_deliveries or [])]
    remaining: list[dict[str, Any]] = []
    for item in deliveries:
        if now >= int(item.get("ends_at", now)):
            if colony is not None and item.get("resource"):
                cap = float(getattr(colony, f"{item['resource']}_max", 0.0))
                before = float(getattr(colony, item["resource"], 0.0))
                after = min(cap, before + float(item.get("qty", 0.0)))
                setattr(colony, item["resource"], round(after, RESOURCE_PRECISION))
            events.append(f"黑市空投送达：{item.get('name', item.get('item_id'))} ×{item.get('qty')}")
        else:
            remaining.append(item)
    darknet.pending_deliveries = remaining

    await session.flush()
    return {"ticks": result["ticks"], "events": events[-10:]}


async def trade_stock(
    session: AsyncSession,
    *,
    stock_id: str,
    action: str,
    shares: float,
    slot_id: int = B.DEFAULT_SLOT_ID,
) -> dict[str, Any]:
    """现货做多：BUY_LONG / SELL_LONG（固定手续费 5 算力币 + 点差 + 交易反噬）。"""
    darknet = await _darknet(session, slot_id)
    action = action.upper()
    if action not in ("BUY_LONG", "SELL_LONG"):
        raise BadRequest("BAD_REQUEST", f"未知交易动作 {action}（合法值：BUY_LONG / SELL_LONG；平仓用 SELL_LONG）")
    stocks = [dict(quote) for quote in (darknet.stocks_data or [])]
    index = next((i for i, quote in enumerate(stocks) if quote["stock_id"] == stock_id), None)
    if index is None:
        raise BadRequest("BAD_REQUEST", f"未知标的 stock_id={stock_id}")
    quote = stocks[index]

    price = trade_price(quote, "BUY" if action == "BUY_LONG" else "SELL")
    # 政令【罐罐外交】黑市手续费 −20%（《数值平衡表》§15.2）
    from app.services import doctrine_service

    doctrine = await doctrine_service.active_effects(session, slot_id)
    fee = (
        B.STOCK_TRADE_FEE_BYTES
        * fee_multiplier(float(darknet.exposure))
        * (1.0 + min(0.0, float(doctrine.get("black_market_fee", 0.0))))
    )
    gross = price * float(shares)
    if action == "BUY_LONG":
        total = gross + fee
        if float(darknet.byte_credits) < total:
            raise InsufficientResource(detail=f"算力币不足：需要 {total:.1f}，当前 {float(darknet.byte_credits):.1f}")
        darknet.byte_credits = round(float(darknet.byte_credits) - total, RESOURCE_PRECISION)
        positions = [dict(item) for item in (darknet.positions or [])]
        positions.append(
            {"stock_id": stock_id, "shares": float(shares), "entry_price": price, "opened_at": now_timestamp()}
        )
        darknet.positions = positions
    else:
        positions = [dict(item) for item in (darknet.positions or [])]
        held = sum(float(item["shares"]) for item in positions if item["stock_id"] == stock_id)
        if held < float(shares):
            raise InsufficientResource(detail=f"持仓不足：持有 {held:g} 股，想卖 {shares:g} 股")
        left = float(shares)
        kept: list[dict[str, Any]] = []
        for item in positions:
            if item["stock_id"] == stock_id and left > 0:
                take = min(left, float(item["shares"]))
                item["shares"] = float(item["shares"]) - take
                left -= take
                if item["shares"] > 0:
                    kept.append(item)
            else:
                kept.append(item)
        darknet.positions = kept
        darknet.byte_credits = round(float(darknet.byte_credits) + gross - fee, RESOURCE_PRECISION)

    stocks[index] = apply_trade_impact(quote, float(shares), side="BUY" if action == "BUY_LONG" else "SELL")
    _write_stocks(darknet, stocks)
    await session.commit()
    return {
        "stock_id": stock_id,
        "action": action,
        "shares": float(shares),
        "price": price,
        "fee": round(fee, 2),
        "byte_credits": round(float(darknet.byte_credits), RESOURCE_PRECISION),
        "trend_strength": stocks[index]["trend_strength"],
    }


async def open_short(
    session: AsyncSession,
    *,
    stock_id: str,
    shares: float,
    leverage: int,
    slot_id: int = B.DEFAULT_SLOT_ID,
) -> dict[str, Any]:
    """借券做空：10 分钟合约 + 0.1%/分钟利息 + 1~10x 杠杆（附爆仓价）。"""
    darknet = await _darknet(session, slot_id)
    if not (B.SHORT_LEVERAGE_MIN <= leverage <= B.SHORT_LEVERAGE_MAX):
        raise BadRequest("BAD_REQUEST", f"杠杆必须在 {B.SHORT_LEVERAGE_MIN}~{B.SHORT_LEVERAGE_MAX} 之间")
    stocks = [dict(quote) for quote in (darknet.stocks_data or [])]
    index = next((i for i, quote in enumerate(stocks) if quote["stock_id"] == stock_id), None)
    if index is None:
        raise BadRequest("BAD_REQUEST", f"未知标的 stock_id={stock_id}")
    quote = stocks[index]

    price = trade_price(quote, "SELL")
    proceeds = price * float(shares)
    margin = proceeds / leverage
    from app.services import doctrine_service

    doctrine = await doctrine_service.active_effects(session, slot_id)
    fee = (
        B.STOCK_TRADE_FEE_BYTES
        * fee_multiplier(float(darknet.exposure))
        * (1.0 + min(0.0, float(doctrine.get("black_market_fee", 0.0))))
    )
    if float(darknet.byte_credits) < margin + fee:
        raise InsufficientResource(
            detail=f"保证金不足：需要 {margin + fee:.1f}（{leverage}x），当前 {float(darknet.byte_credits):.1f}"
        )
    darknet.byte_credits = round(float(darknet.byte_credits) + proceeds - margin - fee, RESOURCE_PRECISION)
    now = now_timestamp()
    contracts = [dict(item) for item in (darknet.short_contracts or [])]
    contract = {
        "stock_id": stock_id,
        "shares": float(shares),
        "entry_price": price,
        "leverage": int(leverage),
        "margin": round(margin, RESOURCE_PRECISION),
        "liquidation_price": short_liquidation_price(price, leverage),
        "opened_at": now,
        "ends_at": now + int(B.SHORT_CONTRACT_SECONDS),
    }
    contracts.append(contract)
    darknet.short_contracts = contracts
    stocks[index] = apply_trade_impact(quote, float(shares), side="SELL")
    _write_stocks(darknet, stocks)
    await session.commit()
    return contract


async def market_trade(
    session: AsyncSession,
    *,
    item_id: str,
    side: str,
    qty: float,
    slot_id: int = B.DEFAULT_SLOT_ID,
    planet_id: int = B.HOME_PLANET_ID,
) -> dict[str, Any]:
    """黑市：大宗通货（5 分钟空投送达）与违禁品（即买即用）。"""
    darknet = await _darknet(session, slot_id)
    side = side.upper()
    now = now_timestamp()
    colony = await session.get(ColonyState, (slot_id, planet_id))

    if item_id in CONTRABAND:
        spec = CONTRABAND[item_id]
        if side != "BUY":
            raise BadRequest("BAD_REQUEST", "违禁品只能买入（即买即用）")
        price = float(spec["price"]) * fee_multiplier(float(darknet.exposure))
        if float(darknet.byte_credits) < price:
            raise InsufficientResource(detail=f"算力币不足：需要 {price:.1f}")
        darknet.byte_credits = round(float(darknet.byte_credits) - price, RESOURCE_PRECISION)
        effect = spec["effect"]
        if effect == "EXPOSURE_ZERO":
            darknet.exposure = 0.0
        elif effect == "ARMOR_PLUS" and colony is not None:
            colony.chips = round(min(float(colony.chips_max), float(colony.chips) + 2), RESOURCE_PRECISION)
        elif effect == "LUBE_EXPORT" and colony is not None:
            colony.lube = round(min(float(colony.lube_max), float(colony.lube) + 1), RESOURCE_PRECISION)
        # 洗白卡本身就是清暴露度的工具，不再叠加走私暴露度
        if effect != "EXPOSURE_ZERO":
            gain = (
                B.SMUGGLING_EXPOSURE_GAIN_MIN
                + (B.SMUGGLING_EXPOSURE_GAIN_MAX - B.SMUGGLING_EXPOSURE_GAIN_MIN) / 2
            )
            darknet.exposure = round(min(100.0, float(darknet.exposure) + gain), 2)
        await session.commit()
        return {"item_id": item_id, "name": spec["name"], "effect": effect, "exposure": darknet.exposure}

    spec = MARKET_ITEMS.get(item_id)
    if spec is None:
        raise BadRequest("BAD_REQUEST", f"未知黑市商品 item_id={item_id}")
    price = round(float(spec["base_price"]) * (1 + 0.1) * float(qty), RESOURCE_PRECISION)
    if side == "BUY":
        if float(darknet.byte_credits) < price:
            raise InsufficientResource(detail=f"算力币不足：需要 {price:.1f}")
        darknet.byte_credits = round(float(darknet.byte_credits) - price, RESOURCE_PRECISION)
        deliveries = [dict(item) for item in (darknet.pending_deliveries or [])]
        deliveries.append(
            {
                "item_id": item_id,
                "name": spec["name"],
                "resource": spec["resource"],
                "qty": float(spec["unit"]) * float(qty),
                "paid": price,
                "ends_at": now + int(B.BLACK_MARKET_DELIVERY_SECONDS),
            }
        )
        darknet.pending_deliveries = deliveries
        await session.commit()
        return {
            "item_id": item_id,
            "side": side,
            "qty": qty,
            "paid": price,
            "arrives_at": now + int(B.BLACK_MARKET_DELIVERY_SECONDS),
        }

    if colony is None:
        raise BadRequest("BAD_REQUEST", "没有基地状态可用于卖出")
    resource = spec["resource"]
    amount = float(spec["unit"]) * float(qty)
    if float(getattr(colony, resource)) < amount:
        raise InsufficientResource(detail=f"{B.RESOURCE_LABELS.get(resource, resource)} 不足：需要 {amount:g}")
    setattr(colony, resource, round(float(getattr(colony, resource)) - amount, RESOURCE_PRECISION))
    darknet.byte_credits = round(float(darknet.byte_credits) + price, RESOURCE_PRECISION)
    gain = B.SMUGGLING_EXPOSURE_GAIN_MIN
    darknet.exposure = round(min(100.0, float(darknet.exposure) + gain), 2)
    await session.commit()
    return {"item_id": item_id, "side": side, "qty": qty, "earned": price, "exposure": darknet.exposure}


def _keyword_judge(title: str, content: str) -> ForumJudgement:
    """本地关键词兜底裁判（LLM 不可用时使用，玩家零感知）。"""
    text = f"{title} {content}"
    bull = sum(1 for word in FORUM_KEYWORDS["BULL"] if word in text)
    bear = sum(1 for word in FORUM_KEYWORDS["BEAR"] if word in text)
    sentiment = (
        ForumSentiment.BULL if bull > bear else ForumSentiment.BEAR if bear > bull else ForumSentiment.NEUTRAL
    )
    target = None
    for stock_id in B.STARTER_STOCKS:
        if stock_id.lower() in text.lower() or B.STARTER_STOCKS[stock_id]["name"] in text:
            target = stock_id
            break
    stars = min(5, 1 + text.count("!") + text.count("！") + (bull + bear))
    return ForumJudgement(
        target_stock=target,
        sentiment=sentiment,
        persuasiveness=max(1, stars),
        suspicion_risk=min(40, 5 * max(1, stars)),
    )


async def post_rumor(
    session: AsyncSession,
    *,
    title: str,
    content: str,
    slot_id: int = B.DEFAULT_SLOT_ID,
) -> dict[str, Any]:
    """玩家自由发帖做局：LLM 裁判星级 → 注入动量 → 记录暴露度（10 分钟冷却）。"""
    darknet = await _darknet(session, slot_id)
    now = now_timestamp()
    cooldown_left = int(darknet.last_post_time or 0) + B.FORUM_POST_COOLDOWN_SECONDS - now
    if darknet.last_post_time and cooldown_left > 0:
        raise Conflict("FORUM_POST_COOLDOWN", f"发帖冷却中，还需 {cooldown_left} 秒")
    if float(darknet.exposure) >= B.EXPOSURE_BAND_BAN:
        raise Conflict("ACCOUNT_BANNED", "账号已被封禁 15 分钟，无法发帖")

    service = get_llm_service()
    system = (
        "你是《喵星破晓》智械匿名论坛的语义裁判。判断这条匿名帖会影响哪只股票、看多还是看空、"
        "煽动星级（1~5）以及它给发帖者带来的暴露度风险（0~40）。只输出 JSON。"
    )
    stock_list = "、".join(f"{key}（{spec['name']}）" for key, spec in B.STARTER_STOCKS.items())
    user = (
        f"可选标的：{stock_list}\n"
        f"标题：{title}\n正文：{content}\n"
        '输出：{"target_stock": "FORGE", "sentiment": "BULL", "persuasiveness": 3, "suspicion_risk": 12}'
    )
    result = await service.complete_json(
        scene=LlmScene.FORUM_JUDGE, system=system, user=user, max_tokens=256, temperature=0.6
    )
    judgement = _keyword_judge(title, content)
    source = "FALLBACK"
    if result.ok and isinstance(result.payload, dict):
        try:
            judgement = ForumJudgement.model_validate(result.payload)
            source = "LLM"
        except Exception as exc:  # 防火墙 1 拦截 ⇒ 走本地关键词兜底
            logger.warning("发帖裁判未通过 Pydantic 防火墙：%s", exc)

    momentum = float(judgement.persuasiveness) * B.FORUM_MOMENTUM_PER_STAR
    stocks = [dict(quote) for quote in (darknet.stocks_data or [])]
    if judgement.target_stock:
        for index, quote in enumerate(stocks):
            if quote["stock_id"] == judgement.target_stock:
                direction = 1.0 if judgement.sentiment == ForumSentiment.BULL else -1.0
                if judgement.sentiment == ForumSentiment.NEUTRAL:
                    direction = 0.0
                quote["momentum"] = round(float(quote.get("momentum", 0.0)) + momentum * direction, 4)
                stocks[index] = quote
        _write_stocks(darknet, stocks)

    darknet.exposure = round(min(100.0, float(darknet.exposure) + float(judgement.suspicion_risk)), 2)
    darknet.last_post_time = now
    post = ForumPost(
        slot_id=slot_id,
        author_id=darknet.burner_id,
        is_player=True,
        content=f"{title}｜{content}",
        created_at=now,
        target_stock=judgement.target_stock,
        sentiment=judgement.sentiment,
        persuasiveness=judgement.persuasiveness,
        suspicion_risk=judgement.suspicion_risk,
        momentum_impact=round(momentum, 4),
        reaction_pattern=ReactionPattern.SLOW_BURN,
        is_fake=judgement.target_stock is None,
    )
    session.add(post)
    await session.commit()
    return {
        "source": source,
        "judgement": judgement.model_dump(),
        "momentum_impact": round(momentum, 4),
        "exposure": darknet.exposure,
        "cooldown_seconds": B.FORUM_POST_COOLDOWN_SECONDS,
        "usage": result.usage.to_dict(),
    }


async def forum_feed(
    session: AsyncSession, *, slot_id: int = B.DEFAULT_SLOT_ID, limit: int = 20
) -> dict[str, Any]:
    """论坛帖子流（完全匿名：不显示 IP / 网段，只给遥测码与账号画像两条线索）。"""
    posts = (
        await session.execute(
            select(ForumPost)
            .where(ForumPost.slot_id == slot_id)
            .order_by(ForumPost.created_at.desc())
            .limit(max(1, min(limit, 50)))
        )
    ).scalars().all()
    darknet = await _darknet(session, slot_id)
    whales = list(darknet.whale_events or [])[-5:]
    return {
        "posts": [
            {
                "id": post.id,
                "author_id": post.author_id,
                "is_player": bool(post.is_player),
                "content": post.content,
                "created_at": int(post.created_at),
                "telemetry_code": post.telemetry_code,
                "author_post_count": int(post.author_post_count),
                "author_hit_rate": float(post.author_hit_rate),
                "reaction_pattern": post.reaction_pattern.value if post.reaction_pattern else None,
            }
            for post in posts
        ],
        "whale_events": whales,
        "clues": [
            "线索一·遥测报错码：真料 70% 附带原始机器报错码，假料 15% 也会伪造",
            "线索二·盘口大单抢跑：真料 40% 在帖前 30~120 秒有大额成交，假料 10%",
            "线索三·账号画像：新号（发帖数 < 3）七成是假料；老号命中率 ≥60% 更可信",
        ],
        "note": "本论坛完全匿名：不显示 IP、不显示网段，只有随机代号。",
    }


async def reroll_id(
    session: AsyncSession,
    *,
    custom_id: str | None = None,
    slot_id: int = B.DEFAULT_SLOT_ID,
    planet_id: int = B.HOME_PLANET_ID,
) -> dict[str, Any]:
    """烧录新身份：消耗 10 废铁 + 50 算力币，暴露度归零。"""
    darknet = await _darknet(session, slot_id)
    colony = await session.get(ColonyState, (slot_id, planet_id))
    cost = B.REROLL_ID_COST
    if colony is None or float(colony.scrap) < cost["scrap"]:
        raise InsufficientResource(detail=f"废铁不足：需要 {cost['scrap']:g}")
    if float(darknet.byte_credits) < cost["byte_credits"]:
        raise InsufficientResource(detail=f"算力币不足：需要 {cost['byte_credits']:g}")
    colony.scrap = round(float(colony.scrap) - cost["scrap"], RESOURCE_PRECISION)
    darknet.byte_credits = round(float(darknet.byte_credits) - cost["byte_credits"], RESOURCE_PRECISION)
    darknet.burner_id = (custom_id or f"robot_{now_timestamp() % 100000:05d}")[:64]
    darknet.exposure = 0.0
    await session.commit()
    return {"burner_id": darknet.burner_id, "exposure": 0.0, "cost_paid": cost}


async def buy_data_eye(
    session: AsyncSession, *, slot_id: int = B.DEFAULT_SLOT_ID
) -> dict[str, Any]:
    """4S 深度数据眼：500 算力币，透视趋势强度与二阶预测。"""
    darknet = await _darknet(session, slot_id)
    if darknet.has_4s_data:
        raise Conflict("ALREADY_OWNED", "已经买过 4S 深度数据眼了")
    if float(darknet.byte_credits) < B.STOCK_DATA_EYE_PRICE:
        raise InsufficientResource(detail=f"算力币不足：需要 {B.STOCK_DATA_EYE_PRICE:g}")
    darknet.byte_credits = round(float(darknet.byte_credits) - B.STOCK_DATA_EYE_PRICE, RESOURCE_PRECISION)
    darknet.has_4s_data = True
    await session.commit()
    return {"has_4s_data": True, "byte_credits": round(float(darknet.byte_credits), RESOURCE_PRECISION)}


async def ensure_darknet(session: AsyncSession, slot_id: int) -> DarknetState:
    """兼容老存档：没有深网行时补一份（用 stock_engine 的初始报价）。"""
    row = await session.get(DarknetState, slot_id)
    if row is None:
        row = DarknetState(
            slot_id=slot_id,
            last_tick_time=now_timestamp(),
            stocks_data=initial_quotes(),
        )
        session.add(row)
        await session.flush()
    return row
