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
    generate_secret,
    render_symbols,
    symbol_count,
)
from app.core.seed_loader import minigame_defs
from app.models import BossState, MinigameState, PlanetState

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

    if minigame_id != "cipher_decode":
        raise BadRequest("NOT_IMPLEMENTED", f"【{spec['name']}】玩法尚未落地（模块 O 后续批次）")
    if not planet.unlocked:
        raise BadRequest(
            "PLANET_LOCKED",
            f"【{spec['name']}】需要先解锁 {B.PLANETS.get(target_planet)}",
        )
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
