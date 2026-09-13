"""§3.5 熔炼玩法验收：电炉把废铁按 10:1 转成合金。"""

from __future__ import annotations

from sqlalchemy import select

from app.core import balance as B
from app.models import ColonyState, FacilityState, LaborBucket, MinigameState, TechRecord
from app.models.tech import TechStatus

STATE_URL = "/api/v1/colony/state"


async def _boot(client) -> None:
    await client.get(STATE_URL, params={"slot": 1})


async def _setup(session, *, furnaces: int, scrap: float, seconds: int, alloys: float = 0.0) -> None:
    await session.rollback()
    row = (
        await session.execute(
            select(FacilityState).where(
                FacilityState.slot_id == 1,
                FacilityState.planet_id == 0,
                FacilityState.facility_id == "induction_furnace",
            )
        )
    ).scalars().one()
    row.level = furnaces
    colony = await session.get(ColonyState, (1, 0))
    colony.scrap = scrap
    colony.alloys = alloys
    colony.total_cats = 0
    colony.job_idle = 0
    colony.last_tick_time = int(colony.last_tick_time) - seconds
    # 电网给足电，专测熔炼本身
    solar = (
        await session.execute(
            select(FacilityState).where(
                FacilityState.slot_id == 1,
                FacilityState.planet_id == 0,
                FacilityState.facility_id == "solar_panel",
            )
        )
    ).scalars().one()
    solar.level = 6
    await session.commit()


async def _run(client) -> dict:
    return (await client.get(STATE_URL, params={"slot": 1})).json()["data"]


async def test_smelting_converts_scrap_to_alloys(client, session) -> None:
    await _boot(client)
    await _setup(session, furnaces=1, scrap=100.0, seconds=100)
    data = await _run(client)
    report = data["offline_report"]
    # 1 座炉 × 100 秒 = 10 炉次 ⇒ 消耗 100 废铁、产出 10 合金
    assert report["smelted_batches"] == 10
    assert report["gained_alloys"] == 10
    assert data["resources"]["scrap"] == 0
    assert data["resources"]["alloys"] == 10


async def test_smelting_limited_by_scrap_and_cap(client, session) -> None:
    await _boot(client)
    # 废铁只够 2 炉次，即便时间只允许更多
    await _setup(session, furnaces=3, scrap=20.0, seconds=600)
    report = (await _run(client))["offline_report"]
    assert report["smelted_batches"] == 2

    # 合金仓满 ⇒ 不再熔炼（不溢出、不欠账）
    await _setup(session, furnaces=1, scrap=100.0, seconds=100, alloys=B.RESOURCE_CAPS["alloys"])
    filled = await _run(client)
    assert filled["offline_report"].get("smelted_batches", 0) == 0
    assert filled["resources"]["alloys"] == B.RESOURCE_CAPS["alloys"]


async def test_blackout_stops_all_furnaces(client, session) -> None:
    await _boot(client)
    await _setup(session, furnaces=1, scrap=100.0, seconds=100)
    await session.rollback()
    # 拆掉太阳能板 ⇒ 净电力为负 ⇒ 全部停炉
    solar = (
        await session.execute(
            select(FacilityState).where(
                FacilityState.slot_id == 1,
                FacilityState.planet_id == 0,
                FacilityState.facility_id == "solar_panel",
            )
        )
    ).scalars().one()
    solar.level = 0
    await session.commit()
    report = (await _run(client))["offline_report"]
    assert report.get("smelted_batches", 0) == 0
    assert any("停炉" in note for note in report["notes"])


async def test_tech_and_minigame_bonus_speed_up_smelting(client, session) -> None:
    await _boot(client)
    await _setup(session, furnaces=1, scrap=1000.0, seconds=100)
    base = (await _run(client))["offline_report"]["smelted_batches"]
    assert base == 10

    await session.rollback()
    # 科技 smelt_speed +25%（白名单键，直接写在节点载荷上）
    row = (
        await session.execute(
            select(TechRecord).where(
                TechRecord.slot_id == 1,
                TechRecord.planet_id == 0,
                TechRecord.tech_id == "tech_cardboard_mechanics",
            )
        )
    ).scalars().one()
    row.buff_payload = {"smelt_speed": 0.25}
    row.status = TechStatus.UNLOCKED
    # 小游戏配方加成 +50%（上限）
    forge = (
        await session.execute(
            select(MinigameState).where(MinigameState.slot_id == 1, MinigameState.minigame_id == "forge_recipe")
        )
    ).scalars().first()
    if forge is None:
        forge = MinigameState(
            slot_id=1, planet_id=1, minigame_id="forge_recipe", state={}, last_tick_time=0, best_score=0.0, play_count=0
        )
        session.add(forge)
    forge.state = {"smelt_speed_bonus": 0.5}
    await session.commit()

    await _setup(session, furnaces=1, scrap=1000.0, seconds=100)
    boosted = (await _run(client))["offline_report"]["smelted_batches"]
    # 10 × (1 + 0.25 + 0.5) = 17.5；秒级时间截断留 3% 容差
    assert abs(boosted - 17.5) <= 17.5 * 0.03
