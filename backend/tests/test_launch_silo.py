"""模块 K 验收：发射井四阶段推进、要塞前置、升空与星区星图解锁。"""

from __future__ import annotations

import pytest

from app.core import balance as B
from app.models import BossState, ColonyState, FacilityState, PlanetState

PLANET_URL = "/api/v1/planet"
BUILD_URL = "/api/v1/facilities/build"


async def _bootstrap(client) -> dict:
    return (await client.get("/api/v1/colony/state")).json()["data"]


async def _give(session, **resources: float) -> None:
    await session.rollback()
    from app.models import TechRecord
    from app.models.tech import TechStatus

    # 发射井是 Tier 4 科技解锁的进阶设施：测试里直接把对应科技设为已解锁
    tech = await session.get(TechRecord, (1, 0, "tech_launch_silo_engineering"))
    tech.status = TechStatus.UNLOCKED
    colony = await session.get(ColonyState, (1, 0))
    colony.scrap_max, colony.scrap = 500.0, 500.0
    colony.alloys_max, colony.alloys = 500.0, 500.0
    colony.chips_max, colony.chips = 300.0, 300.0
    colony.battery_max, colony.battery = 50.0, 30.0
    for key, value in resources.items():
        setattr(colony, key, value)
    await session.commit()


async def _advance_all_stages(client, session, *, destroy_fortress: bool = False) -> list[dict]:
    results: list[dict] = []
    for index in range(len(B.LAUNCH_SILO_STAGES)):
        response = await client.post(BUILD_URL, json={"facility_id": "launch_silo", "count": 1})
        if response.status_code != 200:
            results.append({"error": response.json()})
            break
        results.append(response.json()["data"])
        if destroy_fortress and index == len(B.LAUNCH_SILO_STAGES) - 2:
            await session.rollback()
            boss = await session.get(BossState, 1)
            boss.bombardment_state = {"fortress_destroyed_at": 1}
            await session.commit()
    return results


class TestLaunchSiloStages:
    async def test_stage_costs_match_balance_sheet(self, client, session):
        await _bootstrap(client)
        await _give(session)
        data = (await client.get("/api/v1/colony/state")).json()["data"]
        silo = data["launch_silo"]
        assert silo["level"] == 0
        assert silo["max_level"] == 4
        assert silo["fortress_down"] is False
        assert silo["next_stage"]["cost"] == {"scrap": 200.0, "alloys": 20.0}
        assert silo["stages"][3]["blocked"] is True  # 阶段④被要塞前置挡住

        first = (await client.post(BUILD_URL, json={"facility_id": "launch_silo"})).json()["data"]
        assert first["level"] == 1
        assert first["cost_paid"] == {"scrap": 200.0, "alloys": 20.0}
        assert "竖坑清理" in (first["narrative"] or "")

    async def test_final_stage_requires_fortress_down(self, client, session):
        await _bootstrap(client)
        await _give(session)
        for _ in range(3):
            assert (
                await client.post(BUILD_URL, json={"facility_id": "launch_silo"})
            ).status_code == 200
        blocked = await client.post(BUILD_URL, json={"facility_id": "launch_silo"})
        assert blocked.status_code == 400
        assert blocked.json()["message"] == "FORTRESS_INTACT"

    async def test_insufficient_material_rejected(self, client, session):
        await _bootstrap(client)
        await _give(session, scrap=0.0, alloys=0.0)
        response = await client.post(BUILD_URL, json={"facility_id": "launch_silo"})
        assert response.status_code == 400
        assert response.json()["message"] == "INSUFFICIENT_RESOURCE"

    async def test_launch_unlocks_star_chart(self, client, session):
        """四阶段全通 + 要塞已毁 ⇒ 点火升空，解锁二号熔岩星。"""
        await _bootstrap(client)
        await _give(session)
        results = await _advance_all_stages(client, session, destroy_fortress=True)
        assert all("error" not in item for item in results), results
        final = results[-1]
        assert final["level"] == 4
        assert "喵星一号点火升空" in (final["narrative"] or "")
        assert final["unlocked_planets"]  # 新开放的星球名

        await session.rollback()
        planet = await session.get(PlanetState, (1, 1), populate_existing=True)
        assert planet.unlocked is True

        state = (await client.get("/api/v1/colony/state")).json()["data"]
        assert state["launch_silo"]["launched"] is True


class TestPlanetApi:
    async def test_planet_state_lists_four_celestial_bodies(self, client):
        await _bootstrap(client)
        data = (await client.get(f"{PLANET_URL}/state")).json()["data"]
        assert len(data["planets"]) == 4
        home = next(item for item in data["planets"] if item["planet_id"] == 0)
        assert home["unlocked"] is True and home["is_active"] is True
        assert "母星不删档" in data["note"]

    async def test_switch_requires_unlock(self, client):
        await _bootstrap(client)
        response = await client.post(f"{PLANET_URL}/switch", json={"planet_id": 1})
        assert response.status_code == 400
        assert response.json()["message"] == "PLANET_LOCKED"

    async def test_switch_after_launch(self, client, session):
        await _bootstrap(client)
        await _give(session)
        await _advance_all_stages(client, session, destroy_fortress=True)
        data = (await client.post(f"{PLANET_URL}/switch", json={"planet_id": 1})).json()["data"]
        assert data["planet_id"] == 1
        await session.rollback()
        assert (await session.get(PlanetState, (1, 1), populate_existing=True)).is_active is True
        assert (await session.get(PlanetState, (1, 0), populate_existing=True)).is_active is False

    async def test_unknown_planet_rejected(self, client):
        await _bootstrap(client)
        response = await client.post(f"{PLANET_URL}/switch", json={"planet_id": 3})
        assert response.status_code == 400
