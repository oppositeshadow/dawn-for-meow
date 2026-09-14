"""星际物流调度官验收（《数值平衡表》§15.1）：航线吞吐 +20%/只、被劫掠 −10%/只。"""

from __future__ import annotations

from app.core import balance as B


def test_cargo_capacity_scales_with_officers() -> None:
    base = float(B.STAR_ROUTE_CARGO_PER_TRIP["scrap"])
    assert B.logistics_cargo_capacity(0, base) == base
    assert B.logistics_cargo_capacity(1, base) == round(base * 1.2, 2)
    assert B.logistics_cargo_capacity(4, base) == round(base * 1.8, 2)  # 上限 4 只（§15.1）


def test_raid_chance_drops_but_never_negative() -> None:
    base = B.STAR_ROUTE_RAID_CHANCE
    assert B.logistics_raid_chance(0, base) == base
    assert abs(B.logistics_raid_chance(2, base) - base * 0.8) < 1e-6
    assert B.logistics_raid_chance(99, base) == 0.0  # 封底 0，不会出现负概率


async def test_migrate_accepts_more_cargo_with_officer(client, session) -> None:
    """码头上有 1 只调度官时，单趟废铁上限从 60 提到 72。"""
    await client.get("/api/v1/colony/state", params={"slot": 1})
    await session.rollback()
    from app.models import ColonyState, LaborBucket, PlanetState

    bucket = await session.get(LaborBucket, (1, 0, "logistics"))
    bucket.cat_count = 1
    colony = await session.get(ColonyState, (1, 0))
    colony.scrap = 500.0
    colony.total_cats = 3
    colony.job_idle = 3
    planet = await session.get(PlanetState, (1, 1))
    planet.unlocked = True
    await session.commit()

    over = await client.post(
        "/api/v1/planet/migrate",
        json={"slot": 1, "to_planet": 1, "count": 1, "cargo": {"scrap": 80}},
    )
    assert over.status_code == 400  # 80 > 72
    assert "72" in over.json()["detail"]

    ok = await client.post(
        "/api/v1/planet/migrate",
        json={"slot": 1, "to_planet": 1, "count": 1, "cargo": {"scrap": 70}},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["data"]["cargo"] == {"scrap": 70.0}
