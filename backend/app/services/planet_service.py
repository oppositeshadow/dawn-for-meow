"""星球服务（模块 K3 + LLM 场景 1）：登录新行星时生成生态环境与词缀。

口径（代码结构稿 §6.2 场景 1）：**每颗星球只调用 1 次** LLM，失败自动走本地生态标签池（玩家零感知）；
母星（planet 0）的生态标签是手工定稿，不调用 LLM。
"""

from __future__ import annotations

import logging
import hashlib
import copy
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import balance as B
from app.core.balance import RESOURCE_CAPS
from app.core.errors import BadRequest, InsufficientResource, NotFound
from app.models import BossState, ColonyState, GardenState, LaborBucket, MilitaryState, PlanetState
from app.schemas.planet_agent import PlanetBiome
from app.services.game_init_service import now_timestamp
from app.services.llm_service import LlmScene, get_llm_service

logger = logging.getLogger("dawn_meow.planet")

#: 迁猫时的出发/到达星球名称，仅用于文案
def _route_id(from_planet: int, to_planet: int, departed_at: int) -> str:
    return f"route_{from_planet}_{to_planet}_{departed_at}"


def is_raided(slot_id: int, route_id: str, *, chance: float) -> bool:
    """确定性判定是否被劫掠（同一趟航线结果唯一，便于复现与测试）。"""
    digest = hashlib.sha256(f"{slot_id}:{route_id}".encode("utf-8")).hexdigest()[:8]
    return int(digest, 16) / 0xFFFFFFFF < chance


def raid_chance_for_route(
    route: Mapping[str, Any], *, now: int, officer_count: int, rage: float
) -> float:
    """**一趟航线**当前的被劫掠概率（《数值平衡表》§15.3 + §15.5）。

    * 基础 8%；欧米伽 `rage ≥ 60` 时翻倍（§15.3）；
    * **星球周期**（§15.5）：碎星流「来袭」相位把该区域的劫掠概率 ×2——两端取**较大者**
      （母星恒为 1.0，实际就是"外星球那一端"）：往星带运货会被碎石流刮到，从星带往外运同样要穿过那片区域；
    * 星际物流调度官每只在岗 −10%（最后一步乘，封底 0，§15.1）。
    """
    raid_factor = route_raid_factor(route, now)
    base = (
        B.STAR_ROUTE_RAID_CHANCE
        * (2.0 if rage >= B.STAR_ROUTE_RAID_RAGE_THRESHOLD else 1.0)
        * float(raid_factor)
    )
    return B.logistics_raid_chance(officer_count, base)


def route_raid_factor(route: Mapping[str, Any], now: int) -> float:
    """该航线当前受**星球周期**影响的劫掠倍率（母星恒 1.0；碎星流「来袭」= 2.0，§15.5）。

    两端取较大者：往星带运货会被碎石流刮到，从星带往外运同样要穿过那片区域。
    """
    return max(
        B.planet_cycle(int(route.get("from_planet", B.HOME_PLANET_ID)), now)["raid_multiplier"],
        B.planet_cycle(int(route.get("to_planet", B.HOME_PLANET_ID)), now)["raid_multiplier"],
    )


def _tech_name(tech_id: str) -> str:
    """科技中文名（给玩家的引导文案用；种子是唯一真值，查不到就退回 id）。"""
    from app.core.seed_loader import tech_defs_planet0

    for item in tech_defs_planet0():
        if item.get("tech_id") == tech_id:
            return str(item.get("tech_name") or tech_id)
    return tech_id


def describe_route_events(events: Sequence[Mapping[str, Any]]) -> list[str]:
    """把航线事件翻成《离线休整报表》文案：到货 / 被劫掠（带原因与解锁指引，§15.6）。

    航线到点是在读档、看星图时结算的 —— 也就是**玩家不在场时发生的事**，
    不写进报表就等于没发生（skill §6 的"错过成本"检查表）。
    """
    lines: list[str] = []
    for event in events:
        kind = event.get("type")
        if kind == "ROUTE_ARRIVED":
            planet_name = B.PLANETS.get(int(event.get("to_planet", 0)), "目标星球")
            cargo = event.get("cargo") or {}
            cargo_text = "、".join(
                f"{B.RESOURCE_LABELS.get(key, key)} ×{float(value):g}" for key, value in cargo.items()
            )
            lines.append(
                f"跨星航线抵达【{planet_name}】：{int(event.get('cat_count', 0))} 只猫"
                + (f" + {cargo_text}" if cargo_text else "")
            )
        elif kind == "ROUTE_RAIDED":
            delay_minutes = int(event.get("delay_seconds", 0)) // 60
            note = str(event.get("note") or "")
            lines.append(
                f"跨星航线被劫掠：延误 {delay_minutes} 分钟，猫一只没丢"
                + (f"；{note}" if note else "")
            )
    return lines


