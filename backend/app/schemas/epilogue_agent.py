"""LLM 场景 6（通关碑文）的 Pydantic 边界防火墙。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Epilogue(BaseModel):
    model_config = ConfigDict(extra="ignore")

    epitaph: str = Field(min_length=40, max_length=400, description="猫猫文明星际史诗碑文")
    title: str = Field(default="猫猫文明星际史诗碑文", min_length=2, max_length=32)
