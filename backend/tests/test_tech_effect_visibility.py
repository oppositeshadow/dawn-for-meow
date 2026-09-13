"""接口如实标注"哪些载荷已接线"（《数值平衡表》§6.4 三类载荷的可见性）。"""

from __future__ import annotations

from sqlalchemy import select

from app.models import TechRecord
from app.models.tech import TechStatus

TREE_URL = "/api/v1/tech/tree"


async def test_node_reports_active_and_pending_effects(client, session) -> None:
    await client.get("/api/v1/colony/state", params={"slot": 1})

    def node(data: dict, tech_id: str) -> dict:
        return next(item for item in data["nodes"] if item["tech_id"] == tech_id)

    data = (await client.get(TREE_URL, params={"slot": 1, "planet_id": 0})).json()["data"]
    farm = node(data, "tech_hydroponics_basics")
    # catnip_efficiency 已接线 ⇒ 出现在 active_effects
    assert farm["active_effects"] == {"catnip_efficiency": 0.2}
    assert farm["pending_effects"] == {}

    armor = node(data, "tech_heavy_breaker_exoskeleton")
    # fleet_armor 已接线（全军装甲加成）⇒ 出现在 active_effects
    assert armor["active_effects"] == {"fleet_armor": 0.2}
    assert armor["pending_effects"] == {}

    battery = node(data, "tech_battery_matrix")
    # battery_kwh_max 已接线（电容池扩容）⇒ 出现在 active_effects
    assert battery["active_effects"] == {"battery_kwh_max": 200}
    assert battery["pending_effects"] == {}

    # 人工塞入一个白名单键 ⇒ 立刻反映为"已接线"
    await session.rollback()
    row = (
        await session.execute(
            select(TechRecord).where(
                TechRecord.slot_id == 1, TechRecord.planet_id == 0, TechRecord.tech_id == "tech_heavy_breaker_exoskeleton"
            )
        )
    ).scalars().one()
    row.buff_payload = {"power_kw": 3, "smelt_speed": 1.2}
    row.status = TechStatus.LOCKED
    await session.commit()
    data = (await client.get(TREE_URL, params={"slot": 1, "planet_id": 0})).json()["data"]
    refreshed = node(data, "tech_heavy_breaker_exoskeleton")
    assert refreshed["active_effects"] == {"power_kw": 3}
    assert refreshed["pending_effects"] == {"smelt_speed": 1.2}
