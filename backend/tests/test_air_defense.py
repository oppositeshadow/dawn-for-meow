"""政令【全星系红点防空标准】验收：对**空中单位**伤害 +25%，对地面单位无效。"""

from __future__ import annotations

from app.core import balance as B
from app.core.combat_engine import resolve_attack, unit_from_spec


def test_only_drone_is_marked_air() -> None:
    air = [key for key, spec in B.ENEMY_UNITS.items() if spec.get("air")]
    assert air == ["assault_drone"]


def test_air_defense_boosts_damage_against_drone_only() -> None:
    def hit(unit_key: str, bonus: float) -> float:
        target = unit_from_spec(B.ENEMY_UNITS[unit_key], prefix="e")
        target["shield"] = 0.0  # 跳过护盾层，直接看结构伤害
        _, damage, _ = resolve_attack(100.0, "KINETIC", target, air_defense=bonus)
        return damage

    drone_plain, drone_boosted = hit("assault_drone", 0.0), hit("assault_drone", 0.25)
    assert abs(drone_boosted / drone_plain - 1.25) <= 0.02  # 空中：+25%

    ground_plain, ground_boosted = hit("heavy_cleaner_03", 0.0), hit("heavy_cleaner_03", 0.25)
    assert ground_boosted == ground_plain  # 地面：无加成


def test_air_flag_survives_snapshot() -> None:
    assert unit_from_spec(B.ENEMY_UNITS["assault_drone"], prefix="e")["air"] is True
    assert unit_from_spec(B.ENEMY_UNITS["scout_roomba"], prefix="e")["air"] is False
