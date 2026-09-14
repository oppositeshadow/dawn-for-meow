"""§15.3 第②步验收：跨星航线迁猫（在途 / 交付 / 带宽 / 被劫掠延误）。"""

from __future__ import annotations

import time

import pytest
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


# ----------------------------------------------------------------------
# §15.5 碎星流「被劫掠 ×2」接线 + "幽灵航线"回归（v1.58）
# ----------------------------------------------------------------------
LOAD_URL = "/api/v1/game/load"
STORM_NOW = 100  # 星带周期 offset=100（前 600 秒）⇒ 碎星流「来袭」
CALM_NOW = 700   # offset=700 ⇒ 「平静」


def _star_route(route_id: str, *, arrives_at: int) -> dict:
    """一趟"从星带飞往熔岩星"的航线（航线里只带 1 只猫，方便断言交付）。"""
    return {
        "route_id": route_id,
        "from_planet": 3,
        "to_planet": 1,
        "cat_count": 1,
        "cargo": {},
        "departed_at": arrives_at - int(B.STAR_ROUTE_SECONDS),
        "arrives_at": arrives_at,
        "raided": False,
    }


async def _write_routes(session, routes: list[dict], planet_id: int = 1) -> None:
    await session.rollback()
    planet = await session.get(PlanetState, (1, planet_id), populate_existing=True)
    planet.logistics_routes = routes  # JSON 列必须整条替换
    await session.commit()


def _find_route_id(slot_id: int, lo: float, hi: float) -> str:
    """找一个"平静不劫、来袭必劫"的 route_id：确定性哈希值刚好落在 [lo, hi) 之间。"""
    for offset in range(200_000):
        route_id = f"route_3_1_{offset}"
        if not planet_service.is_raided(slot_id, route_id, chance=lo) and planet_service.is_raided(
            slot_id, route_id, chance=hi
        ):
            return route_id
    raise AssertionError("没找到落在区间的 route_id")


def test_raid_chance_scales_with_planet_cycle() -> None:
    """单元口径：同一趟航线在「来袭」相位的概率是「平静」的 2 倍；rage 与调度官照旧叠加。"""
    route = _star_route("route_3_1_0", arrives_at=STORM_NOW)
    calm = planet_service.raid_chance_for_route(route, now=CALM_NOW, officer_count=0, rage=0.0)
    storm = planet_service.raid_chance_for_route(route, now=STORM_NOW, officer_count=0, rage=0.0)
    assert calm == pytest.approx(B.STAR_ROUTE_RAID_CHANCE)
    assert storm == pytest.approx(B.STAR_ROUTE_RAID_CHANCE * 2)  # 碎星流来袭：被劫掠 ×2
    # 两端都中性（母星↔母星）不受周期影响
    neutral = {"from_planet": 0, "to_planet": 0}
    assert planet_service.raid_chance_for_route(
        neutral, now=STORM_NOW, officer_count=0, rage=0.0
    ) == pytest.approx(B.STAR_ROUTE_RAID_CHANCE)
    # 欧米伽 rage ≥ 60 再翻倍 ⇒ ×2（周期）× 2（rage）= ×4
    assert planet_service.raid_chance_for_route(
        route, now=STORM_NOW, officer_count=0, rage=B.STAR_ROUTE_RAID_RAGE_THRESHOLD
    ) == pytest.approx(B.STAR_ROUTE_RAID_CHANCE * 4)
    # 调度官折扣最后乘、封底 0（10 只 = −100%）
    assert planet_service.raid_chance_for_route(
        route, now=STORM_NOW, officer_count=10, rage=0.0
    ) == 0.0


