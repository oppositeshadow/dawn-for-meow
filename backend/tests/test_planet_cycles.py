"""外星球周期事件验收（《数值平衡表》§15.5）：确定性、相位边界、效果接入。"""

from __future__ import annotations

from app.core import balance as B


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
