"""行星生态塑形师「机器磨损 −5%/只」的落地验收（《数值平衡表》§15.1）。

口径：磨损降低取**载具维修时长**语义——每只塑形师 −5%，最短不低于基准的 30%。
"""

from __future__ import annotations

import time

from app.core import balance as B
from app.models import ColonyState, LaborBucket, VehicleUnit
from app.models.military import VehicleStatus


def test_repair_factor_table() -> None:
    assert B.terraformer_repair_factor(0) == 1.0
    assert B.terraformer_repair_factor(2) == 0.9
    assert B.terraformer_repair_factor(14) == B.TERRAFORMER_WEAR_FLOOR
    assert B.terraformer_repair_factor(-3) == 1.0


async def test_repair_is_faster_with_terraformer(client, session) -> None:
    await client.get("/api/v1/colony/state", params={"slot": 1})
    await session.rollback()
    spec = B.VEHICLE_TYPES["armored_car"]
    units: list[VehicleUnit] = []
    for _ in range(2):
        unit = VehicleUnit(
            slot_id=1, planet_id=0, unit_type="armored_car", nickname=None, modules=[],
            shield=0.0, armor=1.0, armor_max=float(spec["armor"]), hull=1.0,
            status=VehicleStatus.REPAIR, crew_cats=0, acquired_at=int(time.time()),
        )
        session.add(unit)
        units.append(unit)
    colony = await session.get(ColonyState, (1, 0))
    colony.scrap, colony.alloys, colony.chips = 999.0, 999.0, 999.0
    await session.commit()
    first_id, second_id = (int(unit.unit_id) for unit in units)

    plain = await client.post(
        "/api/v1/vehicle/repair", json={"slot": 1, "planet_id": 0, "unit_id": first_id}
    )
    assert plain.status_code == 200, plain.text
    assert plain.json()["data"]["wear_factor"] == 1.0
    base_seconds = plain.json()["data"]["seconds"]

    await session.rollback()
    bucket = await session.get(LaborBucket, (1, 0, "terraformer"))
    bucket.cat_count = 4
    await session.commit()
    faster = await client.post(
        "/api/v1/vehicle/repair", json={"slot": 1, "planet_id": 0, "unit_id": second_id}
    )
    assert faster.status_code == 200, faster.text
    data = faster.json()["data"]
    assert data["wear_factor"] == 0.8
    assert abs(data["seconds"] / base_seconds - 0.8) <= 1e-6
