"""离线报表可观测性：科技进展与凝聚力收入要能被玩家看到（§15.1 / E5）。"""

from __future__ import annotations

from sqlalchemy import select

from app.core import balance as B
from app.models import ColonyState, FacilityState, LaborBucket
from app.models.tech import TechStatus

STATE_URL = "/api/v1/colony/state"


async def test_report_carries_research_and_unity(client, session) -> None:
    await client.get(STATE_URL, params={"slot": 1})
    await session.rollback()
    # 升空（开放星际职业）→ 2 只呼噜大师；同时给一只极客猫推进科研
    silo = (
        await session.execute(
            select(FacilityState).where(
                FacilityState.slot_id == 1,
                FacilityState.planet_id == 0,
                FacilityState.facility_id == "launch_silo",
            )
        )
    ).scalars().one()
    silo.level = len(B.LAUNCH_SILO_STAGES)
    # 科研要电：给图灵终端 + 两座太阳能（否则欠载 ⇒ gained_research 归零）
    for facility_id, level in (("turing_terminal", 1), ("solar_panel", 2)):
        row = (
            await session.execute(
                select(FacilityState).where(
                    FacilityState.slot_id == 1,
                    FacilityState.planet_id == 0,
                    FacilityState.facility_id == facility_id,
                )
            )
        ).scalars().one()
        row.level = level
    colony = await session.get(ColonyState, (1, 0))
    colony.total_cats = 4
    colony.job_idle = 0
    colony.catnip = 200.0  # 别断粮：断粮期间 gained_research 归零（§3.2）
    colony.last_tick_time = int(colony.last_tick_time) - 2000
    for job, count in (("farmer", 2), ("geek", 1), ("purr_master", 2), ("power_runner", 0)):
        bucket = await session.get(LaborBucket, (1, 0, job))
        bucket.cat_count = count
    await session.commit()
    await client.post("/api/v1/tech/research", json={"slot": 1, "tech_id": "tech_cardboard_mechanics"})

    await session.rollback()
    colony = await session.get(ColonyState, (1, 0))
    colony.last_tick_time = int(colony.last_tick_time) - 1200
    await session.commit()
    report = (await client.get(STATE_URL, params={"slot": 1})).json()["data"]["offline_report"]

    assert report["research"] is not None
    assert report["research"]["tech_name"] == "瓦楞纸结构力学"
    assert report["research"]["unlocked"] is True  # 1200 秒 × 1 只极客 > 300 算力
    assert report["gained_unity"] > 0  # 2 只呼噜大师 × 0.02/s × Δt
