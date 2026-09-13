"""§15.3 第③步收官片验收：星球专属产出加成。"""

from __future__ import annotations

from app.core import balance as B
from app.models import ColonyState, LaborBucket, PlanetState

STATE_URL = "/api/v1/colony/state"
LOAD_URL = "/api/v1/game/load"


def test_output_bonus_table() -> None:
    assert B.planet_output_multiplier(0, "scrap") == 1.0  # 母星不偏科
    assert B.planet_output_multiplier(3, "scrap") == 1.25  # 星带善拾荒
    assert B.planet_output_multiplier(2, "chips") == 1.3   # 冰卫星善拆解
    assert B.planet_output_multiplier(1, "catnip") == 1.0  # 未登记资源不生效


async def _run(client, session, planet_id: int, seconds: int = 300) -> dict:
    await session.rollback()
    colony = await session.get(ColonyState, (1, planet_id))
    colony.scrap = 0.0
    colony.chips = 0.0
    colony.total_cats = 0
    colony.job_idle = 0
    colony.last_tick_time = int(colony.last_tick_time) - seconds
    await session.commit()
    return (await client.get(STATE_URL, params={"slot": 1, "planet_id": planet_id})).json()["data"]["offline_report"]


async def test_star_band_mines_more_scrap_than_home(client, session) -> None:
    await client.get(STATE_URL, params={"slot": 1})
    await session.rollback()
    for planet_id in (1, 3):
        planet = await session.get(PlanetState, (1, planet_id))
        planet.unlocked = True
    await session.commit()
    for planet_id in (1, 3):
        await client.get(LOAD_URL, params={"slot": 1, "planet_id": planet_id})  # 建行
    for planet_id in (0, 3):
        await session.rollback()
        bucket = await session.get(LaborBucket, (1, planet_id, "scavenger"))
        bucket.cat_count = 2
        await session.commit()

    # 60 秒：产出约 60，稳在 200 的仓储上限之内（否则被爆仓夹住看不出倍率）
    home = await _run(client, session, 0, seconds=60)
    band = await _run(client, session, 3, seconds=60)
    assert home["gained_scrap"] > 0
    expected = home["gained_scrap"] * B.STAR_PLANET_OUTPUT_BONUS[3]["scrap"]
    assert abs(band["gained_scrap"] - expected) <= expected * 0.03  # 秒级截断留容差


async def test_ice_moon_salvages_more_chips(client, session) -> None:
    await client.get(STATE_URL, params={"slot": 1})
    await session.rollback()
    moon = await session.get(PlanetState, (1, 2))
    moon.unlocked = True
    await session.commit()
    await client.get(LOAD_URL, params={"slot": 1, "planet_id": 2})  # 建行
    await session.rollback()
    bucket = await session.get(LaborBucket, (1, 2, "scavenger"))
    bucket.cat_count = 4
    await session.commit()

    await _run(client, session, 2, seconds=300)
    chips = (await client.get(STATE_URL, params={"slot": 1, "planet_id": 2})).json()["data"]["resources"]["chips"]
    assert chips > 0
    # 冰卫星芯片 +30%：4 只拾荒猫 300 秒的基础值 × 1.3（留 3% 容差避开秒级截断）
    expected = 4 * B.SCAVENGER_CHIPS_PER_SEC * 300 * B.STAR_PLANET_OUTPUT_BONUS[2]["chips"]
    assert chips >= expected * 0.97
