"""§15.3 第①步验收：外星球基地建行 + 读档放行。"""

from __future__ import annotations

import time

from sqlalchemy import func, select

from app.models import ColonyState, FacilityState, LaborBucket, PlanetState, SaveSlot

LOAD_URL = "/api/v1/game/load"
STATE_URL = "/api/v1/colony/state"
SWITCH_URL = "/api/v1/planet/switch"


async def _boot(client) -> None:
    await client.get(STATE_URL, params={"slot": 1})


async def _unlock(session, planet_id: int) -> None:
    await session.rollback()
    planet = await session.get(PlanetState, (1, planet_id))
    planet.unlocked = True
    await session.commit()


async def test_switch_creates_star_colony_with_unlock_anchor(client, session) -> None:
    await _boot(client)
    await _unlock(session, 1)
    before = int(time.time())
    await client.post(SWITCH_URL, json={"slot": 1, "planet_id": 1})

    await session.rollback()
    colony = await session.get(ColonyState, (1, 1), populate_existing=True)
    assert colony is not None
    assert before <= int(colony.last_tick_time) <= int(time.time())  # 锚点 = 解锁那一刻
    assert colony.catnip == 0 and colony.scrap == 0 and colony.total_cats == 0
    assert colony.catnip_max > 0  # 仓储上限沿用母星口径

    await session.rollback()
    labor = await session.execute(
        select(func.count()).select_from(LaborBucket).where(LaborBucket.slot_id == 1, LaborBucket.planet_id == 1)
    )
    facilities = await session.execute(
        select(func.count()).select_from(FacilityState).where(FacilityState.slot_id == 1, FacilityState.planet_id == 1)
    )
    assert labor.scalar_one() > 0
    assert facilities.scalar_one() > 0


async def test_game_load_now_opens_unlocked_planet(client, session) -> None:
    await _boot(client)
    await _unlock(session, 1)

    resp = await client.get(LOAD_URL, params={"slot": 1, "planet_id": 1})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["planet_id"] == 1
    assert data["resources"]["scrap"] == 0
    # 首次读档不会把"解锁之前的时间"补算成收益
    assert data["offline_report"]["elapsed_seconds"] < 60

    await session.rollback()
    save = await session.get(SaveSlot, 1, populate_existing=True)
    assert save.active_planet_id == 1


async def test_star_colony_state_is_served(client, session) -> None:
    await _boot(client)
    await _unlock(session, 1)
    await client.post(SWITCH_URL, json={"slot": 1, "planet_id": 1})
    resp = await client.get(STATE_URL, params={"slot": 1, "planet_id": 1})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["planet_id"] == 1
    assert data["workstations"]  # 工种分桶按母星同构建行
    assert all(level == 0 for level in data["facilities"].values())


async def test_building_rows_is_idempotent_and_keeps_progress(client, session) -> None:
    await _boot(client)
    await _unlock(session, 1)
    await client.post(SWITCH_URL, json={"slot": 1, "planet_id": 1})

    await session.rollback()
    colony = await session.get(ColonyState, (1, 1))
    colony.scrap = 42.0
    colony.total_cats = 3
    anchor = int(colony.last_tick_time)
    await session.commit()

    # 再切一次 / 再读一次档：既不重置进度，也不重设锚点、不重复建行
    await client.post(SWITCH_URL, json={"slot": 1, "planet_id": 1})
    await client.get(LOAD_URL, params={"slot": 1, "planet_id": 1})

    await session.rollback()
    fresh = await session.get(ColonyState, (1, 1), populate_existing=True)
    assert fresh.scrap == 42.0
    assert fresh.total_cats == 3
    assert int(fresh.last_tick_time) >= anchor
    rows = await session.execute(
        select(func.count()).select_from(ColonyState).where(ColonyState.slot_id == 1)
    )
    assert rows.scalar_one() == 2  # 只有母星 + 这一颗


async def test_locked_planet_still_refused(client, session) -> None:
    await _boot(client)
    resp = await client.get(LOAD_URL, params={"slot": 1, "planet_id": 2})
    assert resp.status_code == 400
    assert resp.json()["message"] == "PLANET_LOCKED"
    await session.rollback()
    assert await session.get(ColonyState, (1, 2)) is None  # 未解锁就不建行
