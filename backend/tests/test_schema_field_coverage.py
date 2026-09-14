"""自动防守"Pydantic 静默过滤"（该类已踩 5 次：`launch_silo` / `smelted_batches` / `gained_alloys` /
`research` / `gained_unity`）。

原理：引擎与裁剪函数产出的字段，若没在响应 Schema 里声明，Pydantic 会**安静地丢掉**——
不报错、日志里有值、接口里查无此字段。这里用"**产出的键集合必须被 Schema 覆盖**"来兜住。
"""

from __future__ import annotations

from app.core.offline_engine import build_report_summary, calculate_offline_progress  # noqa: F401
from app.schemas.colony import ColonyStateData, OfflineReport


def _rich_state() -> dict:
    """一份"什么功能都开着"的引擎输入，尽量让报告里的分支都被走到。"""
    return {
        "now": 1000,
        "catnip": 100.0, "catnip_max": 500.0, "scrap": 100.0, "scrap_max": 500.0,
        "chips": 50.0, "chips_max": 500.0, "alloys": 10.0, "alloys_max": 500.0,
        "battery": 5.0, "battery_max": 100.0, "lube": 5.0, "lube_max": 100.0,
        "battery_kwh": 0.0, "battery_kwh_max": 200.0,
        "total_cats": 4, "birth_progress": 0.0, "suspicion": 10.0,
        "farmers": 2, "scavengers": 2, "geeks": 1, "power_runners": 2, "crew": 0,
        "max_cat_capacity": 10, "production_multiplier": 1.0,
        "breeding_rate_multiplier": 1.0, "suspicion_growth_multiplier": 1.0,
        "silent_grass_count": 0, "garden_power_kw": 5.0, "garden_suspicion_per_sec": -0.002,
        "facilities": {"turing_terminal": 1, "solar_panel": 2, "induction_furnace": 1},
        "induction_furnaces": 1,
        "planet_scrap_multiplier": 1.25, "planet_chips_multiplier": 1.0,
        "planet_catnip_multiplier": 1.0, "catnip_efficiency": 0.2,
    }


def test_report_summary_keys_are_all_declared() -> None:
    """裁剪函数产出的每个键，都必须在 `OfflineReport` 里声明（否则会被静默丢弃）。"""
    report = calculate_offline_progress(_rich_state(), 600)
    report["research"] = {"tech_id": "t", "tech_name": "测试节点", "unlocked": True, "progress": 10, "cost": 10}
    report["gained_unity"] = 12.0
    summary = build_report_summary(report)

    declared = set(OfflineReport.model_fields)
    missing = set(summary) - declared
    assert not missing, f"这些字段会被 Pydantic 静默丢弃，请加进 OfflineReport：{sorted(missing)}"


def test_state_payload_block_names_are_declared() -> None:
    """状态报文的顶层块名同理（`ColonyStateData` 的字段就是契约）。"""
    declared = set(ColonyStateData.model_fields)
    # 这几个块是历史踩坑点：launch_silo 曾整块消失、tech_effects 后加
    for block in ("resources", "power", "population", "workstations", "facilities", "suspicion",
                  "security", "launch_silo", "tech_effects", "offline_report"):
        assert block in declared, f"{block} 没在 ColonyStateData 里声明"


def test_offline_report_declares_optional_extras() -> None:
    """新增的可观测字段要显式声明默认值，避免 None 类型漂移。"""
    fields = OfflineReport.model_fields
    for name in ("smelted_batches", "gained_alloys", "research", "gained_unity"):
        assert name in fields, f"{name} 缺失"
    assert fields["gained_unity"].default == 0.0
    assert fields["research"].default is None
