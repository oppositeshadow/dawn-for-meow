"""模块 H 验收：生长阶段、放射枯萎、相邻突变、逐格扩建、采摘变现与在田光环。"""

from __future__ import annotations

import time

import pytest

from app.core import balance as B
from app.core.garden_engine import (
    STAGE_JOINTING,
    STAGE_MATURE,
    STAGE_SEEDLING,
    STAGE_WITHERED,
    advance_tile,
    halo_summary,
    next_expand_tile,
    stage_for_age,
)
from app.models import ColonyState, GardenState
from app.services import garden_service

GARDEN_URL = "/api/v1/garden"


def _plants() -> dict:
    return garden_service.plant_defs()


def _tile(**overrides) -> dict:
    tile = {"x": 3, "y": 3, "unlocked": True, "seed_id": "ordinary_moss", "stage": STAGE_SEEDLING, "age": 0.0}
    tile.update(overrides)
    return tile


async def test_halo_is_split_into_active_and_pending(client, session) -> None:
    """§10《光环接线状态》：真生效的进 `halo`，只展示的进 `halo_pending`，两者绝不混。

    "看得到但不生效"就是对玩家的假承诺——种一株荧光苔藓（+5 kW，已接线）
    与一株金刚地衣（装甲 +15%，待接线），接口必须把它们分开交代。
    """
    await client.get("/api/v1/colony/state", params={"slot": 1})  # 开局建档
    await session.rollback()
    garden = await session.get(GardenState, (1, 0), populate_existing=True)
    grid = [dict(tile) for tile in (garden.grid_data or [])]
    # 开局只有中央 3×3 解锁 ⇒ 用"已解锁"的那几格（编号顺序不保证）
    open_tiles = [tile for tile in grid if tile.get("unlocked")]
    open_tiles[0].update({"seed_id": "glow_moss", "stage": STAGE_MATURE, "age": 200.0})
    open_tiles[1].update({"seed_id": "adamant_lichen", "stage": STAGE_MATURE, "age": 200.0})
    garden.grid_data = grid  # JSON 列整条替换
    await session.commit()

    data = (await client.get(f"{GARDEN_URL}/state", params={"slot": 1})).json()["data"]
    assert data["halo"]["power_kw"] == pytest.approx(5.0)
    assert "vehicle_armor" not in data["halo"]              # 未接线的绝不混进生效数字
    assert data["halo_pending"]["vehicle_armor"] == pytest.approx(0.15)
    # 图鉴层面同样分家：金刚地衣是"待接线"那一栏里的
    assert data["plants"]["glow_moss"]["halo"] == {"power_kw": 5.0}
    assert data["plants"]["adamant_lichen"]["halo"] == {}
    assert data["plants"]["adamant_lichen"]["halo_pending"] == {"vehicle_armor": 0.15}
    # 接线名单由 balance 单点声明（接口按它分类，前端不自己判断）
    assert set(data["halo"]).issubset(set(B.GARDEN_HALO_ACTIVE_KEYS))


class TestGrowthEngine:
    def test_stage_progression_per_90_seconds(self):
        assert stage_for_age(0) == STAGE_SEEDLING
        assert stage_for_age(95) == STAGE_JOINTING
        assert stage_for_age(200) == STAGE_MATURE
        assert stage_for_age(300) == STAGE_WITHERED

    def test_radiation_speeds_growth_and_withers_after_120s(self):
        tile = _tile()
        advance_tile(tile, _plants()["ordinary_moss"], seconds=100, medium="RADIATION")
        # 放射液生长 ×2：100 秒相当于 200 秒 ⇒ 已到成熟期
        assert tile["stage"] == STAGE_MATURE
        result = advance_tile(tile, _plants()["ordinary_moss"], seconds=70, medium="RADIATION")
        assert result["withered"] is True
        assert tile["stage"] == STAGE_WITHERED
        assert tile["seed_id"] == "ordinary_moss"  # 枯萎不清空，等玩家/机械臂处理

    def test_zero_g_never_withers(self):
        tile = _tile(age=400.0)
        advance_tile(tile, _plants()["ordinary_moss"], seconds=600, medium="ZERO_G")
        assert tile["stage"] == STAGE_MATURE  # 停在成熟期，永不老化

    def test_mutation_is_deterministic_expected_value(self):
        """零重力保鲜液下成熟株不会枯萎，突变进度按期望值稳定累积。"""
        tile = _tile(stage=STAGE_MATURE, age=200.0)
        result = advance_tile(tile, _plants()["ordinary_moss"], seconds=200, medium="ZERO_G")
        # 基础 8%/30s ⇒ 期望 0.00267/s；200 秒累计 0.53 < 1 ⇒ 尚未突变
        assert result["mutation_ready"] is False
        result = advance_tile(tile, _plants()["ordinary_moss"], seconds=200, medium="ZERO_G")
        assert result["mutation_ready"] is True  # 累计满 1.0 才突变（可复现，无随机数）

    def test_expand_picks_center_outwards(self):
        grid = [
            {"x": 0, "y": 0, "unlocked": False},
            {"x": 3, "y": 2, "unlocked": False},
            {"x": 4, "y": 2, "unlocked": False},
        ]
        chosen = next_expand_tile(grid)
        assert (chosen["x"], chosen["y"]) == (3, 2)  # 离中心最近

    def test_halo_summary_counts_living_plants(self):
        grid = [
            {"x": 2, "y": 2, "unlocked": True, "seed_id": "glow_moss", "stage": STAGE_MATURE},
            {"x": 3, "y": 2, "unlocked": True, "seed_id": "glow_moss", "stage": STAGE_MATURE},
            {"x": 4, "y": 2, "unlocked": True, "seed_id": "silent_grass", "stage": STAGE_MATURE},
            {"x": 4, "y": 3, "unlocked": True, "seed_id": "glow_moss", "stage": STAGE_WITHERED},
            {"x": 0, "y": 0, "unlocked": False, "seed_id": "glow_moss", "stage": STAGE_MATURE},
        ]
        halo = halo_summary(grid, _plants())
        assert halo["power_kw"] == pytest.approx(10.0)  # 两株存活荧光苔藓（枯萎/未解锁不计）
        assert halo["suspicion_per_sec"] == pytest.approx(-0.002)


