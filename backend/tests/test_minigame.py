"""模块 O 验收：密电译码（Mastermind 推理）+ LLM 场景 1（新行星生态标签）。"""

from __future__ import annotations

import pytest

from app.core import balance as B
from app.core.minigame_engine import (
    code_length,
    daily_seed,
    evaluate_mix,
    evaluate_guess,
    forge_spec,
    generate_forge_recipe,
    generate_secret,
    generate_vein_board,
    render_symbols,
    symbol_count,
    vein_hint,
    vein_spec,
)
from app.models import BossState, ColonyState, MinigameState, PlanetState
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

    async def test_unknown_action_is_rejected(self, client, session):
        """三个小游戏都已落地；不认识的动作如实报错，而不是假装执行。"""
        await _bootstrap(client)
        await _unlock_planet(session, 2)
        response = await client.post(
            f"{MINIGAME_URL}/action",
            json={"minigame_id": "cipher_decode", "action": "SELF_DESTRUCT", "payload": {}},
        )
        assert response.status_code == 400
        assert response.json()["message"] == "BAD_REQUEST"

    async def test_unknown_minigame_rejected(self, client):
        await _bootstrap(client)
        response = await client.post(
            f"{MINIGAME_URL}/action", json={"minigame_id": "moon_race", "action": "GO", "payload": {}}
        )
        assert response.status_code == 400


class TestVeinEngine:
    def test_board_is_deterministic_with_exact_vein_count(self):
        first = generate_vein_board("1:3:0")
        second = generate_vein_board("1:3:0")
        assert first == second
        assert len(first) == len(first[0]) == vein_spec()["grid_size"] == 8
        assert sum(1 for row in first for cell in row if cell) == vein_spec()["vein_count"] == 12

    def test_hint_counts_eight_neighbors(self):
        board = [[False] * 8 for _ in range(8)]
        board[0][1] = True
        board[1][0] = True
        board[1][1] = True
        assert vein_hint(board, 0, 0) == 3
        assert vein_hint(board, 5, 5) == 0


class TestForgeEngine:
    def test_recipe_is_a_deterministic_ratio(self):
        first = generate_forge_recipe("1:1:0")
        assert first == generate_forge_recipe("1:1:0")
        assert sum(first) == 100
        assert all(0 <= value <= 100 for value in first)

    def test_feedback_only_gives_direction(self):
        recipe = [50, 30, 20]
        assert evaluate_mix(recipe, [50, 30, 20])["result"] == "HIT"
        assert evaluate_mix(recipe, [52, 32, 22])["result"] == "HIT"  # 容差内
        assert evaluate_mix(recipe, [90, 90, 90])["result"] == "TOO_HOT"
        assert evaluate_mix(recipe, [10, 10, 10])["result"] == "TOO_COLD"
        off = evaluate_mix(recipe, [80, 10, 10])
        assert off["result"] == "RATIO_OFF"
        assert "偏多" in off["hint"]
        assert off["worst_slot"] == 0


