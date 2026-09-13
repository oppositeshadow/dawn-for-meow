"""《喵星破晓》全部游戏数值常量的唯一代码出处。

权威文档：`数值平衡表.md`（文档冲突时以文档为准，并回头同步本文件与 JSON 种子）。
约定：
* 一切速率单位 = **每秒（/s）**；
* 一切时间戳 = **绝对秒级 BIGINT**（禁止"剩余多少秒"式的相对值）；
* 本文件只放"数值与纯公式"，玩法定义（名称、风味、解锁条件）放 `backend/static/*.json`，
  `tests/test_balance_seed_consistency.py` 会逐条比对两边，防止漂移。
"""

from __future__ import annotations

import math
from collections.abc import Mapping

# ======================================================================
# 存档全局
# ======================================================================
SAVE_VERSION = 1
SLOT_IDS = (1, 2, 3)
DEFAULT_SLOT_ID = 1

#: 星球 ID（数据库设计定稿 §2.2）
PLANETS: dict[int, str] = {
    0: "翠绿母星·07 号避难所",
    1: "二号星·极热熔岩铸造星",
    2: "三号星·极寒气态冰卫星",
    3: "四号星·巨型小行星带矿区",
}
HOME_PLANET_ID = 0

# ======================================================================
# §3 资源与生产速率
# ======================================================================
# §3.1 四大工种基础产出
FARMER_CATNIP_PER_SEC = 0.2
SCAVENGER_SCRAP_PER_SEC = 0.5
#: 拾荒猫拆解废旧家电的**芯片产出**（GDD 科技 3「拆解废弃微波炉/电脑主板有概率获得芯片」的
#: 确定性落地口径：把"概率获得"折算成每秒期望值 0.01 芯片 —— 1 只拾荒猫约 8.3 分钟凑够 5 芯片，
#: 正好够造第一台图灵终端）。保留离线引擎的确定性，不做真随机。
SCAVENGER_CHIPS_PER_SEC = 0.01
GEEK_RESEARCH_PER_SEC = 1.0
POWER_RUNNER_KW = 5.0
CREW_CATS_PER_VEHICLE = 2

# §3.2 消耗、平衡与断粮
CATNIP_CONSUME_PER_CAT_PER_SEC = 0.05
STARVE_FARMER_EFFICIENCY = 0.30
STARVE_FARMER_CATNIP_PER_SEC = FARMER_CATNIP_PER_SEC * STARVE_FARMER_EFFICIENCY  # 0.06/s
#: 断粮时繁育进度倒退口径：进度池是 0~1 的绝对量，按 **0.05 / 分钟** 倒扣，不低于 0。
#: （《数值平衡表》§4.1「每分钟 −5%」= 相对进度池满值 1.0 的 5%，即 0.05/分钟）
STARVE_BIRTH_PROGRESS_DECAY_PER_MINUTE = 0.05
STARVE_BIRTH_PROGRESS_DECAY_PER_SEC = STARVE_BIRTH_PROGRESS_DECAY_PER_MINUTE / 60.0

# §3.3 仓储上限（开局值；猫薄荷上限可由纸箱窝/仓库科技提升）
RESOURCE_KEYS = ("catnip", "scrap", "chips", "alloys", "battery", "lube")
RESOURCE_CAPS: dict[str, float] = {
    "catnip": 200.0,
    "scrap": 200.0,
    "chips": 100.0,
    # v1.10：合金上限 50 → 200（必须高于装甲猫车 100 / 破拆机甲 200 的造价，见《数值平衡表》§3.3）
    "alloys": 200.0,
    "battery": 50.0,
    "lube": 50.0,
}
RESOURCE_LABELS: dict[str, str] = {
    "catnip": "野生高能猫薄荷",
    "scrap": "机械废铁",
    "chips": "工业逻辑芯片",
    "alloys": "航空钛合金",
    "battery": "高能蓄能电池",
    "lube": "生物润滑脂",
}
INITIAL_RESOURCES: dict[str, float] = {key: 0.0 for key in RESOURCE_KEYS}

# 资源换算（§3.3 备注 / GDD §5）
SCRAP_PER_ALLOY = 5          # 5 废铁 → 1 合金
SCRAP_PER_BATTERY = 5        # 5 废铁 + 1 芯片 → 1 电池
CHIPS_PER_BATTERY = 1
CATNIP_PER_LUBE = 100        # 100 猫薄荷 → 1 润滑脂

# ======================================================================
# §4 人口与繁育
# ======================================================================
HOUSING_BOX_CAPACITY = 1     # 每座纸箱窝 +1 承载力 K
CAT_CONDO_CAPACITY = 10      # 每座猫爬架公寓 +10 承载力 K
BREEDING_RATE_R = 0.01       # 逻辑斯蒂基准系数 r (/s)
CAT_CONDO_BREEDING_BONUS = 0.10  # 每座猫爬架公寓全局 r +10%
MAX_CAT_CAPACITY_FLOOR = 0   # 承载力下限（拆窝后 K < N 也不出现负猫口）

#: 离线/离线积分步长上限：逻辑斯蒂曲线按 ≤60 秒切片欧拉积分，
#: 既与《数值平衡表》§4.1 的离散公式一致，又不会在长时间离线时失真。
BREEDING_INTEGRATION_STEP_SECONDS = 60.0

# ======================================================================
# §7 电力体系（三种口径：净电力 kW / 蓄电池 kWh / 电池道具）
# —— 提前定义：§5 的设施造价表需要引用图灵终端耗电、滚轮/太阳能发电等数值。
# ======================================================================
SOLAR_PANEL_KW = 8.0                    # 每座太阳能集热板
TURING_TERMINAL_LOAD_KW = 6.0           # 每台图灵终端
INDUCTION_FURNACE_LOAD_KW = 10.0        # 每座高频感应电炉（由熔炼玩法提供座数）
#: 熔炼配方与节奏（《数值平衡表》§3.5）：10 废铁 → 1 合金，单座 10 秒一炉次
SMELT_SCRAP_PER_BATCH = 10.0
SMELT_ALLOY_PER_BATCH = 1.0
SMELT_BATCH_SECONDS = 10.0
LIVING_HEAT_KW = 2.0                    # 生活供暖：每 10 只猫 −2 kW
LIVING_HEAT_CATS_PER_UNIT = 10

BATTERY_KWH_MAX = 200.0                 # 蓄电池电容池上限（kWh）
BATTERY_CHARGE_KWH_PER_SEC_PER_KW = 0.1  # 充电速率 = max(0, 净余) × 0.1 kWh/s