async def route_slots(session: AsyncSession, slot_id: int, to_planet: int) -> int:
    """该星球当前可用的航线带宽：基础 2 条 + 星际物流调度官每只 +1，封顶 4。"""
    bucket = await session.get(LaborBucket, (slot_id, B.HOME_PLANET_ID, "logistics"))
    officers = int(bucket.cat_count) if bucket else 0
    return min(B.STAR_ROUTE_MAX_SLOTS, B.STAR_ROUTE_BASE_SLOTS + officers)


async def migrate_cats(
    session: AsyncSession,
    *,
    slot_id: int = B.DEFAULT_SLOT_ID,
    from_planet: int = B.HOME_PLANET_ID,
    to_planet: int,
    count: int,
    now: int | None = None,
    cargo: dict[str, float] | None = None,
) -> dict[str, Any]:
    """派一趟跨星航线把猫口从母星运到外星球（《数值平衡表》§15.3 第②步）。

    * 猫口**出发即离开母星**，抵达时才计入目标星球（在途期间两边都不占用工位）；
    * 带宽：基础 2 条在途航线/星，星际物流调度官每只 +1（封顶 4）；
    * 被劫掠 = **延误 10 分钟**（绝不死猫）。

    `cargo`（可选）：随船携带的物资（默认不载货）。外星球从 0 起步且没有"手点废墟"，
    因此**必须能运物资**才不至于死锁（§15.3 落地补充）；单趟每种物资的上限见
    `STAR_ROUTE_CARGO_PER_TRIP`，超量直接拒绝（不做"静默截断"）。
    """
    if to_planet == from_planet:
        raise BadRequest("BAD_REQUEST", "起点与终点不能是同一颗星球")
    if count < 1 or count > B.STAR_ROUTE_MAX_CATS_PER_TRIP:
        raise BadRequest(
            "BAD_REQUEST", f"单趟可运 1~{B.STAR_ROUTE_MAX_CATS_PER_TRIP} 只猫，收到 {count}"
        )
    target = await session.get(PlanetState, (slot_id, to_planet))
    if target is None:
        raise BadRequest("BAD_REQUEST", f"未知星球 planet_id={to_planet}")
    if not target.unlocked:
        raise BadRequest("PLANET_LOCKED", f"【{B.PLANETS.get(to_planet, to_planet)}】尚未解锁")
    await ensure_star_colony(session, slot_id, to_planet, now=now)

    source = await session.get(ColonyState, (slot_id, from_planet))
    if source is None:
        raise NotFound("COLONY_STATE_NOT_FOUND", f"槽位 {slot_id} 母星没有基地状态")
    if int(source.job_idle) < count:
        raise InsufficientResource(
            detail=f"需要 {count} 只空闲猫，母星当前只有 {int(source.job_idle)} 只（先去工位调度里收回）"
        )

    shipped: dict[str, float] = {}
    officer_bucket = await session.get(LaborBucket, (slot_id, B.HOME_PLANET_ID, "logistics"))
    officer_count = int(officer_bucket.cat_count) if officer_bucket else 0
    for resource, amount in (cargo or {}).items():
        if resource not in B.STAR_ROUTE_CARGO_PER_TRIP:
            raise BadRequest("BAD_REQUEST", f"航线暂不支持运送 {resource}")
        if amount <= 0:
            continue
        # 吞吐：星际物流调度官每只 +20% 货舱容量（§15.1）
        limit = B.logistics_cargo_capacity(officer_count, float(B.STAR_ROUTE_CARGO_PER_TRIP[resource]))
        if amount > limit:
            raise BadRequest(
                "BAD_REQUEST",
                f"单趟最多运 {limit:g} {resource}（基础 {B.STAR_ROUTE_CARGO_PER_TRIP[resource]:g}"
                f"＋调度官 {officer_count} 只 +{int(B.LOGISTICS_THROUGHPUT_PER_OFFICER * 100 * officer_count)}%）"
                f"，收到 {amount:g}",
            )
        if float(getattr(source, resource)) < amount:
            raise InsufficientResource(
                detail=f"母星只有 {float(getattr(source, resource)):g} {resource}，不足以运出 {amount:g}"
            )
        shipped[resource] = round(float(amount), 2)
    for resource, amount in shipped.items():
        setattr(source, resource, round(float(getattr(source, resource)) - amount, 2))

    routes = [dict(item) for item in (target.logistics_routes or [])]
    if len(routes) >= await route_slots(session, slot_id, to_planet):
        raise BadRequest(
            "STAR_ROUTE_SLOTS_FULL",
            f"【{B.PLANETS.get(to_planet, to_planet)}】的在途航线已达带宽上限（{len(routes)} 条），等这趟抵达再安排",
        )

    stamped = now if now is not None else now_timestamp()
    route = {
        "route_id": _route_id(from_planet, to_planet, stamped),
        "from_planet": from_planet,
        "to_planet": to_planet,
        "cat_count": int(count),
        "cargo": shipped,
        "departed_at": stamped,
        "arrives_at": stamped + int(B.STAR_ROUTE_SECONDS),
        "raided": False,
    }
    routes.append(route)
    target.logistics_routes = routes  # JSON 列必须整条替换

    # 出发即离港：母星的猫口立刻减少，抵达时再计入目标星球
    source.total_cats = int(source.total_cats) - count
    source.job_idle = int(source.job_idle) - count
    await session.flush()
    logger.info(
        "跨星航线出发：slot=%s %s→%s %s 只，预计 %s 秒抵达",
        slot_id, from_planet, to_planet, count, int(B.STAR_ROUTE_SECONDS),
    )
    return {
        "route": route,
        "source_total_cats": int(source.total_cats),
        "source_idle_cats": int(source.job_idle),
        "eta_seconds": int(B.STAR_ROUTE_SECONDS),
        "cargo": shipped,
        "slots_used": len(routes),
        "slots_total": await route_slots(session, slot_id, to_planet),
    }


