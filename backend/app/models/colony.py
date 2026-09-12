"""存档与基地核心域模型（8 张表）。

对应 DDL：save_slot / colony_state / labor_buckets / facility_state /
planet_state / career_stats / achievements / minigame_state。
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.balance import RESOURCE_CAPS, SAVE_VERSION
from app.core.database import Base
from app.models.types import NowOnUpdate, double, json_column, tinyint, timestamp


class SaveSlot(Base):
    """1. 存档槽位表：多存档的根节点。"""

    __tablename__ = "save_slot"
    __table_args__ = {"comment": "存档槽位表"}

    slot_id: Mapped[int] = mapped_column(tinyint(), primary_key=True, comment="存档槽位 1~3")
    slot_name: Mapped[str | None] = mapped_column(sa.String(32), nullable=True, comment="玩家自定义槽位名")
    save_version: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text(str(SAVE_VERSION)),
        default=SAVE_VERSION, comment="存档格式版本，导入旧档时用于迁移",
    )
    checksum: Mapped[str | None] = mapped_column(sa.String(64), nullable=True, comment="导入导出的完整性校验和")
    active_planet_id: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0"), default=0, comment="当前操作的星球 ID"
    )
    playtime_seconds: Mapped[int] = mapped_column(
        sa.BigInteger, nullable=False, server_default=sa.text("0"), default=0, comment="累计游玩时长(秒)"
    )
    unity: Mapped[float] = mapped_column(
        double(), nullable=False, server_default=sa.text("0"), default=0.0, comment="文明凝聚力(全局点数)"
    )
    doctrines: Mapped[dict] = mapped_column(
        json_column(), nullable=False, default=dict, comment='已点亮政令 {"doctrine_id": level}'
    )
    created_at: Mapped[sa.DateTime] = mapped_column(
        timestamp(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[sa.DateTime] = mapped_column(
        timestamp(),
        nullable=False,
        server_default=NowOnUpdate(),
    )


class ColonyState(Base):
    """2. 基地核心状态表：单行星一行，纯数字池化。"""

    __tablename__ = "colony_state"
    __table_args__ = {"comment": "基地核心状态表"}

    slot_id: Mapped[int] = mapped_column(tinyint(), primary_key=True)
    planet_id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    last_tick_time: Mapped[int] = mapped_column(
        sa.BigInteger, nullable=False, server_default=sa.text("0"), default=0,
        comment="上次结算时间戳(秒)，离线补算锚点",
    )

    catnip: Mapped[float] = mapped_column(double(), nullable=False, server_default=sa.text("0"), default=0.0)
    catnip_max: Mapped[float] = mapped_column(
        double(), nullable=False, server_default=sa.text(str(RESOURCE_CAPS["catnip"])), default=RESOURCE_CAPS["catnip"]
    )
    scrap: Mapped[float] = mapped_column(double(), nullable=False, server_default=sa.text("0"), default=0.0)
    scrap_max: Mapped[float] = mapped_column(
        double(), nullable=False, server_default=sa.text(str(RESOURCE_CAPS["scrap"])), default=RESOURCE_CAPS["scrap"]
    )
    chips: Mapped[float] = mapped_column(double(), nullable=False, server_default=sa.text("0"), default=0.0)
    chips_max: Mapped[float] = mapped_column(
        double(), nullable=False, server_default=sa.text(str(RESOURCE_CAPS["chips"])), default=RESOURCE_CAPS["chips"]
    )
    alloys: Mapped[float] = mapped_column(double(), nullable=False, server_default=sa.text("0"), default=0.0)
    alloys_max: Mapped[float] = mapped_column(
        double(), nullable=False, server_default=sa.text(str(RESOURCE_CAPS["alloys"])), default=RESOURCE_CAPS["alloys"]
    )
    battery: Mapped[float] = mapped_column(double(), nullable=False, server_default=sa.text("0"), default=0.0)
    battery_max: Mapped[float] = mapped_column(
        double(), nullable=False, server_default=sa.text(str(RESOURCE_CAPS["battery"])), default=RESOURCE_CAPS["battery"]
    )
    lube: Mapped[float] = mapped_column(double(), nullable=False, server_default=sa.text("0"), default=0.0)
    lube_max: Mapped[float] = mapped_column(
        double(), nullable=False, server_default=sa.text(str(RESOURCE_CAPS["lube"])), default=RESOURCE_CAPS["lube"]
    )
    manual_scavenge_clicks: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0"), default=0,
        comment="冷启动手点废墟次数（上限见数值平衡表 §3.4）",
    )

    power_net: Mapped[float] = mapped_column(
        double(), nullable=False, server_default=sa.text("0"), default=0.0, comment="净电力平衡(kW)，流量指标"
    )
    battery_kwh: Mapped[float] = mapped_column(
        double(), nullable=False, server_default=sa.text("0"), default=0.0, comment="蓄电池电容池存量(kWh)"
    )
    battery_kwh_max: Mapped[float] = mapped_column(
        double(), nullable=False, server_default=sa.text("200"), default=200.0, comment="电容池上限(kWh)"
    )

    total_cats: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0"), default=0, comment="基地总猫口"
    )
    birth_progress: Mapped[float] = mapped_column(
        double(), nullable=False, server_default=sa.text("0"), default=0.0, comment="繁育累计进度 0~1"
    )
    job_idle: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0"), default=0, comment="空闲待命猫口"
    )

    suspicion: Mapped[float] = mapped_column(
        double(), nullable=False, server_default=sa.text("0"), default=0.0, comment="天网警戒度 0~100"
    )

    labor_automation_policy: Mapped[dict | None] = mapped_column(
        json_column(), nullable=True, comment='迟滞换班策略 {"enabled":true,"upper":0.8,"lower":0.2,"shift":2}'
    )
    smelt_automation_policy: Mapped[dict | None] = mapped_column(
        json_column(), nullable=True, comment="材料溢出自动熔炼策略"
    )


class LaborBucket(Base):
    """3. 通用工种分桶表：母星 5 工种 / 星际 4 高维职业 / 未来新职业共用。"""

    __tablename__ = "labor_buckets"
    __table_args__ = {"comment": "通用工种分桶表"}

    slot_id: Mapped[int] = mapped_column(tinyint(), primary_key=True)
    planet_id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(
        sa.String(32), primary_key=True,
        comment="farmer/scavenger/geek/power_runner/crew/fleet_commander/logistics/terraformer/purr_master",
    )
    cat_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0"), default=0, comment="该工种猫口数"
    )


class FacilityState(Base):
    """4. 通用设施状态表：取代 colony_state 中硬编码的设施列。"""

    __tablename__ = "facility_state"
    __table_args__ = {"comment": "通用设施状态表"}

    slot_id: Mapped[int] = mapped_column(tinyint(), primary_key=True)
    planet_id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    facility_id: Mapped[str] = mapped_column(
        sa.String(32), primary_key=True,
        comment="housing_box/farm_plot/scavenge_station/turing_terminal/acoustic_layer/power_wheel/"
                "solar_panel/refinery/battery_bank/cat_condo/launch_silo",
    )
    level: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0"), default=0, comment="等级或数量，0 = 未建造"
    )


class PlanetState(Base):
    """5. 星球与星图表：解锁进度与跨星物流。"""

    __tablename__ = "planet_state"
    __table_args__ = {"comment": "星球解锁与星际物流表"}

    slot_id: Mapped[int] = mapped_column(tinyint(), primary_key=True)
    planet_id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, comment="0母星 1熔岩星 2冰卫星 3小行星带")
    unlocked: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("0"), default=False
    )
    unlocked_at: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True, comment="解锁时间戳")
    biome_tag: Mapped[str | None] = mapped_column(sa.String(64), nullable=True, comment="生态标签")
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("0"), default=False, comment="是否当前操作星球"
    )
    logistics_routes: Mapped[list] = mapped_column(
        json_column(), nullable=False, default=list, comment="在途空投/货运航线数组"
    )


class CareerStats(Base):
    """13. 生涯统计表：增量累加。"""

    __tablename__ = "career_stats"
    __table_args__ = {"comment": "生涯统计表"}

    slot_id: Mapped[int] = mapped_column(tinyint(), primary_key=True)
    playtime_seconds: Mapped[int] = mapped_column(
        sa.BigInteger, nullable=False, server_default=sa.text("0"), default=0
    )
    total_catnip: Mapped[float] = mapped_column(double(), nullable=False, server_default=sa.text("0"), default=0.0)
    total_scrap: Mapped[float] = mapped_column(double(), nullable=False, server_default=sa.text("0"), default=0.0)
    total_chips: Mapped[float] = mapped_column(double(), nullable=False, server_default=sa.text("0"), default=0.0)
    total_alloys: Mapped[float] = mapped_column(double(), nullable=False, server_default=sa.text("0"), default=0.0)
    total_battery: Mapped[float] = mapped_column(double(), nullable=False, server_default=sa.text("0"), default=0.0)
    total_kwh: Mapped[float] = mapped_column(double(), nullable=False, server_default=sa.text("0"), default=0.0)
    total_cats_born: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0"), default=0
    )
    best_short_profit: Mapped[float] = mapped_column(
        double(), nullable=False, server_default=sa.text("0"), default=0.0
    )
    smuggling_volume: Mapped[float] = mapped_column(
        double(), nullable=False, server_default=sa.text("0"), default=0.0
    )
    expeditions_completed: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0"), default=0
    )
    bombardment_survived: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0"), default=0
    )
    fastest_rebuild_seconds: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    updated_at: Mapped[sa.DateTime] = mapped_column(
        timestamp(),
        nullable=False,
        server_default=NowOnUpdate(),
    )


class Achievement(Base):
    """14. 成就徽章表。"""

    __tablename__ = "achievements"
    __table_args__ = {"comment": "成就徽章表"}

    slot_id: Mapped[int] = mapped_column(tinyint(), primary_key=True)
    achievement_id: Mapped[str] = mapped_column(sa.String(32), primary_key=True, comment="徽章 ID")
    progress: Mapped[float] = mapped_column(
        double(), nullable=False, server_default=sa.text("0"), default=0.0, comment="当前进度"
    )
    unlocked_at: Mapped[int | None] = mapped_column(
        sa.BigInteger, nullable=True, comment="解锁时间戳，NULL 表示未解锁"
    )


class MinigameState(Base):
    """16. 通用小游戏状态表：密电译码 / 矿脉扫描 / 熔炉配比共用。"""

    __tablename__ = "minigame_state"
    __table_args__ = {"comment": "通用小游戏状态表"}

    slot_id: Mapped[int] = mapped_column(tinyint(), primary_key=True)
    planet_id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    minigame_id: Mapped[str] = mapped_column(sa.String(32), primary_key=True)
    state: Mapped[dict] = mapped_column(json_column(), nullable=False, default=dict, comment="玩法私有状态")
    last_tick_time: Mapped[int] = mapped_column(
        sa.BigInteger, nullable=False, server_default=sa.text("0"), default=0, comment="离线推进锚点"
    )
    best_score: Mapped[float] = mapped_column(
        double(), nullable=False, server_default=sa.text("0"), default=0.0, comment="历史最好成绩"
    )
    play_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0"), default=0, comment="累计游玩次数"
    )
