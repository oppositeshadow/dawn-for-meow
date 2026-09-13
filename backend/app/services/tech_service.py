"""科技树服务（模块 E）：DAG 研发推进、科研等级折扣、解锁效果与建造门槛。

口径来源：GDD §4（母星 19 节点）/ 数值平衡表 §6（算力成本与加速）/ 代码结构稿 §4.4。

* **防火墙 2**：开始研发前校验 `parent_ids` 全部 UNLOCKED（防断树），缺失即 `400 TECH_PREREQUISITE_MISSING`；
* **科研等级（Tech Tier）**：最高"整层解锁"的阶梯 + 1。Tier 1 全解锁 ⇒ 科研等级 2，后续按 8 折推进；
  Tier 2 全解锁 ⇒ 科研等级 3，按 6 折推进（折扣见 `TECH_TIER_TIME_DISCOUNT`）；
* 极客科研猫每秒产出的研究算力在**离线结算**时一次性注入当前在研节点（`accumulate_research`）；
* 解锁效果先落地**建造门槛**：进阶设施首次建造需要对应科技已解锁（开荒三件套与猫力滚轮免门槛，
  否则 GDD §1.3 的冷启动心流会死锁）。
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import balance as B
from app.core.errors import BadRequest, Conflict, InsufficientResource, NotFound
from app.models import LaborBucket, TechRecord
from app.models.tech import TechStatus

logger = logging.getLogger("dawn_meow.tech")

#: 首次建造免科技门槛的设施（冷启动开荒部件）
TECH_GATE_EXEMPT_FACILITIES: tuple[str, ...] = (
    "housing_box",
    "farm_plot",
    "scavenge_station",
    "power_wheel",
    # 图灵终端是"算力自动研发时代"的入口：GDD 把它写在第 7 号科技里解锁，
    # 但 Tier 1 科技的算力又只能由极客猫产出 —— 按"能玩通"原则裁决为开荒组件免门槛。
    "turing_terminal",
)

_reroll_cooldown: dict[tuple[int, int, str], float] = {}


def research_tier_level(records: Sequence[TechRecord]) -> int:
    """科研等级：最高"整层解锁"的阶梯 + 1（全未解锁时为 1）。"""
    by_tier: dict[int, list[TechRecord]] = {}
    for row in records:
        by_tier.setdefault(int(row.tier), []).append(row)
    level = 1
    for tier in sorted(by_tier):
        if tier != level:
            break
        if all(row.status == TechStatus.UNLOCKED for row in by_tier[tier]):
            level = tier + 1
        else:
            break
    return level


def tier_discount(tier: int) -> float:
    """节点所属阶梯的算力折扣（Tier 2 打 8 折、Tier 3 打 6 折）。"""
    return float(B.TECH_TIER_TIME_DISCOUNT.get(int(tier), 1.0))


def effective_cost(record: TechRecord) -> float:
    """应用科研折扣后的解锁所需算力。"""
    return float(max(1.0, math.ceil(float(record.target_cost) * tier_discount(record.tier))))


async def load_records(
    session: AsyncSession, slot_id: int, planet_id: int
) -> list[TechRecord]:
    rows = (
        await session.execute(
            select(TechRecord)
            .where(TechRecord.slot_id == slot_id, TechRecord.planet_id == planet_id)
            .order_by(TechRecord.tier, TechRecord.node_order)
        )
    ).scalars().all()
    if not rows:
        raise NotFound("TECH_TREE_NOT_FOUND", f"槽位 {slot_id} 星球 {planet_id} 没有科技记录")
    return list(rows)


async def geek_research_rate(session: AsyncSession, slot_id: int, planet_id: int) -> float:
    """当前每秒研究算力（极客科研猫数量 × 基础产出）。"""
    bucket = await session.get(LaborBucket, (slot_id, planet_id, "geek"))
    geeks = int(bucket.cat_count) if bucket else 0
    return round(geeks * B.GEEK_RESEARCH_PER_SEC, 4)


async def unlocked_effects(
    session: AsyncSession, slot_id: int, planet_id: int, *, keys: Sequence[str] = B.TECH_ACTIVE_EFFECT_KEYS
) -> dict[str, float]:
    """把已解锁科技 `buff_payload` 里**已接入结算**的数值字段汇总（模块 E5）。

    口径：只认 `B.TECH_ACTIVE_EFFECT_KEYS` 白名单里的键，其余键仍属声明性载荷（界面展示用），
    不参与任何结算 —— 避免"看起来有加成其实没接线"的假承诺。
    """
    rows = (
        await session.execute(
            select(TechRecord).where(
                TechRecord.slot_id == slot_id,
                TechRecord.planet_id == planet_id,
                TechRecord.status == TechStatus.UNLOCKED,
            )
        )
    ).scalars().all()
    totals = dict.fromkeys(keys, 0.0)
    for row in rows:
        payload = row.buff_payload or {}
        for key in keys:
            value = payload.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                totals[key] += float(value)
    if "catnip_efficiency" in totals:
        totals["catnip_efficiency"] = round(
            min(totals["catnip_efficiency"], B.TECH_CATNIP_EFFICIENCY_CAP), 4
        )
    return {key: round(value, 4) for key, value in totals.items()}


def node_view(
    record: TechRecord,
    *,
    unlocked_ids: set[str],
    researching_id: str | None,
) -> dict[str, Any]:
    parents = list(record.parent_ids or [])
    missing = [parent for parent in parents if parent not in unlocked_ids]
    # 如实标注"哪些载荷真的接线了"：白名单里的键计入结算，其余只是卡片命题（《数值平衡表》§6.4 三类载荷）
    payload = record.buff_payload or {}
    active_effects = {key: value for key, value in payload.items() if key in B.TECH_ACTIVE_EFFECT_KEYS}
    pending_effects = {
        key: value
        for key, value in payload.items()
        if key not in B.TECH_ACTIVE_EFFECT_KEYS
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    }
    return {
        "tech_id": record.tech_id,
        "tech_name": record.tech_name,
        "tier": int(record.tier),
        "node_order": int(record.node_order),
        "parent_ids": parents,
        "missing_parents": missing,
        "status": record.status.value if hasattr(record.status, "value") else str(record.status),
        "current_progress": round(float(record.current_progress), 2),
        "target_cost": float(record.target_cost),
        "display_cost": effective_cost(record),
        "discount": tier_discount(record.tier),
        "flavor_text": record.flavor_text,
        "mechanic_type": record.mechanic_type,
        "buff_payload": record.buff_payload,
        "active_effects": active_effects,
        "pending_effects": pending_effects,
        "is_agent_generated": bool(record.is_agent_generated),
        "available": bool(
            record.status == TechStatus.LOCKED
            and not missing
            and (researching_id is None or researching_id == record.tech_id)
        ),
    }


async def tree(
    session: AsyncSession, *, slot_id: int = B.DEFAULT_SLOT_ID, planet_id: int = B.HOME_PLANET_ID
) -> dict[str, Any]:
    """整棵科技树视图（前端面板用）。"""
    records = await load_records(session, slot_id, planet_id)
    unlocked_ids = {row.tech_id for row in records if row.status == TechStatus.UNLOCKED}
    researching = next((row for row in records if row.status == TechStatus.RESEARCHING), None)
    researching_id = researching.tech_id if researching else None

    nodes = [
        node_view(row, unlocked_ids=unlocked_ids, researching_id=researching_id) for row in records
    ]
    tiers: dict[str, int] = {}
    for row in records:
        tiers[str(int(row.tier))] = tiers.get(str(int(row.tier)), 0) + 1

    research_view: dict[str, Any] | None = None
    if researching is not None:
        cost = effective_cost(researching)
        rate = await geek_research_rate(session, slot_id, planet_id)
        remaining = max(0.0, cost - float(researching.current_progress))
        research_view = {
            "tech_id": researching.tech_id,
            "tech_name": researching.tech_name,
            "tier": int(researching.tier),
            "current_progress": round(float(researching.current_progress), 2),
            "display_cost": cost,
            "percent": round(min(100.0, float(researching.current_progress) / cost * 100), 1),
            "eta_seconds": round(remaining / rate, 1) if rate > 0 else None,
        }

    return {
        "slot_id": slot_id,
        "planet_id": planet_id,
        "research_tier_level": research_tier_level(records),
        "unlocked_count": len(unlocked_ids),
        "total_nodes": len(records),
        "tier_sizes": tiers,
        "total_cost": sum(float(row.target_cost) for row in records),
        "research_points_per_sec": await geek_research_rate(session, slot_id, planet_id),
        "researching": research_view,
        "nodes": nodes,
    }


async def start_research(
    session: AsyncSession,
    tech_id: str,
    *,
    slot_id: int = B.DEFAULT_SLOT_ID,
    planet_id: int = B.HOME_PLANET_ID,
) -> dict[str, Any]:
    """开始研发：防火墙 2（DAG 前置校验）+ 单线程（一次只研一个节点）。"""
    records = await load_records(session, slot_id, planet_id)
    record = next((row for row in records if row.tech_id == tech_id), None)
    if record is None:
        raise BadRequest("BAD_REQUEST", f"未知科技 tech_id={tech_id}")
    if record.status == TechStatus.UNLOCKED:
        raise Conflict("TECH_ALREADY_UNLOCKED", f"【{record.tech_name}】已解锁")

    researching = next((row for row in records if row.status == TechStatus.RESEARCHING), None)
    if researching is not None and researching.tech_id != tech_id:
        raise Conflict(
            "RESEARCH_IN_PROGRESS",
            f"正在研发【{researching.tech_name}】，先等它完成（单线程研发）",
        )

    unlocked_ids = {row.tech_id for row in records if row.status == TechStatus.UNLOCKED}
    missing = [parent for parent in (record.parent_ids or []) if parent not in unlocked_ids]
    if missing:
        names = "、".join(
            next((row.tech_name for row in records if row.tech_id == parent), parent)
            for parent in missing
        )
        raise BadRequest("TECH_PREREQUISITE_MISSING", f"缺少前置科技：{names}")

    record.status = TechStatus.RESEARCHING
    await session.commit()
    logger.info("开始研发：slot=%s planet=%s tech=%s", slot_id, planet_id, tech_id)
    return node_view(record, unlocked_ids=unlocked_ids, researching_id=tech_id)


async def accumulate_research(
    session: AsyncSession,
    *,
    slot_id: int,
    planet_id: int,
    points: float,
) -> dict[str, Any] | None:
    """把一段时间的研究算力注入当前在研节点；攒满即解锁。返回本次研发事件（可能为 None）。"""
    if points <= 0:
        return None
    records = await load_records(session, slot_id, planet_id)
    record = next((row for row in records if row.status == TechStatus.RESEARCHING), None)
    if record is None:
        return None

    cost = effective_cost(record)
    record.current_progress = min(cost, float(record.current_progress) + float(points))
    unlocked = False
    if record.current_progress >= cost - 1e-9:
        record.current_progress = cost
        record.status = TechStatus.UNLOCKED
        unlocked = True
        logger.info("科技解锁：slot=%s tech=%s（%s）", slot_id, record.tech_id, record.tech_name)
    await session.flush()
    return {
        "tech_id": record.tech_id,
        "tech_name": record.tech_name,
        "progress": round(float(record.current_progress), 2),
        "cost": cost,
        "unlocked": unlocked,
        "spent_points": round(float(points), 2),
    }


async def reroll_tech(
    session: AsyncSession,
    tech_id: str,
    *,
    slot_id: int = B.DEFAULT_SLOT_ID,
    planet_id: int = B.HOME_PLANET_ID,
) -> dict[str, Any]:
    """手动重 Roll（模块 E3）：只对 LLM 动态生成的节点开放，消耗算力的 5%（最低 50）。

    * 母星 19 节点是手工编排（`is_agent_generated = false`）⇒ `400 TECH_NOT_REROLLABLE`；
    * 消耗从该节点**已累积进度**里扣（进度池即算力池），已累积的部分不会被清零。
    """
    records = await load_records(session, slot_id, planet_id)
    record = next((row for row in records if row.tech_id == tech_id), None)
    if record is None:
        raise BadRequest("BAD_REQUEST", f"未知科技 tech_id={tech_id}")
    if not record.is_agent_generated:
        raise BadRequest(
            "TECH_NOT_REROLLABLE",
            f"【{record.tech_name}】是手工编排的母星科技节点，不支持重 Roll（该能力用于外星球特化科技）",
        )

    key = (slot_id, planet_id, tech_id)
    last = _reroll_cooldown.get(key, 0.0)
    cooldown = B.TECH_REROLL_COOLDOWN_SECONDS - (time.time() - last)
    if last and cooldown > 0:
        raise Conflict("TECH_REROLL_COOLDOWN", f"重 Roll 冷却中，还需 {cooldown:.0f} 秒")

    cost = max(
        B.TECH_REROLL_MIN_COST,
        round(float(record.target_cost) * B.TECH_REROLL_COST_RATIO, 2),
    )
    if float(record.current_progress) < cost:
        raise InsufficientResource(
            detail=f"重 Roll 需要 {cost:g} 算力进度，当前只有 {float(record.current_progress):g}"
        )

    record.current_progress = round(float(record.current_progress) - cost, 2)
    _reroll_cooldown[key] = time.time()
    # LLM 场景 2：把这枚节点的卡面重新生成一张（失败走本地卡池，玩家零感知）
    from app.services import planet_tech_service

    card = await planet_tech_service.reroll_card(
        session, record, slot_id=slot_id, planet_id=planet_id
    )
    await session.commit()
    return {
        "tech_id": tech_id,
        "cost": cost,
        "remaining_progress": float(record.current_progress),
        "cooldown_seconds": B.TECH_REROLL_COOLDOWN_SECONDS,
        "card": card,
    }


async def build_gate_check(
    session: AsyncSession,
    facility_id: str,
    *,
    slot_id: int,
    planet_id: int,
    current_level: int,
) -> None:
    """设施首次建造的科技门槛（开荒部件豁免；已建过的不再重复校验）。"""
    if current_level > 0 or facility_id in TECH_GATE_EXEMPT_FACILITIES:
        return
    from app.core.seed_loader import facility_def_map

    required = (facility_def_map().get(facility_id) or {}).get("unlock", {}).get("tech_id")
    if not required:
        return
    record = await session.get(TechRecord, (slot_id, planet_id, required))
    if record is None or record.status != TechStatus.UNLOCKED:
        name = record.tech_name if record else required
        raise BadRequest(
            "TECH_LOCKED",
            f"【{(facility_def_map().get(facility_id) or {}).get('name', facility_id)}】需要先解锁科技【{name}】",
        )


def reset_reroll_cooldown() -> None:
    """测试用：清空进程内冷却表。"""
    _reroll_cooldown.clear()
