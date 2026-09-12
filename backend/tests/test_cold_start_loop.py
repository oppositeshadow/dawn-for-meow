"""模块 C2 / C3 / D 与冷启动心流的接口级验收（GDD §1.3 / WBS 模块 N1）。"""

from __future__ import annotations

import time

import pytest

from app.core import balance as B
from app.models import ColonyState, FacilityState

STATE_URL = "/api/v1/colony/state"
SCAVENGE_URL = "/api/v1/colony/scavenge"
DISPATCH_URL = "/api/v1/colony/dispatch"
BUILD_URL = "/api/v1/facilities/build"


async def _click(client, times: int = 1) -> dict:
    body: dict = {}
    for _ in range(times):
        response = await client.post(SCAVENGE_URL)
        body = response.json()
        if response.status_code != 200:
            return body
    return body


async def _build(client, facility_id: str, count: int = 1):
    return await client.post(BUILD_URL, json={"facility_id": facility_id, "count": count})


async def _dispatch(client, role: str, delta: int, policy: dict | None = None):
    payload: dict = {"role": role, "delta": delta}
    if policy is not None:
        payload["policy"] = policy
    return await client.post(DISPATCH_URL, json=payload)


async def _rewind(session, seconds: int) -> None:
    """把 last_tick_time 回拨，模拟离线。"""
    await session.rollback()
    colony = await session.get(ColonyState, (1, 0))
    colony.last_tick_time = int(time.time()) - seconds
    await session.commit()


async def _bootstrap(client) -> dict:
    return (await client.get(STATE_URL)).json()["data"]


class TestManualScavenge:
    async def test_five_clicks_afford_first_box(self, client):
        """GDD 阶段一：手点 5 次 = 5 废铁，刚好够第一座纸箱窝。"""
        await _bootstrap(client)
        data = (await _click(client, 5))["data"]
        assert data["scrap"] == 5.0
        assert data["manual_scavenge_clicks"] == 5
        assert data["clicks_left"] == B.COLD_START_MAX_MANUAL_CLICKS - 5
        assert data["hint"] and "瓦楞纸箱窝" in data["hint"]

    async def test_click_cap_is_enforced(self, client):
        await _bootstrap(client)
        await _click(client, B.COLD_START_MAX_MANUAL_CLICKS)
        response = await client.post(SCAVENGE_URL)
        assert response.status_code == 400
        assert response.json()["message"] == "COLD_START_EXHAUSTED"

    async def test_entry_closes_once_scavenger_on_duty(self, client):
        """拾荒猫上工后手点入口关闭（自动化真正接管）。"""
        await _bootstrap(client)
        await _click(client, 29)
        await _build(client, "housing_box")            # 5 → 第一只猫
        await _build(client, "farm_plot")              # 10
        await _build(client, "scavenge_station")       # 8
        assert (await _dispatch(client, "scavenger", 1)).status_code == 200

        response = await client.post(SCAVENGE_URL)
        assert response.status_code == 400
        assert response.json()["message"] == "COLD_START_FINISHED"

    async def test_stays_available_when_no_cat_on_scavenge_duty(self, client):
        """防死档：没有拾荒猫在岗时，手点废墟始终是兜底路径。"""
        await _bootstrap(client)
        await _click(client, 23)
        await _build(client, "housing_box")
        await _build(client, "farm_plot")
        await _build(client, "scavenge_station")  # 有操作台但没猫上工
        assert (await client.post(SCAVENGE_URL)).status_code == 200

    async def test_missing_save_rejected(self, client):
        response = await client.post(f"{SCAVENGE_URL}?slot=3")
        assert response.status_code == 404
        assert response.json()["message"] == "SAVE_NOT_FOUND"


