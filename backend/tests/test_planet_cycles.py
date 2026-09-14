"""外星球周期事件验收（《数值平衡表》§15.5）：确定性、相位边界、效果接入。"""

from __future__ import annotations

import pytest

from app.core import balance as B
from app.models import ColonyState, FacilityState, PlanetState, SaveSlot
from app.services import colony_service

STATE_URL = "/api/v1/colony/state"
LOAD_URL = "/api/v1/game/load"


def test_home_and_unknown_planets_are_neutral() -> None:
    for planet_id in (0, 9):
        cycle = B.planet_cycle(planet_id, 12345)
        assert cycle["label"] == ""
        assert cycle["solar_multiplier"] == 1.0
        assert cycle["production_multiplier"] == 1.0
        assert cycle["raid_multiplier"] == 1.0


def test_cycle_is_deterministic_and_periodic() -> None:
    """同一时刻必然同结果；隔一个周期回到同一相位。"""
    spec = B.PLANET_CYCLES[1]
    first = B.planet_cycle(1, 0)
    assert first == B.planet_cycle(1, 0)
    assert B.planet_cycle(1, int(spec["period"])) == first
    assert first["phase"] == "HIGH" and first["label"] == "高潮"
    assert first["seconds_left"] == int(spec["high_seconds"])


def test_lava_tide_swings_solar_output() -> None:
    spec = B.PLANET_CYCLES[1]
    high = B.planet_cycle(1, 10)  # 高潮中
    low = B.planet_cycle(1, int(spec["high_seconds"]) + 10)  # 低潮中
    assert high["solar_multiplier"] == 1.5
    assert low["solar_multiplier"] == 0.5
    assert high["label"] == "高潮" and low["label"] == "低潮"
    # 低潮剩余时间 = 周期 - 已过时间
    assert low["seconds_left"] == int(spec["period"]) - (int(spec["high_seconds"]) + 10)


def test_ice_moon_polar_day_and_night() -> None:
    spec = B.PLANET_CYCLES[2]
    day = B.planet_cycle(2, 10)
    night = B.planet_cycle(2, int(spec["high_seconds"]) + 10)
    assert day["label"] == "极昼" and day["solar_multiplier"] == 1.5
    assert night["label"] == "极夜" and night["solar_multiplier"] == 0.5
    assert night["production_multiplier"] > 1.0  # 极夜温室保温，产粮更高


def test_phase_seconds_describes_current_phase() -> None:
    """界面进度条口径：`phase_seconds` = 当前相位总长，`seconds_left` = 其中还剩多少。"""
    spec = B.PLANET_CYCLES[1]
    period, high_seconds = int(spec["period"]), int(spec["high_seconds"])
    high = B.planet_cycle(1, 10)
    assert high["period"] == period
    assert high["phase_seconds"] == high_seconds
    assert high["seconds_left"] == high_seconds - 10
    low = B.planet_cycle(1, high_seconds + 10)
    assert low["phase_seconds"] == period - high_seconds
    assert low["seconds_left"] == low["phase_seconds"] - 10
    # 相位进度恒在 [0, phase_seconds] 内（进度条不会越界）
    for offset in range(0, period, 137):
        cycle = B.planet_cycle(1, offset)
        elapsed = cycle["phase_seconds"] - cycle["seconds_left"]
        assert 0 <= elapsed <= cycle["phase_seconds"]


def test_neutral_planet_has_zero_length_phase() -> None:
    """母星/未知星球：整轮与相位长度都是 0（前端据此不画进度条，避免除零）。"""
    for planet_id in (0, 9):
        cycle = B.planet_cycle(planet_id, 12345)
        assert cycle["period"] == 0 and cycle["phase_seconds"] == 0


def test_asteroid_debris_stream_is_risk_and_reward() -> None:
    spec = B.PLANET_CYCLES[3]
    storm = B.planet_cycle(3, 10)
    calm = B.planet_cycle(3, int(spec["high_seconds"]) + 10)
    assert storm["label"] == "来袭"
    assert storm["production_multiplier"] == 1.5  # 拾荒加成
    assert storm["raid_multiplier"] == 2.0        # 但被劫掠风险翻倍
    assert calm["production_multiplier"] == 1.0 and calm["raid_multiplier"] == 1.0


async def test_state_payload_carries_solar_multiplier(client) -> None:
    """状态报文要带上当期系数（前端据此显示"高潮/低潮"）。"""
    await client.get("/api/v1/colony/state", params={"slot": 1, "planet_id": 0})
    data = (await client.get("/api/v1/colony/state", params={"slot": 1, "planet_id": 0})).json()["data"]
    assert data["power"]["net_kw"] == data["power"]["net_kw"]  # 报文结构未变
    cycle = B.planet_cycle(0, 0)
    assert cycle["solar_multiplier"] == 1.0  # 母星永远中性


# ----------------------------------------------------------------------
# 离线区间口径：长离线不能用"结算时点的相位"乘整段（v1.56）
# ----------------------------------------------------------------------
def test_average_inside_one_phase_equals_that_phase() -> None:
    """整段都落在同一个相位里 ⇒ 区间平均就等于该相位的系数。"""
    assert B.planet_cycle_average(1, 100, 500)["solar_multiplier"] == pytest.approx(1.5)  # 全在高潮
    assert B.planet_cycle_average(1, 700, 1100)["solar_multiplier"] == pytest.approx(0.5)  # 全在低潮


