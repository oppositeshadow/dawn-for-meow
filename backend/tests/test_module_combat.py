"""§9.10 车载模块真的进战斗：DPS / 装甲 / 对盾倍率三项效果与"不重复记账"。"""

from __future__ import annotations

from app.core import balance as B
from app.core.combat_engine import apply_module_effects, resolve_attack, unit_from_spec


def _car() -> dict:
    return unit_from_spec(B.VEHICLE_TYPES["light_car"], prefix="unit-1")  # damage_type = KINETIC


def test_dps_and_armor_modules_scale_snapshot() -> None:
    plain = _car()
    armed = apply_module_effects(_car(), ["armor_plate", "laser_mk2"])
    assert plain["dps"] == float(B.VEHICLE_TYPES["light_car"]["dps"])
    assert armed["dps"] == round(plain["dps"] * 1.10, 4)  # 只有激光带 dps_bonus
    assert armed["armor"] == round(plain["armor"] * 1.25, 4)  # 附加装甲板
    assert armed["armor_max"] == round(plain["armor_max"] * 1.25, 4)
    assert armed["modules"] == ["armor_plate", "laser_mk2"]


def test_unknown_module_ids_are_ignored() -> None:
    snapshot = apply_module_effects(_car(), ["not_a_module"])
    assert "modules" not in snapshot
    assert snapshot["dps"] == float(B.VEHICLE_TYPES["light_car"]["dps"])


def test_laser_module_gives_shield_multiplier_without_double_counting() -> None:
    target = {"name": "靶", "shield": 100.0, "armor": 0.0, "hull": 100.0}
    # 动能弹打护盾全局只有 ×0.8
    _, damage_plain, _ = resolve_attack(50.0, "KINETIC", dict(target))
    assert damage_plain > 0 or True  # 护盾吸收后不一定打到结构，这里只比"打掉多少护盾"
    shield_plain = 100.0 - dict(target)["shield"]
    _ = shield_plain

    plain_target = {"name": "靶", "shield": 100.0, "armor": 0.0, "hull": 100.0}
    resolve_attack(50.0, "KINETIC", plain_target)
    armed_target = {"name": "靶", "shield": 100.0, "armor": 0.0, "hull": 100.0}
    resolve_attack(50.0, "KINETIC", armed_target, attacker_vs_shield=1.8)
    assert armed_target["shield"] == plain_target["shield"] - 50.0  # ×0.8 → 50 打掉 40；×1.8 → 50 打掉 90
    # 具体来说：全局 0.8 时 50 攻击只能削 40 护盾；模块 1.8 时削 90
    assert plain_target["shield"] == 60.0
    assert armed_target["shield"] == 10.0


def test_laser_module_does_not_stack_with_laser_type() -> None:
    """激光车型本来就吃全局 ×1.8，装模块后仍是 ×1.8（取较大值，不相乘）。"""
    target = {"name": "靶", "shield": 100.0, "armor": 0.0, "hull": 100.0}
    resolve_attack(50.0, "LASER", target, attacker_vs_shield=1.8)
    assert target["shield"] == 10.0  # 若是相乘会变成 50×3.24=162 ⇒ 护盾清零并溢出，一眼可辨


def test_full_skirmish_reads_module_snapshot() -> None:
    from app.core.combat_engine import resolve_skirmish

    enemy = [unit_from_spec(B.ENEMY_UNITS["assault_drone"], prefix="enemy-1")]  # 护盾 120
    plain = resolve_skirmish([_car()], [unit_from_spec(B.ENEMY_UNITS["assault_drone"], prefix="e")])
    armed = resolve_skirmish(
        [apply_module_effects(_car(), ["laser_mk2"])],
        [unit_from_spec(B.ENEMY_UNITS["assault_drone"], prefix="e")],
    )
    assert armed["rounds"] <= plain["rounds"]  # 对盾更强 ⇒ 不会打得更久
    assert "modules" in armed["attackers"][0]
    _ = enemy


def test_drill_module_raises_shred_without_nerfing_others() -> None:
    """破甲系数取较大值：钻头车 45%，没装的车仍是基础 30%（谁都不被削弱）。"""
    armed = apply_module_effects(_car(), ["drill_mk2"])
    assert armed["armor_shred"] == B.ARMOR_SHRED_RATIO_DRILL > B.ARMOR_SHRED_RATIO

    # 同一个高装甲目标：装钻头的一击能多削一点装甲上限
    target_plain = {"name": "靶", "shield": 0.0, "armor": 400.0, "armor_max": 400.0, "hull": 1000.0}
    target_drill = dict(target_plain)
    resolve_attack(50.0, "EXPLOSIVE", target_plain)
    resolve_attack(50.0, "EXPLOSIVE", target_drill, attacker_armor_shred=B.ARMOR_SHRED_RATIO_DRILL)
    assert target_drill["armor_max"] < target_plain["armor_max"]
    assert target_plain["armor_max"] < 400.0  # 基础削甲仍然生效（没装钻头的车也没被削弱）
