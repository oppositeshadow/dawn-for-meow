"""确定性离线收益结算引擎（模块 A / B / C 的数学内核）。

口径来源：`数值平衡表.md` §3（资源与生产速率）/ §4（人口与繁育）/ §7（电力）/ §8（警戒度）。
职责边界：

* 本引擎是**纯函数**，只做数学推演，不触碰数据库；
* 前端 100ms 插值只负责显示，快照与读档时一律由本引擎按 `last_tick_time` 重算（对账协议）；
* 时间回拨（Δt < 0）**不做补偿**：返回零增量并置 `clock_anomaly=True`，由服务层把
  `last_tick_time` 重置为当前时间。

输入 state（扁平字典，字段名与代码结构稿 §2.1 示例一致）::

    {
      "catnip": 0.0, "catnip_max": 200.0,
      "scrap": 0.0, "scrap_max": 200.0,
      "chips": 0.0, "chips_max": 100.0,
      "alloys": 0.0, "alloys_max": 50.0,
      "battery": 0.0, "battery_max": 50.0,
      "lube": 0.0, "lube_max": 50.0,
      "cats_total": 1, "max_cat_capacity": 2, "birth_progress": 0.0,
      "farmers": 1, "scavengers": 0, "geeks": 0, "power_runners": 0,
      "facilities": {"housing_box": 2, "farm_plot": 1, ...},
      "battery_kwh": 0.0, "battery_kwh_max": 200.0,
      "suspicion": 0.0,
      "production_multiplier": 1.0,       # 政令/科技加成（默认 1.0）
      "breeding_rate_multiplier": 1.0,    # 猫爬架公寓等加成（默认 1.0）
      "suspicion_growth_multiplier": 1.0, # 午睡静默令等（默认 1.0）
      "silent_grass_count": 0,            # 在田消音绒草株数
      "expedition_active": False          # 是否有废墟出勤在途
    }
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from app.core import balance as B

RESOURCE_PRECISION = 2   # 资源存量保留 2 位小数（数据库设计定稿 §8.4）
PROGRESS_PRECISION = 4   # 进度类保留 4 位小数

#: 离线会自然增长/消耗的资源（合金、电池、润滑脂仍须手动生产）
OFFLINE_FLOW_RESOURCES = ("catnip", "scrap", "chips")


def _num(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if result == result else default  # 过滤 NaN


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def power_balance(
    facilities: Mapping[str, int],
    labor: Mapping[str, int],
    total_cats: int,
    *,
    induction_furnaces: int = 0,
    garden_power_kw: float = 0.0,
) -> dict[str, Any]:
    """净电力平衡（kW，流量口径，数值平衡表 §7.1）。

    发电：踩轮猫 +5 kW/只、太阳能集热板 +8 kW/座；
    负载：图灵终端 −6 kW/台、高频感应电炉 −10 kW/座、生活供暖 −2 kW/10 只猫。
    """
    gen = (
        _int(labor.get("power_runner", 0)) * B.POWER_RUNNER_KW
        + _int(facilities.get("solar_panel", 0)) * B.SOLAR_PANEL_KW
        + _num(garden_power_kw)  # 在田光环：荧光苔藓 +5 kW/株
    )
    load = (
        _int(facilities.get("turing_terminal", 0)) * B.TURING_TERMINAL_LOAD_KW
        + _int(induction_furnaces) * B.INDUCTION_FURNACE_LOAD_KW
        + (max(0, _int(total_cats)) // B.LIVING_HEAT_CATS_PER_UNIT) * B.LIVING_HEAT_KW
    )
    net = gen - load
    return {
        "gen_kw": round(gen, PROGRESS_PRECISION),
        "load_kw": round(load, PROGRESS_PRECISION),
        "net_kw": round(net, PROGRESS_PRECISION),
        # 欠载：图灵终端强制断电（科研产出归 0）、高级工坊停工
        "blackout": net < 0,
    }


def suspicion_rate_per_second(
    facilities: Mapping[str, int],
    *,
    expedition_active: bool = False,
    silent_grass_count: int = 0,
    suspicion_growth_multiplier: float = 1.0,
    active_facilities: int | None = None,
    base_noise: float | None = None,
    garden_suspicion_per_sec: float | None = None,
) -> float:
    """警戒度净变化率（点/s，数值平衡表 §8.2）。"""
    if active_facilities is None:
        active_facilities = sum(1 for level in facilities.values() if _int(level) > 0)
    acoustic = B.acoustic_noise_multiplier(_int(facilities.get("acoustic_layer", 0)))
    base = B.SUSPICION_BASE_NOISE_PER_SEC if base_noise is None else base_noise
    rate = (
        base
        + B.SUSPICION_PER_ACTIVE_FACILITY_PER_SEC * active_facilities
    ) * acoustic * _num(suspicion_growth_multiplier, 1.0)
    if expedition_active:
        rate += B.SUSPICION_EXPEDITION_PER_SEC
    else:
        rate -= B.SUSPICION_IDLE_DECAY_PER_SEC
    if garden_suspicion_per_sec is None:
        rate += B.GARDEN_SILENT_GRASS_SUSPICION_PER_SEC * _int(silent_grass_count)
    else:
        rate += _num(garden_suspicion_per_sec)
    return rate


def integrate_breeding(
    population: int,
    progress: float,
    capacity: int,
    duration: float,
    *,
    rate_multiplier: float = 1.0,
) -> tuple[int, float]:
    """逻辑斯蒂 S 型繁育积分：dN/dt = r·N·(1 − N/K)，离散进位到 `birth_progress`。

    按 ≤60 秒切片做欧拉积分（与《数值平衡表》§4.1 的离散公式完全一致），
    长时间离线也不会因为"一次跳一大步"而失真；进度池保留小数，绝不每 tick 截断。
    """
    if duration <= 0 or population <= 0 or capacity <= 0 or population >= capacity:
        return population, progress
    r_eff = B.BREEDING_RATE_R * _num(rate_multiplier, 1.0)
    remaining = float(duration)
    while remaining > 1e-9 and population < capacity:
        step = min(remaining, B.BREEDING_INTEGRATION_STEP_SECONDS)
        progress += r_eff * population * (1.0 - population / capacity) * step
        remaining -= step
        # 1e-9 容差：整除型进度（如 200 秒恰好满 1.0）不因浮点误差而"永远涨不满"
        if progress >= 1.0 - 1e-9:
            born = min(int(progress + 1e-9), capacity - population)
            population += born
            progress -= born
            if population >= capacity:
                # 承载力已满：清空进度池，避免"饱和后瞬间连生"
                progress = 0.0
                break
    return population, progress


def _snapshot_state(state: Mapping[str, Any]) -> dict[str, float]:
    """抽取参与结算的资源/进度当前值（用于报表的 final 部分）。"""
    return {
        "catnip": _num(state.get("catnip")),
        "scrap": _num(state.get("scrap")),
        "chips": _num(state.get("chips")),
        "alloys": _num(state.get("alloys")),
        "battery": _num(state.get("battery")),
        "lube": _num(state.get("lube")),
        "cats_total": _int(state.get("cats_total")),
        "birth_progress": _num(state.get("birth_progress")),
        "battery_kwh": _num(state.get("battery_kwh")),
        "suspicion": _num(state.get("suspicion")),
    }


def _clamp_resource(raw: float, cap: float, key: str, overflowed: list[str]) -> float:
    if raw > cap:
        if key not in overflowed:
            overflowed.append(key)
        return cap
    return max(0.0, raw)


def calculate_offline_progress(
    current_state: Mapping[str, Any],
    elapsed_seconds: float,
) -> dict[str, Any]:
    """按 Δt 一次性补算离线（或两个快照之间）的连续时间积分。

    返回《离线休整报表》结构：净增量、繁育猫口、断粮与爆仓标记、研究产出、
    警戒度变化、电力与电容池变化、时间回拨标记，以及可直接落库的 final 终值。
    """
    elapsed = _num(elapsed_seconds)
    facilities = current_state.get("facilities") or {}
    labor = {
        "farmer": _int(current_state.get("farmers", 0)),
        "scavenger": _int(current_state.get("scavengers", 0)),
        "geek": _int(current_state.get("geeks", 0)),
        "power_runner": _int(current_state.get("power_runners", 0)),
    }
    total_cats = _int(current_state.get("cats_total", 0))
    capacity = _int(current_state.get("max_cat_capacity", 0))
    production_multiplier = _num(current_state.get("production_multiplier"), 1.0) or 1.0
    breeding_multiplier = _num(current_state.get("breeding_rate_multiplier"), 1.0) or 1.0
    suspicion_growth_multiplier = _num(current_state.get("suspicion_growth_multiplier"), 1.0) or 1.0
    expedition_active = bool(current_state.get("expedition_active", False))
    silent_grass_count = _int(current_state.get("silent_grass_count", 0))
    now_ts = _int(current_state.get("now", 0)) or int(time.time())
    policy = current_state.get("security_policy") or {}
    decoy_count = _int(current_state.get("decoy_count", 0))
    cooldown_until = _int(current_state.get("false_alarm_cooldown_until", 0))
    go_dark = bool(current_state.get("go_dark", False))
    cooldown_active = bool(cooldown_until) and now_ts < cooldown_until
    garden_power_kw = _num(current_state.get("garden_power_kw", 0.0))
    garden_suspicion_raw = current_state.get("garden_suspicion_per_sec")
    garden_suspicion_per_sec = None if garden_suspicion_raw is None else _num(garden_suspicion_raw)

    power = power_balance(
        facilities,
        labor,
        total_cats,
        induction_furnaces=_int(current_state.get("induction_furnaces", 0)),
        garden_power_kw=garden_power_kw,
    )

    report: dict[str, Any] = {
        "elapsed_seconds": round(elapsed, PROGRESS_PRECISION),
        "applied_seconds": 0.0,
        "clock_anomaly": False,
        "is_starving": False,
        "starve_duration_seconds": 0.0,
        "gained_resources": {key: 0.0 for key in OFFLINE_FLOW_RESOURCES},
        "overflowed": [],
        "gained_cats": 0,
        "gained_research": 0.0,
        "suspicion_delta": 0.0,
        "charged_kwh": 0.0,
        "power": power,
        "security_events": [],
        "notes": [],
        "final": _snapshot_state(current_state),
    }

    # ---- Δt ≤ 0：不产生任何变化（A-4 / A-5）----
    if elapsed <= 0:
        if elapsed < 0:
            report["clock_anomaly"] = True
            report["notes"].append(
                f"检测到时间回拨 Δt={elapsed:.3f}s：last_tick_time 重置为当前时间，不做任何补偿"
            )
        else:
            report["notes"].append("无离线收益（Δt = 0）")
        return report

    catnip = _num(current_state.get("catnip"))
    scrap = _num(current_state.get("scrap"))
    chips = _num(current_state.get("chips"))
    catnip_cap = _num(current_state.get("catnip_max"), B.RESOURCE_CAPS["catnip"])
    scrap_cap = _num(current_state.get("scrap_max"), B.RESOURCE_CAPS["scrap"])
    chips_cap = _num(current_state.get("chips_max"), B.RESOURCE_CAPS["chips"])
    birth_progress = _num(current_state.get("birth_progress"))
    battery_kwh = _num(current_state.get("battery_kwh"))
    battery_kwh_max = _num(current_state.get("battery_kwh_max"), B.BATTERY_KWH_MAX)
    suspicion = _num(current_state.get("suspicion"))

    farmers = labor["farmer"]
    scavengers = labor["scavenger"]
    geeks = labor["geek"]
    # 静默关灯（§8.3 优先级 3）：全员停工，农夫/拾荒/科研/踩轮一并锁死
    if go_dark:
        farmers = scavengers = geeks = 0
        labor["power_runner"] = 0
        report["security_events"].append({"type": "GO_DARK_ACTIVE"})
        report["notes"].append("静默关灯中：全员停工，警戒度以 ×5 速率衰减，跌回 20 点后自动复工")

    # ---- 速率（§3.2 净产出公式）----
    catnip_prod_rate = farmers * B.FARMER_CATNIP_PER_SEC * production_multiplier
    catnip_consume_rate = total_cats * B.CATNIP_CONSUME_PER_CAT_PER_SEC
    net_catnip_rate = catnip_prod_rate - catnip_consume_rate

    # 粮食亏空时先耗尽库存，之后进入断粮绝食阶段；
    # 库存本来就是 0（且净产出为负）⇒ 整段都算断粮（A-2 / B-1）。
    if net_catnip_rate < 0:
        time_to_empty = catnip / abs(net_catnip_rate)
    else:
        time_to_empty = float("inf")
    normal_duration = min(elapsed, time_to_empty)
    starve_duration = max(0.0, elapsed - normal_duration)

    # ---- 阶段一：正常运转 ----
    if normal_duration > 0:
        catnip_raw = catnip + net_catnip_rate * normal_duration
        catnip = _clamp_resource(catnip_raw, catnip_cap, "catnip", report["overflowed"])
        if scavengers:
            scrap_raw = scrap + scavengers * B.SCAVENGER_SCRAP_PER_SEC * production_multiplier * normal_duration
            scrap = _clamp_resource(scrap_raw, scrap_cap, "scrap", report["overflowed"])
            chips_raw = chips + scavengers * B.SCAVENGER_CHIPS_PER_SEC * normal_duration
            chips = _clamp_resource(chips_raw, chips_cap, "chips", report["overflowed"])
        if geeks and not power["blackout"]:
            # 欠载时图灵终端强制断电，科研产出归 0
            report["gained_research"] = geeks * B.GEEK_RESEARCH_PER_SEC * production_multiplier * normal_duration
        elif geeks:
            report["notes"].append("净电力为负，图灵终端强制断电：本段离线科研产出归 0")
        total_cats, birth_progress = integrate_breeding(
            total_cats, birth_progress, capacity, normal_duration, rate_multiplier=breeding_multiplier
        )

    # ---- 阶段二：断粮绝食（§3.2 / §4.1）----
    if starve_duration > 0:
        report["is_starving"] = True
        report["starve_duration_seconds"] = round(starve_duration, PROGRESS_PRECISION)
        # 农夫猫保留 30% 求生本能（0.06/s），拾荒/科研/踩轮全部锁死为 0
        catnip_raw = catnip + farmers * B.STARVE_FARMER_CATNIP_PER_SEC * starve_duration
        catnip = _clamp_resource(catnip_raw, catnip_cap, "catnip", report["overflowed"])
        # 繁育 r = 0，且进度池每分钟倒退 0.05（不低于 0）
        birth_progress = max(
            0.0, birth_progress - B.STARVE_BIRTH_PROGRESS_DECAY_PER_SEC * starve_duration
        )
        report["notes"].append(
            f"离线期间断粮 {starve_duration:.1f}s：拾荒/科研/踩轮产出锁死，繁育进度倒退"
        )

    # ---- 繁育出生口统计 ----
    report["gained_cats"] = max(0, total_cats - _int(current_state.get("cats_total", 0)))

    # ---- 警戒度（§8.2）----
    # 静默关灯期间机器全停 ⇒ 设施噪音按 0 计（与断粮同口径）
    active_facilities = 0 if go_dark else sum(1 for level in facilities.values() if _int(level) > 0)
    rate_normal = suspicion_rate_per_second(
        facilities,
        expedition_active=expedition_active,
        silent_grass_count=silent_grass_count,
        suspicion_growth_multiplier=suspicion_growth_multiplier,
        active_facilities=active_facilities,
        base_noise=0.0 if go_dark else None,
        garden_suspicion_per_sec=garden_suspicion_per_sec,
    )
    # 断粮期间全员瘫软停工 ⇒ 设施噪音归 0，只剩基础噪音与自然衰减
    rate_starve = suspicion_rate_per_second(
        facilities,
        expedition_active=False,
        silent_grass_count=silent_grass_count,
        suspicion_growth_multiplier=suspicion_growth_multiplier,
        active_facilities=0,
        base_noise=0.0 if go_dark else None,
        garden_suspicion_per_sec=garden_suspicion_per_sec,
    )
    suspicion_delta = rate_normal * normal_duration + rate_starve * starve_duration
    if go_dark:
        # §8.3 优先级 3：静默关灯期间警戒度以 ×5 速率**衰减**（基础噪音已归 0，净 −0.005 点/s）
        suspicion_delta *= B.GO_DARK_DECAY_MULTIPLIER
    suspicion = B.clamp(suspicion + suspicion_delta, 0.0, B.SUSPICION_MAX)
    suspicion_pre = suspicion  # 冷却衰减前的值：用于满值判定（§8.4）
    # 诱饵误报冷却期内额外加速衰减（§8.4：−0.05 点/s）
    if cooldown_active:
        suspicion = B.clamp(suspicion - B.DECOY_COOLDOWN_DECAY_PER_SEC * elapsed, 0.0, B.SUSPICION_MAX)

    # ---- 满警戒度三级安防预案（§8.3，零弹窗自动处置）----
    rules = {
        "p1_use_decoy": bool(policy.get("p1_use_decoy", True)),
        "p2_use_vehicle": bool(policy.get("p2_use_vehicle", True)),
        "p3_go_dark": bool(policy.get("p3_go_dark", True)),
    }
    if suspicion_pre >= B.SUSPICION_MAX:
        if cooldown_active:
            report["security_events"].append({"type": "ALERT_SUPPRESSED", "reason": "FALSE_ALARM_COOLDOWN"})
        elif rules["p1_use_decoy"] and decoy_count > 0:
            decoy_count -= 1
            suspicion = max(0.0, suspicion - B.DECOY_SUSPICION_REDUCTION)
            cooldown_until = now_ts + B.DECOY_FALSE_ALARM_COOLDOWN_SECONDS
            report["security_events"].append(
                {"type": "DECOY_TRIGGERED", "decoy_left": decoy_count, "suspicion": round(suspicion, 2)}
            )
            report["notes"].append(
                f"发条机械鼠弹射：警戒度 −{B.DECOY_SUSPICION_REDUCTION:.0f}，"
                f"进入 {int(B.DECOY_FALSE_ALARM_COOLDOWN_SECONDS // 60)} 分钟误报冷却"
            )
        elif rules["p2_use_vehicle"] and _int(current_state.get("idle_vehicles", 0)) > 0:
            # 战车截杀需要战斗引擎（模块 G）：如实记录，不伪造胜负
            report["security_events"].append({"type": "P2_PENDING", "reason": "COMBAT_ENGINE_NOT_READY"})
            report["notes"].append("满警戒度：战车截杀需战斗引擎（模块 G），本段先按静默关灯处置")
            if rules["p3_go_dark"]:
                go_dark = True
                report["security_events"].append({"type": "GO_DARK_START"})
        elif rules["p3_go_dark"]:
            go_dark = True
            report["security_events"].append({"type": "GO_DARK_START"})
        else:
            report["security_events"].append({"type": "ALERT_ONLY", "reason": "SECURITY_POLICY_OFF"})
            report["notes"].append("警戒度满 100：安防预案全部关闭，仅记录告警（需手动处理）")

    if go_dark and suspicion <= B.GO_DARK_RECOVER_THRESHOLD:
        go_dark = False
        report["security_events"].append({"type": "GO_DARK_END", "suspicion": round(suspicion, 2)})
        report["notes"].append("静默期结束：警戒度已跌回安全线，全员复工")
    report["decoy_count"] = decoy_count
    report["go_dark"] = go_dark
    report["false_alarm_cooldown_until"] = cooldown_until or None
    report["suspicion_delta"] = round(suspicion - _num(current_state.get("suspicion")), PROGRESS_PRECISION)
    if suspicion >= B.SUSPICION_MAX:
        report["notes"].append("警戒度已达 100：进入侦察扫地机扫描判定（三级安防预案见模块 F）")

    # ---- 蓄电池充电（§7.2：充电速率 = max(0, 净余) × 0.1 kWh/s）----
    surplus = max(0.0, power["net_kw"])
    if surplus > 0 and battery_kwh < battery_kwh_max:
        charge = surplus * B.BATTERY_CHARGE_KWH_PER_SEC_PER_KW * normal_duration
        charged = min(charge, battery_kwh_max - battery_kwh)
        battery_kwh += charged
        report["charged_kwh"] = round(charged, RESOURCE_PRECISION)

    report["applied_seconds"] = round(normal_duration + starve_duration, PROGRESS_PRECISION)
    report["gained_resources"] = {
        "catnip": round(catnip - _num(current_state.get("catnip")), RESOURCE_PRECISION),
        "scrap": round(scrap - _num(current_state.get("scrap")), RESOURCE_PRECISION),
        "chips": round(chips - _num(current_state.get("chips")), RESOURCE_PRECISION),
    }
    report["final"] = {
        "catnip": round(catnip, RESOURCE_PRECISION),
        "scrap": round(scrap, RESOURCE_PRECISION),
        "chips": round(chips, RESOURCE_PRECISION),
        "alloys": round(_num(current_state.get("alloys")), RESOURCE_PRECISION),
        "battery": round(_num(current_state.get("battery")), RESOURCE_PRECISION),
        "lube": round(_num(current_state.get("lube")), RESOURCE_PRECISION),
        "cats_total": total_cats,
        "birth_progress": round(birth_progress, PROGRESS_PRECISION),
        "battery_kwh": round(battery_kwh, RESOURCE_PRECISION),
        "suspicion": round(suspicion, PROGRESS_PRECISION),
        "decoy_count": decoy_count,
        "go_dark": go_dark,
        "false_alarm_cooldown_until": cooldown_until or None,
    }
    return report


def build_report_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    """把引擎报表裁剪成 API 的《离线休整报表》契约（代码结构稿 §4.3 示例 + 扩展字段）。"""
    final = report.get("final", {})
    gained = report.get("gained_resources", {})
    return {
        "elapsed_seconds": report.get("elapsed_seconds", 0.0),
        "applied_seconds": report.get("applied_seconds", 0.0),
        "gained_catnip": gained.get("catnip", 0.0),
        "gained_scrap": gained.get("scrap", 0.0),
        "gained_cats": report.get("gained_cats", 0),
        "gained_research": round(_num(report.get("gained_research")), PROGRESS_PRECISION),
        "is_starved": bool(report.get("is_starving", False)),
        "starve_duration_seconds": report.get("starve_duration_seconds", 0.0),
        "is_capped": bool(report.get("overflowed")),
        "overflowed_resources": list(report.get("overflowed", [])),
        "suspicion_delta": report.get("suspicion_delta", 0.0),
        "charged_kwh": report.get("charged_kwh", 0.0),
        "clock_anomaly": bool(report.get("clock_anomaly", False)),
        "birth_progress": final.get("birth_progress", 0.0),
        "notes": list(report.get("notes", [])),
    }
