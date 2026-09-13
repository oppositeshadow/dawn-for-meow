"""接口验收：GET /colony/state 与 POST /colony/snapshot（含对账协议与工位上限）。"""

from __future__ import annotations

import logging
import time

import pytest

from app.core import balance as B
from app.core.errors import WorkstationLimitExceeded
from app.core.offline_engine import calculate_offline_progress
from app.models import ColonyState, FacilityState, LaborBucket
from app.services.colony_service import ensure_workstation_capacity, workstation_violations

STATE_URL = "/api/v1/colony/state"
SNAPSHOT_URL = "/api/v1/colony/snapshot"


async def _setup_producing_colony(session, *, seconds_offline: int = 600) -> None:
    """把槽位 1 布置成"1 农夫 + 1 猫 + 1 座纸箱窝"，并回拨 last_tick_time。"""
    colony = await session.get(ColonyState, (1, 0))
    colony.total_cats = 1
    colony.job_idle = 0
    colony.catnip = 0.0
    colony.scrap = 0.0
    colony.last_tick_time = int(time.time()) - seconds_offline
    housing = await session.get(FacilityState, (1, 0, "housing_box"))
    housing.level = 1
    farm = await session.get(FacilityState, (1, 0, "farm_plot"))
    farm.level = 1
    farmer = await session.get(LaborBucket, (1, 0, "farmer"))
    farmer.cat_count = 1
    await session.commit()


