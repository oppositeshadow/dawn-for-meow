"""科技 `fleet_armor` / `armor_bonus` 接入战斗（全军装甲加成单一入口）。"""

from __future__ import annotations

from app.core import balance as B
from app.core.combat_engine import unit_from_spec


def _fleet() -> list[dict]:
    return [unit_from_spec(B.VEHICLE_TYPES["light_car"], prefix="unit-1")]


def _enemy() -> list[dict]:
    return [unit_from_spec(B.ENEMY_UNITS[B.INTERCEPT_ENEMY_UNIT], prefix="enemy-1")]


def _run(bonus: float) -> dict:
    from app.core.combat_engine import resolve_skirmish

    return resolve_skirmish(
        _fleet(), _enemy(), attacker_morale=1.0, attacker_armor_bonus=bonus
    )


def test_armor_bonus_scales_effective_armor() -> None:
    base = _run(0.0)
    armored = _run(0.5)
    # 加成作用于结算最外层：装甲被放大，同一场交战的战损更轻
    assert armored["attackers"][0]["armor_max"] == base["attackers"][0]["armor_max"] * 1.5
    assert armored["attackers"][0]["hull"] >= base["attackers"][0]["hull"]
    assert armored["rounds"] <= base["rounds"] or armored["winner"] == "ATTACK"


def test_zero_bonus_keeps_original_numbers() -> None:
    plain = _run(0.0)
    assert plain["attackers"][0]["armor_max"] == float(B.VEHICLE_TYPES["light_car"]["armor"])


def test_bonus_is_not_applied_to_enemy() -> None:
    base = _run(0.0)
    armored = _run(0.5)
    assert armored["defenders"][0]["armor_max"] == base["defenders"][0]["armor_max"]
