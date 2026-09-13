"""模块 N3 验收：生涯统计大盘与成就徽章（只读聚合 + 点亮幂等）。"""

from __future__ import annotations

from app.core import balance as B
from app.models import Achievement, BossState, CareerStats, MinigameState

STATS_URL = "/api/v1/stats"


async def _bootstrap(client) -> dict:
    return (await client.get("/api/v1/colony/state")).json()["data"]


async def _set_career(session, **fields) -> None:
    await session.rollback()
    career = await session.get(CareerStats, 1)
    for key, value in fields.items():
        setattr(career, key, value)
    await session.commit()


async def _badge(client, badge_id: str) -> dict:
    data = (await client.get(f"{STATS_URL}/achievements")).json()["data"]
    return next(item for item in data["achievements"] if item["achievement_id"] == badge_id)


async def _upsert_minigame(
    session, planet_id: int, minigame_id: str, *, state: dict, best_score: float
) -> None:
    """小游戏行可能已被初始化流程建好，这里按主键更新或补建。"""
    row = await session.get(
        MinigameState, (1, planet_id, minigame_id), populate_existing=True
    )
    if row is None:
        row = MinigameState(
            slot_id=1,
            planet_id=planet_id,
            minigame_id=minigame_id,
            state=state,
            last_tick_time=0,
            best_score=best_score,
            play_count=1,
        )
        session.add(row)
        return
    row.state = dict(state)
    row.best_score = best_score


async def test_career_panel_baseline(client) -> None:
    await _bootstrap(client)
    resp = await client.get(f"{STATS_URL}/career")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200
    data = body["data"]
    assert data["achievements_total"] == len(B.ACHIEVEMENTS)
    # 万一失败，直接把"谁被点亮了"打出来（曾出现过一次无法复现的 1 != 0）
    badges = (await client.get(f"{STATS_URL}/achievements")).json()["data"]["achievements"]
    unlocked = [item["achievement_id"] for item in badges if item["unlocked"]]
    assert data["achievements_unlocked"] == 0, f"新开局不该有已点亮徽章，实际：{unlocked}"
    assert data["completed"] is False
    assert data["playtime_seconds"] == 0
    assert data["playtime_hours"] == 0.0
    assert data["total_scrap"] == 0
    assert data["best_short_profit"] == 0


async def test_achievement_catalog_matches_balance(client) -> None:
    await _bootstrap(client)
    data = (await client.get(f"{STATS_URL}/achievements")).json()["data"]
    ids = [item["achievement_id"] for item in data["achievements"]]
    assert ids == [spec["achievement_id"] for spec in B.ACHIEVEMENTS]
    assert data["total"] == len(B.ACHIEVEMENTS)
    assert data["unlocked_count"] == 0
    assert data["newly_unlocked"] == []
    # 全 0 进度不应误点亮任何徽章
    assert all(item["unlocked"] is False for item in data["achievements"])


async def test_partial_progress_does_not_unlock(client, session) -> None:
    await _bootstrap(client)
    await _set_career(session, total_scrap=2500.0)
    badge = await _badge(client, "scrap_king")
    assert badge["progress"] == 2500.0
    assert badge["percent"] == 50.0
    assert badge["unlocked"] is False
    assert badge["unlocked_at"] is None
    assert badge["newly_unlocked"] is False


async def test_progress_written_to_table(client, session) -> None:
    await _bootstrap(client)
    await _set_career(session, total_catnip=1200.0)
    await client.get(f"{STATS_URL}/achievements")
    await session.rollback()
    row = await session.get(Achievement, (1, "salt_mine"), populate_existing=True)
    assert row is not None
    assert row.progress == 1200.0
    assert row.unlocked_at is None


async def test_unlock_fires_only_once(client, session) -> None:
    await _bootstrap(client)
    await _set_career(session, total_scrap=5000.0)

    first = (await client.get(f"{STATS_URL}/achievements")).json()["data"]
    assert "scrap_king" in first["newly_unlocked"]
    badge = next(i for i in first["achievements"] if i["achievement_id"] == "scrap_king")
    assert badge["unlocked"] is True
    assert badge["newly_unlocked"] is True
    assert badge["unlocked_at"] is not None
    unlocked_at = badge["unlocked_at"]

    second = (await client.get(f"{STATS_URL}/achievements")).json()["data"]
    assert second["newly_unlocked"] == []
    badge2 = next(i for i in second["achievements"] if i["achievement_id"] == "scrap_king")
    assert badge2["unlocked"] is True
    assert badge2["newly_unlocked"] is False
    assert badge2["unlocked_at"] == unlocked_at


async def test_unlock_never_reverts(client, session) -> None:
    await _bootstrap(client)
    await _set_career(session, total_scrap=5000.0)
    await client.get(f"{STATS_URL}/achievements")

    # 数值回落（例如赛季重置/资源消耗口径变化）：已点亮徽章不回退
    await _set_career(session, total_scrap=10.0)
    badge = await _badge(client, "scrap_king")
    assert badge["unlocked"] is True
    assert badge["newly_unlocked"] is False
    assert badge["progress"] == 10.0


async def test_minigame_badges(client, session) -> None:
    await _bootstrap(client)
    await session.rollback()
    await _upsert_minigame(session, 2, "cipher_decode", state={"best_attempts": 1}, best_score=0.0)
    await _upsert_minigame(session, 3, "vein_scan", state={}, best_score=12.0)
    await session.commit()

    assert (await _badge(client, "codebreaker"))["unlocked"] is True
    assert (await _badge(client, "prospector"))["unlocked"] is True

    # 多次尝试才破开不计入「一次破开」徽章
    await session.rollback()
    await _upsert_minigame(session, 2, "cipher_decode", state={"best_attempts": 4}, best_score=0.0)
    await session.commit()
    assert (await _badge(client, "codebreaker"))["unlocked"] is True  # 曾经点亮就不回退


async def test_completion_badge_and_career_flag(client, session) -> None:
    await _bootstrap(client)
    await session.rollback()
    boss = await session.get(BossState, 1)
    state = dict(boss.bombardment_state or {})
    state["override_key_used_at"] = 1_700_000_000
    boss.bombardment_state = state
    await session.commit()

    badge = await _badge(client, "omega_slayer")
    assert badge["unlocked"] is True
    assert badge["newly_unlocked"] is True

    career = (await client.get(f"{STATS_URL}/career")).json()["data"]
    assert career["completed"] is True
    assert career["achievements_unlocked"] >= 1


async def test_slot_isolation(client, session) -> None:
    await _bootstrap(client)
    await _set_career(session, total_scrap=5000.0)
    assert (await _badge(client, "scrap_king"))["unlocked"] is True

    # 槽位 2 还没开局：接口应诚实给出空盘而不是串档
    other = (await client.get(f"{STATS_URL}/achievements", params={"slot": 2})).json()["data"]
    assert other["unlocked_count"] == 0
    assert all(item["unlocked"] is False for item in other["achievements"])