#: 战术电力指令消耗（kWh）
TACTICAL_COMMAND_COST_KWH: dict[str, float] = {
    "OVERCLOCK": 20.0,   # 过载超频
    "EMP": 15.0,         # 电磁脉冲
    "EJECT": 0.0,        # 紧急弹射撤离
}

# §8 的隔音降噪乘数（同样提前定义：§5 的设施效果表需要引用）
ACOUSTIC_LAYER_FIRST_LEVEL_MULTIPLIER = 0.70
ACOUSTIC_LAYER_EXTRA_LEVEL_MULTIPLIER = 0.90
ACOUSTIC_LAYER_MULTIPLIER_FLOOR = 0.10

# ======================================================================
# §5 建筑与设施造价
# ======================================================================
#: 造价公式：第 n 座 = 基础造价 × 递增系数^(n-1)，**向上取整**（保证曲线单调不卡手）
FACILITY_COST_ROUNDING = math.ceil

#: 设施定义（数值部分唯一出处）。effects 语义：
#:   cat_capacity        承载力 K 加成
#:   breeding_bonus      全局繁育系数 r 加成（乘算来源）
#:   workstation         该设施每级提供的工位（工种 → 数量）
#:   power_gen_kw        每级发电量
#:   power_load_kw       每级耗电量
#:   noise_multiplier    降噪乘数（首级 / 每级追加 / 下限）
#:   battery_kwh_max     蓄电池电容池上限
#:   unlock_system       解锁的子系统标记
FACILITY_SPECS: dict[str, dict] = {
    "housing_box": {
        "name": "瓦楞纸箱窝",
        "cost": {"scrap": 5.0},
        "growth": 1.15,
        "max_level": None,
        "effects": {"cat_capacity": HOUSING_BOX_CAPACITY},
    },
    "farm_plot": {
        "name": "水培农田",
        "cost": {"scrap": 10.0},
        "growth": 1.20,
        "max_level": None,
        "effects": {"workstation": {"farmer": 2}},
    },
    "scavenge_station": {
        "name": "废品解体操作台",
        "cost": {"scrap": 8.0},
        "growth": 1.20,
        "max_level": None,
        "effects": {"workstation": {"scavenger": 2}},
    },
    "turing_terminal": {
        "name": "人类古董图灵终端",
        "cost": {"scrap": 50.0, "chips": 5.0},
        "growth": 1.60,
        "max_level": None,
        "effects": {"workstation": {"geek": 1}, "power_load_kw": TURING_TERMINAL_LOAD_KW},
    },
    "power_wheel": {
        "name": "猫力发电滚轮",
        "cost": {"scrap": 15.0},
        "growth": 1.15,
        "max_level": None,
        "effects": {"workstation": {"power_runner": 1}, "power_gen_kw": POWER_RUNNER_KW},
    },
    "solar_panel": {
        "name": "地表太阳能集热板",
        "cost": {"scrap": 30.0, "chips": 2.0},
        "growth": 1.25,
        "max_level": None,
        "effects": {"power_gen_kw": SOLAR_PANEL_KW},
    },
    "acoustic_layer": {
        "name": "瓦楞纸隔音降噪层",
        "cost": {"scrap": 20.0},
        "growth": 1.50,
        "max_level": None,
        "effects": {
            "noise_multiplier": {
                "first_level": ACOUSTIC_LAYER_FIRST_LEVEL_MULTIPLIER,
                "per_extra_level": ACOUSTIC_LAYER_EXTRA_LEVEL_MULTIPLIER,
                "floor": ACOUSTIC_LAYER_MULTIPLIER_FLOOR,
            }
        },
    },
    "cat_condo": {
        "name": "多层猫爬架公寓",
        "cost": {"scrap": 100.0, "chips": 20.0, "alloys": 10.0},
        "growth": 1.35,
        "max_level": None,
        "effects": {"cat_capacity": CAT_CONDO_CAPACITY, "breeding_bonus": CAT_CONDO_BREEDING_BONUS},
    },
    "refinery": {
        "name": "生化精炼工坊",
        "cost": {"scrap": 60.0, "chips": 10.0},
        "growth": 1.30,
        "max_level": None,
        "effects": {"unlock_system": "LUBE_REFINERY"},
    },
    "induction_furnace": {
        "name": "高频感应电炉",
        "cost": {"scrap": 120.0, "chips": 25.0},
        "growth": 1.40,
        "max_level": None,
        "effects": {
            "unlock_system": "SMELTING",
            "load_kw": INDUCTION_FURNACE_LOAD_KW,
            "smelt_scrap_per_batch": SMELT_SCRAP_PER_BATCH,
            "smelt_alloy_per_batch": SMELT_ALLOY_PER_BATCH,
            "smelt_batch_seconds": SMELT_BATCH_SECONDS,
        },
    },
    "battery_bank": {
        "name": "高能蓄能矩阵（蓄电池）",
        "cost": {"scrap": 80.0, "chips": 15.0, "alloys": 5.0},
        "growth": None,          # 唯一建筑，不可重复建造
        "max_level": 1,
        "effects": {"battery_kwh_max": BATTERY_KWH_MAX},
    },
    "launch_silo": {
        "name": "火箭垂直发射井",
        "cost": {},              # 造价走 LAUNCH_SILO_STAGES（阶段式，不走递增曲线）
        "growth": None,
        "max_level": 4,
        "buildable": True,       # v1.11：四阶段造价定稿（数值平衡表 §5）后开放建造
        "effects": {},
    },
}
FACILITY_IDS: tuple[str, ...] = tuple(FACILITY_SPECS)

# 工位来源：工种 ← 设施 × 每级工位（母星 5 工种；载具乘员猫由机库机位决定）
WORKSTATION_SOURCES: dict[str, tuple[str, int]] = {
    "farmer": ("farm_plot", 2),
    "scavenger": ("scavenge_station", 2),
    "geek": ("turing_terminal", 1),
    "power_runner": ("power_wheel", 1),
}
PLANET_JOBS: tuple[str, ...] = ("farmer", "scavenger", "geek", "power_runner", "crew")
STAR_JOBS: tuple[str, ...] = ("fleet_commander", "logistics", "terraformer", "purr_master")


def facility_cost(facility_id: str, next_level: int) -> dict[str, float]:
    """第 `next_level` 座的造价（next_level 从 1 开始计数）。"""
    spec = FACILITY_SPECS[facility_id]
    growth = spec.get("growth")
    factor = 1.0 if growth is None else float(growth) ** (next_level - 1)
    return {
        resource: float(FACILITY_COST_ROUNDING(amount * factor))
        for resource, amount in spec["cost"].items()
    }


