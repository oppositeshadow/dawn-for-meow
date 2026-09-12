"""建表脚本 + 可选的新游戏初始化（Milestone 1 阶段 1）。

用法（在 backend/ 目录下）::

    python scripts/init_db.py                    # 建库（MySQL 缺失时自动 CREATE DATABASE）+ 建 16 张表
    python scripts/init_db.py --drop             # 先删表再建（会清空该库的数据，谨慎）
    python scripts/init_db.py --new-game 1       # 建表后为槽位 1 初始化一份全新存档
    python scripts/init_db.py --url sqlite+aiosqlite:///./dev.db   # 退回 SQLite 自测
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import balance as B  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.database import configure_database, create_all, get_sessionmaker, table_names  # noqa: E402
from app.core.database import dispose_engine  # noqa: E402
from app.core.seed_loader import facility_defs, minigame_defs, tech_defs_planet0  # noqa: E402
from app.models import SaveSlot  # noqa: E402
from app.services.game_init_service import create_new_game  # noqa: E402


def ensure_mysql_database() -> str | None:
    """MySQL：库不存在时自动建库（utf8mb4）。返回提示信息，非 MySQL 时返回 None。"""
    settings = get_settings()
    if settings.database_url and not settings.database_url.startswith("mysql"):
        return None
    if settings.database_url:
        return None

    import pymysql  # 本地已有；仅建库这一步用同步驱动

    connection = pymysql.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        charset="utf8mb4",
        autocommit=True,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{settings.db_name}` "
                "DEFAULT CHARSET utf8mb4 COLLATE utf8mb4_general_ci"
            )
        return f"数据库 `{settings.db_name}` 已就绪（{settings.db_host}:{settings.db_port}）"
    finally:
        connection.close()


async def run(args: argparse.Namespace) -> int:
    tip = ensure_mysql_database()
    if tip:
        print(tip)

    url = args.url or get_settings().sqlalchemy_url
    await configure_database(url)
    tables = await create_all(drop_first=args.drop)
    print(f"建表完成：{len(tables)} 张表")
    for index, name in enumerate(tables, start=1):
        print(f"  {index:2d}. {name}")
    expected = sorted(
        [
            "achievements",
            "boss_state",
            "career_stats",
            "colony_state",
            "darknet_state",
            "event_templates",
            "facility_state",
            "forum_posts",
            "garden_state",
            "labor_buckets",
            "military_state",
            "minigame_state",
            "planet_state",
            "save_slot",
            "tech_records",
            "vehicle_units",
        ]
    )
    if tables != expected:
        print(f"[警告] 表清单与《数据库设计定稿》不一致：{set(expected) ^ set(tables)}")
        return 1

    if args.new_game:
        factory = get_sessionmaker()
        async with factory() as session:
            if await session.get(SaveSlot, args.new_game) is not None:
                print(f"槽位 {args.new_game} 已有存档，跳过初始化（如需重开请先 --drop）")
            else:
                await create_new_game(session, args.new_game)
                await session.commit()
                print(
                    f"新游戏初始化完成：槽位 {args.new_game}｜"
                    f"设施 {len(facility_defs())} 项（等级 0）｜"
                    f"工种 {len(B.PLANET_JOBS)} 项｜"
                    f"科技 {len(tech_defs_planet0())} 节点（LOCKED）｜"
                    f"小游戏 {len(minigame_defs())} 项"
                )
    print(f"当前表：{', '.join(table_names())}")
    await dispose_engine()  # 关掉连接池，避免解释器退出时报 Event loop is closed
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="喵星破晓 建表 / 新游戏初始化脚本")
    parser.add_argument("--url", default=None, help="覆盖数据库 URL（如 sqlite+aiosqlite:///./dev.db）")
    parser.add_argument("--drop", action="store_true", help="先删除已有表再重建（清空数据）")
    parser.add_argument("--new-game", type=int, default=None, metavar="SLOT", help="为指定槽位创建新存档")
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
