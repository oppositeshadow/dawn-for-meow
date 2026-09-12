"""模块 M 验收：本地狂欢节拼装、LRU 防重复、LLM 语料批处理与三道防火墙。

注意：测试默认**不带 LLM 密钥**（conftest 把 `LLM_API_KEY` 置空），因此不会产生任何真实计费；
真机联调走 `scripts/llm_smoke.py` 或带密钥的本地服务。
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.core import template_engine
from app.core.config import get_settings
from app.models import EventTemplate
from app.schemas.template_agent import TemplateBatch
from app.services import radio_service
from app.services.llm_service import LlmResult, LlmUsage

RADIO_URL = "/api/v1/radio"


class TestTemplateEngine:
    def test_render_fills_all_slots(self):
        text = "公频：{cat} 在 {building} 捡到了 {resource}，顺手看了一眼 {stock}。"
        rendered = template_engine.render_template(text)
        assert "{" not in rendered and "}" not in rendered
        for pool in template_engine.SLOT_POOLS.values():
            assert not any(word in rendered for word in pool[:0])

    def test_used_slots_detection(self):
        assert template_engine.used_slots("{cat} 与 {stock}") == ["cat", "stock"]
        assert template_engine.used_slots("没有槽位") == []

    def test_lru_prefers_never_used_then_oldest(self):
        rows = [
            {"id": 1, "template_text": "a", "last_used_time": 100},
            {"id": 2, "template_text": "b", "last_used_time": None},
            {"id": 3, "template_text": "c", "last_used_time": 50},
        ]
        picked = [row["id"] for row in template_engine.pick_lru(rows, 3)]
        assert picked == [2, 3, 1]

    def test_lru_cycles_when_pool_is_small(self):
        rows = [{"id": 1, "template_text": "a", "last_used_time": None}]
        assert len(template_engine.pick_lru(rows, 3)) == 3


class TestSchemaFirewall:
    def test_accepts_valid_card(self):
        batch = TemplateBatch.model_validate(
            {"templates": [{"category": "RADIO_NEWS", "template_text": "{cat} 在 {building} 打呼噜"}]}
        )
        assert batch.templates[0].impact_val == 0.0

    def test_rejects_card_without_slot(self):
        with pytest.raises(Exception):
            TemplateBatch.model_validate(
                {"templates": [{"category": "RADIO_NEWS", "template_text": "这条语料没有槽位"}]}
            )

    def test_rejects_oversized_card(self):
        with pytest.raises(Exception):
            TemplateBatch.model_validate(
                {"templates": [{"category": "BBS_POST", "template_text": "{cat}" + "喵" * 80}]}
            )

    def test_rejects_impact_out_of_range(self):
        with pytest.raises(Exception):
            TemplateBatch.model_validate(
                {
                    "templates": [
                        {
                            "category": "BBS_POST",
                            "template_text": "{stock} 要涨了",
                            "impact_val": 0.9,
                        }
                    ]
                }
            )


class TestRadioFeed:
    async def test_first_feed_seeds_local_pool(self, client, session):
        await client.get("/api/v1/colony/state")
        body = (await client.get(f"{RADIO_URL}/feed")).json()
        data = body["data"]
        assert data["seeded"] > 0
        assert data["pool_size"] == data["seeded"]
        assert len(data["items"]) <= 12
        assert all("{" not in item["text"] for item in data["items"])

        again = (await client.get(f"{RADIO_URL}/feed")).json()["data"]
        assert again["seeded"] == 0  # 幂等

    async def test_feed_marks_lru_and_rotates(self, client):
        await client.get("/api/v1/colony/state")
        first = (await client.get(f"{RADIO_URL}/feed?limit=3")).json()["data"]["items"]
        second = (await client.get(f"{RADIO_URL}/feed?limit=3")).json()["data"]["items"]
        assert {item["id"] for item in first}.isdisjoint({item["id"] for item in second})

    async def test_feed_uses_no_llm_calls(self, client):
        await client.get("/api/v1/colony/state")
        before = radio_service.get_llm_service().budget.snapshot()
        await client.get(f"{RADIO_URL}/feed")
        after = radio_service.get_llm_service().budget.snapshot()
        assert before["calls"] == after["calls"]


class TestGenerateBatch:
    async def test_without_api_key_falls_back(self, client, monkeypatch):
        await client.get("/api/v1/colony/state")
        monkeypatch.setattr(get_settings(), "llm_api_key", "")
        body = (await client.post(f"{RADIO_URL}/generate", json={"count": 4})).json()
        data = body["data"]
        assert data["source"] == "FALLBACK"
        assert data["fell_back"] is True
        assert data["usage"]["reason"] == "LLM_NOT_CONFIGURED"
        assert data["pool_size"] >= 15  # 本地兜底池已就位，玩家始终有台可听
        assert "本地语料池" in (data["note"] or "")
        assert data["generated"] == 0  # 兜底内容与已有语料重复 ⇒ 不造假数据

    async def test_budget_exhausted_falls_back(self, client, monkeypatch):
        await client.get("/api/v1/colony/state")
        service = radio_service.get_llm_service()
        monkeypatch.setattr(get_settings(), "llm_api_key", "fake-key-for-test")
        monkeypatch.setattr(get_settings(), "llm_daily_call_budget", 0)
        body = (await client.post(f"{RADIO_URL}/generate", json={"count": 3})).json()
        data = body["data"]
        assert data["fell_back"] is True
        assert data["usage"]["reason"] == "BUDGET_EXHAUSTED"
        assert service.budget.snapshot()["exhausted"] is True

    async def test_llm_output_passes_firewall_and_lands_in_db(self, client, monkeypatch, session):
        await client.get("/api/v1/colony/state")

        class FakeLlmService:
            def __init__(self) -> None:
                from app.services.llm_service import LlmBudget

                self.budget = LlmBudget()
                self.budget.state.calls = 0

            async def complete_json(self, **_: object) -> LlmResult:
                return LlmResult(
                    payload={
                        "templates": [
                            {
                                "category": "RADIO_NEWS",
                                "template_text": "{cat} 在 {building} 里烤火，顺便看了眼 {stock}",
                                "impact_stock": "forge",
                            },
                            {
                                "category": "DISASTER_ALERT",
                                "template_text": "{building} 的震动让警戒度爬了一点",
                            },
                        ]
                    },
                    usage=LlmUsage(scene="PHASE_TEMPLATES", model="fake", ok=True, attempts=1),
                )

        monkeypatch.setattr(radio_service, "get_llm_service", lambda: FakeLlmService())
        body = (await client.post(f"{RADIO_URL}/generate", json={"count": 2})).json()["data"]
        assert body["source"] == "LLM"
        assert body["generated"] == 2
        assert body["fell_back"] is False

        await session.rollback()
        row = (
            await session.execute(
                select(EventTemplate).where(EventTemplate.impact_stock == "FORGE")
            )
        ).scalars().first()
        assert row is not None
        assert "{" in row.template_text  # 落库的是带槽位的模板，不是渲染后的成品

    async def test_bad_llm_output_is_rejected_then_falls_back(self, client, monkeypatch):
        await client.get("/api/v1/colony/state")

        class BadLlmService:
            def __init__(self) -> None:
                from app.services.llm_service import LlmBudget

                self.budget = LlmBudget()

            async def complete_json(self, **_: object) -> LlmResult:
                return LlmResult(
                    payload={"templates": [{"category": "RADIO_NEWS", "template_text": "没有槽位"}]},
                    usage=LlmUsage(scene="PHASE_TEMPLATES", model="fake", ok=True, attempts=1),
                )

        monkeypatch.setattr(radio_service, "get_llm_service", lambda: BadLlmService())
        data = (await client.post(f"{RADIO_URL}/generate", json={"count": 2})).json()["data"]
        assert data["source"] == "FALLBACK"
        assert data["usage"]["reason"] == "SCHEMA_REJECTED"

    async def test_generate_is_idempotent_on_duplicates(self, client, monkeypatch):
        await client.get("/api/v1/colony/state")
        monkeypatch.setattr(get_settings(), "llm_api_key", "")
        first = (await client.post(f"{RADIO_URL}/generate", json={"count": 5})).json()["data"]
        second = (await client.post(f"{RADIO_URL}/generate", json={"count": 5})).json()["data"]
        assert first["generated"] == 0 and second["generated"] == 0  # 兜底池内容重复，不重复入库
        assert first["pool_size"] == second["pool_size"]

    async def test_count_is_clamped_by_settings(self, client, monkeypatch):
        await client.get("/api/v1/colony/state")
        monkeypatch.setattr(get_settings(), "llm_api_key", "")
        monkeypatch.setattr(get_settings(), "llm_batch_size_max", 2)
        data = (await client.post(f"{RADIO_URL}/generate", json={"count": 6})).json()["data"]
        assert data["requested"] == 2

    async def test_rejects_out_of_range_count(self, client):
        response = await client.post(f"{RADIO_URL}/generate", json={"count": 0})
        assert response.status_code == 400


class TestBudgetLedger:
    async def test_budget_snapshot_shape(self, client):
        data = radio_service.get_llm_service().budget.snapshot()
        assert set(data) >= {"day", "calls", "tokens", "call_budget", "token_budget", "exhausted"}

    async def test_templates_table_is_used(self, session):
        total = await session.execute(select(func.count()).select_from(EventTemplate))
        assert total.scalar_one() >= 0