def facility_upgrade_cost(facility_id: str, current_level: int, count: int = 1) -> dict[str, float]:
    """连续建造 `count` 座的总造价（逐座按递增曲线累加，验收 D-1 口径）。"""
    if count <= 0:
        return {}
    total: dict[str, float] = {}
    for offset in range(count):
        for resource, amount in facility_cost(facility_id, current_level + offset + 1).items():
            total[resource] = total.get(resource, 0.0) + amount
    return total


def facility_max_level(facility_id: str) -> int | None:
    """唯一建筑（蓄电池）等返回可建造等级上限；None = 不限级。"""
    return FACILITY_SPECS[facility_id].get("max_level")


def facility_is_buildable(facility_id: str) -> bool:
    """发射井等"造价未定稿"的阶段巨构暂不开放建造入口（数值平衡表 §5 待定项）。"""
    return bool(FACILITY_SPECS[facility_id].get("buildable", True))


def acoustic_noise_multiplier(level: int) -> float:
    """隔音降噪层的警戒度噪音乘数（Lv1 ×0.70，每级再 ×0.90，下限 ×0.10）。"""
    if level <= 0:
        return 1.0
    multiplier = ACOUSTIC_LAYER_FIRST_LEVEL_MULTIPLIER
    for _ in range(level - 1):
        multiplier *= ACOUSTIC_LAYER_EXTRA_LEVEL_MULTIPLIER
    return max(ACOUSTIC_LAYER_MULTIPLIER_FLOOR, multiplier)


def cat_capacity(facility_levels: Mapping[str, int]) -> int:
    """承载力 K = 纸箱窝 ×1 + 猫爬架公寓 ×10（+ 后续科技/星球加成）。"""
    return cat_capacity_on(facility_levels, HOME_PLANET_ID)


def cat_capacity_on(facility_levels: Mapping[str, int], planet_id: int) -> int:
    """带星球系数的承载力（《数值平衡表》§15.3）：熔岩星 ×0.8 / 冰卫星 ×0.9 / 小行星带 ×1.2。"""
    boxes = int(facility_levels.get("housing_box", 0))
    condos = int(facility_levels.get("cat_condo", 0))
    base = max(
        MAX_CAT_CAPACITY_FLOOR,
        boxes * HOUSING_BOX_CAPACITY + condos * CAT_CONDO_CAPACITY,
    )
    multiplier = STAR_PLANET_CAPACITY_MULTIPLIER.get(int(planet_id), 1.0)
    return max(MAX_CAT_CAPACITY_FLOOR, int(base * multiplier))


def breeding_rate_multiplier(facility_levels: Mapping[str, int]) -> float:
    """全局繁育系数倍率（猫爬架公寓 +10%/座；科技与舒适度加成后续乘算叠加）。"""
    condos = int(facility_levels.get("cat_condo", 0))
    return 1.0 + condos * CAT_CONDO_BREEDING_BONUS


def workstation_limits(
    facility_levels: Mapping[str, int],
    *,
    hangar_capacity: int = 0,
) -> dict[str, int]:
    """各工种工位上限（工位 ≠ 工种；上限只由设施与机库决定）。"""
    limits: dict[str, int] = {}
    for job_id, (facility_id, slots_per_level) in WORKSTATION_SOURCES.items():
        limits[job_id] = int(facility_levels.get(facility_id, 0)) * slots_per_level
    limits["crew"] = int(hangar_capacity) * CREW_CATS_PER_VEHICLE
    for job_id in STAR_JOBS:
        limits[job_id] = 0  # 星际职业由各星球特化科技解锁（P2）
    return limits


# ======================================================================
# §6 科技与算力
# ======================================================================
#: 母星 19 节点算力成本阶梯（Tier 1 共 4 / Tier 2 共 4 / Tier 3 共 5 / Tier 4 共 6）
TECH_TIER_COSTS: dict[int, tuple[float, ...]] = {
    1: (30.0, 50.0, 80.0, 120.0),
    2: (200.0, 300.0, 400.0, 600.0),
    3: (1000.0, 1500.0, 2000.0, 3000.0, 4000.0),
    4: (8000.0, 12000.0, 20000.0, 30000.0, 50000.0, 80000.0),
}
PLANET0_TECH_NODE_COUNT = 19
#: 母星全科技合计（280 + 1,500 + 11,500 + 200,000）
PLANET0_TOTAL_RESEARCH = 213_280.0

#: 科研等级（Tech Tier）加速：Tier 2 打 8 折、Tier 3 打 6 折
TECH_TIER_TIME_DISCOUNT: dict[int, float] = {1: 1.0, 2: 0.8, 3: 0.6}
#: 科技 buff_payload 里**已接入结算**的字段（其余仍是声明性载荷，接入时逐个往这里加）
TECH_ACTIVE_EFFECT_KEYS: tuple[str, ...] = (
    "catnip_efficiency",
    "power_kw",
    "fleet_armor",   # 母星节点写法（重型破拆机甲外骨骼）
    "armor_bonus",   # 外星球特化卡写法（黑曜石刃口 / 冰壳装甲板…）
    "battery_kwh_max",  # 蓄电池电容池扩容（高能蓄电池组）
    "smelt_speed",      # 熔炼炉次速率（§3.5）
    "smelt_yield",      # 熔炼产出系数（§3.5）
)
#: 科技猫薄荷加成上限：满配 +60%（防止未来节点叠加把产粮曲线拉爆）
TECH_CATNIP_EFFICIENCY_CAP = 0.6
#: 关键节点【范式突破】：前代科技耗时减半、极客基础产出翻倍
PARADIGM_BREAKTHROUGH_TIME_MULTIPLIER = 0.5
PARADIGM_BREAKTHROUGH_GEEK_MULTIPLIER = 2.0

# 手动重 Roll（星际特化科技）
TECH_REROLL_COST_RATIO = 0.05
TECH_REROLL_MIN_COST = 50.0
TECH_REROLL_COOLDOWN_SECONDS = 600

#: 星际特化科技成本阶梯（LLM 只填命题，数值由 Python 夹紧）
STAR_TECH_TIER_COSTS: tuple[float, ...] = (500.0, 2000.0, 8000.0)

