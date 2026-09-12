"""真机 MySQL 验收（可选）：设置 DWM_TEST_MYSQL_URL 后运行。

    $env:DWM_TEST_MYSQL_URL = "mysql+aiomysql://root:root@127.0.0.1:3306/dawn_meow?charset=utf8mb4"
    python -m pytest -p no:cacheprovider tests/test_mysql_integration.py

注意：会 DROP 该库下本项目的 16 张表后重建（必须指向专用库 dawn_meow）。
"""

from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core import balance as B
from app.core import database

MYSQL_URL = os.environ.get("DWM_TEST_MYSQL_URL")

pytestmark = pytest.mark.skipif(
    not MYSQL_URL, reason="未设置 DWM_TEST_MYSQL_URL，跳过 MySQL 真机验收"
)


async def test_mysql_schema_and_roundtrip():
    await database.configure_database(MYSQL_URL)
    tables = await database.create_all(drop_first=True)
    assert len(tables) == 16

    engine = database.get_engine()
    async with engine.connect() as conn:
        version = (await conn.execute(text("SELECT VERSION()"))).scalar_one()
        assert version.startswith("8.")
        # 出定稿 DDL 的类型：JSON / ENUM / DOUBLE / TINYINT 必须是真的
        column_types = {
            row[0]: row[1]
            for row in (
                await conn.execute(
                    text(
                        "SELECT COLUMN_NAME, DATA_TYPE FROM information_schema.COLUMNS "
                        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'colony_state'"
                    )
                )
            ).all()
        }
        assert column_types["catnip"] == "double"
        assert column_types["slot_id"] == "tinyint"
        assert column_types["labor_automation_policy"] == "json"
        enum_types = {
            row[0]: row[1]
            for row in (
                await conn.execute(
                    text(
                        "SELECT COLUMN_NAME, DATA_TYPE FROM information_schema.COLUMNS "
                        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tech_records'"
                    )
                )
            ).all()
        }
        assert enum_types["status"] == "enum"

    from main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://dawn.test") as client:
        state = (await client.get("/api/v1/colony/state?slot=1")).json()["data"]
        assert state["slot_id"] == 1
        assert set(state["facilities"]) == set(B.FACILITY_IDS)
        snapshot = await client.post(
            "/api/v1/colony/snapshot", json={"slot": 1, "resources": {"catnip": 0.0}}
        )
        assert snapshot.json()["message"] == "SNAPSHOT_PERSISTED"

    await database.dispose_engine()
