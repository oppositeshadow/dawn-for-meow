"""一次性分析：线性封顶 vs MOBA 式凸曲线的减伤与战斗结果对比（只读）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import balance as B  # noqa: E402
from app.core import combat_engine as CE  # noqa: E402
from app.core.combat_engine import resolve_skirmish, unit_from_spec  # noqa: E402

MAX = B.ARMOR_REDUCTION_MAX  # 0.75

CURVES = {
    "现状 线性÷400 封顶75%": lambda a: min(MAX, a / 400.0),
    "凸曲线 K=100（渐近75%）": lambda a: MAX * a / (a + 100.0),
    "凸曲线 K=150（渐近75%）": lambda a: MAX * a / (a + 150.0),
    "凸曲线 K=300（渐近75%）": lambda a: MAX * a / (a + 300.0),
}

ARMORS = (80.0, 200.0, 300.0, 400.0, 500.0)

DUELS = (
    ("破拆机甲 vs 关底近卫", "breaker_mech", "heavy_guard_mech", 1),
    ("轻装猫车×3 vs 清扫蜘蛛-03", "light_car", "heavy_cleaner_03", 3),
    ("装甲猫车×2 vs 突击无人机", "armored_car", "assault_drone", 2),
    ("轻装猫车×1 vs 侦察扫地机", "light_car", "scout_roomba", 1),
    ("破拆机甲×3 vs 关底近卫（决战编队）", "breaker_mech", "heavy_guard_mech", 3),
    ("破拆机甲×4 vs 关底近卫（决战编队）", "breaker_mech", "heavy_guard_mech", 4),
)


def run_duel(curve, friendly: str, enemy: str, count: int) -> tuple:
    original = CE.armor_reduction_ratio
    CE.armor_reduction_ratio = curve
    try:
        result = resolve_skirmish(
            [unit_from_spec(B.VEHICLE_TYPES[friendly], prefix=f"u{i}") for i in range(count)],
            [unit_from_spec(B.ENEMY_UNITS[enemy], prefix="e1")],
        )
    finally:
        CE.armor_reduction_ratio = original
    own_hull = sum(float(unit["hull"]) for unit in result["attackers"])
    return result["winner"], result["rounds"], float(result["defenders"][0]["hull"]), own_hull


def main() -> None:
    print("== 减伤率对照（装甲值 → 减伤%）==")
    header = "曲线".ljust(24) + "".join(f"{int(a):>8}" for a in ARMORS)
    print(header)
    for label, curve in CURVES.items():
        row = label.ljust(24) + "".join(f"{curve(a) * 100:>7.1f}%" for a in ARMORS)
        print(row)

    for title, friendly, enemy, count in DUELS:
        print(f"\n== {title} ==")
        for label, curve in CURVES.items():
            winner, rounds, enemy_hull, own_hull = run_duel(curve, friendly, enemy, count)
            print(f"  {label:<24} {winner:<8} {rounds:>3} 回合  敌残留 {enemy_hull:>6.0f}  我方残留 {own_hull:>6.0f}")


if __name__ == "__main__":
    main()
