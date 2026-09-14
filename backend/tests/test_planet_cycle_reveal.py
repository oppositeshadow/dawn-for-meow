"""§15.6 潮汐监测（分档揭示）验收：没科技就什么都看不见，点亮后逐档出现相位与倒计时。

口径要点：**裁剪发生在后端**（`balance.planet_cycle_view`）——前端"只是不画"的话，
抓一次 `/planet/state` 就全看穿了，所以用例直接断言**接口返回里有没有这个字段**。
"""

from __future__ import annotations

from app.core import balance as B
from app.models import TechRecord
from app.models.tech import TechStatus
from app.services import planet_service

STATE_URL = "/api/v1/colony/state"
PLANET_URL = "/api/v1/planet/state"

OBSERVE_TECH = B.PLANET_CYCLE_REVEAL_TECHS[0]   # 暗区短波穿透电台（T3）
FORECAST_TECH = B.PLANET_CYCLE_REVEAL_TECHS[1]  # 近轨矢量突破导航中枢（T4）
COUNTDOWN_KEYS = ("seconds_left", "phase_seconds", "period")


# ----------------------------------------------------------------------
# 纯函数：裁剪口径
# ----------------------------------------------------------------------
def test_view_is_none_before_any_tech() -> None:
    assert B.planet_cycle_view(1, 100, 0) is None


def test_view_level1_has_phase_but_no_countdown() -> None:
    """L1 观测档：只知道"现在是什么潮"，不知道还剩多久。"""
    view = B.planet_cycle_view(1, 100, 1)
    assert view is not None
    assert view["name"] == "岩浆潮汐" and view["label"] == "高潮" and view["phase"] == "HIGH"
    assert view["solar_multiplier"] == 1.5
    assert all(key not in view for key in COUNTDOWN_KEYS)


def test_view_level2_adds_countdown() -> None:
    """L2 预报档：多出倒计时与相位长度（前端据此画进度条）。"""
    view = B.planet_cycle_view(1, 100, 2)
    assert view is not None
    assert view["seconds_left"] == 500 and view["phase_seconds"] == 600 and view["period"] == 1200
    # 星带的"被劫掠 ×2"在 L1 就能看到（观测的意义就是知道要不要发货）
    assert B.planet_cycle_view(3, 100, 1)["raid_multiplier"] == 2.0


def test_view_is_none_for_home_and_unknown_planets() -> None:
    """母星与未登记星球本来就没有潮汐 ⇒ 各档一律不给（前端据此不画任何东西）。"""
    for planet_id in (0, 9):
        for level in (0, 1, 2):
            assert B.planet_cycle_view(planet_id, 100, level) is None


# ----------------------------------------------------------------------
# 接口：星图 payload 真的按档位裁剪
# ----------------------------------------------------------------------
async def _unlock(session, tech_id: str) -> None:
    await session.rollback()
    record = await session.get(TechRecord, (1, 0, tech_id), populate_existing=True)
    record.status = TechStatus.UNLOCKED
    record.current_progress = float(record.target_cost)
    await session.commit()


async def _cycles(client) -> dict[int, dict | None]:
    data = (await client.get(PLANET_URL, params={"slot": 1})).json()["data"]
    return {row["planet_id"]: row["cycle"] for row in data["planets"]}


async def test_star_map_hides_all_tides_without_tech(client) -> None:
    """L0：星图上**一个字都没有**，连字段都不返回（抓包也看不到）。"""
    await client.get(STATE_URL, params={"slot": 1})
    cycles = await _cycles(client)
    assert set(cycles) == {0, 1, 2, 3}
    assert all(value is None for value in cycles.values())


async def test_star_map_reveals_phase_then_countdown(client, session) -> None:
    """点亮短波电台 ⇒ 出现"当前相位"；再点亮导航中枢 ⇒ 出现倒计时。"""
    await client.get(STATE_URL, params={"slot": 1})
    assert (await _cycles(client))[1] is None

    await _unlock(session, OBSERVE_TECH)
    lava = (await _cycles(client))[1]
    assert lava is not None and lava["name"] == "岩浆潮汐"
    assert all(key not in lava for key in COUNTDOWN_KEYS)  # L1 还没有倒计时
    # 但当期系数已经能看到：断言"相位 ⇄ 系数"自洽（不锁挂钟 = 用例不随相位翻面，见 skill 八·三）
    assert lava["solar_multiplier"] == (1.5 if lava["phase"] == "HIGH" else 0.5)

    await _unlock(session, FORECAST_TECH)
    lava = (await _cycles(client))[1]
    assert lava is not None
    assert lava["phase_seconds"] > 0 and lava["seconds_left"] >= 0


async def test_reveal_level_is_stepwise(client, session) -> None:
    """阶梯式：绕过前置只点亮 L2 的节点也不给能力（低档没点亮，高档不算）。"""
    await client.get(STATE_URL, params={"slot": 1})
    await _unlock(session, FORECAST_TECH)
    assert (await _cycles(client))[1] is None


# ----------------------------------------------------------------------
# 航线事件 → 报表文案（"不在场时发生的事"必须留痕）
# ----------------------------------------------------------------------
def test_describe_route_events_covers_arrival_and_raid() -> None:
    lines = planet_service.describe_route_events(
        [
            {"type": "ROUTE_ARRIVED", "to_planet": 1, "cat_count": 2, "cargo": {"scrap": 30.0}},
            {"type": "ROUTE_RAIDED", "delay_seconds": 600, "note": "碎星流·来袭期间穿越星带"},
        ]
    )
    assert lines == [
        "跨星航线抵达【二号星·极热熔岩铸造星】：2 只猫 + 机械废铁 ×30",
        "跨星航线被劫掠：延误 10 分钟，猫一只没丢；碎星流·来袭期间穿越星带",
    ]
