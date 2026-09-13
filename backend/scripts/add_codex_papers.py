"""一次性迁移：给已有库的 `garden_state` 补上 `codex_papers` JSON 列（模块 H2）。

用法（在 backend/ 下）：`python scripts/add_codex_papers.py`
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymysql

from app.core.config import get_settings  # noqa: E402


def main() -> None:
    settings = get_settings()
    conn = pymysql.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        database=settings.db_name,
        charset="utf8mb4",
    )
    cursor = conn.cursor()
    cursor.execute("SHOW COLUMNS FROM garden_state LIKE 'codex_papers'")
    if cursor.fetchall():
        print("codex_papers 列已存在，跳过")
    else:
        cursor.execute("ALTER TABLE garden_state ADD COLUMN codex_papers JSON NULL")
        cursor.execute("UPDATE garden_state SET codex_papers = JSON_OBJECT() WHERE codex_papers IS NULL")
        cursor.execute("ALTER TABLE garden_state MODIFY COLUMN codex_papers JSON NOT NULL")
        conn.commit()
        print("codex_papers 列已添加并回填")
    cursor.execute("SELECT slot_id, planet_id, codex_papers FROM garden_state")
    for row in cursor.fetchall():
        print(row)
    conn.close()


if __name__ == "__main__":
    main()
