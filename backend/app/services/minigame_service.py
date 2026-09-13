"""星球特色小游戏服务（模块 O）：通用状态表 + 密电译码。

口径（数值平衡表 §16）：所有小游戏**纯加速不卡主线**、零美术、可挂机、**共用 `minigame_state`**；
密电译码每天 3 次机会，失败不扣资源，成功则情报破译度 **+8%**（直接联动伏击车队玩法）。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import balance as B
from app.core.errors import BadRequest, NotFound
from app.core.minigame_engine import (
    code_length,
    daily_seed,
    evaluate_guess,
    evaluate_mix,
    forge_spec,
    generate_forge_recipe,
    generate_secret,
    generate_vein_board,
    render_symbols,
    symbol_count,
    vein_hint,
    vein_spec,
)
from app.core.seed_loader import minigame_defs
from app.core.errors import InsufficientResource
from app.models import BossState, ColonyState, MinigameState, PlanetState

logger = logging.getLogger("dawn_meow.minigame")


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _spec(minigame_id: str) -> dict:
    for item in minigame_defs():
        if item["minigame_id"] == minigame_id:
            return item
    raise BadRequest("BAD_REQUEST", f"未知小游戏 minigame_id={minigame_id}")


async def ensure_rows(session: AsyncSession, slot_id: int) -> list[MinigameState]:
    """老存档兼容：按 minigames.json 补齐缺失的小游戏行。"""
    rows = (
        await session.execute(select(MinigameState).where(MinigameState.slot_id == slot_id))
    ).scalars().all()
    existing = {(row.planet_id, row.minigame_id) for row in rows}
    for game in minigame_defs():
        key = (int(game["planet_id"]), game["minigame_id"])
        if key not in existing:
            row = MinigameState(
                slot_id=slot_id,
                planet_id=key[0],
                minigame_id=key[1],
                state=dict(game.get("initial_state", {})),
                last_tick_time=int(time.time()),
            )
            session.add(row)
            rows = list(rows) + [row]
    await session.flush()
    return list(rows)


def _cipher_state(row: MinigameState, slot_id: int) -> dict:
    state = dict(row.state or {})
    if state.get("day") != _today():
        state = {"day": _today(), "used": 0, "guesses": [], "best_attempts": state.get("best_attempts", 0)}
    state.setdefault("best_attempts", 0)
    return state


def _vein_state(row: MinigameState, *, slot_id: int) -> dict:
    """矿脉扫描状态：配额按 30 分钟回 1（上限 5），棋盘按 board_index 确定性生成。"""
    spec = vein_spec()
    now = int(time.time())
    state = dict(row.state or {})
    quota = int(state.get("quota", spec["quota_max"]))
    last = int(state.get("last_regen_ts") or now)
    regen_seconds = int(spec["quota_regen_seconds"])
    steps = (now - last) // regen_seconds
    if steps > 0 and quota < int(spec["quota_max"]):
        quota = min(int(spec["quota_max"]), quota + int(steps))
        last = last + int(steps) * regen_seconds
    state.setdefault("board_index", 0)
    state["quota"] = quota
    state["last_regen_ts"] = last
    state.setdefault("revealed", {})
    state.setdefault("found_count", 0)
    state["next_regen_in"] = max(0, regen_seconds - (now - last))
    return state


def _forge_state(row: MinigameState) -> dict:
    state = dict(row.state or {})
    spec = forge_spec()
    state.setdefault("recipes_found", 0)
    state.setdefault("attempts", 0)
    state.setdefault("recipe_index", 0)
    state["smelt_speed_bonus"] = round(
        min(float(spec["recipe_bonus_cap"]), float(spec["smelt_speed_bonus_per_recipe"]) * int(state["recipes_found"])),
        4,
    )
    state["recipe_cap"] = int(spec["recipe_cap"])
    state["scrap_cost"] = float(spec["scrap_cost_per_round"])
    return state


async def _colony_for(session: AsyncSession, slot_id: int, planet_id: int) -> ColonyState:
    """小游戏在外星球上玩，但资源池挂在基地：目标星球没有基地行时回落到母星。"""
    colony = await session.get(ColonyState, (slot_id, planet_id))
    if colony is None:
        colony = await session.get(ColonyState, (slot_id, B.HOME_PLANET_ID))
    if colony is None:
        raise NotFound("COLONY_STATE_NOT_FOUND", "找不到可用的基地状态")
    return colony


async def list_games(
    session: AsyncSession, *, slot_id: int = B.DEFAULT_SLOT_ID
) -> dict[str, Any]:
    rows = await ensure_rows(session, slot_id)
    planets = {
        row.planet_id: row
        for row in (
            await session.execute(select(PlanetState).where(PlanetState.slot_id == slot_id))
        ).scalars().all()
    }
    games: list[dict[str, Any]] = []
    for row in rows:
        spec = _spec(row.minigame_id)
        planet = planets.get(row.planet_id)
        quota = spec.get("quota", {})
        entry: dict[str, Any] = {
            "minigame_id": row.minigame_id,
            "name": spec["name"],
            "planet_id": row.planet_id,
            "planet_name": B.PLANETS.get(row.planet_id),
            "planet_unlocked": bool(planet.unlocked) if planet else False,
            "best_score": float(row.best_score),
            "play_count": int(row.play_count),
            "quota": quota,
            "core_loop": spec.get("core_loop"),
            "reward": spec.get("reward"),
        }
        if row.minigame_id == "cipher_decode":
            state = _cipher_state(row, slot_id)
            daily_quota = int(quota.get("amount", 3))
            entry["state"] = {
                "day": state["day"],
                "used": int(state["used"]),
                "remaining": max(0, daily_quota - int(state["used"])),
                "guesses": state["guesses"],
                "code_length": code_length(),
                "symbol_count": symbol_count(),
                "best_attempts": int(state["best_attempts"]),
            }
        elif row.minigame_id == "vein_scan":
            state = _vein_state(row, slot_id=slot_id)
            spec_vein = vein_spec()
            entry["state"] = {
                "quota": int(state["quota"]),
                "quota_max": int(spec_vein["quota_max"]),
                "regen_seconds": int(spec_vein["quota_regen_seconds"]),
                "next_regen_in": int(state["next_regen_in"]),
                "board_size": int(spec_vein["grid_size"]),
                "vein_count": int(spec_vein["vein_count"]),
                "found_count": int(state["found_count"]),
                "board_index": int(state["board_index"]),
                "revealed": [
                    {"x": int(key.split(",")[0]), "y": int(key.split(",")[1]), "hint": value}
                    for key, value in (state["revealed"] or {}).items()
                ],
            }
        elif row.minigame_id == "forge_recipe":
            state = _forge_state(row)
            entry["state"] = {
                "recipes_found": int(state["recipes_found"]),
                "recipe_cap": int(state["recipe_cap"]),
                "smelt_speed_bonus": float(state["smelt_speed_bonus"]),
                "attempts": int(state["attempts"]),
                "scrap_cost": float(state["scrap_cost"]),
                "last_feedback": state.get("last_feedback"),
            }
        games.append(entry)
    boss = await session.get(BossState, slot_id)
    return {
        "games": games,
        "intel_level": float(boss.intel_level) if boss else 0.0,
        "convoy_ends_at": int(boss.convoy_ends_at) if boss and boss.convoy_ends_at else None,
        "note": "所有小游戏都是纯加速项：不玩也能通关，玩了更快（数值平衡表 §16 四条铁律）",
    }


async def act(
    session: AsyncSession,
    *,
    minigame_id: str,
    action: str,
    payload: dict[str, Any],
    slot_id: int = B.DEFAULT_SLOT_ID,
    planet_id: int | None = None,
) -> dict[str, Any]:
    spec = _spec(minigame_id)
    target_planet = int(spec["planet_id"]) if planet_id is None else int(planet_id)
    planet = await session.get(PlanetState, (slot_id, target_planet))
    if planet is None:
        raise BadRequest("BAD_REQUEST", f"未知星球 planet_id={target_planet}")

    row = await session.get(MinigameState, (slot_id, target_planet, minigame_id))
    if row is None:
        row = MinigameState(
            slot_id=slot_id,
            planet_id=target_planet,
            minigame_id=minigame_id,
            state=dict(spec.get("initial_state", {})),
            last_tick_time=int(time.time()),
        )
        session.add(row)
        await session.flush()

    if not planet.unlocked:
        raise BadRequest(
            "PLANET_LOCKED",
            f"【{spec['name']}】需要先解锁 {B.PLANETS.get(target_planet)}",
        )
    if minigame_id == "vein_scan":
        return await _scan_vein(session, row, payload, slot_id, target_planet)
    if minigame_id == "forge_recipe":
        return await _submit_mix(session, row, payload, slot_id, target_planet)
    if action.upper() != "SUBMIT_GUESS":
        raise BadRequest("BAD_REQUEST", f"未知动作 action={action}")

    state = _cipher_state(row, slot_id)
    quota = int(spec.get("quota", {}).get("amount", 3))
    if int(state["used"]) >= quota:
        raise BadRequest("QUOTA_EXHAUSTED", f"今日 {quota} 次密电译码已用完，明天再来")

    guess = payload.get("guess")
    if (
        not isinstance(guess, list)
        or len(guess) != code_length()
        or any(not isinstance(value, int) or not (1 <= value <= symbol_count()) for value in guess)
    ):
        raise BadRequest(
            "BAD_REQUEST",
            f"猜测必须是 {code_length()} 个 {1}~{symbol_count()} 的整数符号",
        )

    secret = generate_secret(daily_seed(slot_id, target_planet, state["day"]))
    exact, partial = evaluate_guess(secret, guess)
    state["used"] = int(state["used"]) + 1
    state["guesses"] = list(state["guesses"]) + [
        {"guess": list(guess), "exact": exact, "partial": partial}
    ]
    solved = exact == code_length()
    reward: dict[str, Any] | None = None
    if solved:
        attempts = int(state["used"])
        best = int(state.get("best_attempts") or 0)
        state["best_attempts"] = attempts if best == 0 else min(best, attempts)
        row.best_score = float(state["best_attempts"])
        boss = await session.get(BossState, slot_id)
        gain = float(spec.get("reward", {}).get("intel_level_gain", 0.08))
        if boss is not None:
            boss.intel_level = round(min(1.0, float(boss.intel_level) + gain), 4)
            reward = {
                "intel_level": float(boss.intel_level),
                "convoy_ends_at": int(boss.convoy_ends_at) if boss.convoy_ends_at else None,
                "message": "译码成功：欧米茄车队时刻与要塞调度进入视野",
            }
        logger.info("密电译码成功：slot=%s 步数=%s", slot_id, attempts)
    state.pop("symbols", None)
    row.state = state
    row.play_count = int(row.play_count) + 1
    await session.commit()
    return {
        "minigame_id": minigame_id,
        "action": "SUBMIT_GUESS",
        "guess": list(guess),
        "exact": exact,
        "partial": partial,
        "solved": solved,
        "used": int(state["used"]),
        "remaining": max(0, quota - int(state["used"])),
        "best_attempts": int(state.get("best_attempts") or 0),
        "reward": reward,
        "answer": render_symbols(secret) if solved else None,
        "note": "反馈只有「位置对 / 符号对」两个数字：位置对 = 符号与位置都对，符号对 = 符号对但位置错",
    }


async def _scan_vein(
    session: AsyncSession,
    row: MinigameState,
    payload: dict[str, Any],
    slot_id: int,
    planet_id: int,
) -> dict[str, Any]:
    """矿脉扫描：每次消耗 1 次配额，命中矿脉得【反物质前驱体】等价物，找齐 12 处清盘。"""
    spec = vein_spec()
    state = _vein_state(row, slot_id=slot_id)
    x, y = payload.get("x"), payload.get("y")
    size = int(spec["grid_size"])
    if not isinstance(x, int) or not isinstance(y, int) or not (0 <= x < size and 0 <= y < size):
        raise BadRequest("BAD_REQUEST", f"扫描坐标必须是 0~{size - 1} 的整数")
    if int(state["quota"]) <= 0:
        raise BadRequest("QUOTA_EXHAUSTED", f"扫描配额用完了，{state['next_regen_in'] // 60} 分钟后恢复 1 次")
    key = f"{x},{y}"
    if key in (state["revealed"] or {}):
        raise BadRequest("ALREADY_SCANNED", f"({x},{y}) 已经扫描过了")

    board = generate_vein_board(f"{slot_id}:{planet_id}:{state['board_index']}")
    is_vein = bool(board[y][x])
    hint = 0 if is_vein else vein_hint(board, x, y)
    revealed = dict(state["revealed"] or {})
    revealed[key] = -1 if is_vein else hint
    state["revealed"] = revealed
    state["quota"] = int(state["quota"]) - 1

    colony = await _colony_for(session, slot_id, planet_id)
    gained: dict[str, float] = {}
    board_cleared = False
    if is_vein:
        state["found_count"] = int(state["found_count"]) + 1
        # 矿脉产出落地为现有资源口径：反物质前驱体 → 合金；氦-3 微粒 → 电池
        for resource, amount in (("alloys", 2.0), ("battery", 1.0)):
            cap = float(getattr(colony, f"{resource}_max"))
            before = float(getattr(colony, resource))
            setattr(colony, resource, round(min(cap, before + amount), 2))
            gained[resource] = round(min(cap, before + amount) - before, 2)
        if int(state["found_count"]) >= int(spec["vein_count"]):
            board_cleared = True
            for resource, amount in (("alloys", 10.0), ("battery", 3.0)):
                cap = float(getattr(colony, f"{resource}_max"))
                before = float(getattr(colony, resource))
                setattr(colony, resource, round(min(cap, before + amount), 2))
                gained[resource] = round(gained.get(resource, 0.0) + min(cap, before + amount) - before, 2)
            state["board_index"] = int(state["board_index"]) + 1
            state["revealed"] = {}
            state["found_count"] = 0

    row.state = state
    row.play_count = int(row.play_count) + 1
    if int(state.get("found_count", 0)) > float(row.best_score):
        row.best_score = float(state["found_count"])
    await session.commit()
    return {
        "minigame_id": "vein_scan",
        "action": "SCAN",
        "x": x,
        "y": y,
        "is_vein": is_vein,
        "hint": hint,
        "gained": gained,
        "quota": int(state["quota"]),
        "found_count": int(state["found_count"]),
        "board_cleared": board_cleared,
        "board_index": int(state["board_index"]),
        "note": "扫空只消耗配额不扣资源；数字表示相邻 8 格里的矿脉数量（扫雷式推理）",
    }


async def _submit_mix(
    session: AsyncSession,
    row: MinigameState,
    payload: dict[str, Any],
    slot_id: int,
    planet_id: int,
) -> dict[str, Any]:
    """熔炉配比：每轮消耗 20 废铁，反馈只有「太热／太冷／比例偏差」，命中永久 +5% 熔炼速度。"""
    spec = forge_spec()
    state = _forge_state(row)
    mix = payload.get("mix")
    if (
        not isinstance(mix, list)
        or len(mix) != 3
        or any(not isinstance(value, int) or not (0 <= value <= 100) for value in mix)
    ):
        raise BadRequest("BAD_REQUEST", "配方必须是 3 个 0~100 的整数投料量")
    colony = await _colony_for(session, slot_id, planet_id)
    cost = float(spec["scrap_cost_per_round"])
    if float(colony.scrap) < cost:
        raise InsufficientResource(detail=f"每轮熔炉配比需要 {cost:g} 废铁，当前 {float(colony.scrap):.1f}")
    colony.scrap = round(float(colony.scrap) - cost, 2)

    recipe = generate_forge_recipe(f"{slot_id}:{planet_id}:{state['recipe_index']}")
    feedback = evaluate_mix(recipe, mix)
    state["attempts"] = int(state["attempts"]) + 1
    reward: dict[str, Any] | None = None
    if feedback["result"] == "HIT":
        found = min(int(spec["recipe_cap"]), int(state["recipes_found"]) + 1)
        state["recipes_found"] = found
        state["recipe_index"] = int(state["recipe_index"]) + 1
        bonus = min(
            float(spec["recipe_bonus_cap"]), float(spec["smelt_speed_bonus_per_recipe"]) * found
        )
        reward = {
            "recipes_found": found,
            "smelt_speed_bonus": round(bonus, 4),
            "message": f"第 {found} 条配方入册：熔炼速度永久 +{round(bonus * 100)}%",
        }
    state["last_feedback"] = feedback
    row.state = state
    row.play_count = int(row.play_count) + 1
    row.best_score = float(state["recipes_found"])
    await session.commit()
    return {
        "minigame_id": "forge_recipe",
        "action": "SUBMIT_MIX",
        "mix": list(mix),
        "feedback": feedback,
        "recipes_found": int(state["recipes_found"]),
        "smelt_speed_bonus": state["smelt_speed_bonus"],
        "reward": reward,
        "scrap_left": round(float(colony.scrap), 2),
        "note": "反馈不给具体数值，只给方向：多试几条曲线就能逼近隐藏配方",
    }
