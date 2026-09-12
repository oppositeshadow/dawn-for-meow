"""模块 G 验收：三层抗性交火、载具组装/维修/拆解、异步远征与战车截杀。"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import select

from app.core import balance as B
from app.core.combat_engine import combat_power, morale_multiplier, resolve_attack, resolve_skirmish, unit_from_spec
from app.models import ColonyState, VehicleUnit
from app.models.military import VehicleStatus

VEHICLE_URL = "/api/v1/vehicle"
MILITARY_URL = "/api/v1/military"


class TestCombatEngine:
    def test_laser_melts_shield_kinetic_does_not(self):
        """§9.2：LASER 对护盾 ×1.8，KINETIC ×0.8。"""
        laser_target = {"shield": 100.0, "armor": 0.0, "hull": 100.0, "armor_max": 0.0}
        kinetic_target = {"shield": 100.0, "armor": 0.0, "hull": 100.0, "armor_max": 0.0}
        resolve_attack(10.0, "LASER", laser_target)
        resolve_attack(10.0, "KINETIC", kinetic_target)
        assert laser_target["shield"] == pytest.approx(100 - 18.0)
        assert kinetic_target["shield"] == pytest.approx(100 - 8.0)

    def test_shield_overflow_spills_into_armor_and_hull(self):
        target = {"shield": 5.0, "armor": 400.0, "hull": 500.0, "armor_max": 400.0}
        _, damage, ejected = resolve_attack(100.0, "LASER", target)
        assert target["shield"] == 0.0
        assert damage > 0 and not ejected

    def test_armor_reduction_uses_400_divisor_and_caps_at_75pct(self):
        """修正项：减伤率 = min(75%, 装甲 ÷ 400)，所以 400 装甲才吃满上限。"""
        target = {"shield": 0.0, "armor": 400.0, "hull": 1000.0, "armor_max": 400.0}
        _, damage, _ = resolve_attack(100.0, "LASER", target)
        assert damage == pytest.approx(25.0)  # 100 × (1 − 0.75)

    def test_kinetic_shreds_armor_and_armor_max(self):
        """破甲：KINETIC/EXPLOSIVE 削蚀装甲上限，跨战斗持久化。"""
        target = {"shield": 0.0, "armor": 200.0, "hull": 1000.0, "armor_max": 200.0}
        resolve_attack(100.0, "KINETIC", target)
        assert target["armor"] < 200.0
        assert target["armor_max"] < 200.0
        assert target["armor_max"] == pytest.approx(target["armor"], abs=1e-6)

    def test_hull_zero_triggers_ejection(self):
        """结构归零 100% 弹射免死（绝无死猫）。"""
        target = {"shield": 0.0, "armor": 0.0, "hull": 10.0, "armor_max": 0.0}
        _, _, ejected = resolve_attack(999.0, "KINETIC", target)
        assert ejected is True
        assert target["hull"] == 0.0

    def test_morale_multiplier_thresholds(self):
        assert morale_multiplier(0.95) == B.MORALE_FULL_MULTIPLIER
        assert morale_multiplier(0.10) == B.MORALE_LOW_MULTIPLIER
        assert morale_multiplier(0.50) == 1.0

    def test_combat_power_is_ordering_only(self):
        light = combat_power({"shield": 50, "armor": 80, "hull": 120, "dps": 12})
        heavy = combat_power({"shield": 80, "armor": 400, "hull": 500, "dps": 40})
        assert heavy > light

    def test_skirmish_light_car_beats_scout_roomba(self):
        attacker = unit_from_spec(B.VEHICLE_TYPES["light_car"], prefix="car")
        defender = unit_from_spec(B.ENEMY_UNITS["scout_roomba"], prefix="scout")
        result = resolve_skirmish([attacker], [defender])
        assert result["winner"] == "ATTACK"
        assert result["rounds"] > 0
        assert result["log"]

    def test_skirmish_against_guard_mech_loses_and_ejects(self):
        attacker = unit_from_spec(B.VEHICLE_TYPES["light_car"], prefix="car")
        defender = unit_from_spec(B.ENEMY_UNITS["heavy_guard_mech"], prefix="guard")
        result = resolve_skirmish([attacker], [defender])
        assert result["winner"] == "DEFENSE"
        assert result["ejected"]  # 弹射记录（乘员免死）


async def _bootstrap(client) -> dict:
    return (await client.get("/api/v1/colony/state")).json()["data"]


async def _give(session, **resources: float) -> None:
    await session.rollback()
    colony = await session.get(ColonyState, (1, 0))
    for key, value in resources.items():
        setattr(colony, key, value)
    await session.commit()


async def _add_idle_cats(session, count: int) -> None:
    await session.rollback()
    colony = await session.get(ColonyState, (1, 0))
    colony.total_cats += count
    colony.job_idle += count
    await session.commit()


class TestHangarApi:
    async def test_empty_hangar_payload(self, client):
        await _bootstrap(client)
        data = (await client.get(f"{VEHICLE_URL}/list")).json()["data"]
        assert data["hangar_capacity"] == 12
        assert data["hangar_used"] == 0
        assert data["vehicles"] == []
        assert set(data["vehicle_types"]) == set(B.VEHICLE_TYPES)
        assert set(data["expedition_targets"]) == set(B.EXPEDITION_TARGETS)

    async def test_assemble_requires_resources(self, client):
        await _bootstrap(client)
        response = await client.post(
            f"{VEHICLE_URL}/assemble", json={"unit_type": "light_car", "nickname": "雷霆号"}
        )
        assert response.status_code == 400
        assert response.json()["message"] == "INSUFFICIENT_RESOURCE"

    async def test_assemble_locks_crew_and_charges_resources(self, client, session):
        await _bootstrap(client)
        await _give(session, scrap=200.0, chips=20.0, alloys=20.0)
        await _add_idle_cats(session, 2)
        body = (
            await client.post(f"{VEHICLE_URL}/assemble", json={"unit_type": "light_car", "nickname": "雷霆号"})
        ).json()["data"]
        assert body["cost_paid"] == {"scrap": 40.0, "chips": 5.0}
        assert body["crew_locked"] == 2
        assert body["vehicle"]["nickname"] == "雷霆号"
        assert body["vehicle"]["status"] == "IDLE"

        await session.rollback()
        colony = await session.get(ColonyState, (1, 0))
        assert colony.job_idle == 0  # 2 只乘员猫被锁进车里
        assert colony.scrap == 160.0

    async def test_assemble_without_idle_cat_is_rejected(self, client, session):
        await _bootstrap(client)
        await _give(session, scrap=200.0, chips=20.0)
        response = await client.post(f"{VEHICLE_URL}/assemble", json={"unit_type": "light_car"})
        assert response.status_code == 400
        assert response.json()["message"] == "NO_IDLE_CAT"

    async def test_hangar_capacity_is_enforced(self, client, session):
        await _bootstrap(client)
        # 12 辆轻装猫车需要 480 废铁 + 60 芯片 ⇒ 先把仓储上限抬高
        await session.rollback()
        colony = await session.get(ColonyState, (1, 0))
        colony.scrap_max, colony.scrap = 2000.0, 2000.0
        colony.chips_max, colony.chips = 500.0, 500.0
        await session.commit()
        await _add_idle_cats(session, 30)
        # 母星机库 12 机位：轻装猫车 1 机位/辆 ⇒ 造 12 辆后第 13 辆必须被拒
        for _ in range(12):
            assert (
                await client.post(f"{VEHICLE_URL}/assemble", json={"unit_type": "light_car"})
            ).status_code == 200
        rejected = await client.post(f"{VEHICLE_URL}/assemble", json={"unit_type": "light_car"})
        assert rejected.status_code == 400
        assert rejected.json()["message"] == "HANGAR_FULL"

    async def test_scrap_returns_half_materials_and_releases_crew(self, client, session):
        await _bootstrap(client)
        await _give(session, scrap=200.0, chips=20.0)
        await _add_idle_cats(session, 2)
        unit_id = (
            await client.post(f"{VEHICLE_URL}/assemble", json={"unit_type": "light_car"})
        ).json()["data"]["vehicle"]["unit_id"]

        data = (
            await client.post(f"{VEHICLE_URL}/modify", json={"unit_id": unit_id, "action": "SCRAP"})
        ).json()["data"]
        assert data["refund"] == {"scrap": 20.0, "chips": 2.5}
        assert data["crew_released"] == 2
        await session.rollback()
        colony = await session.get(ColonyState, (1, 0))
        assert colony.job_idle >= 2


class TestExpedition:
    async def _make_vehicle(self, client, session) -> int:
        await _give(session, scrap=200.0, chips=20.0, alloys=20.0)
        await _add_idle_cats(session, 4)
        return (
            await client.post(f"{VEHICLE_URL}/assemble", json={"unit_type": "light_car"})
        ).json()["data"]["vehicle"]["unit_id"]

    async def test_dispatch_and_collect_after_deadline(self, client, session):
        await _bootstrap(client)
        unit_id = await self._make_vehicle(client, session)
        body = (
            await client.post(
                f"{MILITARY_URL}/dispatch", json={"target_id": "WALMART", "unit_ids": [unit_id]}
            )
        ).json()["data"]
        assert body["ends_at"] > int(time.time())

        early = await client.post(
            f"{MILITARY_URL}/collect", json={"expedition_id": body["expedition_id"]}
        )
        assert early.status_code == 409
        assert early.json()["message"] == "EXPEDITION_IN_PROGRESS"

        # 把到期时间回拨，模拟"关闭游戏 1 小时后远征已完成"
        await session.rollback()
        from app.models import MilitaryState

        military = await session.get(MilitaryState, (1, 0), populate_existing=True)
        # JSON 列必须整条替换（in-place 改 dict 不会被 SQLAlchemy 侦测到）
        entries = [dict(item) for item in military.active_expeditions]
        entries[0]["ends_at"] = int(time.time()) - 1
        military.active_expeditions = entries
        await session.flush()
        await session.commit()

        # 抬高仓储上限，避免"爆仓裁剪"干扰掉落断言
        colony = await session.get(ColonyState, (1, 0), populate_existing=True)
        colony.scrap_max, colony.catnip_max = 1000.0, 1000.0
        await session.commit()

        response = await client.post(
            f"{MILITARY_URL}/collect", json={"expedition_id": body["expedition_id"]}
        )
        assert response.status_code == 200, response.text
        collected = response.json()["data"]
        assert collected["gained"]["scrap"] == pytest.approx(60.0)
        assert collected["gained"]["catnip"] == pytest.approx(40.0)
        assert collected["suspicion_cost"] == B.EXPEDITION_TARGETS["WALMART"]["suspicion_cost"]

        again = await client.post(f"{MILITARY_URL}/collect", json={"expedition_id": body["expedition_id"]})
        assert again.status_code == 409  # 不能重复收取

    async def test_target_requires_specific_vehicle(self, client, session):
        await _bootstrap(client)
        unit_id = await self._make_vehicle(client, session)
        response = await client.post(
            f"{MILITARY_URL}/dispatch", json={"target_id": "MINE", "unit_ids": [unit_id]}
        )
        assert response.status_code == 400
        assert "破拆机甲" in response.json()["detail"]

    async def test_busy_vehicle_cannot_be_dispatched_twice(self, client, session):
        await _bootstrap(client)
        unit_id = await self._make_vehicle(client, session)
        await client.post(f"{MILITARY_URL}/dispatch", json={"target_id": "WALMART", "unit_ids": [unit_id]})
        second = await client.post(
            f"{MILITARY_URL}/dispatch", json={"target_id": "WALMART", "unit_ids": [unit_id]}
        )
        assert second.status_code == 409
        assert second.json()["message"] == "VEHICLE_BUSY"


class TestRepairAndIntercept:
    async def test_repair_costs_half_and_returns_on_deadline(self, client, session):
        await _bootstrap(client)
        await _give(session, scrap=200.0, chips=20.0)
        await _add_idle_cats(session, 2)
        unit_id = (
            await client.post(f"{VEHICLE_URL}/assemble", json={"unit_type": "light_car"})
        ).json()["data"]["vehicle"]["unit_id"]

        await session.rollback()
        unit = await session.get(VehicleUnit, unit_id)
        unit.status = VehicleStatus.REPAIR
        unit.hull = 0.0
        await session.commit()

        data = (await client.post(f"{VEHICLE_URL}/repair", json={"unit_id": unit_id})).json()["data"]
        assert data["cost_paid"] == {"scrap": 20.0, "chips": 2.5}
        assert data["seconds"] == 60.0

        await session.rollback()
        unit = (
            await session.execute(
                select(VehicleUnit).where(VehicleUnit.unit_id == unit_id).execution_options(populate_existing=True)
            )
        ).scalars().one()
        unit.repair_ends_at = int(time.time()) - 5
        await session.commit()
        settle = await client.get("/api/v1/colony/state")  # 结算推进军备时间轴
        assert settle.status_code == 200, settle.text
        await session.rollback()
        unit = (
            await session.execute(
                select(VehicleUnit)
                .where(VehicleUnit.unit_id == unit_id)
                .execution_options(populate_existing=True)
            )
        ).scalars().one()
        assert unit.status == VehicleStatus.IDLE
        assert unit.hull == B.VEHICLE_TYPES["light_car"]["hull"]

    async def test_intercept_alert_wins_and_clears_suspicion(self, client, session):
        """§8.3 优先级 2：有闲置载具时真打一场，赢了清警报 + 缴获 + rage +25。"""
        await _bootstrap(client)
        await _give(session, scrap=200.0, chips=20.0)
        await _add_idle_cats(session, 2)
        await client.post(f"{VEHICLE_URL}/assemble", json={"unit_type": "light_car"})

        await session.rollback()
        colony = await session.get(ColonyState, (1, 0))
        colony.suspicion = 99.9
        colony.last_tick_time = int(time.time()) - 120
        await session.commit()
        await session.rollback()
        from app.models import MilitaryState

        military = await session.get(MilitaryState, (1, 0))
        military.decoy_count = 0  # 没有诱饵 ⇒ 走战车截杀
        await session.commit()

        first = (await client.get("/api/v1/colony/state")).json()["data"]
        last = (await client.get("/api/v1/colony/state")).json()["data"]
        if "intercept" not in (await client.get("/api/v1/colony/state")).json()["data"]["offline_report"]:
            # 第一段离线还没越线时，再补一段
            await session.rollback()
            colony = await session.get(ColonyState, (1, 0))
            colony.suspicion = 99.9
            colony.last_tick_time = int(time.time()) - 120
            await session.commit()
            last = (await client.get("/api/v1/colony/state")).json()["data"]
        assert first["security"]["decoy_count"] == 0
        assert last["suspicion"]["current"] <= B.SUSPICION_MAX
