"""模块 E 验收：DAG 前置校验、算力累积解锁、科研等级折扣、重 Roll 与建造门槛。"""

from __future__ import annotations

import time

import pytest

from app.core import balance as B
from app.models import ColonyState, FacilityState, LaborBucket, TechRecord
from app.models.tech import TechStatus
from app.services import tech_service

from tests.test_cold_start_loop import _build, _click, _dispatch, _rewind

TECH_URL = "/api/v1/tech"


@pytest.fixture(autouse=True)
def _reset_reroll_cooldown():
    """重 Roll 冷却是进程内状态，测试之间必须清空。"""
    tech_service.reset_reroll_cooldown()
    yield
    tech_service.reset_reroll_cooldown()


async def _bootstrap(client) -> dict:
    return (await client.get("/api/v1/colony/state")).json()["data"]


async def _set_status(session, tech_id: str, status: TechStatus, progress: float = 0.0) -> None:
    await session.rollback()
    record = await session.get(TechRecord, (1, 0, tech_id))
    record.status = status
    record.current_progress = progress
    await session.commit()


class TestTechTree:
    async def test_tree_returns_19_locked_nodes(self, client):
        await _bootstrap(client)
        data = (await client.get(f"{TECH_URL}/tree")).json()["data"]
        assert data["total_nodes"] == B.PLANET0_TECH_NODE_COUNT == 19
        assert data["unlocked_count"] == 0
        assert data["research_tier_level"] == 1
        assert data["tier_sizes"] == {"1": 4, "2": 4, "3": 5, "4": 6}
        assert data["total_cost"] == B.PLANET0_TOTAL_RESEARCH
        assert data["researching"] is None
        roots = [node for node in data["nodes"] if not node["parent_ids"]]
        assert all(node["available"] for node in roots)
        assert len(roots) == 3

    async def test_nodes_carry_discount_by_tier(self, client):
        await _bootstrap(client)
        nodes = {n["tech_id"]: n for n in (await client.get(f"{TECH_URL}/tree")).json()["data"]["nodes"]}
        # 折扣按阶梯走，具体值从种子读取，避免每次调曲线都要改测试
        first_cost = nodes["tech_cardboard_mechanics"]["target_cost"]
        assert nodes["tech_cardboard_mechanics"]["display_cost"] == first_cost  # Tier 1 无折扣
        assert nodes["tech_acoustic_layer"]["display_cost"] == nodes["tech_acoustic_layer"]["target_cost"] * 0.8
        assert nodes["tech_meow_ai_labor"]["display_cost"] == nodes["tech_meow_ai_labor"]["target_cost"] * 0.6


class TestResearch:
    async def test_start_research_and_accumulate_until_unlock(self, client, session):
        await _bootstrap(client)
        assert (await client.post(f"{TECH_URL}/research", json={"tech_id": "tech_cardboard_mechanics"})).status_code == 200

        tree = (await client.get(f"{TECH_URL}/tree")).json()["data"]
        assert tree["researching"]["tech_id"] == "tech_cardboard_mechanics"
        first_cost = tree["researching"]["display_cost"]
        assert first_cost > 0

        # 给 1 只极客猫（工位靠图灵终端，这里直接放行数据以聚焦研发逻辑）
        await session.rollback()
        bucket = await session.get(LaborBucket, (1, 0, "geek"))
        bucket.cat_count = 1
        await session.commit()
        await _rewind(session, int(first_cost) + 5)  # 1.0/s ⇒ 秒数 = 算力需求 + 余量
        await client.get("/api/v1/colony/state")

        tree = (await client.get(f"{TECH_URL}/tree")).json()["data"]
        node = next(n for n in tree["nodes"] if n["tech_id"] == "tech_cardboard_mechanics")
        assert node["status"] == "UNLOCKED"
        assert tree["unlocked_count"] == 1
        assert tree["researching"] is None

    async def test_prerequisite_is_enforced(self, client):
        """E-1：前置未解锁 ⇒ 400 BAD_REQUEST（DAG 防火墙）。"""
        await _bootstrap(client)
        response = await client.post(
            f"{TECH_URL}/research", json={"tech_id": "tech_night_stealth_scavenging"}
        )
        assert response.status_code == 400
        assert response.json()["message"] == "TECH_PREREQUISITE_MISSING"
        assert "废旧家电逆向拆解" in response.json()["detail"]

    async def test_single_thread_research(self, client):
        await _bootstrap(client)
        await client.post(f"{TECH_URL}/research", json={"tech_id": "tech_cardboard_mechanics"})
        response = await client.post(f"{TECH_URL}/research", json={"tech_id": "tech_hydroponics_basics"})
        assert response.status_code == 409
        assert response.json()["message"] == "RESEARCH_IN_PROGRESS"

    async def test_unknown_tech_rejected(self, client):
        await _bootstrap(client)
        assert (await client.post(f"{TECH_URL}/research", json={"tech_id": "tech_moon_base"})).status_code == 400

    async def test_research_tier_level_advances_when_tier_complete(self, client, session):
        await _bootstrap(client)
        for tech in (
            "tech_cardboard_mechanics",
            "tech_hydroponics_basics",
            "tech_appliance_teardown",
            "tech_night_stealth_scavenging",
        ):
            await _set_status(session, tech, TechStatus.UNLOCKED, progress=1.0)

        tree = (await client.get(f"{TECH_URL}/tree")).json()["data"]
        assert tree["research_tier_level"] == 2  # Tier 1 全解锁 ⇒ 科研等级 2
        tier2 = next(n for n in tree["nodes"] if n["tech_id"] == "tech_acoustic_layer")
        assert tier2["available"] is True
        assert tier2["display_cost"] == tier2["target_cost"] * 0.8  # 8 折


