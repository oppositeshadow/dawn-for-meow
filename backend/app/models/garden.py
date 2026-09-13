"""7. 水培基因实验室表。"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.balance import GARDEN_INITIAL_GRID_SIZE, GARDEN_INITIAL_UNLOCKED_CELLS
from app.core.database import Base
from app.models.types import json_column, tinyint


class GardenState(Base):
    __tablename__ = "garden_state"
    __table_args__ = {"comment": "水培基因实验室状态表"}

    slot_id: Mapped[int] = mapped_column(tinyint(), primary_key=True)
    planet_id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    last_tick_time: Mapped[int] = mapped_column(
        sa.BigInteger, nullable=False, server_default=sa.text("0"), default=0, comment="生长/枯萎离线补算锚点"
    )
    grid_size: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text(str(GARDEN_INITIAL_GRID_SIZE)),
        default=GARDEN_INITIAL_GRID_SIZE, comment="已解锁边长 3~7",
    )
    unlocked_cells: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text(str(GARDEN_INITIAL_UNLOCKED_CELLS)),
        default=GARDEN_INITIAL_UNLOCKED_CELLS, comment="已解锁格子数 9~49(每次扩建 +1 格)",
    )
    current_medium: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, server_default=sa.text("'STERILE'"), default="STERILE",
        comment="STERILE / RADIATION / ZERO_G",
    )
    mechanical_arm_enabled: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("0"), default=False, comment="智能机械臂托管"
    )
    auto_protect_unknown: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("1"), default=True, comment="自动保护未知突变幼苗"
    )
    grid_data: Mapped[list] = mapped_column(
        json_column(), nullable=False, default=list, comment="固定 7x7 场地网格数组"
    )
    unlocked_seed_ids: Mapped[list] = mapped_column(
        json_column(), nullable=False, default=list, comment="已解锁母本基因图鉴"
    )
    codex_papers: Mapped[dict] = mapped_column(
        json_column(), nullable=False, default=dict,
        comment="异星植物学图鉴论文 {plant_id: {title, body, source, at}}（LLM 场景 5）",
    )
