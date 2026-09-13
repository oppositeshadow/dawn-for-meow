"""模块 O 验收：密电译码（Mastermind 推理）+ LLM 场景 1（新行星生态标签）。"""

from __future__ import annotations

from app.core import balance as B
from app.core.minigame_engine import (
    code_length,
    daily_seed,
    evaluate_guess,
    generate_secret,
    render_symbols,
    symbol_count,
)
from app.models import BossState, MinigameState, PlanetState
from app.services import planet_service

MINIGAME_URL = "/api/v1/minigame"
PLANET_URL = "/api/v1/planet"


async def _bootstrap(client) -> dict:
    return (await client.get("/api/v1/colony/state")).json()["data"]


async def _unlock_planet(session, planet_id: int = 2) -> None:
    await session.rollback()
    planet = await session.get(PlanetState, (1, planet_id))
    planet.unlocked = True
    planet.unlocked_at = 1
    await session.commit()


class TestCipherEngine:
    def test_secret_is_deterministic_by_seed(self):
        first = generate_secret(daily_seed(1, 2, "2026-09-13"))
        second = generate_secret(daily_seed(1, 2, "2026-09-13"))
        other = generate_secret(daily_seed(1, 2, "2026-09-14"))
        assert first == second
        assert first != other
        assert len(first) == code_length() == 4
        assert all(1 <= value <= symbol_count() == 6 for value in first)

    def test_feedback_counts_exact_and_partial(self):
        assert evaluate_guess([1, 2, 3, 4], [1, 2, 3, 4]) == (4, 0)
        assert evaluate_guess([1, 2, 3, 4], [4, 3, 2, 1]) == (0, 4)
        assert evaluate_guess([1, 2, 3, 4], [1, 1, 1, 1]) == (1, 0)

    def test_feedback_handles_duplicate_symbols(self):
        """重复符号按 Mastermind 规则：先扣位置对，再从剩余里配对。"""
        # 位置对 3 个（1,2,2 与 2,2 都对上了），剩下 secret 的 1 与 guess 的 2 配不上
        assert evaluate_guess([1, 1, 2, 2], [1, 2, 2, 2]) == (3, 0)
        # 交叉匹配：位置对 2 个，符号对再补 2 个
        assert evaluate_guess([1, 1, 2, 2], [2, 1, 1, 2]) == (2, 2)

    def test_render_symbols_uses_chinese_alphabet(self):
        assert render_symbols([1, 2, 6, 4]) == "甲乙己丁"


