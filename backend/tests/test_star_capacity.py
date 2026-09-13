"""§15.3 第③步首片验收：三颗星的承载力系数。"""

from __future__ import annotations

from sqlalchemy import select

from app.core import balance as B
from app.models import ColonyState, FacilityState, PlanetState

STATE_URL = "/api/v1/colony/state"


def test_capacity_multiplier_table() -> None:
    facilities = {"housing_box": 20, "cat_condo": 3}
    base = B.cat_capacity_on(facilities, 0)
    assert B.cat_capacity(facilities) == base  # 母星口径不变（向后兼容）
    assert B.cat_capacity_on(facilities, 1) == int(base * B.STAR_PLANET_CAPACITY_MULTIPLIER[1])
    assert B.cat_capacity_on(facilities, 3) == int(base * B.STAR_PLANET_CAPACITY_MULTIPLIER[3])
    assert B.cat_capacity_on(facilities, 3) > base > B.cat_capacity_on(facilities, 1)


def test_planet_traits_are_single_source_of_truth() -> None:
    """星球性格只认 `balance.planet_traits()`（界面读它，不再各写一套系数）。"""
    home = B.planet_traits(0)
    assert home["capacity_multiplier"] == 1.0 and home["catnip_multiplier"] == 1.0
    assert home["output_bonus"] == {"scrap": 1.0, "chips": 1.0}

    band = B.planet_traits(3)
    assert band["capacity_multiplier"] == B.STAR_PLANET_CAPACITY_MULTIPLIER[3]
    assert band["catnip_multiplier"] == B.STAR_PLANET_CATNIP_MULTIPLIER[3]
    assert band["output_bonus"]["scrap"] == B.STAR_PLANET_OUTPUT_BONUS[3]["scrap"]
    # 未登记的星球也不能崩（返回全 1.0）
    assert B.planet_traits(9)["catnip_multiplier"] == 1.0


async def test_planet_state_exposes_traits(client) -> None:
    await client.get(STATE_URL, params={"slot": 1})
    planets = (await client.get("/api/v1/planet/state", params={"slot": 1})).json()["data"]["planets"]
    by_id = {item["planet_id"]: item for item in planets}
    assert by_id[0]["traits"]["output_bonus"]["scrap"] == 1.0
    assert by_id[3]["traits"]["output_bonus"]["scrap"] == 1.25
    assert by_id[2]["traits"]["output_bonus"]["chips"] == 1.3


async def test_star_colony_reports_its_own_capacity(client, session) -> None:
    await client.get(STATE_URL, params={"slot": 1})
    await session.rollback()
    planet = await session.get(PlanetState, (1, 1))
    planet.unlocked = True
    await session.commit()
    await client.get("/api/v1/game/load", params={"slot": 1, "planet_id": 1})  # 建行

    # 同等级住房，在母星与熔岩星上给出不同承载力
    await session.rollback()
    for planet_id in (0, 1):
        row = (
            await session.execute(
                select(FacilityState).where(
                    FacilityState.slot_id == 1,
                    FacilityState.planet_id == planet_id,
                    FacilityState.facility_id == "housing_box",
                )
            )
        ).scalars().one()
        row.level = 20
    await session.commit()

    home = (await client.get(STATE_URL, params={"slot": 1, "planet_id": 0})).json()["data"]
    star = (await client.get(STATE_URL, params={"slot": 1, "planet_id": 1})).json()["data"]
    assert home["population"]["max_cap"] == 20
    assert star["population"]["max_cap"] == int(20 * B.STAR_PLANET_CAPACITY_MULTIPLIER[1])
    await session.rollback()
    assert (await session.get(ColonyState, (1, 1), populate_existing=True)) is not None