class TestVeinApi:
    async def test_scan_requires_unlocked_planet(self, client):
        await _bootstrap(client)
        response = await client.post(
            f"{MINIGAME_URL}/action",
            json={"minigame_id": "vein_scan", "action": "SCAN", "payload": {"x": 0, "y": 0}},
        )
        assert response.status_code == 400
        assert response.json()["message"] == "PLANET_LOCKED"

    async def test_scan_reveals_hint_and_consumes_quota(self, client, session):
        await _bootstrap(client)
        await _unlock_planet(session, 3)
        board = generate_vein_board("1:3:0")
        empty = next((x, y) for y in range(8) for x in range(8) if not board[y][x])
        data = (
            await client.post(
                f"{MINIGAME_URL}/action",
                json={"minigame_id": "vein_scan", "action": "SCAN", "payload": {"x": empty[0], "y": empty[1]}},
            )
        ).json()["data"]
        assert data["is_vein"] is False
        assert data["hint"] == vein_hint(board, empty[0], empty[1])
        assert data["quota"] == vein_spec()["quota_max"] - 1
        assert data["gained"] == {}

    async def test_hitting_a_vein_pays_out(self, client, session):
        await _bootstrap(client)
        await _unlock_planet(session, 3)
        await session.rollback()
        colony = await session.get(ColonyState, (1, 0))
        colony.alloys, colony.battery = 0.0, 0.0
        await session.commit()
        board = generate_vein_board("1:3:0")
        vein = next((x, y) for y in range(8) for x in range(8) if board[y][x])
        data = (
            await client.post(
                f"{MINIGAME_URL}/action",
                json={"minigame_id": "vein_scan", "action": "SCAN", "payload": {"x": vein[0], "y": vein[1]}},
            )
        ).json()["data"]
        assert data["is_vein"] is True
        assert data["hint"] == 0
        assert data["gained"]["alloys"] == 2.0
        assert data["gained"]["battery"] == 1.0
        assert data["found_count"] == 1

    async def test_repeat_scan_and_quota_exhaustion(self, client, session):
        await _bootstrap(client)
        await _unlock_planet(session, 3)
        payload = {"minigame_id": "vein_scan", "action": "SCAN", "payload": {"x": 0, "y": 0}}
        await client.post(f"{MINIGAME_URL}/action", json=payload)
        again = await client.post(f"{MINIGAME_URL}/action", json=payload)
        assert again.status_code == 400
        assert again.json()["message"] == "ALREADY_SCANNED"

        # 把配额耗光（8×8 上换格子扫）
        for x, y in [(1, 0), (2, 0), (3, 0), (4, 0)]:
            await client.post(
                f"{MINIGAME_URL}/action",
                json={"minigame_id": "vein_scan", "action": "SCAN", "payload": {"x": x, "y": y}},
            )
        exhausted = await client.post(
            f"{MINIGAME_URL}/action",
            json={"minigame_id": "vein_scan", "action": "SCAN", "payload": {"x": 5, "y": 0}},
        )
        assert exhausted.status_code == 400
        assert exhausted.json()["message"] == "QUOTA_EXHAUSTED"

    async def test_invalid_coordinates_rejected(self, client, session):
        await _bootstrap(client)
        await _unlock_planet(session, 3)
        bad = await client.post(
            f"{MINIGAME_URL}/action",
            json={"minigame_id": "vein_scan", "action": "SCAN", "payload": {"x": 9, "y": 0}},
        )
        assert bad.status_code == 400
        assert bad.json()["message"] == "BAD_REQUEST"


class TestForgeApi:
    async def test_submit_mix_costs_scrap(self, client, session):
        await _bootstrap(client)
        await _unlock_planet(session, 1)
        await session.rollback()
        colony = await session.get(ColonyState, (1, 0))
        colony.scrap = 100.0
        await session.commit()
        data = (
            await client.post(
                f"{MINIGAME_URL}/action",
                json={"minigame_id": "forge_recipe", "action": "SUBMIT_MIX", "payload": {"mix": [50, 30, 20]}},
            )
        ).json()["data"]
        assert data["feedback"]["result"] in ("HIT", "RATIO_OFF", "TOO_HOT", "TOO_COLD")
        assert data["scrap_left"] == pytest.approx(80.0)

    async def test_insufficient_scrap_rejected(self, client, session):
        await _bootstrap(client)
        await _unlock_planet(session, 1)
        await session.rollback()
        colony = await session.get(ColonyState, (1, 0))
        colony.scrap = 5.0
        await session.commit()
        response = await client.post(
            f"{MINIGAME_URL}/action",
            json={"minigame_id": "forge_recipe", "action": "SUBMIT_MIX", "payload": {"mix": [50, 30, 20]}},
        )
        assert response.status_code == 400
        assert response.json()["message"] == "INSUFFICIENT_RESOURCE"

    async def test_hitting_the_recipe_grants_permanent_bonus_and_caps(self, client, session):
        await _bootstrap(client)
        await _unlock_planet(session, 1)
        await session.rollback()
        colony = await session.get(ColonyState, (1, 0))
        colony.scrap_max, colony.scrap = 2000.0, 2000.0
        await session.commit()

        found = 0
        for index in range(11):  # 故意多打一次，验证上限封顶
            recipe = generate_forge_recipe(f"1:1:{index}")
            data = (
                await client.post(
                    f"{MINIGAME_URL}/action",
                    json={
                        "minigame_id": "forge_recipe",
                        "action": "SUBMIT_MIX",
                        "payload": {"mix": recipe},
                    },
                )
            ).json()["data"]
            assert data["feedback"]["result"] == "HIT"
            found = data["recipes_found"]
        cap = forge_spec()["recipe_cap"]
        assert found == cap == 10
        assert data["smelt_speed_bonus"] == pytest.approx(forge_spec()["recipe_bonus_cap"])

    async def test_invalid_mix_rejected(self, client, session):
        await _bootstrap(client)
        await _unlock_planet(session, 1)
        bad = await client.post(
            f"{MINIGAME_URL}/action",
            json={"minigame_id": "forge_recipe", "action": "SUBMIT_MIX", "payload": {"mix": [50, 30]}},
        )
        assert bad.status_code == 400


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
