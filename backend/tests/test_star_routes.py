"""§15.3 第②步验收：跨星航线迁猫（在途 / 交付 / 带宽 / 被劫掠延误）。"""

from __future__ import annotations

import time

from sqlalchemy import select

from app.core import balance as B
from app.models import ColonyState, PlanetState
from app.services import planet_service

MIGRATE_URL = "/api/v1/planet/migrate"
PLANET_URL = "/api/v1/planet"
STATE_URL = "/api/v1/colony/state"


async def _boot(client, session, cats: int = 6, *, planet_id: int = 1) -> None:
    await client.get(STATE_URL, params={"slot": 1})
    await session.rollback()
    planet = await session.get(PlanetState, (1, planet_id))
    planet.unlocked = True
    colony = await session.get(ColonyState, (1, 0))
    colony.total_cats = cats
    colony.job_idle = cats
    await session.commit()


async def _routes(session, planet_id: int = 1) -> list[dict]:
    await session.rollback()
    planet = await session.get(PlanetState, (1, planet_id), populate_existing=True)
    return [dict(item) for item in (planet.logistics_routes or [])]


async def test_migrate_departs_immediately_and_creates_in_flight_route(client, session) -> None:
    await _boot(client, session, cats=6)
    resp = await client.post(
        MIGRATE_URL, json={"slot": 1, "to_planet": 1, "count": 3}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["source_total_cats"] == 3  # 出发即离港
    assert data["source_idle_cats"] == 3
    assert data["eta_seconds"] == int(B.STAR_ROUTE_SECONDS)
    assert data["slots_used"] == 1 and data["slots_total"] == B.STAR_ROUTE_BASE_SLOTS

    routes = await _routes(session)
    assert len(routes) == 1
    assert routes[0]["cat_count"] == 3
    assert routes[0]["arrives_at"] - routes[0]["departed_at"] == int(B.STAR_ROUTE_SECONDS)
    # 在途期间目标星球还没猫
    await session.rollback()
    target = await session.get(ColonyState, (1, 1), populate_existing=True)
    assert target.total_cats == 0


async def test_route_delivers_on_arrival_and_clears(client, session, monkeypatch) -> None:
    await _boot(client, session, cats=4)
    # route_id 含出发时间戳 ⇒ 是否被劫掠取决于哈希；这条用例只测"交付"，先关掉劫掠
    monkeypatch.setattr(B, "STAR_ROUTE_RAID_CHANCE", 0.0)
    await client.post(MIGRATE_URL, json={"slot": 1, "to_planet": 1, "count": 2})

    # 把抵达时间挪到过去（等价于等满 60 秒）
    await session.rollback()
    planet = await session.get(PlanetState, (1, 1))
    routes = [dict(item) for item in (planet.logistics_routes or [])]
    routes[0]["arrives_at"] = int(time.time()) - 1
    planet.logistics_routes = routes
    await session.commit()

    events = await planet_service.settle_routes(session, 1)
    await session.commit()
    assert [event["type"] for event in events] == ["ROUTE_ARRIVED"]
    assert events[0]["cat_count"] == 2

    await session.rollback()
    target = await session.get(ColonyState, (1, 1), populate_existing=True)
    assert target.total_cats == 2 and target.job_idle == 2
    assert await _routes(session) == []  # 航线结清


async def test_bandwidth_limits_concurrent_routes(client, session) -> None:
    await _boot(client, session, cats=20)
    for _ in range(B.STAR_ROUTE_BASE_SLOTS):
        resp = await client.post(MIGRATE_URL, json={"slot": 1, "to_planet": 1, "count": 1})
        assert resp.status_code == 200, resp.text
    third = await client.post(MIGRATE_URL, json={"slot": 1, "to_planet": 1, "count": 1})
    assert third.status_code == 400
    assert third.json()["message"] == "STAR_ROUTE_SLOTS_FULL"

    # 物流调度官每只 +1 条带宽（封顶 4）
    await session.rollback()
    from app.models import LaborBucket

    bucket = await session.get(LaborBucket, (1, 0, "logistics"))
    bucket.cat_count = 1
    await session.commit()
    assert await planet_service.route_slots(session, 1, 1) == B.STAR_ROUTE_BASE_SLOTS + 1
    again = await client.post(MIGRATE_URL, json={"slot": 1, "to_planet": 1, "count": 1})
    assert again.status_code == 200, resp.text


async def test_migrate_needs_idle_cats(client, session) -> None:
    await _boot(client, session, cats=2)
    resp = await client.post(MIGRATE_URL, json={"slot": 1, "to_planet": 1, "count": 5})
    assert resp.status_code == 400
    assert resp.json()["message"] == "INSUFFICIENT_RESOURCE"


async def test_migrate_refuses_locked_planet(client, session) -> None:
    await client.get(STATE_URL, params={"slot": 1})
    resp = await client.post(MIGRATE_URL, json={"slot": 1, "to_planet": 3, "count": 1})
    assert resp.status_code == 400
    assert resp.json()["message"] == "PLANET_LOCKED"


async def test_route_can_carry_materials_so_star_colony_can_start(client, session, monkeypatch) -> None:
    """外星球从 0 起步且没有"手点废墟"⇒ 航线必须能运物资，否则永远造不出第一座纸箱窝。"""
    await _boot(client, session, cats=2)
    monkeypatch.setattr(B, "STAR_ROUTE_RAID_CHANCE", 0.0)
    await session.rollback()
    colony = await session.get(ColonyState, (1, 0))
    colony.scrap = 40.0
    await session.commit()

    sent = await client.post(
        MIGRATE_URL, json={"slot": 1, "to_planet": 1, "count": 1, "cargo": {"scrap": 30}}
    )
    assert sent.status_code == 200, sent.text
    assert sent.json()["data"]["cargo"] == {"scrap": 30.0}

    await session.rollback()
    source = await session.get(ColonyState, (1, 0), populate_existing=True)
    assert source.scrap == 10.0  # 出发即离港（货也算）

    # 到点交付：猫口与物资一起落地
    await session.rollback()
    planet = await session.get(PlanetState, (1, 1))
    routes = [dict(item) for item in (planet.logistics_routes or [])]
    routes[0]["arrives_at"] = int(time.time()) - 1
    planet.logistics_routes = routes
    await session.commit()
    await planet_service.settle_routes(session, 1)
    await session.commit()

    await session.rollback()
    target = await session.get(ColonyState, (1, 1), populate_existing=True)
    assert target.total_cats == 1
    assert target.scrap == 30.0  # 够造两座纸箱窝（5 废铁/座）


async def test_cargo_limits_and_insufficient_materials(client, session) -> None:
    await _boot(client, session, cats=4)
    await session.rollback()
    colony = await session.get(ColonyState, (1, 0))
    colony.scrap = 200.0
    await session.commit()

    too_much = await client.post(
        MIGRATE_URL, json={"slot": 1, "to_planet": 1, "count": 1, "cargo": {"scrap": 999}}
    )
    assert too_much.status_code == 400
    assert too_much.json()["message"] == "BAD_REQUEST"

    unsupported = await client.post(
        MIGRATE_URL, json={"slot": 1, "to_planet": 1, "count": 1, "cargo": {"alloys": 5}}
    )
    assert unsupported.status_code == 400

    await session.rollback()
    colony = await session.get(ColonyState, (1, 0))
    colony.scrap = 1.0
    await session.commit()
    poor = await client.post(
        MIGRATE_URL, json={"slot": 1, "to_planet": 1, "count": 1, "cargo": {"scrap": 10}}
    )
    assert poor.status_code == 400
    assert poor.json()["message"] == "INSUFFICIENT_RESOURCE"


async def test_raided_route_is_delayed_not_lost(client, session, monkeypatch) -> None:
    """被劫掠 = 延误 10 分钟，绝不死猫（对齐"绝无死猫"原则）。"""
    await _boot(client, session, cats=4)
    await client.post(MIGRATE_URL, json={"slot": 1, "to_planet": 1, "count": 2})
    monkeypatch.setattr(B, "STAR_ROUTE_RAID_CHANCE", 1.0)  # 必定被劫掠

    await session.rollback()
    planet = await session.get(PlanetState, (1, 1))
    routes = [dict(item) for item in (planet.logistics_routes or [])]
    routes[0]["arrives_at"] = int(time.time()) - 1
    planet.logistics_routes = routes
    await session.commit()

    events = await planet_service.settle_routes(session, 1)
    await session.commit()
    assert [event["type"] for event in events] == ["ROUTE_RAIDED"]
    assert events[0]["delay_seconds"] == int(B.STAR_ROUTE_RAID_DELAY_SECONDS)

    await session.rollback()
    target = await session.get(ColonyState, (1, 1), populate_existing=True)
    assert target.total_cats == 0  # 还没到
    routes = await _routes(session)
    assert routes[0]["raided"] is True

    # 延误期满后照样交付（猫一只不少）
    await session.rollback()
    planet = await session.get(PlanetState, (1, 1))
    routes = [dict(item) for item in (planet.logistics_routes or [])]
    routes[0]["arrives_at"] = int(time.time()) - 1
    planet.logistics_routes = routes
    await session.commit()
    monkeypatch.setattr(B, "STAR_ROUTE_RAID_CHANCE", 0.0)
    await planet_service.settle_routes(session, 1)
    await session.commit()
    await session.rollback()
    target = await session.get(ColonyState, (1, 1), populate_existing=True)
    assert target.total_cats == 2
