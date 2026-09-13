"""模块 H2 验收：异星植物学图鉴论文（LLM 场景 5 + 本地模板文案 + 幂等）。"""

from __future__ import annotations

from app.core.garden_engine import STAGE_MATURE
from app.models import ColonyState, GardenState
from app.services import plant_codex_service
from app.services.llm_service import LlmResult, LlmUsage

GARDEN_URL = "/api/v1/garden"
NEW_PLANT = "glow_moss"  # 荧光苔藓：开局图鉴里没有，靠突变/机械臂收录


async def _bootstrap(client) -> None:
    await client.get("/api/v1/colony/state")
    await client.get(f"{GARDEN_URL}/state")


async def _place_mature(session, seed_id: str) -> None:
    """把中央格直接改成成熟株，省掉 6 分钟生长（测试只关心收录那一刻）。"""
    await session.rollback()
    garden = await session.get(GardenState, (1, 0))
    grid = [dict(tile) for tile in (garden.grid_data or [])]
    for tile in grid:
        if tile.get("x") == 3 and tile.get("y") == 3:
            tile.update({"seed_id": seed_id, "stage": STAGE_MATURE, "age": 200.0, "mutation_progress": 0.0})
    garden.grid_data = grid
    await session.commit()


async def _harvest(client) -> dict:
    resp = await client.post(f"{GARDEN_URL}/action", json={"action": "HARVEST", "x": 3, "y": 3})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def test_new_codex_entry_lands_fallback_paper(client, session) -> None:
    await _bootstrap(client)
    await _place_mature(session, NEW_PLANT)
    data = await _harvest(client)
    assert data["new_codex_entry"] is True
    paper = data["codex_paper"]
    assert paper["source"] == "FALLBACK"  # 测试期无密钥 → 本地模板文案
    assert "荧光苔藓" in paper["title"]
    assert len(paper["body"]) >= 20

    state = (await client.get(f"{GARDEN_URL}/state")).json()["data"]
    assert NEW_PLANT in state["codex"]
    assert state["codex_papers"][NEW_PLANT]["title"] == paper["title"]
    assert state["codex_papers"][NEW_PLANT]["source"] == "FALLBACK"


async def test_known_plant_gets_no_duplicate_paper(client, session) -> None:
    await _bootstrap(client)
    # 普通猫薄荷开局就在图鉴里 ⇒ 采摘不触发论文
    await _place_mature(session, "ordinary_moss")
    data = await _harvest(client)
    assert data["new_codex_entry"] is False
    assert data["codex_paper"] is None
    state = (await client.get(f"{GARDEN_URL}/state")).json()["data"]
    assert "ordinary_moss" not in state["codex_papers"]


async def test_paper_is_written_once_per_plant(client, session) -> None:
    await _bootstrap(client)
    await _place_mature(session, NEW_PLANT)
    first = (await _harvest(client))["codex_paper"]

    await _place_mature(session, NEW_PLANT)
    second = await _harvest(client)
    # 第二次采摘已知母本 ⇒ 不再是新收录，也就不再生成论文
    assert second["new_codex_entry"] is False
    assert second["codex_paper"] is None

    await session.rollback()
    garden = await session.get(GardenState, (1, 0), populate_existing=True)
    assert garden.codex_papers[NEW_PLANT]["title"] == first["title"]
    assert garden.codex_papers[NEW_PLANT]["at"] == first["at"]


async def test_llm_paper_passes_firewall(client, session, monkeypatch) -> None:
    await _bootstrap(client)

    class FakeLlmService:
        configured = True

        def __init__(self) -> None:
            from app.services.llm_service import LlmBudget

            self.budget = LlmBudget()

        async def complete_json(self, **_: object) -> LlmResult:
            return LlmResult(
                payload={
                    "title": "荧光苔藓的夜光节律",
                    "body": "第 14 号样本在断电后仍持续发光 6 小时，推测其光周期与避难所电网负载反向耦合。",
                },
                usage=LlmUsage(scene="PLANT_CODEX", model="fake", ok=True, attempts=1),
            )

    monkeypatch.setattr(plant_codex_service, "get_llm_service", lambda: FakeLlmService())
    await _place_mature(session, NEW_PLANT)
    paper = (await _harvest(client))["codex_paper"]
    assert paper["source"] == "LLM"
    assert paper["title"] == "荧光苔藓的夜光节律"
    assert paper["usage"]["scene"] == "PLANT_CODEX"


async def test_bad_llm_paper_falls_back(client, session, monkeypatch) -> None:
    await _bootstrap(client)

    class BadLlmService:
        configured = True

        def __init__(self) -> None:
            from app.services.llm_service import LlmBudget

            self.budget = LlmBudget()

        async def complete_json(self, **_: object) -> LlmResult:
            return LlmResult(
                payload={"title": "短", "body": "太短"},  # title < 4 字、body < 20 字 ⇒ 防火墙拦截
                usage=LlmUsage(scene="PLANT_CODEX", model="fake", ok=True, attempts=1),
            )

    monkeypatch.setattr(plant_codex_service, "get_llm_service", lambda: BadLlmService())
    await _place_mature(session, NEW_PLANT)
    paper = (await _harvest(client))["codex_paper"]
    assert paper["source"] == "FALLBACK"
    assert "荧光苔藓" in paper["title"]


async def test_mechanical_arm_paper_skips_llm_for_offline_path(client, session, monkeypatch) -> None:
    """离线结算里的机械臂补写必须走本地模板：离线补算绝不等网络。"""
    await _bootstrap(client)

    class ExplodingLlmService:
        configured = True

        def __init__(self) -> None:
            from app.services.llm_service import LlmBudget

            self.budget = LlmBudget()

        async def complete_json(self, **_: object) -> LlmResult:  # pragma: no cover - 不应被调用
            raise AssertionError("离线路径不应调用 LLM")

    monkeypatch.setattr(plant_codex_service, "get_llm_service", lambda: ExplodingLlmService())
    await session.rollback()
    garden = await session.get(GardenState, (1, 0))
    grid = [dict(tile) for tile in (garden.grid_data or [])]
    for tile in grid:
        if tile.get("x") == 3 and tile.get("y") == 3:
            tile.update({"seed_id": NEW_PLANT, "stage": STAGE_MATURE, "age": 200.0, "mutation_progress": 0.0})
    garden.grid_data = grid
    garden.mechanical_arm_enabled = True
    garden.auto_protect_unknown = False
    # 离线推进的 Δt 取自 colony_state：回拨 30 秒，让机械臂本班次有活干
    colony = await session.get(ColonyState, (1, 0))
    colony.last_tick_time = int(colony.last_tick_time) - 30
    await session.commit()

    await client.get("/api/v1/colony/state")  # 触发离线结算（含机械臂自动收割）
    state = (await client.get(f"{GARDEN_URL}/state")).json()["data"]
    assert NEW_PLANT in state["codex"]
    assert state["codex_papers"][NEW_PLANT]["source"] == "FALLBACK"
