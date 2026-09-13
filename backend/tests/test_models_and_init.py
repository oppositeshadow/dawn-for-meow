"""数据底座验收：16 张表结构、新游戏初始化流程（数据库设计定稿 §2.4）、种子与数值一致性。"""

from __future__ import annotations

from collections import Counter

import pytest
from sqlalchemy import select

from app.core import balance as B
from app.core.database import Base
from app.core.errors import Conflict
from app.core.seed_loader import (
    facility_defs,
    job_defs,
    minigame_defs,
    tech_defs_planet0,
)
from app.models import (
    BossState,
    CareerStats,
    ColonyState,
    DarknetState,
    FacilityState,
    GardenState,
    LaborBucket,
    MilitaryState,
    MinigameState,
    PlanetState,
    SaveSlot,
    TechRecord,
)
from app.models.tech import TechStatus
from app.services.game_init_service import build_initial_grid, create_new_game, starter_stock_quotes

EXPECTED_TABLES = {
    "save_slot",
    "colony_state",
    "labor_buckets",
    "facility_state",
    "planet_state",
    "tech_records",
    "garden_state",
    "darknet_state",
    "forum_posts",
    "military_state",
    "vehicle_units",
    "boss_state",
    "career_stats",
    "achievements",
    "event_templates",
    "minigame_state",
}


class TestSchema:
    async def test_exactly_16_tables(self, tables: list[str]):
        assert set(tables) == EXPECTED_TABLES
        assert len(tables) == 16

    async def test_colony_state_columns_match_ddl(self, tables: list[str]):
        columns = {c.name for c in Base.metadata.tables["colony_state"].columns}
        assert columns == {
            "slot_id", "planet_id", "last_tick_time",
            "catnip", "catnip_max", "scrap", "scrap_max", "chips", "chips_max",
            "alloys", "alloys_max", "battery", "battery_max", "lube", "lube_max",
            "manual_scavenge_clicks",
            "power_net", "battery_kwh", "battery_kwh_max",
            "total_cats", "birth_progress", "job_idle", "suspicion",
            "labor_automation_policy", "smelt_automation_policy",
        }
        # 设施列与工种列已外移（抽象化改造后不应再出现在 colony_state）
        assert "housing_boxes" not in columns
        assert "job_farmers" not in columns

    async def test_primary_keys_follow_conventions(self, tables: list[str]):
        def pk(table: str) -> list[str]:
            return [c.name for c in Base.metadata.tables[table].primary_key.columns]

        assert pk("colony_state") == ["slot_id", "planet_id"]
        assert pk("labor_buckets") == ["slot_id", "planet_id", "job_id"]
        assert pk("facility_state") == ["slot_id", "planet_id", "facility_id"]
        assert pk("tech_records") == ["slot_id", "planet_id", "tech_id"]
        assert pk("darknet_state") == ["slot_id"]
        assert pk("boss_state") == ["slot_id"]
        assert pk("career_stats") == ["slot_id"]
        assert pk("minigame_state") == ["slot_id", "planet_id", "minigame_id"]
        assert pk("vehicle_units") == ["unit_id"]

    def test_declares_16_models(self):
        from app.models import TABLE_COUNT

        assert TABLE_COUNT == 16


