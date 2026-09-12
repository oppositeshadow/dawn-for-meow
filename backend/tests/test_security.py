"""模块 F 验收：满警戒度的三级安防预案（诱饵 → 战车（待模块 G）→ 静默关灯）。"""

from __future__ import annotations

import pytest

from app.core import balance as B
from app.core.offline_engine import calculate_offline_progress


def make_state(**overrides) -> dict:
    """1 农夫 + 1 拾荒 + 1 猫，警戒度可控。"""
    state = {
        "catnip": 100.0,
        "catnip_max": 200.0,
        "scrap": 0.0,
        "scrap_max": 200.0,
        "chips": 0.0,
        "chips_max": 100.0,
        "alloys": 0.0,
        "alloys_max": 50.0,
        "battery": 0.0,
        "battery_max": 50.0,
        "lube": 0.0,
        "lube_max": 50.0,
        "cats_total": 1,
        "max_cat_capacity": 1,
        "birth_progress": 0.0,
        "farmers": 1,
        "scavengers": 1,
        "geeks": 0,
        "power_runners": 0,
        "facilities": {"housing_box": 1, "farm_plot": 1, "scavenge_station": 1},
        "battery_kwh": 0.0,
        "battery_kwh_max": 200.0,
        "suspicion": 99.0,
        "now": 1_780_000_000,
        "decoy_count": 0,
        "idle_vehicles": 0,
    }
    state.update(overrides)
    return state


class TestDecoy:
    def test_decoy_fires_at_full_suspicion(self):
        """§8.3 优先级 1：满 100 时自动弹射诱饵，−30 点并进入 5 分钟误报冷却。"""
        state = make_state(suspicion=100.0, decoy_count=2)
        report = calculate_offline_progress(state, 10)
        events = [event["type"] for event in report["security_events"]]
        assert "DECOY_TRIGGERED" in events
        assert report["decoy_count"] == 1
        assert report["final"]["suspicion"] == pytest.approx(70.0, abs=0.2)
        assert report["false_alarm_cooldown_until"] == state["now"] + int(
            B.DECOY_FALSE_ALARM_COOLDOWN_SECONDS
        )

    def test_cooldown_suppresses_second_alert_and_decays_faster(self):
        """§8.4：冷却期内不再触发满值判定，且额外 −0.05 点/s 加速衰减。"""
        state = make_state(
            suspicion=100.0,
            decoy_count=1,
            false_alarm_cooldown_until=1_780_000_000 + 300,  # 正处于误报冷却中
        )
        report = calculate_offline_progress(state, 60)
        events = [event["type"] for event in report["security_events"]]
        assert "ALERT_SUPPRESSED" in events
        assert report["decoy_count"] == 1  # 没再消耗诱饵
        # 满值判定发生在冷却衰减之前（100 → 触发抑制），随后基础噪音与冷却加速衰减叠加
        assert report["final"]["suspicion"] < 100.0

    def test_no_decoy_and_policy_off_only_alerts(self):
        """预案全关 ⇒ 只记录告警，不做任何自动处置（更不伪造胜利）。"""
        state = make_state(
            suspicion=100.0,
            security_policy={"p1_use_decoy": False, "p2_use_vehicle": False, "p3_go_dark": False},
        )
        report = calculate_offline_progress(state, 30)
        events = [event["type"] for event in report["security_events"]]
        assert "ALERT_ONLY" in events
        assert not report["go_dark"]
        assert report["final"]["suspicion"] >= 100.0

    def test_vehicle_branch_is_honest_about_missing_combat_engine(self):
        """有闲置载具但战斗引擎未落地（模块 G）⇒ 记录 P2_PENDING 并转静默关灯。"""
        state = make_state(suspicion=100.0, decoy_count=0, idle_vehicles=1)
        report = calculate_offline_progress(state, 30)
        events = [event["type"] for event in report["security_events"]]
        assert "P2_PENDING" in events
        assert "GO_DARK_START" in events
        assert report["go_dark"] is True


class TestGoDark:
    def test_go_dark_started_when_no_decoy(self):
        """§8.3 优先级 3：无诱饵时进入静默关灯。"""
        report = calculate_offline_progress(make_state(suspicion=100.0, decoy_count=0), 30)
        assert report["go_dark"] is True
        assert "GO_DARK_START" in [event["type"] for event in report["security_events"]]

    def test_go_dark_locks_all_labor(self):
        """静默期内全员停工：猫薄荷、废铁、芯片、科研全部为 0，消费照旧。"""
        state = make_state(suspicion=50.0, decoy_count=0)
        state["go_dark"] = True
        state["catnip"] = 100.0
        report = calculate_offline_progress(state, 60)
        assert report["gained_resources"]["scrap"] == 0.0
        assert report["gained_resources"]["chips"] == 0.0
        assert report["gained_research"] == 0.0
        # 只有消耗：−0.05/s × 60 = −3
        assert report["gained_resources"]["catnip"] == pytest.approx(-3.0, abs=1e-6)
        assert "GO_DARK_ACTIVE" in [event["type"] for event in report["security_events"]]

    def test_go_dark_decays_at_5x_and_recovers_at_20(self):
        """静默关灯 ×5 速率衰减，跌回 20 点自动复工。"""
        state = make_state(suspicion=22.0, decoy_count=0)
        state["go_dark"] = True
        state["facilities"] = {}  # 关灯时设施噪音本就按 0 计
        report = calculate_offline_progress(state, 400)  # −0.005/s × 400s = −2.0
        events = [event["type"] for event in report["security_events"]]
        assert "GO_DARK_END" in events
        assert report["go_dark"] is False
        assert report["final"]["suspicion"] <= B.GO_DARK_RECOVER_THRESHOLD

    def test_facility_noise_still_applies_outside_go_dark(self):
        quiet = calculate_offline_progress(make_state(suspicion=10.0, facilities={}), 60)
        loud = calculate_offline_progress(
            make_state(suspicion=10.0, facilities={"housing_box": 1, "farm_plot": 1}), 60
        )
        assert loud["final"]["suspicion"] > quiet["final"]["suspicion"]


class TestSecurityPayload:
    async def test_state_payload_exposes_security_block(self, client):
        await client.get("/api/v1/colony/state")
        data = (await client.get("/api/v1/colony/state")).json()["data"]
        assert data["security"]["decoy_count"] == 0
        assert data["security"]["go_dark"] is False
        assert data["security"]["policy"]["p1_use_decoy"] is True
        assert data["security"]["cooldown_left_seconds"] == 0
