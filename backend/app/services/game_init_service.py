"""新游戏初始化流程（《数据库设计定稿》§2.4）。

新游戏创建时执行一次初始化事务：写入 save_slot、career_stats、planet_state（母星解锁+激活）、
colony_state（资源与人口全 0，等玩家手点废墟攒 5 废铁造第一个纸箱）、labor_buckets（按 jobs.json
建母星 5 工种行）、facility_state（按 facilities.json 建全部设施行，等级 0）、garden_state、
darknet_state、military_state、boss_state、minigame_state（按 minigames.json 建行），并把
techs_planet0.json 的母星 19 节点以 LOCKED 状态写入 tech_records。
"""

from __future__ import annotations

import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import balance as B
from app.core.balance import RESOURCE_CAPS
from app.core.errors import BadRequest, Conflict
from app.core.seed_loader import minigame_defs, tech_defs_planet0
from app.models import (
    BossState,
    CareerStats,
    ColonyState,
    DarknetState,
    FacilityState,
    GardenState,
    LaborBucket,
    MilitaryState,
    MinigameState,
    PlanetState,
    SaveSlot,
    TechRecord,
)
from app.models.tech import TechStatus

#: 母星生态标签为手工定稿（外星球才由 LLM 生成）
HOME_BIOME_TAG = "翠绿生态摇篮·07 号地下避难所"

DEFAULT_SECURITY_POLICY: dict = {
    "p1_use_decoy": True,
    "p2_use_vehicle": True,
    "p3_go_dark": True,
    "false_alarm_cooldown_until": None,
}

DEFAULT_BOMBARDMENT_STATE: dict = {
    "active": False,
    "damaged_facilities": [],
    "scattered_cats": 0,
    "regroup_progress": 0,
    "cooldown_until": None,
}


def now_timestamp() -> int:
    """统一时间基准：绝对秒级时间戳（数据库设计定稿 §2.3）。"""
    return int(time.time())


def build_initial_grid() -> list[dict]:
    """固定 7×7 场地，开局只解锁中央 3×3 = 9 格（数值平衡表 §10）。"""
    size = B.GARDEN_GRID_SIZE
    half = B.GARDEN_INITIAL_GRID_SIZE // 2
    center_start = size // 2 - half
    center_end = center_start + B.GARDEN_INITIAL_GRID_SIZE - 1
    tiles: list[dict] = []
    for y in range(size):
        for x in range(size):
            unlocked = center_start <= x <= center_end and center_start <= y <= center_end
            tiles.append(
                {
                    "x": x,
                    "y": y,
                    "unlocked": unlocked,
                    "seed_id": None,
                    "stage": None,
                    "age": 0.0,
                }
            )
    return tiles


def starter_stock_quotes() -> list[dict]:
    """深网大盘初始快照（数值平衡表 §11.1 的四只标的）。

    初始趋势强度（+20 / +5 / −10 / 0）→ 一阶胜率 forecast = (50 + 趋势) / 100；
    真正的行情演化由 stock_engine（P1·模块 I）负责，这里只落种子。
    """
    quotes: list[dict] = []
    for stock_id, spec in B.STARTER_STOCKS.items():
        forecast = B.clamp((50.0 + spec["trend_strength"]) / 100.0, 0.0, 1.0)
        quotes.append(
            {
                "stock_id": stock_id,
                "name": spec["name"],
                "price": spec["price"],
                "forecast": round(forecast, 4),
                "target_forecast": round(forecast, 4),
                "momentum": 0.0,
                "volatility": spec["volatility"],
                "spread": spec["spread"],
                "trend_strength": spec["trend_strength"],
            }
        )
    return quotes


