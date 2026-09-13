"""LLM 场景 5 的 Pydantic 防火墙：《异星植物学图鉴》论文。"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

_TITLE_PATTERN = re.compile(r"[\u4e00-\u9fffA-Za-z0-9·\-\s《》]{4,24}")


class PlantPaper(BaseModel):
    """一条图鉴论文条目（标题 + 正文），LLM 只写文字，不碰任何数值。"""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=4, max_length=24)
    body: str = Field(min_length=20, max_length=220)

    @field_validator("title")
    @classmethod
    def _check_title(cls, value: str) -> str:
        text = value.strip()
        if not _TITLE_PATTERN.fullmatch(text):
            raise ValueError("title 只能由 4~24 个中英文/数字/书名号字符组成")
        return text

    @field_validator("body")
    @classmethod
    def _check_body(cls, value: str) -> str:
        text = value.strip()
        if any(token in text for token in ("http", "```", "<script", "{")):
            raise ValueError("body 含可疑内容")
        return text
