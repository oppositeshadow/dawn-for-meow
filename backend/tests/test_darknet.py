"""模块 I 验收：行情推演、现货/做空、黑市空投、论坛做局与暴露度四段危机。"""

from __future__ import annotations

import random
import time

import pytest

from app.core import balance as B
from app.core.stock_engine import (
    advance_market,
    apply_trade_impact,
    initial_quotes,
    short_liquidation_price,
    tick_market,
    trade_price,
)
from app.models import ColonyState, DarknetState
from app.services import darknet_service

DARKNET_URL = "/api/v1/darknet"


async def _bootstrap(client) -> dict:
    return (await client.get("/api/v1/colony/state")).json()["data"]


async def _fund(session, *, credits: float = 5000.0, scrap: float = 200.0) -> None:
    await session.rollback()
    darknet = await session.get(DarknetState, 1)
    darknet.byte_credits = credits
    colony = await session.get(ColonyState, (1, 0))
    colony.scrap = scrap
    await session.commit()


async def _rewind(session, seconds: int) -> None:
    """把 last_tick_time 回拨，保证离线推进的 seconds > 0。"""
    await session.rollback()
    colony = await session.get(ColonyState, (1, 0))
    colony.last_tick_time = int(time.time()) - seconds
    await session.commit()


class TestStockEngine:
    def test_initial_quotes_follow_balance_sheet(self):
        quotes = {quote["stock_id"]: quote for quote in initial_quotes()}
        assert quotes["FORGE"]["price"] == 45.0
        assert quotes["HELIUM3"]["volatility"] == 2.0
        assert quotes["FORGE"]["trend_strength"] == 20.0
        assert quotes["FORGE"]["initial_price"] == 45.0

    def test_tick_moves_price_and_keeps_trend_bounded(self):
        stocks = initial_quotes()
        rng = random.Random(7)
        for _ in range(200):
            stocks, _ = tick_market(stocks, rng=rng)
        for quote in stocks:
            assert quote["price"] > 0
            assert -50 <= quote["trend_strength"] <= 50
            assert 0.0 <= quote["forecast"] <= 1.0

    def test_soft_cap_forces_low_up_probability(self):
        stocks = initial_quotes()
        # 价格远高于软上限 ⇒ 即使本 tick 下跌也仍在软上限之上，上涨概率被强制压到 10%
        stocks[0]["price"] = stocks[0]["initial_price"] * B.STOCK_SOFT_CAP_MULTIPLIER * 100
        updated, _ = tick_market(stocks, rng=random.Random(1))
        assert updated[0]["forecast"] == pytest.approx(B.STOCK_SOFT_CAP_UP_PROBABILITY)

    def test_market_advance_is_deterministic_with_seed(self):
        first = advance_market(initial_quotes(), seconds=600, seed=42)
        second = advance_market(initial_quotes(), seconds=600, seed=42)
        assert first["ticks"] == 100  # 600s / 6s
        assert [q["price"] for q in first["stocks"]] == [q["price"] for q in second["stocks"]]

    def test_market_advance_caps_tick_count(self):
        result = advance_market(initial_quotes(), seconds=86400, seed=1, max_ticks=50)
        assert result["ticks"] == 50
        assert result["skipped_seconds"] > 0

    def test_trade_impact_shrinks_trend(self):
        quote = initial_quotes()[0]
        before = quote["trend_strength"]
        apply_trade_impact(quote, 500, side="BUY")
        assert abs(quote["trend_strength"]) < abs(before) + 0.01  # 越做越没劲

    def test_trade_price_applies_spread(self):
        quote = initial_quotes()[0]  # 点差 0.5%
        assert trade_price(quote, "BUY") == pytest.approx(45.0 * 1.005, abs=0.01)
        assert trade_price(quote, "SELL") == pytest.approx(45.0 * 0.995, abs=0.01)

    def test_liquidation_price_scales_with_leverage(self):
        assert short_liquidation_price(100.0, 1) == 200.0
        assert short_liquidation_price(100.0, 5) == 120.0


class TestExposureBands:
    def test_bands(self):
        assert darknet_service.exposure_band(0) == "CLEAN"
        assert darknet_service.exposure_band(30) == "SURCHARGED"
        assert darknet_service.exposure_band(60) == "INTERROGATION"
        assert darknet_service.exposure_band(80) == "TRACKED"
        assert darknet_service.exposure_band(100) == "BANNED"

    def test_fee_multiplier_surcharges_when_exposed(self):
        assert darknet_service.fee_multiplier(10) == 1.0
        assert darknet_service.fee_multiplier(60) == B.EXPOSURE_FEE_MULTIPLIER