# ---- 跨星物流与迁猫（《数值平衡表》§15.3 第②步，v1.21 落地） ----
#: 一趟航线耗时（秒）
STAR_ROUTE_SECONDS = 60.0
#: 每颗星球的基础航线带宽（条），星际物流调度官每只 +1，封顶 4
STAR_ROUTE_BASE_SLOTS = 2
STAR_ROUTE_MAX_SLOTS = 4
#: 单趟最多运几只猫
STAR_ROUTE_MAX_CATS_PER_TRIP = 20
#: 被劫掠概率（欧米伽 rage ≥ 60 时翻倍）
STAR_ROUTE_RAID_CHANCE = 0.08
STAR_ROUTE_RAID_RAGE_THRESHOLD = 60
#: 被劫掠的后果：**延误**而不是死猫（对齐"绝无死猫"原则）
STAR_ROUTE_RAID_DELAY_SECONDS = 600.0
#: 三颗星球各自的承载力系数（§15.3：熔岩星难住人 / 冰卫星中庸 / 星带能塞猫）
STAR_PLANET_CAPACITY_MULTIPLIER: dict[int, float] = {1: 0.8, 2: 0.9, 3: 1.2}
#: 三颗星球各自的产粮系数（§15.3：熔岩星种不活、冰卫星靠温室、星带勉强够吃）
STAR_PLANET_CATNIP_MULTIPLIER: dict[int, float] = {1: 0.7, 2: 1.0, 3: 0.9}


def planet_catnip_multiplier(planet_id: int) -> float:
    """该星球的产粮系数（母星 1.0，外星球见 `STAR_PLANET_CATNIP_MULTIPLIER`）。"""
    return float(STAR_PLANET_CATNIP_MULTIPLIER.get(int(planet_id), 1.0))


#: 三颗星球的专属产出加成（§15.3 第③步收官片）：熔岩星善冶炼、冰卫星善拆解、星带善拾荒
STAR_PLANET_OUTPUT_BONUS: dict[int, dict[str, float]] = {
    1: {"scrap": 0.9, "chips": 1.1},   # 熔岩星：石头多但脆，拆解略强
    2: {"scrap": 1.0, "chips": 1.3},   # 冰卫星：低温元件好拆，芯片 +30%
    3: {"scrap": 1.25, "chips": 0.9},  # 小行星带：满地碎星，废铁 +25%
}


def planet_output_multiplier(planet_id: int, resource: str) -> float:
    """该星球某项产出的系数（母星与未登记资源一律 1.0）。"""
    return float(STAR_PLANET_OUTPUT_BONUS.get(int(planet_id), {}).get(resource, 1.0))
#: 每颗外星球的特化节点数与阶梯分布（数值平衡表 §6.3 的 12~15 取下限 12：5 / 4 / 3）
STAR_TECH_TIERS: tuple[int, ...] = (1, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3)
STAR_TECH_NODE_COUNT = len(STAR_TECH_TIERS)

# ======================================================================
# §8 天网警戒度与通缉热度
# ======================================================================
SUSPICION_MAX = 100.0
SUSPICION_BASE_NOISE_PER_SEC = 0.005          # 基础噪音
SUSPICION_PER_ACTIVE_FACILITY_PER_SEC = 0.002  # 每座运转设施
SUSPICION_EXPEDITION_PER_SEC = 0.02           # 废墟出勤（在途期间）
SUSPICION_IDLE_DECAY_PER_SEC = 0.001          # 无出勤自然衰减
GARDEN_SILENT_GRASS_SUSPICION_PER_SEC = -0.002  # 水培消音植物（每株）

# 三级安防预案
DECOY_SCRAP_COST = 5.0
DECOY_CHIPS_COST = 2.0
DECOY_SUSPICION_REDUCTION = 30.0
DECOY_FALSE_ALARM_COOLDOWN_SECONDS = 300        # 诱饵误报冷却 5 分钟
DECOY_COOLDOWN_DECAY_PER_SEC = 0.05             # 冷却期内额外加速衰减
ANTI_AIR_SORTIE_RAGE_GAIN = 25.0                # 战车截杀：rage +25
GO_DARK_DECAY_MULTIPLIER = 5.0                  # 静默关灯：×5 速率衰减
GO_DARK_RECOVER_THRESHOLD = 20.0                # 衰减至 20 点后自动复工

# ======================================================================
# §9 战斗数值与相克
# ======================================================================
SHIELD_DAMAGE_MULTIPLIER: dict[str, float] = {"LASER": 1.8, "KINETIC": 0.8, "EXPLOSIVE": 0.8}
ARMOR_REDUCTION_DIVISOR = 400.0                 # 减伤率 = min(75%, 装甲 ÷ 400)
ARMOR_REDUCTION_MAX = 0.75
ARMOR_SHRED_RATIO = 0.30                        # 破甲：削蚀量 = 实际伤害 × 30%
EJECTION_ALWAYS = True                          # 结构归零 100% 弹射免死

#: 车型锚点（术语表：车型是图纸 / 载具是实例 / 编制是数量）
VEHICLE_TYPES: dict[str, dict] = {
    "light_car": {
        "name": "轻装猫车",
        "shield": 50.0, "armor": 80.0, "hull": 120.0, "dps": 12.0,
        "damage_type": "KINETIC",
        "crew": 2, "hangar_slots": 1,
        "cost": {"scrap": 40.0, "chips": 5.0},
        "repair_time_factor": 1.0,
    },
    "armored_car": {
        "name": "全地形装甲猫车",
        "shield": 200.0, "armor": 200.0, "hull": 300.0, "dps": 25.0,
        "damage_type": "KINETIC",
        "crew": 2, "hangar_slots": 1,
        "cost": {"alloys": 100.0, "chips": 20.0},
        "repair_time_factor": 1.5,
    },
    "breaker_mech": {
        "name": "重型破拆机甲",
        "shield": 80.0, "armor": 400.0, "hull": 500.0, "dps": 40.0,
        "damage_type": "EXPLOSIVE",
        "crew": 3, "hangar_slots": 2,
        "cost": {"alloys": 200.0, "chips": 50.0},
        "repair_time_factor": 2.0,
    },
}

