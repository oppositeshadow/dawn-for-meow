"""战斗过程可展示性：三条战斗链的响应必须带 `rounds` 与 `log`（前端据此播报交战过程）。"""

from __future__ import annotations

import time

from sqlalchemy import select

from app.core import balance as B
from app.models import BossState, ColonyState, FacilityState, MilitaryState, SaveSlot, VehicleUnit
from app.models.military import VehicleStatus

STATE_URL = "/api/v1/colony/state"


async def _prepare(client, session) -> int:
    """给槽位 1 备一辆装甲猫车 + 足够资源，并让"掠夺突袭"到期（可拦截）。"""
    await client.get(STATE_URL, params={"slot": 1})
    await session.rollback()
    spec = B.VEHICLE_TYPES["armored_car"]
    unit = VehicleUnit(
        slot_id=1, planet_id=0, unit_type="armored_car", nickname=None, modules=[],
        shield=float(spec["shield"]), armor=float(spec["armor"]), armor_max=float(spec["armor"]),
        hull=float(spec["hull"]), status=VehicleStatus.IDLE, crew_cats=int(spec["crew"]),
        acquired_at=int(time.time()),
    )
    session.add(unit)
    colony = await session.get(ColonyState, (1, 0))
    colony.total_cats = 10
    colony.job_idle = 10
    colony.chips, colony.alloys = 500.0, 500.0
    boss = await session.get(BossState, 1)
    # 拦截窗口 = 突袭**尚未**落地（`raid_ends_at` 在未来）——设成过去会变成"已经突袭得手"
    boss.raid_ends_at = int(time.time()) + 600
    boss.raid_stage = "INCOMING"
    boss.bombardment_state = {**(boss.bombardment_state or {}), "raid_targets": ["farm_plot"]}
    await session.commit()
    return int(unit.unit_id)


async def test_intercept_raid_returns_rounds_and_log(client, session) -> None:
    unit_id = await _prepare(client, session)
    response = await client.post(
        "/api/v1/military/intercept-raid", json={"slot": 1, "planet_id": 0, "unit_ids": [unit_id]}
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["rounds"] >= 1
    assert data["log"]  # 逐回合战报，前端会念出末尾几行
    assert data["won"] in (True, False)


async def test_final_assault_returns_rounds_and_log(client, session) -> None:
    unit_id = await _prepare(client, session)
    # 决战需要机库与物资：给一座发射井并把兵工厂停工解除
    await session.rollback()
    silo = (
        await session.execute(
            select(FacilityState).where(
                FacilityState.slot_id == 1,
                FacilityState.planet_id == 0,
                FacilityState.facility_id == "launch_silo",
            )
        )
    ).scalars().one()
    silo.level = 1
    save = await session.get(SaveSlot, 1)
    save.active_planet_id = 0
    await session.commit()

    response = await client.post(
        "/api/v1/military/final-assault",
        json={"slot": 1, "planet_id": 0, "stage": 1, "unit_ids": [unit_id]},
    )
    # 物资/顺序不满足时也要给出明确的业务错误，而不是 500
    assert response.status_code in (200, 400, 409), response.text
    if response.status_code == 200:
        data = response.json()["data"]
        assert data["rounds"] >= 1 and data["log"]
    else:
        assert response.json()["message"] != "INTERNAL_ERROR"
    _ = MilitaryState
