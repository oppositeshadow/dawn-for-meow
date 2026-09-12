"""模块 A / B / C 的引擎级验收：断粮、爆仓、Δt=0、时间回拨、繁育曲线、电力与警戒度。"""

from __future__ import annotations

import pytest

from app.core import balance as B
from app.core.offline_engine import (
    calculate_offline_progress,
    integrate_breeding,
    power_balance,
    suspicion_rate_per_second,
)


def make_state(**overrides) -> dict:
    """开局 1 农夫 + 1 猫的最小状态（净 +0.15 猫薄荷/s）。"""
    state = {
        "catnip": 0.0,
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
        "max_cat_capacity": 2,
        "birth_progress": 0.0,
        "farmers": 1,
        "scavengers": 1,
        "geeks": 0,
        "power_runners": 0,
        "facilities": {"housing_box": 2, "farm_plot": 1, "scavenge_station": 1},
        "battery_kwh": 0.0,
        "battery_kwh_max": 200.0,
        "suspicion": 0.0,
    }
    state.update(overrides)
    return state


class TestOfflineBasics:
    def test_normal_production(self):
        """A-1：1 农夫 + 1 猫、Δt=100s ⇒ 猫薄荷 +15（0.15/s）、废铁 +50（0.5/s）。"""
        report = calculate_offline_progress(make_state(), 100)
        assert report["gained_resources"]["catnip"] == pytest.approx(15.0, abs=1e-6)
        assert report["gained_resources"]["scrap"] == pytest.approx(50.0, abs=1e-6)
        # 拾荒猫拆解家电的芯片产出（0.01/s × 100s）
        assert report["gained_resources"]["chips"] == pytest.approx(1.0, abs=1e-6)
        assert report["is_starving"] is False
        assert report["final"]["catnip"] == pytest.approx(15.0, abs=1e-6)
        assert report["clock_anomaly"] is False

    def test_zero_delta_changes_nothing(self):
        """A-4：Δt = 0 ⇒ 不产生任何资源变化，报表标注"无离线收益"。"""
        state = make_state(catnip=42.0, scrap=7.0, birth_progress=0.5)
        report = calculate_offline_progress(state, 0)
        assert report["gained_resources"] == {"catnip": 0.0, "scrap": 0.0, "chips": 0.0}
        assert report["gained_cats"] == 0
        assert report["applied_seconds"] == 0.0
        assert report["final"]["catnip"] == 42.0
        assert report["final"]["birth_progress"] == 0.5
        assert any("Δt = 0" in note for note in report["notes"])

    def test_negative_delta_is_clock_rollback(self):
        """A-5：时间回拨 ⇒ 不做补偿、不出负数资源，仅标记 clock_anomaly。"""
        state = make_state(catnip=10.0)
        report = calculate_offline_progress(state, -3600)
        assert report["clock_anomaly"] is True
        assert report["gained_resources"] == {"catnip": 0.0, "scrap": 0.0, "chips": 0.0}
        assert report["final"]["catnip"] == 10.0
        assert report["final"]["catnip"] >= 0


