"""外星球特化科技树（模块 E4 + LLM 场景 2）。

口径见《数值平衡表》§6.3：每颗外星球 12 个特化节点、成本阶梯 500 → 2,000 → 8,000，
**LLM 只负责命题填空**（名称 / 风味 / 机制类型与载荷），成本、阶梯与 DAG 由 Python 夹紧。

* 每颗星球只在**首次登录**时生成一次（`is_agent_generated = true` 即视为已生成，可重 Roll）；
* 生成走**一次批量调用**（≤ 节点数张卡），避免 12 次串行请求拖慢切星；单卡重 Roll 走单次调用；
* 三道防火墙：Pydantic `TechCard` → DAG 结构由后端拼装（LLM 不给 parent）→ `fallback_techs.json` 兜底池。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import balance as B
from app.core.errors import NotFound
from app.core.seed_loader import load_seed
from app.models import TechRecord
from app.models.tech import TechStatus
from app.schemas.tech_agent import TechCard
from app.services.llm_service import LlmScene, get_llm_service

logger = logging.getLogger("dawn_meow.planet_tech")

SYSTEM_PROMPT = (
    "你是《喵星破晓》的星区推演导演。为这颗外星球设计特化科技卡：每张卡给 2~12 字的中文科技名、"
    "一句 2~40 字的风味文案、机制类型（PASSIVE_BUFF / CONVERSION / TRADE_OFF / UNLOCK_ABILITY）"
    "与一个小的 buff_payload。**不要输出成本、阶梯或前置科技**（这些由工程侧决定）。"
    "语气冷硬、有科幻质感，不要出现现实品牌。只输出 JSON。"
)


def _fallback_pool(planet_id: int) -> list[TechCard]:
    """本地兜底卡池（`fallback_techs.json`）；池子不足时循环取用。"""
    seed = load_seed("fallback_techs.json")
    raw = (seed.get("planets") or {}).get(str(planet_id)) or seed.get("generic") or []
    cards: list[TechCard] = []
    for item in raw:
        try:
            cards.append(TechCard.model_validate(item))
        except Exception as exc:  # pragma: no cover - 人为改坏种子才会触发
            logger.warning("兜底卡池条目未通过防火墙（planet=%s）：%s", planet_id, exc)
    if not cards:  # pragma: no cover - 双保险
        cards = [TechCard(tech_name="未命名特化", flavor_text="星区推演导演没有留下记录。", mechanic_type="PASSIVE_BUFF")]
    while len(cards) < B.STAR_TECH_NODE_COUNT:
        cards = cards + cards[: B.STAR_TECH_NODE_COUNT - len(cards)]
    return cards


def blueprint(planet_id: int) -> list[dict[str, Any]]:
    """节点骨架：ID / 阶梯 / 顺序 / 前置 / 成本**全部由后端拼**（LLM 无权改动）。"""
    tiers = list(B.STAR_TECH_TIERS)
    counters: dict[int, int] = {}
    ids: list[str] = []
    for tier in tiers:
        counters[tier] = counters.get(tier, 0) + 1
        ids.append(f"star_p{planet_id}_t{tier}_{counters[tier]}")
    by_tier: dict[int, list[str]] = {}
    for tier, tech_id in zip(tiers, ids):
        by_tier.setdefault(tier, []).append(tech_id)

    nodes: list[dict[str, Any]] = []
    for tech_id, tier in zip(ids, tiers):
        position = by_tier[tier].index(tech_id)
        if tier == 1:
            parents: list[str] = []
        else:
            previous = by_tier[tier - 1]
            parents = [previous[position] if position < len(previous) else previous[-1]]
        nodes.append(
            {
                "tech_id": tech_id,
                "tier": tier,
                "node_order": position + 1,
                "parent_ids": parents,
                "target_cost": float(B.STAR_TECH_TIER_COSTS[min(tier, len(B.STAR_TECH_TIER_COSTS)) - 1]),
            }
        )
    return nodes


def _user_prompt(planet_id: int, nodes: list[dict[str, Any]]) -> str:
    ladder = "、".join(
        f"Tier {tier} 成本 {B.STAR_TECH_TIER_COSTS[min(tier, len(B.STAR_TECH_TIER_COSTS)) - 1]:g}"
        for tier in sorted(set(B.STAR_TECH_TIERS))
    )
    return (
        f"星球代号：{B.PLANETS.get(planet_id, planet_id)}（planet_id={planet_id}）。\n"
        f"需要 {len(nodes)} 张卡，阶梯分布：{ladder}。\n"
        '请输出 JSON：{"cards": [{"tech_name": "地热虹吸管阵列", "flavor_text": "把岩浆的热量抽成电。",'
        ' "mechanic_type": "PASSIVE_BUFF", "buff_payload": {"power_kw": 5}}]}'
    )


def _accept_cards(payload: Any, *, count: int) -> tuple[list[TechCard], int]:
    """逐张过防火墙：合法的收下，非法的丢弃（不让一张坏卡毁掉整批）。"""
    raw = payload.get("cards") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return [], 0
    accepted: list[TechCard] = []
    rejected = 0
    for item in raw[:count]:
        try:
            accepted.append(TechCard.model_validate(item))
        except Exception as exc:
            rejected += 1
            logger.warning("特化科技卡未通过 Pydantic 防火墙：%s", exc)
    return accepted, rejected


async def ensure_specialized_techs(
    session: AsyncSession, slot_id: int, planet_id: int
) -> dict[str, Any] | None:
    """给一颗外星球铺特化科技树（幂等：已有 `is_agent_generated` 节点则跳过，也就不再调 LLM）。"""
    if planet_id == B.HOME_PLANET_ID:
        return None
    existing = (
        await session.execute(
            select(TechRecord.tech_id).where(
                TechRecord.slot_id == slot_id,
                TechRecord.planet_id == planet_id,
                TechRecord.is_agent_generated.is_(True),
            )
        )
    ).scalars().all()
    if existing:
        return None
    if planet_id not in B.PLANETS:
        raise NotFound("BAD_REQUEST", f"未知星球 planet_id={planet_id}")

    nodes = blueprint(planet_id)
    pool = _fallback_pool(planet_id)
    cards: list[TechCard] = [pool[index % len(pool)] for index in range(len(nodes))]

    source = "FALLBACK"
    usage: dict[str, Any] = {}
    rejected = 0
    service = get_llm_service()
    if service.configured:
        result = await service.complete_json(
            scene=LlmScene.TECH_CARD,
            system=SYSTEM_PROMPT,
            user=_user_prompt(planet_id, nodes),
            max_tokens=1600,
            temperature=0.9,
        )
        if result.ok:
            accepted, rejected = _accept_cards(result.payload, count=len(nodes))
            for index, card in enumerate(accepted):
                cards[index] = card
            if accepted:
                source = "LLM"
        usage = result.usage.to_dict()

    for node, card in zip(nodes, cards):
        session.add(
            TechRecord(
                slot_id=slot_id,
                planet_id=planet_id,
                tech_id=node["tech_id"],
                tech_name=card.tech_name,
                parent_ids=node["parent_ids"],
                tier=node["tier"],
                node_order=node["node_order"],
                status=TechStatus.LOCKED,
                current_progress=0.0,
                target_cost=node["target_cost"],
                is_agent_generated=True,
                flavor_text=card.flavor_text,
                mechanic_type=card.mechanic_type,
                buff_payload=dict(card.buff_payload),
            )
        )
    await session.flush()
    logger.info(
        "特化科技树生成：slot=%s planet=%s 节点=%s source=%s 拒收=%s",
        slot_id,
        planet_id,
        len(nodes),
        source,
        rejected,
    )
    return {
        "planet_id": planet_id,
        "count": len(nodes),
        "source": source,
        "rejected_cards": rejected,
        "usage": usage,
        "node_ids": [node["tech_id"] for node in nodes],
    }


async def reroll_card(
    session: AsyncSession,
    record: TechRecord,
    *,
    slot_id: int = B.DEFAULT_SLOT_ID,
    planet_id: int = B.HOME_PLANET_ID,
) -> dict[str, Any]:
    """单张卡重 Roll：LLM 单卡生成，失败/被拦则从本地池换一张不同名的卡。"""
    pool = _fallback_pool(planet_id)
    alternatives = [card for card in pool if card.tech_name != record.tech_name] or pool
    index = (int(record.node_order) + int(record.tier)) % len(alternatives)
    chosen = alternatives[index]

    source = "FALLBACK"
    usage: dict[str, Any] = {}
    service = get_llm_service()
    if service.configured:
        result = await service.complete_json(
            scene=LlmScene.TECH_CARD,
            system=SYSTEM_PROMPT,
            user=f"重 Roll【{record.tech_name}】（Tier {record.tier}，星球 planet_id={planet_id}）。"
            '请输出 JSON：{"cards": [{"tech_name": "新名字", "flavor_text": "新的风味文案。",'
            ' "mechanic_type": "PASSIVE_BUFF", "buff_payload": {"power_kw": 5}}]}',
            max_tokens=400,
            temperature=1.0,
        )
        if result.ok:
            accepted, _ = _accept_cards(result.payload, count=1)
            if accepted:
                chosen = accepted[0]
                source = "LLM"
        usage = result.usage.to_dict()

    record.tech_name = chosen.tech_name
    record.flavor_text = chosen.flavor_text
    record.mechanic_type = chosen.mechanic_type
    record.buff_payload = dict(chosen.buff_payload)
    await session.flush()
    logger.info("特化科技重 Roll：slot=%s planet=%s tech=%s source=%s", slot_id, planet_id, record.tech_id, source)
    return {
        "tech_id": record.tech_id,
        "tech_name": record.tech_name,
        "flavor_text": record.flavor_text,
        "mechanic_type": record.mechanic_type,
        "buff_payload": record.buff_payload,
        "source": source,
        "usage": usage,
    }