async def settle_routes(
    session: AsyncSession, slot_id: int, *, now: int | None = None
) -> list[dict[str, Any]]:
    """结算所有星球的在途航线：到点交付猫口，被劫掠则延误 10 分钟。

    被劫掠判定**每趟只做一次**（首次到点）：命中的船延误 10 分钟，期满照常交付；
    重复判定会让同一 `route_id` 每 10 分钟再命中一次 ⇒ 船永远到不了（详见下方注释）。
    """
    stamped = now if now is not None else now_timestamp()
    boss = await session.get(BossState, slot_id)
    rage = float(boss.rage) if boss else 0.0
    # 星际物流调度官：每只在岗 −10% 被劫掠概率（§15.1）；具体概率按**每条航线**现算
    # （星球周期会改它：碎星流来袭时 ×2，见 raid_chance_for_route）
    officers = (await session.get(LaborBucket, (slot_id, B.HOME_PLANET_ID, "logistics")))
    officer_count = int(officers.cat_count) if officers else 0

    events: list[dict[str, Any]] = []
    rows = (
        await session.execute(select(PlanetState).where(PlanetState.slot_id == slot_id))
    ).scalars().all()
    for planet in rows:
        routes = [dict(item) for item in (planet.logistics_routes or [])]
        if not routes:
            continue
        pending: list[dict[str, Any]] = []
        for route in routes:
            if int(route.get("arrives_at", 0)) > stamped:
                pending.append(route)
                continue
            route_id = str(route.get("route_id") or "")
            # 判定**只在第一次到点时做一次**：`is_raided` 的哈希只依赖 (slot, route_id)，
            # 若每次到点都重判，命中的那趟会每 10 分钟再命中一次 ⇒ "延误 10 分钟"变成永久卡住
            # （猫和货锁死在途，还白占一条航线带宽）。延误期满 = 照常交付（§15.3 口径）。
            if not route.get("raided") and is_raided(
                slot_id,
                route_id,
                chance=raid_chance_for_route(
                    route, now=stamped, officer_count=officer_count, rage=rage
                ),
            ):
                route["raided"] = True
                route["arrives_at"] = stamped + int(B.STAR_ROUTE_RAID_DELAY_SECONDS)
                # 事后点名（§15.6）：没点观测科技的玩家也要能看见因果，并知道去哪解锁
                factor = route_raid_factor(route, stamped)
                note = ""
                if factor > 1.0:
                    watcher = _tech_name(B.PLANET_CYCLE_REVEAL_TECHS[0])
                    note = (
                        f"碎星流·来袭期间穿越星带（被劫掠概率 ×{factor:g}）："
                        f"研究【{watcher}】就能提前看到潮汐，避开这段"
                    )
                    route["raid_note"] = note  # 前端在"在途"那行直接显示
                pending.append(route)
                events.append(
                    {
                        "type": "ROUTE_RAIDED",
                        "route_id": route_id,
                        "delay_seconds": int(B.STAR_ROUTE_RAID_DELAY_SECONDS),
                        "note": note,
                    }
                )
                continue
            target = await session.get(ColonyState, (slot_id, int(route["to_planet"])))
            if target is None:  # 目标星球基地被拆（理论上不会发生）⇒ 猫口回母星，绝不凭空消失
                target = await session.get(ColonyState, (slot_id, B.HOME_PLANET_ID))
            if target is not None:
                target.total_cats = int(target.total_cats) + int(route["cat_count"])
                target.job_idle = int(target.job_idle) + int(route["cat_count"])
                # 随船物资一起交付（按目标星球的仓储上限夹紧）
                for resource, amount in (route.get("cargo") or {}).items():
                    cap = float(getattr(target, f"{resource}_max", amount))
                    setattr(
                        target,
                        resource,
                        round(min(cap, float(getattr(target, resource)) + float(amount)), 2),
                    )
            events.append(
                {"type": "ROUTE_ARRIVED", "route_id": route_id, "to_planet": int(route["to_planet"]),
                 "cat_count": int(route["cat_count"]), "cargo": dict(route.get("cargo") or {})}
            )
        planet.logistics_routes = pending
    await session.flush()
    return events

