"""15. 动态语料模板库：补 LRU 冷却。"""

from __future__ import annotations

import enum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.types import bigint_pk, ddl_enum, double, timestamp, tinyint


class TemplateCategory(str, enum.Enum):
    RADIO_NEWS = "RADIO_NEWS"
    BBS_POST = "BBS_POST"
    DISASTER_ALERT = "DISASTER_ALERT"


class EventTemplate(Base):
    __tablename__ = "event_templates"
    __table_args__ = (
        sa.Index("idx_lru", "slot_id", "phase_id", "category", "last_used_time"),
        {"comment": "动态事件语料模板库"},
    )

    id: Mapped[int] = mapped_column(bigint_pk(), primary_key=True, autoincrement=True)
    slot_id: Mapped[int] = mapped_column(tinyint(), nullable=False)
    phase_id: Mapped[str] = mapped_column(
        sa.String(32), nullable=False, comment="所属时代阶段 PHASE_1_SURFACE / PHASE_2_ORBIT ..."
    )
    category: Mapped[TemplateCategory] = mapped_column(
        ddl_enum(TemplateCategory, "template_category"), nullable=False
    )
    template_text: Mapped[str] = mapped_column(
        sa.Text, nullable=False, comment="含槽位 {cat} {building} {resource} {stock}"
    )
    impact_stock: Mapped[str | None] = mapped_column(sa.String(32), nullable=True, comment="联动股票代码(可空)")
    impact_val: Mapped[float] = mapped_column(
        double(), nullable=False, server_default=sa.text("0"), default=0.0, comment="联动大盘动量冲击值"
    )
    last_used_time: Mapped[int | None] = mapped_column(
        sa.BigInteger, nullable=True, comment="上次被展示时间戳(LRU 防重复)"
    )
    use_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0"), default=0, comment="被展示次数"
    )
    created_at: Mapped[sa.DateTime] = mapped_column(
        timestamp(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
    )
