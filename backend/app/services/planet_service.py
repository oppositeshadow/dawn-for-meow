"""星球服务（模块 K3 + LLM 场景 1）：登录新行星时生成生态环境与词缀。

口径（代码结构稿 §6.2 场景 1）：**每颗星球只调用 1 次** LLM，失败自动走本地生态标签池（玩家零感知）；
母星（planet 0）的生态标签是手工定稿，不调用 LLM。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import balance as B
from app.core.errors import NotFound
from app.models import PlanetState
from app.schemas.planet_agent import PlanetBiome
from app.services.llm_service import LlmScene, get_llm_service

logger = logging.getLogger("dawn_meow.planet")

#: 本地兜底生态池（LLM 不可用时使用；加内容只改这里）
FALLBACK_BIOMES: dict[int, PlanetBiome] = {
    1: PlanetBiome(biome_tag="赤色熔炉带", affixes=["岩浆潮汐", "地热脉动"]),
    2: PlanetBiome(biome_tag="永冻镜海", affixes=["极夜长哨", "冰下回声"]),
    3: PlanetBiome(biome_tag="碎星环带", affixes=["氦雨", "失控轨道"]),
}

SYSTEM_PROMPT = (
    "你是《喵星破晓》的星区推演导演。为下面的行星起一个 4~10 字的生态标签，"
    "再给 1~3 条 2~6 字的行星词缀（可以是气候、地貌、危险或机遇）。"
    "语气冷硬、有科幻质感，不要出现现实地名与品牌。只输出 JSON。"
)


async def ensure_biome(
    session: AsyncSession, slot_id: int, planet_id: int
) -> dict[str, Any] | None:
    """给一颗外星球补上生态标签（幂等：已有标签则跳过，也就不再调用 LLM）。"""
    planet = await session.get(PlanetState, (slot_id, planet_id))
    if planet is None:
        raise NotFound("BAD_REQUEST", f"未知星球 planet_id={planet_id}")
    if planet_id == B.HOME_PLANET_ID or planet.biome_tag:
        # 生态标签已存在时不再调 LLM，但仍要保证特化科技树已铺好（幂等、0 Token）
        if planet_id != B.HOME_PLANET_ID:
            from app.services import planet_tech_service

            specialized = await planet_tech_service.ensure_specialized_techs(session, slot_id, planet_id)
            if specialized:
                return {
                    "planet_id": planet_id,
                    "biome_tag": planet.biome_tag,
                    "affixes": [],
                    "source": None,
                    "specialized_techs": specialized,
                    "usage": {},
                }
        return None

    fallback = FALLBACK_BIOMES.get(planet_id) or PlanetBiome(biome_tag="未命名星域", affixes=[])
    biome = fallback
    source = "FALLBACK"
    usage: dict[str, Any] = {}
    service = get_llm_service()
    if service.configured:
        result = await service.complete_json(
            scene=LlmScene.PLANET_ENTRY,
            system=SYSTEM_PROMPT,
            user=f"星球代号：{B.PLANETS.get(planet_id)}（planet_id={planet_id}）。请输出 JSON："
            '{"biome_tag": "赤色熔炉带", "affixes": ["岩浆潮汐", "地热脉动"]}',
            max_tokens=200,
            temperature=0.9,
        )
        if result.ok and isinstance(result.payload, dict):
            try:
                biome = PlanetBiome.model_validate(result.payload)
                source = "LLM"
            except Exception as exc:  # 防火墙 1 拦截 ⇒ 走本地生态池
                logger.warning("行星生态未通过 Pydantic 防火墙：%s", exc)
        usage = result.usage.to_dict()

    label = biome.biome_tag if not biome.affixes else f"{biome.biome_tag} · {'、'.join(biome.affixes[:2])}"
    planet.biome_tag = label[:64]
    # LLM 场景 2：铺该星球的特化科技树（首次登录 1 次批量生成，失败走本地卡池）
    from app.services import planet_tech_service

    specialized = await planet_tech_service.ensure_specialized_techs(session, slot_id, planet_id)
    await session.flush()
    logger.info("行星生态标签生成：slot=%s planet=%s source=%s tag=%s", slot_id, planet_id, source, planet.biome_tag)
    return {
        "planet_id": planet_id,
        "biome_tag": planet.biome_tag,
        "affixes": list(biome.affixes),
        "source": source,
        "specialized_techs": specialized,
        "usage": usage,
    }
