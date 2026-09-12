"""12. 敌对天网与 BOSS 状态表：单存档一行。"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.types import double, json_column, tinyint


class BossState(Base):
    __tablename__ = "boss_state"
    __table_args__ = {"comment": "欧米伽天网演化状态表"}

    slot_id: Mapped[int] = mapped_column(tinyint(), primary_key=True)
    last_tick_time: Mapped[int] = mapped_column(
        sa.BigInteger, nullable=False, server_default=sa.text("0"), default=0, comment="后台演化离线补算锚点"
    )
    threat_level: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("1"), default=1, comment="欧米伽威胁等级 1~10"
    )
    expansion_rate: Mapped[float] = mapped_column(
        double(), nullable=False, server_default=sa.text("1.0"), default=1.0, comment="无人工厂扩张速率"
    )
    fleet_strength: Mapped[float] = mapped_column(
        double(), nullable=False, server_default=sa.text("500"), default=500.0, comment="核心舰队总战力"
    )
    suspicion_toward_player: Mapped[float] = mapped_column(
        double(), nullable=False, server_default=sa.text("0"), default=0.0, comment="对玩家的锁定戒备度"
    )
    rage: Mapped[float] = mapped_column(
        double(), nullable=False, server_default=sa.text("0"), default=0.0, comment="通缉热度 0~100"
    )
    convoy_ends_at: Mapped[int | None] = mapped_column(
        sa.BigInteger, nullable=True, comment="下一班矿石车队出发/到达时间戳"
    )
    factory_frozen_until: Mapped[int | None] = mapped_column(
        sa.BigInteger, nullable=True, comment="兵工厂断料停工到期时间戳"
    )
    intel_level: Mapped[float] = mapped_column(
        double(), nullable=False, server_default=sa.text("0"), default=0.0, comment="侦察情报破译度 0~1"
    )
    raid_ends_at: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True)
    raid_target_planet: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    satellite_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0"), default=0, comment="在轨侦察卫星存活数"
    )
    bombardment_state: Mapped[dict | None] = mapped_column(
        json_column(), nullable=True, comment="大轰炸进行态与灾后重建状态"
    )