class TestStocksApi:
    async def test_state_lists_four_stocks_and_market(self, client):
        await _bootstrap(client)
        data = (await client.get(f"{DARKNET_URL}/state")).json()["data"]
        assert {quote["stock_id"] for quote in data["stocks"]} == set(B.STARTER_STOCKS)
        assert data["exposure_band"] == "CLEAN"
        assert data["burner_id"].startswith("robot_")
        assert set(data["market_items"]) == set(darknet_service.MARKET_ITEMS)
        assert "burner_card" in data["contraband"]

    async def test_buy_long_charges_fee_and_records_position(self, client, session):
        await _bootstrap(client)
        await _fund(session, credits=1000.0)
        data = (
            await client.post(
                f"{DARKNET_URL}/stock/trade",
                json={"stock_id": "FORGE", "action": "BUY_LONG", "shares": 10},
            )
        ).json()["data"]
        assert data["fee"] == B.STOCK_TRADE_FEE_BYTES
        assert data["byte_credits"] < 1000.0
        await session.rollback()
        darknet = await session.get(DarknetState, 1)
        assert darknet.positions and darknet.positions[0]["stock_id"] == "FORGE"

    async def test_sell_without_position_is_rejected(self, client, session):
        await _bootstrap(client)
        await _fund(session, credits=1000.0)
        response = await client.post(
            f"{DARKNET_URL}/stock/trade",
            json={"stock_id": "FORGE", "action": "SELL_LONG", "shares": 5},
        )
        assert response.status_code == 400
        assert response.json()["message"] == "INSUFFICIENT_RESOURCE"

    async def test_buy_requires_credits(self, client, session):
        await _bootstrap(client)
        await _fund(session, credits=1.0)
        response = await client.post(
            f"{DARKNET_URL}/stock/trade",
            json={"stock_id": "FORGE", "action": "BUY_LONG", "shares": 100},
        )
        assert response.status_code == 400


class TestShortApi:
    async def test_open_short_creates_contract(self, client, session):
        await _bootstrap(client)
        await _fund(session, credits=2000.0)
        contract = (
            await client.post(
                f"{DARKNET_URL}/short", json={"stock_id": "HELIUM3", "shares": 5, "leverage": 2}
            )
        ).json()["data"]
        assert contract["leverage"] == 2
        assert contract["liquidation_price"] > contract["entry_price"]
        assert contract["ends_at"] > int(time.time())

    async def test_short_settles_on_expiry(self, client, session):
        await _bootstrap(client)
        await _fund(session, credits=2000.0)
        contract = (
            await client.post(
                f"{DARKNET_URL}/short", json={"stock_id": "HELIUM3", "shares": 5, "leverage": 2}
            )
        ).json()["data"]
        await session.rollback()
        darknet = await session.get(DarknetState, 1)
        darknet.short_contracts = [{**contract, "ends_at": int(time.time()) - 1}]
        await session.commit()
        await _rewind(session, 10)
        state = (await client.get("/api/v1/colony/state")).json()["data"]
        assert any("做空" in note for note in state["offline_report"]["notes"])
        await session.rollback()
        darknet = await session.get(DarknetState, 1, populate_existing=True)
        assert darknet.short_contracts == []  # 到期强制买回交割后清空

    async def test_leverage_bounds(self, client, session):
        await _bootstrap(client)
        await _fund(session, credits=2000.0)
        response = await client.post(
            f"{DARKNET_URL}/short", json={"stock_id": "GRID", "shares": 1, "leverage": 99}
        )
        assert response.status_code == 400  # Pydantic 边界被统一收敛成 {code:400, message:"BAD_REQUEST"}


