"""读档接口验收：`GET /game/load`（不自动建档 + 读档即激活）。"""

from __future__ import annotations

from sqlalchemy import select

from app.models import ColonyState, PlanetState, SaveSlot

LOAD_URL = "/api/v1/game/load"
STATE_URL = "/api/v1/colony/state"


async def test_empty_slot_refuses_instead_of_autocreating(client, session) -> None:
    await client.get(STATE_URL, params={"slot": 1})
    resp = await client.get(LOAD_URL, params={"slot": 3})
    assert resp.status_code == 404
    assert resp.json()["message"] == "SAVE_NOT_FOUND"
    await session.rollback()
    assert await session.get(SaveSlot, 3) is None  # 读档不会偷偷建新档


async def test_load_existing_save_returns_state_and_offline_report(client) -> None:
    created = (await client.get(STATE_URL, params={"slot": 1})).json()["data"]
    loaded = (await client.get(LOAD_URL, params={"slot": 1})).json()["data"]
    assert loaded["slot_id"] == 1
    assert loaded["planet_id"] == created["planet_id"]
    assert "offline_report" in loaded
    assert loaded["resources"].keys() == created["resources"].keys()
    assert loaded["saved_at"] >= created["saved_at"]


async def test_load_invalid_slot_is_bad_request(client) -> None:
    resp = await client.get(LOAD_URL, params={"slot": 9})
    assert resp.status_code == 400
    assert resp.json()["message"] == "BAD_REQUEST"


async def test_load_locked_planet_is_refused(client) -> None:
    await client.get(STATE_URL, params={"slot": 1})
    resp = await client.get(LOAD_URL, params={"slot": 1, "planet_id": 2})
    assert resp.status_code == 400
    assert resp.json()["message"] == "PLANET_LOCKED"


async def test_load_foreign_planet_builds_base_and_opens(client, session) -> None:
    """§15.3 第①步落地后：读到已解锁的外星球会先按母星同构建行，再正常读档。"""
    await client.get(STATE_URL, params={"slot": 1})
    await session.rollback()
    planet = await session.get(PlanetState, (1, 1))
    planet.unlocked = True
    await session.commit()

    resp = await client.get(LOAD_URL, params={"slot": 1, "planet_id": 1})
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["planet_id"] == 1

    await session.rollback()
    colony = await session.get(ColonyState, (1, 1), populate_existing=True)
    assert colony is not None and colony.total_cats == 0 and colony.scrap == 0
    assert int(colony.last_tick_time) > 0  # 锚点写的是"解锁那一刻"，不是 0
    # 该星球的特化科技树也随登录铺好（LLM 场景 2）
    tree = (await client.get("/api/v1/tech/tree", params={"slot": 1, "planet_id": 1})).json()["data"]
    assert tree["total_nodes"] == 12


async def test_load_is_idempotent_for_already_loaded_planet(client, session) -> None:
    await client.get(STATE_URL, params={"slot": 1})
    first = (await client.get(LOAD_URL, params={"slot": 1})).json()["data"]
    second = (await client.get(LOAD_URL, params={"slot": 1})).json()["data"]
    # 重复读档幂等：活跃星球与资源口径不变，离线报表照常给出
    assert first["planet_id"] == second["planet_id"] == 0
    assert first["resources"] == second["resources"]
    assert first["slot_id"] == second["slot_id"] == 1


async def test_active_planet_without_colony_self_heals_to_home(client, session) -> None:
    """切到未开发的外星球后，活跃星球会指向没有基地的星球 —— 读档必须自愈而不是 404 死锁。"""
    await client.get(STATE_URL, params={"slot": 1})
    await session.rollback()
    planet = await session.get(PlanetState, (1, 2))
    planet.unlocked = True
    save = await session.get(SaveSlot, 1)
    save.active_planet_id = 2  # 模拟"曾经切到三号冰卫星"，但那边没有 colony_state 行
    await session.commit()

    # /colony/state 是只读入口、不建行 ⇒ 仍走自愈（兼容"早年切过外星球"的旧存档）
    data = (await client.get(STATE_URL, params={"slot": 1})).json()["data"]
    assert data["planet_id"] == 0  # 回落到母星
    await session.rollback()
    healed = await session.get(SaveSlot, 1, populate_existing=True)
    assert healed.active_planet_id == 0  # 存档锚点被治好，下次读档不再踩坑

    # 显式点名已解锁的外星球：按 §15.3 第①步建行并放行
    explicit = await client.get(LOAD_URL, params={"slot": 1, "planet_id": 2})
    assert explicit.status_code == 200
    assert explicit.json()["data"]["planet_id"] == 2
