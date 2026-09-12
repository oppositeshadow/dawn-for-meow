"""水培基因实验室服务（模块 H）：播种 / 采摘 / 换介质 / 逐格扩建 / 机械臂托管 / 离线生长。

光环联动：在田光环由 `core/garden_engine.halo_summary` 汇总后喂给离线引擎
（荧光苔藓供电、消音绒草降警戒、金刚地衣加装甲、爆裂刺果降诱饵造价……）。
"""

from __future__ import annotations

import logging
import math
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import balance as B
from app.core.errors import BadRequest, Conflict, InsufficientResource, NotFound
from app.core.garden_engine import (
    STAGE_MATURE,
    STAGE_WITHERED,
    advance_tile,
    halo_summary,
    neighbors,
    next_expand_tile,
    pick_mutation,
)
from app.core.seed_loader import load_seed
from app.models import ColonyState, DarknetState, GardenState

logger = logging.getLogger("dawn_meow.garden")

RESOURCE_PRECISION = 2


def plant_defs() -> dict[str, dict]:
    return {item["plant_id"]: item for item in load_seed("cat_plants.json")["plants"]}


def school_defs() -> dict[str, dict]:
    return load_seed("cat_plants.json")["schools"]


def starter_seeds() -> list[str]:
    return list(load_seed("cat_plants.json").get("starter_seeds", ["ordinary_moss"]))


async def _garden(session: AsyncSession, slot_id: int, planet_id: int) -> GardenState:
    row = await session.get(GardenState, (slot_id, planet_id))
    if row is None:
        raise NotFound("GARDEN_NOT_FOUND", f"槽位 {slot_id} 星球 {planet_id} 没有水培实验室")
    return row


def find_tile(grid: list[dict], x: int, y: int) -> dict | None:
    return next((tile for tile in grid if int(tile.get("x", -1)) == x and int(tile.get("y", -1)) == y), None)


def expansion_cost(unlocked_cells: int) -> dict[str, float]:
    """第 n 次扩建的造价（沿用设施曲线的向上取整规则）。"""
    steps = max(0, unlocked_cells - B.GARDEN_INITIAL_UNLOCKED_CELLS)
    factor = B.GARDEN_EXPANSION_GROWTH**steps
    return {
        resource: float(math.ceil(amount * factor))
        for resource, amount in B.GARDEN_EXPANSION_BASE_COST.items()
    }


async def state_view(
    session: AsyncSession, *, slot_id: int = B.DEFAULT_SLOT_ID, planet_id: int = B.HOME_PLANET_ID
) -> dict[str, Any]:
    garden = await _garden(session, slot_id, planet_id)
    plants = plant_defs()
    # JSON 列的 dict 必须复制后替换：就地改不会被 SQLAlchemy 侦测到（会漏写库）
    grid = [dict(tile) for tile in (garden.grid_data or [])]
    codex = list(garden.unlocked_seed_ids or [])
    halo = halo_summary(grid, plants)
    return {
        "grid_size": int(garden.grid_size),
        "unlocked_cells": int(garden.unlocked_cells),
        "current_medium": garden.current_medium,
        "mechanical_arm_enabled": bool(garden.mechanical_arm_enabled),
        "auto_protect_unknown": bool(garden.auto_protect_unknown),
        "last_tick_time": int(garden.last_tick_time),
        "grid": [
            {
                "x": tile.get("x"),
                "y": tile.get("y"),
                "unlocked": bool(tile.get("unlocked")),
                "seed_id": tile.get("seed_id"),
                "plant_name": plants.get(str(tile.get("seed_id")), {}).get("name") if tile.get("seed_id") else None,
                "school": plants.get(str(tile.get("seed_id")), {}).get("school") if tile.get("seed_id") else None,
                "stage": tile.get("stage"),
                "age": round(float(tile.get("age", 0.0)), 1),
                "mutation_progress": round(float(tile.get("mutation_progress", 0.0)), 4),
            }
            for tile in grid
        ],
        "halo": {key: round(value, 4) for key, value in halo.items()},
        "codex": codex,
        "codex_total": len(plants),
        "plants": {
            plant_id: {
                "name": spec["name"],
                "school": spec["school"],
                "harvest": spec["harvest"],
                "halo": spec.get("halo", {}),
                "growth_multiplier": spec.get("growth_multiplier", 1.0),
                "unlocked": plant_id in codex,
            }
            for plant_id, spec in plants.items()
        },
        "schools": school_defs(),
        "expansion_cost": expansion_cost(int(garden.unlocked_cells)),
        "media": {key: spec["name"] for key, spec in B.GARDEN_MEDIA.items()},
    }


