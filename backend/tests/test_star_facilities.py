"""§15.4 外星球专属设施验收：星球限定 + 产出/熔炼加成接入结算。"""

from __future__ import annotations

from sqlalchemy import select

from app.core import balance as B
from app.models import ColonyState, FacilityState, LaborBucket, PlanetState

STATE_URL = "/api/v1/colony/state"
SWITCH_URL = "/api/v1/planet/switch"
BUILD_URL = "/api/v1/facilities/build"


def test_facility_definitions_are_scoped() -> None:
    assert B.facility_planet_scope("geothermal_forge") == 1
    assert B.facility_planet_scope("cryo_salvage_bench") == 2
    assert B.facility_planet_scope("asteroid_sorter") == 3
    assert B.facility_planet_scope("farm_plot") is None  # 普通设施不限定
    assert B.facility_output_bonus("asteroid_sorter", 2) == ("scrap", 0.7)
    assert B.facility_output_bonus("cryo_salvage_bench", 1) == ("chips", 0.3)
    assert B.facility_smelt_speed_bonus("geothermal_forge", 3) == 0.9


async def _boot_and_unlock(client, session, planet_id: int) -> None:
    await client.get(STATE_URL, params={"slot": 1})
    await session.rollback()
    planet = await session.get(PlanetState, (1, planet_id))
    planet.unlocked = True
    await session.commit()
    await client.post(SWITCH_URL, json={"slot": 1, "planet_id": planet_id})  # 建行


async def test_exclusive_facility_refused_on_other_planets(client, session) -> None:
    await _boot_and_unlock(client, session, 2)  # 冰卫星
    resp = await client.post(
        BUILD_URL, json={"slot": 1, "planet_id": 2, "facility_id": "geothermal_forge"}
    )
    assert resp.status_code == 400
    assert resp.json()["message"] == "FACILITY_PLANET_MISMATCH"

    # 在自己星球上则通过作用域校验（下一步是资源校验，说明作用域确实放行了）
    ok_scope = await client.post(
        BUILD_URL, json={"slot": 1, "planet_id": 2, "facility_id": "cryo_salvage_bench"}
    )
    assert ok_scope.status_code == 400
    assert ok_scope.json()["message"] == "INSUFFICIENT_RESOURCE"


async def _set_level(session, planet_id: int, facility_id: str, level: int) -> None:
    await session.rollback()
    row = (
        await session.execute(
            select(FacilityState).where(
                FacilityState.slot_id == 1,
                FacilityState.planet_id == planet_id,
                FacilityState.facility_id == facility_id,
            )
        )
    ).scalars().one()
    row.level = level
    await session.commit()


async def test_scrap_output_bonus_is_additive(client, session) -> None:
    """星带：本星 ×1.25 + 分选线 2 座 ×0.35 = 1.95（**相加不相乘**）。"""
    from app.core.offline_engine import calculate_offline_progress
    from app.services import colony_service

    await _boot_and_unlock(client, session, 3)
    await _set_level(session, 3, "asteroid_sorter", 2)

    assert colony_service._exclusive_output_bonus({"asteroid_sorter": 2}, "scrap") == 0.7
    # 引擎侧：同 Δt、只改产出系数 ⇒ 增量恰好按 1.95/1.25 放大（用引擎直接算，避开时间截断噪声）
    def run(multiplier: float) -> float:
        state = {
            "now": 1000,
            "scrap": 0.0, "scrap_max": 500.0, "catnip": 100.0, "catnip_max": 500.0,
            "chips": 0.0, "chips_max": 500.0, "alloys": 0.0, "alloys_max": 500.0,
            "battery": 0.0, "battery_max": 100.0, "lube": 0.0, "lube_max": 100.0,
            "battery_kwh": 0.0, "battery_kwh_max": 200.0, "total_cats": 0,
            "birth_progress": 0.0, "suspicion": 0.0,
            "scavengers": 2, "farmers": 0, "geeks": 0, "power_runners": 0,
            "max_cat_capacity": 10, "production_multiplier": 1.0,
            "breeding_rate_multiplier": 1.0, "suspicion_growth_multiplier": 1.0,
            "silent_grass_count": 0, "garden_power_kw": 0.0, "garden_suspicion_per_sec": 0.0,
            "planet_scrap_multiplier": multiplier, "planet_chips_multiplier": 1.25,
        }
        return float(calculate_offline_progress(state, 100)["gained_resources"]["scrap"])

    base = run(1.25)
    boosted = run(1.95)
    assert base > 0
    assert abs(boosted / base - 1.56) <= 0.01


async def test_smelt_speed_bonus_from_exclusive_forge(client, session) -> None:
    """熔岩星：地热熔炉 1 座把熔炼速度 +30%（与科技/小游戏相加）。"""
    await _boot_and_unlock(client, session, 1)
    await _set_level(session, 1, "induction_furnace", 1)
    # 外星球没有发电 ⇒ 默认欠载停炉（这正是设计）；给足电才能测熔炼速度本身
    await _set_level(session, 1, "solar_panel", 4)

    async def run(seconds: int) -> float:
        await session.rollback()
        colony = await session.get(ColonyState, (1, 1))
        colony.scrap = 500.0
        colony.alloys = 0.0
        colony.total_cats = 0
        colony.job_idle = 0
        colony.last_tick_time = int(colony.last_tick_time) - seconds
        await session.commit()
        data = (await client.get(STATE_URL, params={"slot": 1, "planet_id": 1})).json()["data"]
        return float(data["offline_report"]["smelted_batches"])

    base = await run(100)
    await _set_level(session, 1, "geothermal_forge", 1)
    boosted = await run(100)
    assert base > 0
    assert abs(boosted / base - 1.30) <= 0.05