async def test_debris_storm_turns_calm_route_into_raided(client, session, monkeypatch) -> None:
    """端到端：同一趟航线、同一概率基数，平静时照常交付、来袭时被劫——「×2」真的改变了结果。"""
    await _boot(client, session, cats=4)
    await client.get(LOAD_URL, params={"slot": 1, "planet_id": 1})  # 建行（交付目标）
    monkeypatch.setattr(B, "STAR_ROUTE_RAID_CHANCE", 0.10)
    route_id = _find_route_id(1, 0.10, 0.20)  # 平静 10% 不命中、来袭 20% 命中

    await _write_routes(session, [_star_route(route_id, arrives_at=CALM_NOW - 1)])
    calm_events = await planet_service.settle_routes(session, 1, now=CALM_NOW)
    assert [event["type"] for event in calm_events] == ["ROUTE_ARRIVED"]
    assert calm_events[0].get("note", "") == ""  # 平静相位不编理由（交付事件本来也不带 note）

    # 同一 route_id（哈希完全一样）落在来袭相位 ⇒ 概率 ×2 ⇒ 被判劫掠
    await _write_routes(session, [_star_route(route_id, arrives_at=STORM_NOW - 1)])
    storm_events = await planet_service.settle_routes(session, 1, now=STORM_NOW)
    assert [event["type"] for event in storm_events] == ["ROUTE_RAIDED"]
    # §15.6 事后点名：说清原因（碎星流来袭 ×2）并指向解锁观测能力的科技（铁律 11 同款要求）
    note = storm_events[0]["note"]
    assert "碎星流" in note and "×2" in note
    assert "暗区短波穿透电台" in note
    await session.commit()
    assert (await _routes(session, planet_id=1))[0]["raid_note"] == note  # 在途那行也能看到


async def test_route_arrival_lands_in_offline_report(client, session, monkeypatch) -> None:
    """航线到货要进《离线休整报表》：读档时结算的"不在场事件"不能凭空消失（skill §6）。"""
    await _boot(client, session, cats=4)
    await client.get(LOAD_URL, params={"slot": 1, "planet_id": 1})
    monkeypatch.setattr(B, "STAR_ROUTE_RAID_CHANCE", 0.0)  # 必定安全抵达
    await _write_routes(
        session,
        [dict(_star_route("route_3_1_report", arrives_at=CALM_NOW - 1), cargo={"scrap": 30.0})],
    )
    data = (await client.get(STATE_URL, params={"slot": 1})).json()["data"]
    notes = " ".join(data["offline_report"]["notes"])
    assert "跨星航线抵达【二号星·极热熔岩铸造星】：1 只猫 + 机械废铁 ×30" in notes


async def test_raided_route_arrives_after_delay_even_with_unchanged_chance(
    client, session, monkeypatch
) -> None:
    """回归（幽灵航线）：概率不变时，延误期满必须照常交付。

    旧实现每次到点都重判，而哈希只依赖 `(slot, route_id)` ⇒ 命中的船每 10 分钟再延误一次，
    永远到不了、还白占一条带宽（现有用例靠临时把概率改回 0 才通过，真机没有这个口子）。
    """
    await _boot(client, session, cats=4)
    await client.get(LOAD_URL, params={"slot": 1, "planet_id": 1})
    monkeypatch.setattr(B, "STAR_ROUTE_RAID_CHANCE", 1.0)  # 必定被劫掠，且**整段不还原**
    await _write_routes(session, [_star_route("route_3_1_fixed", arrives_at=CALM_NOW - 1)])

    first = await planet_service.settle_routes(session, 1, now=CALM_NOW)
    await session.commit()
    assert [event["type"] for event in first] == ["ROUTE_RAIDED"]
    routes = await _routes(session, planet_id=1)
    assert routes[0]["raided"] is True
    arrives_at = int(routes[0]["arrives_at"])
    assert arrives_at == CALM_NOW + int(B.STAR_ROUTE_RAID_DELAY_SECONDS)  # 延误 10 分钟

    # 延误期满 ⇒ 概率**仍然是 1.0**，但这条路已经"被劫过一次"，必须交付（旧实现在这里会再次被劫）
    await _write_routes(session, [dict(routes[0], arrives_at=arrives_at - 1)])
    second = await planet_service.settle_routes(session, 1, now=arrives_at)
    await session.commit()
    assert [event["type"] for event in second] == ["ROUTE_ARRIVED"]
    assert await _routes(session, planet_id=1) == []  # 航线上清零，带宽释放
