"""§15.1 验收：星际职业工位随升空开放，呼噜大师产出文明凝聚力（政令的收入来源）。"""

from __future__ import annotations

from sqlalchemy import select

from app.core import balance as B
from app.models import FacilityState, LaborBucket, SaveSlot

STATE_URL = "/api/v1/colony/state"
DISPATCH_URL = "/api/v1/colony/dispatch"


def test_limits_locked_before_launch() -> None:
    limits = B.workstation_limits({"housing_box": 1}, hangar_capacity=12)
    assert all(limits[job] == 0 for job in B.STAR_JOBS)
    unlocked = B.workstation_limits({"housing_box": 1}, hangar_capacity=12, star_jobs_unlocked=True)
    assert {job: unlocked[job] for job in B.STAR_JOBS} == B.STAR_JOB_LIMITS


async def _launch(session, slot_id: int = 1) -> None:
    """把母星发射井推到顶（等价于"已升空"）。"""
    await session.rollback()
    row = (
        await session.execute(
            select(FacilityState).where(
                FacilityState.slot_id == slot_id,
                FacilityState.planet_id == B.HOME_PLANET_ID,
                FacilityState.facility_id == "launch_silo",
            )
        )
    ).scalars().one()
    row.level = len(B.LAUNCH_SILO_STAGES)
    await session.commit()


async def test_star_jobs_assignable_only_after_launch(client, session) -> None:
    await client.get(STATE_URL, params={"slot": 1})
    # 给 3 只猫（不必走完整冷启动，聚焦星际职业本身）
    await session.rollback()
    from app.models import ColonyState

    colony = await session.get(ColonyState, (1, 0))
    colony.total_cats = 3
    colony.job_idle = 3
    await session.commit()

    before = await client.post(
        DISPATCH_URL, json={"slot": 1, "job_id": "purr_master", "delta": 1}
    )
    assert before.status_code == 400
    assert before.json()["message"] == "WORKSTATION_LIMIT_EXCEEDED"

    await _launch(session)
    after = await client.post(DISPATCH_URL, json={"slot": 1, "job_id": "purr_master", "delta": 1})
    assert after.status_code == 200, after.text
    assert after.json()["data"]["count"] == 1


async def test_purr_master_produces_unity(client, session) -> None:
    await client.get(STATE_URL, params={"slot": 1})
    await _launch(session)
    await session.rollback()
    bucket = await session.get(LaborBucket, (1, 0, "purr_master"))
    bucket.cat_count = 2
    from app.models import ColonyState

    colony = await session.get(ColonyState, (1, 0))
    colony.last_tick_time = int(colony.last_tick_time) - 1000
    await session.commit()

    await client.get(STATE_URL, params={"slot": 1})
    await session.rollback()
    save = await session.get(SaveSlot, 1, populate_existing=True)
    # 2 只 × 0.02/s × 1000s ≈ 40 点（秒级截断留容差）
    assert 38.0 <= float(save.unity) <= 42.0

    # 政令商店能看到这笔收入
    listed = (await client.get("/api/v1/doctrine/list", params={"slot": 1})).json()["data"]
    assert listed["unity"] == round(float(save.unity), 2)
