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
    armor_reduction = min(B.ARMOR_REDUCTION_MAX, float(unit.get("armor", 0)) / B.ARMOR_REDUCTION_DIVISOR)
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


def resolve_attack(
    attacker_atk: float,
    damage_type: str,
    target: dict[str, float],
) -> tuple[dict[str, float], float, bool]:
    """单次攻击结算：返回（更新后的 target、对结构的实际伤害、是否触发弹射）。"""
    remaining = max(0.0, float(attacker_atk))

    # ① 护盾层
    if target.get("shield", 0.0) > 0 and remaining > 0:
        shield_mult = B.SHIELD_DAMAGE_MULTIPLIER.get(damage_type, 1.0)
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
            armor_reduction = min(B.ARMOR_REDUCTION_MAX, armor / B.ARMOR_REDUCTION_DIVISOR)
            real_damage_to_hull = remaining * (1.0 - armor_reduction)
            if damage_type in ("KINETIC", "EXPLOSIVE"):
                shred = real_damage_to_hull * B.ARMOR_SHRED_RATIO
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
        "shield": float(spec.get("shield", 0.0)),
        "armor": float(spec.get("armor", 0.0)),
        "armor_max": float(spec.get("armor", 0.0)),
        "hull": float(spec.get("hull", 0.0)),
        "dps": float(spec.get("dps", 0.0)),
        "damage_type": spec.get("damage_type", "KINETIC"),
    }


def is_alive(unit: Mapping[str, float]) -> bool:
    return float(unit.get("hull", 0.0)) > 0


def resolve_skirmish(
    attackers: Sequence[dict[str, Any]],
    defenders: Sequence[dict[str, Any]],
    *,
    attacker_morale: float = 1.0,
    max_rounds: int = B.COMBAT_MAX_ROUNDS,
) -> dict[str, Any]:
    """一次完整交战（每回合 1 秒，双方集火各自最前的存活目标）。

    返回 `winner`（ATTACK / DEFENSE / TIMEOUT）、回合数、双方单位终态与结构化战报。
    """
    log: list[str] = []
    for unit in attackers:
        unit["morale"] = attacker_morale

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
        if attacker_target is not None:
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