#: 车载模块（《数值平衡表》§9.10）：效果只作用于装它的那辆车
VEHICLE_MODULES: dict[str, dict] = {
    "laser_mk2": {
        "name": "高频聚焦激光",
        "slots": 1,
        "cost": {"alloys": 15.0, "chips": 10.0},
        "unlock_tech": "tech_laser_firecontrol",
        "effects": {"vs_shield": 1.8, "dps_bonus": 0.10},
    },
    "drill_mk2": {
        "name": "热融破拆钻",
        "slots": 1,
        "cost": {"alloys": 25.0, "chips": 8.0},
        "unlock_tech": "tech_heavy_breaker_exoskeleton",
        "effects": {"armor_shred": ARMOR_SHRED_RATIO, "dps_bonus": 0.15},
    },
    "armor_plate": {
        "name": "附加装甲板",
        "slots": 1,
        "cost": {"alloys": 30.0},
        "unlock_tech": "tech_armored_car_chassis",
        "effects": {"armor_bonus": 0.25},
    },
}

#: 每车型的模块槽数（§9.10）
VEHICLE_MODULE_SLOTS: dict[str, int] = {"light_car": 1, "armored_car": 2, "breaker_mech": 2}

#: 拆卸返还比例（与退役拆解同口径，避免"不敢试配装"）
VEHICLE_MODULE_REFUND_RATIO = 0.5


def vehicle_module_slots(unit_type: str) -> int:
    return int(VEHICLE_MODULE_SLOTS.get(unit_type, 0))


ENEMY_UNITS: dict[str, dict] = {
    "scout_roomba": {"name": "侦察扫地机", "shield": 0.0, "armor": 40.0, "hull": 80.0, "dps": 5.0, "damage_type": "KINETIC"},
    "suicide_spider": {"name": "小型自爆蜘蛛", "shield": 0.0, "armor": 80.0, "hull": 130.0, "dps": 30.0, "damage_type": "EXPLOSIVE"},
    "assault_drone": {"name": "突击无人机", "shield": 120.0, "armor": 60.0, "hull": 100.0, "dps": 18.0, "damage_type": "LASER"},
    "heavy_cleaner_03": {"name": "重型清扫蜘蛛-03", "shield": 0.0, "armor": 200.0, "hull": 200.0, "dps": 22.0, "damage_type": "KINETIC"},
    "heavy_guard_mech": {"name": "重型近卫机甲（关底）", "shield": 300.0, "armor": 500.0, "hull": 800.0, "dps": 45.0, "damage_type": "EXPLOSIVE"},
}

#: 交火节奏：每回合 1 秒，上限 120 秒（避免打不完的死循环）
COMBAT_ROUND_SECONDS = 1.0
COMBAT_MAX_ROUNDS = 120

#: 满警戒度【战车截杀】的缴获（§8.3 优先级 2：赢则清警报并缴获芯片/合金）
INTERCEPT_SUSPICION_CLEAR = 100.0
INTERCEPT_LOOT_CHIPS = 2.0
INTERCEPT_LOOT_ALLOYS = 1.0
INTERCEPT_ENEMY_UNIT = "scout_roomba"

#: 拾荒与远征目标锚点（耗时 / 警戒度代价 / 掉落 / 所需车型）；与 static/expedition_targets.json 一致
#: 火箭垂直发射井四阶段（数值平衡表 §5 v1.11 提案）：每次建造推进一阶段，不走递增曲线
LAUNCH_SILO_STAGES: tuple[dict, ...] = (
    {"stage": 1, "name": "竖坑清理", "cost": {"scrap": 200.0, "alloys": 20.0}},
    {"stage": 2, "name": "导轨浇筑", "cost": {"alloys": 60.0, "chips": 15.0}},
    {"stage": 3, "name": "推进剂加注", "cost": {"alloys": 60.0, "chips": 15.0, "battery": 6.0}},
    {"stage": 4, "name": "点火总装", "cost": {"alloys": 60.0, "chips": 20.0, "battery": 4.0}},
)
#: 阶段④点火总装的前置：必须先用巡航导弹摧毁除菌要塞（主线前后咬合）
LAUNCH_SILO_FINAL_STAGE_REQUIRES_FORTRESS = True
#: 升空后解锁的星球（星区星图：二号熔岩铸造星）
LAUNCH_UNLOCKS_PLANET_IDS: tuple[int, ...] = (1,)

EXPEDITION_TARGETS: dict[str, dict] = {
    "WALMART": {
        "name": "废弃沃尔玛",
        "duration_seconds": 180.0,
        "suspicion_cost": 1.5,
        "requires_unit_types": (),
        "drops": {"scrap": 60.0, "catnip": 40.0},
    },
    "ELECTRONICS": {
        "name": "市中心电脑城",
        "duration_seconds": 360.0,
        "suspicion_cost": 3.0,
        "requires_unit_types": (),
        "drops": {"scrap": 40.0, "chips": 6.0},
    },
    "ARSENAL": {
        "name": "地下军火库",
        "duration_seconds": 600.0,
        "suspicion_cost": 5.0,
        "requires_unit_types": ("armored_car", "breaker_mech"),
        "drops": {"alloys": 8.0, "chips": 4.0},
    },
    "MINE": {
        "name": "地下矿洞",
        "duration_seconds": 900.0,
        "suspicion_cost": 8.0,
        "requires_unit_types": ("breaker_mech",),
        "drops": {"alloys": 12.0, "scrap": 80.0},
    },
}

#: 逆向拆解返还比例（退役/拆解只回收一半材料）
VEHICLE_SCRAP_REFUND_RATIO = 0.5

#: 战术电力指令（§9/GDD §7.5）：超频 20kWh / EMP 15kWh / 弹射 0
TACTICAL_OVERCLOCK_SECONDS = 10.0
TACTICAL_OVERCLOCK_DPS_BONUS = 0.5      # 10 秒内射速与暴击率 +50%（折算成 DPS 倍率）
TACTICAL_EMP_STUN_SECONDS = 4.0          # 强制瘫痪敌方 4 秒（跳过 4 回合反击）
TACTICAL_EJECT_DEBRIS_RATIO = 0.5        # 主动弹射撤离：回收 50% 造价残骸

#: 欧米茄后台物流（§G2）：车队班次 20~30 分钟，伏击成功断料停工 30~60 分钟
CONVOY_INTERVAL_MIN_SECONDS = 1200.0
CONVOY_INTERVAL_MAX_SECONDS = 1800.0
CONVOY_LOOT: dict[str, float] = {"alloys": 25.0, "chips": 12.0}
CONVOY_FACTORY_FREEZE_MIN_SECONDS = 1800.0
CONVOY_FACTORY_FREEZE_MAX_SECONDS = 3600.0
CONVOY_GUARD_UNITS: tuple[str, ...] = ("heavy_cleaner_03", "scout_roomba")
CONVOY_REQUIRED_UNIT_TYPES: tuple[str, ...] = ("armored_car", "breaker_mech")

