"""政令系统验收（《数值平衡表》§15.2）：清单、点亮、扣凝聚力、重复与不足的处理。"""

from __future__ import annotations

from app.models import SaveSlot
from app.services import doctrine_service

DOCTRINE_URL = "/api/v1/doctrine"


async def _boot(client) -> None:
    await client.get("/api/v1/colony/state", params={"slot": 1})


def test_seed_matches_balance_table() -> None:
    """种子里的 8 条政令与 §15.2 的消耗一一对应（改数值先改表）。"""
    defs = doctrine_service.doctrine_defs()
    assert len(defs) == 8
    costs = {item["name"]: item["cost"] for item in defs}
    assert costs["下午三点晒太阳协议"] == 200
    assert costs["全星系红点防空标准"] == 300
    assert costs["永久呼噜场"] == 800
    # 白名单 = 真正接进结算的键；没接入点的（防空）单独列在 PENDING 里，绝不混进白名单
    assert set(doctrine_service.WIRED_EFFECTS) == {
        "production_multiplier",
        "cat_capacity",
        "fleet_armor",
        "suspicion_growth",
        "morale",
        "black_market_fee",
        "intel_speed",
    }
    assert doctrine_service.PENDING_EFFECTS == ("air_defense",)
    # 8 条政令的效果键必须全部落在"已接线 ∪ 无接入点"里，不能有漏网的
    keys = {key for item in defs for key in item["effects"]}
    assert keys <= set(doctrine_service.WIRED_EFFECTS) | set(doctrine_service.PENDING_EFFECTS)


async def test_list_shows_unity_and_affordability(client, session) -> None:
    await _boot(client)
    data = (await client.get(f"{DOCTRINE_URL}/list", params={"slot": 1})).json()["data"]
    assert data["total"] == 8 and data["unlocked_count"] == 0
    assert data["unity"] == 0.0
    assert all(item["affordable"] is False for item in data["doctrines"])


async def test_unlock_deducts_unity(client, session) -> None:
    await _boot(client)
    await session.rollback()
    save = await session.get(SaveSlot, 1)
    save.unity = 500.0
    await session.commit()

    body = (await client.post(
        f"{DOCTRINE_URL}/unlock", json={"slot": 1, "doctrine_id": "sunbath_3pm"}
    )).json()
    assert body["code"] == 200
    data = body["data"]
    assert data["cost"] == 200 and data["unity_left"] == 300.0
    assert data["wired"] == {"production_multiplier": 0.2}
    assert data["pending"] == {}

    await session.rollback()
    save = await session.get(SaveSlot, 1, populate_existing=True)
    assert save.doctrines == {"sunbath_3pm": 1}
    assert save.unity == 300.0


async def test_unlock_rejects_duplicate_and_insufficient(client, session) -> None:
    await _boot(client)
    await session.rollback()
    save = await session.get(SaveSlot, 1)
    save.unity = 250.0
    await session.commit()

    first = await client.post(
        f"{DOCTRINE_URL}/unlock", json={"slot": 1, "doctrine_id": "sunbath_3pm"}
    )
    assert first.status_code == 200
    again = await client.post(
        f"{DOCTRINE_URL}/unlock", json={"slot": 1, "doctrine_id": "sunbath_3pm"}
    )
    assert again.status_code == 400
    assert again.json()["message"] == "DOCTRINE_ALREADY_UNLOCKED"

    poor = await client.post(
        f"{DOCTRINE_URL}/unlock", json={"slot": 1, "doctrine_id": "permanent_purr_field"}
    )
    assert poor.status_code == 400
    assert poor.json()["message"] == "INSUFFICIENT_RESOURCE"

    unknown = await client.post(
        f"{DOCTRINE_URL}/unlock", json={"slot": 1, "doctrine_id": "free_lunch"}
    )
    assert unknown.status_code == 400


async def test_active_effects_sums_only_wired_keys(client, session) -> None:
    await _boot(client)
    await session.rollback()
    save = await session.get(SaveSlot, 1)
    save.unity = 1000.0
    await session.commit()
    for doctrine_id in ("sunbath_3pm", "planet_greening_act", "red_dot_air_defense"):
        response = await client.post(
            f"{DOCTRINE_URL}/unlock", json={"slot": 1, "doctrine_id": doctrine_id}
        )
        assert response.status_code == 200, f"{doctrine_id}: {response.text}"

    await session.rollback()  # 清掉测试会话里的旧快照，读到接口刚写入的政令
    listed = (await client.get(f"{DOCTRINE_URL}/list", params={"slot": 1})).json()["data"]
    assert listed["unlocked_count"] == 3, listed
    effects = await doctrine_service.active_effects(session, 1)
    assert effects["production_multiplier"] == 0.2
    assert effects["cat_capacity"] == 0.1
    assert effects["fleet_armor"] == 0.0
    assert "air_defense" not in effects  # 未接线的键不参与结算