#: 本地兜底生态池（LLM 不可用时使用；加内容只改这里）
FALLBACK_BIOMES: dict[int, PlanetBiome] = {
    1: PlanetBiome(biome_tag="赤色熔炉带", affixes=["岩浆潮汐", "地热脉动"]),
    2: PlanetBiome(biome_tag="永冻镜海", affixes=["极夜长哨", "冰下回声"]),
    3: PlanetBiome(biome_tag="碎星环带", affixes=["氦雨", "失控轨道"]),
}

SYSTEM_PROMPT = (
    "你是《喵星破晓》的星区推演导演。为下面的行星起一个 4~10 字的生态标签，"
    "再给 1~3 条 2~6 字的行星词缀（可以是气候、地貌、危险或机遇）。"
    "语气冷硬、有科幻质感，不要出现现实地名与品牌。只输出 JSON。"
)

#: 外星球建行时必须齐活的行（《代码结构稿》§2.5 / §7 作用域矩阵的"每星球独立"部分）。
#: 声明在这里，由 `tests/test_scope_matrix.py` 断言"声明 == 实际建出的行"——
#: 以后往建行流程里加表却忘了同步这张清单，测试会直接红（真机曾因为只建 3 张表而读档 404）。
STAR_COLONY_TABLES: tuple[str, ...] = (
    "colony_state",
    "labor_buckets",
    "facility_state",
    "military_state",
    "garden_state",
)


