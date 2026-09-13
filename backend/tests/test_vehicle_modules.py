"""§9.10 车载模块验收：科技门槛、槽位上限、材料消耗与拆卸返还。"""

from __future__ import annotations

from sqlalchemy import select

from app.core import balance as B
from app.models import ColonyState, TechRecord, VehicleUnit
from app.models.military import VehicleStatus
from app.models.tech import TechStatus

MODIFY_URL = "/api/v1/vehicle/modify"


async def _boot(client) -> None:
    await client.get("/api/v1/colony/state", params={"slot": 1})


async def _vehicle(session, unit_type: str = "armored_car") -> int:
    await session.rollback()
    spec = B.VEHICLE_TYPES[unit_type]
    unit = VehicleUnit(
        slot_id=1,
        planet_id=0,
        unit_type=unit_type,
        nickname=None,
        modules=[],
        shield=float(spec["shield"]),
        armor=float(spec["armor"]),
        armor_max=float(spec["armor"]),
        hull=float(spec["hull"]),
        status=VehicleStatus.IDLE,
        crew_cats=int(spec["crew"]),
        acquired_at=1_700_000_000,
    )
    session.add(unit)
    colony = await session.get(ColonyState, (1, 0))
    colony.alloys = 200.0
    colony.chips = 50.0
    await session.commit()
    return int(unit.unit_id)


async def _unlock(session, tech_id: str) -> None:
    await session.rollback()
    row = await session.get(TechRecord, (1, 0, tech_id), populate_existing=True)
    row.status = TechStatus.UNLOCKED
    await session.commit()


async def test_equip_requires_tech_then_pays_cost(client, session) -> None:
    await _boot(client)
    unit_id = await _vehicle(session)

    locked = await client.post(
        MODIFY_URL, json={"slot": 1, "unit_id": unit_id, "action": "EQUIP", "module_id": "armor_plate"}
    )
    assert locked.status_code == 400
    assert locked.json()["message"] == "TECH_LOCKED"

    await _unlock(session, B.VEHICLE_MODULES["armor_plate"]["unlock_tech"])
    ok = await client.post(
        MODIFY_URL, json={"slot": 1, "unit_id": unit_id, "action": "EQUIP", "module_id": "armor_plate"}
    )
    assert ok.status_code == 200, ok.text
    data = ok.json()["data"]
    assert data["modules"] == ["armor_plate"]
    assert data["cost_paid"] == {"alloys": 30.0}
    assert data["slots_used"] == 1 and data["slots_total"] == 2

    await session.rollback()
    colony = await session.get(ColonyState, (1, 0), populate_existing=True)
    assert colony.alloys == 170.0  # 200 − 30
    row = await session.get(VehicleUnit, unit_id, populate_existing=True)
    assert row.modules == ["armor_plate"]


async def test_slots_are_enforced(client, session) -> None:
    await _boot(client)
    unit_id = await _vehicle(session, "light_car")  # 只有 1 个槽
    for module_id in ("armor_plate", "laser_mk2"):
        await _unlock(session, B.VEHICLE_MODULES[module_id]["unlock_tech"])
    first = await client.post(
        MODIFY_URL, json={"slot": 1, "unit_id": unit_id, "action": "EQUIP", "module_id": "armor_plate"}
    )
    assert first.status_code == 200
    second = await client.post(
        MODIFY_URL, json={"slot": 1, "unit_id": unit_id, "action": "EQUIP", "module_id": "laser_mk2"}
    )
    assert second.status_code == 400
    assert second.json()["message"] == "VEHICLE_MODULE_SLOTS_FULL"


async def test_unequip_refunds_half(client, session) -> None:
    await _boot(client)
    unit_id = await _vehicle(session)
    await _unlock(session, B.VEHICLE_MODULES["drill_mk2"]["unlock_tech"])
    await client.post(
        MODIFY_URL, json={"slot": 1, "unit_id": unit_id, "action": "EQUIP", "module_id": "drill_mk2"}
    )
    out = await client.post(
        MODIFY_URL, json={"slot": 1, "unit_id": unit_id, "action": "UNEQUIP", "module_id": "drill_mk2"}
    )
    assert out.status_code == 200, out.text
    data = out.json()["data"]
    assert data["modules"] == []
    # 合金 25 → 返还 12.5；芯片 8 → 返还 4
    assert data["refund"] == {"alloys": 12.5, "chips": 4.0}

    again = await client.post(
        MODIFY_URL, json={"slot": 1, "unit_id": unit_id, "action": "UNEQUIP", "module_id": "drill_mk2"}
    )
    assert again.status_code == 400
    assert again.json()["message"] == "MODULE_NOT_EQUIPPED"


async def test_insufficient_materials_refused(client, session) -> None:
    await _boot(client)
    unit_id = await _vehicle(session)
    await _unlock(session, B.VEHICLE_MODULES["laser_mk2"]["unlock_tech"])
    await session.rollback()
    colony = await session.get(ColonyState, (1, 0))
    colony.chips = 0.0
    await session.commit()
    resp = await client.post(
        MODIFY_URL, json={"slot": 1, "unit_id": unit_id, "action": "EQUIP", "module_id": "laser_mk2"}
    )
    assert resp.status_code == 400
    assert resp.json()["message"] == "INSUFFICIENT_RESOURCE"