async def plant_seed(
    session: AsyncSession,
    *,
    x: int,
    y: int,
    seed_id: str,
    slot_id: int = B.DEFAULT_SLOT_ID,
    planet_id: int = B.HOME_PLANET_ID,
) -> dict[str, Any]:
    """播种：格必须已解锁且为空，种子必须在图鉴里解锁过。"""
    garden = await _garden(session, slot_id, planet_id)
    plants = plant_defs()
    if seed_id not in plants:
        raise BadRequest("BAD_REQUEST", f"未知猫草 seed_id={seed_id}")
    codex = list(garden.unlocked_seed_ids or [])
    if seed_id not in codex:
        raise BadRequest("SEED_LOCKED", f"【{plants[seed_id]['name']}】还未解锁母本，先杂交或采摘获得")

    grid = [dict(tile) for tile in (garden.grid_data or [])]
    tile = find_tile(grid, x, y)
    if tile is None:
        raise BadRequest("BAD_REQUEST", f"坐标越界：({x}, {y})")
    if not tile.get("unlocked"):
        raise BadRequest("CELL_LOCKED", f"({x}, {y}) 尚未解锁，先扩建")
    if tile.get("seed_id"):
        raise Conflict("CELL_OCCUPIED", f"({x}, {y}) 已经种着 {plants.get(str(tile['seed_id']), {}).get('name')}")

    tile.update({"seed_id": seed_id, "stage": "SEEDLING", "age": 0.0, "mutation_progress": 0.0})
    garden.grid_data = grid
    await session.commit()
    return {"x": x, "y": y, "seed_id": seed_id, "plant_name": plants[seed_id]["name"]}


async def harvest(
    session: AsyncSession,
    *,
    x: int,
    y: int,
    slot_id: int = B.DEFAULT_SLOT_ID,
    planet_id: int = B.HOME_PLANET_ID,
) -> dict[str, Any]:
    """采摘：只有成熟株可采；变异株首次采摘会永久解锁图鉴母本。"""
    garden = await _garden(session, slot_id, planet_id)
    plants = plant_defs()
    grid = [dict(tile) for tile in (garden.grid_data or [])]
    tile = find_tile(grid, x, y)
    if tile is None or not tile.get("seed_id"):
        raise BadRequest("BAD_REQUEST", "该格没有作物")
    if tile.get("stage") != STAGE_MATURE:
        raise Conflict("PLANT_NOT_MATURE", f"当前阶段 {tile.get('stage')}，还没到成熟期")

    plant = plants[str(tile["seed_id"])]
    colony = await session.get(ColonyState, (slot_id, planet_id))
    gained: dict[str, float] = {}
    overflowed: list[str] = []
    research_points = 0.0
    for resource, amount in plant["harvest"].items():
        if resource == "research_points":
            research_points += float(amount)
            continue
        if resource == "byte_credits":
            darknet = await session.get(DarknetState, slot_id)
            if darknet is not None:
                darknet.byte_credits = round(float(darknet.byte_credits) + float(amount), RESOURCE_PRECISION)
            gained[resource] = float(amount)
            continue
        cap = float(getattr(colony, f"{resource}_max"))
        before = float(getattr(colony, resource))
        after = min(cap, before + float(amount))
        if before + float(amount) > cap:
            overflowed.append(resource)
        setattr(colony, resource, round(after, RESOURCE_PRECISION))
        gained[resource] = round(after - before, RESOURCE_PRECISION)

    if research_points:
        from app.services import tech_service

        event = await tech_service.accumulate_research(
            session, slot_id=slot_id, planet_id=planet_id, points=research_points
        )
        gained["research_points"] = round(research_points - float((event or {}).get("spent_points", 0.0)), 2)

    codex = list(garden.unlocked_seed_ids or [])
    new_codex = False
    if plant["plant_id"] not in codex:
        codex.append(plant["plant_id"])
        garden.unlocked_seed_ids = codex
        new_codex = True

    tile.update({"seed_id": None, "stage": None, "age": 0.0, "mutation_progress": 0.0})
    garden.grid_data = grid
    await session.commit()
    return {
        "x": x,
        "y": y,
        "plant_id": plant["plant_id"],
        "plant_name": plant["name"],
        "gained": gained,
        "overflowed": overflowed,
        "new_codex_entry": new_codex,
        "codex_size": len(codex),
    }


