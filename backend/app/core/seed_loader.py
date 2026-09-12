"""静态 JSON 种子加载器（"定义进 JSON"原则的读取入口）。

策划定义（工种 / 设施 / 小游戏 / 母星科技树）放在 `backend/static/*.json`，
玩家状态进数据库；加内容只改 JSON 与《数值平衡表》，永不改表结构。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import STATIC_DIR


class SeedFileMissing(FileNotFoundError):
    """种子文件缺失或解析失败。"""


@lru_cache(maxsize=32)
def load_seed(file_name: str) -> dict[str, Any]:
    """读取并缓存一个种子 JSON（缓存仅在进程内，改文件后重启即可生效）。"""
    path: Path = STATIC_DIR / file_name
    if not path.is_file():
        raise SeedFileMissing(f"种子文件不存在：{path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - 人为破坏文件才会触发
        raise SeedFileMissing(f"种子文件 JSON 解析失败：{path}（{exc}）") from exc


def clear_cache() -> None:
    load_seed.cache_clear()


def job_defs() -> list[dict[str, Any]]:
    return list(load_seed("jobs.json")["jobs"])


def facility_defs() -> list[dict[str, Any]]:
    return list(load_seed("facilities.json")["facilities"])


def facility_def_map() -> dict[str, dict[str, Any]]:
    return {item["facility_id"]: item for item in facility_defs()}


def minigame_defs() -> list[dict[str, Any]]:
    return list(load_seed("minigames.json")["minigames"])


def tech_defs_planet0() -> list[dict[str, Any]]:
    return list(load_seed("techs_planet0.json")["techs"])
