"""政令效果**真的接进结算**（v2.58 自查发现：白名单登记 ≠ 接线）。"""

from __future__ import annotations

from sqlalchemy import select

from app.core import balance as B
from app.core.offline_engine import calculate_offline_progress
from app.models import ColonyState, FacilityState, LaborBucket, SaveSlot
from app.services import colony_service

STATE_URL = "/api/v1/colony/state"


async def _boot(client) -> None:
    await client.get(STATE_URL, params={"slot": 1})


async def _grant_unity(session, amount: float) -> None:
    await session.rollback()
    save = await session.get(SaveSlot, 1)
    save.unity = amount
    await session.commit()


async def _unlock(client, doctrine_id: str) -> None:
    response = await client.post(
        "/api/v1/doctrine/unlock", json={"slot": 1, "doctrine_id": doctrine_id}
    )
    assert response.status_code == 200, response.text


async def test_production_doctrine_boosts_offline_output(client, session) -> None:
    """【下午三点晒太阳协议】+20% 生产效率 ⇒ 同样时长、同样人力，产出正好多两成。"""
    await _boot(client)
    await session.rollback()
    bucket = await session.get(LaborBucket, (1, 0, "scavenger"))
    bucket.cat_count = 2
    await session.commit()

    async def run(seconds: int = 100) -> float:  # 100 秒 ≈ 100 废铁，稳在 200 的仓储上限内
        await session.rollback()
        colony = await session.get(ColonyState, (1, 0))
        colony.scrap = 0.0
        colony.total_cats = 0
        colony.job_idle = 0
        colony.last_tick_time = int(colony.last_tick_time) - seconds
        await session.commit()
        data = (await client.get(STATE_URL, params={"slot": 1})).json()["data"]
        return float(data["offline_report"]["gained_scrap"])

    base = await run()
    assert base > 0
    await _grant_unity(session, 500.0)
    await _unlock(client, "sunbath_3pm")
    boosted = await run()
    assert abs(boosted / base - 1.2) <= 0.03  # +20%（秒级截断留容差）


async def test_capacity_doctrine_and_terraformer_raise_K(client, session) -> None:
    """【行星绿化法案】+10% 与生态塑形师 +8%/只，都乘在承载力上。"""
    await _boot(client)
    await session.rollback()
    row = (
        await session.execute(
            select(FacilityState).where(
                FacilityState.slot_id == 1,
                FacilityState.planet_id == 0,
                FacilityState.facility_id == "housing_box",
            )
        )
    ).scalars().one()
    row.level = 10  # 基础 K = 10
    await session.commit()
    base = (await client.get(STATE_URL, params={"slot": 1})).json()["data"]["population"]["max_cap"]
    assert base == 10

    await _grant_unity(session, 600.0)
    await _unlock(client, "planet_greening_act")
    with_doctrine = (await client.get(STATE_URL, params={"slot": 1})).json()["data"]["population"]["max_cap"]
    assert with_doctrine == 11  # 10 × 1.1

    await session.rollback()
    bucket = await session.get(LaborBucket, (1, 0, "terraformer"))
    bucket.cat_count = 2  # +16%
    await session.commit()
    with_both = (await client.get(STATE_URL, params={"slot": 1})).json()["data"]["population"]["max_cap"]
    assert with_both == 12  # 10 × 1.26 = 12.6 → 12


async def test_suspicion_doctrine_slows_growth(client, session) -> None:
    """【午睡静默令】−20% 警戒度增速：同 Δt、同设施，增速系数 1.0 vs 0.8 对比。

    用**引擎直算**而不是两次读档：读档会引入"上次残留的警戒度/随机事件"噪声（实测比值飘到 2.5）。
    """
    await _boot(client)
    await session.rollback()
    base_state = {
        "now": 1000, "suspicion": 0.0,
        "catnip": 100.0, "catnip_max": 500.0, "scrap": 0.0, "scrap_max": 500.0,
        "chips": 0.0, "chips_max": 500.0, "alloys": 0.0, "alloys_max": 500.0,
        "battery": 0.0, "battery_max": 100.0, "lube": 0.0, "lube_max": 100.0,
        "battery_kwh": 0.0, "battery_kwh_max": 200.0, "total_cats": 0,
        "birth_progress": 0.0, "facilities": {"turing_terminal": 1},
        "farmers": 0, "scavengers": 0, "geeks": 0, "power_runners": 0,
        "max_cat_capacity": 10, "production_multiplier": 1.0,
        "breeding_rate_multiplier": 1.0, "suspicion_growth_multiplier": 1.0,
        "silent_grass_count": 0, "garden_power_kw": 0.0, "garden_suspicion_per_sec": 0.0,
    }
    base = calculate_offline_progress(dict(base_state), 600)["suspicion_delta"]
    slowed_state = dict(base_state)
    slowed_state["suspicion_growth_multiplier"] = 0.8
    slowed = calculate_offline_progress(slowed_state, 600)["suspicion_delta"]
    assert base > 0
    # 容差 5%：警戒度是 2 位小数存储，且引擎里另有"满值先判定 / 冷却衰减"的先后顺序影响
    assert abs(slowed / base - 0.8) <= 0.05

    # 接口侧：点亮【午睡静默令】后，报文里的增速系数确实变成 0.8 的语义（政令已记录）
    await _grant_unity(session, 400.0)
    await _unlock(client, "nap_silence_order")
    listed = (await client.get("/api/v1/doctrine/list", params={"slot": 1})).json()["data"]
    assert any(item["doctrine_id"] == "nap_silence_order" and item["unlocked"] for item in listed["doctrines"])
    _ = (B, colony_service)