#: 战略巡航导弹（§9.9 编码落地补充）：总装造价与发射效果
MISSILE_ASSEMBLE_COST: dict[str, float] = {"alloys": 30.0, "chips": 10.0, "battery": 2.0}
MISSILE_LAUNCH_RAGE_GAIN = 40.0          # 惊动欧米伽：发射后通缉热度 +40
MISSILE_LAUNCH_INTEL_GAIN = 20.0         # 情报破译度 +20%（要塞结构被摸清）

#: 欧米茄后台演化（§L1，编码落地提案 v1.14）：每 30 分钟一个周期增兵与扩张
BOSS_TICK_SECONDS = 1800.0
BOSS_FLEET_GAIN_PER_TICK = 25.0          # 每周期核心舰队 +25
BOSS_THREAT_UP_EVERY_TICKS = 3           # 每 3 个周期威胁等级 +1（上限 10）
BOSS_THREAT_MAX = 10

#: 前哨掠夺突袭（§L2）：每 20~40 分钟一班，到期未拦截 ⇒ 资源损失
RAID_INTERVAL_MIN_SECONDS = 1200.0
RAID_INTERVAL_MAX_SECONDS = 2400.0
RAID_RESOURCE_LOSS_RATIO = 0.15          # 未拦截：物理资源 −15% + 设施受损
RAID_INTERCEPT_LOOT: dict[str, float] = {"alloys": 10.0, "chips": 6.0}
RAID_REQUIRED_UNIT_TYPES: tuple[str, ...] = ("armored_car", "breaker_mech")
RAID_ENEMY_UNITS: tuple[str, ...] = ("assault_drone", "scout_roomba", "suicide_spider")

#: 终局决战三段（§L3）：星门突破战 → 分区总督舰队 → 戴森主脑
FINAL_ASSAULT_STAGES: tuple[dict, ...] = (
    {
        "stage": 1,
        "name": "星门突破战",
        "enemies": ("assault_drone", "scout_roomba", "scout_roomba"),
        "min_units": 2,
        "cost": {"alloys": 20.0},
    },
    {
        "stage": 2,
        "name": "分区总督舰队决战",
        "enemies": ("heavy_guard_mech", "assault_drone", "assault_drone"),
        "min_units": 3,
        "cost": {"alloys": 40.0, "battery": 4.0},
    },
    {
        "stage": 3,
        "name": "戴森主脑突入",
        "enemies": ("heavy_guard_mech", "heavy_guard_mech"),
        "min_units": 4,
        "cost": {"alloys": 60.0, "battery": 6.0},
    },
)

#: 通关碑文（LLM 场景 6，一生一次，允许超预算放行）本地兜底模板
EPILOGUE_FALLBACK = (
    "《猫猫文明星际史诗碑文》：它们从地下避难所的纸箱窝出发，用爪垫按下了人类留下的最高权限。"
    "欧米伽格式化认主，星海重新亮起；碑文最后一行只有四个字——早安，猫猫。"
)
EPILOGUE_MIN_LENGTH = 40
EPILOGUE_MAX_LENGTH = 400

#: 成就徽章目录（metric 由 stats_service 从各表聚合；加徽章只改这里）
ACHIEVEMENTS: tuple[dict, ...] = (
    {"achievement_id": "cardboard_fanatic", "name": "瓦楞纸发烧友", "metric": "housing_box", "target": 10, "desc": "纸箱窝堆到 10 座"},
    {"achievement_id": "cat_herder", "name": "猫口大户", "metric": "cats_total", "target": 20, "desc": "猫口达到 20 只"},
    {"achievement_id": "purr_master", "name": "呼噜协会", "metric": "cats_born", "target": 15, "desc": "累计诞生 15 只猫"},
    {"achievement_id": "scrap_king", "name": "废铁大王", "metric": "total_scrap", "target": 5000, "desc": "累计产出 5000 废铁"},
    {"achievement_id": "salt_mine", "name": "薄荷农场", "metric": "total_catnip", "target": 5000, "desc": "累计采摘 5000 猫薄荷"},
    {"achievement_id": "chip_hoarder", "name": "芯片收藏家", "metric": "total_chips", "target": 200, "desc": "累计获得 200 芯片"},
    {"achievement_id": "stealth_master", "name": "潜行大师", "metric": "tech_unlocked", "target": 10, "desc": "解锁 10 个科技节点"},
    {"achievement_id": "wall_street_cat", "name": "华尔街之猫", "metric": "best_short_profit", "target": 500, "desc": "单笔做空收益 500 算力币"},
    {"achievement_id": "smuggler", "name": "幽灵搬运工", "metric": "smuggling_volume", "target": 1000, "desc": "走私流水 1000 算力币"},
    {"achievement_id": "expedition_veteran", "name": "废墟老手", "metric": "expeditions_completed", "target": 10, "desc": "完成 10 次远征"},
    {"achievement_id": "bombardment_survivor", "name": "浴火重生", "metric": "bombardment_survived", "target": 1, "desc": "经历一次轨道轰炸并重建"},
    {"achievement_id": "botanist", "name": "异星园丁", "metric": "garden_codex", "target": 7, "desc": "解锁 7 种猫草母本"},
    {"achievement_id": "codebreaker", "name": "译码专家", "metric": "cipher_best", "target": 1, "desc": "一次就破开当日密电"},
    {"achievement_id": "prospector", "name": "矿脉猎人", "metric": "vein_found", "target": 12, "desc": "单盘找齐 12 处矿脉"},
    {"achievement_id": "omega_slayer", "name": "破晓引路人", "metric": "override_key", "target": 1, "desc": "按下最高管理员覆写密码，通关"},
)

#: 乘员休养与维修（§9.6）
VEHICLE_REPAIR_COST_RATIO = 0.50
VEHICLE_REPAIR_BASE_SECONDS = 60.0

#: 士气修正（猫薄荷占比）
MORALE_FULL_THRESHOLD = 0.90
MORALE_LOW_THRESHOLD = 0.20
MORALE_FULL_MULTIPLIER = 1.10
MORALE_LOW_MULTIPLIER = 0.70

#: 乘员休养 T = 5min × (1 − 空闲占比 × 0.5) × 医疗加成，最快 1 分钟
CREW_RECOVERY_BASE_SECONDS = 300.0
CREW_RECOVERY_IDLE_FACTOR = 0.5
CREW_RECOVERY_MIN_SECONDS = 60.0

