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
