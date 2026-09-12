"""模块 G 尾款验收：战术电力指令、伏击矿石车队、战略巡航导弹。"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import select

from app.core import balance as B
from app.models import BossState, ColonyState, MilitaryState, VehicleUnit
from app.models.military import VehicleStatus

MILITARY_URL = "/api/v1/military"
VEHICLE_URL = "/api/v1/vehicle"


async def _bootstrap(client) -> dict:
    return (await client.get("/api/v1/colony/state")).json()["data"]


async def _setup_fleet(session, unit_type: str = "armored_car", *, count: int = 1) -> list[int]:
    """直接建载具行（跳过组装校验，专注战术/伏击逻辑）。"""
    await session.rollback()
    colony = await session.get(ColonyState, (1, 0))
    colony.scrap_max = 2000.0
    colony.scrap = 2000.0
    colony.chips_max = 500.0
    colony.chips = 100.0  # 留出空间，便于断言伏击缴获
    colony.alloys_max = 500.0
    colony.alloys = 100.0
    colony.battery = 50.0
    colony.battery_kwh = 200.0
    colony.total_cats += count * 4
    colony.job_idle += count * 4
    await session.commit()
    unit_ids: list[int] = []
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
        unit_ids.append(int(unit.unit_id))
    await session.commit()
    return unit_ids


async def _open_convoy(session, *, ends_in: int = 600) -> None:
    await session.rollback()
    boss = await session.get(BossState, 1)
    boss.convoy_ends_at = int(time.time()) + ends_in
    await session.commit()


class TestTacticalAction:
    async def test_overclock_spends_battery_and_arms_buff(self, client, session):
        await _bootstrap(client)
        await _setup_fleet(session)
        data = (
            await client.post(f"{MILITARY_URL}/tactical-action", json={"command": "OVERCLOCK"})
        ).json()["data"]
        assert data["cost_kwh"] == B.TACTICAL_COMMAND_COST_KWH["OVERCLOCK"]
        assert data["battery_kwh"] == pytest.approx(180.0)
        await session.rollback()
        military = await session.get(MilitaryState, (1, 0), populate_existing=True)
        assert military.security_policy["tactical"]["command"] == "OVERCLOCK"

    async def test_emp_costs_15kwh(self, client, session):
        await _bootstrap(client)
        await _setup_fleet(session)
        data = (await client.post(f"{MILITARY_URL}/tactical-action", json={"command": "EMP"})).json()["data"]
        assert data["cost_kwh"] == B.TACTICAL_COMMAND_COST_KWH["EMP"]
        assert data["battery_kwh"] == pytest.approx(185.0)

    async def test_insufficient_battery_is_rejected(self, client, session):
        await _bootstrap(client)
        await _setup_fleet(session)
        await session.rollback()
        colony = await session.get(ColonyState, (1, 0))
        colony.battery_kwh = 5.0
        await session.commit()
        response = await client.post(f"{MILITARY_URL}/tactical-action", json={"command": "OVERCLOCK"})
        assert response.status_code == 400
        assert response.json()["message"] == "INSUFFICIENT_RESOURCE"

    async def test_unknown_command_rejected(self, client):
        await _bootstrap(client)
        assert (
            await client.post(f"{MILITARY_URL}/tactical-action", json={"command": "NUKE"})
        ).status_code == 400

    async def test_eject_recalls_expedition_with_debris(self, client, session):
        await _bootstrap(client)
        unit_ids = await _setup_fleet(session, "light_car")
        await client.post(
            f"{MILITARY_URL}/dispatch", json={"target_id": "WALMART", "unit_ids": unit_ids}
        )
        data = (await client.post(f"{MILITARY_URL}/tactical-action", json={"command": "EJECT"})).json()["data"]
        assert data["command"] == "EJECT"
        assert data["recalled"] == 1
        assert data["refund"]["scrap"] == pytest.approx(20.0)  # 40 × 50%
        await session.rollback()
        unit = (await session.execute(select(VehicleUnit).where(VehicleUnit.unit_id == unit_ids[0]))).scalars().one()
        assert unit.status == VehicleStatus.IDLE

    async def test_eject_without_expedition_is_rejected(self, client, session):
        await _bootstrap(client)
        await _setup_fleet(session)
        response = await client.post(f"{MILITARY_URL}/tactical-action", json={"command": "EJECT"})
        assert response.status_code == 409
        assert response.json()["message"] == "NO_ACTIVE_BATTLE"


class TestAmbushConvoy:
    async def test_requires_active_convoy(self, client, session):
        await _bootstrap(client)
        unit_ids = await _setup_fleet(session)
        await session.rollback()
        boss = await session.get(BossState, 1)
        boss.convoy_ends_at = None
        await session.commit()
        response = await client.post(f"{MILITARY_URL}/ambush-convoy", json={"unit_ids": unit_ids})
        assert response.status_code == 409
        assert response.json()["message"] == "NO_CONVOY"

    async def test_missed_window_is_rejected(self, client, session):
        await _bootstrap(client)
        unit_ids = await _setup_fleet(session)
        await _open_convoy(session, ends_in=-5)
        response = await client.post(f"{MILITARY_URL}/ambush-convoy", json={"unit_ids": unit_ids})
        assert response.status_code == 409
        assert response.json()["message"] == "CONVOY_MISSED"

    async def test_light_car_cannot_ambush(self, client, session):
        await _bootstrap(client)
        unit_ids = await _setup_fleet(session, "light_car")
        await _open_convoy(session)
        response = await client.post(f"{MILITARY_URL}/ambush-convoy", json={"unit_ids": unit_ids})
        assert response.status_code == 400
        assert "全地形装甲猫车" in response.json()["detail"]

    async def test_ambush_win_loots_and_freezes_factory(self, client, session):
        await _bootstrap(client)
        unit_ids = await _setup_fleet(session, "breaker_mech")
        await _open_convoy(session)
        data = (await client.post(f"{MILITARY_URL}/ambush-convoy", json={"unit_ids": unit_ids})).json()["data"]
        assert data["won"] is True
        assert data["loot"]["alloys"] > 0
        assert data["factory_frozen_until"] > int(time.time())
        await session.rollback()
        boss = await session.get(BossState, 1, populate_existing=True)
        assert boss.factory_frozen_until is not None
        assert boss.convoy_ends_at is None

    async def test_ambush_loss_damages_vehicle_and_hospitalizes_crew(self, client, session):
        await _bootstrap(client)
        unit_ids = await _setup_fleet(session)
        await session.rollback()
        unit = await session.get(VehicleUnit, unit_ids[0])
        unit.hull = 10.0
        unit.armor = 0.0
        unit.shield = 0.0
        await session.commit()
        await _open_convoy(session)
        data = (await client.post(f"{MILITARY_URL}/ambush-convoy", json={"unit_ids": unit_ids})).json()["data"]
        assert data["won"] is False
        await session.rollback()
        unit = await session.get(VehicleUnit, unit_ids[0], populate_existing=True)
        military = await session.get(MilitaryState, (1, 0), populate_existing=True)
        assert unit.status == VehicleStatus.REPAIR
        assert military.hospital_queue  # 乘员进急救舱（绝无死猫）

    async def test_tactical_buff_is_consumed_by_one_battle(self, client, session):
        await _bootstrap(client)
        unit_ids = await _setup_fleet(session, "breaker_mech")
        await client.post(f"{MILITARY_URL}/tactical-action", json={"command": "EMP"})
        await _open_convoy(session)
        data = (await client.post(f"{MILITARY_URL}/ambush-convoy", json={"unit_ids": unit_ids})).json()["data"]
        assert data["tactical_buff"]["command"] == "EMP"
        await session.rollback()
        military = await session.get(MilitaryState, (1, 0), populate_existing=True)
        assert "tactical" not in (military.security_policy or {})


class TestMissile:
    async def test_assemble_requires_materials(self, client, session):
        await _bootstrap(client)
        await session.rollback()
        colony = await session.get(ColonyState, (1, 0))
        colony.alloys = 0.0
        colony.chips = 0.0
        colony.battery = 0.0
        await session.commit()
        response = await client.post(f"{MILITARY_URL}/assemble-missile", json={})
        assert response.status_code == 400
        assert response.json()["message"] == "INSUFFICIENT_RESOURCE"

    async def test_assemble_then_launch_destroys_fortress(self, client, session):
        await _bootstrap(client)
        await _setup_fleet(session)
        assembled = (await client.post(f"{MILITARY_URL}/assemble-missile", json={})).json()["data"]
        assert assembled["cruise_missiles"] == 1
        assert assembled["cost_paid"] == B.MISSILE_ASSEMBLE_COST

        await session.rollback()
        boss_before = await session.get(BossState, 1, populate_existing=True)
        threat_before, fleet_before = int(boss_before.threat_level), float(boss_before.fleet_strength)

        launched = (await client.post(f"{MILITARY_URL}/launch-missile", json={})).json()["data"]
        assert launched["cruise_missiles"] == 0
        assert launched["fortress_destroyed_at"] > 0
        assert launched["threat_level"] <= threat_before
        assert launched["fleet_strength"] == pytest.approx(fleet_before * 0.8, abs=0.5)
        assert launched["rage"] >= B.MISSILE_LAUNCH_RAGE_GAIN
        assert launched["intel_level"] == pytest.approx(B.MISSILE_LAUNCH_INTEL_GAIN / 100, abs=1e-6)

        again = await client.post(f"{MILITARY_URL}/launch-missile", json={})
        assert again.status_code == 409
        assert again.json()["message"] == "NO_MISSILE"


class TestHangarOpsView:
    async def test_hangar_exposes_convoy_and_fortress_state(self, client, session):
        await _bootstrap(client)
        await _open_convoy(session, ends_in=300)
        data = (await client.get(f"{VEHICLE_URL}/list")).json()["data"]
        assert data["convoy_ends_at"] > int(time.time())
        assert data["threat_level"] >= 1
        assert "rage" in data
        assert data["tactical_buff"] is None
