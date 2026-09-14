"""星系法典政令（《数值平衡表》§15.2）：消耗文明凝聚力（`save_slot.unity`）点亮。

* 定义在 `static/doctrines.json`（唯一出处），玩家状态写 `save_slot.doctrines`（`{id: level}`）；
* **效果接线分批做**：白名单见 `WIRED_EFFECTS`，未接线的键照旧只展示（沿用 §6.4 的三类载荷原则）；
* 已点亮的政令**不可退点**（凝聚力不返还），避免反复开关刷效果。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequest, InsufficientResource, NotFound
from app.core.seed_loader import load_seed
from app.models import SaveSlot

#: 已接入结算的效果键（其余键只展示；接线时往这里加，并同步《数值平衡表》§15.2）
WIRED_EFFECTS: tuple[str, ...] = (
    "production_multiplier",  # 全员生产效率
    "cat_capacity",           # 各星球承载力 K
    "fleet_armor",            # 载具装甲（与科技的同一条通道相加）
    "suspicion_growth",       # 警戒度增速
    "morale",                 # 全星系士气（并入生产效率通道）
    "black_market_fee",       # 黑市手续费
    "intel_speed",            # 情报破译速度
    "air_defense",            # 对空中单位伤害（突击无人机等 air 目标）
)

#: **暂不接线**（没有真实接入点，接了就成假接线；等玩法落地再接）
PENDING_EFFECTS: tuple[str, ...] = ()


def doctrine_defs() -> list[dict[str, Any]]:
    return list(load_seed("doctrines.json")["doctrines"])


def doctrine_map() -> dict[str, dict[str, Any]]:
    return {item["doctrine_id"]: item for item in doctrine_defs()}


async def list_view(session: AsyncSession, *, slot_id: int = 1) -> dict[str, Any]:
    save = await session.get(SaveSlot, slot_id)
    if save is None:
        raise NotFound("SAVE_NOT_FOUND", f"槽位 {slot_id} 还没有存档")
    unlocked = dict(save.doctrines or {})
    return {
        "unity": round(float(save.unity), 2),
        "unlocked_count": len(unlocked),
        "total": len(doctrine_defs()),
        "wired_effects": list(WIRED_EFFECTS),
        "doctrines": [
            {
                "doctrine_id": item["doctrine_id"],
                "name": item["name"],
                "cost": float(item["cost"]),
                "effect_text": item["effect_text"],
                "effects": dict(item["effects"]),
                "unlocked": item["doctrine_id"] in unlocked,
                "affordable": float(save.unity) >= float(item["cost"]),
            }
            for item in doctrine_defs()
        ],
    }


async def unlock(
    session: AsyncSession, doctrine_id: str, *, slot_id: int = 1
) -> dict[str, Any]:
    """点亮一条政令：校验存在性 → 是否已点 → 凝聚力是否够 → 扣点并记录。"""
    spec = doctrine_map().get(doctrine_id)
    if spec is None:
        raise BadRequest("BAD_REQUEST", f"未知政令 doctrine_id={doctrine_id}")
    save = await session.get(SaveSlot, slot_id)
    if save is None:
        raise NotFound("SAVE_NOT_FOUND", f"槽位 {slot_id} 还没有存档")

    unlocked = dict(save.doctrines or {})
    if doctrine_id in unlocked:
        raise BadRequest("DOCTRINE_ALREADY_UNLOCKED", f"【{spec['name']}】已经点亮过了")
    cost = float(spec["cost"])
    if float(save.unity) < cost:
        raise InsufficientResource(
            detail=f"点亮【{spec['name']}】需要 {cost:g} 文明凝聚力，当前只有 {float(save.unity):g}"
            "（派猫当【文明呼噜大师】才会产出）"
        )

    save.unity = round(float(save.unity) - cost, 2)
    unlocked[doctrine_id] = 1
    save.doctrines = unlocked  # JSON 列必须整条替换
    await session.flush()
    return {
        "doctrine_id": doctrine_id,
        "name": spec["name"],
        "cost": cost,
        "unity_left": float(save.unity),
        "unlocked_count": len(unlocked),
        "effects": dict(spec["effects"]),
        "wired": {key: value for key, value in spec["effects"].items() if key in WIRED_EFFECTS},
        "pending": {key: value for key, value in spec["effects"].items() if key not in WIRED_EFFECTS},
    }


async def active_effects(session: AsyncSession, slot_id: int) -> dict[str, float]:
    """已点亮政令里**已接线**效果的汇总（同类键相加）。"""
    # 用显式查询 + populate_existing：`session.get` 会命中 identity map 里的旧快照，
    # 拿到刚被接口写过的政令（测试实测：同一会话先读过 SaveSlot 就永远读到空政令表）
    save = (
        await session.execute(
            select(SaveSlot)
            .where(SaveSlot.slot_id == slot_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if save is None:
        return dict.fromkeys(WIRED_EFFECTS, 0.0)
    unlocked = set((save.doctrines or {}).keys())
    totals = dict.fromkeys(WIRED_EFFECTS, 0.0)
    for item in doctrine_defs():
        if item["doctrine_id"] not in unlocked:
            continue
        for key, value in item["effects"].items():
            if key in totals:
                totals[key] += float(value)
    return {key: round(value, 4) for key, value in totals.items()}