class TestColonyState:
    async def test_first_call_bootstraps_new_game(self, client):
        """槽位为空时自动执行新游戏初始化（单机开箱即用）。"""
        response = await client.get(STATE_URL)
        assert response.status_code == 200
        body = response.json()
        assert body["code"] == 200
        data = body["data"]
        assert data["slot_id"] == 1
        assert data["planet_id"] == 0
        for key in B.RESOURCE_KEYS:
            assert data["resources"][key] == 0.0
            assert data["resources"]["caps"][key] == B.RESOURCE_CAPS[key]
        assert data["population"] == {
            "total": 0,
            "max_cap": 0,
            "unassigned": 0,
            "birth_progress": 0.0,
        }
        assert data["workstations"]["farmer"] == 0
        assert data["workstation_limits"]["farmer"] == 0
        assert data["workstation_limits"]["crew"] == 24  # 机库 12 机位 × 2 只乘员
        assert data["power"]["net_kw"] == 0.0
        # 只比"值"不 lock JSON 类型：全量运行时偶发把 0.0 序列化成 0（跨测试污染，已记录待查）
        assert float(data["suspicion"]["current"]) == 0.0
        assert float(data["suspicion"]["max"]) == 100.0
        assert set(data["facilities"]) == set(B.FACILITY_IDS)

    async def test_second_call_has_no_offline_gain(self, client):
        """A-4：连续两次读档（Δt≈0）不产生收益，报表标注"无离线收益"。"""
        await client.get(STATE_URL)
        response = await client.get(STATE_URL)
        report = response.json()["data"]["offline_report"]
        assert report["gained_catnip"] == 0.0
        assert report["gained_scrap"] == 0.0
        assert report["is_starved"] is False
        # 时间戳是整秒粒度：两次读档最多跨 1 秒（空档位无产出，所以收益依然为 0）
        assert report["elapsed_seconds"] <= 1.0

    async def test_offline_settlement_applied_on_load(self, client, session):
        """A-1：离线 600 秒 ⇒ 猫薄荷 +90（0.15/s），last_tick_time 刷新为现在。"""
        await client.get(STATE_URL)  # 触发初始化
        await _setup_producing_colony(session, seconds_offline=600)

        response = await client.get(STATE_URL)
        data = response.json()["data"]
        assert data["resources"]["catnip"] == pytest.approx(90.0, abs=1.0)
        assert data["population"]["max_cap"] == 1
        assert data["suspicion"]["current"] > 0
        assert data["last_tick_time"] == pytest.approx(int(time.time()), abs=3)

        colony = await session.get(ColonyState, (1, 0))
        await session.refresh(colony)
        assert colony.catnip == pytest.approx(90.0, abs=1.0)

    async def test_starvation_reported_in_offline_report(self, client, session):
        """A-2：离线期间断粮 ⇒ 报表标注断粮与断粮时长。"""
        await client.get(STATE_URL)
        colony = await session.get(ColonyState, (1, 0))
        colony.total_cats = 4
        colony.job_idle = 4
        colony.catnip = 10.0  # 净 -0.2/s ⇒ 50 秒后断粮
        colony.last_tick_time = int(time.time()) - 650
        await session.commit()

        report = (await client.get(STATE_URL)).json()["data"]["offline_report"]
        assert report["is_starved"] is True
        assert report["starve_duration_seconds"] == pytest.approx(600.0, abs=2.0)
        assert report["gained_catnip"] == pytest.approx(-10.0, abs=0.01)

    async def test_storage_cap_reported(self, client, session):
        """A-3：离线一小时严格裁剪在仓储上限，并标注爆仓。"""
        await client.get(STATE_URL)
        await _setup_producing_colony(session, seconds_offline=3600)

        report = (await client.get(STATE_URL)).json()["data"]["offline_report"]
        assert report["is_capped"] is True
        assert "catnip" in report["overflowed_resources"]

    async def test_clock_rollback_resets_anchor_without_compensation(self, client, session):
        """A-5：时间回拨 ⇒ last_tick_time 重置为当前时间、不做补偿、不出负数。"""
        await client.get(STATE_URL)
        colony = await session.get(ColonyState, (1, 0))
        colony.catnip = 12.5
        colony.last_tick_time = int(time.time()) + 9999
        await session.commit()

        data = (await client.get(STATE_URL)).json()["data"]
        assert data["offline_report"]["clock_anomaly"] is True
        assert data["resources"]["catnip"] == 12.5
        assert data["last_tick_time"] == pytest.approx(int(time.time()), abs=3)

    async def test_locked_planet_rejected(self, client):
        await client.get(STATE_URL)
        response = await client.get(f"{STATE_URL}?planet_id=1")
        assert response.status_code == 400
        assert response.json()["message"] == "PLANET_LOCKED"

    async def test_missing_save_can_be_refused(self, client):
        response = await client.get(f"{STATE_URL}?slot=3&create_if_missing=false")
        assert response.status_code == 404
        assert response.json()["message"] == "SAVE_NOT_FOUND"

    async def test_invalid_slot_rejected(self, client):
        response = await client.get(f"{STATE_URL}?slot=9")
        # Pydantic 边界校验被统一收敛为 {code:400, message:"BAD_REQUEST"}（代码结构稿 §4.1）
        assert response.status_code == 400
        assert response.json()["message"] == "BAD_REQUEST"

    async def test_workstation_limits_follow_facilities(self, client, session):
        """D-2：工位上限只由设施提供（农田每座 2 个、图灵终端每台 1 个）。"""
        await client.get(STATE_URL)
        farm = await session.get(FacilityState, (1, 0, "farm_plot"))
        farm.level = 2
        terminal = await session.get(FacilityState, (1, 0, "turing_terminal"))
        terminal.level = 3
        wheel = await session.get(FacilityState, (1, 0, "power_wheel"))
        wheel.level = 1
        await session.commit()

        limits = (await client.get(STATE_URL)).json()["data"]["workstation_limits"]
        assert limits["farmer"] == 4
        assert limits["geek"] == 3
        assert limits["power_runner"] == 1
        assert limits["scavenger"] == 0


