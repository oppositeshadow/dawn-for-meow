"""军备与远征服务（模块 G）：组装 / 维修 / 拆解 / 异步远征 / 急救舱 / 满警戒度战车截杀。

设计要点：

* 载具逐辆建行（`vehicle_units`），三层血条与装甲上限**跨战斗持久化**（破甲削蚀不回满）；
* 机位上限 = `military_state.hangar_capacity` × 车型 `hangar_slots`（校验在组装时做）；
* 乘员猫：组装时从空闲待命池扣、占用 `labor_buckets.crew` 工位（每辆 2~3 只，按车型）；
* 远征是**绝对时间戳**倒计时（`ends_at`），离线可完成，返航后由玩家点【收取】入库；
* 满警戒度【战车截杀】由离线结算产出 `P2_PENDING` 事件后，在本层用 `combat_engine` 真打一场。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import balance as B
from app.core.combat_engine import (
    apply_module_effects,
    combat_power,
    morale_multiplier,
    resolve_skirmish,
    unit_from_spec,
)
from app.core.errors import BadRequest, Conflict, InsufficientResource, NotFound
from app.models import BossState, ColonyState, LaborBucket, MilitaryState, VehicleUnit
from app.models.military import VehicleStatus
from app.services.game_init_service import now_timestamp

logger = logging.getLogger("dawn_meow.combat")


async def _fleet_armor_bonus(session, slot_id: int, planet_id: int) -> float:
    """已解锁科技给全军装甲的加成（`fleet_armor` 母星写法 + `armor_bonus` 特化卡写法）。"""
    from app.services import tech_service

    totals = await tech_service.unlocked_effects(session, slot_id, planet_id)
    return round(float(totals.get("fleet_armor", 0.0)) + float(totals.get("armor_bonus", 0.0)), 4)

RESOURCE_PRECISION = 2


async def _military(session: AsyncSession, slot_id: int, planet_id: int) -> MilitaryState:
    row = await session.get(MilitaryState, (slot_id, planet_id))
    if row is None:
        raise NotFound("COLONY_STATE_NOT_FOUND", f"槽位 {slot_id} 星球 {planet_id} 没有军备状态")
    return row


async def _vehicles(session: AsyncSession, slot_id: int, planet_id: int) -> list[VehicleUnit]:
    rows = (
        await session.execute(
            select(VehicleUnit)
            .where(VehicleUnit.slot_id == slot_id, VehicleUnit.planet_id == planet_id)
            .order_by(VehicleUnit.unit_id)
        )
    ).scalars().all()
    return [row for row in rows if row.status != VehicleStatus.SCRAPPED]


def _used_hangar_slots(rows: list[VehicleUnit]) -> int:
    return sum(int(B.VEHICLE_TYPES.get(row.unit_type, {}).get("hangar_slots", 1)) for row in rows)


def vehicle_view(row: VehicleUnit) -> dict[str, Any]:
    spec = B.VEHICLE_TYPES.get(row.unit_type, {})
    return {
        "unit_id": row.unit_id,
        "unit_type": row.unit_type,
        "unit_name": spec.get("name", row.unit_type),
        "nickname": row.nickname,
        "modules": list(row.modules or []),
        "shield": round(float(row.shield), 1),
        "armor": round(float(row.armor), 1),
        "armor_max": round(float(row.armor_max), 1),
        "hull": round(float(row.hull), 1),
        "crew_cats": int(row.crew_cats),
        "status": row.status.value if hasattr(row.status, "value") else str(row.status),
        "repair_ends_at": row.repair_ends_at,
        "expedition_id": row.expedition_id,
        "combat_power": combat_power(
            {
                "shield": row.shield,
                "armor": row.armor,
                "hull": row.hull,
                "dps": spec.get("dps", 0.0),
            }
        ),
    }


# ----------------------------------------------------------------------
# 战备机库
# ----------------------------------------------------------------------
async def list_hangar(
    session: AsyncSession, *, slot_id: int = B.DEFAULT_SLOT_ID, planet_id: int = B.HOME_PLANET_ID
) -> dict[str, Any]:
    military = await _military(session, slot_id, planet_id)
    rows = await _vehicles(session, slot_id, planet_id)
    boss = await session.get(BossState, slot_id)
    policy = dict(military.security_policy or {})
    return {
        "hangar_capacity": int(military.hangar_capacity),
        "hangar_used": _used_hangar_slots(rows),
        "laser_turrets": int(military.laser_turrets),
        "cruise_missiles": int(military.cruise_missiles),
        "decoy_count": int(military.decoy_count),
        "tactical_buff": policy.get("tactical"),
        "convoy_ends_at": int(boss.convoy_ends_at) if boss and boss.convoy_ends_at else None,
        "factory_frozen_until": int(boss.factory_frozen_until) if boss and boss.factory_frozen_until else None,
        "threat_level": int(boss.threat_level) if boss else 1,
        "rage": float(boss.rage) if boss else 0.0,
        "fleet_strength": float(boss.fleet_strength) if boss else 0.0,
        "raid_ends_at": int(boss.raid_ends_at) if boss and boss.raid_ends_at else None,
        "final_stage_cleared": int((boss.bombardment_state or {}).get("final_stage_cleared") or 0) if boss else 0,
        "completed": bool((boss.bombardment_state or {}).get("override_key_used_at")) if boss else False,
        "epitaph": (boss.bombardment_state or {}).get("epitaph") if boss else None,
        "hospital_queue": list(military.hospital_queue or []),
        "active_expeditions": list(military.active_expeditions or []),
        "vehicles": [vehicle_view(row) for row in rows],
        "vehicle_types": {
            key: {
                "name": spec["name"],
                "cost": spec["cost"],
                "crew": spec["crew"],
                "hangar_slots": spec["hangar_slots"],
                "shield": spec["shield"],
                "armor": spec["armor"],
                "hull": spec["hull"],
                "dps": spec["dps"],
                "module_slots": B.vehicle_module_slots(key),
            }
            for key, spec in B.VEHICLE_TYPES.items()
        },
        "vehicle_modules": {
            key: {
                "name": spec["name"],
                "slots": spec["slots"],
                "cost": spec["cost"],
                "unlock_tech": spec["unlock_tech"],
                "effects": spec["effects"],
            }
            for key, spec in B.VEHICLE_MODULES.items()
        },
        "expedition_targets": {
            key: {
                "name": spec["name"],
                "duration_seconds": spec["duration_seconds"],
                "suspicion_cost": spec["suspicion_cost"],
                "drops": spec["drops"],
                "requires_unit_types": list(spec["requires_unit_types"]),
            }
            for key, spec in B.EXPEDITION_TARGETS.items()
        },
    }


async def assemble_vehicle(
    session: AsyncSession,
    *,
    unit_type: str,
    nickname: str | None = None,
    slot_id: int = B.DEFAULT_SLOT_ID,
    planet_id: int = B.HOME_PLANET_ID,
) -> dict[str, Any]:
    """组装新车：校机位 → 校资源 → 校空闲猫口（乘员猫占 crew 工位）。"""
    spec = B.VEHICLE_TYPES.get(unit_type)
    if spec is None:
        raise BadRequest("BAD_REQUEST", f"未知车型 unit_type={unit_type}")

    military = await _military(session, slot_id, planet_id)
    colony = await session.get(ColonyState, (slot_id, planet_id))
    if colony is None:
        raise NotFound("COLONY_STATE_NOT_FOUND")

    rows = await _vehicles(session, slot_id, planet_id)
    if _used_hangar_slots(rows) + int(spec["hangar_slots"]) > int(military.hangar_capacity):
        raise BadRequest(
            "HANGAR_FULL",
            f"机库机位不足：已用 {_used_hangar_slots(rows)}/{int(military.hangar_capacity)}，"
            f"该车型需要 {int(spec['hangar_slots'])} 个机位",
        )

    missing = {
        resource: round(need - float(getattr(colony, resource)), RESOURCE_PRECISION)
        for resource, need in spec["cost"].items()
        if float(getattr(colony, resource)) < float(need)
    }
    if missing:
        raise InsufficientResource(
            detail="缺料：" + "、".join(f"{B.RESOURCE_LABELS.get(k, k)} 差 {v:g}" for k, v in missing.items())
        )

    crew_needed = int(spec["crew"])
    if colony.job_idle < crew_needed:
        raise BadRequest(
            "NO_IDLE_CAT",
            f"需要 {crew_needed} 只乘员猫，当前空闲待命只有 {colony.job_idle} 只",
        )

    for resource, need in spec["cost"].items():
        setattr(colony, resource, round(float(getattr(colony, resource)) - float(need), RESOURCE_PRECISION))
    colony.job_idle -= crew_needed
    crew_bucket = await session.get(LaborBucket, (slot_id, planet_id, "crew"))
    if crew_bucket is None:
        crew_bucket = LaborBucket(slot_id=slot_id, planet_id=planet_id, job_id="crew", cat_count=crew_needed)
        session.add(crew_bucket)
    else:
        crew_bucket.cat_count = int(crew_bucket.cat_count) + crew_needed

    unit = VehicleUnit(
        slot_id=slot_id,
        planet_id=planet_id,
        unit_type=unit_type,
        nickname=nickname,
        modules=[],
        shield=float(spec["shield"]),
        armor=float(spec["armor"]),
        armor_max=float(spec["armor"]),
        hull=float(spec["hull"]),
        status=VehicleStatus.IDLE,
        crew_cats=crew_needed,
        acquired_at=now_timestamp(),
    )
    session.add(unit)
    await session.commit()
    await session.refresh(unit)
    logger.info("组装载具：slot=%s type=%s unit_id=%s", slot_id, unit_type, unit.unit_id)
    return {"vehicle": vehicle_view(unit), "cost_paid": dict(spec["cost"]), "crew_locked": crew_needed}


async def equip_module(
    session: AsyncSession,
    unit_id: int,
    module_id: str,
    *,
    slot_id: int = 1,
    planet_id: int = 0,
) -> dict[str, Any]:
    """给一辆车装模块（《数值平衡表》§9.10）：校验科技门槛、槽位与材料。"""
    from app.models import TechRecord
    from app.models.tech import TechStatus

    spec = B.VEHICLE_MODULES.get(module_id)
    if spec is None:
        raise BadRequest("BAD_REQUEST", f"未知模块 module_id={module_id}")
    unit = await session.get(VehicleUnit, unit_id)
    if unit is None or unit.slot_id != slot_id or unit.status == VehicleStatus.SCRAPPED:
        raise NotFound("VEHICLE_NOT_FOUND", f"载具 {unit_id} 不存在")
    if unit.status != VehicleStatus.IDLE:
        raise Conflict("VEHICLE_BUSY", f"载具当前状态为 {unit.status.value}，无法改装")

    record = await session.get(TechRecord, (slot_id, planet_id, spec["unlock_tech"]))
    if record is None or record.status != TechStatus.UNLOCKED:
        name = record.tech_name if record else spec["unlock_tech"]
        raise BadRequest("TECH_LOCKED", f"【{spec['name']}】需要先解锁科技【{name}】")

    modules = list(unit.modules or [])
    if module_id in modules:
        raise BadRequest("MODULE_ALREADY_EQUIPPED", f"该车已经装了【{spec['name']}】")
    used = sum(int(B.VEHICLE_MODULES[item]["slots"]) for item in modules if item in B.VEHICLE_MODULES)
    if used + int(spec["slots"]) > B.vehicle_module_slots(unit.unit_type):
        raise BadRequest(
            "VEHICLE_MODULE_SLOTS_FULL",
            f"【{B.VEHICLE_TYPES[unit.unit_type]['name']}】模块槽只有 {B.vehicle_module_slots(unit.unit_type)} 个，已占用 {used}",
        )

    colony = await session.get(ColonyState, (slot_id, planet_id))
    if colony is None:
        raise NotFound("COLONY_STATE_NOT_FOUND", f"槽位 {slot_id} 星球 {planet_id} 无基地状态")
    for resource, amount in spec["cost"].items():
        if float(getattr(colony, resource)) < float(amount):
            raise InsufficientResource(
                detail=f"装配【{spec['name']}】需要 {amount:g} {B.RESOURCE_LABELS.get(resource, resource)}，"
                f"当前只有 {float(getattr(colony, resource)):g}"
            )
    for resource, amount in spec["cost"].items():
        setattr(colony, resource, round(float(getattr(colony, resource)) - float(amount), 2))
    modules.append(module_id)
    unit.modules = modules  # JSON 列必须整条替换
    await session.flush()
    logger.info("装配模块：slot=%s unit=%s module=%s", slot_id, unit_id, module_id)
    return {
        "unit_id": unit_id,
        "module_id": module_id,
        "modules": modules,
        "slots_used": used + int(spec["slots"]),
        "slots_total": B.vehicle_module_slots(unit.unit_type),
        "cost_paid": dict(spec["cost"]),
    }


async def unequip_module(
    session: AsyncSession,
    unit_id: int,
    module_id: str,
    *,
    slot_id: int = 1,
    planet_id: int = 0,
) -> dict[str, Any]:
    """拆下模块并返还 50% 材料（与退役拆解同口径，鼓励试配装）。"""
    unit = await session.get(VehicleUnit, unit_id)
    if unit is None or unit.slot_id != slot_id or unit.status == VehicleStatus.SCRAPPED:
        raise NotFound("VEHICLE_NOT_FOUND", f"载具 {unit_id} 不存在")
    modules = list(unit.modules or [])
    if module_id not in modules:
        raise BadRequest("MODULE_NOT_EQUIPPED", f"该车没有装 {module_id}")
    spec = B.VEHICLE_MODULES.get(module_id, {"name": module_id, "cost": {}})
    colony = await session.get(ColonyState, (slot_id, planet_id))
    refund: dict[str, float] = {}
    if colony is not None:
        for resource, amount in (spec.get("cost") or {}).items():
            back = round(float(amount) * B.VEHICLE_MODULE_REFUND_RATIO, 2)
            cap = float(getattr(colony, f"{resource}_max", back))
            setattr(colony, resource, round(min(cap, float(getattr(colony, resource)) + back), 2))
            refund[resource] = back
    modules.remove(module_id)
    unit.modules = modules
    await session.flush()
    logger.info("拆卸模块：slot=%s unit=%s module=%s 返还 %s", slot_id, unit_id, module_id, refund)
    return {"unit_id": unit_id, "module_id": module_id, "modules": modules, "refund": refund}


async def repair_vehicle(
    session: AsyncSession,
    unit_id: int,
    *,
    slot_id: int = B.DEFAULT_SLOT_ID,
    planet_id: int = B.HOME_PLANET_ID,
) -> dict[str, Any]:
    """维修：扣原造价的 50%，耗时 60 秒 × 车型系数。"""
    unit = await session.get(VehicleUnit, unit_id)
    if unit is None or unit.slot_id != slot_id:
        raise NotFound("VEHICLE_NOT_FOUND", f"载具 {unit_id} 不存在")
    if unit.status != VehicleStatus.REPAIR:
        raise Conflict("VEHICLE_NOT_DAMAGED", "该载具不在维修状态")

    spec = B.VEHICLE_TYPES[unit.unit_type]
    cost = {k: round(v * B.VEHICLE_REPAIR_COST_RATIO, RESOURCE_PRECISION) for k, v in spec["cost"].items()}
    colony = await session.get(ColonyState, (slot_id, planet_id))
    missing = {
        resource: need - float(getattr(colony, resource))
        for resource, need in cost.items()
        if float(getattr(colony, resource)) < need
    }
    if missing:
        raise InsufficientResource(
            detail="维修缺料：" + "、".join(f"{B.RESOURCE_LABELS.get(k, k)} 差 {v:g}" for k, v in missing.items())
        )
    for resource, need in cost.items():
        setattr(colony, resource, round(float(getattr(colony, resource)) - need, RESOURCE_PRECISION))

    seconds = B.VEHICLE_REPAIR_BASE_SECONDS * float(spec.get("repair_time_factor", 1.0))
    unit.repair_ends_at = now_timestamp() + int(seconds)
    await session.commit()
    return {"unit_id": unit_id, "cost_paid": cost, "repair_ends_at": unit.repair_ends_at, "seconds": seconds}


async def scrap_vehicle(
    session: AsyncSession,
    unit_id: int,
    *,
    slot_id: int = B.DEFAULT_SLOT_ID,
    planet_id: int = B.HOME_PLANET_ID,
) -> dict[str, Any]:
    """退役拆解：返还 50% 材料，乘员猫归队。"""
    unit = await session.get(VehicleUnit, unit_id)
    if unit is None or unit.slot_id != slot_id:
        raise NotFound("VEHICLE_NOT_FOUND", f"载具 {unit_id} 不存在")
    if unit.status == VehicleStatus.EXPEDITION:
        raise Conflict("VEHICLE_ON_EXPEDITION", "载具正在远征途中，不能拆解")

    spec = B.VEHICLE_TYPES[unit.unit_type]
    refund = {
        resource: round(float(need) * B.VEHICLE_SCRAP_REFUND_RATIO, RESOURCE_PRECISION)
        for resource, need in spec["cost"].items()
    }
    colony = await session.get(ColonyState, (slot_id, planet_id))
    for resource, amount in refund.items():
        cap = float(getattr(colony, f"{resource}_max"))
        setattr(colony, resource, round(min(cap, float(getattr(colony, resource)) + amount), RESOURCE_PRECISION))

    crew = int(unit.crew_cats)
    crew_bucket = await session.get(LaborBucket, (slot_id, planet_id, "crew"))
    if crew_bucket is not None:
        crew_bucket.cat_count = max(0, int(crew_bucket.cat_count) - crew)
    colony.job_idle += crew

    unit.status = VehicleStatus.SCRAPPED
    unit.crew_cats = 0
    await session.commit()
    return {"unit_id": unit_id, "refund": refund, "crew_released": crew}


# ----------------------------------------------------------------------
# 异步远征
# ----------------------------------------------------------------------
async def start_expedition(
    session: AsyncSession,
    *,
    target_id: str,
    unit_ids: list[int],
    slot_id: int = B.DEFAULT_SLOT_ID,
    planet_id: int = B.HOME_PLANET_ID,
) -> dict[str, Any]:
    """派遣远征：写绝对到期时间戳（离线照样推进）。"""
    target = B.EXPEDITION_TARGETS.get(target_id)
    if target is None:
        raise BadRequest("BAD_REQUEST", f"未知远征目标 target_id={target_id}")
    if not unit_ids:
        raise BadRequest("BAD_REQUEST", "至少要派一辆载具")

    military = await _military(session, slot_id, planet_id)
    vehicles: list[VehicleUnit] = []
    for unit_id in unit_ids:
        unit = await session.get(VehicleUnit, unit_id)
        if unit is None or unit.slot_id != slot_id or unit.status == VehicleStatus.SCRAPPED:
            raise NotFound("VEHICLE_NOT_FOUND", f"载具 {unit_id} 不存在")
        if unit.status != VehicleStatus.IDLE:
            raise Conflict("VEHICLE_BUSY", f"载具 {unit_id} 当前状态为 {unit.status.value}，无法派遣")
        vehicles.append(unit)

    required = list(target["requires_unit_types"])
    if required:
        types = {unit.unit_type for unit in vehicles}
        if not types & set(required):
            names = "、".join(B.VEHICLE_TYPES[t]["name"] for t in required)
            raise BadRequest("BAD_REQUEST", f"该目标需要编入：{names}")

    now = now_timestamp()
    expedition_id = f"e{now}{unit_ids[0]}"
    ends_at = now + int(target["duration_seconds"])
    for unit in vehicles:
        unit.status = VehicleStatus.EXPEDITION
        unit.expedition_id = expedition_id

    # 注意：JSON 列的 dict 必须整条复制后替换，就地改不会被 SQLAlchemy 侦测到（会漏写库）
    expeditions = [dict(item) for item in (military.active_expeditions or [])]
    expeditions.append(
        {
            "expedition_id": expedition_id,
            "target_id": target_id,
            "unit_ids": list(unit_ids),
            "started_at": now,
            "ends_at": ends_at,
            "collected": False,
        }
    )
    military.active_expeditions = expeditions
    await session.commit()
    return {
        "expedition_id": expedition_id,
        "target_id": target_id,
        "ends_at": ends_at,
        "duration_seconds": target["duration_seconds"],
        "unit_ids": list(unit_ids),
    }


async def collect_expedition(
    session: AsyncSession,
    *,
    expedition_id: str,
    slot_id: int = B.DEFAULT_SLOT_ID,
    planet_id: int = B.HOME_PLANET_ID,
) -> dict[str, Any]:
    """收取战利品：未到点拒绝；到点则入库（受仓储上限裁剪）并归还载具。"""
    military = await _military(session, slot_id, planet_id)
    colony = await session.get(ColonyState, (slot_id, planet_id))
    now = now_timestamp()
    expeditions = [dict(item) for item in (military.active_expeditions or [])]
    entry = next((item for item in expeditions if item.get("expedition_id") == expedition_id), None)
    if entry is None:
        raise NotFound("EXPEDITION_NOT_FOUND", f"远征 {expedition_id} 不存在")
    if entry.get("collected"):
        raise Conflict("EXPEDITION_COLLECTED", "该远征战利品已经收过了")
    if now < int(entry.get("ends_at", 0)):
        raise Conflict(
            "EXPEDITION_IN_PROGRESS",
            f"远征尚未结束，还需 {int(entry['ends_at']) - now} 秒",
        )

    target = B.EXPEDITION_TARGETS[entry["target_id"]]
    gained: dict[str, float] = {}
    overflowed: list[str] = []
    for resource, amount in target["drops"].items():
        cap = float(getattr(colony, f"{resource}_max"))
        before = float(getattr(colony, resource))
        after = min(cap, before + float(amount))
        if before + float(amount) > cap:
            overflowed.append(resource)
        setattr(colony, resource, round(after, RESOURCE_PRECISION))
        gained[resource] = round(after - before, RESOURCE_PRECISION)

    colony.suspicion = round(min(B.SUSPICION_MAX, float(colony.suspicion) + float(target["suspicion_cost"])), 4)

    for unit_id in entry.get("unit_ids", []):
        unit = await session.get(VehicleUnit, unit_id)
        if unit is not None:
            unit.status = VehicleStatus.IDLE
            unit.expedition_id = None
    entry["collected"] = True
    military.active_expeditions = expeditions
    await session.commit()
    return {
        "expedition_id": expedition_id,
        "target_id": entry["target_id"],
        "gained": gained,
        "overflowed": overflowed,
        "suspicion_cost": target["suspicion_cost"],
        "report": [
            f"远征【{target['name']}】返航：{', '.join(f'{B.RESOURCE_LABELS.get(k, k)} +{v:g}' for k, v in gained.items())}",
            f"地表警戒度 +{target['suspicion_cost']:g}（出勤噪音）",
        ],
    }


async def advance_military(
    session: AsyncSession,
    *,
    slot_id: int = B.DEFAULT_SLOT_ID,
    planet_id: int = B.HOME_PLANET_ID,
    now: int | None = None,
) -> dict[str, Any]:
    """推进军备时间轴：维修到期回待命、急救舱休养结束归队（每次结算调用）。"""
    now = now or now_timestamp()
    military = await _military(session, slot_id, planet_id)
    colony = await session.get(ColonyState, (slot_id, planet_id))
    events: list[str] = []

    for unit in await _vehicles(session, slot_id, planet_id):
        if unit.status == VehicleStatus.REPAIR and unit.repair_ends_at and now >= int(unit.repair_ends_at):
            spec = B.VEHICLE_TYPES[unit.unit_type]
            unit.status = VehicleStatus.IDLE
            unit.repair_ends_at = None
            unit.hull = float(spec["hull"])
            unit.shield = float(spec["shield"])
            unit.armor = min(float(unit.armor_max), float(spec["armor"]))
            events.append(f"{unit.nickname or spec['name']} 维修完成，回归机库")

    queue = list(military.hospital_queue or [])
    remaining = []
    for item in queue:
        if now >= int(item.get("ends_at", 0)):
            released = int(item.get("cats", 0))
            colony.job_idle += released
            crew_bucket = await session.get(LaborBucket, (slot_id, planet_id, "crew"))
            if crew_bucket is not None:
                crew_bucket.cat_count = max(0, int(crew_bucket.cat_count) - released)
            events.append(f"{released} 只乘员猫休养结束，回到待命池")
        else:
            remaining.append(item)
    military.hospital_queue = remaining

    # 欧米茄后台物流：车队班次到点就发下一班（20~30 分钟一班，确定性取模）
    boss = await session.get(BossState, slot_id)
    if boss is not None and (boss.convoy_ends_at is None or now >= int(boss.convoy_ends_at)):
        span = B.CONVOY_INTERVAL_MAX_SECONDS - B.CONVOY_INTERVAL_MIN_SECONDS
        boss.convoy_ends_at = now + int(B.CONVOY_INTERVAL_MIN_SECONDS + (now % int(span + 1)))
        events.append("欧米茄矿石车队已发车，进入下一班倒计时")
    await session.flush()
    return {"events": events, "hospital_remaining": len(remaining)}


# ----------------------------------------------------------------------
# 战术电力指令（模块 G4）
# ----------------------------------------------------------------------
async def tactical_action(
    session: AsyncSession,
    *,
    command: str,
    slot_id: int = B.DEFAULT_SLOT_ID,
    planet_id: int = B.HOME_PLANET_ID,
) -> dict[str, Any]:
    """超频 / 电磁脉冲 / 紧急弹射。

    * 超频与 EMP 是**短时效战术增益**，由紧接着的那一场交火消耗（10 秒窗口内有效）；
    * 弹射撤离不消耗电力，让所有在途远征立即返航并回收 50% 造价残骸（乘员安全）。
    """
    command = command.upper()
    colony = await session.get(ColonyState, (slot_id, planet_id))
    military = await _military(session, slot_id, planet_id)
    now = now_timestamp()

    if command == "EJECT":
        expeditions = [dict(item) for item in (military.active_expeditions or [])]
        active = [item for item in expeditions if not item.get("collected")]
        if not active:
            raise Conflict("NO_ACTIVE_BATTLE", "当前没有在途编队可以撤离")
        refunds: dict[str, float] = {}
        for entry in active:
            entry["collected"] = True
            for unit_id in entry.get("unit_ids", []):
                unit = await session.get(VehicleUnit, unit_id)
                if unit is None:
                    continue
                spec = B.VEHICLE_TYPES[unit.unit_type]
                for resource, need in spec["cost"].items():
                    refunds[resource] = refunds.get(resource, 0.0) + float(need) * B.TACTICAL_EJECT_DEBRIS_RATIO
                unit.status = VehicleStatus.IDLE
                unit.expedition_id = None
        military.active_expeditions = expeditions
        for resource, amount in refunds.items():
            cap = float(getattr(colony, f"{resource}_max"))
            setattr(
                colony,
                resource,
                round(min(cap, float(getattr(colony, resource)) + amount), RESOURCE_PRECISION),
            )
        await session.commit()
        return {
            "command": command,
            "cost_kwh": 0.0,
            "recalled": len(active),
            "refund": {key: round(value, RESOURCE_PRECISION) for key, value in refunds.items()},
            "battery_kwh": round(float(colony.battery_kwh), RESOURCE_PRECISION),
        }

    cost = B.TACTICAL_COMMAND_COST_KWH.get(command)
    if cost is None:
        raise BadRequest("BAD_REQUEST", f"未知战术指令 {command}")
    if float(colony.battery_kwh) < cost:
        raise InsufficientResource(
            detail=f"蓄电池电量不足：需要 {cost:g} kWh，当前 {float(colony.battery_kwh):.1f} kWh"
        )
    colony.battery_kwh = round(float(colony.battery_kwh) - cost, RESOURCE_PRECISION)
    policy = dict(military.security_policy or {})
    window = B.TACTICAL_OVERCLOCK_SECONDS if command == "OVERCLOCK" else B.TACTICAL_EMP_STUN_SECONDS
    policy["tactical"] = {"command": command, "expires_at": now + int(max(window, 10))}
    military.security_policy = policy
    colony.power_net = round(float(colony.power_net), 4)
    await session.commit()
    logger.info("战术指令：slot=%s command=%s cost=%skWh", slot_id, command, cost)
    return {
        "command": command,
        "cost_kwh": cost,
        "battery_kwh": round(float(colony.battery_kwh), RESOURCE_PRECISION),
        "buff_expires_at": policy["tactical"]["expires_at"],
    }


def _consume_tactical_buff(military: MilitaryState, now: int) -> dict[str, Any] | None:
    """取出未过期的战术增益并清空（一场交火只能吃一次）。"""
    policy = dict(military.security_policy or {})
    buff = policy.get("tactical")
    if not buff:
        return None
    policy.pop("tactical", None)
    military.security_policy = policy
    if int(buff.get("expires_at", 0)) < now:
        return None
    return buff


# ----------------------------------------------------------------------
# 伏击欧米茄矿石车队（模块 G3）
# ----------------------------------------------------------------------
async def ambush_convoy(
    session: AsyncSession,
    *,
    unit_ids: list[int],
    slot_id: int = B.DEFAULT_SLOT_ID,
    planet_id: int = B.HOME_PLANET_ID,
) -> dict[str, Any]:
    """派装甲猫车/破拆机甲拦截矿石车队：胜则掠夺 + 兵工厂断料停工 30~60 分钟。"""
    military = await _military(session, slot_id, planet_id)
    boss = await session.get(BossState, slot_id)
    if boss is None or boss.convoy_ends_at is None:
        raise Conflict("NO_CONVOY", "当前没有在途车队")
    now = now_timestamp()
    if now >= int(boss.convoy_ends_at):
        raise Conflict("CONVOY_MISSED", "车队已经驶入兵工厂，本班次伏击窗口已关闭")
    if not unit_ids:
        raise BadRequest("BAD_REQUEST", "至少要派一辆载具")

    vehicles: list[VehicleUnit] = []
    for unit_id in unit_ids:
        unit = await session.get(VehicleUnit, unit_id)
        if unit is None or unit.slot_id != slot_id or unit.status == VehicleStatus.SCRAPPED:
            raise NotFound("VEHICLE_NOT_FOUND", f"载具 {unit_id} 不存在")
        if unit.status != VehicleStatus.IDLE:
            raise Conflict("VEHICLE_BUSY", f"载具 {unit_id} 当前状态为 {unit.status.value}")
        vehicles.append(unit)
    types = {unit.unit_type for unit in vehicles}
    if not types & set(B.CONVOY_REQUIRED_UNIT_TYPES):
        names = "、".join(B.VEHICLE_TYPES[t]["name"] for t in B.CONVOY_REQUIRED_UNIT_TYPES)
        raise BadRequest("BAD_REQUEST", f"伏击车队需要编入：{names}")

    colony = await session.get(ColonyState, (slot_id, planet_id))
    catnip_ratio = (
        float(colony.catnip) / float(colony.catnip_max) if float(colony.catnip_max or 0) > 0 else 1.0
    )
    buff = _consume_tactical_buff(military, now)
    dps_bonus = B.TACTICAL_OVERCLOCK_DPS_BONUS if buff and buff.get("command") == "OVERCLOCK" else 0.0
    stun_rounds = (
        int(B.TACTICAL_EMP_STUN_SECONDS) if buff and buff.get("command") == "EMP" else 0
    )

    attackers = []
    for unit in vehicles:
        spec = B.VEHICLE_TYPES[unit.unit_type]
        snapshot = unit_from_spec(spec, prefix=f"unit-{unit.unit_id}")
        snapshot.update(
            {
                "shield": float(unit.shield),
                "armor": float(unit.armor),
                "armor_max": float(unit.armor_max),
                "hull": float(unit.hull),
            }
        )
        # 车载模块（§9.10）：效果写进这辆车的快照，与科技/士气加成各走各的通道
        apply_module_effects(snapshot, list(unit.modules or []))
        attackers.append(snapshot)
    defenders = [
        unit_from_spec(B.ENEMY_UNITS[unit_id], prefix=f"enemy-{unit_id}")
        for unit_id in B.CONVOY_GUARD_UNITS
    ]

    result = resolve_skirmish(
        attackers,
        defenders,
        attacker_morale=morale_multiplier(catnip_ratio),
        attacker_dps_bonus=dps_bonus,
        attacker_armor_bonus=await _fleet_armor_bonus(session, slot_id, planet_id),
        defender_stun_rounds=stun_rounds,
    )
    won = result["winner"] == "ATTACK"
    loot: dict[str, float] = {}
    for unit, snapshot in zip(vehicles, result["attackers"]):
        unit.shield = round(float(snapshot["shield"]), RESOURCE_PRECISION)
        unit.armor = round(float(snapshot["armor"]), RESOURCE_PRECISION)
        unit.armor_max = round(float(snapshot["armor_max"]), RESOURCE_PRECISION)
        unit.hull = round(float(snapshot["hull"]), RESOURCE_PRECISION)
        if unit.hull <= 0:
            unit.status = VehicleStatus.REPAIR
            crew = int(unit.crew_cats)
            if crew:
                unit.crew_cats = 0
                queue = list(military.hospital_queue or [])
                queue.append({"unit_id": unit.unit_id, "cats": crew, "ends_at": now + B.CREW_RECOVERY_BASE_SECONDS})
                military.hospital_queue = queue
                crew_bucket = await session.get(LaborBucket, (slot_id, planet_id, "crew"))
                if crew_bucket is not None:
                    crew_bucket.cat_count = max(0, int(crew_bucket.cat_count) - crew)
                colony.job_idle = max(0, colony.job_idle - crew)

    if won:
        for resource, amount in B.CONVOY_LOOT.items():
            cap = float(getattr(colony, f"{resource}_max"))
            before = float(getattr(colony, resource))
            setattr(colony, resource, round(min(cap, before + amount), RESOURCE_PRECISION))
            loot[resource] = round(min(cap, before + amount) - before, RESOURCE_PRECISION)
        freeze_span = B.CONVOY_FACTORY_FREEZE_MAX_SECONDS - B.CONVOY_FACTORY_FREEZE_MIN_SECONDS
        freeze = B.CONVOY_FACTORY_FREEZE_MIN_SECONDS + (now % int(freeze_span + 1))
        boss.factory_frozen_until = now + int(freeze)
        boss.convoy_ends_at = None  # 车队被劫，等下一班
        events = [f"伏击成功：缴获 {'、'.join(f'{k} +{v:g}' for k, v in loot.items())}，兵工厂断料停工 {freeze / 60:.0f} 分钟"]
    else:
        boss.convoy_ends_at = None  # 护卫突破拦截，继续驶向兵工厂
        events = ["伏击失败：护卫编队突破拦截，车队继续驶向兵工厂（载具已进维修，乘员进急救舱）"]

    await session.commit()
    return {
        "won": won,
        "rounds": result["rounds"],
        "loot": loot,
        "factory_frozen_until": boss.factory_frozen_until if won else None,
        "tactical_buff": buff,
        "ejected": result["ejected"],
        "log": result["log"][-12:],
        "events": events,
    }


# ----------------------------------------------------------------------
# 战略巡航导弹（模块 K1）
# ----------------------------------------------------------------------
async def assemble_missile(
    session: AsyncSession,
    *,
    slot_id: int = B.DEFAULT_SLOT_ID,
    planet_id: int = B.HOME_PLANET_ID,
) -> dict[str, Any]:
    """总装一枚地对地战略巡航导弹（合金 30 + 芯片 10 + 电池 2）。"""
    military = await _military(session, slot_id, planet_id)
    colony = await session.get(ColonyState, (slot_id, planet_id))
    missing = {
        resource: need - float(getattr(colony, resource))
        for resource, need in B.MISSILE_ASSEMBLE_COST.items()
        if float(getattr(colony, resource)) < need
    }
    if missing:
        raise InsufficientResource(
            detail="缺料：" + "、".join(f"{B.RESOURCE_LABELS.get(k, k)} 差 {v:g}" for k, v in missing.items())
        )
    for resource, need in B.MISSILE_ASSEMBLE_COST.items():
        setattr(colony, resource, round(float(getattr(colony, resource)) - need, RESOURCE_PRECISION))
    military.cruise_missiles = int(military.cruise_missiles) + 1
    await session.commit()
    return {
        "cruise_missiles": int(military.cruise_missiles),
        "cost_paid": dict(B.MISSILE_ASSEMBLE_COST),
    }


async def launch_missile(
    session: AsyncSession,
    *,
    slot_id: int = B.DEFAULT_SLOT_ID,
    planet_id: int = B.HOME_PLANET_ID,
) -> dict[str, Any]:
    """点火巡航导弹：摧毁除菌要塞能量穹顶与主反应堆（母星关底破壁）。"""
    military = await _military(session, slot_id, planet_id)
    if int(military.cruise_missiles) <= 0:
        raise Conflict("NO_MISSILE", "机库里还没有总装好的巡航导弹")
    boss = await session.get(BossState, slot_id)
    if boss is None:
        raise NotFound("BOSS_STATE_NOT_FOUND")
    now = now_timestamp()
    military.cruise_missiles = int(military.cruise_missiles) - 1
    state = dict(boss.bombardment_state or {})
    state["fortress_destroyed_at"] = now
    boss.bombardment_state = state
    boss.threat_level = max(1, int(boss.threat_level) - 1)
    boss.fleet_strength = round(float(boss.fleet_strength) * 0.8, 2)
    boss.rage = round(min(B.SUSPICION_MAX, float(boss.rage) + B.MISSILE_LAUNCH_RAGE_GAIN), 2)
    boss.intel_level = round(min(1.0, float(boss.intel_level) + B.MISSILE_LAUNCH_INTEL_GAIN / 100), 4)
    await session.commit()
    return {
        "cruise_missiles": int(military.cruise_missiles),
        "fortress_destroyed_at": now,
        "threat_level": int(boss.threat_level),
        "fleet_strength": float(boss.fleet_strength),
        "rage": float(boss.rage),
        "intel_level": float(boss.intel_level),
        "events": [
            "巡航导弹点火：除菌要塞能量穹顶与主反应堆被摧毁",
            f"欧米伽通缉热度 +{B.MISSILE_LAUNCH_RAGE_GAIN:.0f}，核心舰队战力 −20%",
        ],
    }


# ----------------------------------------------------------------------
# 满警戒度：战车截杀（§8.3 优先级 2）
# ----------------------------------------------------------------------
async def intercept_alert(
    session: AsyncSession,
    *,
    slot_id: int = B.DEFAULT_SLOT_ID,
    planet_id: int = B.HOME_PLANET_ID,
    catnip_ratio: float = 1.0,
) -> dict[str, Any] | None:
    """派闲置载具迎击满警戒度的侦察扫地机。

    赢：清空警报 + 缴获芯片/合金 + `rage + 25`；输：载具进维修、乘员进急救舱（**绝无死猫**）。
    """
    military = await _military(session, slot_id, planet_id)
    colony = await session.get(ColonyState, (slot_id, planet_id))
    idle = [u for u in await _vehicles(session, slot_id, planet_id) if u.status == VehicleStatus.IDLE]
    if not idle:
        return None

    spec = B.VEHICLE_TYPES[idle[0].unit_type]
    attackers = []
    for unit in idle[:2]:
        unit_spec = B.VEHICLE_TYPES[unit.unit_type]
        snapshot = unit_from_spec(unit_spec, prefix=f"unit-{unit.unit_id}")
        snapshot["id"] = f"unit-{unit.unit_id}"
        snapshot["shield"] = float(unit.shield)
        snapshot["armor"] = float(unit.armor)
        snapshot["armor_max"] = float(unit.armor_max)
        snapshot["hull"] = float(unit.hull)
        apply_module_effects(snapshot, list(unit.modules or []))  # 模块效果（§9.10）
        attackers.append(snapshot)
    enemy_spec = B.ENEMY_UNITS[B.INTERCEPT_ENEMY_UNIT]
    defenders = [unit_from_spec(enemy_spec, prefix="enemy-scout")]

    result = resolve_skirmish(
        attackers,
        defenders,
        attacker_morale=morale_multiplier(catnip_ratio),
        attacker_armor_bonus=await _fleet_armor_bonus(session, slot_id, planet_id),
    )
    won = result["winner"] == "ATTACK"
    now = now_timestamp()

    for unit, snapshot in zip(idle[:2], result["attackers"]):
        unit.shield = round(float(snapshot["shield"]), RESOURCE_PRECISION)
        unit.armor = round(float(snapshot["armor"]), RESOURCE_PRECISION)
        unit.armor_max = round(float(snapshot["armor_max"]), RESOURCE_PRECISION)
        unit.hull = round(float(snapshot["hull"]), RESOURCE_PRECISION)
        if unit.hull <= 0:
            unit.status = VehicleStatus.REPAIR
            unit.repair_ends_at = None
            crew = int(unit.crew_cats)
            if crew:
                unit.crew_cats = 0
                queue = list(military.hospital_queue or [])
                queue.append({"unit_id": unit.unit_id, "cats": crew, "ends_at": now + B.CREW_RECOVERY_BASE_SECONDS})
                military.hospital_queue = queue
                crew_bucket = await session.get(LaborBucket, (slot_id, planet_id, "crew"))
                if crew_bucket is not None:
                    crew_bucket.cat_count = max(0, int(crew_bucket.cat_count) - crew)
                colony.job_idle = max(0, colony.job_idle - crew)

    from app.models import BossState  # 局部导入避免循环

    boss = await session.get(BossState, slot_id)
    loot: dict[str, float] = {}
    if won:
        colony.suspicion = 0.0
        for resource, amount in (
            ("chips", B.INTERCEPT_LOOT_CHIPS),
            ("alloys", B.INTERCEPT_LOOT_ALLOYS),
        ):
            cap = float(getattr(colony, f"{resource}_max"))
            before = float(getattr(colony, resource))
            setattr(colony, resource, round(min(cap, before + amount), RESOURCE_PRECISION))
            loot[resource] = round(min(cap, before + amount) - before, RESOURCE_PRECISION)
        if boss is not None:
            boss.rage = round(min(B.SUSPICION_MAX, float(boss.rage) + B.ANTI_AIR_SORTIE_RAGE_GAIN), 2)
    else:
        colony.suspicion = round(max(0.0, float(colony.suspicion) - 20.0), 4)

    await session.commit()
    return {
        "won": won,
        "enemy": enemy_spec["name"],
        "rounds": result["rounds"],
        "loot": loot,
        "rage_gain": B.ANTI_AIR_SORTIE_RAGE_GAIN if won else 0.0,
        "ejected": result["ejected"],
        "log": result["log"][-12:],
        "lead_vehicle": spec["name"],
    }
