"""智械股市推演引擎（模块 I，数值平衡表 §11.1，对齐 Bitburner 官方实现）。

每 6 秒一次 tick，单只股票的演化：

1. **全局冲击** `v = rand(0,1)`：同一 tick **所有股票共用**（全市场共同冲击）；
2. 个股波动率 `volatility ∈ [0.4, 4.0]`；
3. 本 tick 涨跌幅 `av = v × volatility / 100`（对称涨跌，天然不会跌穿 0）；
4. 上涨概率 `chc = 50% ± 趋势强度`（趋势强度 −50~50，即 0~100%）；
5. `price × (1+av)` 或 `price ÷ (1+av)`；
6. 趋势强度演化 `Δ = 趋势强度 × av`，|趋势| < 5 时特殊抬升避免"熄火"；
7. 二阶预测每 tick 变化 `±Δ/2`（供 4S 数据眼透视）；
8. 价格软上限 = 初始价 × 1000，触顶后上涨概率强制降到 10%；
9. **市场周期**每 1~75 tick 随机触发，结束时 45% 概率翻转牛熊并把二阶预测取反。

引擎是纯函数 + 显式注入的 `random.Random`（离线批量推演时可传固定种子，保证可复现）。
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from typing import Any

from app.core import balance as B


def initial_quotes() -> list[dict[str, Any]]:
    """播种四只标的（初始价 / 波动率 / 初始趋势 / 点差）。"""
    quotes: list[dict[str, Any]] = []
    for stock_id, spec in B.STARTER_STOCKS.items():
        trend = float(spec["trend_strength"])
        quotes.append(
            {
                "stock_id": stock_id,
                "name": spec["name"],
                "price": float(spec["price"]),
                "initial_price": float(spec["price"]),
                "volatility": float(spec["volatility"]),
                "spread": float(spec["spread"]),
                "trend_strength": trend,
                "forecast": round((50.0 + trend) / 100.0, 4),
                "target_forecast": round((50.0 + trend) / 100.0, 4),
                "cycle_ticks_left": None,
                "momentum": 0.0,
            }
        )
    return quotes


def _forecast(stock: Mapping[str, Any]) -> float:
    """一阶胜率：50% + 趋势强度（0~1）。软上限触顶时强制 10%。"""
    trend = float(stock.get("trend_strength", 0.0))
    chc = min(1.0, max(0.0, 0.5 + trend / 100.0))
    soft_cap = float(stock.get("initial_price", stock.get("price", 1.0))) * B.STOCK_SOFT_CAP_MULTIPLIER
    if float(stock.get("price", 0.0)) >= soft_cap:
        chc = B.STOCK_SOFT_CAP_UP_PROBABILITY
    momentum = float(stock.get("momentum", 0.0))
    return min(1.0, max(0.0, chc + momentum * 0.1))


def tick_market(
    stocks: Sequence[Mapping[str, Any]],
    *,
    rng: random.Random,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """推进一个 tick（6 秒）：同一全局冲击作用于所有标的。"""
    shock = rng.random()
    updated: list[dict[str, Any]] = []
    for stock in stocks:
        item = dict(stock)
        volatility = float(item.get("volatility", 1.0))
        amplitude = shock * volatility / 100.0
        chc = _forecast(item)
        goes_up = rng.random() < chc
        price = float(item.get("price", 1.0))
        price = price * (1.0 + amplitude) if goes_up else max(0.01, price / (1.0 + amplitude))
        item["price"] = round(price, 2)

        trend = float(item.get("trend_strength", 0.0))
        delta = trend * amplitude
        trend += delta
        if abs(trend) < 5.0:
            # 避免"熄火"：小幅离开零点，保住趋势方向
            trend += 0.1 if trend >= 0 else -0.1
        item["trend_strength"] = round(max(-50.0, min(50.0, trend)), 4)

        target = float(item.get("target_forecast", 0.5))
        target = min(1.0, max(0.0, target * 100.0 + delta / 2)) / 100.0
        item["target_forecast"] = round(target, 4)
        item["forecast"] = round(_forecast(item), 4)
        item["momentum"] = round(float(item.get("momentum", 0.0)) * 0.92, 4)

        # 市场周期：每 1~75 tick 随机触发一次，结束时 45% 概率翻转牛熊
        cycle = item.get("cycle_ticks_left")
        if cycle is None:
            cycle = rng.randint(B.STOCK_MARKET_CYCLE_MIN_TICKS, B.STOCK_MARKET_CYCLE_MAX_TICKS)
        cycle = int(cycle) - 1
        if cycle <= 0:
            if rng.random() < B.STOCK_MARKET_CYCLE_FLIP_PROBABILITY:
                item["trend_strength"] = round(-float(item["trend_strength"]), 4)
                item["target_forecast"] = round(1.0 - float(item["target_forecast"]), 4)
            cycle = rng.randint(B.STOCK_MARKET_CYCLE_MIN_TICKS, B.STOCK_MARKET_CYCLE_MAX_TICKS)
        item["cycle_ticks_left"] = cycle
        updated.append(item)
    return updated, {"shock": round(shock, 4)}


def advance_market(
    stocks: Sequence[Mapping[str, Any]],
    *,
    seconds: float,
    seed: int | None = None,
    max_ticks: int = 600,
    kline_limit: int = 60,
) -> dict[str, Any]:
    """按离线秒数批量推演（每 tick 6 秒，最多 `max_ticks` 个 tick 防止离线几天后爆算）。"""
    ticks = min(max_ticks, max(0, int(seconds // B.STOCK_TICK_SECONDS)))
    rng = random.Random(seed)
    current: list[dict[str, Any]] = [dict(stock) for stock in stocks]
    kline: list[dict[str, Any]] = []
    for _ in range(ticks):
        current, _info = tick_market(current, rng=rng)
        kline.append({stock["stock_id"]: stock["price"] for stock in current})
        if len(kline) > kline_limit:
            kline.pop(0)
    return {"ticks": ticks, "skipped_seconds": max(0.0, seconds - ticks * B.STOCK_TICK_SECONDS), "stocks": current, "kline": kline}


def apply_trade_impact(stock: dict[str, Any], shares: float, *, side: str) -> dict[str, Any]:
    """交易反噬（§11.1）：每成交 100 股，趋势强度向 0 收缩 0.006，玩家影响力上限 5。"""
    shrink = min(
        B.STOCK_PLAYER_INFLUENCE_CAP,
        abs(shares) / 100.0 * B.STOCK_IMPACT_PER_100_SHARES,
    )
    trend = float(stock.get("trend_strength", 0.0))
    direction = 1.0 if trend >= 0 else -1.0
    # 买入（做多）会推高趋势、卖出会压低，但整体是"越做越没劲"的收缩
    adjusted = trend - direction * shrink + (0.004 if side == "BUY" else -0.004)
    stock["trend_strength"] = round(max(-50.0, min(50.0, adjusted)), 4)
    return stock


def trade_price(stock: Mapping[str, Any], side: str) -> float:
    """买按 ask = 价 ×(1+点差)，卖按 bid = 价 ×(1−点差)。"""
    spread = float(stock.get("spread", 0.0))
    price = float(stock.get("price", 0.0))
    return round(price * (1 + spread) if side == "BUY" else price * (1 - spread), 4)


def short_liquidation_price(entry_price: float, leverage: int) -> float:
    """爆仓价：价格涨到（1 + 1/杠杆）就能击穿保证金。"""
    return round(entry_price * (1.0 + 1.0 / max(1, leverage)), 2)
