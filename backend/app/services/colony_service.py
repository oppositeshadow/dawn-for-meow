"""基地状态服务：离线结算落库、状态读取、15 秒快照对账（模块 A/B/C 的服务层）。

职责划分（数据库设计定稿 §8.1）：

* 前端：100ms 插值渲染 + 每 15 秒提交一次**预测**快照，不作为数值真值；
* 后端：用 core/balance.py 的同一套公式，从 `last_tick_time` 重算时间区间并落库。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import balance as B
from app.core.config import get_settings
from app.core.errors import (
    BadRequest,
    Conflict,
    InsufficientResource,
    NotFound,
    WorkstationLimitExceeded,
)
from app.core.offline_engine import (
    build_report_summary,
    calculate_offline_progress,
    power_balance,
)
from app.models import (
    CareerStats,
    ColonyState,
    FacilityState,
    GardenState,
    LaborBucket,
    MilitaryState,
    PlanetState,
    SaveSlot,
)
from app.models import BossState, MilitaryState, VehicleUnit
from app.models.military import VehicleStatus
from app.core.seed_loader import facility_def_map
from app.schemas.colony import SnapshotRequest
from app.services import tech_service
from app.services import combat_service
from app.services import garden_service
from app.services import darknet_service
from app.services import boss_service
from app.services.game_init_service import create_new_game, now_timestamp

logger = logging.getLogger("dawn_meow.colony")

RESOURCE_PRECISION = 2
PROGRESS_PRECISION = 4

#: GDD §1.3 阶段二：第一座纸箱窝建成时弹给玩家的温情叙事
FIRST_CAT_NARRATIVE = (
    "黑暗潮湿的通风管道深处传来了轻微的爪子声……一只脏兮兮的折耳流浪猫闻到干燥纸箱的气味，"
    "警惕地探出小脑袋，钻进纸箱蜷成毛球，喉咙里发出了微弱的呼噜声。"
    "它抬起头看了看你——好像在等下一顿饭（造一座【水培农田】，再把它派去当农夫吧）。"
)


# ----------------------------------------------------------------------
# 读取辅助
# ----------------------------------------------------------------------
async def get_save(session: AsyncSession, slot_id: int) -> SaveSlot:
    save = await session.get(SaveSlot, slot_id)
    if save is None:
        raise NotFound("SAVE_NOT_FOUND", f"槽位 {slot_id} 还没有存档")
    return save


async def get_facility_levels(
    session: AsyncSession, slot_id: int, planet_id: int
) -> dict[str, int]:
    """设施等级字典（facilities.json 里定义但库里缺失的，一律按 0 级处理）。"""
    rows = (
        await session.execute(
            select(FacilityState).where(
                FacilityState.slot_id == slot_id, FacilityState.planet_id == planet_id
            )
        )
    ).scalars().all()
    levels = {facility_id: 0 for facility_id in B.FACILITY_IDS}
    for row in rows:
        levels[row.facility_id] = int(row.level)
    return levels


async def sync_facility_rows(
    session: AsyncSession, slot_id: int, planet_id: int
) -> list[str]:
    """为 facilities.json 新增的设施补 0 级行（验收 D-3：加设施不用改表结构）。"""
    rows = (
        await session.execute(
            select(FacilityState.facility_id).where(
                FacilityState.slot_id == slot_id, FacilityState.planet_id == planet_id
            )
        )
    ).scalars().all()
    existing = set(rows)
    added: list[str] = []
    for facility_id in B.FACILITY_IDS:
        if facility_id not in existing:
            session.add(
                FacilityState(
                    slot_id=slot_id, planet_id=planet_id, facility_id=facility_id, level=0
                )
            )
            added.append(facility_id)
    if added:
        await session.flush()
    return added


async def get_labor_counts(
    session: AsyncSession, slot_id: int, planet_id: int
) -> dict[str, int]:
    """工种分桶字典（含母星 5 工种与星际 4 职业的 0 值占位）。"""
    rows = (
        await session.execute(
            select(LaborBucket).where(
                LaborBucket.slot_id == slot_id, LaborBucket.planet_id == planet_id
            )
        )
    ).scalars().all()
    counts = {job_id: 0 for job_id in (*B.PLANET_JOBS, *B.STAR_JOBS)}
    for row in rows:
        counts[row.job_id] = int(row.cat_count)
    return counts


async def sync_labor_rows(session: AsyncSession, slot_id: int, planet_id: int) -> list[str]:
    """为 jobs.json 新增的工种补 0 猫口行（验收 C2：新职业不用改表结构）。"""
    rows = (
        await session.execute(
            select(LaborBucket.job_id).where(
                LaborBucket.slot_id == slot_id, LaborBucket.planet_id == planet_id
            )
        )
    ).scalars().all()
    existing = set(rows)
    added: list[str] = []
    for job_id in (*B.PLANET_JOBS, *B.STAR_JOBS):
        if job_id not in existing:
            session.add(
                LaborBucket(
                    slot_id=slot_id, planet_id=planet_id, job_id=job_id, cat_count=0
                )
            )
            added.append(job_id)
    if added:
        await session.flush()
    return added


async def get_hangar_capacity(session: AsyncSession, slot_id: int, planet_id: int) -> int:
    military = await session.get(MilitaryState, (slot_id, planet_id))
    return int(military.hangar_capacity) if military else 0


async def star_jobs_unlocked(session: AsyncSession, slot_id: int) -> bool:
    """是否已升空（母星发射井推定完成）——星际职业工位与文明凝聚力的开关（§15.1）。"""
    row = await session.get(FacilityState, (slot_id, B.HOME_PLANET_ID, "launch_silo"))
    return bool(row and int(row.level) >= len(B.LAUNCH_SILO_STAGES))


async def get_military(session: AsyncSession, slot_id: int, planet_id: int) -> MilitaryState | None:
    """军备池化行（安防预案与诱饵库存都在这里）。"""
    return await session.get(MilitaryState, (slot_id, planet_id))


async def count_idle_vehicles(session: AsyncSession, slot_id: int, planet_id: int) -> int:
    """闲置载具数（满警戒度时"战车截杀"预案的资格判定）。"""
    total = await session.execute(
        select(func.count())
        .select_from(VehicleUnit)
        .where(
            VehicleUnit.slot_id == slot_id,
            VehicleUnit.planet_id == planet_id,
            VehicleUnit.status == VehicleStatus.IDLE,
        )
    )
    return int(total.scalar_one())


def garden_halo(garden: GardenState | None) -> dict[str, float]:
    """在田光环汇总（模块 H）：荧光苔藓供电、消音绒草降警戒……"""
    if garden is None or not garden.grid_data:
        return {}
    from app.core.garden_engine import halo_summary
    from app.services.garden_service import plant_defs

    return halo_summary(list(garden.grid_data), plant_defs())


async def get_garden_power_kw(session: AsyncSession, slot_id: int, planet_id: int) -> float:
    garden = await session.get(GardenState, (slot_id, planet_id))
    return float(garden_halo(garden).get("power_kw", 0.0))


async def get_tech_effects(session: AsyncSession, slot_id: int, planet_id: int) -> dict[str, float]:
    """已解锁科技里**已接入结算**的数值加成（模块 E5；口径见 balance.TECH_ACTIVE_EFFECT_KEYS）。"""
    from app.services import tech_service

    return await tech_service.unlocked_effects(session, slot_id, planet_id)


def _exclusive_output_bonus(facilities: Mapping[str, int], resource: str) -> float:
    """该星球专属设施给出的产出加成（§15.4，按设施等级累加）。"""
    total = 0.0
    for facility_id in B.FACILITY_IDS:
        level = int(facilities.get(facility_id, 0))
        bonus_resource, bonus = B.facility_output_bonus(facility_id, level)
        if bonus_resource == resource:
            total += bonus
    return round(total, 4)


def _exclusive_smelt_bonus(facilities: Mapping[str, int]) -> float:
    """该星球专属设施给出的熔炼速度加成（§15.4）。"""
    total = 0.0
    for facility_id in B.FACILITY_IDS:
        total += B.facility_smelt_speed_bonus(facility_id, int(facilities.get(facility_id, 0)))
    return round(total, 4)


async def get_doctrine_effects(session: AsyncSession, slot_id: int) -> dict[str, float]:
    """已点亮政令里**已接线**效果的汇总（§15.2；白名单见 `doctrine_service.WIRED_EFFECTS`）。"""
    from app.services import doctrine_service

    return await doctrine_service.active_effects(session, slot_id)


def star_job_bonuses(labor: Mapping[str, int]) -> dict[str, float]:
    """星际职业的加成（§15.1）：返回 {键: 加值}，供承载力 / 生产 / 士气等通道使用。"""
    count = lambda job: int(labor.get(job, 0))  # noqa: E731
    return {
        # 行星生态塑形师：该星球承载力 K +8%/只
        "cat_capacity": round(count("terraformer") * 0.08, 4),
        # 文明呼噜大师：士气 +2%/只（按"生产效率"通道并入，与政令生产效率相加）
        "morale": round(count("purr_master") * 0.02, 4),
    }


async def get_smelt_speed_bonus(session: AsyncSession, slot_id: int) -> float:
    """小游戏【熔炉配比】攒下的永久熔炼加成（上限 +50%，§16 口径）。"""
    from app.models import MinigameState

    rows = (
        await session.execute(
            select(MinigameState).where(
                MinigameState.slot_id == slot_id, MinigameState.minigame_id == "forge_recipe"
            )
        )
    ).scalars().all()
    return max([float((row.state or {}).get("smelt_speed_bonus", 0.0)) for row in rows] or [0.0])


# ----------------------------------------------------------------------
# 工位上限（模块 C2 / D-2）
# ----------------------------------------------------------------------
def workstation_violations(
    limits: Mapping[str, int], counts: Mapping[str, int]
) -> dict[str, str]:
    """返回"超出工位上限"的工种 → 说明；空字典表示全部合法。"""
    violations: dict[str, str] = {}
    for job_id, count in counts.items():
        limit = int(limits.get(job_id, 0))
        if int(count) > limit:
            violations[job_id] = f"{job_id}: {count} > 工位上限 {limit}"
    return violations


def ensure_workstation_capacity(
    limits: Mapping[str, int], counts: Mapping[str, int]
) -> None:
    """工位上限硬校验：越界抛 400 WORKSTATION_LIMIT_EXCEEDED（工位调整接口复用）。"""
    violations = workstation_violations(limits, counts)
    if violations:
        raise WorkstationLimitExceeded(detail=", ".join(violations.values()))


# ----------------------------------------------------------------------
# 状态构建与离线结算
# ----------------------------------------------------------------------
def build_engine_state(
    colony: ColonyState,
    labor: Mapping[str, int],
    facilities: Mapping[str, int],
    *,
    garden: GardenState | None = None,
    military: MilitaryState | None = None,
    now: int | None = None,
    idle_vehicles: int = 0,
    production_multiplier: float = 1.0,
    doctrine_effects: Mapping[str, float] | None = None,
    star_jobs: Mapping[str, float] | None = None,
    planet_cycle_factor: float = 1.0,
    solar_multiplier: float = 1.0,
    catnip_efficiency: float = 0.0,
    tech_power_kw: float = 0.0,
    tech_effects: Mapping[str, float] | None = None,
    minigame_smelt_bonus: float = 0.0,
) -> dict[str, Any]:
    """把 ORM 行摊平成离线引擎的输入（扁平字典，见 core/offline_engine 文档）。"""
    silent_grass = 0
    halo: dict[str, float] = {}
    if garden is not None:
        # 在田光环（模块 H）：暂只统计"消音绒草"株数，其余光环由 garden_service 落地
        silent_grass = sum(
            1
            for tile in (garden.grid_data or [])
            if tile.get("seed_id") == "silent_grass" and tile.get("stage") != "WITHERED"
        )
        halo = garden_halo(garden)
    return {
        "now": now if now is not None else now_timestamp(),
        "decoy_count": int(military.decoy_count) if military else 0,
        "security_policy": dict(military.security_policy) if military and military.security_policy else {},
        "false_alarm_cooldown_until": (
            (military.security_policy or {}).get("false_alarm_cooldown_until") if military else None
        ),
        "go_dark": bool((military.security_policy or {}).get("go_dark", False)) if military else False,
        "idle_vehicles": int(idle_vehicles),
        "catnip": colony.catnip,
        "catnip_max": colony.catnip_max,
        "scrap": colony.scrap,
        "scrap_max": colony.scrap_max,
        "chips": colony.chips,
        "chips_max": colony.chips_max,
        "alloys": colony.alloys,
        "alloys_max": colony.alloys_max,
        "battery": colony.battery,
        "battery_max": colony.battery_max,
        "lube": colony.lube,
        "lube_max": colony.lube_max,
        "cats_total": colony.total_cats,
        # 承载力：星球系数 × (1 + 政令【行星绿化法案】 + 生态塑形师 +8%/只)
        "max_cat_capacity": B.cat_capacity_on(
            facilities,
            colony.planet_id or 0,
            extra_multiplier=1.0
            + float((doctrine_effects or {}).get("cat_capacity", 0.0))
            + float((star_jobs or {}).get("cat_capacity", 0.0)),
        ),
        "birth_progress": colony.birth_progress,
        "farmers": int(labor.get("farmer", 0)),
        "scavengers": int(labor.get("scavenger", 0)),
        "geeks": int(labor.get("geek", 0)),
        "power_runners": int(labor.get("power_runner", 0)),
        "facilities": dict(facilities),
        "battery_kwh": colony.battery_kwh,
        "battery_kwh_max": colony.battery_kwh_max,
        "suspicion": colony.suspicion,
        # 生产效率：士气系数 × (1 + 政令【下午三点晒太阳协议】 + 呼噜大师士气 +2%/只)
        "production_multiplier": production_multiplier
        * (
            1.0
            + float((doctrine_effects or {}).get("production_multiplier", 0.0))
            + float((doctrine_effects or {}).get("morale", 0.0))
            + float((star_jobs or {}).get("morale", 0.0))
        )
        * float(planet_cycle_factor),  # 星球周期（§15.5）：碎星流 +50% / 极夜 +10% 等
        "catnip_efficiency": max(0.0, float(catnip_efficiency)),
        "planet_catnip_multiplier": B.planet_catnip_multiplier(colony.planet_id),
        # 产出系数 = 星球系数 + 专属设施加成（**相加不相乘**，§15.4）
        "planet_scrap_multiplier": B.planet_output_multiplier(colony.planet_id, "scrap")
        + _exclusive_output_bonus(facilities, "scrap"),
        "planet_chips_multiplier": B.planet_output_multiplier(colony.planet_id, "chips")
        + _exclusive_output_bonus(facilities, "chips"),
        # 星球周期（§15.5）：太阳能出力与产出的当期系数（确定性，只依赖绝对时间）
        "solar_multiplier": float(solar_multiplier),
        # 高频感应电炉座数（《数值平衡表》§3.5）：电力侧按 −10 kW/座 计，熔炼循环下一步接
        "induction_furnaces": int(facilities.get("induction_furnace", 0)),
        # 熔炼速度加成（§3.5）：科技 smelt_speed + 小游戏配方加成，相加后统一乘在炉次速率上
        # 熔炼速度 = 1 + 科技 + 小游戏配方 + 专属设施（§3.5 / §15.4，全部相加）
        "smelt_speed_multiplier": 1.0
        + float((tech_effects or {}).get("smelt_speed", 0.0))
        + float(minigame_smelt_bonus)
        + _exclusive_smelt_bonus(facilities),
        "breeding_rate_multiplier": B.breeding_rate_multiplier(facilities),
        # 警戒度增速：政令【午睡静默令】为负值（−20% ⇒ ×0.8）
        "suspicion_growth_multiplier": 1.0
        + float((doctrine_effects or {}).get("suspicion_growth", 0.0)),
        "silent_grass_count": silent_grass,
        "garden_power_kw": float(halo.get("power_kw", 0.0)),
        "tech_power_kw": max(0.0, float(tech_power_kw)),
        "garden_suspicion_per_sec": float(halo.get("suspicion_per_sec", 0.0)),
        "expedition_active": False,
    }


def _persist_report(
    colony: ColonyState,
    labor: Mapping[str, int],
    report: Mapping[str, Any],
    *,
    now: int,
) -> None:
    """把引擎终值写回 colony_state；时间回拨只重置锚点、不做补偿。"""
    if report.get("clock_anomaly"):
        colony.last_tick_time = now
        logger.warning(
            "CLOCK_ROLLBACK slot=%s planet=%s 时间回拨：last_tick_time 重置为 %s，不做补偿",
            colony.slot_id,
            colony.planet_id,
            now,
        )
        return

    final = report["final"]
    colony.catnip = round(float(final["catnip"]), RESOURCE_PRECISION)
    colony.scrap = round(float(final["scrap"]), RESOURCE_PRECISION)
    colony.chips = round(float(final["chips"]), RESOURCE_PRECISION)
    colony.alloys = round(float(final["alloys"]), RESOURCE_PRECISION)
    colony.battery = round(float(final["battery"]), RESOURCE_PRECISION)
    colony.lube = round(float(final["lube"]), RESOURCE_PRECISION)
    colony.total_cats = int(final["cats_total"])
    colony.birth_progress = round(float(final["birth_progress"]), PROGRESS_PRECISION)
    colony.battery_kwh = round(float(final["battery_kwh"]), RESOURCE_PRECISION)
    colony.suspicion = round(float(final["suspicion"]), PROGRESS_PRECISION)
    colony.power_net = float(report["power"]["net_kw"])
    colony.job_idle = max(0, colony.total_cats - sum(int(v) for v in labor.values()))
    colony.last_tick_time = now


def _persist_security(military: MilitaryState | None, report: Mapping[str, Any]) -> None:
    """把安防处置结果写回 military_state（诱饵库存是真列，冷却/静默状态进 security_policy JSON）。"""
    if military is None:
        return
    policy = dict(military.security_policy or {})
    if report.get("decoy_count") is not None:
        military.decoy_count = int(report["decoy_count"])
    if "false_alarm_cooldown_until" in report:
        policy["false_alarm_cooldown_until"] = report.get("false_alarm_cooldown_until")
    if "go_dark" in report:
        policy["go_dark"] = bool(report.get("go_dark"))
    military.security_policy = policy


async def _accumulate_career_stats(
    session: AsyncSession,
    slot_id: int,
    report: Mapping[str, Any],
    *,
    elapsed_seconds: float,
) -> None:
    career = await session.get(CareerStats, slot_id)
    if career is None:
        return
    gained = report.get("gained_resources", {})
    career.playtime_seconds += max(0, int(elapsed_seconds))
    career.total_catnip += max(0.0, float(gained.get("catnip", 0.0)))
    career.total_scrap += max(0.0, float(gained.get("scrap", 0.0)))
    career.total_cats_born += max(0, int(report.get("gained_cats", 0)))


async def settle_offline(
    session: AsyncSession,
    save: SaveSlot,
    planet_id: int,
    *,
    now: int | None = None,
) -> tuple[dict[str, Any], ColonyState, dict[str, int], dict[str, int]]:
    """按 `last_tick_time` 与当前时间之差补算，并落库。

    返回 (离线报表, colony_state 行, 设施等级, 工种分桶)。
    """
    now = now if now is not None else now_timestamp()
    colony = await session.get(ColonyState, (save.slot_id, planet_id))
    if colony is None:
        raise NotFound("COLONY_STATE_NOT_FOUND", f"槽位 {save.slot_id} 星球 {planet_id} 无基地状态")

    await sync_facility_rows(session, save.slot_id, planet_id)
    await sync_labor_rows(session, save.slot_id, planet_id)
    facilities = await get_facility_levels(session, save.slot_id, planet_id)
    labor = await get_labor_counts(session, save.slot_id, planet_id)
    garden = await session.get(GardenState, (save.slot_id, planet_id))

    military = await get_military(session, save.slot_id, planet_id)
    idle_vehicles = await count_idle_vehicles(session, save.slot_id, planet_id)
    tech_effects = await get_tech_effects(session, save.slot_id, planet_id)
    minigame_smelt_bonus = await get_smelt_speed_bonus(session, save.slot_id)
    doctrine_effects = await get_doctrine_effects(session, save.slot_id)
    star_jobs = star_job_bonuses(labor)
    # 星球周期（§15.5）：按**绝对时间**取当期阶段（确定性，无新表）
    cycle = B.planet_cycle(planet_id, now)
    # 离线时长在结算前就要用（周期加权、报表 note），先算出来
    delta_seconds = now - int(colony.last_tick_time)
    # 离线区间可能跨很多轮相位 ⇒ 用区间**时间加权平均**，而不是拿结算时点的相位乘整段
    cycle_avg = B.planet_cycle_average(planet_id, now - int(max(0, delta_seconds)), now)
    # 蓄电池电容池 = 基础值 + 科技扩容（每次结算重算一遍，幂等、不累加）
    colony.battery_kwh_max = B.BATTERY_KWH_MAX + float(tech_effects.get("battery_kwh_max", 0.0))
    engine_state = build_engine_state(
        colony,
        labor,
        facilities,
        garden=garden,
        military=military,
        now=now,
        idle_vehicles=idle_vehicles,
        catnip_efficiency=tech_effects.get("catnip_efficiency", 0.0),
        tech_power_kw=tech_effects.get("power_kw", 0.0),
        tech_effects=tech_effects,
        minigame_smelt_bonus=minigame_smelt_bonus,
        doctrine_effects=doctrine_effects,
        star_jobs=star_jobs,
        planet_cycle_factor=float(cycle_avg["production_multiplier"]),
        solar_multiplier=float(cycle_avg["solar_multiplier"]),
    )
    report = calculate_offline_progress(engine_state, delta_seconds)
    # 跨了至少一整轮才值得提：告诉玩家"离线期间这颗星换了几次潮"，并给出区间平均值
    cycle_period = int(cycle.get("period", 0) or 0)
    if cycle["name"] and cycle_period > 0 and delta_seconds >= cycle_period:
        rounds = int(delta_seconds // cycle_period)
        swing: list[str] = []
        if abs(cycle_avg["solar_multiplier"] - 1.0) > 1e-9:
            swing.append(f"发电 ×{cycle_avg['solar_multiplier']:.2f}")
        if abs(cycle_avg["production_multiplier"] - 1.0) > 1e-9:
            swing.append(f"产出 ×{cycle_avg['production_multiplier']:.2f}")
        avg_hint = f"区间平均 {' / '.join(swing)}" if swing else "区间平均为中性，高低相位互相抵消"
        report["notes"].insert(
            0,
            f"离线期间经历 {rounds} 轮【{cycle['name']}】，当前处于{cycle['label']}（{avg_hint}）",
        )

    _persist_report(colony, labor, report, now=now)
    _persist_security(military, report)
    # 军备时间轴（模块 G）：维修到期回库、急救舱休养归队
    military_events = await combat_service.advance_military(
        session, slot_id=save.slot_id, planet_id=planet_id, now=now
    )
    if military_events["events"]:
        report.setdefault("military_events", []).extend(military_events["events"])
        for line in military_events["events"]:
            report["notes"].append(line)
    # 满警戒度：有闲置载具 ⇒ 真打一场战车截杀（§8.3 优先级 2），替换占位的 P2_PENDING
    if any(event.get("type") == "P2_PENDING" for event in report.get("security_events", [])):
        catnip_ratio = (
            float(colony.catnip) / float(colony.catnip_max) if float(colony.catnip_max or 0) > 0 else 1.0
        )
        intercept = await combat_service.intercept_alert(
            session, slot_id=save.slot_id, planet_id=planet_id, catnip_ratio=catnip_ratio
        )
        if intercept is not None:
            report["intercept"] = intercept
            report["notes"].append(
                "战车截杀："
                + ("击退侦察扫地机，警报解除" if intercept["won"] else "载具受损，乘员已进急救舱（绝无死猫）")
            )
    # 水培实验室离线生长（模块 H）：生长 / 枯萎 / 相邻突变 / 机械臂自动收割
    garden_event = await garden_service.advance_garden(
        session,
        slot_id=save.slot_id,
        planet_id=planet_id,
        seconds=float(max(0, delta_seconds)),
        now=now,
    )
    if garden_event["events"] or garden_event["harvests"]:
        report.setdefault("garden_events", []).extend(garden_event["events"])
        for line in garden_event["events"]:
            report["notes"].append(line)
        if garden_event["harvests"]:
            report["notes"].append(f"机械臂自动收割 {garden_event['harvests']} 株成熟作物")
    # 深网离线推进（模块 I）：行情 tick / 做空到期 / 黑市空投送达
    darknet_event = await darknet_service.advance_darknet(
        session,
        slot_id=save.slot_id,
        planet_id=planet_id,
        seconds=float(max(0, delta_seconds)),
        now=now,
    )
    if darknet_event["events"]:
        report.setdefault("darknet_events", []).extend(darknet_event["events"])
        for line in darknet_event["events"]:
            report["notes"].append(line)
    # 欧米茄后台演化与掠夺突袭（模块 L）：增兵 / 威胁升级 / 突袭到期惩罚
    boss_event = await boss_service.advance_boss(
        session,
        slot_id=save.slot_id,
        planet_id=planet_id,
        seconds=float(max(0, delta_seconds)),
        now=now,
    )
    if boss_event["events"]:
        report.setdefault("boss_events", []).extend(boss_event["events"])
        for line in boss_event["events"]:
            report["notes"].append(line)
    save.playtime_seconds += max(0, int(delta_seconds))
    await _accumulate_career_stats(
        session, save.slot_id, report, elapsed_seconds=max(0, delta_seconds)
    )
    # 研究算力一次性注入当前在研科技节点（模块 E：极客猫 → 科技解锁）
    research_event = await tech_service.accumulate_research(
        session,
        slot_id=save.slot_id,
        planet_id=planet_id,
        points=float(report.get("gained_research", 0.0)),
    )
    if research_event:
        report["research"] = research_event
    # 文明凝聚力（§15.1）：本星球在岗的【文明呼噜大师】每只 +0.02/s（升空后可派，见 star_jobs_unlocked）
    purr_masters = int(labor.get("purr_master", 0))
    if purr_masters and delta_seconds > 0:
        gained_unity = purr_masters * B.UNITY_PER_PURR_MASTER_PER_SEC * float(max(0, delta_seconds))
        save.unity = round(float(save.unity) + gained_unity, 2)
        report["gained_unity"] = round(gained_unity, 2)
    await session.flush()
    return report, colony, facilities, labor


# ----------------------------------------------------------------------
# 状态载荷（GET /colony/state）
# ----------------------------------------------------------------------
def build_state_payload(
    save: SaveSlot,
    colony: ColonyState,
    facilities: Mapping[str, int],
    labor: Mapping[str, int],
    report: Mapping[str, Any],
    *,
    hangar_capacity: int,
    military: MilitaryState | None = None,
    garden_power_kw: float = 0.0,
    tech_effects: Mapping[str, float] | None = None,
    fortress_down: bool = False,
    star_jobs_ready: bool = False,
    capacity_multiplier: float = 1.0,
    now: int,
) -> dict[str, Any]:
    cooldown_until = int(report.get("false_alarm_cooldown_until") or 0)
    go_dark = bool(report.get("go_dark", False))
    policy = dict(military.security_policy or {}) if military else {}
    silo_level = int(facilities.get("launch_silo", 0))
    silo_stages = [
        {
            "stage": int(stage["stage"]),
            "name": stage["name"],
            "cost": dict(stage["cost"]),
            "done": silo_level >= int(stage["stage"]),
            "blocked": bool(
                B.LAUNCH_SILO_FINAL_STAGE_REQUIRES_FORTRESS
                and int(stage["stage"]) == len(B.LAUNCH_SILO_STAGES)
                and not fortress_down
            ),
        }
        for stage in B.LAUNCH_SILO_STAGES
    ]
    limits = B.workstation_limits(
        facilities,
        hangar_capacity=hangar_capacity,
        star_jobs_unlocked=star_jobs_ready,
    )
    power = power_balance(
        facilities,
        {"power_runner": labor.get("power_runner", 0)},
        colony.total_cats,
        garden_power_kw=garden_power_kw,
        tech_power_kw=float((tech_effects or {}).get("power_kw", 0.0)),
        induction_furnaces=int(facilities.get("induction_furnace", 0)),
    )
    return {
        "slot_id": save.slot_id,
        "planet_id": colony.planet_id,
        "last_tick_time": int(colony.last_tick_time),
        "saved_at": now,
        "resources": {
            "catnip": round(colony.catnip, RESOURCE_PRECISION),
            "scrap": round(colony.scrap, RESOURCE_PRECISION),
            "chips": round(colony.chips, RESOURCE_PRECISION),
            "alloys": round(colony.alloys, RESOURCE_PRECISION),
            "battery": round(colony.battery, RESOURCE_PRECISION),
            "lube": round(colony.lube, RESOURCE_PRECISION),
            "caps": {
                "catnip": colony.catnip_max,
                "scrap": colony.scrap_max,
                "chips": colony.chips_max,
                "alloys": colony.alloys_max,
                "battery": colony.battery_max,
                "lube": colony.lube_max,
            },
        },
        "power": {
            "gen_kw": power["gen_kw"],
            "load_kw": power["load_kw"],
            "net_kw": power["net_kw"],
            "battery_kwh": round(colony.battery_kwh, RESOURCE_PRECISION),
            "battery_kwh_max": colony.battery_kwh_max,
            "blackout": power["blackout"],
        },
        "population": {
            "total": colony.total_cats,
            "max_cap": B.cat_capacity_on(
                facilities, colony.planet_id or 0, extra_multiplier=capacity_multiplier
            ),
            "unassigned": colony.job_idle,
            "birth_progress": round(colony.birth_progress, PROGRESS_PRECISION),
        },
        "workstations": {job_id: int(labor.get(job_id, 0)) for job_id in (*B.PLANET_JOBS, *B.STAR_JOBS)},
        "workstation_limits": limits,
        "facilities": {facility_id: int(level) for facility_id, level in facilities.items()},
        # 显式转 float：SQLite 会把手写 0 存成整数，round() 也就跟着返回 int，JSON 里就成了 `0`
        "suspicion": {"current": round(float(colony.suspicion), PROGRESS_PRECISION), "max": B.SUSPICION_MAX},
        "tech_effects": {
            key: round(float(value), 4) for key, value in (tech_effects or {}).items()
        },
        "launch_silo": {
            "level": silo_level,
            "max_level": len(B.LAUNCH_SILO_STAGES),
            "stages": silo_stages,
            "next_stage": silo_stages[silo_level] if silo_level < len(silo_stages) else None,
            "can_advance": bool(silo_level < len(silo_stages) and not silo_stages[silo_level]["blocked"]),
            "fortress_down": fortress_down,
            "launched": silo_level >= len(B.LAUNCH_SILO_STAGES),
        },
        "security": {
            "decoy_count": int(military.decoy_count) if military else 0,
            "cooldown_until": cooldown_until or None,
            "cooldown_left_seconds": max(0, cooldown_until - now) if cooldown_until else 0,
            "go_dark": go_dark,
            "policy": {
                "p1_use_decoy": bool(policy.get("p1_use_decoy", True)),
                "p2_use_vehicle": bool(policy.get("p2_use_vehicle", True)),
                "p3_go_dark": bool(policy.get("p3_go_dark", True)),
            },
        },
        "offline_report": build_report_summary(report),
    }


async def load_state(
    session: AsyncSession,
    *,
    slot_id: int = B.DEFAULT_SLOT_ID,
    planet_id: int | None = None,
    create_if_missing: bool = True,
) -> dict[str, Any]:
    """读档：离线补算 → 落库 → 返回完整状态（代码结构稿 §4.3 样例结构）。"""
    if slot_id not in B.SLOT_IDS:
        raise BadRequest("BAD_REQUEST", f"slot 必须为 {B.SLOT_IDS} 之一，收到 {slot_id}")

    save = await session.get(SaveSlot, slot_id)
    if save is None:
        if not create_if_missing:
            raise NotFound("SAVE_NOT_FOUND", f"槽位 {slot_id} 还没有存档")
        save = await create_new_game(session, slot_id)

    target_planet = save.active_planet_id if planet_id is None else planet_id
    planet = await session.get(PlanetState, (slot_id, target_planet))
    if planet is None:
        raise BadRequest("BAD_REQUEST", f"未知星球 planet_id={target_planet}")
    if not planet.unlocked:
        raise BadRequest("PLANET_LOCKED", f"星球 {target_planet} 尚未解锁")
    # 自愈：活跃星球若还没有基地（切到外星球后星级内容未落地），读档回落到母星，
    # 否则玩家会卡在"读档 404"的死循环里（真机踩到过）。显式指定 planet_id 时不回落，让调用方拿到明确错误。
    if planet_id is None and await session.get(ColonyState, (slot_id, target_planet)) is None:
        logger.warning(
            "活跃星球 %s 尚无基地，读档回落到母星（slot=%s）", target_planet, slot_id
        )
        target_planet = B.HOME_PLANET_ID
        save.active_planet_id = target_planet

    now = now_timestamp()
    # 在途航线到点即交付（§15.3 第②步）：读档时一并结算，避免"必须打开星图才到货"
    from app.services import planet_service

    route_events = await planet_service.settle_routes(session, save.slot_id, now=now)
    report, colony, facilities, labor = await settle_offline(session, save, target_planet, now=now)
    # 航线到货 / 被劫掠也在"玩家不在场时发生"，一并进《离线休整报表》（§15.6）
    for line in planet_service.describe_route_events(route_events):
        report["notes"].append(line)
    hangar = await get_hangar_capacity(session, slot_id, target_planet)
    military = await get_military(session, slot_id, target_planet)
    boss_row = await session.get(BossState, slot_id)
    fortress_down = bool((boss_row.bombardment_state or {}).get("fortress_destroyed_at")) if boss_row else False
    payload = build_state_payload(
        save,
        colony,
        facilities,
        labor,
        report,
        hangar_capacity=hangar,
        military=military,
        garden_power_kw=await get_garden_power_kw(session, slot_id, target_planet),
        fortress_down=fortress_down,
        now=now,
        tech_effects=await get_tech_effects(session, slot_id, target_planet),
        star_jobs_ready=await star_jobs_unlocked(session, slot_id),
        capacity_multiplier=1.0
        + float((await get_doctrine_effects(session, slot_id)).get("cat_capacity", 0.0))
        + float(star_job_bonuses(labor).get("cat_capacity", 0.0)),
    )
    await session.commit()
    return payload


# ----------------------------------------------------------------------
# 快照对账（POST /colony/snapshot）
# ----------------------------------------------------------------------
def _deviation(client_value: float, backend_value: float) -> float:
    """相对偏差；后端值为 0 时用 1.0 做分母，避免除零。"""
    return abs(float(client_value) - float(backend_value)) / max(1.0, abs(float(backend_value)))


def compare_snapshot_payload(
    payload: SnapshotRequest,
    colony: ColonyState,
    facilities: Mapping[str, int],
    labor: Mapping[str, int],
    *,
    hangar_capacity: int,
    tolerance: float,
    star_jobs_ready: bool = False,
) -> list[str]:
    """前端预测值 vs 后端重算值；偏差 > 0.5% 记 SNAPSHOT_DRIFT warning。"""
    warnings: list[str] = []

    for key in B.RESOURCE_KEYS:
        if key not in payload.resources:
            continue
        backend_value = float(getattr(colony, key))
        deviation = _deviation(payload.resources[key], backend_value)
        if deviation > tolerance:
            warnings.append(
                f"SNAPSHOT_DRIFT resource={key} client={payload.resources[key]} "
                f"backend={backend_value} deviation={deviation:.4%}"
            )

    claimed_total = payload.population.get("total")
    if claimed_total is not None:
        deviation = _deviation(claimed_total, colony.total_cats)
        if deviation > tolerance:
            warnings.append(
                f"SNAPSHOT_DRIFT population.total client={claimed_total} "
                f"backend={colony.total_cats} deviation={deviation:.4%}"
            )

    limits = B.workstation_limits(
        facilities,
        hangar_capacity=hangar_capacity,
        star_jobs_unlocked=star_jobs_ready,
    )
    if payload.workstations:
        violations = workstation_violations(limits, payload.workstations)
        for job_id, detail in violations.items():
            warnings.append(f"SNAPSHOT_WORKSTATION_OVERFLOW {detail}")
        for job_id, count in payload.workstations.items():
            if job_id in violations:
                continue
            backend_count = int(labor.get(job_id, 0))
            if int(count) != backend_count:
                warnings.append(
                    f"SNAPSHOT_DRIFT workstation={job_id} client={count} backend={backend_count}"
                )
    return warnings


async def persist_snapshot(
    session: AsyncSession, payload: SnapshotRequest
) -> tuple[dict[str, Any], list[str]]:
    """15 秒静默快照：后端重算并落库，返回响应体与对账 warning 列表。"""
    if payload.slot not in B.SLOT_IDS:
        raise BadRequest("BAD_REQUEST", f"slot 必须为 {B.SLOT_IDS} 之一，收到 {payload.slot}")
    save = await get_save(session, payload.slot)
    planet_id = save.active_planet_id if payload.planet_id is None else payload.planet_id
    planet = await session.get(PlanetState, (payload.slot, planet_id))
    if planet is None:
        raise BadRequest("BAD_REQUEST", f"未知星球 planet_id={planet_id}")

    now = now_timestamp()
    report, colony, facilities, labor = await settle_offline(session, save, planet_id, now=now)
    hangar = await get_hangar_capacity(session, payload.slot, planet_id)
    warnings = compare_snapshot_payload(
        payload,
        colony,
        facilities,
        labor,
        hangar_capacity=hangar,
        tolerance=get_settings().snapshot_drift_tolerance,
        star_jobs_ready=await star_jobs_unlocked(session, payload.slot),
    )
    for warning in warnings:
        logger.warning("%s slot=%s planet=%s", warning, payload.slot, planet_id)

    await session.commit()
    return {"code": 200, "message": "SNAPSHOT_PERSISTED", "saved_at": now}, warnings


# ----------------------------------------------------------------------
# 冷启动：手点废墟 → 第一座纸箱窝 → 第一只猫（GDD §1.3）
# ----------------------------------------------------------------------
async def _resolve_target(
    session: AsyncSession, slot_id: int, planet_id: int | None
) -> tuple[SaveSlot, int, PlanetState]:
    """定位存档与目标星球（含解锁校验）。"""
    if slot_id not in B.SLOT_IDS:
        raise BadRequest("BAD_REQUEST", f"slot 必须为 {B.SLOT_IDS} 之一，收到 {slot_id}")
    save = await get_save(session, slot_id)
    target_planet = save.active_planet_id if planet_id is None else planet_id
    planet = await session.get(PlanetState, (slot_id, target_planet))
    if planet is None:
        raise BadRequest("BAD_REQUEST", f"未知星球 planet_id={target_planet}")
    if not planet.unlocked:
        raise BadRequest("PLANET_LOCKED", f"星球 {target_planet} 尚未解锁")
    return save, target_planet, planet


async def manual_scavenge(
    session: AsyncSession,
    *,
    slot_id: int = B.DEFAULT_SLOT_ID,
    planet_id: int | None = None,
) -> dict[str, Any]:
    """手点废墟：+1 机械废铁（冷启动专属动作）。

    口径（数值平衡表 §3.4）：
    * 每次 +1 废铁，5 次即够造第一座纸箱窝（GDD §1.3 阶段一）；
    * 总次数上限 40 次（够造 窝 5 + 农田 10 + 操作台 8 + 第二座窝 6，还留 11 次余量），防无限手点；
    * 一旦有拾荒猫在岗（自动化真正跑起来）入口即关闭；没猫上工时保留这条兜底路径（防死档）。
    """
    save, target_planet, _planet = await _resolve_target(session, slot_id, planet_id)
    # 手点废墟是**母星避难所专属**的冷启动兜底（§3.4：通风管道尽头的地表建筑废墟）。
    # 外星球没有这条路：星际扩张的代价就是"从母星把物资运过去"（§15.3），
    # 否则玩家在外星球点 40 次就能绕过整套跨星物流（带宽/调度官/被劫掠全都失去意义）。
    if target_planet != B.HOME_PLANET_ID:
        raise BadRequest(
            "COLD_START_HOME_ONLY",
            "手点废墟只适用于母星避难所；外星球请走跨星航线运物资（星区星图 → 迁猫时随船带货）",
        )
    _report, colony, facilities, labor = await settle_offline(session, save, target_planet)

    if int(labor.get(B.COLD_START_EXIT_ON_DUTY_JOB, 0)) > 0:
        raise BadRequest(
            "COLD_START_FINISHED",
            "拾荒猫已接管废土回收线，手点废墟入口已关闭（拾荒猫会自动产废铁）",
        )
    if colony.manual_scavenge_clicks >= B.COLD_START_MAX_MANUAL_CLICKS:
        raise BadRequest(
            "COLD_START_EXHAUSTED",
            f"手点废墟已达上限 {B.COLD_START_MAX_MANUAL_CLICKS} 次，"
            f"快去造【{B.FACILITY_SPECS['scavenge_station']['name']}】并派拾荒猫上工",
        )
    if colony.scrap >= colony.scrap_max:
        raise BadRequest(
            "STORAGE_FULL", f"机械废铁已爆仓（{colony.scrap:g}/{colony.scrap_max:g}），先花掉一些再来翻"
        )

    colony.scrap = round(min(colony.scrap + B.COLD_START_SCRAP_PER_CLICK, colony.scrap_max), RESOURCE_PRECISION)
    colony.manual_scavenge_clicks += 1
    career = await session.get(CareerStats, slot_id)
    if career is not None:
        career.total_scrap += B.COLD_START_SCRAP_PER_CLICK
    await session.commit()

    clicks_left = max(0, B.COLD_START_MAX_MANUAL_CLICKS - colony.manual_scavenge_clicks)
    hint = None
    if colony.scrap >= B.COLD_START_HOUSING_BOX_COST and int(facilities.get("housing_box", 0)) == 0:
        hint = f"废铁够了！去造【{B.FACILITY_SPECS['housing_box']['name']}】（{B.COLD_START_HOUSING_BOX_COST:g} 废铁）"
    return {
        "scrap": round(colony.scrap, RESOURCE_PRECISION),
        "scrap_max": colony.scrap_max,
        "manual_scavenge_clicks": colony.manual_scavenge_clicks,
        "clicks_left": clicks_left,
        "cold_start_finished": False,
        "hint": hint,
    }


# ----------------------------------------------------------------------
# 工位调度（POST /colony/dispatch，模块 C2 / C3）
# ----------------------------------------------------------------------
async def dispatch_labor(
    session: AsyncSession,
    *,
    role: str,
    delta: int,
    slot_id: int = B.DEFAULT_SLOT_ID,
    planet_id: int | None = None,
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """调配工位分桶：受"工位上限"与"空闲猫口"双重硬校验（验收 C2 / D-2）。

    `delta = 0` 仅在同时提交 `policy` 时合法，用于"只改迟滞换班策略、不动工位"。
    """
    save, target_planet, _planet = await _resolve_target(session, slot_id, planet_id)
    _report, colony, facilities, labor = await settle_offline(session, save, target_planet)

    if role not in (*B.PLANET_JOBS, *B.STAR_JOBS):
        raise BadRequest("BAD_REQUEST", f"未知工种 role={role}")

    hangar = await get_hangar_capacity(session, slot_id, target_planet)
    limits = B.workstation_limits(
        facilities, hangar_capacity=hangar, star_jobs_unlocked=await star_jobs_unlocked(session, slot_id)
    )
    current = int(labor.get(role, 0))
    new_count = current + int(delta)

    # delta = 0 只允许与 policy 同时提交：仅更新迟滞换班策略，不改动工位分桶
    if delta == 0:
        if policy is None:
            raise BadRequest("BAD_REQUEST", "delta 不能为 0（仅提交 policy 时才允许 0）")
        colony.labor_automation_policy = dict(policy)
        power = power_balance(facilities, {"power_runner": labor.get("power_runner", 0)}, colony.total_cats)
        colony.power_net = power["net_kw"]
        await session.commit()
        return {
            "role": role,
            "count": current,
            "unassigned": colony.job_idle,
            "total_cats": colony.total_cats,
            "workstations": {
                job_id: int(labor.get(job_id, 0)) for job_id in (*B.PLANET_JOBS, *B.STAR_JOBS)
            },
            "workstation_limits": limits,
            "power_net_kw": power["net_kw"],
            "policy": colony.labor_automation_policy,
        }

    if new_count < 0:
        raise BadRequest("BAD_REQUEST", f"{role} 工位数不能为负（当前 {current}）")
    if new_count > limits[role]:
        raise WorkstationLimitExceeded(
            detail=f"{role}: 目标 {new_count} 只 > 工位上限 {limits[role]}"
            f"（工位来自设施，见 facilities.json）"
        )
    others = sum(int(count) for job_id, count in labor.items() if job_id != role)
    if others + new_count > colony.total_cats:
        raise WorkstationLimitExceeded(
            detail=f"无空闲猫口：总猫口 {colony.total_cats}，其他工种已占 {others}，"
            f"{role} 最多可上 {max(0, colony.total_cats - others)} 只"
        )

    bucket = await session.get(LaborBucket, (slot_id, target_planet, role))
    if bucket is None:
        bucket = LaborBucket(slot_id=slot_id, planet_id=target_planet, job_id=role, cat_count=new_count)
        session.add(bucket)
    else:
        bucket.cat_count = new_count
    labor[role] = new_count

    if policy is not None:
        colony.labor_automation_policy = dict(policy)
    colony.job_idle = max(0, colony.total_cats - sum(int(count) for count in labor.values()))
    power = power_balance(
        facilities,
        {"power_runner": labor.get("power_runner", 0)},
        colony.total_cats,
        garden_power_kw=await get_garden_power_kw(session, slot_id, target_planet),
    )
    colony.power_net = power["net_kw"]
    await session.commit()

    return {
        "role": role,
        "count": new_count,
        "unassigned": colony.job_idle,
        "total_cats": colony.total_cats,
        "workstations": {job_id: int(labor.get(job_id, 0)) for job_id in (*B.PLANET_JOBS, *B.STAR_JOBS)},
        "workstation_limits": limits,
        "power_net_kw": power["net_kw"],
        "policy": colony.labor_automation_policy,
    }


# ----------------------------------------------------------------------
# 设施建造（POST /facilities/build，模块 D）
# ----------------------------------------------------------------------
async def build_facility(
    session: AsyncSession,
    *,
    facility_id: str,
    count: int = 1,
    slot_id: int = B.DEFAULT_SLOT_ID,
    planet_id: int | None = None,
) -> dict[str, Any]:
    """建造 / 升级设施：扣费 = 基础造价 × 递增系数^(n-1)（验收 D-1）。

    冷启动特例：第一座【瓦楞纸箱窝】建成时，第一只折耳流浪猫入驻（总猫口 0 → 1，GDD §1.3 阶段二）。
    阶段 3 暂不做科技硬门槛（否则冷启动死锁），`unlock_hint` 只作为前端提示；模块 E 接线后再启用校验。
    """
    save, target_planet, _planet = await _resolve_target(session, slot_id, planet_id)
    _report, colony, facilities, labor = await settle_offline(session, save, target_planet)

    if facility_id not in B.FACILITY_IDS:
        raise BadRequest("BAD_REQUEST", f"未知设施 facility_id={facility_id}")
    # 发射井是**母星巨构**（§5：从地下避难所造火箭、地表破晓）：外星球没有对应的地壳厚度与
    # 工程配套，若允许随地建会变成"每个星球各升空一次"的怪异状态（半截链路审计 v1.40 收口）。
    if facility_id == "launch_silo" and target_planet != B.HOME_PLANET_ID:
        raise BadRequest(
            "LAUNCH_SILO_HOME_ONLY",
            "火箭垂直发射井是母星巨构；外星球请专注产能与物流，升空只能在母星完成",
        )
    # 外星球专属设施（§15.4）：只有对应星球能建——它就是"这颗星球的地理天赋"。
    scoped_planet = B.facility_planet_scope(facility_id)
    if scoped_planet is not None and target_planet != scoped_planet:
        raise BadRequest(
            "FACILITY_PLANET_MISMATCH",
            f"【{B.FACILITY_SPECS[facility_id]['name']}】是【{B.PLANETS.get(scoped_planet, scoped_planet)}】的专属设施，"
            f"不能建在别处",
        )
    if not B.facility_is_buildable(facility_id):
        raise BadRequest(
            "BAD_REQUEST",
            f"【{B.FACILITY_SPECS[facility_id]['name']}】造价与阶段拆分尚未定稿，暂不开放建造入口",
        )
    if count <= 0:
        raise BadRequest("BAD_REQUEST", "count 必须为正整数")

    current_level = int(facilities.get(facility_id, 0))
    # 模块 E 解锁效果：进阶设施首次建造需要对应科技（开荒部件豁免，见 TECH_GATE_EXEMPT_FACILITIES）
    await tech_service.build_gate_check(
        session,
        facility_id,
        slot_id=slot_id,
        planet_id=target_planet,
        current_level=current_level,
    )
    max_level = B.facility_max_level(facility_id)
    if max_level is not None and current_level + count > max_level:
        raise Conflict(
            "FACILITY_MAX_LEVEL",
            f"【{B.FACILITY_SPECS[facility_id]['name']}】上限 {max_level} 级，当前 {current_level} 级",
        )

    unlocked_names: list[str] = []
    if facility_id == "launch_silo":
        # 发射井走"阶段式"造价（数值平衡表 §5 v1.11），一次推进一个阶段
        if count != 1:
            raise BadRequest("BAD_REQUEST", "发射井一次只能推进一个阶段")
        stage = B.LAUNCH_SILO_STAGES[current_level]
        if (
            B.LAUNCH_SILO_FINAL_STAGE_REQUIRES_FORTRESS
            and int(stage["stage"]) == len(B.LAUNCH_SILO_STAGES)
        ):
            boss_now = await session.get(BossState, slot_id)
            if not (boss_now and (boss_now.bombardment_state or {}).get("fortress_destroyed_at")):
                raise BadRequest(
                    "FORTRESS_INTACT",
                    "【点火总装】需要先摧毁近轨除菌要塞（造巡航导弹并点火发射）",
                )
        cost = {resource: float(amount) for resource, amount in stage["cost"].items()}
    else:
        cost = B.facility_upgrade_cost(facility_id, current_level, count)
    missing = {
        resource: round(need - float(getattr(colony, resource)), RESOURCE_PRECISION)
        for resource, need in cost.items()
        if float(getattr(colony, resource)) < need
    }
    if missing:
        raise InsufficientResource(
            detail="缺料：" + "、".join(f"{B.RESOURCE_LABELS.get(k, k)} 差 {v:g}" for k, v in missing.items())
        )

    for resource, need in cost.items():
        setattr(colony, resource, round(float(getattr(colony, resource)) - need, RESOURCE_PRECISION))

    row = await session.get(FacilityState, (slot_id, target_planet, facility_id))
    if row is None:
        row = FacilityState(
            slot_id=slot_id, planet_id=target_planet, facility_id=facility_id, level=current_level + count
        )
        session.add(row)
    else:
        row.level = current_level + count
    facilities[facility_id] = row.level

    narrative: str | None = None
    if (
        facility_id == "housing_box"
        and row.level >= 1
        and colony.total_cats == 0
        and B.FIRST_HOUSING_BOX_BRINGS_FIRST_CAT
    ):
        colony.total_cats = 1
        colony.job_idle = 1
        narrative = FIRST_CAT_NARRATIVE
    if facility_id == "battery_bank":
        # 科技扩容单独累加：建蓄电池组是"拿到基础容量"，科技是"在基础之上再加"
        effects = await get_tech_effects(session, slot_id, planet_id)
        colony.battery_kwh_max = B.BATTERY_KWH_MAX + float(effects.get("battery_kwh_max", 0.0))
    if facility_id == "launch_silo":
        if row.level < len(B.LAUNCH_SILO_STAGES):
            narrative = f"发射井阶段 {row.level}【{B.LAUNCH_SILO_STAGES[row.level - 1]['name']}】完成"
        else:
            # 升空：解锁星区星图（母星永不删档，仍作后勤大本营）
            for planet_id in B.LAUNCH_UNLOCKS_PLANET_IDS:
                planet_row = await session.get(PlanetState, (slot_id, planet_id))
                if planet_row is not None and not planet_row.unlocked:
                    planet_row.unlocked = True
                    planet_row.unlocked_at = now_timestamp()
                    unlocked_names.append(B.PLANETS.get(planet_id, str(planet_id)))
            narrative = "🚀 喵星一号点火升空！避难所全员脱离母星，星区星图已展开" + (
                f"（新开放：{'、'.join(unlocked_names)}）" if unlocked_names else ""
            )

    hangar = await get_hangar_capacity(session, slot_id, target_planet)
    limits = B.workstation_limits(
        facilities, hangar_capacity=hangar, star_jobs_unlocked=await star_jobs_unlocked(session, slot_id)
    )
    power = power_balance(
        facilities,
        {"power_runner": labor.get("power_runner", 0)},
        colony.total_cats,
        garden_power_kw=await get_garden_power_kw(session, slot_id, target_planet),
    )
    colony.power_net = power["net_kw"]
    colony.job_idle = max(0, colony.total_cats - sum(int(count_value) for count_value in labor.values()))
    await session.commit()

    definition = facility_def_map().get(facility_id, {})
    return {
        "facility_id": facility_id,
        "level": row.level,
        "count": count,
        "cost_paid": cost,
        "resources": {
            resource: round(float(getattr(colony, resource)), RESOURCE_PRECISION)
            for resource in B.RESOURCE_KEYS
        },
        "caps": {f"{resource}_max": float(getattr(colony, f"{resource}_max")) for resource in B.RESOURCE_KEYS},
        "total_cats": colony.total_cats,
        "unassigned": colony.job_idle,
        "cat_capacity": B.cat_capacity_on(facilities, colony.planet_id or 0),
        "workstation_limits": limits,
        "power_net_kw": power["net_kw"],
        "narrative": narrative,
        "unlocked_planets": unlocked_names,
        "unlock_hint": definition.get("unlock"),
    }


__all__: Sequence[str] = (
    "build_engine_state",
    "build_facility",
    "build_state_payload",
    "compare_snapshot_payload",
    "dispatch_labor",
    "ensure_workstation_capacity",
    "get_facility_levels",
    "get_labor_counts",
    "get_save",
    "load_state",
    "manual_scavenge",
    "persist_snapshot",
    "settle_offline",
    "sync_facility_rows",
    "sync_labor_rows",
    "workstation_violations",
)