async def create_new_game(
    session: AsyncSession,
    slot_id: int = B.DEFAULT_SLOT_ID,
    *,
    now: int | None = None,
) -> SaveSlot:
    """执行一次新游戏初始化事务（幂等保护：槽位已存在则报 409）。"""
    if slot_id not in B.SLOT_IDS:
        raise BadRequest("BAD_REQUEST", f"slot 必须为 {B.SLOT_IDS} 之一，收到 {slot_id}")
    if await session.get(SaveSlot, slot_id) is not None:
        raise Conflict("SAVE_SLOT_EXISTS", f"槽位 {slot_id} 已有存档")

    now = now if now is not None else now_timestamp()

    save = SaveSlot(
        slot_id=slot_id,
        slot_name=None,
        save_version=B.SAVE_VERSION,
        checksum=None,
        active_planet_id=B.HOME_PLANET_ID,
        playtime_seconds=0,
        unity=0.0,
        doctrines={},
    )
    session.add(save)

    # 生涯统计：全 0
    session.add(CareerStats(slot_id=slot_id))

    # 星图：四颗天体都建行；母星解锁并激活，其余锁定（等待 LLM 生成生态标签）
    for planet_id in B.PLANETS:
        is_home = planet_id == B.HOME_PLANET_ID
        session.add(
            PlanetState(
                slot_id=slot_id,
                planet_id=planet_id,
                unlocked=is_home,
                unlocked_at=now if is_home else None,
                biome_tag=HOME_BIOME_TAG if is_home else None,
                is_active=is_home,
                logistics_routes=[],
            )
        )

    # 基地核心：全 0（等玩家手点废墟攒 5 废铁造第一个纸箱）
    session.add(
        ColonyState(
            slot_id=slot_id,
            planet_id=B.HOME_PLANET_ID,
            last_tick_time=now,
            catnip=0.0,
            catnip_max=RESOURCE_CAPS["catnip"],
            scrap=0.0,
            scrap_max=RESOURCE_CAPS["scrap"],
            chips=0.0,
            chips_max=RESOURCE_CAPS["chips"],
            alloys=0.0,
            alloys_max=RESOURCE_CAPS["alloys"],
            battery=0.0,
            battery_max=RESOURCE_CAPS["battery"],
            lube=0.0,
            lube_max=RESOURCE_CAPS["lube"],
            power_net=0.0,
            battery_kwh=0.0,
            battery_kwh_max=B.BATTERY_KWH_MAX,
            total_cats=0,
            birth_progress=0.0,
            job_idle=0,
            suspicion=0.0,
            labor_automation_policy=None,
            smelt_automation_policy=None,
        )
    )

    # 工种分桶：母星 5 工种，计数 0
    for job_id in B.PLANET_JOBS:
        session.add(
            LaborBucket(
                slot_id=slot_id, planet_id=B.HOME_PLANET_ID, job_id=job_id, cat_count=0
            )
        )

    # 设施状态：按 facilities.json 建全部设施行，等级 0
    for facility_id in B.FACILITY_IDS:
        session.add(
            FacilityState(
                slot_id=slot_id, planet_id=B.HOME_PLANET_ID, facility_id=facility_id, level=0
            )
        )

    # 科技树：母星 19 节点全部 LOCKED
    for node in tech_defs_planet0():
        session.add(
            TechRecord(
                slot_id=slot_id,
                planet_id=B.HOME_PLANET_ID,
                tech_id=node["tech_id"],
                tech_name=node["tech_name"],
                parent_ids=list(node.get("parent_ids", [])),
                tier=int(node.get("tier", 1)),
                node_order=int(node.get("node_order", 0)),
                status=TechStatus.LOCKED,
                current_progress=0.0,
                target_cost=float(node.get("target_cost", 0.0)),
                is_agent_generated=False,
                flavor_text=node.get("flavor_text"),
                mechanic_type=node.get("mechanic_type"),
                buff_payload=node.get("buff_payload"),
            )
        )

    # 水培实验室：固定 7×7，开局中央 3×3
    session.add(
        GardenState(
            slot_id=slot_id,
            planet_id=B.HOME_PLANET_ID,
            last_tick_time=now,
            grid_size=B.GARDEN_INITIAL_GRID_SIZE,
            unlocked_cells=B.GARDEN_INITIAL_UNLOCKED_CELLS,
            current_medium="STERILE",
            mechanical_arm_enabled=False,
            auto_protect_unknown=True,
            grid_data=build_initial_grid(),
            unlocked_seed_ids=[],
        )
    )

    # 深网：大盘种子 + 空持仓
    session.add(
        DarknetState(
            slot_id=slot_id,
            last_tick_time=now,
            byte_credits=0.0,
            exposure=0.0,
            burner_id="robot_4a932cz",
            has_4s_data=False,
            stocks_data=starter_stock_quotes(),
            kline_history=[],
            positions=[],
            short_contracts=[],
            limit_orders=[],
            black_market_items=[],
            pending_deliveries=[],
            whale_events=[],
            last_post_time=None,
        )
    )

    # 军备：机库 12 机位（母星），池化编制全 0
    session.add(
        MilitaryState(
            slot_id=slot_id,
            planet_id=B.HOME_PLANET_ID,
            last_tick_time=now,
            hangar_capacity=12,
            laser_turrets=0,
            cruise_missiles=0,
            decoy_count=0,
            security_policy=dict(DEFAULT_SECURITY_POLICY),
            hospital_queue=[],
            active_expeditions=[],
        )
    )

    # 欧米伽后台演化：威胁 1 / 扩张 1.0 / 舰队 500
    session.add(
        BossState(
            slot_id=slot_id,
            last_tick_time=now,
            threat_level=1,
            expansion_rate=1.0,
            fleet_strength=500.0,
            suspicion_toward_player=0.0,
            rage=0.0,
            convoy_ends_at=None,
            factory_frozen_until=None,
            intel_level=0.0,
            raid_ends_at=None,
            raid_target_planet=None,
            satellite_count=0,
            bombardment_state=dict(DEFAULT_BOMBARDMENT_STATE),
        )
    )

    # 小游戏：按 minigames.json 建行（解锁星球的玩法行一并落库，未解锁星球自然不可进入）
    for game in minigame_defs():
        session.add(
            MinigameState(
                slot_id=slot_id,
                planet_id=int(game["planet_id"]),
                minigame_id=game["minigame_id"],
                state=dict(game.get("initial_state", {})),
                last_tick_time=now,
                best_score=0.0,
                play_count=0,
            )
        )

    await session.flush()
    return save
