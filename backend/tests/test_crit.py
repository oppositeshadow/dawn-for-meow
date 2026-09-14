"""无人机暴击验收（《数值平衡表》§15.1）：确定性判定、倍率 1.5、只影响我方。"""

from __future__ import annotations

from app.core import balance as B
from app.core.combat_engine import is_critical, resolve_skirmish, unit_from_spec


def test_crit_chance_table() -> None:
    assert B.fleet_commander_crit_chance(0) == 0.0
    assert B.fleet_commander_crit_chance(3) == 0.06
    assert B.fleet_commander_crit_chance(5) == 0.10
    assert B.fleet_commander_crit_chance(99) == 0.10  # 封顶


def test_crit_is_deterministic() -> None:
    """同一场战斗、同一回合、同一对攻守方 ⇒ 判定永远一致（用哈希而非随机数）。"""
    first = [is_critical(round_number, "unit-1", "enemy-1", 0.5) for round_number in range(1, 21)]
    second = [is_critical(round_number, "unit-1", "enemy-1", 0.5) for round_number in range(1, 21)]
    assert first == second
    assert any(first)  # 50% 概率下 20 回合不可能一次都不暴击
    assert is_critical(1, "unit-1", "enemy-1", 0.0) is False  # 概率 0 永不暴击


def test_crit_raises_damage() -> None:
    """把概率拉到 100% 时，同一场战斗造成的伤害明显更高（倍率 1.5 生效）。"""
    dummy = {"name": "静止靶", "shield": 0.0, "armor": 0.0, "hull": 100_000.0, "dps": 0.0, "damage_type": "KINETIC"}

    def battle(chance: float) -> float:
        result = resolve_skirmish(
            [unit_from_spec(B.VEHICLE_TYPES["armored_car"], prefix="u1")],
            [unit_from_spec(dummy, prefix="e1")],
            attacker_crit_chance=chance,
            max_rounds=10,
        )
        return 100_000.0 - float(result["defenders"][0]["hull"])

    plain, always_crit = battle(0.0), battle(1.0)
    assert plain > 0
    assert abs(always_crit / plain - B.CRIT_DAMAGE_MULTIPLIER) <= 1e-6
