"""公频电台服务（模块 M）：本地狂欢节拼装 + 时代语料批处理（LLM 场景 3）。

数据流：

* `static/templates_default.json` 是**兜底池**，首启/断网/超预算都能开台（0 Token）；
* LLM 只在"跨时代"这类低频高影响时刻批量生成语料（单次 ≤ `LLM_BATCH_SIZE_MAX` 条）；
* 生成结果经 Pydantic 防火墙（必须含槽位、长度与动量区间夹紧）后落 `event_templates`；
* 展示时按 LRU 取最久未用的一条，用 `core/template_engine.py` 本地填槽（0 Token、0 延迟）。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.seed_loader import load_seed
from app.core.template_engine import pick_lru, render_template
from app.models import EventTemplate
from app.models.template import TemplateCategory
from app.schemas.template_agent import TemplateBatch
from app.services.game_init_service import now_timestamp
from app.services.llm_service import LlmScene, get_llm_service

logger = logging.getLogger("dawn_meow.radio")

DEFAULT_PHASE = "PHASE_1_SURFACE"
DEFAULT_BATCH_COUNT = 20

SYSTEM_PROMPT = (
    "你是《喵星破晓》里的地下公频电台编辑。世界观：人类升维离开后，星系托管 AI 欧米伽把猫猫判定为"
    "有机污染残渣，07 号地下避难所的二代智械拾荒猫正在废墟里重建文明，机器人们自建了匿名深网与公频电台。"
    "语气：冷幽默、温柔、有生活感，绝不出现现实世界政治、宗教、真实品牌或真实人物。"
)


def _category_value(raw: Any) -> str:
    """兼容三种入参：models 枚举 / schemas 枚举 / 纯字符串。"""
    return str(getattr(raw, "value", raw))


async def _count_templates(session: AsyncSession, slot_id: int, phase_id: str) -> int:
    total = await session.execute(
        select(func.count()).select_from(EventTemplate).where(
            EventTemplate.slot_id == slot_id, EventTemplate.phase_id == phase_id
        )
    )
    return int(total.scalar_one())


async def ensure_default_templates(session: AsyncSession, slot_id: int, phase_id: str) -> int:
    """从本地兜底池播种语料（幂等；已有该时代语料则跳过）。返回新增条数。"""
    if await _count_templates(session, slot_id, phase_id) > 0:
        return 0

    seed = load_seed("templates_default.json")
    phase_block = (seed.get("phases") or {}).get(phase_id) or {}
    added = 0
    for category, lines in phase_block.items():
        for line in lines:
            session.add(
                EventTemplate(
                    slot_id=slot_id,
                    phase_id=phase_id,
                    category=TemplateCategory(category),
                    template_text=line,
                    impact_stock=None,
                    impact_val=0.0,
                    last_used_time=None,
                    use_count=0,
                )
            )
            added += 1
    for line in seed.get("fallback_extra", []):
        session.add(
            EventTemplate(
                slot_id=slot_id,
                phase_id=phase_id,
                category=TemplateCategory.RADIO_NEWS,
                template_text=line,
                last_used_time=None,
                use_count=0,
            )
        )
        added += 1
    if added:
        await session.flush()
        logger.info("本地兜底语料已播种：slot=%s phase=%s 共 %s 条（0 Token）", slot_id, phase_id, added)
    return added


async def feed(
    session: AsyncSession,
    *,
    slot_id: int = 1,
    phase_id: str = DEFAULT_PHASE,
    limit: int = 12,
) -> dict[str, Any]:
    """取一段公频播报（本地填槽 + LRU 标记，不产生任何 LLM 调用）。"""
    seeded = await ensure_default_templates(session, slot_id, phase_id)
    rows = (
        await session.execute(
            select(EventTemplate).where(
                EventTemplate.slot_id == slot_id, EventTemplate.phase_id == phase_id
            )
        )
    ).scalars().all()

    picked = pick_lru(
        [
            {
                "id": row.id,
                "category": _category_value(row.category),
                "template_text": row.template_text,
                "impact_stock": row.impact_stock,
                "last_used_time": row.last_used_time,
            }
            for row in rows
        ],
        max(1, min(limit, 50)),
    )

    now = now_timestamp()
    by_id = {row.id: row for row in rows}
    items: list[dict[str, Any]] = []
    for entry in picked:
        row = by_id.get(entry["id"])
        if row is None:
            continue
        row.last_used_time = now
        row.use_count = int(row.use_count) + 1
        items.append(
            {
                "id": row.id,
                "category": _category_value(row.category),
                "text": render_template(row.template_text),
                "impact_stock": row.impact_stock,
                "template_text": row.template_text,
                "use_count": row.use_count,
            }
        )
    await session.commit()
    return {
        "phase_id": phase_id,
        "seeded": seeded,
        "pool_size": len(rows),
        "items": items,
    }


def _build_prompt(phase_id: str, count: int) -> tuple[str, str]:
    categories = "、".join(c.value for c in TemplateCategory)
    user = (
        f"请为时代阶段 `{phase_id}`（地表破晓：地下避难所、废墟拾荒、机器噪音引来天网警戒）"
        f"生成 {count} 条公频语料。\n"
        "严格输出 JSON 对象：{\"templates\": [{\"category\": \"RADIO_NEWS\", "
        "\"template_text\": \"...\", \"impact_stock\": null}]}\n"
        f"要求：\n"
        f"1) category 只能是 {categories}；三类都要有；\n"
        "2) template_text 每条 10~40 个汉字，**必须包含** {cat} 或 {building} 或 {resource} 或 {stock} 之一作为槽位占位符；\n"
        "2.1) 每一条都必须带槽位，漏掉槽位的条目会被直接丢弃；\n"
        "3) 每条独占一行风格的一句话，不要编号、不要 Markdown、不要解释；\n"
        "4) impact_stock 可填 FORGE / GRID / HELIUM3 / LOGISTICS 或 null。"
    )
    return SYSTEM_PROMPT, user


async def generate_batch(
    session: AsyncSession,
    *,
    slot_id: int = 1,
    phase_id: str = DEFAULT_PHASE,
    count: int = DEFAULT_BATCH_COUNT,
) -> dict[str, Any]:
    """时代语料批处理（LLM 场景 3）：失败/超预算自动走本地兜底池，玩家零感知。"""
    settings = get_settings()
    count = max(1, min(int(count), settings.llm_batch_size_max))
    await ensure_default_templates(session, slot_id, phase_id)

    service = get_llm_service()
    system, user = _build_prompt(phase_id, count)
    result = await service.complete_json(
        scene=LlmScene.PHASE_TEMPLATES,
        system=system,
        user=user,
        max_tokens=min(2048, 120 * count + 200),
        temperature=0.95,
    )

    cards: list[Any] = []
    rejected = 0
    if result.ok:
        raw = result.payload
        if isinstance(raw, dict):
            raw = raw.get("templates", [])
        # 防火墙 1：**逐条**校验，坏的丢掉、好的留下（一条不合格不该废掉整批）
        for item in raw if isinstance(raw, list) else []:
            try:
                cards.append(TemplateBatch.model_validate({"templates": [item]}).templates[0])
            except Exception as exc:
                rejected += 1
                logger.warning("语料被 Pydantic 防火墙拦截：%s（%s）", item, exc)
        if not cards:
            result.usage.fell_back = True
            result.usage.reason = "SCHEMA_REJECTED"

    existing = {
        text
        for (text,) in (
            await session.execute(
                select(EventTemplate.template_text).where(
                    EventTemplate.slot_id == slot_id, EventTemplate.phase_id == phase_id
                )
            )
        ).all()
    }

    source = "LLM"
    note: str | None = None
    if rejected and cards:
        note = f"{len(cards)} 条通过防火墙、{rejected} 条被拦下（不合规已丢弃）"
    if not cards:
        source = "FALLBACK"
        note = {
            "LLM_NOT_CONFIGURED": "未配置 LLM 密钥，保持本地语料池",
            "BUDGET_EXHAUSTED": "星区推演导演今日已下线（预算熔断），保持本地语料池",
        }.get(str(result.usage.reason), f"LLM 不可用（{result.usage.reason}），保持本地语料池")
        seed = load_seed("templates_default.json")
        pool = list((seed.get("phases") or {}).get(phase_id, {}).get("RADIO_NEWS", []))
        pool += list(seed.get("fallback_extra", []))
        cards = [
            {
                "category": TemplateCategory.RADIO_NEWS,
                "template_text": pool[index % len(pool)],
                "impact_stock": None,
                "impact_val": 0.0,
            }
            for index in range(count)
        ] if pool else []

    inserted = 0
    for card in cards[:count]:
        text = card.template_text if hasattr(card, "template_text") else card["template_text"]
        category = card.category if hasattr(card, "category") else card["category"]
        stock = getattr(card, "impact_stock", None) if hasattr(card, "impact_stock") else card.get("impact_stock")
        value = getattr(card, "impact_val", 0.0) if hasattr(card, "impact_val") else card.get("impact_val", 0.0)
        if text in existing:
            continue
        session.add(
            EventTemplate(
                slot_id=slot_id,
                phase_id=phase_id,
                category=TemplateCategory(_category_value(category)),
                template_text=text,
                impact_stock=stock,
                impact_val=float(value or 0.0),
                last_used_time=None,
                use_count=0,
            )
        )
        existing.add(text)
        inserted += 1
    await session.commit()

    return {
        "phase_id": phase_id,
        "requested": count,
        "generated": inserted,
        "rejected": rejected,
        "source": source,
        "note": note,
        "fell_back": result.usage.fell_back or source == "FALLBACK",
        "usage": result.usage.to_dict(),
        "budget": service.budget.snapshot(),
        "pool_size": await _count_templates(session, slot_id, phase_id),
    }