class TestFacilityBuild:
    async def test_first_housing_box_brings_first_cat(self, client):
        """GDD 阶段二：第一座纸箱窝建成 ⇒ 第一只折耳猫入驻 + 解锁工位面板。"""
        await _bootstrap(client)
        await _click(client, 5)
        body = (await _build(client, "housing_box")).json()
        data = body["data"]
        assert data["level"] == 1
        assert data["cost_paid"] == {"scrap": 5.0}
        assert data["resources"]["scrap"] == 0.0
        assert data["total_cats"] == 1
        assert data["unassigned"] == 1
        assert data["cat_capacity"] == 1
        assert data["narrative"] and "折耳流浪猫" in data["narrative"]

    async def test_cost_curve_follows_balance_sheet(self, client):
        """D-1：第 n 座造价 = 基础造价 × 递增系数^(n-1)（纸箱窝 5 → 6 → 7）。"""
        await _bootstrap(client)
        await _click(client, 20)
        first = (await _build(client, "housing_box")).json()["data"]
        second = (await _build(client, "housing_box")).json()["data"]
        third = (await _build(client, "housing_box")).json()["data"]
        assert [first["cost_paid"]["scrap"], second["cost_paid"]["scrap"], third["cost_paid"]["scrap"]] == [5.0, 6.0, 7.0]
        assert third["level"] == 3
        assert third["cat_capacity"] == 3
        assert third["resources"]["scrap"] == pytest.approx(20 - 18, abs=1e-6)

    async def test_multi_count_uses_summed_curve(self, client):
        await _bootstrap(client)
        await _click(client, 20)
        data = (await _build(client, "housing_box", count=2)).json()["data"]
        assert data["cost_paid"] == {"scrap": 11.0}  # 5 + 6
        assert data["level"] == 2

    async def test_insufficient_resource(self, client):
        await _bootstrap(client)
        response = await _build(client, "farm_plot")
        assert response.status_code == 400
        assert response.json()["message"] == "INSUFFICIENT_RESOURCE"
        assert "机械废铁" in response.json()["detail"]

    async def test_farm_plot_grants_workstations(self, client):
        """D-2：水培农田每座 +2 农夫工位。"""
        await _bootstrap(client)
        await _click(client, 30)  # 第一座 10 + 第二座 12 = 22 废铁
        first = (await _build(client, "farm_plot")).json()["data"]
        assert first["workstation_limits"]["farmer"] == 2
        second = (await _build(client, "farm_plot")).json()["data"]
        assert second["workstation_limits"]["farmer"] == 4

    async def test_launch_silo_is_placeholder_only(self, client):
        """数值平衡表 §5 待定项：发射井只入等级 0 占位行，不开放建造入口。"""
        await _bootstrap(client)
        response = await _build(client, "launch_silo")
        assert response.status_code == 400
        assert "尚未定稿" in response.json()["detail"]

    async def test_unknown_facility_rejected(self, client):
        await _bootstrap(client)
        response = await _build(client, "moon_base")
        assert response.status_code == 400
        assert response.json()["message"] == "BAD_REQUEST"

    async def test_unique_building_cannot_exceed_max_level(self, client, session):
        await _bootstrap(client)
        await session.rollback()
        colony = await session.get(ColonyState, (1, 0))
        colony.scrap, colony.chips, colony.alloys = 200.0, 100.0, 50.0
        # 蓄电池属于进阶设施：先解锁对应科技，才能验证"唯一建筑不能超过 1 级"
        from app.models import TechRecord
        from app.models.tech import TechStatus

        record = await session.get(TechRecord, (1, 0, "tech_battery_matrix"))
        record.status = TechStatus.UNLOCKED
        await session.commit()

        first = await _build(client, "battery_bank")
        assert first.status_code == 200
        assert first.json()["data"]["level"] == 1

        again = await _build(client, "battery_bank")
        assert again.status_code == 409
        assert again.json()["message"] == "FACILITY_MAX_LEVEL"

    async def test_build_without_slot_returns_404(self, client):
        response = await client.post(BUILD_URL, json={"slot": 3, "facility_id": "housing_box"})
        assert response.status_code == 404


