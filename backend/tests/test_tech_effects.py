"""模块 E5 验收：科技 buff_payload 真正接入结算（catnip_efficiency 首项）。"""

from __future__ import annotations

from sqlalchemy import select

from app.core import balance as B
from app.models import TechRecord
from app.models.tech import TechStatus
from app.services import tech_service

STATE_URL = "/api/v1/colony/state"
TREE_URL = "/api/v1/tech/tree"

FARM_TECH = "tech_hydroponics_basics"  # 母星 Tier 1：buff_payload.catnip_efficiency = 0.2


async def _bootstrap(client) -> None:
    await client.get(STATE_URL, params={"slot": 1})


async def _unlock(session, tech_id: str) -> TechRecord:
    await session.rollback()
    record = await session.get(TechRecord, (1, 0, tech_id), populate_existing=True)
    record.status = TechStatus.UNLOCKED
    record.current_progress = float(record.target_cost)
    await session.commit()
    return record


async def _farmers(client, session, *, farmers: int) -> float:
    """派 n 只农夫，回报告周期内的猫薄荷净增速（用于比较科技加成前后）。"""
    await session.rollback()
    from app.models import LaborBucket

    bucket = await session.get(LaborBucket, (1, 0, "farmer"))
    bucket.cat_count = farmers
    await session.commit()
    await client.get(STATE_URL, params={"slot": 1})
    return 0.0


def _net_catnip(report: dict) -> float:
    gained = float(report["gained_catnip"])
    return gained


async def test_locked_tech_contributes_nothing(client, session) -> None:
    await _bootstrap(client)
    data = (await client.get(STATE_URL, params={"slot": 1})).json()["data"]
    assert data["tech_effects"]["catnip_efficiency"] == 0.0

    await session.rollback()
    totals = await tech_service.unlocked_effects(session, 1, 0)
    # 白名单每个键都给 0 占位（当前白名单：猫薄荷 / 发电 / 两种装甲写法）
    assert totals == {"catnip_efficiency": 0.0, "power_kw": 0.0, "fleet_armor": 0.0, "armor_bonus": 0.0}


async def test_unlocked_tech_bonus_shows_and_is_capped(client, session) -> None:
    await _bootstrap(client)
    await _unlock(session, FARM_TECH)
    data = (await client.get(STATE_URL, params={"slot": 1})).json()["data"]
    assert data["tech_effects"]["catnip_efficiency"] == 0.2

    # 人为把某节点载荷改成超标值 ⇒ 被上限夹住（防止未来叠加拉爆产粮曲线）
    await session.rollback()
    record = await session.get(TechRecord, (1, 0, FARM_TECH), populate_existing=True)
    record.buff_payload = {"catnip_efficiency": 5.0}
    await session.commit()
    totals = await tech_service.unlocked_effects(session, 1, 0)
    assert totals["catnip_efficiency"] == B.TECH_CATNIP_EFFICIENCY_CAP


async def test_bonus_actually_multiplies_farmer_output(client, session) -> None:
    """同一批农夫、同一段时间：解锁科技后猫薄荷增量应恰好 ×1.2。"""
    await _bootstrap(client)

    async def harvest(seconds: int) -> float:
        await session.rollback()
        from app.models import ColonyState

        colony = await session.get(ColonyState, (1, 0))
        colony.catnip = 0.0
        colony.total_cats = 0  # 排除消耗，只看产出
        colony.job_idle = 0
        colony.last_tick_time = int(colony.last_tick_time) - seconds
        await session.commit()
        data = (await client.get(STATE_URL, params={"slot": 1})).json()["data"]
        report = data["offline_report"]
        return float(report["gained_catnip"])

    await _farmers(client, session, farmers=2)
    # 300 秒：产出 120 上下，稳在 200 仓储上限之内（否则会被爆仓夹住，看不出倍率）
    baseline = await harvest(300)
    assert baseline > 0

    await _unlock(session, FARM_TECH)
    boosted = await harvest(300)
    # 秒级时间戳截断会让两次 Δt 差 1 秒 ⇒ 用 2% 容差（数值本身仍是 ×1.2 的口径）
    expected = baseline * 1.2
    assert abs(boosted - expected) <= expected * 0.02



async def test_whitelist_settles_power_kw_but_not_other_keys(client, session) -> None:
    """白名单只认已接入的键：`power_kw` 现在真发电，`armor_bonus` 这类仍不生效。"""
    await _bootstrap(client)
    before = (await client.get(STATE_URL, params={"slot": 1})).json()["data"]["power"]["gen_kw"]
    row = (
        await session.execute(
            select(TechRecord).where(TechRecord.slot_id == 1, TechRecord.planet_id == 0)
        )
    ).scalars().first()
    row.buff_payload = {"power_kw": 5, "catnip_efficiency": 0.1, "armor_bonus": 0.9}
    row.status = TechStatus.UNLOCKED
    await session.commit()

    totals = await tech_service.unlocked_effects(session, 1, 0)
    assert totals["power_kw"] == 5.0
    assert totals["catnip_efficiency"] == 0.1
    assert totals["armor_bonus"] == 0.9  # 装甲加成同样已接线（全军装甲入口）
    assert "smelt_speed" not in totals  # 未接入的键绝不偷偷生效

    after = (await client.get(STATE_URL, params={"slot": 1})).json()["data"]["power"]["gen_kw"]
    assert round(after - before, 2) == 5.0  # 净电力真的多了 5 kW
