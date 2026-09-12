"""8~9. 智械匿名深网状态表与论坛帖子表。"""

from __future__ import annotations

import enum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.types import bigint_pk, ddl_enum, double, json_column, tinyint


class ForumSentiment(str, enum.Enum):
    BULL = "BULL"
    BEAR = "BEAR"
    NEUTRAL = "NEUTRAL"


class ReactionPattern(str, enum.Enum):
    FRONT_RUN = "FRONT_RUN"
    SLOW_BURN = "SLOW_BURN"
    BEAR_TRAP = "BEAR_TRAP"
    FATIGUE = "FATIGUE"


class DarknetState(Base):
    """8. 智械匿名深网表：单存档一行。"""

    __tablename__ = "darknet_state"
    __table_args__ = {"comment": "智械深网金融与论坛状态表"}

    slot_id: Mapped[int] = mapped_column(tinyint(), primary_key=True)
    last_tick_time: Mapped[int] = mapped_column(
        sa.BigInteger, nullable=False, server_default=sa.text("0"), default=0, comment="大盘离线补算锚点"
    )
    byte_credits: Mapped[float] = mapped_column(
        double(), nullable=False, server_default=sa.text("0"), default=0.0, comment="算力币余额"
    )
    exposure: Mapped[float] = mapped_column(
        double(), nullable=False, server_default=sa.text("0"), default=0.0, comment="节点异常暴露度 0~100"
    )
    burner_id: Mapped[str] = mapped_column(
        sa.String(64), nullable=False, server_default=sa.text("'robot_4a932cz'"), default="robot_4a932cz",
        comment="匿名代号(可自定义)",
    )
    has_4s_data: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("0"), default=False, comment="是否已购买 4S 深度数据眼"
    )
    stocks_data: Mapped[list] = mapped_column(
        json_column(), nullable=False, default=list,
        comment="大盘快照 [{stock_id,price,forecast,target_forecast,momentum,volatility}]",
    )
    kline_history: Mapped[list] = mapped_column(json_column(), nullable=False, default=list, comment="近 N 根 K 线")
    positions: Mapped[list] = mapped_column(json_column(), nullable=False, default=list, comment="现货多头持仓")
    short_contracts: Mapped[list] = mapped_column(
        json_column(), nullable=False, default=list, comment="做空合约(含绝对到期时间戳 ends_at)"
    )
    limit_orders: Mapped[list] = mapped_column(json_column(), nullable=False, default=list, comment="量化条件单配置")
    black_market_items: Mapped[list] = mapped_column(
        json_column(), nullable=False, default=list, comment="黑市货架 [{item_id,base_price,current_price,tag}]"
    )
    pending_deliveries: Mapped[list] = mapped_column(
        json_column(), nullable=False, default=list, comment="在途空投 [{item_id,qty,ends_at}]"
    )
    whale_events: Mapped[list] = mapped_column(
        json_column(), nullable=False, default=list, comment="盘口匿名大单异动（看破迷雾线索二）"
    )
    last_post_time: Mapped[int | None] = mapped_column(
        sa.BigInteger, nullable=True, comment="上次论坛发帖时间戳(10 分钟冷却判定)"
    )


class ForumPost(Base):
    """9. 论坛帖子表：玩家发帖 + 机器人跟帖。"""

    __tablename__ = "forum_posts"
    __table_args__ = (
        sa.Index("idx_slot_time", "slot_id", "created_at"),
        sa.Index("idx_slot_stock", "slot_id", "target_stock"),
        {"comment": "深网匿名论坛帖子表"},
    )

    id: Mapped[int] = mapped_column(bigint_pk(), primary_key=True, autoincrement=True)
    slot_id: Mapped[int] = mapped_column(tinyint(), nullable=False)
    author_id: Mapped[str] = mapped_column(
        sa.String(64), nullable=False, comment="发帖代号(机器散户哈希 / 玩家 burner_id)"
    )
    is_player: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("0"), default=False, comment="是否玩家自己发的"
    )
    telemetry_code: Mapped[str | None] = mapped_column(
        sa.String(64), nullable=True, comment="遥测报错码附件(线索一，可空)"
    )
    author_post_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0"), default=0, comment="该代号历史发帖数(线索三)"
    )
    author_hit_rate: Mapped[float] = mapped_column(
        double(), nullable=False, server_default=sa.text("0"), default=0.0, comment="该代号历史爆料命中率 0~1"
    )
    content: Mapped[str] = mapped_column(sa.Text, nullable=False, comment="正文")
    created_at: Mapped[int] = mapped_column(sa.BigInteger, nullable=False, comment="发帖时间戳")
    target_stock: Mapped[str | None] = mapped_column(sa.String(32), nullable=True, comment="LLM 判定关联股票")
    sentiment: Mapped[ForumSentiment | None] = mapped_column(
        ddl_enum(ForumSentiment, "forum_sentiment"), nullable=True
    )
    persuasiveness: Mapped[int | None] = mapped_column(sa.SmallInteger, nullable=True, comment="煽动星级 1~5")
    suspicion_risk: Mapped[int | None] = mapped_column(sa.SmallInteger, nullable=True, comment="判定增加的暴露度")
    momentum_impact: Mapped[float] = mapped_column(
        double(), nullable=False, server_default=sa.text("0"), default=0.0, comment="注入的行情动量"
    )
    reaction_pattern: Mapped[ReactionPattern] = mapped_column(
        ddl_enum(ReactionPattern, "reaction_pattern"),
        nullable=False,
        server_default=sa.text("'SLOW_BURN'"),
        default=ReactionPattern.SLOW_BURN,
        comment="事件到行情的滞后模式",
    )
    is_fake: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("0"), default=False, comment="后台真假标记(玩家不可见)"
    )
    reply_to_id: Mapped[int | None] = mapped_column(sa.BigInteger, nullable=True, comment="跟贴所回复的主帖 ID")
