"""SQLAlchemy ORM 实体映射（16 张表，一一对应《数据库设计定稿》第 4 章 DDL）。

按域分文件承载（模型模块 ↔ 表）：

* `colony.py`   → save_slot / colony_state / labor_buckets / facility_state /
                  planet_state / career_stats / achievements / minigame_state
* `tech.py`     → tech_records
* `military.py` → military_state / vehicle_units
* `boss.py`     → boss_state
* `darknet.py`  → darknet_state / forum_posts
* `garden.py`   → garden_state
* `template.py` → event_templates
"""

from app.models.boss import BossState
from app.models.colony import (
    Achievement,
    CareerStats,
    ColonyState,
    FacilityState,
    LaborBucket,
    MinigameState,
    PlanetState,
    SaveSlot,
)
from app.models.darknet import DarknetState, ForumPost
from app.models.garden import GardenState
from app.models.military import MilitaryState, VehicleUnit
from app.models.template import EventTemplate
from app.models.tech import TechRecord

__all__ = [
    "Achievement",
    "BossState",
    "CareerStats",
    "ColonyState",
    "DarknetState",
    "EventTemplate",
    "FacilityState",
    "ForumPost",
    "GardenState",
    "LaborBucket",
    "MilitaryState",
    "MinigameState",
    "PlanetState",
    "SaveSlot",
    "TechRecord",
    "VehicleUnit",
]

#: 表数量（数据库设计定稿：7 张 → 13 张 → 抽象化后 16 张）
TABLE_COUNT = 16