async def set_medium(
    session: AsyncSession,
    medium: str,
    *,
    slot_id: int = B.DEFAULT_SLOT_ID,
    planet_id: int = B.HOME_PLANET_ID,
) -> dict[str, Any]:
    if medium not in B.GARDEN_MEDIA:
        raise BadRequest("BAD_REQUEST", f"未知培养液 {medium}")
    garden = await _garden(session, slot_id, planet_id)
    garden.current_medium = medium
    await session.commit()
    spec = B.GARDEN_MEDIA[medium]
    return {"current_medium": medium, "name": spec["name"], "growth_multiplier": spec["growth_multiplier"]}


async def set_mechanical_arm(
    session: AsyncSession,
    *,
    enabled: bool | None = None,
    auto_protect_unknown: bool | None = None,
    slot_id: int = B.DEFAULT_SLOT_ID,
    planet_id: int = B.HOME_PLANET_ID,
) -> dict[str, Any]:
    garden = await _garden(session, slot_id, planet_id)
    if enabled is not None:
        garden.mechanical_arm_enabled = bool(enabled)
    if auto_protect_unknown is not None:
        garden.auto_protect_unknown = bool(auto_protect_unknown)
    await session.commit()
    return {
        "mechanical_arm_enabled": bool(garden.mechanical_arm_enabled),
        "auto_protect_unknown": bool(garden.auto_protect_unknown),
    }


async def expand(
    session: AsyncSession, *, slot_id: int = B.DEFAULT_SLOT_ID, planet_id: int = B.HOME_PLANET_ID
) -> dict[str, Any]:
    """逐格扩建（由内向外），受 49 格上限与造价曲线约束。"""
    garden = await _garden(session, slot_id, planet_id)
    if int(garden.unlocked_cells) >= B.GARDEN_MAX_UNLOCKED_CELLS:
        raise Conflict("GARDEN_FULL", "7×7 场地已全部解锁")

    grid = [dict(tile) for tile in (garden.grid_data or [])]
    target = next_expand_tile(grid)
    if target is None:
        raise Conflict("GARDEN_FULL", "没有可解锁的格子")

    cost = expansion_cost(int(garden.unlocked_cells))
    colony = await session.get(ColonyState, (slot_id, planet_id))
    missing = {
        resource: need - float(getattr(colony, resource))
        for resource, need in cost.items()
        if float(getattr(colony, resource)) < need
    }
    if missing:
        raise InsufficientResource(
            detail="扩建缺料：" + "、".join(f"{B.RESOURCE_LABELS.get(k, k)} 差 {v:g}" for k, v in missing.items())
        )
    for resource, need in cost.items():
        setattr(colony, resource, round(float(getattr(colony, resource)) - need, RESOURCE_PRECISION))

    for tile in grid:
        if int(tile.get("x", -1)) == int(target.get("x", -2)) and int(tile.get("y", -1)) == int(target.get("y", -2)):
            tile["unlocked"] = True
    unlocked_cells = int(garden.unlocked_cells) + 1
    garden.unlocked_cells = unlocked_cells
    garden.grid_size = max(B.GARDEN_INITIAL_GRID_SIZE, math.ceil(math.sqrt(unlocked_cells)))
    garden.grid_data = grid
    await session.commit()
    return {
        "unlocked_cells": unlocked_cells,
        "grid_size": int(garden.grid_size),
        "new_cell": {"x": target.get("x"), "y": target.get("y")},
        "cost_paid": cost,
        "next_cost": expansion_cost(unlocked_cells),
    }


