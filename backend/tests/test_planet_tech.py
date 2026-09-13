"""模块 E4 验收：外星球特化科技树（LLM 场景 2 + 本地卡池 + 单卡重 Roll）。"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core import balance as B
from app.models import PlanetState, TechRecord
from app.schemas.tech_agent import TechCard
from app.services import planet_tech_service, tech_service
from app.services.llm_service import LlmResult, LlmUsage

TECH_URL = "/api/v1/tech"
PLANET_URL = "/api/v1/planet"


@pytest.fixture(autouse=True)
def _clear_cooldown() -> None:
    tech_service.reset_reroll_cooldown()


async def _bootstrap(client) -> dict:
    return (await client.get("/api/v1/colony/state")).json()["data"]


async def _unlock(session, planet_id: int) -> None:
    await session.rollback()
    planet = await session.get(PlanetState, (1, planet_id))
    planet.unlocked = True
    await session.commit()


async def _switch(client, session, planet_id: int) -> dict:
    await _unlock(session, planet_id)
    resp = await client.post(f"{PLANET_URL}/switch", json={"slot": 1, "planet_id": planet_id})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def test_home_planet_has_no_agent_nodes(client) -> None:
    await _bootstrap(client)
    data = (await client.get(f"{TECH_URL}/tree", params={"planet_id": 0})).json()["data"]
    assert data["total_nodes"] == 19
    assert all(node["is_agent_generated"] is False for node in data["nodes"])


async def test_switch_lands_specialized_tree_from_local_pool(client, session) -> None:
    await _bootstrap(client)
    body = await _switch(client, session, 1)
    assert body["specialized_techs"]["count"] == B.STAR_TECH_NODE_COUNT
    assert body["specialized_techs"]["source"] == "FALLBACK"  # 测试期无密钥 → 本地卡池

    data = (await client.get(f"{TECH_URL}/tree", params={"planet_id": 1})).json()["data"]
    assert data["total_nodes"] == 12
    assert all(node["is_agent_generated"] is True for node in data["nodes"])
    assert all(node["tech_name"] and node["flavor_text"] for node in data["nodes"])

    tiers: dict[int, list[dict]] = {}
    for node in data["nodes"]:
        tiers.setdefault(node["tier"], []).append(node)
    assert {tier: len(rows) for tier, rows in tiers.items()} == {1: 5, 2: 4, 3: 3}
    assert {row["target_cost"] for row in tiers[1]} == {B.STAR_TECH_TIER_COSTS[0]}
    assert {row["target_cost"] for row in tiers[2]} == {B.STAR_TECH_TIER_COSTS[1]}
    assert {row["target_cost"] for row in tiers[3]} == {B.STAR_TECH_TIER_COSTS[2]}
    # Tier 1 无前置 ⇒ 立即可研发；Tier 2/3 有前置
    assert all(row["available"] for row in tiers[1])
    assert all(row["parent_ids"] for row in tiers[2] + tiers[3])


async def test_specialized_tree_is_idempotent(client, session) -> None:
    await _bootstrap(client)
    await _switch(client, session, 1)
    again = await _switch(client, session, 1)
    assert again["specialized_techs"] is None  # 已有节点 ⇒ 不再生成、不再调 LLM
    await session.rollback()
    rows = (
        await session.execute(
            select(TechRecord.tech_id).where(TechRecord.planet_id == 1, TechRecord.slot_id == 1)
        )
    ).scalars().all()
    assert len(rows) == B.STAR_TECH_NODE_COUNT


async def test_reroll_regenerates_card_and_spends_progress(client, session) -> None:
    await _bootstrap(client)
    await _switch(client, session, 2)
    await session.rollback()
    record = (
        await session.execute(
            select(TechRecord).where(TechRecord.planet_id == 2, TechRecord.tier == 1).order_by(TechRecord.node_order)
        )
    ).scalars().first()
    old_name, old_flavor = record.tech_name, record.flavor_text
    record.current_progress = float(record.target_cost)
    await session.commit()
    tech_id = record.tech_id

    resp = await client.post(f"{TECH_URL}/reroll", json={"slot": 1, "planet_id": 2, "tech_id": tech_id})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    expected_cost = max(B.TECH_REROLL_MIN_COST, round(float(B.STAR_TECH_TIER_COSTS[0]) * B.TECH_REROLL_COST_RATIO, 2))
    assert data["cost"] == expected_cost
    assert data["remaining_progress"] == float(B.STAR_TECH_TIER_COSTS[0]) - expected_cost
    assert data["card"]["source"] == "FALLBACK"
    assert data["card"]["tech_name"] != old_name  # 换一张不同名的卡

    await session.rollback()
    fresh = await session.get(TechRecord, (1, 2, tech_id), populate_existing=True)
    assert fresh.tech_name == data["card"]["tech_name"]
    assert fresh.flavor_text != old_flavor
    assert fresh.target_cost == B.STAR_TECH_TIER_COSTS[0]  # 卡面变、成本不变

    # 冷却期内二次重 Roll 一律拒绝
    again = await client.post(f"{TECH_URL}/reroll", json={"slot": 1, "planet_id": 2, "tech_id": tech_id})
    assert again.status_code == 409
    assert again.json()["message"] == "TECH_REROLL_COOLDOWN"


async def test_reroll_manual_node_still_refused(client) -> None:
    await _bootstrap(client)
    resp = await client.post(
        f"{TECH_URL}/reroll", json={"slot": 1, "planet_id": 0, "tech_id": "tech_cardboard_mechanics"}
    )
    assert resp.status_code == 400
    assert resp.json()["message"] == "TECH_NOT_REROLLABLE"


async def test_batch_llm_cards_pass_firewall(client, session, monkeypatch) -> None:
    await _bootstrap(client)

    class FakeLlmService:
        configured = True

        def __init__(self) -> None:
            from app.services.llm_service import LlmBudget

            self.budget = LlmBudget()

        async def complete_json(self, **_: object) -> LlmResult:
            return LlmResult(
                payload={
                    "cards": [
                        {
                            "tech_name": "熔岩透镜塔",
                            "flavor_text": "把恒星的光聚成一把刀。",
                            "mechanic_type": "PASSIVE_BUFF",
                            "buff_payload": {"power_kw": 6},
                        },
                        {
                            "tech_name": "灰烬轮耕",
                            "flavor_text": "灰里种猫草，长得更野。",
                            "mechanic_type": "CONVERSION",
                            "buff_payload": {"in": "scrap", "out": "catnip", "ratio": 5},
                        },
                        {  # 想偷偷改成本 ⇒ 被防火墙丢掉
                            "tech_name": "免费午餐机",
                            "flavor_text": "不劳而获的味道。",
                            "mechanic_type": "PASSIVE_BUFF",
                            "buff_payload": {"target_cost": 1},
                        },
                    ]
                },
                usage=LlmUsage(scene="TECH_CARD", model="fake", ok=True, attempts=1),
            )

    monkeypatch.setattr(planet_tech_service, "get_llm_service", lambda: FakeLlmService())
    body = await _switch(client, session, 1)
    specialized = body["specialized_techs"]
    assert specialized["source"] == "LLM"
    assert specialized["rejected_cards"] == 1

    data = (await client.get(f"{TECH_URL}/tree", params={"planet_id": 1})).json()["data"]
    ordered = sorted(data["nodes"], key=lambda node: (node["tier"], node["node_order"]))
    assert ordered[0]["tech_name"] == "熔岩透镜塔"
    assert ordered[1]["tech_name"] == "灰烬轮耕"
    assert ordered[2]["is_agent_generated"] is True  # 未覆盖的节点继续用本地卡池
    assert "免费午餐机" not in {node["tech_name"] for node in data["nodes"]}


def test_card_schema_rejects_forbidden_payload() -> None:
    for payload in ({"target_cost": 1}, {"tier": 4}, {"parent_ids": []}):
        with pytest.raises(Exception):
            TechCard(
                tech_name="越权卡",
                flavor_text="想改结构。",
                mechanic_type="PASSIVE_BUFF",
                buff_payload=payload,
            )
    with pytest.raises(Exception):
        TechCard(tech_name="A", flavor_text="太短", mechanic_type="PASSIVE_BUFF")
    with pytest.raises(Exception):
        TechCard(tech_name="正常卡", flavor_text="机制类型瞎写。", mechanic_type="FREE_STUFF")