class TestStarvation:
    def test_starvation_truncates_production_and_decays_breeding(self):
        """A-2 / B-1：库存耗尽后拾荒归 0、繁育进度 −0.05/分钟、农夫保留 30%。"""
        # 无农夫 ⇒ 净 -0.1/s（2 猫 × 0.05）；库存 1 ⇒ 10 秒后断粮，剩 90 秒绝食
        state = make_state(catnip=1.0, farmers=0, cats_total=2, birth_progress=0.5)
        report = calculate_offline_progress(state, 100)
        assert report["is_starving"] is True
        assert report["starve_duration_seconds"] == pytest.approx(90.0, abs=1e-6)
        # 断粮段拾荒产出锁死为 0：废铁只来自前 10 秒正常运转
        assert report["gained_resources"]["scrap"] == pytest.approx(
            B.SCAVENGER_SCRAP_PER_SEC * 10, abs=1e-6
        )
        # 繁育进度：0.5 − 0.05/60 × 90 = 0.425
        assert report["final"]["birth_progress"] == pytest.approx(0.425, abs=1e-4)
        assert report["gained_cats"] == 0

    def test_starve_farmer_keeps_survival_output(self):
        """断粮时农夫猫仍以 30%（0.06/s）翻野草维持底线。"""
        # 2 农夫 = +0.4/s，16 只猫 = −0.8/s ⇒ 净 −0.4/s，属于断粮
        state = make_state(catnip=0.0, farmers=2, cats_total=16, scavengers=3)
        report = calculate_offline_progress(state, 600)
        assert report["is_starving"] is True
        assert report["starve_duration_seconds"] == pytest.approx(600.0, abs=1e-6)
        assert report["gained_resources"]["catnip"] == pytest.approx(2 * 0.06 * 600, abs=1e-2)
        assert report["gained_resources"]["scrap"] == 0.0

    def test_break_even_is_not_starvation(self):
        """净产出恰好为 0 ⇒ 不算断粮（2 农夫 0.4/s 恰好养 8 只猫）。"""
        report = calculate_offline_progress(
            make_state(catnip=0.0, farmers=2, cats_total=8), 600
        )
        assert report["is_starving"] is False
        assert report["final"]["catnip"] == 0.0

    def test_no_farmer_means_stock_stays_zero(self):
        """断粮且没有农夫 ⇒ 库存始终为 0，不出现负数。"""
        state = make_state(catnip=0.5, farmers=0, cats_total=3)
        report = calculate_offline_progress(state, 300)
        assert report["final"]["catnip"] == 0.0
        assert report["gained_resources"]["catnip"] == pytest.approx(-0.5, abs=1e-6)

    def test_starvation_ends_with_next_tick_after_refill(self):
        """B-2：猫薄荷回正后立即全面复工（下一段结算照常产出）。"""
        starving = calculate_offline_progress(make_state(catnip=0.0, farmers=0, cats_total=4), 60)
        assert starving["is_starving"] is True
        recovered_state = make_state(catnip=50.0, farmers=1, cats_total=1)
        recovered = calculate_offline_progress(recovered_state, 60)
        assert recovered["is_starving"] is False
        assert recovered["gained_resources"]["catnip"] == pytest.approx(9.0, abs=1e-6)


class TestStorageCap:
    def test_overflow_is_discarded_and_reported(self):
        """A-3：产出严格裁剪在仓储上限，超出部分丢弃并标记爆仓。"""
        state = make_state(catnip=199.0, scrap=199.0, cats_total=1)
        report = calculate_offline_progress(state, 3600)
        assert report["final"]["catnip"] == 200.0
        assert report["final"]["scrap"] == 200.0
        assert set(report["overflowed"]) == {"catnip", "scrap"}
        assert report["gained_resources"]["catnip"] == pytest.approx(1.0, abs=1e-6)
        assert report["gained_resources"]["scrap"] == pytest.approx(1.0, abs=1e-6)

    def test_resources_never_negative(self):
        state = make_state(catnip=0.2, scrap=0.0, farmers=0, cats_total=5)
        report = calculate_offline_progress(state, 10_000)
        assert report["final"]["catnip"] >= 0
        assert report["final"]["scrap"] >= 0


class TestBreeding:
    def test_second_cat_arrives_at_about_200_seconds(self):
        """C-1：N=1、K=2、饱食 ⇒ 约 200 秒（±10%）迎来第 2 只猫。"""
        state = make_state(catnip=50.0, cats_total=1, max_cat_capacity=2, farmers=1)
        report_half = calculate_offline_progress(state, 170)
        assert report_half["gained_cats"] == 0  # 170 秒还差一点
        report = calculate_offline_progress(state, 200)
        assert report["gained_cats"] == 1
        assert report["final"]["cats_total"] == 2

    def test_population_never_exceeds_capacity(self):
        """C-2：N 逼近 K ⇒ 平滑趋 0，绝不突破人口上限。"""
        state = make_state(catnip=200.0, cats_total=1, max_cat_capacity=2)
        report = calculate_offline_progress(state, 24 * 3600)
        assert report["final"]["cats_total"] == 2
        assert report["final"]["birth_progress"] == 0.0

    def test_breeding_progress_keeps_fraction(self):
        """小数进度池不被截断：60 秒的 0.3 进度要完整保留。"""
        population, progress = integrate_breeding(1, 0.0, 2, 60)
        assert population == 1
        assert progress == pytest.approx(0.3, abs=1e-9)

    def test_zero_capacity_or_zero_population_stalls(self):
        assert integrate_breeding(0, 0.0, 0, 600) == (0, 0.0)
        assert integrate_breeding(0, 0.5, 5, 600) == (0, 0.5)

    def test_capacity_below_population_is_safe(self):
        """C-5：拆窝导致 K < N 时不出现负猫口、不崩。"""
        report = calculate_offline_progress(make_state(cats_total=5, max_cat_capacity=2), 600)
        assert report["final"]["cats_total"] == 5
        assert report["gained_cats"] == 0