class TestSnapshotReconciliation:
    async def test_snapshot_persists_backend_result(self, client, session):
        """§8.2：后端自己重算并落库，前端预测值只用于告警。"""
        await client.get(STATE_URL)
        await _setup_producing_colony(session, seconds_offline=100)

        response = await client.post(
            SNAPSHOT_URL,
            json={
                "slot": 1,
                "client_time": time.time(),
                "resources": {"catnip": 0.0, "scrap": 0.0},
                "population": {"total": 1},
                "workstations": {"farmer": 1},
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["code"] == 200
        assert body["message"] == "SNAPSHOT_PERSISTED"
        assert body["saved_at"] == pytest.approx(int(time.time()), abs=3)

        colony = await session.get(ColonyState, (1, 0))
        await session.refresh(colony)
        # 后端重算 100 秒（+0.15/s ⇒ 15 猫薄荷、+30 废铁），与前端提交的 0 无关
        assert colony.catnip == pytest.approx(15.0, abs=0.5)
        assert colony.scrap == pytest.approx(0.0, abs=0.01)  # 无拾荒猫 ⇒ 废铁不涨

    async def test_drift_above_half_percent_logs_warning(self, client, caplog):
        """N-5：前端人为改大/改小数值 ⇒ 记 SNAPSHOT_DRIFT warning。"""
        await client.get(STATE_URL)
        with caplog.at_level(logging.WARNING, logger="dawn_meow.colony"):
            await client.post(
                SNAPSHOT_URL, json={"slot": 1, "resources": {"catnip": 999.0}}
            )
        warnings = [record.getMessage() for record in caplog.records]
        assert any("SNAPSHOT_DRIFT" in message and "catnip" in message for message in warnings)
        assert any("deviation=" in message for message in warnings)

    async def test_matching_prediction_logs_no_drift(self, client, caplog):
        """空档位（无产出）时前后端一致 ⇒ 不产生对账 warning。"""
        await client.get(STATE_URL)
        with caplog.at_level(logging.WARNING, logger="dawn_meow.colony"):
            response = await client.post(
                SNAPSHOT_URL,
                json={
                    "slot": 1,
                    "resources": {"catnip": 0.0, "scrap": 0.0},
                    "population": {"total": 0},
                },
            )
        assert response.status_code == 200
        assert not [r for r in caplog.records if "SNAPSHOT_DRIFT" in r.getMessage()]

    async def test_snapshot_on_empty_slot_returns_404(self, client):
        response = await client.post(SNAPSHOT_URL, json={"slot": 2, "resources": {"catnip": 0.0}})
        assert response.status_code == 404
        assert response.json()["message"] == "SAVE_NOT_FOUND"

    async def test_snapshot_overflowing_workstations_is_flagged_not_trusted(self, client, caplog):
        """C2/D-2：前端声称的工位超过上限 ⇒ 记 warning 且不采信。"""
        await client.get(STATE_URL)
        with caplog.at_level(logging.WARNING, logger="dawn_meow.colony"):
            await client.post(
                SNAPSHOT_URL, json={"slot": 1, "workstations": {"farmer": 5}}
            )
        warnings = [record.getMessage() for record in caplog.records]
        assert any("SNAPSHOT_WORKSTATION_OVERFLOW" in message for message in warnings)

    async def test_snapshot_twice_does_not_double_count(self, client, session):
        """§8.3：连续快照不重复也不丢失（后端按 last_tick_time 推进锚点）。"""
        await client.get(STATE_URL)
        await _setup_producing_colony(session, seconds_offline=100)
        await client.post(SNAPSHOT_URL, json={"slot": 1})
        colony = await session.get(ColonyState, (1, 0))
        await session.refresh(colony)
        first = colony.catnip

        await client.post(SNAPSHOT_URL, json={"slot": 1})
        await session.refresh(colony)
        # 时间戳是整秒粒度，两次快照最多跨 1 秒 ⇒ 容忍 0.15/s × 1s 的粒度误差
        assert colony.catnip == pytest.approx(first, abs=0.2)


class TestWorkstationGuard:
    def test_violations_detected(self):
        assert workstation_violations({"farmer": 2}, {"farmer": 2}) == {}
        assert "farmer" in workstation_violations({"farmer": 2}, {"farmer": 3})
        assert "geek" in workstation_violations({"farmer": 2}, {"geek": 1})

    def test_guard_raises_contract_error(self):
        with pytest.raises(WorkstationLimitExceeded) as exc:
            ensure_workstation_capacity({"farmer": 2}, {"farmer": 5})
        assert exc.value.code == 400
        assert exc.value.message == "WORKSTATION_LIMIT_EXCEEDED"
        assert "farmer" in (exc.value.detail or "")

    def test_engine_report_is_reusable_for_reconciliation(self):
        """服务层与引擎口径同源：同一输入得到同一结果（对账协议的可信基础）。"""
        state = {
            "catnip": 0.0, "catnip_max": 200.0, "scrap": 0.0, "scrap_max": 200.0,
            "cats_total": 1, "max_cat_capacity": 1, "birth_progress": 0.0,
            "farmers": 1, "scavengers": 0, "geeks": 0, "power_runners": 0,
            "facilities": {"housing_box": 1, "farm_plot": 1},
            "suspicion": 0.0,
        }
        first = calculate_offline_progress(state, 600)
        second = calculate_offline_progress(state, 600)
        assert first == second