class TestNewGameInit:
    async def test_cold_start_writes_zeroed_state(self, session):
        """§2.4：新档不"送"资源，全靠玩家手点废墟攒 5 废铁。"""
        await create_new_game(session, 1, now=1_700_000_000)
        await session.commit()

        save = await session.get(SaveSlot, 1)
        assert save is not None
        assert save.save_version == B.SAVE_VERSION
        assert save.unity == 0.0
        assert save.doctrines == {}
        assert save.active_planet_id == B.HOME_PLANET_ID

        colony = await session.get(ColonyState, (1, 0))
        assert colony is not None
        for key in B.RESOURCE_KEYS:
            assert getattr(colony, key) == 0.0
            assert getattr(colony, f"{key}_max") == B.RESOURCE_CAPS[key]
        assert colony.total_cats == 0
        assert colony.job_idle == 0
        assert colony.birth_progress == 0.0
        assert colony.suspicion == 0.0
        assert colony.battery_kwh_max == B.BATTERY_KWH_MAX
        assert colony.last_tick_time == 1_700_000_000

    async def test_labor_buckets_and_facility_rows(self, session):
        await create_new_game(session, 1, now=1_700_000_000)
        await session.commit()

        buckets = (
            await session.execute(select(LaborBucket).where(LaborBucket.slot_id == 1))
        ).scalars().all()
        assert {b.job_id for b in buckets} == set(B.PLANET_JOBS)
        assert all(b.cat_count == 0 for b in buckets)

        facilities = (
            await session.execute(select(FacilityState).where(FacilityState.slot_id == 1))
        ).scalars().all()
        assert {f.facility_id for f in facilities} == set(B.FACILITY_IDS)
        assert len(facilities) == len(facility_defs()) == 12
        assert all(f.level == 0 for f in facilities)

    async def test_planet_state_home_unlocked_others_locked(self, session):
        await create_new_game(session, 1, now=1_700_000_000)
        await session.commit()

        planets = (
            await session.execute(select(PlanetState).where(PlanetState.slot_id == 1))
        ).scalars().all()
        assert len(planets) == 4
        home = next(p for p in planets if p.planet_id == 0)
        assert home.unlocked is True
        assert home.is_active is True
        assert home.biome_tag
        assert home.unlocked_at == 1_700_000_000
        for planet in planets:
            assert planet.logistics_routes == []
            if planet.planet_id != 0:
                assert planet.unlocked is False
                assert planet.is_active is False
                assert planet.biome_tag is None

    async def test_tech_tree_written_as_locked(self, session):
        await create_new_game(session, 1, now=1_700_000_000)
        await session.commit()

        records = (
            await session.execute(select(TechRecord).where(TechRecord.slot_id == 1))
        ).scalars().all()
        assert len(records) == B.PLANET0_TECH_NODE_COUNT == 19
        assert all(r.status == TechStatus.LOCKED for r in records)
        assert all(r.current_progress == 0.0 for r in records)
        assert all(r.is_agent_generated is False for r in records)
        tiers = Counter(r.tier for r in records)
        assert tiers == Counter({1: 4, 2: 4, 3: 5, 4: 6})

    async def test_singleton_rows_created(self, session):
        await create_new_game(session, 2, now=1_700_000_000)
        await session.commit()

        assert await session.get(CareerStats, 2) is not None
        military = await session.get(MilitaryState, (2, 0))
        assert military.hangar_capacity == 12
        assert military.security_policy["p1_use_decoy"] is True
        assert military.active_expeditions == []

        boss = await session.get(BossState, 2)
        assert boss.threat_level == 1
        assert boss.fleet_strength == 500.0
        assert boss.rage == 0.0
        assert boss.bombardment_state["active"] is False

        darknet = await session.get(DarknetState, 2)
        assert darknet.burner_id == "robot_4a932cz"
        assert {q["stock_id"] for q in darknet.stocks_data} == set(B.STARTER_STOCKS)
        assert darknet.positions == [] and darknet.short_contracts == []

    async def test_garden_starts_with_center_3x3(self, session):
        await create_new_game(session, 1, now=1_700_000_000)
        await session.commit()

        garden = await session.get(GardenState, (1, 0))
        assert garden.grid_size == 3
        assert garden.unlocked_cells == 9
        assert garden.current_medium == "STERILE"
        assert len(garden.grid_data) == 49
        unlocked = [tile for tile in garden.grid_data if tile["unlocked"]]
        assert len(unlocked) == 9
        assert all(2 <= tile["x"] <= 4 and 2 <= tile["y"] <= 4 for tile in unlocked)
        # 初始母本：普通猫薄荷（其余靠杂交突变解锁图鉴）
        assert garden.unlocked_seed_ids == ["ordinary_moss"]

    async def test_minigame_rows_created(self, session):
        await create_new_game(session, 1, now=1_700_000_000)
        await session.commit()

        rows = (
            await session.execute(select(MinigameState).where(MinigameState.slot_id == 1))
        ).scalars().all()
        assert {r.minigame_id for r in rows} == {g["minigame_id"] for g in minigame_defs()}
        assert {r.planet_id for r in rows} == {g["planet_id"] for g in minigame_defs()}

    async def test_slots_are_isolated(self, session):
        """N-1：不同槽位数据完全隔离。"""
        await create_new_game(session, 1, now=1_700_000_000)
        await create_new_game(session, 2, now=1_700_000_000)
        await session.commit()
        colony_one = await session.get(ColonyState, (1, 0))
        colony_one.catnip = 42.0
        await session.commit()
        assert (await session.get(ColonyState, (2, 0))).catnip == 0.0

    async def test_duplicate_slot_raises_conflict(self, session):
        await create_new_game(session, 1, now=1_700_000_000)
        await session.commit()
        with pytest.raises(Conflict) as exc:
            await create_new_game(session, 1)
        assert exc.value.code == 409
        assert exc.value.message == "SAVE_SLOT_EXISTS"

    async def test_invalid_slot_rejected(self, session):
        from app.core.errors import BadRequest

        with pytest.raises(BadRequest):
            await create_new_game(session, 9)

    def test_initial_grid_is_deterministic(self):
        assert build_initial_grid() == build_initial_grid()

    def test_starter_stock_quotes_follow_balance_sheet(self):
        quotes = {q["stock_id"]: q for q in starter_stock_quotes()}
        assert quotes["FORGE"]["price"] == 45.0
        assert quotes["FORGE"]["forecast"] == pytest.approx(0.70)
        assert quotes["HELIUM3"]["forecast"] == pytest.approx(0.40)
        assert quotes["GRID"]["spread"] == 0.004


