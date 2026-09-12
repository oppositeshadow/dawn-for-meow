"""本地狂欢节模板引擎（模块 M1：Mad-Libs 槽位随机拼装，0 Token、0 延迟）。

规则（代码结构稿 §6.2 场景 3 / 数据库设计定稿 event_templates 表）：

* 模板文本里的 `{cat}` `{building}` `{resource}` `{stock}` 槽位由本引擎本地填充；
* 展示走 **LRU**：优先挑最久没被展示过的模板（`last_used_time`），避免短时间重复；
* 本引擎不产生任何网络调用，离线也能开台。
"""

from __future__ import annotations

import random
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

SLOT_NAMES: tuple[str, ...] = ("cat", "building", "resource", "stock")

#: 槽位词池（世界观口径：07 号避难所 / 二代智械拾荒猫 / 欧米伽天网）
SLOT_POOLS: dict[str, tuple[str, ...]] = {
    "cat": (
        "折耳猫",
        "三花娘娘",
        "奶牛猫队长",
        "橘色推土机",
        "黑猫技工",
        "狸花侦察兵",
        "布偶工程师",
        "小奶猫实习生",
    ),
    "building": (
        "瓦楞纸箱窝",
        "水培农田",
        "废品解体操作台",
        "人类古董图灵终端",
        "猫力发电滚轮",
        "地表太阳能集热板",
        "生化精炼工坊",
        "多层猫爬架公寓",
    ),
    "resource": (
        "机械废铁",
        "高能猫薄荷",
        "工业逻辑芯片",
        "航空钛合金",
        "生物润滑脂",
        "高能蓄能电池",
    ),
    "stock": ("FORGE 熔岩重工", "GRID 近地电网", "HELIUM3 外星氦3", "LOGISTICS 欧米伽战略后勤"),
}

SLOT_PATTERN = re.compile(r"\{(" + "|".join(SLOT_NAMES) + r")\}")


def render_template(text: str, rng: random.Random | None = None) -> str:
    """把模板里的槽位替换成随机词条（未识别的槽位原样保留，便于事后排查）。"""
    random_source = rng or random
    return SLOT_PATTERN.sub(
        lambda match: random_source.choice(SLOT_POOLS[match.group(1)]),
        text,
    )


def used_slots(text: str) -> list[str]:
    """提取模板里用到的槽位名（用于"有槽位才算合格语料"的校验）。"""
    return sorted({match.group(1) for match in SLOT_PATTERN.finditer(text)})


def pick_lru(
    rows: Sequence[Mapping[str, Any]],
    count: int,
    *,
    key: str = "template_text",
) -> list[Mapping[str, Any]]:
    """按 LRU 挑 `count` 条模板：没展示过（last_used_time 为空）的排最前，其次按最久未用。

    当可用模板少于 `count` 时按顺序循环补足（同一轮里尽量不重复同一条）。
    """
    if count <= 0 or not rows:
        return []
    ordered = sorted(
        rows,
        key=lambda row: (
            row.get("last_used_time") is not None,
            row.get("last_used_time") or 0,
            str(row.get(key, "")),
        ),
    )
    result: list[Mapping[str, Any]] = []
    pool: Iterable[Mapping[str, Any]] = ordered
    while len(result) < count:
        progressed = False
        for row in pool:
            result.append(row)
            progressed = True
            if len(result) >= count:
                break
        if not progressed:
            break
    return result[:count]
