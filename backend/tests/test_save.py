"""模块 N4 验收：多槽位存档的 Base64 + Gzip 导出与整事务导入。"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json

from sqlalchemy import func, select

from app.core import balance as B
from app.models import (
    Achievement,
    ColonyState,
    FacilityState,
    MilitaryState,
    SaveSlot,
    VehicleUnit,
)
from app.models.military import VehicleStatus

SAVE_URL = "/api/v1/save"
STATE_URL = "/api/v1/colony/state"


async def _bootstrap(client, slot: int = 1) -> dict:
    return (await client.get(STATE_URL, params={"slot": slot})).json()["data"]


def _scrap(state: dict) -> float:
    return float(state["resources"]["scrap"])


async def _export(client, slot: int = 1) -> dict:
    resp = await client.get(f"{SAVE_URL}/export", params={"slot": slot})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _unpack(payload: str) -> dict:
    return json.loads(gzip.decompress(base64.b64decode(payload)).decode("utf-8"))


def _repack(envelope: dict, *, checksum: str | None = None) -> str:
    if checksum is not None:
        envelope["checksum"] = checksum
    raw = json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.b64encode(gzip.compress(raw, 6)).decode("ascii")


async def test_slot_listing(client) -> None:
    await _bootstrap(client)
    data = (await client.get(f"{SAVE_URL}/slots")).json()["data"]
    assert data["max_slots"] == 3
    by_slot = {item["slot"]: item for item in data["slots"]}
    assert by_slot[1]["exists"] is True
    assert by_slot[1]["save_version"] == data["save_version"]
    assert by_slot[1]["playtime_hours"] == 0.0
    assert by_slot[2]["exists"] is False
    assert by_slot[2]["name"] is None


async def test_export_payload_structure(client) -> None:
    await _bootstrap(client)
    body = await _export(client)
    assert body["code"] == 200
    assert body["slot"] == 1
    assert len(body["checksum"]) == 64
    envelope = _unpack(body["base64_payload"])
    assert envelope["save_version"] == body["save_version"]
    assert envelope["checksum"] == body["checksum"]
    assert set(envelope["tables"]) >= {"save_slot", "colony_state", "facility_state", "achievements"}
    assert len(envelope["tables"]["save_slot"]) == 1
    assert envelope["tables"]["save_slot"][0]["slot_id"] == 1


async def test_export_empty_slot_is_404(client) -> None:
    resp = await client.get(f"{SAVE_URL}/export", params={"slot": 3})
    assert resp.status_code == 404
    assert resp.json()["message"] == "SAVE_SLOT_EMPTY"


async def test_roundtrip_import_copies_state(client, session) -> None:
    await _bootstrap(client)
    for _ in range(3):  # 攒点废铁，确保拷贝的是"有内容"的存档
        await client.post("/api/v1/colony/scavenge", params={"slot": 1})
    source = (await client.get(STATE_URL, params={"slot": 1})).json()["data"]
    assert _scrap(source) > 0

    body = await _export(client, 1)
    resp = await client.post(
        f"{SAVE_URL}/import", json={"slot": 2, "base64_payload": body["base64_payload"], "slot_name": "拷贝档"}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["slot"] == 2
    assert data["slot_name"] == "拷贝档"
    assert data["total_rows"] == body["total_rows"]

    copied = (await client.get(STATE_URL, params={"slot": 2})).json()["data"]
    assert _scrap(copied) == _scrap(source)
    assert copied["population"]["total"] == source["population"]["total"]

    # 导入的是目标槽位：源槽位不受影响，且行不跨槽位混入
    await session.rollback()
    rows = (
        await session.execute(
            select(ColonyState.slot_id, func.count()).group_by(ColonyState.slot_id)
        )
    ).all()
    assert sorted(rows) == [(1, 1), (2, 1)]


async def test_import_is_idempotent_replace(client, session) -> None:
    await _bootstrap(client)
    await client.get("/api/v1/stats/achievements")  # 先让徽章行落库，验证它们确实随存档搬运
    body = await _export(client, 1)
    payload = body["base64_payload"]
    for _ in range(2):
        resp = await client.post(f"{SAVE_URL}/import", json={"slot": 2, "base64_payload": payload})
        assert resp.status_code == 200, resp.text

    await session.rollback()
    badges = await session.execute(
        select(func.count()).select_from(Achievement).where(Achievement.slot_id == 2)
    )
    facilities = await session.execute(
        select(func.count()).select_from(FacilityState).where(FacilityState.slot_id == 2)
    )
    saves = await session.execute(select(func.count()).select_from(SaveSlot).where(SaveSlot.slot_id == 2))
    assert badges.scalar_one() == len(B.ACHIEVEMENTS)
    assert facilities.scalar_one() > 0
    assert saves.scalar_one() == 1


async def test_checksum_mismatch_rejected(client) -> None:
    await _bootstrap(client)
    envelope = _unpack((await _export(client))["base64_payload"])
    tampered = _repack(envelope, checksum="0" * 64)
    resp = await client.post(f"{SAVE_URL}/import", json={"slot": 2, "base64_payload": tampered})
    assert resp.status_code == 400
    assert resp.json()["message"] == "CHECKSUM_MISMATCH"


async def test_tampered_content_rejected(client) -> None:
    await _bootstrap(client)
    envelope = _unpack((await _export(client))["base64_payload"])
    # 改内容但保留原校验和：必须被识破
    envelope["tables"]["save_slot"][0]["playtime_seconds"] = 999_999
    resp = await client.post(
        f"{SAVE_URL}/import", json={"slot": 2, "base64_payload": _repack(envelope)}
    )
    assert resp.status_code == 400
    assert resp.json()["message"] == "CHECKSUM_MISMATCH"


async def test_invalid_payload_rejected(client) -> None:
    for bad in ("not-base64!!", base64.b64encode(b"plain text").decode("ascii")):
        resp = await client.post(f"{SAVE_URL}/import", json={"slot": 2, "base64_payload": bad})
        assert resp.status_code == 400
        assert resp.json()["message"] == "INVALID_SAVE_PAYLOAD"


async def test_future_version_rejected(client) -> None:
    await _bootstrap(client)
    envelope = _unpack((await _export(client))["base64_payload"])
    envelope["save_version"] = 99
    resp = await client.post(
        f"{SAVE_URL}/import", json={"slot": 2, "base64_payload": _repack(envelope)}
    )
    assert resp.status_code == 400
    assert resp.json()["message"] == "UNSUPPORTED_SAVE_VERSION"


async def test_unknown_table_rejected(client) -> None:
    await _bootstrap(client)
    envelope = _unpack((await _export(client))["base64_payload"])
    envelope["tables"]["mystery_table"] = []
    # 空行的未知表会先被校验和拦下，这里同步更新校验和以检验"未知表一律拒绝"
    canonical = json.dumps(
        envelope["tables"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    envelope["checksum"] = hashlib.sha256(canonical).hexdigest()
    resp = await client.post(
        f"{SAVE_URL}/import", json={"slot": 2, "base64_payload": _repack(envelope)}
    )
    assert resp.status_code == 400
    assert resp.json()["message"] == "INVALID_SAVE_PAYLOAD"


async def test_cross_slot_import_reassigns_autoincrement_ids(client, session) -> None:
    """载具主键是全局自增：跨槽位导入必须重分配 ID，并回写军事状态里的引用。"""
    await _bootstrap(client)
    await session.rollback()
    spec = B.VEHICLE_TYPES["light_car"]
    units: list[VehicleUnit] = []
    for _ in range(2):
        unit = VehicleUnit(
            slot_id=1,
            planet_id=0,
            unit_type="light_car",
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
        units.append(unit)
    await session.flush()
    source_ids = [int(unit.unit_id) for unit in units]
    military = await session.get(MilitaryState, (1, 0))
    military.active_expeditions = [
        {"expedition_id": "exp_1", "target_id": "t1", "unit_ids": source_ids, "ends_at": 1_700_003_600}
    ]
    military.hospital_queue = [{"unit_id": source_ids[0], "cats": [{"cat_id": 1}], "ends_at": 1_700_000_600}]
    await session.commit()

    body = await _export(client, 1)
    resp = await client.post(f"{SAVE_URL}/import", json={"slot": 3, "base64_payload": body["base64_payload"]})
    assert resp.status_code == 200, resp.text

    await session.rollback()
    copied = (
        await session.execute(
            select(VehicleUnit.unit_id).where(VehicleUnit.slot_id == 3).order_by(VehicleUnit.unit_id)
        )
    ).scalars().all()
    assert len(copied) == 2
    assert not set(copied) & set(source_ids)  # 与源槽位的载具不撞主键

    state = await session.get(MilitaryState, (3, 0), populate_existing=True)
    assert state.active_expeditions[0]["unit_ids"] == list(copied)
    assert state.hospital_queue[0]["unit_id"] == copied[0]