async def ensure_star_colony(
    session: AsyncSession, slot_id: int, planet_id: int, *, now: int | None = None
) -> dict[str, Any] | None:
    """外星球基地建行（《数值平衡表》§15.3 第①步 / 《数据库设计定稿》§2.5）。

    * **幂等**：已有 `colony_state` 行则直接返回 `None`（不重置任何进度、不重设锚点）；
    * **锚点铁律**：`last_tick_time` 写**解锁那一刻**，不能写 0——写 0 会把解锁之前的历史时间当成离线时长补算；
    * 初始资源 / 设施 / 猫口全 0（沿用母星冷启动经验：想要资源就靠跨星航线运进来）。
    """
    if planet_id == B.HOME_PLANET_ID:
        return None
    if planet_id not in B.PLANETS:
        raise NotFound("BAD_REQUEST", f"未知星球 planet_id={planet_id}")
    if await session.get(ColonyState, (slot_id, planet_id)) is not None:
        return None

    from app.services import colony_service

    stamped = now if now is not None else now_timestamp()
    session.add(
        ColonyState(
            slot_id=slot_id,
            planet_id=planet_id,
            last_tick_time=stamped,
            catnip=0.0, catnip_max=RESOURCE_CAPS["catnip"],
            scrap=0.0, scrap_max=RESOURCE_CAPS["scrap"],
            chips=0.0, chips_max=RESOURCE_CAPS["chips"],
            alloys=0.0, alloys_max=RESOURCE_CAPS["alloys"],
            battery=0.0, battery_max=RESOURCE_CAPS["battery"],
            lube=0.0, lube_max=RESOURCE_CAPS["lube"],
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
    await session.flush()
    # 工种分桶与设施行按母星同构建行（复用"加设施/加职业不用改表"的同步函数）
    labor_rows = await colony_service.sync_labor_rows(session, slot_id, planet_id)
    facility_rows = await colony_service.sync_facility_rows(session, slot_id, planet_id)
    # 军备态与猫草实验舱是**按星球独立**的表：缺行会让读档/军备接口报 404（真机踩到过）
    from app.services.game_init_service import DEFAULT_SECURITY_POLICY, build_initial_grid
    from app.core.seed_loader import load_seed

    if await session.get(MilitaryState, (slot_id, planet_id)) is None:
        session.add(
            MilitaryState(
                slot_id=slot_id,
                planet_id=planet_id,
                last_tick_time=stamped,
                hangar_capacity=12,
                laser_turrets=0,
                cruise_missiles=0,
                decoy_count=0,
                # 深拷贝（同 game_init_service：浅拷贝会让嵌套列表/字典在多档之间共享）
                security_policy=copy.deepcopy(DEFAULT_SECURITY_POLICY),
                hospital_queue=[],
                active_expeditions=[],
            )
        )
    if await session.get(GardenState, (slot_id, planet_id)) is None:
        session.add(
            GardenState(
                slot_id=slot_id,
                planet_id=planet_id,
                last_tick_time=stamped,
                grid_size=B.GARDEN_INITIAL_GRID_SIZE,
                unlocked_cells=B.GARDEN_INITIAL_UNLOCKED_CELLS,
                current_medium="STERILE",
                mechanical_arm_enabled=False,
                auto_protect_unknown=True,
                grid_data=build_initial_grid(),
                unlocked_seed_ids=list(load_seed("cat_plants.json").get("starter_seeds", [])),
                codex_papers={},
            )
        )
    await session.flush()
    logger.info(
        "外星球基地建行：slot=%s planet=%s 工种 %s 行 / 设施 %s 行 / 锚点 %s",
        slot_id, planet_id, len(labor_rows), len(facility_rows), stamped,
    )
    return {
        "planet_id": planet_id,
        "created_at": stamped,
        "labor_rows": len(labor_rows),
        "facility_rows": len(facility_rows),
        "tables": list(STAR_COLONY_TABLES),
    }


async def ensure_biome(
    session: AsyncSession, slot_id: int, planet_id: int
) -> dict[str, Any] | None:
    """给一颗外星球补上生态标签（幂等：已有标签则跳过，也就不再调用 LLM）。"""
    planet = await session.get(PlanetState, (slot_id, planet_id))
    if planet is None:
        raise NotFound("BAD_REQUEST", f"未知星球 planet_id={planet_id}")
    if planet_id == B.HOME_PLANET_ID or planet.biome_tag:
        # 生态标签已存在时不再调 LLM，但仍要保证特化科技树已铺好（幂等、0 Token）
        if planet_id != B.HOME_PLANET_ID:
            from app.services import planet_tech_service

            specialized = await planet_tech_service.ensure_specialized_techs(session, slot_id, planet_id)
            if specialized:
                return {
                    "planet_id": planet_id,
                    "biome_tag": planet.biome_tag,
                    "affixes": [],
                    "source": None,
                    "specialized_techs": specialized,
                    "usage": {},
                }
        return None

    fallback = FALLBACK_BIOMES.get(planet_id) or PlanetBiome(biome_tag="未命名星域", affixes=[])
    biome = fallback
    source = "FALLBACK"
    usage: dict[str, Any] = {}
    service = get_llm_service()
    if service.configured:
        result = await service.complete_json(
            scene=LlmScene.PLANET_ENTRY,
            system=SYSTEM_PROMPT,
            user=f"星球代号：{B.PLANETS.get(planet_id)}（planet_id={planet_id}）。请输出 JSON："
            '{"biome_tag": "赤色熔炉带", "affixes": ["岩浆潮汐", "地热脉动"]}',
            max_tokens=200,
            temperature=0.9,
        )
        if result.ok and isinstance(result.payload, dict):
            try:
                biome = PlanetBiome.model_validate(result.payload)
                source = "LLM"
            except Exception as exc:  # 防火墙 1 拦截 ⇒ 走本地生态池
                logger.warning("行星生态未通过 Pydantic 防火墙：%s", exc)
        usage = result.usage.to_dict()

    label = biome.biome_tag if not biome.affixes else f"{biome.biome_tag} · {'、'.join(biome.affixes[:2])}"
    planet.biome_tag = label[:64]
    # LLM 场景 2：铺该星球的特化科技树（首次登录 1 次批量生成，失败走本地卡池）
    from app.services import planet_tech_service

    specialized = await planet_tech_service.ensure_specialized_techs(session, slot_id, planet_id)
    await session.flush()
    logger.info("行星生态标签生成：slot=%s planet=%s source=%s tag=%s", slot_id, planet_id, source, planet.biome_tag)
    return {
        "planet_id": planet_id,
        "biome_tag": planet.biome_tag,
        "affixes": list(biome.affixes),
        "source": source,
        "specialized_techs": specialized,
        "usage": usage,
    }
