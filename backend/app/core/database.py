"""SQLAlchemy 2.0 异步引擎、会话工厂与声明式基类。"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    """全部 ORM 模型的声明式基类。"""


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def create_engine_for_url(url: str, *, echo: bool | None = None) -> AsyncEngine:
    """按 URL 构建异步引擎。

    MySQL(aiomysql) 使用连接池并开启 pool_pre_ping；SQLite(aiosqlite，测试用) 关闭
    连接池复用带来的跨事件循环问题。
    """
    settings = get_settings()
    if url.startswith("sqlite"):
        return create_async_engine(url, echo=echo if echo is not None else settings.db_echo, future=True)
    return create_async_engine(
        url,
        echo=echo if echo is not None else settings.db_echo,
        pool_pre_ping=True,
        pool_recycle=1800,
        future=True,
    )


def get_engine() -> AsyncEngine:
    """进程内单例引擎（懒加载）。"""
    global _engine
    if _engine is None:
        _engine = create_engine_for_url(get_settings().sqlalchemy_url)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _sessionmaker


async def dispose_engine() -> None:
    """优雅关闭：应用退出或测试切换数据库时调用。"""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


async def configure_database(url: str | None = None) -> AsyncEngine:
    """切换数据库（测试与脚本用）：重建引擎与会话工厂。"""
    global _engine, _sessionmaker
    await dispose_engine()
    _engine = create_engine_for_url(url or get_settings().sqlalchemy_url)
    _sessionmaker = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    return _engine


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：每请求一个会话，异常自动回滚。"""
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def create_all(*, drop_first: bool = False) -> list[str]:
    """建表（脚本与测试共用）。返回建好的表名列表。"""
    # 触发全部模型注册
    import app.models  # noqa: F401  pylint: disable=unused-import

    engine = get_engine()
    async with engine.begin() as conn:
        if drop_first:
            await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    return sorted(Base.metadata.tables.keys())


def table_names() -> list[str]:
    import app.models  # noqa: F401  pylint: disable=unused-import

    return sorted(Base.metadata.tables.keys())
