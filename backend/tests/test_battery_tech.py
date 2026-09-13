"""科技 `battery_kwh_max` 接入蓄电池电容池。"""

from __future__ import annotations

from sqlalchemy import select

from app.core import balance as B
from app.models import TechRecord
from app.models.tech import TechStatus

STATE_URL = "/api/v1/colony/state"


async def _payload(client) -> dict:
    return (await client.get(STATE_URL, params={"slot": 1})).json()["data"]


async def test_battery_capacity_starts_at_base(client) -> None:
    await _payload(client)
    data = await _payload(client)
    assert data["power"]["battery_kwh_max"] == B.BATTERY_KWH_MAX


async def test_unlocked_battery_tech_expands_capacity(client, session) -> None:
    await _payload(client)
    await session.rollback()
    row = (
        await session.execute(
            select(TechRecord).where(
                TechRecord.slot_id == 1,
                TechRecord.planet_id == 0,
                TechRecord.tech_id == "tech_battery_matrix",
            )
        )
    ).scalars().one()
    row.status = TechStatus.UNLOCKED
    await session.commit()

    data = await _payload(client)
    # 母星【高能蓄电池组】自带 battery_kwh_max = 200 ⇒ 电容池 200 + 200 = 400
    assert data["power"]["battery_kwh_max"] == B.BATTERY_KWH_MAX + 200

    # 反复读档不会累加（每次都是"基础 + 科技"重算）
    again = await _payload(client)
    assert again["power"]["battery_kwh_max"] == data["power"]["battery_kwh_max"]
