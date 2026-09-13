"""异星植物学图鉴论文（模块 H2 + LLM 场景 5）。

口径（代码结构稿 §6.2 场景 5）：**每种猫草只生成 1 次**（全案 ≤ 25~30 次），失败走本地模板文案；
论文只写文字，**不带任何数值加成**（数值仍以《数值平衡表》§10 为准）。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import balance as B
from app.models import GardenState
from app.schemas.plant_agent import PlantPaper
from app.services.game_init_service import now_timestamp
from app.services.llm_service import LlmScene, get_llm_service

logger = logging.getLogger("dawn_meow.plant_codex")

SCHOOL_LABELS = {
    "ECOLOGY": "生态温饱",
    "ENERGY": "能源与降噪",
    "MILITARY": "军备与防御",
    "DARKNET": "暗网暴利",
}

SYSTEM_PROMPT = (
    "你是《喵星破晓》的星区推演导演，正在为避难所的《异星植物学图鉴》写条目。"
    "给定一株猫草的图鉴数据，写一个 6~20 字的条目标题与一段 40~120 字的观察笔记。"
    "语气冷静、像野外考察记录，可以提到猫猫的用法，但不要出现现实品牌与数值公式。只输出 JSON。"
)


def fallback_paper(plant: dict[str, Any]) -> PlantPaper:
    """本地模板文案：0 Token、确定性、永远可用。"""
    name = plant.get("name", "未知猫草")
    school = SCHOOL_LABELS.get(plant.get("school", ""), "未分类")
    harvest = "、".join(f"{key} {value:g}" for key, value in (plant.get("harvest") or {}).items())
    halo = "、".join(f"{key} {value:g}" for key, value in (plant.get("halo") or {}).items())
    body = (
        f"样本编号 {plant.get('plant_id', 'unknown')}，归入【{school}】流派。"
        f"成熟后单株产出：{harvest or '无记录'}。"
        f"{f'在田光环：{halo}。' if halo else ''}"
        "记录员备注：同一格连作会加快变异，建议搭配不同流派轮作。"
    )
    return PlantPaper(title=f"{name}观察记录", body=body)


async def ensure_paper(
    session: AsyncSession,
    garden: GardenState,
    plant: dict[str, Any],
    *,
    slot_id: int = B.DEFAULT_SLOT_ID,
    allow_llm: bool = True,
) -> dict[str, Any] | None:
    """给新收录的猫草补一条图鉴论文（幂等：已写过就跳过，也就不再调用 LLM）。

    `allow_llm=False` 用于**离线结算路径**（机械臂批量补写）：离线补算绝不等网络，一律走本地模板文案。
    """
    plant_id = str(plant.get("plant_id") or "")
    if not plant_id:
        return None
    papers = dict(garden.codex_papers or {})
    if plant_id in papers:
        return None

    paper = fallback_paper(plant)
    source = "FALLBACK"
    usage: dict[str, Any] = {}
    service = get_llm_service()
    if allow_llm and service.configured:
        result = await service.complete_json(
            scene=LlmScene.PLANT_CODEX,
            system=SYSTEM_PROMPT,
            user=(
                f"猫草：{plant.get('name')}（{plant_id}），流派 {SCHOOL_LABELS.get(plant.get('school', ''), '未分类')}，"
                f"成熟产出 {plant.get('harvest')}，在田光环 {plant.get('halo') or '无'}。"
                '请输出 JSON：{"title": "荧光苔藓的夜光节律", "body": "……"}'
            ),
            max_tokens=400,
            temperature=0.9,
        )
        if result.ok and isinstance(result.payload, dict):
            try:
                paper = PlantPaper.model_validate(result.payload)
                source = "LLM"
            except Exception as exc:  # 防火墙 1 拦截 ⇒ 用本地模板文案
                logger.warning("图鉴论文未通过 Pydantic 防火墙：%s", exc)
        usage = result.usage.to_dict()

    entry = {
        "plant_id": plant_id,
        "name": plant.get("name"),
        "school": plant.get("school"),
        "title": paper.title,
        "body": paper.body,
        "source": source,
        "at": now_timestamp(),
    }
    papers[plant_id] = entry
    garden.codex_papers = papers  # JSON 列必须整条替换（就地改会漏写库）
    logger.info("图鉴论文生成：slot=%s plant=%s source=%s", slot_id, plant_id, source)
    entry_with_usage = dict(entry)
    entry_with_usage["usage"] = usage
    return entry_with_usage