class TestCipherApi:
    async def test_list_exposes_three_games_without_leaking_answer(self, client):
        await _bootstrap(client)
        data = (await client.get(f"{MINIGAME_URL}/list")).json()["data"]
        assert {game["minigame_id"] for game in data["games"]} == {"cipher_decode", "vein_scan", "forge_recipe"}
        cipher = next(game for game in data["games"] if game["minigame_id"] == "cipher_decode")
        assert cipher["planet_id"] == 2
        assert cipher["state"]["code_length"] == 4
        assert cipher["state"]["symbol_count"] == 6
        assert cipher["state"]["remaining"] == 3
        assert "symbols" not in cipher["state"]  # 绝不泄漏当天密钥
        assert "纯加速" in data["note"]

    async def test_planet_must_be_unlocked(self, client):
        await _bootstrap(client)
        response = await client.post(
            f"{MINIGAME_URL}/action",
            json={"minigame_id": "cipher_decode", "action": "SUBMIT_GUESS", "payload": {"guess": [1, 2, 3, 4]}},
        )
        assert response.status_code == 400
        assert response.json()["message"] == "PLANET_LOCKED"

    async def test_wrong_guess_returns_feedback_and_costs_a_try(self, client, session):
        await _bootstrap(client)
        await _unlock_planet(session)
        data = (
            await client.post(
                f"{MINIGAME_URL}/action",
                json={"minigame_id": "cipher_decode", "action": "SUBMIT_GUESS", "payload": {"guess": [1, 1, 1, 1]}},
            )
        ).json()["data"]
        assert data["solved"] is False
        assert data["used"] == 1
        assert data["remaining"] == 2
        assert 0 <= data["exact"] <= 4 and 0 <= data["partial"] <= 4
        assert data["reward"] is None
        assert data["answer"] is None

    async def test_daily_quota_is_enforced(self, client, session):
        await _bootstrap(client)
        await _unlock_planet(session)
        payload = {"minigame_id": "cipher_decode", "action": "SUBMIT_GUESS", "payload": {"guess": [1, 1, 1, 1]}}
        for _ in range(3):
            await client.post(f"{MINIGAME_URL}/action", json=payload)
        fourth = await client.post(f"{MINIGAME_URL}/action", json=payload)
        assert fourth.status_code == 400
        assert fourth.json()["message"] == "QUOTA_EXHAUSTED"

    async def test_solving_raises_intel_and_records_best(self, client, session):
        await _bootstrap(client)
        await _unlock_planet(session)
        secret = generate_secret(daily_seed(1, 2, __import__("time").strftime("%Y-%m-%d")))
        data = (
            await client.post(
                f"{MINIGAME_URL}/action",
                json={"minigame_id": "cipher_decode", "action": "SUBMIT_GUESS", "payload": {"guess": secret}},
            )
        ).json()["data"]
        assert data["solved"] is True
        assert data["exact"] == 4
        assert data["answer"] == render_symbols(secret)
        assert data["best_attempts"] == 1
        assert data["reward"]["intel_level"] == 0.08
        await session.rollback()
        boss = await session.get(BossState, 1, populate_existing=True)
        assert boss.intel_level == 0.08
        row = await session.get(MinigameState, (1, 2, "cipher_decode"), populate_existing=True)
        assert row.best_score == 1.0
        assert row.state["best_attempts"] == 1

    async def test_invalid_guess_shape_rejected(self, client, session):
        await _bootstrap(client)
        await _unlock_planet(session)
        bad = await client.post(
            f"{MINIGAME_URL}/action",
            json={"minigame_id": "cipher_decode", "action": "SUBMIT_GUESS", "payload": {"guess": [1, 2, 9]}},
        )
        assert bad.status_code == 400
        assert bad.json()["message"] == "BAD_REQUEST"

    async def test_unimplemented_minigame_is_honest(self, client, session):
        """矿脉扫描还没做：如实返回 NOT_IMPLEMENTED，而不是假装能玩。"""
        await _bootstrap(client)
        await _unlock_planet(session, 3)
        response = await client.post(
            f"{MINIGAME_URL}/action",
            json={"minigame_id": "vein_scan", "action": "SCAN", "payload": {}},
        )
        assert response.status_code == 400
        assert response.json()["message"] == "NOT_IMPLEMENTED"

    async def test_unknown_minigame_rejected(self, client):
        await _bootstrap(client)
        response = await client.post(
            f"{MINIGAME_URL}/action", json={"minigame_id": "moon_race", "action": "GO", "payload": {}}
        )
        assert response.status_code == 400


class TestPlanetBiome:
    async def test_biome_falls_back_without_llm(self, client, session, monkeypatch):
        """LLM 不可用时走本地生态池（玩家零感知），并且只生成一次（幂等）。"""
        from app.core.config import get_settings

        await _bootstrap(client)
        await _unlock_planet(session, 1)
        monkeypatch.setattr(get_settings(), "llm_api_key", "")
        data = (await client.post(f"{PLANET_URL}/switch", json={"planet_id": 1})).json()["data"]
        assert data["biome_source"] == "FALLBACK"
        assert data["biome_tag"]
        assert "熔炉" in data["biome_tag"] or "赤色" in data["biome_tag"]

        again = (await client.post(f"{PLANET_URL}/switch", json={"planet_id": 1})).json()["data"]
        assert again["biome_source"] is None  # 已有标签 ⇒ 不再调用 LLM
        assert again["biome_tag"] == data["biome_tag"]

    async def test_home_planet_keeps_handwritten_biome(self, client, session):
        await _bootstrap(client)
        assert await planet_service.ensure_biome(session, 1, 0) is None
        planet = await session.get(PlanetState, (1, 0))
        assert "翠绿生态摇篮" in (planet.biome_tag or "")

    async def test_fallback_pool_covers_all_planets(self):
        assert set(planet_service.FALLBACK_BIOMES) == {1, 2, 3}
        for biome in planet_service.FALLBACK_BIOMES.values():
            assert 2 <= len(biome.biome_tag) <= 32
            assert len(biome.affixes) <= 3
