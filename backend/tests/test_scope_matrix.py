"""把《代码结构稿》§7《星球作用域矩阵》变成可执行检查。

这张表是两次"文档说 A、代码做 B"之后总结出来的，所以它不能只是文字：

* **母星专属**：断言确实存在显式校验（不是靠"玩家不会那么做"）；
* **每星球独立**：断言建行后每颗星球都有自己的一套状态（互不串档）；
* **存档级**：断言开分基地**不会**复制出第二份存档级状态（深网 / 欧米伽 / 生涯统计）。

以后新增机制忘了登记或忘了校验，这个文件会直接红。
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.models import (
    BossState,
    CareerStats,
    ColonyState,
    DarknetState,
    FacilityState,
    GardenState,
    LaborBucket,
    MilitaryState,
    PlanetState,
    SaveSlot,
)

SWITCH_URL = "/api/v1/planet/switch"
STATE_URL = "/api/v1/colony/state"


async def _boot(client) -> None:
    await client.get(STATE_URL, params={"slot": 1})


async def _unlock_and_switch(client, session, planet_id: int) -> None:
    await session.rollback()
    planet = await session.get(PlanetState, (1, planet_id))
    planet.unlocked = True
    await session.commit()
    await client.post(SWITCH_URL, json={"slot": 1, "planet_id": planet_id})


async def _count(session, model) -> int:
    await session.rollback()
    return int(
        (await session.execute(select(func.count()).select_from(model).where(model.slot_id == 1))).scalar_one()
    )


class TestHomePlanetOnly:
    """母星专属机制：必须带显式校验与明确错误码。"""

    async def test_scavenge_rejected_off_home(self, client, session):
        await _boot(client)
        await _unlock_and_switch(client, session, 1)
        resp = await client.post("/api/v1/colony/scavenge", params={"slot": 1, "planet_id": 1})
        assert resp.status_code == 400
        assert resp.json()["message"] == "COLD_START_HOME_ONLY"

    async def test_launch_silo_rejected_off_home(self, client, session):
        await _boot(client)
        await _unlock_and_switch(client, session, 2)
        resp = await client.post(
            "/api/v1/facilities/build", json={"slot": 1, "planet_id": 2, "facility_id": "launch_silo"}
        )
        assert resp.status_code == 400
        assert resp.json()["message"] == "LAUNCH_SILO_HOME_ONLY"


class TestPerPlanet:
    """每星球独立：建行后每颗星都有自己的一套状态，互不串档。"""

    async def test_star_colony_gets_its_own_rows(self, client, session):
        await _boot(client)
        await _unlock_and_switch(client, session, 1)
        await _unlock_and_switch(client, session, 3)

        for model in (ColonyState, MilitaryState, GardenState):
            rows = (
                await session.execute(
                    select(model.planet_id).where(model.slot_id == 1).order_by(model.planet_id)
                )
            ).scalars().all()
            assert list(rows) == [0, 1, 3], f"{model.__tablename__} 应每星球一行"
        for model in (LaborBucket, FacilityState):
            rows = (
                await session.execute(
                    select(func.count(func.distinct(model.planet_id))).where(model.slot_id == 1)
                )
            ).scalar_one()
            assert int(rows) == 3, f"{model.__tablename__} 应覆盖三颗星球"

    async def test_star_colony_resources_are_independent(self, client, session):
        await _boot(client)
        await _unlock_and_switch(client, session, 1)
        await session.rollback()
        colony = await session.get(ColonyState, (1, 1))
        colony.scrap = 77.0
        await session.commit()

        home = (await client.get(STATE_URL, params={"slot": 1, "planet_id": 0})).json()["data"]
        star = (await client.get(STATE_URL, params={"slot": 1, "planet_id": 1})).json()["data"]
        assert star["resources"]["scrap"] == 77.0
        assert home["resources"]["scrap"] != 77.0  # 母星不受影响

    async def test_declared_build_rows_match_reality(self, client, session):
        """建行清单是**声明式**的：声明了哪几张表，就必须真的都建出该星球的行。"""
        from app.services import planet_service

        await _boot(client)
        await _unlock_and_switch(client, session, 1)
        await session.rollback()
        for table_name in planet_service.STAR_COLONY_TABLES:
            model = next(
                item
                for item in (
                    ColonyState,
                    LaborBucket,
                    FacilityState,
                    MilitaryState,
                    GardenState,
                )
                if item.__tablename__ == table_name
            )
            rows = (
                await session.execute(
                    select(func.count())
                    .select_from(model)
                    .where(model.slot_id == 1, model.planet_id == 1)
                )
            ).scalar_one()
            assert int(rows) > 0, f"{table_name} 声明在建行清单里，却没有该星球的行"


class TestSlotScoped:
    """存档级机制：开分基地绝不能复制出第二份（否则深网行情/终局进度会分裂）。"""

    async def test_star_colonies_do_not_duplicate_slot_rows(self, client, session):
        await _boot(client)
        for planet_id in (1, 2, 3):
            await _unlock_and_switch(client, session, planet_id)

        for model in (SaveSlot, CareerStats, DarknetState, BossState):
            assert await _count(session, model) == 1, f"{model.__tablename__} 只应有 1 行"

    async def test_slot_rows_stay_slot_scoped_in_schema(self, session):
        """静态检查：存档级模型的主键里**不该有** planet_id（防止以后被"顺手"改宽）。"""
        for model in (CareerStats, DarknetState, BossState):
            keys = {column.key for column in model.__table__.primary_key.columns}
            assert keys == {"slot_id"}, f"{model.__tablename__} 的主键应只有 slot_id，实际 {keys}"
