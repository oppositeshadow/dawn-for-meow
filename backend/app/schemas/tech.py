"""科技树接口契约（模块 E）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TechResearchRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    slot: int = Field(default=1, ge=1, le=3)
    planet_id: int = Field(default=0, ge=0, le=3)
    tech_id: str = Field(min_length=1, max_length=64)


class TechRerollRequest(TechResearchRequest):
    pass


class TechNode(BaseModel):
    tech_id: str
    tech_name: str
    tier: int
    node_order: int
    parent_ids: list[str] = Field(default_factory=list)
    missing_parents: list[str] = Field(default_factory=list)
    status: str
    current_progress: float
    target_cost: float
    display_cost: float
    discount: float
    flavor_text: str | None = None
    mechanic_type: str | None = None
    buff_payload: dict[str, Any] | None = None
    active_effects: dict[str, Any] = Field(default_factory=dict)
    pending_effects: dict[str, Any] = Field(default_factory=dict)
    is_agent_generated: bool = False
    available: bool = False


class TechTreeData(BaseModel):
    slot_id: int
    planet_id: int
    research_tier_level: int
    unlocked_count: int
    total_nodes: int
    tier_sizes: dict[str, int] = Field(default_factory=dict)
    total_cost: float
    research_points_per_sec: float
    researching: dict[str, Any] | None = None
    nodes: list[TechNode] = Field(default_factory=list)


class TechTreeEnvelope(BaseModel):
    code: int = 200
    data: TechTreeData


class TechActionEnvelope(BaseModel):
    """`/tech/research` 与 `/tech/reroll` 的响应（结构随动作不同，用宽松封装）。"""

    code: int = 200
    data: dict[str, Any]
