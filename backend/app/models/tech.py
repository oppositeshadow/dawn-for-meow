"""6. 科技研发记录表：DAG 节点状态。"""

from __future__ import annotations

import enum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.types import ddl_enum, double, json_column, tinyint


class TechStatus(str, enum.Enum):
    LOCKED = "LOCKED"
    RESEARCHING = "RESEARCHING"
    UNLOCKED = "UNLOCKED"


class TechRecord(Base):
    __tablename__ = "tech_records"
    __table_args__ = {"comment": "科技树状态记录表"}

    slot_id: Mapped[int] = mapped_column(tinyint(), primary_key=True)
    planet_id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    tech_id: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    tech_name: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    parent_ids: Mapped[list] = mapped_column(
        json_column(), nullable=False, default=list, comment="DAG 前置科技 ID 数组(防火墙2 校验用)"
    )
    tier: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("1"), default=1, comment="所属阶梯 1~4"
    )
    node_order: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0"), default=0, comment="同阶梯内排序"
    )
    status: Mapped[TechStatus] = mapped_column(
        ddl_enum(TechStatus, "tech_status"),
        nullable=False,
        server_default=sa.text("'LOCKED'"),
        default=TechStatus.LOCKED,
    )
    current_progress: Mapped[float] = mapped_column(
        double(), nullable=False, server_default=sa.text("0"), default=0.0, comment="已累积算力"
    )
    target_cost: Mapped[float] = mapped_column(
        double(), nullable=False, server_default=sa.text("0"), default=0.0, comment="解锁所需算力"
    )
    is_agent_generated: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("0"), default=False, comment="是否 LLM 动态推演生成"
    )
    flavor_text: Mapped[str | None] = mapped_column(sa.String(255), nullable=True, comment="风味文案")
    mechanic_type: Mapped[str | None] = mapped_column(
        sa.String(32), nullable=True, comment="PASSIVE_BUFF / CONVERSION / TRADE_OFF / UNLOCK_ABILITY"
    )
    buff_payload: Mapped[dict | None] = mapped_column(json_column(), nullable=True, comment="机制或数值加成载荷")
