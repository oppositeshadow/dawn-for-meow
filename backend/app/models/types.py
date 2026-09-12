"""跨方言列类型助手：MySQL 出定稿 DDL 的类型，SQLite（测试）出等价类型。"""

from __future__ import annotations

import enum as _enum

import sqlalchemy as sa
from sqlalchemy.dialects import mysql
from sqlalchemy.ext.compiler import compiles


def tinyint() -> sa.SmallInteger:
    """TINYINT：MySQL 原生，SQLite 退化为 INTEGER。"""
    return sa.SmallInteger().with_variant(mysql.TINYINT(), "mysql")


def double() -> sa.Float:
    """DOUBLE：MySQL 原生双精度，SQLite 退化为 FLOAT。

    `asdecimal=False` 是必须的：MySQL 方言下 DOUBLE 默认把结果集转成 `Decimal`，
    会让"每秒 +0.15"这类浮点结算与 `float` 累加冲突（实机踩到过）。
    """
    return sa.Float().with_variant(mysql.DOUBLE(asdecimal=False), "mysql")


def bigint_pk() -> sa.BigInteger:
    """自增大整数主键：MySQL 出 BIGINT，SQLite 退化为 INTEGER（否则不自增）。"""
    return sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def ddl_enum(enum_cls: type[_enum.Enum], name: str) -> sa.Enum:
    """MySQL ENUM，SQLite 退化为 VARCHAR + CHECK（值取枚举 value，与 DDL 一致）。"""
    return sa.Enum(
        enum_cls,
        name=name,
        values_callable=lambda cls: [member.value for member in cls],
        native_enum=True,
        validate_strings=False,
    )


def json_column() -> sa.JSON:
    """JSON：MySQL 原生 JSON，SQLite 退化为 TEXT 存储。"""
    return sa.JSON()


def timestamp() -> sa.DateTime:
    """DATETIME，服务端默认 CURRENT_TIMESTAMP。"""
    return sa.DateTime().with_variant(mysql.DATETIME(), "mysql")


class NowOnUpdate(sa.sql.elements.ClauseElement):
    """DATETIME 列默认值，对齐定稿 DDL 的 `ON UPDATE CURRENT_TIMESTAMP`。

    * MySQL：渲染 `CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP`；
    * SQLite（测试）与其他方言：退化为 `CURRENT_TIMESTAMP`。
    """

    inherit_cache = True


@compiles(NowOnUpdate, "mysql")
def _compile_now_on_update_mysql(element: NowOnUpdate, compiler, **kw) -> str:  # pragma: no cover
    return "CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"


@compiles(NowOnUpdate)
def _compile_now_on_update_default(element: NowOnUpdate, compiler, **kw) -> str:  # pragma: no cover
    return "CURRENT_TIMESTAMP"
