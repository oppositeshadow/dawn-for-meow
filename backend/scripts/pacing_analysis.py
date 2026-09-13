"""中期节奏只读分析：19 节点科技 + 发射井四阶段要多久、哪一段容易空转。

不修改任何数据；改数值后重跑即可复核（《数值平衡表》§6 / §5）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import balance as B  # noqa: E402
from app.core.seed_loader import load_seed  # noqa: E402


def tech_timeline(geeks: int) -> list[tuple[int, float, float]]:
    """返回 [(tier, 有效算力, 所需小时)]，按阶梯累计。"""
    nodes = load_seed("techs_planet0.json")["techs"]
    by_tier: dict[int, float] = {}
    for node in nodes:
        by_tier[int(node["tier"])] = by_tier.get(int(node["tier"]), 0.0) + float(node["target_cost"])

    rows: list[tuple[int, float, float]] = []
    cumulative = 0.0
    rate = max(1, geeks) * B.GEEK_RESEARCH_PER_SEC
    for tier in sorted(by_tier):
        discount = B.TECH_TIER_TIME_DISCOUNT.get(tier - 1, 1.0)  # 上一层全通后享受的折扣
        effective = by_tier[tier] * discount
        cumulative += effective
        rows.append((tier, effective, cumulative / rate / 3600.0))
    return rows


def silo_material_hours(furnaces: int) -> float:
    """发射井四阶段：废铁靠拾荒、合金靠熔炼，取最慢的一项作为瓶颈。"""
    stages = B.LAUNCH_SILO_STAGES
    need: dict[str, float] = {}
    for stage in stages:
        for resource, amount in stage["cost"].items():
            need[resource] = need.get(resource, 0.0) + float(amount)
    # 合金：每座电炉 0.1/s（10 废铁↔1 合金）；废铁：2 只拾荒猫 × 0.5/s
    alloy_hours = need.get("alloys", 0.0) / max(1, furnaces) / 0.1 / 3600.0
    scrap_hours = need.get("scrap", 0.0) / (2 * B.SCAVENGER_SCRAP_PER_SEC) / 3600.0
    return max(alloy_hours, scrap_hours)


def main() -> None:
    print("== 母星 19 节点科技：按极客猫数量看累计耗时 ==")
    header = "Tier".ljust(6) + "".join(f"{g} 只极客".rjust(12) for g in (1, 2, 4, 6))
    print(header)
    timelines = {g: tech_timeline(g) for g in (1, 2, 4, 6)}
    for index, (tier, effective, _) in enumerate(timelines[1]):
        row = f"T{ tier:<5}" + "".join(f"{timelines[g][index][2]:>10.1f} h" for g in (1, 2, 4, 6))
        print(row + f"   （本层算力 {effective:,.0f}）")

    print("\n== 发射井四阶段材料：按电炉数量看瓶颈耗时 ==")
    total = {resource: sum(float(s["cost"].get(resource, 0.0)) for s in B.LAUNCH_SILO_STAGES)
             for resource in ("scrap", "alloys", "chips", "battery")}
    print(f"  合计需求：{total}")
    for furnaces in (1, 2, 3):
        print(f"  {furnaces} 座电炉 → 材料瓶颈 {silo_material_hours(furnaces):.1f} h")

    print("\n== 节点级内容节奏：什么时候解锁什么东西（1 / 2 / 4 只极客的累计小时）==")
    nodes = load_seed("techs_planet0.json")["techs"]
    tiers = sorted({int(node["tier"]) for node in nodes})
    tier_effective = {
        tier: sum(float(n["target_cost"]) for n in nodes if int(n["tier"]) == tier)
        * B.TECH_TIER_TIME_DISCOUNT.get(tier - 1, 1.0)
        for tier in tiers
    }
    elapsed: dict[int, float] = {}  # 极客数 → 累计算力
    for geeks in (1, 2, 4):
        elapsed[geeks] = 0.0
    for tier in tiers:
        discount = B.TECH_TIER_TIME_DISCOUNT.get(tier - 1, 1.0)
        for node in [n for n in nodes if int(n["tier"]) == tier]:
            node_cost = float(node["target_cost"]) * discount
            for geeks in elapsed:
                elapsed[geeks] += node_cost
            payload = node.get("buff_payload") or {}
            unlocks = [
                *(payload.get("unlock_facility") or []),
                *( [payload["unlock_system"]] if payload.get("unlock_system") else [] ),
                *([f"职业:{payload['unlock_job']}"] if payload.get("unlock_job") else []),
                *([f"兵种:{payload['unlock_unit']}"] if payload.get("unlock_unit") else []),
                *([f"系统:{payload['unlock_refine']}精炼"] if payload.get("unlock_refine") else []),
            ]
            if not unlocks:
                continue
            times = "  ".join(f"{geeks} 只：{elapsed[geeks] / max(1, geeks) / 3600:>5.2f} h" for geeks in (1, 2, 4))
            print(f"  {times}   {node['tech_name']} → {'、'.join(unlocks)}")
        _ = tier_effective


if __name__ == "__main__":
    main()
