"""§15.3 第③步第二片验收：三颗星的产粮系数。"""

from __future__ import annotations

from app.core import balance as B
from app.models import ColonyState, LaborBucket, PlanetState

STATE_URL = "/api/v1/colony/state"
LOAD_URL = "/api/v1/game/load"


def test_multiplier_table() -> None:
    assert B.planet_catnip_multiplier(0) == 1.0  # 母星口径不变
    assert B.planet_catnip_multiplier(1) == 0.7
    assert B.planet_catnip_multiplier(2) == 1.0
    assert B.planet_catnip_multiplier(3) == 0.9


async def _harvest(client, session, planet_id: int, seconds: int = 300) -> float:
    await session.rollback()
    colony = await session.get(ColonyState, (1, planet_id))
    colony.catnip = 0.0
    colony.total_cats = 0  # 排除消耗，只看产出
    colony.job_idle = 0
    colony.last_tick_time = int(colony.last_tick_time) - seconds
    await session.commit()
    data = (await client.get(STATE_URL, params={"slot": 1, "planet_id": planet_id})).json()["data"]
    return float(data["offline_report"]["gained_catnip"])


async def _farmers(session, planet_id: int, count: int) -> None:
    await session.rollback()
    bucket = await session.get(LaborBucket, (1, planet_id, "farmer"))
    bucket.cat_count = count
    await session.commit()


async def test_star_farm_yields_less_than_home(client, session) -> None:
    await client.get(STATE_URL, params={"slot": 1})
    await session.rollback()
    planet = await session.get(PlanetState, (1, 1))
    planet.unlocked = True
    await session.commit()
    await client.get(LOAD_URL, params={"slot": 1, "planet_id": 1})  # 建行

    await _farmers(session, 0, 2)
    await _farmers(session, 1, 2)
    home = await _harvest(client, session, 0)
    star = await _harvest(client, session, 1)
    assert home > 0
    # 秒级时间戳会被截断，两次结算的 Δt 可能差 1 秒 ⇒ 用 2% 容差而不是精确相等
    expected = home * B.STAR_PLANET_CATNIP_MULTIPLIER[1]
    assert abs(star - expected) <= expected * 0.02
