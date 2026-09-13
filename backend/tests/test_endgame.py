"""模块 L 验收：欧米茄后台演化、掠夺突袭拦截、星门决战与通关碑文。"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import select

from app.core import balance as B
from app.models import BossState, ColonyState, MilitaryState, VehicleUnit
from app.models.military import VehicleStatus

MILITARY_URL = "/api/v1/military"


async def _bootstrap(client) -> dict:
    return (await client.get("/api/v1/colony/state")).json()["data"]


async def _fleet(session, unit_type: str = "breaker_mech", *, count: int = 4) -> list[int]:
    await session.rollback()
    colony = await session.get(ColonyState, (1, 0))
    colony.alloys_max, colony.alloys = 2000.0, 2000.0
    colony.battery_max, colony.battery = 100.0, 100.0
    colony.scrap_max, colony.scrap = 2000.0, 2000.0
    colony.total_cats += count * 4
    colony.job_idle += count * 4
    await session.commit()
    ids: list[int] = []
    for _ in range(count):
        spec = B.VEHICLE_TYPES[unit_type]
        unit = VehicleUnit(
            slot_id=1,
            planet_id=0,
            unit_type=unit_type,
            nickname=None,
            modules=[],
            shield=float(spec["shield"]),
            armor=float(spec["armor"]),
            armor_max=float(spec["armor"]),
            hull=float(spec["hull"]),
            status=VehicleStatus.IDLE,
            crew_cats=int(spec["crew"]),
            acquired_at=int(time.time()),
        )
        session.add(unit)
        await session.flush()
        ids.append(int(unit.unit_id))
    await session.commit()
    return ids


async def _rewind(session, seconds: int) -> None:
    await session.rollback()
    colony = await session.get(ColonyState, (1, 0))
    colony.last_tick_time = int(time.time()) - seconds
    await session.commit()


async def _heal(session, unit_ids: list[int]) -> None:
    """两场决战之间把编队修好（模拟玩家在机库里做完维修）。"""
    await session.rollback()
    for unit_id in unit_ids:
        unit = (
            await session.execute(
                select(VehicleUnit).where(VehicleUnit.unit_id == unit_id).execution_options(populate_existing=True)
            )
        ).scalars().one()
        spec = B.VEHICLE_TYPES[unit.unit_type]
        unit.status = VehicleStatus.IDLE
        unit.shield = float(spec["shield"])
        unit.armor = float(spec["armor"])
        unit.armor_max = float(spec["armor"])
        unit.hull = float(spec["hull"])
        unit.repair_ends_at = None
    await session.commit()


class TestBossEvolution:
    async def test_fleet_and_threat_grow_every_cycle(self, client, session):
        """L1：每 30 分钟 +25 舰队；每 3 个周期威胁 +1。"""
        await _bootstrap(client)
        await session.rollback()
        boss = await session.get(BossState, 1)
        boss.fleet_strength = 500.0
        boss.threat_level = 1
        await session.commit()
        await _rewind(session, int(B.BOSS_TICK_SECONDS * 6))  # 6 个周期
        state = (await client.get("/api/v1/colony/state")).json()["data"]
        assert any("后台演化" in note for note in state["offline_report"]["notes"])
        await session.rollback()
        boss = await session.get(BossState, 1, populate_existing=True)
        assert boss.fleet_strength == pytest.approx(500 + 6 * B.BOSS_FLEET_GAIN_PER_TICK)
        assert boss.threat_level == 3  # 6 // 3 = +2

    async def test_missed_raid_costs_resources(self, client, session):
        """突袭到期未拦截 ⇒ 物理资源 −15% 并记录设施受损。"""
        await _bootstrap(client)
        await session.rollback()
        colony = await session.get(ColonyState, (1, 0))
        colony.catnip, colony.scrap = 100.0, 100.0
        boss = await session.get(BossState, 1)
        boss.raid_ends_at = int(time.time()) - 5
        await session.commit()
        await _rewind(session, 60)
        state = (await client.get("/api/v1/colony/state")).json()["data"]
        assert any("掠夺舰队突袭得手" in note for note in state["offline_report"]["notes"])
        await session.rollback()
        colony = await session.get(ColonyState, (1, 0), populate_existing=True)
        assert colony.catnip == pytest.approx(85.0, abs=0.5)
        boss = await session.get(BossState, 1, populate_existing=True)
        assert boss.raid_ends_at > int(time.time())
        assert (boss.bombardment_state or {}).get("damaged_facilities")


class TestRaidIntercept:
    async def test_requires_active_raid_and_proper_fleet(self, client, session):
        await _bootstrap(client)
        unit_ids = await _fleet(session, "breaker_mech", count=1)
        await session.rollback()
        boss = await session.get(BossState, 1)
        boss.raid_ends_at = None
        await session.commit()
        no_raid = await client.post(f"{MILITARY_URL}/intercept-raid", json={"unit_ids": unit_ids})
        assert no_raid.status_code == 409
        assert no_raid.json()["message"] == "NO_RAID"

        light_ids = await _fleet(session, "light_car", count=1)
        await session.rollback()
        boss = await session.get(BossState, 1)
        boss.raid_ends_at = int(time.time()) + 600
        await session.commit()
        wrong_type = await client.post(f"{MILITARY_URL}/intercept-raid", json={"unit_ids": light_ids})
        assert wrong_type.status_code == 400

    async def test_winning_intercept_loots_and_reschedules(self, client, session):
        await _bootstrap(client)
        await _bootstrap(client)
        unit_ids = await _fleet(session, "breaker_mech", count=2)
        await session.rollback()
        boss = await session.get(BossState, 1)
        boss.raid_ends_at = int(time.time()) + 600
        colony = await session.get(ColonyState, (1, 0))
        colony.alloys, colony.chips = 0.0, 0.0
        await session.commit()
        data = (await client.post(f"{MILITARY_URL}/intercept-raid", json={"unit_ids": unit_ids})).json()["data"]
        assert data["won"] is True
        assert data["loot"]["alloys"] == B.RAID_INTERCEPT_LOOT["alloys"]
        assert data["next_raid_at"] > int(time.time())


class TestFinalAssault:
    async def test_stage_order_and_requirements(self, client, session):
        await _bootstrap(client)
        unit_ids = await _fleet(session, "breaker_mech", count=4)
        skipping = await client.post(
            f"{MILITARY_URL}/final-assault", json={"stage": 2, "unit_ids": unit_ids[:3]}
        )
        assert skipping.status_code == 409
        assert skipping.json()["message"] == "STAGE_ORDER"

        too_few = await client.post(
            f"{MILITARY_URL}/final-assault", json={"stage": 1, "unit_ids": unit_ids[:1]}
        )
        assert too_few.status_code == 400

    async def test_three_stage_clear_completes_game_with_epitaph(self, client, session, monkeypatch):
        """三战全胜 ⇒ 按下 Override Key 通关 + 碑文（无 key 时走本地模板）+ 演职员表。"""
        from app.core.config import get_settings

        await _bootstrap(client)
        await _bootstrap(client)
        monkeypatch.setattr(get_settings(), "llm_api_key", "")
        unit_ids = await _fleet(session, "breaker_mech", count=4)

        for stage in (1, 2, 3):
            await _heal(session, unit_ids)
            response = await client.post(
                f"{MILITARY_URL}/final-assault", json={"stage": stage, "unit_ids": unit_ids}
            )
            assert response.status_code == 200, response.text
            data = response.json()["data"]
            assert data["stage"] == stage
            assert data["cleared_stage"] == stage, data
            if stage < 3:
                assert data["completed"] is False
            else:
                assert data["completed"] is True
                assert data["epitaph"]["source"] == "FALLBACK"
                assert len(data["epitaph"]["epitaph"]) >= B.EPILOGUE_MIN_LENGTH
                assert data["staff_roll"]

        await session.rollback()
        boss = await session.get(BossState, 1, populate_existing=True)
        assert boss.fleet_strength == 0.0
        assert boss.rage == 0.0
        assert (boss.bombardment_state or {}).get("override_key_used_at")
        assert (boss.bombardment_state or {}).get("epitaph")

        again = await client.post(f"{MILITARY_URL}/final-assault", json={"stage": 3, "unit_ids": unit_ids})
        assert again.status_code == 409
        assert again.json()["message"] == "ALREADY_COMPLETED"

    async def test_insufficient_material_blocks_stage(self, client, session):
        await _bootstrap(client)
        unit_ids = await _fleet(session, "breaker_mech", count=4)
        await session.rollback()
        colony = await session.get(ColonyState, (1, 0))
        colony.alloys = 0.0
        await session.commit()
        response = await client.post(
            f"{MILITARY_URL}/final-assault", json={"stage": 1, "unit_ids": unit_ids[:2]}
        )
        assert response.status_code == 400
        assert response.json()["message"] == "INSUFFICIENT_RESOURCE"

    async def test_invalid_stage_rejected(self, client, session):
        await _bootstrap(client)
        response = await client.post(
            f"{MILITARY_URL}/final-assault", json={"stage": 9, "unit_ids": [1]}
        )
        assert response.status_code == 400  # Pydantic 边界被统一收敛成 400 BAD_REQUEST


class TestHangarEndgameView:
    async def test_hangar_exposes_raid_and_progress(self, client, session):
        await _bootstrap(client)
        await session.rollback()
        boss = await session.get(BossState, 1)
        boss.raid_ends_at = int(time.time()) + 900
        await session.commit()
        data = (await client.get("/api/v1/vehicle/list")).json()["data"]
        assert data["raid_ends_at"] > int(time.time())
        assert data["fleet_strength"] >= 0
        assert data["final_stage_cleared"] == 0
        assert data["completed"] is False
        assert data["epitaph"] is None
