"""LLM 场景 1（新行星生态推演）的 Pydantic 边界防火墙。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PlanetBiome(BaseModel):
    """生态标签 + 词缀：长度与数量全部夹死，越界即判不合格走兜底池。"""

    model_config = ConfigDict(extra="ignore")

    biome_tag: str = Field(min_length=2, max_length=32, description="生态标签，如「赤色熔炉带」")
    affixes: list[str] = Field(default_factory=list, max_length=3, description="行星词缀 0~3 条")
