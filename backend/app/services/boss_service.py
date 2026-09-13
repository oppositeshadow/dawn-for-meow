"""欧米茄终局服务（模块 L）：后台演化 / 掠夺突袭拦截 / 星门决战与通关碑文。

* **L1 后台演化**：每 30 分钟一个周期，核心舰队 +25、每 3 个周期威胁等级 +1（上限 10），
  并自动排下一班【前哨掠夺突袭】（20~40 分钟）——全部按绝对时间戳补算，离线照走；
* **L2 掠夺战**：突袭到期前可派装甲猫车/破拆机甲拦截（胜负都进战报），到期未拦截则资源 −15% 并标记设施受损；
* **L3 决战**：星门突破战 → 分区总督舰队 → 戴森主脑突入，三战全胜后按下 Override Key 通关，
  并由 LLM（场景 6，一生一次，允许超预算）写下《猫猫文明星际史诗碑文》，断网走本地模板兜底。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import balance as B
from app.core.combat_engine import morale_multiplier, resolve_skirmish, unit_from_spec
from app.core.errors import BadRequest, Conflict, InsufficientResource, NotFound
from app.models import BossState, ColonyState, LaborBucket, MilitaryState, VehicleUnit
from app.models.military import VehicleStatus
from app.schemas.epilogue_agent import Epilogue
from app.services.game_init_service import now_timestamp
from app.services.llm_service import LlmScene, get_llm_service

logger = logging.getLogger("dawn_meow.boss")

RESOURCE_PRECISION = 2

EPILOGUE_SYSTEM = (
    "你是《喵星破晓》的编年史官。玩家刚带领猫猫文明打通终局：欧米伽格式化认主、星海重新点亮。"
    "请写一段 80~200 字的《猫猫文明星际史诗碑文》，从地下避难所的纸箱窝写到星海破晓，"
    "语气庄重又温柔，可以有猫的细节（呼噜声、纸箱、尾巴），不要出现现实国家与人名。只输出 JSON。"
)


async def _boss(session: AsyncSession, slot_id: int) -> BossState:
    row = await session.get(BossState, slot_id)
    if row is None:
        raise NotFound("BOSS_STATE_NOT_FOUND", f"槽位 {slot_id} 没有欧米茄状态")
    return row


async def _military(session: AsyncSession, slot_id: int, planet_id: int) -> MilitaryState:
    row = await session.get(MilitaryState, (slot_id, planet_id))
    if row is None:
        raise NotFound("COLONY_STATE_NOT_FOUND", "没有军备状态")
    return row


def _state(boss: BossState) -> dict[str, Any]:
    return dict(boss.bombardment_state or {})


def next_raid_delay(now: int) -> int:
    span = B.RAID_INTERVAL_MAX_SECONDS - B.RAID_INTERVAL_MIN_SECONDS
    return int(B.RAID_INTERVAL_MIN_SECONDS + (now % int(span + 1)))


async def advance_boss(
    session: AsyncSession,
    *,
    slot_id: int = B.DEFAULT_SLOT_ID,
    planet_id: int = B.HOME_PLANET_ID,
    seconds: float,
    now: int | None = None,
) -> dict[str, Any]:
    """后台演化补算：增兵、威胁升级、突袭调度与"未拦截"惩罚。"""
    if seconds <= 0:
        return {"ticks": 0, "events": []}
    boss = await _boss(session, slot_id)
    colony = await session.get(ColonyState, (slot_id, planet_id))
    now = now or now_timestamp()
    events: list[str] = []

    ticks = int(seconds // B.BOSS_TICK_SECONDS)
    if ticks > 0:
        boss.fleet_strength = round(float(boss.fleet_strength) + ticks * B.BOSS_FLEET_GAIN_PER_TICK, 2)
        threat_steps = ticks // B.BOSS_THREAT_UP_EVERY_TICKS
        if threat_steps:
            boss.threat_level = min(B.BOSS_THREAT_MAX, int(boss.threat_level) + threat_steps)
        events.append(
            f"欧米伽后台演化 {ticks} 个周期：舰队战力 → {float(boss.fleet_strength):.0f}（威胁 Lv.{int(boss.threat_level)}）"
        )
    boss.last_tick_time = now

    # 突袭调度：没有排班就排一班；已到期且没被拦截 ⇒ 扣资源 + 标记设施受损
    if boss.raid_ends_at is None:
        boss.raid_ends_at = now + next_raid_delay(now)
        boss.raid_target_planet = B.HOME_PLANET_ID
    elif now >= int(boss.raid_ends_at):
        if colony is not None:
            losses: list[str] = []
            for resource in ("catnip", "scrap", "chips", "alloys"):
                before = float(getattr(colony, resource))
                after = round(before * (1.0 - B.RAID_RESOURCE_LOSS_RATIO), RESOURCE_PRECISION)
                setattr(colony, resource, after)
                if before > 0:
                    losses.append(f"{B.RESOURCE_LABELS.get(resource, resource)} −{before - after:.1f}")
            events.append("欧米茄前哨掠夺舰队突袭得手：" + ("、".join(losses) if losses else "资源本就见底"))
        state = _state(boss)
        damaged = list(state.get("damaged_facilities") or [])
        damaged.append({"at": now, "reason": "RAID"})
        state["damaged_facilities"] = damaged[-20:]
        state["last_raid_at"] = now
        boss.bombardment_state = state
        boss.raid_ends_at = now + next_raid_delay(now)
        events.append(f"下一班掠夺舰队已排定（{int(B.RAID_INTERVAL_MIN_SECONDS // 60)}~{int(B.RAID_INTERVAL_MAX_SECONDS // 60)} 分钟后）")

    await session.flush()
    return {"ticks": ticks, "events": events}


async def intercept_raid(
    session: AsyncSession,
    *,
    unit_ids: list[int],
    slot_id: int = B.DEFAULT_SLOT_ID,
    planet_id: int = B.HOME_PLANET_ID,
) -> dict[str, Any]:
    """拦截掠夺舰队：胜则击退并缴获，败则车损 + 乘员进急救舱（资源损失减半）。"""
    boss = await _boss(session, slot_id)
    military = await _military(session, slot_id, planet_id)
    colony = await session.get(ColonyState, (slot_id, planet_id))
    now = now_timestamp()
    if boss.raid_ends_at is None or now >= int(boss.raid_ends_at):
        raise Conflict("NO_RAID", "当前没有在途的掠夺舰队（要么还没排班，要么已经突袭得手）")
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
    if not types & set(B.RAID_REQUIRED_UNIT_TYPES):
        names = "、".join(B.VEHICLE_TYPES[t]["name"] for t in B.RAID_REQUIRED_UNIT_TYPES)
        raise BadRequest("BAD_REQUEST", f"拦截掠夺舰队需要编入：{names}")

    catnip_ratio = (
        float(colony.catnip) / float(colony.catnip_max) if float(colony.catnip_max or 0) > 0 else 1.0
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
        attackers.append(snapshot)
    defenders = [
        unit_from_spec(B.ENEMY_UNITS[unit_id], prefix=f"enemy-{unit_id}") for unit_id in B.RAID_ENEMY_UNITS
    ]
    result = resolve_skirmish(attackers, defenders, attacker_morale=morale_multiplier(catnip_ratio))
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
                bucket = await session.get(LaborBucket, (slot_id, planet_id, "crew"))
                if bucket is not None:
                    bucket.cat_count = max(0, int(bucket.cat_count) - crew)
                colony.job_idle = max(0, colony.job_idle - crew)

    if won:
        for resource, amount in B.RAID_INTERCEPT_LOOT.items():
            cap = float(getattr(colony, f"{resource}_max"))
            before = float(getattr(colony, resource))
            setattr(colony, resource, round(min(cap, before + amount), RESOURCE_PRECISION))
            loot[resource] = round(min(cap, before + amount) - before, RESOURCE_PRECISION)
        events = [f"拦截至截成功：击退掠夺舰队，缴获 {'、'.join(f'{k} +{v:g}' for k, v in loot.items())}"]
    else:
        loss = B.RAID_RESOURCE_LOSS_RATIO * 0.5  # 拦截失败：损失减半（舰队被拖住了）
        for resource in ("catnip", "scrap", "chips", "alloys"):
            before = float(getattr(colony, resource))
            setattr(colony, resource, round(before * (1.0 - loss), RESOURCE_PRECISION))
        events = [f"拦截失败：掠夺舰队抢走约 {int(loss * 100)}% 物资（载具进维修、乘员进急救舱）"]

    boss.raid_ends_at = now + next_raid_delay(now)
    await session.commit()
    return {
        "won": won,
        "rounds": result["rounds"],
        "loot": loot,
        "ejected": result["ejected"],
        "log": result["log"][-12:],
        "events": events,
        "next_raid_at": int(boss.raid_ends_at),
    }


async def _epitaph(session: AsyncSession, slot_id: int, state: dict[str, Any]) -> dict[str, Any]:
    """LLM 场景 6：通关碑文（一生一次，允许超预算放行）。"""
    service = get_llm_service()
    fallback = Epilogue(epitaph=B.EPILOGUE_FALLBACK, title="猫猫文明星际史诗碑文")
    epilogue = fallback
    source = "FALLBACK"
    usage: dict[str, Any] = {}
    if service.configured:
        result = await service.complete_json(
            scene=LlmScene.EPIC_EPITAPH,
            system=EPILOGUE_SYSTEM,
            user='输出 JSON：{"title": "猫猫文明星际史诗碑文", "epitaph": "……"}（80~200 字）',
            max_tokens=400,
            temperature=0.9,
        )
        if result.ok and isinstance(result.payload, dict):
            try:
                payload = dict(result.payload)
                if "epitaph" in payload:
                    payload["epitaph"] = str(payload["epitaph"])[: B.EPILOGUE_MAX_LENGTH]
                epilogue = Epilogue.model_validate(payload)
                source = "LLM"
            except Exception as exc:  # 防火墙 1 拦截 ⇒ 本地模板
                logger.warning("通关碑文未通过 Pydantic 防火墙：%s", exc)
        usage = result.usage.to_dict()
    state["epitaph"] = epilogue.epitaph
    state["epitaph_title"] = epilogue.title
    state["epitaph_source"] = source
    return {"title": epilogue.title, "epitaph": epilogue.epitaph, "source": source, "usage": usage}


async def final_assault(
    session: AsyncSession,
    *,
    stage: int,
    unit_ids: list[int],
    slot_id: int = B.DEFAULT_SLOT_ID,
    planet_id: int = B.HOME_PLANET_ID,
) -> dict[str, Any]:
    """终局决战：按 1→2→3 依次推进，三战全胜即通关并写下碑文。"""
    boss = await _boss(session, slot_id)
    colony = await session.get(ColonyState, (slot_id, planet_id))
    military = await _military(session, slot_id, planet_id)
    state = _state(boss)
    if state.get("override_key_used_at"):
        raise Conflict("ALREADY_COMPLETED", "星海已经归于猫猫文明，无需再战")
    spec = next((item for item in B.FINAL_ASSAULT_STAGES if int(item["stage"]) == int(stage)), None)
    if spec is None:
        raise BadRequest("BAD_REQUEST", f"决战只有 1~{len(B.FINAL_ASSAULT_STAGES)} 个阶段")
    cleared = int(state.get("final_stage_cleared") or 0)
    if int(stage) != cleared + 1:
        raise Conflict("STAGE_ORDER", f"必须先打赢第 {cleared + 1} 段（{B.FINAL_ASSAULT_STAGES[cleared]['name']}）")

    cost = {resource: float(amount) for resource, amount in spec["cost"].items()}
    missing = {
        resource: need - float(getattr(colony, resource))
        for resource, need in cost.items()
        if float(getattr(colony, resource)) < need
    }
    if missing:
        raise InsufficientResource(
            detail="决战物资不足：" + "、".join(f"{B.RESOURCE_LABELS.get(k, k)} 差 {v:g}" for k, v in missing.items())
        )

    vehicles: list[VehicleUnit] = []
    for unit_id in unit_ids:
        unit = await session.get(VehicleUnit, unit_id)
        if unit is None or unit.slot_id != slot_id or unit.status != VehicleStatus.IDLE:
            raise Conflict("VEHICLE_BUSY", f"载具 {unit_id} 不在待命状态")
        vehicles.append(unit)
    if len(vehicles) < int(spec["min_units"]):
        raise BadRequest("BAD_REQUEST", f"【{spec['name']}】至少要派 {int(spec['min_units'])} 辆载具")

    for resource, need in cost.items():
        setattr(colony, resource, round(float(getattr(colony, resource)) - need, RESOURCE_PRECISION))

    catnip_ratio = (
        float(colony.catnip) / float(colony.catnip_max) if float(colony.catnip_max or 0) > 0 else 1.0
    )
    attackers = []
    for unit in vehicles:
        unit_spec = B.VEHICLE_TYPES[unit.unit_type]
        snapshot = unit_from_spec(unit_spec, prefix=f"unit-{unit.unit_id}")
        snapshot.update(
            {
                "shield": float(unit.shield),
                "armor": float(unit.armor),
                "armor_max": float(unit.armor_max),
                "hull": float(unit.hull),
            }
        )
        attackers.append(snapshot)
    defenders = [
        unit_from_spec(B.ENEMY_UNITS[unit_id], prefix=f"enemy-{unit_id}") for unit_id in spec["enemies"]
    ]
    result = resolve_skirmish(attackers, defenders, attacker_morale=morale_multiplier(catnip_ratio))
    won = result["winner"] == "ATTACK"

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
                queue.append({"unit_id": unit.unit_id, "cats": crew, "ends_at": now_timestamp() + B.CREW_RECOVERY_BASE_SECONDS})
                military.hospital_queue = queue

    events = [f"{spec['name']}：{'大捷' if won else '失利'}（{result['rounds']} 回合）"]
    epitaph: dict[str, Any] | None = None
    completed = False
    if won:
        state["final_stage_cleared"] = int(stage)
        if int(stage) == len(B.FINAL_ASSAULT_STAGES):
            state["override_key_used_at"] = now_timestamp()
            boss.fleet_strength = 0.0
            boss.threat_level = 1
            boss.rage = 0.0
            epitaph = await _epitaph(session, slot_id, state)
            completed = True
            events.append("人类最高管理员覆写密码已按下：欧米茄格式化认主，全星系统一")
            events.append(f"🎬 {epitaph['title']}：{epitaph['epitaph']}")
    boss.bombardment_state = state
    await session.commit()
    return {
        "stage": int(stage),
        "stage_name": spec["name"],
        "won": won,
        "rounds": result["rounds"],
        "cost_paid": cost,
        "cleared_stage": int(state.get("final_stage_cleared") or 0),
        "completed": completed,
        "epitaph": epitaph,
        "ejected": result["ejected"],
        "log": result["log"][-12:],
        "events": events,
        "staff_roll": (
            [
                "《喵星破晓 Dawn for Meow》—— 演职员表",
                "主演：折耳猫、三花娘娘、奶牛猫队长、橘色推土机、黑猫技工",
                "特别出演：欧米伽（被格式化后成为猫爬架管理员）",
                "技术顾问：极客科研猫；电力保障：踩轮发电猫；后勤：拾荒猫小队",
                "鸣谢：每一位在通风管道里打呼噜的猫猫",
            ]
            if completed
            else []
        ),
    }