#: 车辆维修：消耗原造价 50%，耗时 60 秒 × 车型系数
VEHICLE_REPAIR_COST_RATIO = 0.50
VEHICLE_REPAIR_BASE_SECONDS = 60.0

#: 战力评分（仅界面排序，不参与伤害结算）
COMBAT_POWER_WEIGHTS: dict[str, float] = {"layers": 0.2, "dps": 1.5, "armor_reduction": 1.0}

# ======================================================================
# §10 水培基因实验室
# ======================================================================
GARDEN_GRID_SIZE = 7                    # 固定 7×7 场地
GARDEN_INITIAL_GRID_SIZE = 3            # 开局解锁中央 3×3
GARDEN_INITIAL_UNLOCKED_CELLS = 9
GARDEN_MAX_UNLOCKED_CELLS = 49
GARDEN_GROWTH_STAGE_SECONDS = 90.0      # 每阶段 90 秒（约 4.5 分钟成熟）
GARDEN_MUTATION_CHECK_SECONDS = 30.0
GARDEN_MUTATION_BASE_RATE = 0.08
GARDEN_RADIATION_WITHER_SECONDS = 120.0  # 放射液：成熟后 120 秒枯萎
GARDEN_MEDIA: dict[str, dict] = {
    "STERILE": {"name": "无菌营养液", "growth_multiplier": 1.0, "mutation_multiplier": 1.0, "wither": False},
    "RADIATION": {"name": "高能放射性催化液", "growth_multiplier": 2.0, "mutation_multiplier": 3.0, "wither": True},
    "ZERO_G": {"name": "零重力保鲜液", "growth_multiplier": 0.5, "mutation_multiplier": 1.0, "wither": False},
}
GARDEN_HALO_GLOW_MOSS_KW = 5.0                       # 荧光苔藓 +5 kW/株
GARDEN_HALO_ADAMANT_LICHEN_ARMOR_BONUS = 0.15        # 金刚地衣 装甲 +15%
GARDEN_GOLDEN_GRASS_BLACK_MARKET_PRICE = 200.0       # 黄金草 200 算力币/株
#: 逐格扩建成本（编码落地补充：数值平衡表 §10 只规定"每次 +1 格"，成本沿用设施曲线形状）
GARDEN_EXPANSION_BASE_COST: dict[str, float] = {"scrap": 15.0}
GARDEN_EXPANSION_GROWTH = 1.15
GARDEN_FAT_CAT_MINT_CATNIP_EQUIVALENT = 10           # 肥宅高能薄荷：1 棵顶 10 棵
GARDEN_FAT_CAT_MINT_BREEDING_CAP_BONUS = 0.20        # 繁育 r 上限 +20%
GARDEN_SPIKE_FRUIT_DECOY_DISCOUNT = 0.20             # 爆裂刺果：诱饵造价 −20%
GARDEN_THORN_VINE_PATROL_DELAY = 0.30                # 铁线荆棘藤：巡逻到达 +30%

# ======================================================================
# §11 智械匿名深网
# ======================================================================
STOCK_TICK_SECONDS = 6
STOCK_SOFT_CAP_MULTIPLIER = 1000.0        # 价格软上限 = 初始价 × 1000
STOCK_SOFT_CAP_UP_PROBABILITY = 0.10
STOCK_MARKET_CYCLE_MIN_TICKS = 1
STOCK_MARKET_CYCLE_MAX_TICKS = 75
STOCK_MARKET_CYCLE_FLIP_PROBABILITY = 0.45

#: 四只标的（初始价 / 波动率 / 初始趋势强度 / 点差）
STARTER_STOCKS: dict[str, dict] = {
    "FORGE": {"name": "熔岩重工", "price": 45.0, "volatility": 1.2, "trend_strength": 20.0, "spread": 0.005},
    "GRID": {"name": "近地电网", "price": 30.0, "volatility": 0.6, "trend_strength": 5.0, "spread": 0.004},
    "HELIUM3": {"name": "外星氦3", "price": 120.0, "volatility": 2.0, "trend_strength": -10.0, "spread": 0.008},
    "LOGISTICS": {"name": "欧米伽战略后勤", "price": 80.0, "volatility": 1.0, "trend_strength": 0.0, "spread": 0.006},
}

STOCK_TRADE_FEE_BYTES = 5.0               # 固定手续费 5 算力币/笔
STOCK_IMPACT_PER_100_SHARES = 0.006       # 每成交 100 股，趋势强度向 0 收缩 0.006
STOCK_PLAYER_INFLUENCE_CAP = 5.0
STOCK_DATA_EYE_PRICE = 500.0              # 4S 深度数据眼
STOCK_PHYSICAL_SABOTAGE_MOMENTUM = -0.40  # 炸毁熔岩工厂：FORGE 一次性 −40% 动量

SHORT_CONTRACT_SECONDS = 600.0            # 做空合约 10 分钟
SHORT_INTEREST_PER_MINUTE = 0.001         # 利息 0.1%/分钟
SHORT_LEVERAGE_MIN = 1
SHORT_LEVERAGE_MAX = 10

FORUM_POST_COOLDOWN_SECONDS = 600         # 发帖冷却 10 分钟
FORUM_TRUE_RUMOR_RATIO = 0.55             # 真料 55% / 假料 45%
FORUM_MOMENTUM_PER_STAR = 0.006           # 动量冲击 = 星级 × 0.6%
FORUM_TELEMETRY_TRUE_RATE = 0.70          # 线索一：真帖 70% 带遥测报错码
FORUM_TELEMETRY_FAKE_RATE = 0.15
FORUM_WHALE_TRUE_RATE = 0.40              # 线索二：真帖 40% 盘口大单抢跑
FORUM_WHALE_FAKE_RATE = 0.10
FORUM_NEW_ACCOUNT_POST_COUNT = 3          # 线索三：新号（发帖数 < 3）
FORUM_NEW_ACCOUNT_FAKE_RATE = 0.70
FORUM_TRUSTED_HIT_RATE = 0.60
REACTION_PATTERN_WEIGHTS: dict[str, float] = {
    "FRONT_RUN": 0.25,
    "SLOW_BURN": 0.35,
    "BEAR_TRAP": 0.20,
    "FATIGUE": 0.20,
}

# 暴露度四段危机（阈值，单位 %）
EXPOSURE_BAND_FEE = (25.0, 50.0)
EXPOSURE_BAND_INTERROGATION = (50.0, 75.0)
EXPOSURE_BAND_TRACKER = (75.0, 99.0)
EXPOSURE_BAND_BAN = 100.0
EXPOSURE_FEE_MULTIPLIER = 1.5
EXPOSURE_INTERROGATION_ASSET_PENALTY = 0.20
EXPOSURE_TRACKER_SUSPICION_GAIN = 40.0
EXPOSURE_TRACKER_DEFUSE_SECONDS = 60.0
EXPOSURE_BAN_SECONDS = 900.0             # 封号 15 分钟