def test_average_over_exactly_one_round_is_neutral() -> None:
    """恰好一整轮（600s 高潮 ×1.5 + 600s 低潮 ×0.5）⇒ 平均值 1.0，与起点无关。"""
    period = int(B.PLANET_CYCLES[1]["period"])
    for start in (0, 137, 1_700_000_000):
        avg = B.planet_cycle_average(1, start, start + period)
        assert avg["solar_multiplier"] == pytest.approx(1.0)


def test_average_neutral_for_home_unknown_and_zero_span() -> None:
    neutral = {"solar_multiplier": 1.0, "production_multiplier": 1.0, "raid_multiplier": 1.0}
    for planet_id in (0, 9):
        assert B.planet_cycle_average(planet_id, 0, 100_000) == neutral
    assert B.planet_cycle_average(1, 500, 500) == neutral  # Δt = 0
    assert B.planet_cycle_average(1, 500, 400) == neutral  # 时间回拨（结束早于开始）


def test_average_matches_second_by_second_sum() -> None:
    """3 小时（跨 9 轮）⇒ 与"逐秒求和"的朴素口径一致，且落在 [0.5, 1.5] 内。"""
    start = 1_700_000_000
    end = start + 3 * 3600
    avg = B.planet_cycle_average(1, start, end)
    naive = sum(B.planet_cycle(1, t)["solar_multiplier"] for t in range(start, end)) / (end - start)
    assert avg["solar_multiplier"] == pytest.approx(naive, abs=1e-6)
    assert 0.5 <= avg["solar_multiplier"] <= 1.5
    # 碎星流：来袭 600s×1.5 产出 + 平静 1200s×1.0 ⇒ 整轮平均 1.166667
    assert B.planet_cycle_average(3, start, start + 1800)["production_multiplier"] == pytest.approx(
        1.166667, abs=1e-6
    )
    # 极地日照：极昼 ×0.9 / 极夜 ×1.1 时长相等 ⇒ 整轮平均 1.0
    assert B.planet_cycle_average(2, start, start + 2400)["production_multiplier"] == pytest.approx(1.0)


def test_average_of_long_span_is_bounded_and_fast() -> None:
    """超长离线（一个月）不能逐段循环到卡死：结果仍落在摆动区间内。"""
    avg = B.planet_cycle_average(1, 0, 30 * 86400)
    assert 0.5 <= avg["solar_multiplier"] <= 1.5


async def _open_star(client, session, planet_id: int) -> None:
    await client.get(STATE_URL, params={"slot": 1})
    await session.rollback()
    planet = await session.get(PlanetState, (1, planet_id))
    planet.unlocked = True
    await session.commit()
    await client.get(LOAD_URL, params={"slot": 1, "planet_id": planet_id})


async def _settle_at(session, planet_id: int, *, now: int, offline_seconds: int) -> dict:
    await session.rollback()
    colony = await session.get(ColonyState, (1, planet_id), populate_existing=True)
    colony.last_tick_time = now - offline_seconds
    save = await session.get(SaveSlot, 1)
    await session.commit()
    report, *_ = await colony_service.settle_offline(session, save, planet_id, now=now)
    return report


async def test_offline_report_counts_cycle_rounds(client, session) -> None:
    """离线跨整轮 ⇒ 报表首条 note 报出"经历了几轮"并给区间平均值。"""
    await _open_star(client, session, planet_id=1)
    period = int(B.PLANET_CYCLES[1]["period"])
    now = 1_700_000_000  # % 1200 = 800 ⇒ 结算时点落在低潮
    assert B.planet_cycle(1, now)["label"] == "低潮"

    report = await _settle_at(session, 1, now=now, offline_seconds=3 * period)
    note = report["notes"][0]
    assert "3 轮【岩浆潮汐】" in note
    assert "低潮" in note
    assert "中性" in note  # 整轮多相位相抵 ⇒ 平均值就是 1.0

    # 不足一轮 ⇒ 不提，避免短离线被噪声刷屏
    short = await _settle_at(session, 1, now=now, offline_seconds=period - 60)
    assert all("岩浆潮汐" not in line for line in short["notes"])


async def test_offline_uses_interval_average_not_instant_phase(client, session) -> None:
    """跨整轮时太阳能按区间平均（1.0）出力，而不是结算时点的低潮（0.5）——数字真的变了。"""
    await _open_star(client, session, planet_id=1)
    await session.rollback()
    panel = await session.get(FacilityState, (1, 1, "solar_panel"))
    panel.level = 1
    await session.commit()

    period = int(B.PLANET_CYCLES[1]["period"])
    now = 1_700_000_000
    assert B.planet_cycle(1, now)["solar_multiplier"] == 0.5  # 结算时点是低潮
    report = await _settle_at(session, 1, now=now, offline_seconds=period)
    # 1 座板 × 8 kW × 区间平均 1.0（按旧口径会得到 4.0）
    assert report["power"]["gen_kw"] == pytest.approx(B.SOLAR_PANEL_KW)
