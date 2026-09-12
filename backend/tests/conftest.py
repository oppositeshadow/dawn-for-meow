"""测试基座：默认用 SQLite（aiosqlite）跑全量用例；MySQL 实测见 test_mysql_integration.py。"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import database


@pytest_asyncio.fixture
async def sqlite_url(tmp_path) -> str:
    return f"sqlite+aiosqlite:///{(tmp_path / 'dawn_meow_test.db').as_posix()}"


@pytest_asyncio.fixture
async def tables(sqlite_url: str) -> AsyncIterator[list[str]]:
    await database.configure_database(sqlite_url)
    names = await database.create_all(drop_first=True)
    yield names
    await database.dispose_engine()


@pytest_asyncio.fixture
async def session(tables: list[str]) -> AsyncIterator[AsyncSession]:
    factory = database.get_sessionmaker()
    async with factory() as db_session:
        yield db_session


@pytest_asyncio.fixture
async def client(tables: list[str]) -> AsyncIterator[AsyncClient]:
    from main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://dawn.test") as http_client:
        yield http_client


def pytest_configure(config: pytest.Config) -> None:
    # 测试期禁用 LLM 与外部网络（模块 J 尚未落地，这里先立规矩）
    os.environ.setdefault("LLM_API_KEY", "")