async def advance_garden(
    session: AsyncSession,
    *,
    slot_id: int = B.DEFAULT_SLOT_ID,
    planet_id: int = B.HOME_PLANET_ID,
    seconds: float,
    now: int | None = None,
) -> dict[str, Any]:
    """离线推进：生长 / 枯萎 / 相邻突变 / 机械臂自动收割。返回事件列表。"""
    garden = await _garden(session, slot_id, planet_id)
    if seconds <= 0:
        return {"events": [], "mutations": 0, "harvests": 0}

    plants = plant_defs()
    codex = list(garden.unlocked_seed_ids or [])
    grid = [dict(tile) for tile in (garden.grid_data or [])]
    medium = garden.current_medium
    colony = await session.get(ColonyState, (slot_id, planet_id))
    darknet = await session.get(DarknetState, slot_id)
    auto_gained: dict[str, float] = {}
    events: list[str] = []
    mutations = 0
    harvests = 0

    mutation_ready_tiles: list[dict] = []
    for tile in grid:
        result = advance_tile(tile, plants.get(str(tile.get("seed_id"))) if tile.get("seed_id") else None,
                              seconds=seconds, medium=medium)
        if result["withered"]:
            events.append(f"({tile.get('x')},{tile.get('y')}) 的作物在放射液中枯萎了")
        if result["mutation_ready"]:
            mutation_ready_tiles.append(tile)

    # 相邻突变：成熟株 + 相邻空格 ⇒ 生成新苗（确定性结算）
    for parent in mutation_ready_tiles:
        if parent.get("stage") != STAGE_MATURE or not parent.get("seed_id"):
            continue
        empty = [
            item
            for item in neighbors(parent, grid)
            if item.get("unlocked") and not item.get("seed_id")
        ]
        if not empty:
            continue
        child_id = pick_mutation(parent, plants)
        if child_id is None:
            continue
        target = sorted(empty, key=lambda item: (item.get("x", 0), item.get("y", 0)))[0]
        target.update({"seed_id": child_id, "stage": "SEEDLING", "age": 0.0, "mutation_progress": 0.0})
        mutations += 1
        events.append(f"({parent.get('x')},{parent.get('y')}) 邻居突变出【{plants[child_id]['name']}】")

    # 机械臂托管：自动收割成熟株防枯萎（默认保护未收录的未知突变幼苗）
    if garden.mechanical_arm_enabled:
        for tile in grid:
            if tile.get("stage") != STAGE_MATURE or not tile.get("seed_id"):
                continue
            plant = plants.get(str(tile["seed_id"]))
            if plant is None:
                continue
            if garden.auto_protect_unknown and plant["plant_id"] not in codex:
                continue
            if plant["plant_id"] not in codex:
                codex.append(plant["plant_id"])
                garden.unlocked_seed_ids = codex
            # 自动收割的产出照样入库（机械臂托管不吞战利品）
            for resource, amount in plant["harvest"].items():
                if resource == "byte_credits" and darknet is not None:
                    darknet.byte_credits = round(float(darknet.byte_credits) + float(amount), RESOURCE_PRECISION)
                    auto_gained[resource] = auto_gained.get(resource, 0.0) + float(amount)
                elif resource in B.RESOURCE_KEYS and colony is not None:
                    cap = float(getattr(colony, f"{resource}_max"))
                    before = float(getattr(colony, resource))
                    after = min(cap, before + float(amount))
                    setattr(colony, resource, round(after, RESOURCE_PRECISION))
                    auto_gained[resource] = auto_gained.get(resource, 0.0) + round(after - before, RESOURCE_PRECISION)
            tile.update({"seed_id": None, "stage": None, "age": 0.0, "mutation_progress": 0.0})
            harvests += 1

    garden.grid_data = grid
    if now is not None:
        garden.last_tick_time = int(now)
    await session.flush()
    return {
        "events": events[-20:],
        "mutations": mutations,
        "harvests": harvests,
        "auto_gained": {key: round(value, RESOURCE_PRECISION) for key, value in auto_gained.items()},
    }