class TestReroll:
    async def test_hand_authored_node_cannot_reroll(self, client):
        await _bootstrap(client)
        response = await client.post(f"{TECH_URL}/reroll", json={"tech_id": "tech_cardboard_mechanics"})
        assert response.status_code == 400
        assert response.json()["message"] == "TECH_NOT_REROLLABLE"

    async def test_agent_generated_node_costs_progress(self, client, session):
        await _bootstrap(client)
        await session.rollback()
        record = await session.get(TechRecord, (1, 0, "tech_acoustic_layer"))
        record.is_agent_generated = True
        cost = max(B.TECH_REROLL_MIN_COST, float(record.target_cost) * B.TECH_REROLL_COST_RATIO)
        record.current_progress = cost + 40.0
        await session.commit()

        body = (await client.post(f"{TECH_URL}/reroll", json={"tech_id": "tech_acoustic_layer"})).json()
        assert body["code"] == 200
        assert body["data"]["cost"] == cost
        assert body["data"]["remaining_progress"] == 40.0

    async def test_reroll_requires_progress(self, client, session):
        await _bootstrap(client)
        await session.rollback()
        record = await session.get(TechRecord, (1, 0, "tech_acoustic_layer"))
        record.is_agent_generated = True
        record.current_progress = 10.0
        await session.commit()
        response = await client.post(f"{TECH_URL}/reroll", json={"tech_id": "tech_acoustic_layer"})
        assert response.status_code == 400
        assert response.json()["message"] == "INSUFFICIENT_RESOURCE"

    async def test_reroll_cooldown(self, client, session):
        await _bootstrap(client)
        await session.rollback()
        record = await session.get(TechRecord, (1, 0, "tech_acoustic_layer"))
        record.is_agent_generated = True
        record.current_progress = 500.0
        await session.commit()
        assert (await client.post(f"{TECH_URL}/reroll", json={"tech_id": "tech_acoustic_layer"})).status_code == 200
        second = await client.post(f"{TECH_URL}/reroll", json={"tech_id": "tech_acoustic_layer"})
        assert second.status_code == 409
        assert second.json()["message"] == "TECH_REROLL_COOLDOWN"


class TestBuildGate:
    async def test_advanced_facility_needs_tech(self, client):
        """解锁效果：进阶设施首次建造需要对应科技。"""
        await _bootstrap(client)
        await _click(client, 40)
        response = await _build(client, "solar_panel")  # 需要 tech_cardboard_mechanics
        assert response.status_code == 400
        assert response.json()["message"] == "TECH_LOCKED"
        assert "瓦楞纸结构力学" in response.json()["detail"]

    async def test_turing_terminal_is_exempt_to_break_the_deadlock(self, client):
        """图灵终端免门槛：否则"Tier 1 科技要算力、算力要极客猫、极客猫要 Tier 2 科技"会死锁。"""
        await _bootstrap(client)
        await _click(client, 40)
        response = await _build(client, "turing_terminal")
        assert response.status_code == 400
        assert response.json()["message"] == "INSUFFICIENT_RESOURCE"  # 只差料，不再卡科技

    async def test_cold_start_facilities_are_exempt(self, client):
        """开荒部件免门槛：否则 GDD §1.3 的冷启动会死锁。"""
        await _bootstrap(client)
        await _click(client, 20)
        assert (await _build(client, "housing_box")).status_code == 200
        assert (await _build(client, "farm_plot")).status_code == 200

    async def test_gate_opens_after_unlock(self, client, session):
        await _bootstrap(client)
        await _click(client, 40)
        await session.rollback()
        colony = await session.get(ColonyState, (1, 0))
        colony.scrap, colony.chips = 200.0, 100.0
        await session.commit()
        await _set_status(session, "tech_cardboard_mechanics", TechStatus.UNLOCKED, progress=1.0)
        assert (await _build(client, "solar_panel")).status_code == 200
