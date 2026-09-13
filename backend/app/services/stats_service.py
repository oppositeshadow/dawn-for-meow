"""生涯统计与成就服务（模块 N3）：把各系统的数字聚合成看板，并在达标时点亮徽章。

* 统计口径见《数值平衡表》§17 之外的 WBS N3：只做**只读聚合**，不改变任何玩法数值；
* 成就进度写 `achievements` 表，`unlocked_at` 只在首次达标时写入（幂等）。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import balance as B
from app.models import (
    Achievement,
    BossState,
    CareerStats,
    ColonyState,
    DarknetState,
    FacilityState,
    GardenState,
    MinigameState,
    SaveSlot,
    TechRecord,
    VehicleUnit,
)
from app.models.tech import TechStatus
from app.services.game_init_service import now_timestamp

logger = logging.getLogger("dawn_meow.stats")

PLAYER_UNIT_STATUSES = ("IDLE", "EXPEDITION", "REPAIR")


async def _metrics(session: AsyncSession, slot_id: int) -> dict[str, float]:
    """把散落在各表里的生涯数字聚合成一个扁平字典（成就与看板共用）。"""
    career = await session.get(CareerStats, slot_id)
    colony = await session.get(ColonyState, (slot_id, B.HOME_PLANET_ID))
    boss = await session.get(BossState, slot_id)
    darknet = await session.get(DarknetState, slot_id)
    garden = await session.get(GardenState, (slot_id, B.HOME_PLANET_ID))
    tech_unlocked = await session.execute(
        select(func.count()).select_from(TechRecord).where(
            TechRecord.slot_id == slot_id, TechRecord.status == TechStatus.UNLOCKED
        )
    )
    housing = await session.get(FacilityState, (slot_id, B.HOME_PLANET_ID, "housing_box"))
    vehicles = await session.execute(
        select(func.count()).select_from(VehicleUnit).where(VehicleUnit.slot_id == slot_id)
    )
    cipher = await session.get(MinigameState, (slot_id, 2, "cipher_decode"))
    vein = await session.get(MinigameState, (slot_id, 3, "vein_scan"))
    forge = await session.get(MinigameState, (slot_id, 1, "forge_recipe"))
    boss_state = dict(boss.bombardment_state or {}) if boss else {}
    cipher_best = int((cipher.state or {}).get("best_attempts") or 0) if cipher else 0
    return {
        "housing_box": float(housing.level) if housing else 0.0,
        "cats_total": float(colony.total_cats) if colony else 0.0,
        "cats_born": float(career.total_cats_born) if career else 0.0,
        "total_scrap": float(career.total_scrap) if career else 0.0,
        "total_catnip": float(career.total_catnip) if career else 0.0,
        "total_chips": float(career.total_chips) if career else 0.0,
        "total_alloys": float(career.total_alloys) if career else 0.0,
        "total_kwh": float(career.total_kwh) if career else 0.0,
        "best_short_profit": float(career.best_short_profit) if career else 0.0,
        "smuggling_volume": float(career.smuggling_volume) if career else 0.0,
        "expeditions_completed": float(career.expeditions_completed) if career else 0.0,
        "bombardment_survived": float(career.bombardment_survived) if career else 0.0,
        "tech_unlocked": float(tech_unlocked.scalar_one()),
        "vehicles": float(vehicles.scalar_one()),
        "garden_codex": float(len(garden.unlocked_seed_ids or [])) if garden else 0.0,
        # 密电：一次破开记 1 分（没破解过记 0）
        "cipher_best": 1.0 if cipher_best == 1 else 0.0,
        "vein_found": float(vein.best_score) if vein else 0.0,
        "forge_recipes": float(forge.best_score) if forge else 0.0,
        "byte_credits": float(darknet.byte_credits) if darknet else 0.0,
        "override_key": 1.0 if boss_state.get("override_key_used_at") else 0.0,
        "threat_level": float(boss.threat_level) if boss else 1.0,
        "fleet_strength": float(boss.fleet_strength) if boss else 0.0,
        "playtime_seconds": float(career.playtime_seconds) if career else 0.0,
    }


async def career_view(session: AsyncSession, *, slot_id: int = B.DEFAULT_SLOT_ID) -> dict[str, Any]:
    metrics = await _metrics(session, slot_id)
    career = await session.get(CareerStats, slot_id)
    unlocked = await session.execute(
        select(func.count()).select_from(Achievement).where(
            Achievement.slot_id == slot_id, Achievement.unlocked_at.is_not(None)
        )
    )
    return {
        "playtime_seconds": int(metrics["playtime_seconds"]),
        "playtime_hours": round(metrics["playtime_seconds"] / 3600.0, 2),
        "total_catnip": round(metrics["total_catnip"], 2),
        "total_scrap": round(metrics["total_scrap"], 2),
        "total_chips": round(metrics["total_chips"], 2),
        "total_alloys": round(metrics["total_alloys"], 2),
        "total_battery": round(float(career.total_battery), 2) if career else 0.0,
        "total_kwh": round(metrics["total_kwh"], 2),
        "total_cats_born": int(metrics["cats_born"]),
        "best_short_profit": round(metrics["best_short_profit"], 2),
        "smuggling_volume": round(metrics["smuggling_volume"], 2),
        "expeditions_completed": int(metrics["expeditions_completed"]),
        "bombardment_survived": int(metrics["bombardment_survived"]),
        "fastest_rebuild_seconds": career.fastest_rebuild_seconds if career else None,
        "achievements_unlocked": int(unlocked.scalar_one()),
        "achievements_total": len(B.ACHIEVEMENTS),
        "completed": bool(metrics["override_key"]),
    }


async def achievements_view(
    session: AsyncSession, *, slot_id: int = B.DEFAULT_SLOT_ID
) -> dict[str, Any]:
    """按当前状态刷新成就进度（达标即点亮，`unlocked_at` 只写一次）。"""
    metrics = await _metrics(session, slot_id)
    # 未开局的槽位：只读给空盘，不落库（避免给不存在的存档写徽章行）
    slot_exists = await session.get(SaveSlot, slot_id) is not None
    rows = {
        row.achievement_id: row
        for row in (
            await session.execute(select(Achievement).where(Achievement.slot_id == slot_id))
        ).scalars().all()
    }
    now = now_timestamp()
    items: list[dict[str, Any]] = []
    newly_unlocked: list[str] = []
    for spec in B.ACHIEVEMENTS:
        value = float(metrics.get(spec["metric"], 0.0))
        target = float(spec["target"])
        row = rows.get(spec["achievement_id"])
        if row is None:
            if not slot_exists:
                items.append(
                    {
                        "achievement_id": spec["achievement_id"],
                        "name": spec["name"],
                        "desc": spec["desc"],
                        "target": target,
                        "progress": 0.0,
                        "percent": 0.0,
                        "unlocked": False,
                        "unlocked_at": None,
                        "newly_unlocked": False,
                    }
                )
                continue
            row = Achievement(slot_id=slot_id, achievement_id=spec["achievement_id"], progress=0.0)
            session.add(row)
        was_locked = row.unlocked_at is None
        row.progress = round(value, 4)
        if value >= target and row.unlocked_at is None:
            row.unlocked_at = now
            newly_unlocked.append(spec["achievement_id"])
            logger.info("成就点亮：slot=%s badge=%s", slot_id, spec["achievement_id"])
        items.append(
            {
                "achievement_id": spec["achievement_id"],
                "name": spec["name"],
                "desc": spec["desc"],
                "target": target,
                "progress": round(min(value, target), 4),
                "percent": round(min(100.0, value / target * 100.0) if target else 0.0, 1),
                "unlocked": row.unlocked_at is not None,
                "unlocked_at": row.unlocked_at,
                "newly_unlocked": bool(was_locked and row.unlocked_at is not None),
            }
        )
    await session.commit()
    return {
        "achievements": items,
        "unlocked_count": sum(1 for item in items if item["unlocked"]),
        "total": len(items),
        "newly_unlocked": newly_unlocked,
    }
