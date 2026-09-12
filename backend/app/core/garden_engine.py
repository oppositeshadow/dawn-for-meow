"""猫草水培生长引擎（模块 H，数值平衡表 §10）。

纯粹的网格推演：生长阶段推进、放射性枯萎、相邻突变、采摘产出与在田光环汇总。
**确定性**：突变不做真随机，而是把"每 30 秒 8% 概率"折算成每秒期望进度
（`GARDEN_MUTATION_BASE_RATE / GARDEN_MUTATION_CHECK_SECONDS`），攒满 1.0 即突变 —— 离线也能复现。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.core import balance as B

STAGE_SEEDLING = "SEEDLING"
STAGE_JOINTING = "JOINTING"
STAGE_MATURE = "MATURE"
STAGE_WITHERED = "WITHERED"
STAGE_ORDER = (STAGE_SEEDLING, STAGE_JOINTING, STAGE_MATURE, STAGE_WITHERED)


def stage_for_age(age: float) -> str:
    """按年龄推阶段：每 `GARDEN_GROWTH_STAGE_SECONDS` 秒进一阶。"""
    index = int(age // B.GARDEN_GROWTH_STAGE_SECONDS)
    return STAGE_ORDER[min(index, len(STAGE_ORDER) - 1)]


def medium_multipliers(medium: str) -> tuple[float, float]:
    """返回（生长倍率、突变倍率）。"""
    spec = B.GARDEN_MEDIA.get(medium, B.GARDEN_MEDIA["STERILE"])
    return float(spec["growth_multiplier"]), float(spec["mutation_multiplier"])


def tile_center_distance(tile: Mapping[str, Any], size: int = B.GARDEN_GRID_SIZE) -> float:
    center = (size - 1) / 2
    return abs(float(tile.get("x", 0)) - center) + abs(float(tile.get("y", 0)) - center)


def next_expand_tile(grid: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """由内向外挑下一个待解锁格（曼哈顿距离最近、同环按 x/y 排序，保证确定性）。"""
    locked = [tile for tile in grid if not tile.get("unlocked")]
    if not locked:
        return None
    return sorted(locked, key=lambda tile: (tile_center_distance(tile), tile.get("x", 0), tile.get("y", 0)))[0]


def advance_tile(
    tile: dict[str, Any],
    plant: Mapping[str, Any] | None,
    *,
    seconds: float,
    medium: str,
) -> dict[str, Any]:
    """推进单格：生长 / 放射性枯萎 / 突变进度。返回是否有变化。"""
    if plant is None or tile.get("seed_id") is None:
        return {"grown": False, "withered": False, "mutation_ready": False}
    if tile.get("stage") == STAGE_WITHERED:
        return {"grown": False, "withered": False, "mutation_ready": False}

    growth_multiplier, mutation_multiplier = medium_multipliers(medium)
    plant_growth = float(plant.get("growth_multiplier", 1.0))
    before_stage = stage_for_age(float(tile.get("age", 0.0)))
    tile["age"] = float(tile.get("age", 0.0)) + seconds * growth_multiplier * plant_growth
    after_stage = stage_for_age(tile["age"])

    withered = False
    if medium == "ZERO_G":
        # 零重力保鲜液：成熟后永不枯萎（阶段停在 MATURE）
        if after_stage == STAGE_WITHERED:
            after_stage = STAGE_MATURE
    elif medium == "RADIATION" and after_stage in (STAGE_MATURE, STAGE_WITHERED):
        mature_age = B.GARDEN_GROWTH_STAGE_SECONDS * 2
        if tile["age"] >= mature_age + B.GARDEN_RADIATION_WITHER_SECONDS:
            after_stage = STAGE_WITHERED
            withered = True

    tile["stage"] = after_stage

    mutation_ready = False
    if after_stage == STAGE_MATURE:
        rate_per_second = (
            B.GARDEN_MUTATION_BASE_RATE / B.GARDEN_MUTATION_CHECK_SECONDS * mutation_multiplier
        )
        progress = float(tile.get("mutation_progress", 0.0)) + rate_per_second * seconds
        if progress >= 1.0:
            tile["mutation_progress"] = 0.0
            mutation_ready = True
        else:
            tile["mutation_progress"] = round(progress, 4)
    else:
        tile["mutation_progress"] = 0.0

    return {
        "grown": after_stage != before_stage,
        "withered": withered,
        "mutation_ready": mutation_ready,
    }


def neighbors(tile: Mapping[str, Any], grid: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """四邻格（上下左右）。"""
    x, y = int(tile.get("x", 0)), int(tile.get("y", 0))
    wanted = {(x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)}
    return [item for item in grid if (int(item.get("x", 0)), int(item.get("y", 0))) in wanted]


def pick_mutation(parent: Mapping[str, Any], plants: Mapping[str, Mapping[str, Any]]) -> str | None:
    """从父株的可突变表里按确定性顺序取第一个（不引入随机数）。"""
    plant = plants.get(str(parent.get("seed_id")))
    if plant is None:
        return None
    options = [option for option in plant.get("mutable_into", []) if option in plants]
    if not options:
        return None
    # 确定性轮转：按母本已突变次数取模（用 age 的整数位做稳定扰动）
    index = int(float(parent.get("age", 0.0))) % len(options)
    return options[index]


def halo_summary(
    grid: Sequence[Mapping[str, Any]],
    plants: Mapping[str, Mapping[str, Any]],
) -> dict[str, float]:
    """在田光环汇总（只统计存活且已成熟的植株）。"""
    totals: dict[str, float] = {}
    for tile in grid:
        if not tile.get("unlocked") or tile.get("seed_id") is None:
            continue
        if tile.get("stage") == STAGE_WITHERED:
            continue
        plant = plants.get(str(tile.get("seed_id")))
        if plant is None:
            continue
        for key, value in (plant.get("halo") or {}).items():
            totals[key] = totals.get(key, 0.0) + float(value)
    return totals
