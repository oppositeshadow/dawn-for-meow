"""三层抗性交火结算引擎（模块 G，数值平衡表 §9）。

纯函数、确定性、不碰数据库：护盾（能量层）→ 装甲（百分比减伤 + 破甲削蚀）→ 结构（归零 100% 弹射免死）。
弹药相克：`LASER` 对护盾 ×1.8，`KINETIC / EXPLOSIVE` ×0.8 且削蚀装甲上限。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.core import balance as B


def combat_power(unit: Mapping[str, float]) -> float:
    """战力评分（仅用于界面排序，不参与伤害结算）。"""
    weights = B.COMBAT_POWER_WEIGHTS
    layers = float(unit.get("shield", 0)) + float(unit.get("armor", 0)) + float(unit.get("hull", 0))
    armor_reduction = armor_reduction_ratio(float(unit.get("armor", 0)))
    return round(
        weights["layers"] * layers
        + weights["dps"] * float(unit.get("dps", 0))
        + weights["armor_reduction"] * armor_reduction * 100,
        2,
    )


def morale_multiplier(catnip_ratio: float) -> float:
    """士气修正（§9.6）：猫薄荷爆仓 ×1.10、紧张 ×0.70。"""
    if catnip_ratio >= B.MORALE_FULL_THRESHOLD:
        return B.MORALE_FULL_MULTIPLIER
    if catnip_ratio < B.MORALE_LOW_THRESHOLD:
        return B.MORALE_LOW_MULTIPLIER
    return 1.0


def armor_reduction_ratio(armor: float) -> float:
    """装甲减伤率（《数值平衡表》§9.2）。

    现行口径（v1.37 拍板）：**MOBA 式凸曲线** `减伤率 = 上限 × 装甲 ÷ (装甲 + K)`，K = 100、上限 75%。

    * 没有硬封顶 —— 每一点装甲都真的降伤（旧线性口径在装甲 300 以上完全浪费）；
    * 装甲 200 处仍是 50%（与旧口径一致，手感不断层）：`0.75 × 200 ÷ 300 = 0.5`；
    * 装甲 500 处 62.5%（旧口径是封顶 75%），重甲敌人因此**相对变强**，已同步把关底近卫装甲
      从 500 下调到 430（§9.5）；
    * 本函数是唯一减伤入口，`resolve_attack` 与战力评分（`combat_power`）共用它。
    """
    value = max(0.0, float(armor))
    return B.ARMOR_REDUCTION_MAX * value / (value + B.ARMOR_REDUCTION_CURVE_K)


def resolve_attack(
    attacker_atk: float,
    damage_type: str,
    target: dict[str, float],
    attacker_vs_shield: float | None = None,
    attacker_armor_shred: float | None = None,
    air_defense: float = 0.0,
) -> tuple[dict[str, float], float, bool]:
    """单次攻击结算：返回（更新后的 target、对结构的实际伤害、是否触发弹射）。"""
    # 防空加成只对**空中目标**生效：判断放在这里（单一位置），调用方无需再判一次
    air_bonus = max(0.0, float(air_defense)) if target.get("air") else 0.0
    remaining = max(0.0, float(attacker_atk) * (1.0 + air_bonus))

    # ① 护盾层
    if target.get("shield", 0.0) > 0 and remaining > 0:
        # 模块【高频聚焦激光】给的是"该车对护盾的倍率"：与全局相克表**取较大值**，
        # 不做相乘（否则同一份收益记两次账，§9.10 明确禁止）。
        shield_mult = B.SHIELD_DAMAGE_MULTIPLIER.get(damage_type, 1.0)
        if attacker_vs_shield:
            shield_mult = max(shield_mult, float(attacker_vs_shield))
        effective = remaining * shield_mult
        if target["shield"] >= effective:
            target["shield"] -= effective
            remaining = 0.0
        else:
            remaining -= target["shield"] / shield_mult
            target["shield"] = 0.0

    # ② 装甲层（百分比减伤；破甲武器额外削蚀装甲上限）
    real_damage_to_hull = 0.0
    if remaining > 0:
        armor = float(target.get("armor", 0.0))
        if armor > 0:
            armor_reduction = armor_reduction_ratio(armor)
            real_damage_to_hull = remaining * (1.0 - armor_reduction)
            if damage_type in ("KINETIC", "EXPLOSIVE"):
                # 破甲系数：取"全局基础"与"该车模块"的较大值（钻头模块提高它，不削弱别人）
                shred_ratio = B.ARMOR_SHRED_RATIO
                if attacker_armor_shred:
                    shred_ratio = max(shred_ratio, float(attacker_armor_shred))
                shred = real_damage_to_hull * shred_ratio
                target["armor"] = max(0.0, armor - shred)
                if "armor_max" in target:
                    target["armor_max"] = max(0.0, float(target["armor_max"]) - shred)
        else:
            real_damage_to_hull = remaining

    # ③ 结构层：归零即 100% 弹射免死
    target["hull"] = float(target.get("hull", 0.0)) - real_damage_to_hull
    ejected = False
    if target["hull"] <= 0:
        target["hull"] = 0.0
        ejected = True
    return target, real_damage_to_hull, ejected


def unit_from_spec(spec: Mapping[str, Any], *, prefix: str = "unit") -> dict[str, Any]:
    """按锚点表造一个满血战斗单位快照。"""
    return {
        "id": prefix,
        "name": spec.get("name", prefix),
        "air": bool(spec.get("air", False)),  # 空中单位（政令【红点防空标准】对它加伤）
        "shield": float(spec.get("shield", 0.0)),
        "armor": float(spec.get("armor", 0.0)),
        "armor_max": float(spec.get("armor", 0.0)),
        "hull": float(spec.get("hull", 0.0)),
        "dps": float(spec.get("dps", 0.0)),
        "damage_type": spec.get("damage_type", "KINETIC"),
    }


def is_alive(unit: Mapping[str, float]) -> bool:
    return float(unit.get("hull", 0.0)) > 0


def apply_module_effects(unit: dict[str, Any], modules: list[str] | None) -> dict[str, Any]:
    """把车载模块的效果写进该单位的战斗快照（《数值平衡表》§9.10）。

    * 只影响**装模块的那辆车**，不改全军；
    * `vs_shield` 走"取较大值"而不是相乘，杜绝与全局相克表重复记账；
    * 装模块的车在快照上留下 `modules` 列表，便于战报与界面追溯。
    """
    installed = [item for item in (modules or []) if item in B.VEHICLE_MODULES]
    if not installed:
        return unit
    dps_bonus = 0.0
    armor_bonus = 0.0
    vs_shield = 0.0
    for module_id in installed:
        effects = B.VEHICLE_MODULES[module_id].get("effects", {})
        dps_bonus += float(effects.get("dps_bonus", 0.0))
        armor_bonus += float(effects.get("armor_bonus", 0.0))
        vs_shield = max(vs_shield, float(effects.get("vs_shield", 0.0)))
    if dps_bonus:
        unit["dps"] = round(float(unit.get("dps", 0.0)) * (1.0 + dps_bonus), 4)
    if armor_bonus:
        unit["armor"] = round(float(unit.get("armor", 0.0)) * (1.0 + armor_bonus), 4)
        if "armor_max" in unit:
            unit["armor_max"] = round(float(unit["armor_max"]) * (1.0 + armor_bonus), 4)
    if vs_shield:
        unit["vs_shield"] = vs_shield
    armor_shred = 0.0
    for module_id in installed:
        armor_shred = max(armor_shred, float(B.VEHICLE_MODULES[module_id].get("effects", {}).get("armor_shred", 0.0)))
    if armor_shred:
        unit["armor_shred"] = armor_shred
    unit["modules"] = installed
    return unit


def resolve_skirmish(
    attackers: Sequence[dict[str, Any]],
    defenders: Sequence[dict[str, Any]],
    *,
    attacker_morale: float = 1.0,
    attacker_dps_bonus: float = 0.0,
    attacker_armor_bonus: float = 0.0,
    attacker_air_defense: float = 0.0,
    defender_stun_rounds: int = 0,
    max_rounds: int = B.COMBAT_MAX_ROUNDS,
) -> dict[str, Any]:
    """一次完整交战（每回合 1 秒，双方集火各自最前的存活目标）。

    返回 `winner`（ATTACK / DEFENSE / TIMEOUT）、回合数、双方单位终态与结构化战报。
    """
    log: list[str] = []
    # 全军装甲加成（科技 `fleet_armor` / `armor_bonus`）：在结算最外层一次性生效，
    # 远征 / 伏击 / 突袭拦截 / 三段决战四条战斗链共用同一入口，杜绝"只有一半战斗吃得到"。
    if attacker_armor_bonus:
        factor = 1.0 + float(attacker_armor_bonus)
        for unit in attackers:
            unit["armor"] = float(unit.get("armor", 0.0)) * factor
            if "armor_max" in unit:
                unit["armor_max"] = float(unit["armor_max"]) * factor
    for unit in attackers:
        unit["morale"] = attacker_morale
        if attacker_dps_bonus:
            unit["dps"] = float(unit.get("dps", 0.0)) * (1.0 + attacker_dps_bonus)

    rounds = 0
    while rounds < max_rounds and any(is_alive(u) for u in attackers) and any(is_alive(u) for u in defenders):
        rounds += 1
        # 攻方集火：所有存活单位打同一个目标（最前的存活敌人）
        target = next((u for u in defenders if is_alive(u)), None)
        if target is not None:
            for attacker in attackers:
                if not is_alive(attacker):
                    continue
                before = float(target["hull"])
                _, damage, ejected = resolve_attack(
                    float(attacker["dps"]) * B.COMBAT_ROUND_SECONDS * float(attacker.get("morale", 1.0)),
                    str(attacker.get("damage_type", "KINETIC")),
                    target,
                    attacker_vs_shield=attacker.get("vs_shield"),
                    attacker_armor_shred=attacker.get("armor_shred"),
                    # 政令【全星系红点防空标准】：对**空中目标**（如突击无人机）伤害 +25%
                    # （是否空中由 resolve_attack 内部判定，这里直接传全军加成）
                    air_defense=attacker_air_defense,
                )
                if damage > 0:
                    log.append(
                        f"R{rounds} {attacker['name']} → {target['name']}：结构 −{damage:.1f}"
                        + (f"（{target['name']} 触发紧急弹射，乘员免死）" if ejected else "")
                    )
                if ejected:
                    break
        # 守方反击
        attacker_target = next((u for u in attackers if is_alive(u)), None)
        if attacker_target is not None and rounds > defender_stun_rounds:
            for defender in defenders:
                if not is_alive(defender):
                    continue
                _, damage, ejected = resolve_attack(
                    float(defender["dps"]) * B.COMBAT_ROUND_SECONDS,
                    str(defender.get("damage_type", "KINETIC")),
                    attacker_target,
                )
                if damage > 0:
                    log.append(
                        f"R{rounds} {defender['name']} → {attacker_target['name']}：结构 −{damage:.1f}"
                        + (f"（{attacker_target['name']} 触发紧急弹射）" if ejected else "")
                    )
                if ejected:
                    break
        elif attacker_target is not None and rounds == defender_stun_rounds:
            log.append(f"R{rounds} 电磁脉冲生效：敌方瘫痪 {defender_stun_rounds} 秒，本轮无法反击")

    attackers_alive = any(is_alive(u) for u in attackers)
    defenders_alive = any(is_alive(u) for u in defenders)
    if attackers_alive and not defenders_alive:
        winner = "ATTACK"
    elif defenders_alive and not attackers_alive:
        winner = "DEFENSE"
    else:
        winner = "TIMEOUT"

    return {
        "winner": winner,
        "rounds": rounds,
        "duration_seconds": rounds * B.COMBAT_ROUND_SECONDS,
        "attackers": [dict(u) for u in attackers],
        "defenders": [dict(u) for u in defenders],
        "ejected": [u["name"] for u in list(attackers) + list(defenders) if not is_alive(u)],
        "log": log[-40:],
    }
