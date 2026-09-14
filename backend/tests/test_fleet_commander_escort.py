"""无人机群领航官「防御战力 +15%/只」的落地验收（《数值平衡表》§15.1）。

口径：把"防御战力"落到**护航护盾**上——每只领航官 +5% 全军护盾，封顶 5 只 = +25%。
"""

from __future__ import annotations

from app.core import balance as B
from app.core.combat_engine import resolve_skirmish, unit_from_spec


def test_escort_bonus_table() -> None:
    assert B.fleet_commander_shield_bonus(0) == 0.0
    assert B.fleet_commander_shield_bonus(2) == 0.10
    assert B.fleet_commander_shield_bonus(5) == 0.25
    assert B.fleet_commander_shield_bonus(99) == 0.25  # 封顶（每星球上限 5 只）
    assert B.fleet_commander_shield_bonus(-1) == 0.0


def test_escort_raises_fleet_shield() -> None:
    # 用"不还手的靶子"（dps=0、海量结构）看**开战瞬间**的护盾，避免战后快照把护盾打没了
    dummy = {"name": "静止靶", "shield": 0.0, "armor": 0.0, "hull": 100_000.0, "dps": 0.0, "damage_type": "KINETIC"}

    def battle(bonus: float) -> dict:
        return resolve_skirmish(
            [unit_from_spec(B.VEHICLE_TYPES["armored_car"], prefix="u1")],
            [unit_from_spec(dummy, prefix="e1")],
            attacker_shield_bonus=bonus,
        )

    plain = battle(0.0)
    escorted = battle(0.25)
    base_shield = float(B.VEHICLE_TYPES["armored_car"]["shield"])
    assert plain["attackers"][0]["shield"] == base_shield
    assert escorted["attackers"][0]["shield"] == round(base_shield * 1.25, 6)