class TestMarketApi:
    async def test_buy_creates_delivery_then_arrives(self, client, session):
        await _bootstrap(client)
        await _fund(session, credits=500.0)
        result = (
            await client.post(
                f"{DARKNET_URL}/market/trade", json={"item_id": "chips", "side": "BUY", "qty": 2}
            )
        ).json()["data"]
        assert result["arrives_at"] > int(time.time())
        await session.rollback()
        darknet = await session.get(DarknetState, 1)
        delivery = darknet.pending_deliveries[0]
        darknet.pending_deliveries = [{**delivery, "ends_at": int(time.time()) - 1}]
        colony = await session.get(ColonyState, (1, 0))
        colony.chips = 0.0
        await session.commit()
        await _rewind(session, 10)
        state = (await client.get("/api/v1/colony/state")).json()["data"]
        assert any("空投送达" in note for note in state["offline_report"]["notes"])
        assert state["resources"]["chips"] == pytest.approx(10.0)  # 5 芯片/份 × 2

    async def test_sell_raises_credits_and_exposure(self, client, session):
        await _bootstrap(client)
        await _fund(session, credits=0.0, scrap=200.0)
        data = (
            await client.post(
                f"{DARKNET_URL}/market/trade", json={"item_id": "scrap", "side": "SELL", "qty": 1}
            )
        ).json()["data"]
        assert data["earned"] > 0
        assert data["exposure"] >= B.SMUGGLING_EXPOSURE_GAIN_MIN

    async def test_contraband_burner_card_clears_exposure(self, client, session):
        await _bootstrap(client)
        await _fund(session, credits=500.0)
        await session.rollback()
        darknet = await session.get(DarknetState, 1)
        darknet.exposure = 60.0
        await session.commit()
        data = (
            await client.post(
                f"{DARKNET_URL}/market/trade", json={"item_id": "burner_card", "side": "BUY", "qty": 1}
            )
        ).json()["data"]
        assert data["effect"] == "EXPOSURE_ZERO"
        assert data["exposure"] == pytest.approx(0.0)


class TestForum:
    async def test_feed_is_anonymous_and_exposes_three_clues(self, client):
        await _bootstrap(client)
        data = (await client.get(f"{DARKNET_URL}/forum/feed")).json()["data"]
        assert len(data["clues"]) == 3
        assert "不显示 IP" in data["note"]
        for post in data["posts"]:
            assert "subnet" not in post  # 绝不暴露网段

    async def test_player_post_falls_back_without_llm_and_injects_momentum(self, client, session, monkeypatch):
        await _bootstrap(client)
        await _fund(session, credits=100.0)
        from app.core.config import get_settings

        monkeypatch.setattr(get_settings(), "llm_api_key", "")
        data = (
            await client.post(
                f"{DARKNET_URL}/forum/post",
                json={"title": "内幕爆料", "content": "听说熔岩铸造厂高炉炸膛了！FORGE 要暴涨！！！"},
            )
        ).json()["data"]
        assert data["source"] == "FALLBACK"  # 无 key 时本地关键词兜底，玩家零感知
        assert data["judgement"]["sentiment"] == "BULL"
        assert data["judgement"]["persuasiveness"] >= 2
        assert data["momentum_impact"] > 0
        await session.rollback()
        darknet = await session.get(DarknetState, 1)
        assert darknet.last_post_time is not None
        forge = next(q for q in darknet.stocks_data if q["stock_id"] == "FORGE")
        assert forge["momentum"] > 0

    async def test_post_cooldown_is_enforced(self, client, session):
        await _bootstrap(client)
        await _fund(session, credits=100.0)
        payload = {"title": "水帖", "content": "随便说说"}
        assert (await client.post(f"{DARKNET_URL}/forum/post", json=payload)).status_code == 200
        second = await client.post(f"{DARKNET_URL}/forum/post", json=payload)
        assert second.status_code == 409
        assert second.json()["message"] == "FORUM_POST_COOLDOWN"


class TestIdentityAndTools:
    async def test_reroll_id_costs_and_clears_exposure(self, client, session):
        await _bootstrap(client)
        await _fund(session, credits=500.0, scrap=200.0)
        await session.rollback()
        darknet = await session.get(DarknetState, 1)
        darknet.exposure = 40.0
        await session.commit()
        data = (
            await client.post(f"{DARKNET_URL}/reroll-id", json={"custom_id": "anon_meow_99"})
        ).json()["data"]
        assert data["burner_id"] == "anon_meow_99"
        assert data["exposure"] == 0.0
        assert data["cost_paid"]["scrap"] == B.REROLL_ID_COST["scrap"]

    async def test_data_eye_purchase(self, client, session):
        await _bootstrap(client)
        await _fund(session, credits=B.STOCK_DATA_EYE_PRICE)
        data = (await client.post(f"{DARKNET_URL}/data-eye", json={})).json()["data"]
        assert data["has_4s_data"] is True
        again = await client.post(f"{DARKNET_URL}/data-eye", json={})
        assert again.status_code == 409