class TestSeedConsistency:
    """定义进 JSON、数值进 balance.py：两边绝不能漂移。"""

    def test_facility_seed_matches_balance(self):
        seeds = {item["facility_id"]: item for item in facility_defs()}
        assert set(seeds) == set(B.FACILITY_IDS)
        for facility_id, spec in B.FACILITY_SPECS.items():
            seed = seeds[facility_id]
            assert seed["name"] == spec["name"]
            assert seed.get("growth") == spec.get("growth")
            assert seed.get("max_level") == spec.get("max_level")
            assert {k: float(v) for k, v in seed["cost"].items()} == {
                k: float(v) for k, v in spec["cost"].items()
            }
            for effect_key, effect_value in seed["effects"].items():
                assert spec["effects"][effect_key] == effect_value, f"{facility_id}.{effect_key}"
            assert set(seed["effects"]) == set(spec["effects"]), facility_id

    def test_job_seed_matches_balance(self):
        seeds = {item["job_id"]: item for item in job_defs()}
        assert set(seeds) >= set(B.PLANET_JOBS)
        assert set(seeds) == set((*B.PLANET_JOBS, *B.STAR_JOBS))
        assert seeds["farmer"]["output"]["rate_per_second"] == B.FARMER_CATNIP_PER_SEC
        assert seeds["scavenger"]["output"]["rate_per_second"] == B.SCAVENGER_SCRAP_PER_SEC
        assert seeds["geek"]["output"]["rate_per_second"] == B.GEEK_RESEARCH_PER_SEC
        assert seeds["power_runner"]["output"]["rate_per_second"] == B.POWER_RUNNER_KW
        assert seeds["purr_master"]["output"]["rate_per_second"] == B.UNITY_PER_PURR_MASTER_PER_SEC
        assert seeds["farmer"]["workstation"] == {"facility_id": "farm_plot", "slots_per_level": 2}
        assert seeds["geek"]["workstation"]["slots_per_level"] == 1
        for job_id, (facility_id, slots) in B.WORKSTATION_SOURCES.items():
            assert seeds[job_id]["workstation"]["facility_id"] == facility_id
            assert seeds[job_id]["workstation"]["slots_per_level"] == slots

    def test_minigame_seed_matches_balance(self):
        seeds = {item["minigame_id"]: item for item in minigame_defs()}
        assert set(seeds) == set(B.MINIGAME_SPECS)
        assert seeds["cipher_decode"]["planet_id"] == B.MINIGAME_SPECS["cipher_decode"]["planet_id"]
        assert seeds["cipher_decode"]["quota"]["amount"] == B.MINIGAME_SPECS["cipher_decode"]["daily_quota"]
        assert seeds["vein_scan"]["quota"]["max"] == B.MINIGAME_SPECS["vein_scan"]["quota_max"]
        assert (
            seeds["vein_scan"]["quota"]["regen_seconds"]
            == B.MINIGAME_SPECS["vein_scan"]["quota_regen_seconds"]
        )
        assert (
            seeds["forge_recipe"]["quota"]["cost"]["scrap"]
            == B.MINIGAME_SPECS["forge_recipe"]["scrap_cost_per_round"]
        )

    def test_tech_tree_costs_match_balance_ladder(self):
        techs = tech_defs_planet0()
        assert len(techs) == B.PLANET0_TECH_NODE_COUNT
        assert sum(t["target_cost"] for t in techs) == B.PLANET0_TOTAL_RESEARCH

        for tier, costs in B.TECH_TIER_COSTS.items():
            tier_techs = sorted(
                (t for t in techs if t["tier"] == tier), key=lambda t: t["node_order"]
            )
            assert [t["target_cost"] for t in tier_techs] == list(costs), f"tier {tier}"

    def test_tech_tree_is_a_valid_dag(self):
        techs = {t["tech_id"]: t for t in tech_defs_planet0()}
        assert len(techs) == 19
        for tech in techs.values():
            for parent_id in tech["parent_ids"]:
                assert parent_id in techs, f"{tech['tech_id']} 的前置 {parent_id} 不存在"
                assert techs[parent_id]["tier"] <= tech["tier"]
            assert tech["tech_id"].startswith("tech_")

    def test_tier1_nodes_have_no_parents(self):
        techs = tech_defs_planet0()
        tier1 = [t for t in techs if t["tier"] == 1]
        assert len(tier1) == 4
        roots = [t for t in tier1 if not t["parent_ids"]]
        assert {t["tech_id"] for t in roots} == {
            "tech_cardboard_mechanics",
            "tech_hydroponics_basics",
            "tech_appliance_teardown",
        }