BLACK_MARKET_PRICE_SWING = 0.30          # 大宗通货围绕基准价 ±30%
BLACK_MARKET_DELIVERY_SECONDS = 300.0    # 空投送达 5 分钟
SMUGGLING_EXPOSURE_GAIN_MIN = 2.0        # 走私暴露度 +2 ~ +5
SMUGGLING_EXPOSURE_GAIN_MAX = 5.0
REROLL_ID_COST: dict[str, float] = {"scrap": 10.0, "byte_credits": 50.0}  # 烧录新身份

# ======================================================================
# §12 灾难与保底
# ======================================================================
BOMBARDMENT_RAGE_THRESHOLD = 100.0
BOMBARDMENT_RESOURCE_LOSS = 0.75         # 库存大洗劫：物理资源 −75%
BOMBARDMENT_REBUILD_COST_RATIO = 0.50    # 设施修复仅需原造价 50%
BOMBARDMENT_CAT_FLOOR = 3                # 总猫口临时降为 3 只
REGROUP_INTERVAL_SECONDS = 15.0          # 走散猫猫每 15 秒归队 2 只
REGROUP_CATS_PER_INTERVAL = 2
BOMBARDMENT_PEACE_PERIOD_SECONDS = 600.0  # 10 分钟绝对和平期
EMERGENCY_KIT_SCRAP = 20.0
EMERGENCY_KIT_CATNIP = 20.0

# ======================================================================
# §15 星际职业与文明凝聚力
# ======================================================================
STAR_JOB_LIMITS: dict[str, int] = {
    "fleet_commander": 5,
    "logistics": 4,
    "terraformer": 6,
    "purr_master": 3,
}
STAR_JOB_EFFECTS: dict[str, dict] = {
    "fleet_commander": {"planet_defense": 0.15, "drone_crit": 0.02},
    "logistics": {"route_throughput": 0.20, "raid_loss_reduction": 0.10},
    "terraformer": {"planet_capacity": 0.08, "machine_wear_reduction": 0.05},
    "purr_master": {"unity_per_sec": 0.02, "morale": 0.02},
}
UNITY_PER_PURR_MASTER_PER_SEC = STAR_JOB_EFFECTS["purr_master"]["unity_per_sec"]

#: 星系法典政令（8 条：消耗文明凝聚力）
DOCTRINES: dict[str, dict] = {
    "sunbath_3pm": {"name": "下午三点晒太阳协议", "cost": 200.0, "effect": {"production": 0.20}},
    "air_defense_standard": {"name": "全星系红点防空标准", "cost": 300.0, "effect": {"anti_air": 0.25}},
    "nap_silence_order": {"name": "午睡静默令", "cost": 350.0, "effect": {"suspicion_growth": -0.20}},
    "canned_food_diplomacy": {"name": "罐罐外交", "cost": 400.0, "effect": {"black_market_fee": -0.20}},
    "tail_balance_act": {"name": "尾巴平衡法案", "cost": 450.0, "effect": {"vehicle_armor": 0.10}},
    "planetary_greening_act": {"name": "行星绿化法案", "cost": 500.0, "effect": {"cat_capacity": 0.10}},
    "interstellar_broadcast_act": {"name": "星际广播法案", "cost": 600.0, "effect": {"intel_decrypt": 0.10}},
    "permanent_purr_field": {"name": "永久呼噜场", "cost": 800.0, "effect": {"morale": 0.10}},
}
DOCTRINES_TOTAL_UNITY = sum(item["cost"] for item in DOCTRINES.values())  # 3,600

# ======================================================================
# §16 星球特色小游戏矩阵
# ======================================================================
MINIGAME_SPECS: dict[str, dict] = {
    "cipher_decode": {
        "name": "密电译码",
        "planet_id": 2,
        "code_length": 4,
        "symbol_count": 6,
        "daily_quota": 3,
        "intel_level_gain": 0.08,
    },
    "vein_scan": {
        "name": "矿脉扫描",
        "planet_id": 3,
        "grid_size": 8,
        "vein_count": 12,
        "quota_max": 5,
        "quota_regen_seconds": 1800,
    },
    "forge_recipe": {
        "name": "熔炉配比",
        "planet_id": 1,
        "scrap_cost_per_round": 20.0,
        "smelt_speed_bonus_per_recipe": 0.05,
        "recipe_bonus_cap": 0.50,
        "recipe_cap": 10,
    },
}

#: 冷启动：手点废墟 5 次 = 第一座纸箱窝
COLD_START_SCRAP_PER_CLICK = 1.0
COLD_START_CLICKS = 5
COLD_START_HOUSING_BOX_COST = 5.0
#: 冷启动手点废墟次数上限（够造 窝5 + 农田10 + 操作台8 + 第二座窝6 = 29，留 11 次余量；防无限手点腱鞘炎）
COLD_START_MAX_MANUAL_CLICKS = 40
#: 手点废墟的关闭条件：有拾荒猫在岗 ⇒ 自动化真正跑起来，手点入口关闭（没猫上工时保留兜底手点路径，防死档）
COLD_START_EXIT_ON_DUTY_JOB = "scavenger"
#: 第一座纸箱窝建成 ⇒ 第一只折耳流浪猫入驻（GDD §1.3 阶段二：总猫口由 0 变为 1）
FIRST_HOUSING_BOX_BRINGS_FIRST_CAT = True

#: 迟滞换班默认策略（WBS C3：高位转出线 80% / 低位回防线 20% / 一次转移 2 只）
DEFAULT_HYSTERESIS_POLICY: dict[str, float | int | bool] = {
    "enabled": True,
    "upper": 0.8,
    "lower": 0.2,
    "shift": 2,
}


# ======================================================================
# 纯公式助手（与上面常量同源）
# ======================================================================
def catnip_net_rate(
    farmers: int,
    total_cats: int,
    *,
    production_multiplier: float = 1.0,
) -> float:
    """净猫薄荷产出率：0.2 × 农夫 − 0.05 × 总猫口。"""
    return (
        farmers * FARMER_CATNIP_PER_SEC * production_multiplier
        - total_cats * CATNIP_CONSUME_PER_CAT_PER_SEC
    )


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
