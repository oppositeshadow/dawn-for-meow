"""基地状态、工位调度与快照对账的请求/响应契约（代码结构稿 §4.3）。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SnapshotRequest(BaseModel):
    """`POST /colony/snapshot` 请求体：前端 Pinia 状态快照（含前台计算校验值）。

    * 携带前端预测值只用于**对账告警**；后端一律用 `last_tick_time` 自行重算；
    * 允许携带额外字段（`extra="allow"`），便于前端渐进式扩展而不用改接口。
    """

    model_config = ConfigDict(extra="allow")

    slot: int = Field(default=1, ge=1, le=3, description="存档槽位 1~3")
    planet_id: int | None = Field(default=None, description="星球 ID，缺省取存档的活跃星球")
    client_time: float | None = Field(default=None, description="前端本地时间戳（仅用于诊断）")
    resources: dict[str, float] = Field(default_factory=dict, description="前端预测的资源存量")
    population: dict[str, float] = Field(default_factory=dict, description="前端预测的猫口与繁育进度")
    workstations: dict[str, int] = Field(default_factory=dict, description="前端预测的工位分桶")
    facilities: dict[str, int] = Field(default_factory=dict, description="前端预测的设施等级")


class ResourcesBlock(BaseModel):
    catnip: float = 0.0
    scrap: float = 0.0
    chips: float = 0.0
    alloys: float = 0.0
    battery: float = 0.0
    lube: float = 0.0
    caps: dict[str, float] = Field(default_factory=dict)


class PowerBlock(BaseModel):
    gen_kw: float = 0.0
    load_kw: float = 0.0
    net_kw: float = 0.0
    battery_kwh: float = 0.0
    battery_kwh_max: float = 0.0
    blackout: bool = False


class PopulationBlock(BaseModel):
    total: int = 0
    max_cap: int = 0
    unassigned: int = 0
    birth_progress: float = 0.0


class SuspicionBlock(BaseModel):
    current: float = 0.0
    max: float = 100.0


class SecurityBlock(BaseModel):
    """三级安防预案状态（模块 F）。"""

    decoy_count: int = 0
    cooldown_until: int | None = None
    cooldown_left_seconds: int = 0
    go_dark: bool = False
    policy: dict[str, bool] = Field(default_factory=dict)


class LaunchSiloStage(BaseModel):
    stage: int
    name: str
    cost: dict[str, float] = Field(default_factory=dict)
    done: bool = False
    blocked: bool = False


class LaunchSiloBlock(BaseModel):
    """火箭垂直发射井（模块 K）阶段视图。"""

    level: int = 0
    max_level: int = 4
    stages: list[LaunchSiloStage] = Field(default_factory=list)
    next_stage: LaunchSiloStage | None = None
    can_advance: bool = True
    fortress_down: bool = False
    launched: bool = False


class OfflineReport(BaseModel):
    """《离线休整报表》（A-1 ~ A-6 的可观测字段）。"""

    elapsed_seconds: float = 0.0
    applied_seconds: float = 0.0
    gained_catnip: float = 0.0
    gained_scrap: float = 0.0
    gained_cats: int = 0
    gained_research: float = 0.0
    is_starved: bool = False
    starve_duration_seconds: float = 0.0
    is_capped: bool = False
    overflowed_resources: list[str] = Field(default_factory=list)
    suspicion_delta: float = 0.0
    charged_kwh: float = 0.0
    clock_anomaly: bool = False
    birth_progress: float = 0.0
    notes: list[str] = Field(default_factory=list)


class ColonyStateData(BaseModel):
    slot_id: int
    planet_id: int
    last_tick_time: int
    saved_at: int
    resources: ResourcesBlock
    power: PowerBlock
    population: PopulationBlock
    workstations: dict[str, int]
    workstation_limits: dict[str, int]
    facilities: dict[str, int]
    suspicion: SuspicionBlock
    security: SecurityBlock = SecurityBlock()
    launch_silo: LaunchSiloBlock = LaunchSiloBlock()
    offline_report: OfflineReport


class SnapshotResponse(BaseModel):
    """`POST /colony/snapshot` 响应（与定稿契约一致，不做多余包装）。"""

    code: int = 200
    message: str = "SNAPSHOT_PERSISTED"
    saved_at: int


class ColonyStateEnvelope(BaseModel):
    """`GET /colony/state` 的 `{code, data}` 封装。"""

    code: int = 200
    data: ColonyStateData


# ----------------------------------------------------------------------
# 冷启动 / 工位调度 / 设施建造（Milestone 1 阶段 3 服务端）
# ----------------------------------------------------------------------
class ScavengeResult(BaseModel):
    """手点废墟（冷启动专属动作）结果。"""

    scrap: float
    scrap_max: float
    manual_scavenge_clicks: int
    clicks_left: int
    cold_start_finished: bool = False
    hint: str | None = None


class ScavengeEnvelope(BaseModel):
    code: int = 200
    data: ScavengeResult


class DispatchPolicy(BaseModel):
    """迟滞换班策略（WBS C3，防高频震荡）。"""

    enabled: bool = True
    upper: float = Field(default=0.8, ge=0.0, le=1.0, description="高位转出线（猫薄荷占比）")
    lower: float = Field(default=0.2, ge=0.0, le=1.0, description="低位回防线（猫薄荷占比）")
    shift: int = Field(default=2, ge=1, description="一次转移猫口数")


class DispatchRequest(BaseModel):
    """`POST /colony/dispatch`：调配工位分桶并写入迟滞换班策略。"""

    model_config = ConfigDict(extra="allow")

    slot: int = Field(default=1, ge=1, le=3)
    planet_id: int | None = None
    role: str = Field(description="工种 ID（job_id）：farmer / scavenger / geek / power_runner / crew")
    delta: int = Field(description="工位增减量，正数上工、负数下岗")
    policy: DispatchPolicy | None = Field(default=None, description="可选的迟滞换班策略")


class DispatchResult(BaseModel):
    role: str
    count: int
    unassigned: int
    total_cats: int
    workstations: dict[str, int]
    workstation_limits: dict[str, int]
    power_net_kw: float
    policy: dict | None = None


class DispatchEnvelope(BaseModel):
    code: int = 200
    data: DispatchResult


class BuildRequest(BaseModel):
    """`POST /facilities/build`：建造 / 升级设施。"""

    model_config = ConfigDict(extra="allow")

    slot: int = Field(default=1, ge=1, le=3)
    planet_id: int | None = None
    facility_id: str
    count: int = Field(default=1, ge=1, le=100)


class BuildResult(BaseModel):
    facility_id: str
    level: int
    count: int
    cost_paid: dict[str, float]
    resources: dict[str, float]
    caps: dict[str, float]
    total_cats: int
    unassigned: int
    cat_capacity: int
    workstation_limits: dict[str, int]
    power_net_kw: float
    narrative: str | None = None
    unlock_hint: dict | None = None
    unlocked_planets: list[str] = Field(default_factory=list)


class BuildEnvelope(BaseModel):
    code: int = 200
    data: BuildResult