class TestDispatch:
    async def test_limit_zero_without_facility(self, client):
        """C2/D-2：没有农田就没有农夫工位。"""
        await _bootstrap(client)
        response = await _dispatch(client, "farmer", 1)
        assert response.status_code == 400
        assert response.json()["message"] == "WORKSTATION_LIMIT_EXCEEDED"

    async def test_no_free_cat_is_rejected(self, client):
        """没有空闲猫口时不能上工（哪怕工位还有空）。"""
        await _bootstrap(client)
        await _click(client, 15)
        await _build(client, "housing_box")   # 1 猫
        await _build(client, "farm_plot")     # 2 农夫工位
        assert (await _dispatch(client, "farmer", 1)).status_code == 200
        response = await _dispatch(client, "farmer", 1)
        assert response.status_code == 400
        assert response.json()["message"] == "WORKSTATION_LIMIT_EXCEEDED"
        assert "无空闲猫口" in response.json()["detail"]

    async def test_assign_and_release_updates_idle_pool(self, client):
        await _bootstrap(client)
        await _click(client, 15)
        await _build(client, "housing_box")
        await _build(client, "farm_plot")

        assigned = (await _dispatch(client, "farmer", 1)).json()["data"]
        assert assigned["count"] == 1
        assert assigned["unassigned"] == 0
        assert assigned["workstations"]["farmer"] == 1

        released = (await _dispatch(client, "farmer", -1)).json()["data"]
        assert released["count"] == 0
        assert released["unassigned"] == 1

    async def test_negative_below_zero_rejected(self, client):
        await _bootstrap(client)
        await _click(client, 15)
        await _build(client, "housing_box")
        await _build(client, "farm_plot")
        response = await _dispatch(client, "farmer", -1)
        assert response.status_code == 400
        assert response.json()["message"] == "BAD_REQUEST"

    async def test_unknown_role_and_zero_delta_rejected(self, client):
        await _bootstrap(client)
        assert (await _dispatch(client, "chief_meow", 1)).status_code == 400
        assert (await _dispatch(client, "farmer", 0)).status_code == 400

    async def test_star_jobs_locked_before_upgrade(self, client):
        """星际四大高维职业在母星阶段一律锁定（P2）。"""
        await _bootstrap(client)
        response = await _dispatch(client, "purr_master", 1)
        assert response.status_code == 400
        assert response.json()["message"] == "WORKSTATION_LIMIT_EXCEEDED"

    async def test_hysteresis_policy_is_persisted(self, client, session):
        """C3：迟滞换班策略写入 colony_state.labor_automation_policy。"""
        await _bootstrap(client)
        await _click(client, 15)
        await _build(client, "housing_box")
        await _build(client, "farm_plot")
        policy = {"enabled": True, "upper": 0.8, "lower": 0.2, "shift": 2}
        data = (await _dispatch(client, "farmer", 1, policy=policy)).json()["data"]
        assert data["policy"] == policy

        await session.rollback()
        colony = await session.get(ColonyState, (1, 0))
        assert colony.labor_automation_policy == policy

    async def test_policy_only_update_allows_zero_delta(self, client, session):
        """前端策略面板：delta = 0 + policy ⇒ 只更新策略，工位数不变。"""
        await _bootstrap(client)
        await _click(client, 15)
        await _build(client, "housing_box")
        await _build(client, "farm_plot")
        await _dispatch(client, "farmer", 1)

        policy = {"enabled": False, "upper": 0.9, "lower": 0.1, "shift": 3}
        data = (await _dispatch(client, "farmer", 0, policy=policy)).json()["data"]
        assert data["count"] == 1  # 工位没被改动
        assert data["policy"] == policy

        await session.rollback()
        colony = await session.get(ColonyState, (1, 0))
        assert colony.labor_automation_policy == policy

    async def test_zero_delta_without_policy_still_rejected(self, client):
        await _bootstrap(client)
        response = await _dispatch(client, "farmer", 0)
        assert response.status_code == 400
        assert response.json()["message"] == "BAD_REQUEST"

    async def test_dispatch_recomputes_power_net(self, client, session):
        """踩轮猫上工后净电力立即变化（§7.1：+5 kW/只）。"""
        await _bootstrap(client)
        await _click(client, 40)  # 窝 5 + 滚轮 15 + 滚轮 18 = 38 废铁
        await _build(client, "housing_box")
        await _build(client, "power_wheel", count=2)
        data = (await _dispatch(client, "power_runner", 1)).json()["data"]
        assert data["power_net_kw"] == pytest.approx(5.0)


class TestColdStartLoopEndToEnd:
    async def test_from_hand_clicking_to_full_automation(self, client, session):
        """N1 冷启动心流全跑通：手点 → 造窝引来第一只猫 → 农田/操作台 → 第二只猫 → 自动化自给自足。"""
        state = await _bootstrap(client)
        assert state["population"]["total"] == 0

        # ① 阶段一：手点废墟攒料（GDD：5 次够第一个窝）
        clicked = (await _click(client, 40))["data"]
        assert clicked["scrap"] == 40.0
        assert clicked["clicks_left"] == 0

        # ② 阶段二：第一座纸箱窝 ⇒ 第一只折耳猫
        box = (await _build(client, "housing_box")).json()["data"]
        assert box["total_cats"] == 1 and box["narrative"]

        # ③ 阶段三：农田 + 操作台 + 第二座窝（K=2）
        await _build(client, "farm_plot")
        await _build(client, "scavenge_station")
        second_box = (await _build(client, "housing_box")).json()["data"]
        assert second_box["cat_capacity"] == 2
        assert (await _dispatch(client, "farmer", 1)).status_code == 200

        # ④ 逻辑斯蒂繁育：约 200 秒迎来第 2 只猫
        await _rewind(session, 200)
        state = (await client.get(STATE_URL)).json()["data"]
        assert state["population"]["total"] == 2
        assert state["population"]["unassigned"] == 1

        # ⑤ 指派拾荒猫 ⇒ 手点入口关闭，自动化接管
        assert (await _dispatch(client, "scavenger", 1)).status_code == 200
        assert (await client.post(SCAVENGE_URL)).status_code == 400

        # ⑥ 离线 600 秒：猫薄荷 +0.1/s、废铁 +0.5/s（爆仓裁剪）
        await _rewind(session, 600)
        report = (await client.get(STATE_URL)).json()["data"]["offline_report"]
        assert report["gained_catnip"] == pytest.approx(60.0, abs=2.0)
        assert report["is_capped"] is True
        assert "scrap" in report["overflowed_resources"]

        final = (await client.get(STATE_URL)).json()["data"]
        assert final["resources"]["scrap"] == 200.0
        assert final["resources"]["catnip"] > 80.0

    async def test_facility_rows_are_created_lazily_for_new_facilities(self, client, session):
        """D-3：facilities.json 新增设施后，旧存档缺行也能自动补 0 级行。"""
        await _bootstrap(client)
        await session.rollback()
        row = await session.get(FacilityState, (1, 0, "refinery"))
        await session.delete(row)
        await session.commit()

        state = (await client.get(STATE_URL)).json()["data"]
        assert "refinery" in state["facilities"]
        assert state["facilities"]["refinery"] == 0
