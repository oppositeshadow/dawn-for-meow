"""10~11. 军备池化表与载具实例表。"""

from __future__ import annotations

import enum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.types import bigint_pk, ddl_enum, double, json_column, tinyint


class VehicleStatus(str, enum.Enum):
    IDLE = "IDLE"
    EXPEDITION = "EXPEDITION"
    REPAIR = "REPAIR"
    SCRAPPED = "SCRAPPED"


class MilitaryState(Base):
    """10. 军备池化表：不需要个性的部分。"""

    __tablename__ = "military_state"
    __table_args__ = {"comment": "军备池化状态表"}

    slot_id: Mapped[int] = mapped_column(tinyint(), primary_key=True)
    planet_id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    last_tick_time: Mapped[int] = mapped_column(
        sa.BigInteger, nullable=False, server_default=sa.text("0"), default=0
    )
    hangar_capacity: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("12"), default=12, comment="机库车位上界(母星12/星际24)"
    )
    laser_turrets: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0"), default=0, comment="固定激光防空炮台(池化)"
    )
    cruise_missiles: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0"), default=0, comment="已总装巡航导弹(池化)"
    )
    decoy_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0"), default=0, comment="发条机械鼠诱饵库存(池化)"
    )
    security_policy: Mapped[dict] = mapped_column(
        json_column(), nullable=False, default=dict, comment='三级安防预案 {"p1_use_decoy":true,...}'
    )
    hospital_queue: Mapped[list] = mapped_column(
        json_column(), nullable=False, default=list, comment="急救舱 [{unit_id,cats:[...],ends_at}]"
    )
    active_expeditions: Mapped[list] = mapped_column(
        json_column(), nullable=False, default=list, comment="远征 [{expedition_id,target_id,unit_ids:[...],ends_at}]"
    )


class VehicleUnit(Base):
    """11. 载具实例表：每辆车单独计算。"""

    __tablename__ = "vehicle_units"
    __table_args__ = (
        sa.Index("idx_slot_planet_status", "slot_id", "planet_id", "status"),
        {"comment": "载具实例表(每辆车一行)"},
    )

    unit_id: Mapped[int] = mapped_column(bigint_pk(), primary_key=True, autoincrement=True)
    slot_id: Mapped[int] = mapped_column(tinyint(), nullable=False)
    planet_id: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    unit_type: Mapped[str] = mapped_column(
        sa.String(32), nullable=False, comment="车型 ID，定义见 static/vehicle_types.json"
    )
    nickname: Mapped[str | None] = mapped_column(sa.String(32), nullable=True, comment="昵称，如 雷霆号")
    modules: Mapped[list] = mapped_column(
        json_column(), nullable=False, default=list, comment="装配模块 [{slot,module_id}]"
    )
    shield: Mapped[float] = mapped_column(
        double(), nullable=False, server_default=sa.text("0"), default=0.0, comment="当前护盾"
    )
    armor: Mapped[float] = mapped_column(
        double(), nullable=False, server_default=sa.text("0"), default=0.0, comment="当前装甲"
    )
    armor_max: Mapped[float] = mapped_column(
        double(), nullable=False, server_default=sa.text("0"), default=0.0, comment="被破甲削蚀后的装甲上限"
    )
    hull: Mapped[float] = mapped_column(
        double(), nullable=False, server_default=sa.text("0"), default=0.0, comment="结构值(归零则损毁并弹射)"
    )
    status: Mapped[VehicleStatus] = mapped_column(
        ddl_enum(VehicleStatus, "vehicle_status"),
        nullable=False,
        server_default=sa.text("'IDLE'"),
        default=VehicleStatus.IDLE,
    )
    crew_cats: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0"), default=0, comment="乘员猫数量"
    )
    repair_ends_at: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    expedition_id: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    acquired_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, comment="获得时间戳")