class TestPowerAndSuspicion:
    def test_power_balance_and_blackout(self):
        """B-3 / §7.1：滚轮 5 kW、图灵终端 −6 kW、生活供暖 −2 kW/10 只猫。"""
        facilities = {"power_wheel": 1, "turing_terminal": 1, "solar_panel": 1}
        power = power_balance(facilities, {"power_runner": 1}, total_cats=10)
        assert power["gen_kw"] == pytest.approx(13.0)
        assert power["load_kw"] == pytest.approx(8.0)
        assert power["net_kw"] == pytest.approx(5.0)
        assert power["blackout"] is False

        blackout = power_balance({"turing_terminal": 2}, {"power_runner": 0}, total_cats=0)
        assert blackout["net_kw"] < 0
        assert blackout["blackout"] is True

    def test_blackout_cuts_research_to_zero(self):
        state = make_state(
            catnip=100.0,
            cats_total=1,
            farmers=1,
            geeks=2,
            facilities={"housing_box": 1, "farm_plot": 1, "turing_terminal": 2},
        )
        report = calculate_offline_progress(state, 600)
        assert report["power"]["blackout"] is True
        assert report["gained_research"] == 0.0
        assert any("断电" in note for note in report["notes"])

    def test_research_and_battery_charge_when_powered(self):
        state = make_state(
            catnip=100.0,
            cats_total=1,
            farmers=1,
            geeks=1,
            power_runners=2,
            facilities={"housing_box": 1, "farm_plot": 1, "turing_terminal": 1, "power_wheel": 2},
        )
        report = calculate_offline_progress(state, 60)
        assert report["power"]["net_kw"] == pytest.approx(4.0)
        assert report["gained_research"] == pytest.approx(60.0)
        # 充电 = 4 kW × 0.1 kWh/s × 60s = 24 kWh
        assert report["charged_kwh"] == pytest.approx(24.0, abs=1e-6)
        assert report["final"]["battery_kwh"] == pytest.approx(24.0, abs=1e-6)

    def test_battery_charge_is_capped(self):
        state = make_state(
            catnip=100.0,
            geeks=1,
            power_runners=2,
            facilities={"housing_box": 1, "farm_plot": 1, "power_wheel": 2, "turing_terminal": 1},
            battery_kwh=199.0,
        )
        report = calculate_offline_progress(state, 3600)
        assert report["final"]["battery_kwh"] == 200.0
        assert report["charged_kwh"] == pytest.approx(1.0, abs=1e-6)

    def test_suspicion_accrual_matches_gdd_window(self):
        """F-1：开局 2~3 座设施挂机 6 分钟约 +3 点。"""
        facilities = {"housing_box": 1, "farm_plot": 1, "scavenge_station": 1}
        rate = suspicion_rate_per_second(facilities)
        assert rate == pytest.approx(B.SUSPICION_BASE_NOISE_PER_SEC + 3 * 0.002 - 0.001, abs=1e-9)
        report = calculate_offline_progress(make_state(facilities=facilities), 360)
        assert report["suspicion_delta"] == pytest.approx(3.6, abs=0.05)

    def test_acoustic_layer_reduces_suspicion(self):
        """§8.2：隔音层 Lv1 乘算 ×0.70。"""
        quiet = calculate_offline_progress(
            make_state(facilities={"housing_box": 1, "farm_plot": 1, "acoustic_layer": 1}), 600
        )
        loud = calculate_offline_progress(
            make_state(facilities={"housing_box": 1, "farm_plot": 1}), 600
        )
        assert quiet["suspicion_delta"] < loud["suspicion_delta"]
        # 隔音层自身也按"每座运转设施 +0.002/s"口径计数（字面规则），故为 3 座设施
        assert quiet["suspicion_delta"] == pytest.approx(
            (0.005 + 0.002 * 3) * 0.7 * 600 - 0.001 * 600, abs=1e-3
        )

    def test_suspicion_is_clamped_at_100(self):
        report = calculate_offline_progress(
            make_state(suspicion=99.0, facilities={"housing_box": 1, "farm_plot": 1}), 10_000
        )
        assert report["final"]["suspicion"] == 100.0
        assert any("警戒度" in note for note in report["notes"])
