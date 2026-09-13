"""多槽位存档服务（模块 N4）：Base64 + Gzip 打包导出与单事务整体替换导入。

协议见《数据库设计定稿》§8.5：

* 导出：把该 `slot_id` 下所有表的行序列化为 JSON → Gzip → Base64，并带上 `save_version` 与 `checksum`；
* 导入：先校验 `checksum` 与 `save_version` → 走版本迁移函数 → 在**单个事务内整体替换**；
* 校验失败的存档一律拒绝，不做"尽力恢复"。
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import enum
import gzip
import hashlib
import hmac
import json
import logging
from typing import Any

import sqlalchemy as sa
from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import balance as B
from app.core.errors import BadRequest, NotFound
from app.models import (
    Achievement,
    BossState,
    CareerStats,
    ColonyState,
    DarknetState,
    FacilityState,
    ForumPost,
    GardenState,
    LaborBucket,
    MilitaryState,
    MinigameState,
    PlanetState,
    SaveSlot,
    TechRecord,
    VehicleUnit,
)

logger = logging.getLogger("dawn_meow.save")

#: 参与导出/导入的表（顺序即建表顺序；`event_templates` 是全局模板，不随存档走）
SAVE_MODELS: tuple[type, ...] = (
    SaveSlot,
    ColonyState,
    LaborBucket,
    FacilityState,
    PlanetState,
    CareerStats,
    Achievement,
    MinigameState,
    TechRecord,
    MilitaryState,
    VehicleUnit,
    BossState,
    DarknetState,
    ForumPost,
    GardenState,
)

#: 解压后的存档上限（8 MB）：单机自用，纯粹防手抖粘贴异常大串
MAX_DECODED_BYTES = 8 * 1024 * 1024

#: 全局自增主键（不属于 `(slot_id, ...)` 复合键）：导入时必须由数据库重新分配，
#: 否则把槽位 1 的载具拷进槽位 2 会撞 `vehicle_units.PRIMARY`（真机 MySQL 实测踩到）。
AUTOINCREMENT_PKS: dict[str, str] = {"vehicle_units": "unit_id", "forum_posts": "id"}

#: 插入顺序：先普通表，再载具（拿到新 ID 映射），最后军事状态（把映射回写进 JSON 引用）
INSERT_ORDER: tuple[type, ...] = tuple(
    model for model in SAVE_MODELS if model not in (VehicleUnit, MilitaryState)
) + (VehicleUnit, MilitaryState)

REFERENCE_KEYS = ("unit_id", "unit_ids")


def _remap_references(value: Any, remap: dict[int, int]) -> Any:
    """把 JSON 里的 `unit_id` / `unit_ids` 引用改写到载具重分配后的新 ID。"""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key in REFERENCE_KEYS and remap:
                if isinstance(item, int) and item in remap:
                    item = remap[item]
                elif isinstance(item, list):
                    item = [remap.get(entry, entry) if isinstance(entry, int) else entry for entry in item]
            result[key] = _remap_references(item, remap)
        return result
    if isinstance(value, list):
        return [_remap_references(item, remap) for item in value]
    return value


def _canonical(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _checksum(tables: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(tables)).hexdigest()


def _encode_value(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, dt.datetime):
        return value.isoformat()
    return value


def _decode_value(column: sa.Column, value: Any) -> Any:
    if value is None:
        return None
    column_type = column.type
    if isinstance(column_type, sa.Enum) and isinstance(value, str):
        enum_class = getattr(column_type, "enum_class", None)
        return enum_class(value) if enum_class else value
    if isinstance(column_type, sa.DateTime) and isinstance(value, str):
        return dt.datetime.fromisoformat(value)
    return value


def _dump_rows(model: type, rows: list[Any]) -> list[dict[str, Any]]:
    keys = [column.key for column in model.__table__.columns]
    return [{key: _encode_value(getattr(row, key)) for key in keys} for row in rows]


async def slots_view(session: AsyncSession) -> dict[str, Any]:
    """三个槽位的元信息（含"空槽位"占位，便于前端直接渲染 1/2/3）。"""
    slots: list[dict[str, Any]] = []
    for slot_id in B.SLOT_IDS:
        save = await session.get(SaveSlot, slot_id)
        if save is None:
            slots.append({"slot": slot_id, "exists": False, "name": None, "save_version": None})
            continue
        colony = await session.get(ColonyState, (slot_id, save.active_planet_id))
        slots.append(
            {
                "slot": slot_id,
                "exists": True,
                "name": save.slot_name or f"存档 {slot_id}",
                "save_version": save.save_version,
                "active_planet_id": save.active_planet_id,
                "playtime_seconds": int(save.playtime_seconds),
                "playtime_hours": round(save.playtime_seconds / 3600.0, 2),
                "cats_total": int(colony.total_cats) if colony else 0,
                "unity": round(float(save.unity), 2),
                "doctrines": len(save.doctrines or {}),
                "updated_at": save.updated_at.isoformat() if save.updated_at else None,
                "checksum": save.checksum,
            }
        )
    return {"slots": slots, "save_version": B.SAVE_VERSION, "max_slots": len(B.SLOT_IDS)}


async def export_slot(session: AsyncSession, slot_id: int = B.DEFAULT_SLOT_ID) -> dict[str, Any]:
    """把该槽位所有行打包成 `base64(gzip(json))`，并给出 SHA-256 校验和。"""
    if await session.get(SaveSlot, slot_id) is None:
        raise NotFound("SAVE_SLOT_EMPTY", f"槽位 {slot_id} 还没有存档")
    tables: dict[str, list[dict[str, Any]]] = {}
    for model in SAVE_MODELS:
        rows = (
            await session.execute(
                select(model).where(model.slot_id == slot_id).order_by(*model.__table__.primary_key.columns)
            )
        ).scalars().all()
        tables[model.__tablename__] = _dump_rows(model, list(rows))

    checksum = _checksum(tables)
    envelope = {
        "save_version": B.SAVE_VERSION,
        "slot_id": slot_id,
        "exported_at": int(dt.datetime.now(dt.UTC).timestamp()),
        "checksum": checksum,
        "tables": tables,
    }
    raw = _canonical(envelope)
    packed = base64.b64encode(gzip.compress(raw, 6)).decode("ascii")
    logger.info("导出存档：slot=%s 表 %s 张 / %.1f KB", slot_id, len(tables), len(raw) / 1024)
    return {
        "slot": slot_id,
        "base64_payload": packed,
        "checksum": checksum,
        "save_version": B.SAVE_VERSION,
        "raw_bytes": len(raw),
        "total_rows": sum(len(rows) for rows in tables.values()),
    }


def _migrate(envelope: dict[str, Any]) -> dict[str, Any]:
    """版本迁移钩子：目前只有 v1，占位保证旧档导入路径存在。"""
    version = int(envelope.get("save_version") or 0)
    if version > B.SAVE_VERSION:
        raise BadRequest(
            "UNSUPPORTED_SAVE_VERSION",
            f"存档版本 {version} 高于当前支持的 {B.SAVE_VERSION}，请升级后端再导入",
        )
    # version < B.SAVE_VERSION 时在此逐版升级字段；v1 是初版，无需处理
    return envelope


def _unpack(base64_payload: str) -> dict[str, Any]:
    try:
        raw = gzip.decompress(base64.b64decode(base64_payload, validate=True))
    except (binascii.Error, ValueError, gzip.BadGzipFile, EOFError) as exc:
        raise BadRequest("INVALID_SAVE_PAYLOAD", f"存档不是合法的 Base64+Gzip 文本：{exc}") from exc
    if len(raw) > MAX_DECODED_BYTES:
        raise BadRequest("INVALID_SAVE_PAYLOAD", f"存档解压后 {len(raw) // 1024} KB，超过上限")
    try:
        envelope = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BadRequest("INVALID_SAVE_PAYLOAD", f"存档 JSON 解析失败：{exc}") from exc
    if not isinstance(envelope, dict) or not isinstance(envelope.get("tables"), dict):
        raise BadRequest("INVALID_SAVE_PAYLOAD", "存档缺少 tables 结构")
    return envelope


async def import_slot(
    session: AsyncSession, slot_id: int, base64_payload: str, *, now: int | None = None
) -> dict[str, Any]:
    """校验 → 迁移 → 单事务整体替换该槽位（导入失败不留半截数据）。"""
    if slot_id not in B.SLOT_IDS:
        raise BadRequest("BAD_REQUEST", f"slot 必须为 {B.SLOT_IDS} 之一，收到 {slot_id}")
    envelope = _migrate(_unpack(base64_payload))
    tables = envelope["tables"]
    expected = envelope.get("checksum")
    actual = _checksum(tables)
    if not isinstance(expected, str) or not hmac.compare_digest(expected, actual):
        raise BadRequest(
            "CHECKSUM_MISMATCH",
            f"校验和不匹配（存档 {str(expected)[:12]}… / 重算 {actual[:12]}…），已拒绝导入",
        )

    model_by_table = {model.__tablename__: model for model in SAVE_MODELS}
    restored: dict[str, int] = {}
    parsed: dict[str, list[dict[str, Any]]] = {}
    for table_name, rows in tables.items():
        model = model_by_table.get(table_name)
        if model is None:  # 未知表（例如未来版本新增）按定稿"一律拒绝"处理
            raise BadRequest("INVALID_SAVE_PAYLOAD", f"存档含未知表 {table_name}")
        keys = {column.key for column in model.__table__.columns}
        payload_rows: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                raise BadRequest("INVALID_SAVE_PAYLOAD", f"{table_name} 的行不是对象")
            item: dict[str, Any] = {}
            for key, value in row.items():
                if key not in keys:
                    continue
                item[key] = _decode_value(model.__table__.columns[key], value)
            item["slot_id"] = slot_id  # 跨槽位导入时重定向到目标槽位
            payload_rows.append(item)
        parsed[table_name] = payload_rows

    remap: dict[int, int] = {}
    try:
        for model in SAVE_MODELS:
            await session.execute(delete(model).where(model.slot_id == slot_id))
        for model in INSERT_ORDER:
            table_name = model.__tablename__
            rows = parsed.get(table_name, [])
            auto_pk = AUTOINCREMENT_PKS.get(table_name)
            if auto_pk and model is not VehicleUnit:
                # 论坛帖子的自增主键无外部引用，直接交回数据库分配
                rows = [{key: value for key, value in row.items() if key != auto_pk} for row in rows]
                if rows:
                    await session.execute(insert(model), rows)
            elif model is VehicleUnit:
                for row in rows:
                    item = {key: value for key, value in row.items() if key != "unit_id"}
                    result = await session.execute(insert(VehicleUnit).values(**item))
                    old_id = row.get("unit_id")
                    if isinstance(old_id, int):
                        remap[old_id] = int(result.inserted_primary_key[0])
            else:
                if model is MilitaryState:
                    rows = [_remap_references(row, remap) for row in rows]
                if rows:
                    await session.execute(insert(model), rows)
            restored[table_name] = len(rows)
        save = await session.get(SaveSlot, slot_id, populate_existing=True)
        if save is None:
            raise BadRequest("INVALID_SAVE_PAYLOAD", "存档缺少 save_slot 行")
        if save.active_planet_id not in B.PLANETS:
            save.active_planet_id = B.HOME_PLANET_ID
        save.save_version = B.SAVE_VERSION
        save.checksum = actual
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    logger.info("导入存档：slot=%s 共 %s 行", slot_id, sum(restored.values()))
    stamped = now if now is not None else int(dt.datetime.now(dt.UTC).timestamp())
    return {
        "slot": slot_id,
        "save_version": B.SAVE_VERSION,
        "checksum": actual,
        "restored": restored,
        "total_rows": sum(restored.values()),
        "imported_at": stamped,
    }