async def _bootstrap(client) -> dict:
    return (await client.get("/api/v1/colony/state")).json()["data"]


async def _rewind(session, seconds: int) -> None:
    await session.rollback()
    colony = await session.get(ColonyState, (1, 0))
    colony.last_tick_time = int(time.time()) - seconds
    await session.commit()


class TestGardenApi:
    async def test_new_game_grid_is_7x7_with_center_unlocked(self, client):
        await _bootstrap(client)
        data = (await client.get(f"{GARDEN_URL}/state")).json()["data"]
        assert len(data["grid"]) == 49
        assert data["unlocked_cells"] == 9
        assert data["grid_size"] == 3
        assert data["codex"] == ["ordinary_moss"]
        assert data["codex_total"] == 14
        assert data["current_medium"] == "STERILE"
        unlocked = [tile for tile in data["grid"] if tile["unlocked"]]
        assert all(2 <= tile["x"] <= 4 and 2 <= tile["y"] <= 4 for tile in unlocked)

    async def test_plant_and_harvest_round_trip(self, client, session):
        await _bootstrap(client)
        planted = (
            await client.post(
                f"{GARDEN_URL}/action", json={"action": "PLANT", "x": 3, "y": 3, "seed_id": "ordinary_moss"}
            )
        ).json()["data"]
        assert planted["plant_name"] == "普通猫薄荷"

        early = await client.post(f"{GARDEN_URL}/action", json={"action": "HARVEST", "x": 3, "y": 3})
        assert early.status_code == 409
        assert early.json()["message"] == "PLANT_NOT_MATURE"

        # 用零重力保鲜液 + 离线推进到成熟期（每阶段 90 秒 × 2）
        await _rewind(session, 200)
        await client.get("/api/v1/colony/state")
        harvested = (
            await client.post(f"{GARDEN_URL}/action", json={"action": "HARVEST", "x": 3, "y": 3})
        ).json()["data"]
        assert harvested["gained"]["catnip"] > 0
        assert harvested["new_codex_entry"] is False  # 普通猫薄荷本来就在图鉴里

    async def test_plant_requires_unlocked_cell_and_seed(self, client):
        await _bootstrap(client)
        locked_cell = await client.post(
            f"{GARDEN_URL}/action", json={"action": "PLANT", "x": 0, "y": 0, "seed_id": "ordinary_moss"}
        )
        assert locked_cell.status_code == 400
        assert locked_cell.json()["message"] == "CELL_LOCKED"

        locked_seed = await client.post(
            f"{GARDEN_URL}/action", json={"action": "PLANT", "x": 3, "y": 3, "seed_id": "golden_grass"}
        )
        assert locked_seed.status_code == 400
        assert locked_seed.json()["message"] == "SEED_LOCKED"

        unknown = await client.post(
            f"{GARDEN_URL}/action", json={"action": "PLANT", "x": 3, "y": 3, "seed_id": "moon_flower"}
        )
        assert unknown.status_code == 400

    async def test_occupied_cell_is_rejected(self, client):
        await _bootstrap(client)
        payload = {"action": "PLANT", "x": 3, "y": 3, "seed_id": "ordinary_moss"}
        await client.post(f"{GARDEN_URL}/action", json=payload)
        again = await client.post(f"{GARDEN_URL}/action", json=payload)
        assert again.status_code == 409
        assert again.json()["message"] == "CELL_OCCUPIED"

    async def test_expand_one_cell_at_a_time_with_cost(self, client, session):
        await _bootstrap(client)
        await session.rollback()
        colony = await session.get(ColonyState, (1, 0))
        colony.scrap = 60.0
        await session.commit()
        data = (await client.post(f"{GARDEN_URL}/action", json={"action": "EXPAND"})).json()["data"]
        assert data["unlocked_cells"] == 10
        assert data["cost_paid"] == {"scrap": 15.0}
        assert data["next_cost"]["scrap"] == pytest.approx(18.0)  # 15 × 1.15 向上取整
        assert data["grid_size"] == 4  # ceil(sqrt(10))

    async def test_expand_requires_material(self, client, session):
        await _bootstrap(client)
        await session.rollback()
        colony = await session.get(ColonyState, (1, 0))
        colony.scrap = 0.0
        await session.commit()
        response = await client.post(f"{GARDEN_URL}/action", json={"action": "EXPAND"})
        assert response.status_code == 400
        assert response.json()["message"] == "INSUFFICIENT_RESOURCE"

    async def test_medium_and_arm_switches(self, client):
        await _bootstrap(client)
        medium = (
            await client.post(f"{GARDEN_URL}/action", json={"action": "MEDIUM", "medium": "RADIATION"})
        ).json()["data"]
        assert medium["growth_multiplier"] == 2.0
        arm = (
            await client.post(
                f"{GARDEN_URL}/action", json={"action": "ARM", "enabled": True, "auto_protect_unknown": True}
            )
        ).json()["data"]
        assert arm["mechanical_arm_enabled"] is True
        bad = await client.post(f"{GARDEN_URL}/action", json={"action": "MEDIUM", "medium": "SODA"})
        assert bad.status_code == 400

    async def test_offline_growth_advances_stage(self, client, session):
        await _bootstrap(client)
        await client.post(
            f"{GARDEN_URL}/action", json={"action": "PLANT", "x": 3, "y": 3, "seed_id": "ordinary_moss"}
        )
        await _rewind(session, 95)
        await client.get("/api/v1/colony/state")
        tile = next(
            item
            for item in (await client.get(f"{GARDEN_URL}/state")).json()["data"]["grid"]
            if item["x"] == 3 and item["y"] == 3
        )
        assert tile["stage"] == STAGE_JOINTING
        assert tile["age"] == pytest.approx(95, abs=3)

    async def test_radiation_mutation_spawns_neighbor(self, client, session):
        """零重力保鲜液是"突变农场"：成熟后永不枯萎，突变进度能一直攒到触发。"""
        await _bootstrap(client)
        await client.post(f"{GARDEN_URL}/action", json={"action": "MEDIUM", "medium": "ZERO_G"})
        await client.post(
            f"{GARDEN_URL}/action", json={"action": "PLANT", "x": 3, "y": 3, "seed_id": "ordinary_moss"}
        )
        await _rewind(session, 800)
        state = (await client.get("/api/v1/colony/state")).json()["data"]
        assert any("突变" in note for note in state["offline_report"]["notes"])
        grid = (await client.get(f"{GARDEN_URL}/state")).json()["data"]["grid"]
        center = next(item for item in grid if item["x"] == 3 and item["y"] == 3)
        neighbours = [item for item in grid if (item["x"], item["y"]) in {(2, 3), (4, 3), (3, 2), (3, 4)}]
        assert center["stage"] in (STAGE_MATURE, STAGE_WITHERED)
        assert any(item["seed_id"] for item in neighbours)  # 突变苗长在相邻格上

    async def test_halo_power_reaches_colony_state(self, client, session):
        """在田光环联动：荧光苔藓 +5 kW 直接进净电力平衡。"""
        await _bootstrap(client)
        await session.rollback()
        garden = await session.get(GardenState, (1, 0))
        grid = [dict(tile) for tile in garden.grid_data]
        for tile in grid:
            if (tile["x"], tile["y"]) == (3, 3):
                tile.update({"seed_id": "glow_moss", "stage": STAGE_MATURE, "age": 200.0})
        garden.grid_data = grid
        garden.unlocked_seed_ids = ["ordinary_moss", "glow_moss"]
        await session.commit()
        before = (await client.get("/api/v1/colony/state")).json()["data"]["power"]["gen_kw"]
        await session.rollback()
        garden = await session.get(GardenState, (1, 0))
        grid = [dict(tile) for tile in garden.grid_data]
        for tile in grid:
            if (tile["x"], tile["y"]) == (3, 3):
                tile.update({"seed_id": None, "stage": None, "age": 0.0})
        garden.grid_data = grid
        await session.commit()
        after = (await client.get("/api/v1/colony/state")).json()["data"]["power"]["gen_kw"]
        assert before - after == pytest.approx(5.0)
