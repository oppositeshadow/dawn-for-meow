"""真机开局闭环演练（只动测试槽位，不碰主档）。

用法：先启动 uvicorn（8010），再 `python scripts/e2e_cold_start.py [slot]`（默认槽位 3）。
走的是玩家视角：手点废墟 → 纸箱窝 → 第一只猫 → 农田 → 农夫 → 操作台 → 拾荒猫，
中间用数据库"时间旅行"跳过等待（离线结算本身按真实公式跑）。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymysql  # noqa: E402

from app.core.config import get_settings  # noqa: E402

# Windows 控制台默认 GBK，emoji（✅/❌）会直接抛 UnicodeEncodeError
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8010/api/v1"
SLOT = int(sys.argv[1]) if len(sys.argv) > 1 else 3
RESET = "--reset" in sys.argv

SLOT_TABLES = (
    "save_slot", "colony_state", "labor_buckets", "facility_state", "planet_state",
    "career_stats", "achievements", "minigame_state", "tech_records", "military_state",
    "vehicle_units", "boss_state", "darknet_state", "forum_posts", "garden_state",
)


def reset_slot() -> None:
    """清空一个**测试槽位**（只允许 2/3，绝不碰主档），用于反复演练。"""
    if SLOT == 1:
        raise SystemExit("拒绝清空槽位 1（那是主档）")
    settings = get_settings()
    conn = pymysql.connect(
        host=settings.db_host, port=settings.db_port, user=settings.db_user,
        password=settings.db_password, database=settings.db_name, charset="utf8mb4", autocommit=True,
    )
    with conn.cursor() as cur:
        for table in SLOT_TABLES:
            cur.execute(f"DELETE FROM {table} WHERE slot_id = %s", (SLOT,))
    conn.close()
    print(f"（已清空测试槽位 {SLOT}）")


def call(method: str, path: str, body: dict | None = None, query: dict | None = None) -> dict:
    url = BASE + path + ("?" + urllib.parse.urlencode(query) if query else "")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if data:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # 业务错误也照实打印
        return {"code": exc.code, **json.loads(exc.read().decode("utf-8"))}


def travel(seconds: int) -> None:
    """把测试槽位的时间锚点往回拨并立刻读一次档（读档才会跑离线结算）。"""
    settings = get_settings()
    conn = pymysql.connect(
        host=settings.db_host, port=settings.db_port, user=settings.db_user,
        password=settings.db_password, database=settings.db_name, charset="utf8mb4", autocommit=True,
    )
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE colony_state SET last_tick_time = last_tick_time - %s "
            "WHERE slot_id = %s AND planet_id = 0",
            (seconds, SLOT),
        )
    conn.close()
    call("GET", "/colony/state", query={"slot": SLOT})


def calm() -> None:
    """测试专用"镇静"：把警戒度归零并解除静默关灯。

    **真实玩家没法这么干**——正确做法是造【隔音层】降噪、种【消音绒草】、
    或按三级安防预案用诱饵/战车截杀处理警报。这里只是为了让演练能跑下去。
    """
    settings = get_settings()
    conn = pymysql.connect(
        host=settings.db_host, port=settings.db_port, user=settings.db_user,
        password=settings.db_password, database=settings.db_name, charset="utf8mb4", autocommit=True,
    )
    with conn.cursor() as cur:
        cur.execute("UPDATE colony_state SET suspicion = 0 WHERE slot_id = %s", (SLOT,))
        cur.execute(
            "UPDATE military_state SET security_policy = JSON_SET(COALESCE(security_policy, JSON_OBJECT()), "
            "'$.go_dark', FALSE) WHERE slot_id = %s",
            (SLOT,),
        )
    conn.close()
    print("（已镇静：警戒度归零 + 解除静默关灯——玩家需靠隔音层/诱饵/截杀自行处理）")


def step(label: str, result: dict, expect: str | None = None) -> None:
    code = result.get("code")
    detail = result.get("data") if code == 200 else result.get("message") + "：" + str(result.get("detail", ""))
    mark = "✅" if code == 200 else "❌"
    print(f"{mark} {label:<24} → {json.dumps(detail, ensure_ascii=False)[:120]}")
    if code != 200 and expect is None:
        raise SystemExit(f"演练中断于：{label}")


def main() -> None:
    print(f"== 真机开局闭环演练（槽位 {SLOT}）==")
    if RESET:
        reset_slot()
    state = call("GET", "/colony/state", query={"slot": SLOT})["data"]
    if state["facilities"]["housing_box"] > 0 or state["population"]["total"] > 0:
        raise SystemExit(f"槽位 {SLOT} 不是空档，演练只在空档上进行（换个槽位或先清档）")

    for index in range(5):
        result = call("POST", "/colony/scavenge", query={"slot": SLOT})
        if index == 4:
            step("手点废墟 ×5", result)

    step("造第一座纸箱窝", call("POST", "/facilities/build",
                              body={"slot": SLOT, "facility_id": "housing_box"}))
    state = call("GET", "/colony/state", query={"slot": SLOT})["data"]
    print(f"   猫口 {state['population']['total']}（应为 1：纸箱窝引来第一只折耳猫）")

    travel(60)
    call("POST", "/colony/scavenge", query={"slot": SLOT})  # 还有 35 次额度可用
    for _ in range(10):
        call("POST", "/colony/scavenge", query={"slot": SLOT})
    step("造水培农田", call("POST", "/facilities/build",
                           body={"slot": SLOT, "facility_id": "farm_plot"}))
    step("派 1 只农夫猫", call("POST", "/colony/dispatch",
                            body={"slot": SLOT, "job_id": "farmer", "delta": 1}))
    for _ in range(8):
        call("POST", "/colony/scavenge", query={"slot": SLOT})
    step("造废品解体操作台", call("POST", "/facilities/build",
                              body={"slot": SLOT, "facility_id": "scavenge_station"}))

    # §3.4 的冷启动路径含「第二座窝 6 废铁」：只有 1 座窝时 K=1，繁育增长为 0
    for _ in range(6):
        call("POST", "/colony/scavenge", query={"slot": SLOT})
    step("造第二座纸箱窝", call("POST", "/facilities/build",
                             body={"slot": SLOT, "facility_id": "housing_box"}))

    travel(400)  # 等第 2 只猫出生（r=0.01/s）
    state = call("GET", "/colony/state", query={"slot": SLOT})["data"]
    print(f"   离线 400 秒后猫口 {state['population']['total']}（应为 2）")
    step("派 1 只拾荒猫", call("POST", "/colony/dispatch",
                            body={"slot": SLOT, "job_id": "scavenger", "delta": 1}))

    travel(120)
    state = call("GET", "/colony/state", query={"slot": SLOT})["data"]
    report = state["offline_report"]
    print(
        f"   自动化结算：猫薄荷 +{report['gained_catnip']}、废铁 +{report['gained_scrap']}、"
        f"断粮={report['is_starved']}"
    )
    print(f"   工位：{state['workstations']}")
    print("== 开局闭环演练完成：手点 → 纸箱窝 → 第一只猫 → 农田 → 农夫 → 操作台 → 拾荒猫 ==")
    if "--stage" in sys.argv and "mid" in sys.argv:
        mid_stage()
    if "--stage" in sys.argv and "late" in sys.argv:
        late_stage()
    _ = time


def grant(tech_ids: list[str], *, scrap: float = 500.0, alloys: float = 300.0,
          chips: float = 200.0, cats: int = 8) -> None:
    """测试专用：直接给科技/资源/猫口，跳过数小时挂机，聚焦接口契约走查。"""
    settings = get_settings()
    conn = pymysql.connect(
        host=settings.db_host, port=settings.db_port, user=settings.db_user,
        password=settings.db_password, database=settings.db_name, charset="utf8mb4", autocommit=True,
    )
    with conn.cursor() as cur:
        for tech_id in tech_ids:
            cur.execute(
                "UPDATE tech_records SET status='UNLOCKED' WHERE slot_id=%s AND planet_id=0 AND tech_id=%s",
                (SLOT, tech_id),
            )
        cur.execute(
            "UPDATE colony_state SET scrap=%s, alloys=%s, chips=%s, total_cats=%s, job_idle=%s "
            "WHERE slot_id=%s AND planet_id=0",
            (scrap, alloys, chips, cats, cats, SLOT),
        )
        # 深网开户启动资金（测试专用；真机靠股市/黑市自己挣）
        cur.execute("UPDATE darknet_state SET byte_credits=2000 WHERE slot_id=%s", (SLOT,))
    conn.close()
    print(f"（已授予科技 {tech_ids} 与资源/猫口，供契约走查）")


def late_stage() -> None:
    """后期契约走查：深网交易 → 机库组装 → 派遣远征（这三条链路此前没有端到端跑过）。"""
    print("\n== 后期契约走查：深网 / 机库 / 远征 ==")
    grant(["tech_shortwave_radio", "tech_armored_car_chassis", "tech_battery_matrix"])
    travel(60)

    state = call("GET", "/darknet/state", query={"slot": SLOT})["data"]
    print(f"   深网行情：{len(state.get('stocks', []))} 支标的，算力币 {state.get('byte_credits')}")
    step(
        "买入 FORGE 10 股",
        call("POST", "/darknet/stock/trade",
             body={"slot": SLOT, "stock_id": "FORGE", "action": "BUY_LONG", "shares": 10}),
    )

    step("组装轻装猫车", call("POST", "/vehicle/assemble",
                          body={"slot": SLOT, "unit_type": "light_car"}))
    vehicles = call("GET", "/vehicle/list", query={"slot": SLOT, "planet_id": 0})["data"]
    unit_ids = [item["unit_id"] for item in vehicles["vehicles"] if item["status"] == "IDLE"]
    print(f"   机库待命载具：{unit_ids}")

    step(
        "派遣远征（沃尔玛废墟）",
        call("POST", "/military/dispatch",
             body={"slot": SLOT, "planet_id": 0, "target_id": "WALMART", "unit_ids": unit_ids[:2]}),
    )
    step("收取远征（未到点应报错）", call("POST", "/military/collect",
                                   body={"slot": SLOT, "expedition_id": "not-exist"}),
         expect="error")
    print("== 后期契约走查完成 ==")


def mid_stage() -> None:
    """中期演练：供电 → 极客猫 → 科技 → 熔炼 → 合金（用时间旅行压缩等待）。

    真机走查发现的**依赖链**（下一步要按这个顺序补全脚本）：
    1. 图灵终端要**芯片**（拾荒猫 0.01/s ⇒ 冷启动后先挂机约 15 分钟）；
    2. 图灵终端 −6 kW，没电就强制断电、科研归 0（§7.1）⇒ 必须先解决供电；
    3. 太阳能板被 T1【瓦楞纸结构力学】门槛挡住 ⇒ 早期只能走**猫力滚轮 + 踩轮猫**（开荒豁免名单内，+5 kW/座）；
    4. 5 kW < 6 kW ⇒ 需要**两座滚轮**（或一座滚轮 + 科研完的太阳能板）；
    5. 踩轮猫会占掉空闲猫口 ⇒ 派极客猫之前要再扩一次窝。

    **当前状态（2026-09-13）**：依赖链已全部走通（滚轮×2 → 扩窝 → 踩轮猫×2 → 净电力 10 kW →
    图灵终端 → 扩窝 → 极客猫 → T1 前两个节点解锁）。**遗留疑点**：脚本里连续研发多个节点时，
    第 3 个节点报 `RESEARCH_IN_PROGRESS`（上一个节点看似未在 3600 秒的"时间旅行"里完成）——
    需要确认是"脚本节奏问题"还是"研发注入只在特定路径发生"（真机手玩没遇到，因为间隔更长）。
    下一次会话请先查这一点，再往下走熔炼。
    """
    print("\n== 中期演练：科研 → 冶炼 ==")
    # 图灵终端要芯片，而拾荒猫产芯片只有 0.01/s ⇒ 先挂机攒一会儿（这是设计内的资源门槛）
    travel(900)
    # §7.1：净电力为负时图灵终端强制断电、科研归 0；而太阳能被 T1 科技门槛挡住，
    # 所以早期只能走【猫力滚轮 + 踩轮猫】（开荒豁免，+5 kW/座，两座才够抵消终端 −6 kW）
    step("造猫力滚轮 ×2", call("POST", "/facilities/build",
                           body={"slot": SLOT, "facility_id": "power_wheel", "count": 2}))
    # 两只猫都在岗（农夫 + 拾荒）⇒ 想派踩轮猫必须先扩窝、等第 3 只猫
    step("扩第三座纸箱窝", call("POST", "/facilities/build",
                            body={"slot": SLOT, "facility_id": "housing_box"}))
    travel(900)
    state = call("GET", "/colony/state", query={"slot": SLOT})["data"]
    print(f"   扩窝后猫口 {state['population']['total']}（K={state['population']['max_cap']}）")
    step("派 1 只踩轮猫供电", call("POST", "/colony/dispatch",
                            body={"slot": SLOT, "job_id": "power_runner", "delta": 1}))
    # 1 只踩轮猫只有 +5 kW，抵不过图灵终端的 −6 kW（§7.1）⇒ 还得第二只
    step("扩第四座纸箱窝", call("POST", "/facilities/build",
                            body={"slot": SLOT, "facility_id": "housing_box"}))
    travel(900)
    step("派第 2 只踩轮猫", call("POST", "/colony/dispatch",
                            body={"slot": SLOT, "job_id": "power_runner", "delta": 1}))
    state = call("GET", "/colony/state", query={"slot": SLOT})["data"]
    print(f"   净电力 {state['power']['net_kw']} kW（应 ≥ 0，否则图灵终端会断电、科研归 0）")
    step("造图灵终端机房", call("POST", "/facilities/build",
                            body={"slot": SLOT, "facility_id": "turing_terminal"}))
    step("派 2 只极客猫", call("POST", "/colony/dispatch",
                           body={"slot": SLOT, "job_id": "geek", "delta": 2})) if False else None
    # 猫口只有 2 只（1 农夫 + 1 拾荒）⇒ 先造窝扩容再养猫
    for _ in range(20):
        call("POST", "/colony/scavenge", query={"slot": SLOT})
    call("POST", "/facilities/build", body={"slot": SLOT, "facility_id": "housing_box"})
    travel(900)  # K=3 ⇒ 长到 3 只
    state = call("GET", "/colony/state", query={"slot": SLOT})["data"]
    print(f"   扩容后猫口 {state['population']['total']}（K={state['population']['max_cap']}）")
    step("派 1 只极客猫", call("POST", "/colony/dispatch",
                           body={"slot": SLOT, "job_id": "geek", "delta": 1}))

    # 人口扩张必须同步补粮，而且要算上**低士气惩罚**：猫薄荷见底时 production_multiplier = 0.7，
    # 农夫有效产出只剩 0.14/s ⇒ 1 个农夫只养得起约 2.8 只猫（不是直觉的 4 只）。
    # 养到 6 只猫时 2 个农夫仍是净负 → 断粮 → `gained_research` 归零（科研静默停摆）⇒ 必须 3 个农夫。
    step("扩第七座纸箱窝", call("POST", "/facilities/build",
                            body={"slot": SLOT, "facility_id": "housing_box"}))
    travel(900)
    step("派第 2 只农夫猫", call("POST", "/colony/dispatch",
                            body={"slot": SLOT, "job_id": "farmer", "delta": 1}))
    step("扩第八座纸箱窝", call("POST", "/facilities/build",
                            body={"slot": SLOT, "facility_id": "housing_box"}))
    # 工位来自设施：1 座水培农田只有 2 个农夫工位 ⇒ 想上 3 个农夫得再建一座
    step("建第二座水培农田", call("POST", "/facilities/build",
                            body={"slot": SLOT, "facility_id": "farm_plot"}))
    travel(900)
    step("派第 3 只农夫猫", call("POST", "/colony/dispatch",
                            body={"slot": SLOT, "job_id": "farmer", "delta": 1}))
    state = call("GET", "/colony/state", query={"slot": SLOT})["data"]
    print(
        f"   猫口 {state['population']['total']} / 农夫 {state['workstations']['farmer']}"
        f" / 猫薄荷 {state['resources']['catnip']}（断粮={state['offline_report']['is_starved']}）"
    )
    # 长挂机会把警戒度推满 ⇒ 触发静默关灯 ⇒ 科研/拾荒全停（这是机制，不是 bug）
    calm()

    travel(3600)  # 1 小时科研
    state = call("GET", "/colony/state", query={"slot": SLOT})["data"]
    print(f"   科研产出 {state['population'].get('geek_jobs', state['workstations']['geek'])} 只极客猫在岗")

    for tech_id in ("tech_cardboard_mechanics", "tech_hydroponics_basics",
                    "tech_appliance_teardown", "tech_night_stealth_scavenging",
                    "tech_acoustic_layer", "tech_induction_furnace"):
        result = call("POST", "/tech/research", body={"slot": SLOT, "tech_id": tech_id})
        if result.get("code") != 200:
            print(f"   ⏳ {tech_id}: {result.get('message')}")
            travel(3600)
            result = call("POST", "/tech/research", body={"slot": SLOT, "tech_id": tech_id})
        step(f"研发 {tech_id}", result)
        calm()  # 每轮挂机都会攒满警戒度 ⇒ 每轮都要"镇静"（玩家需自行处理警报）
        travel(3600)

    step("造高频感应电炉", call("POST", "/facilities/build",
                           body={"slot": SLOT, "facility_id": "induction_furnace"}))
    travel(900)  # 等拾荒猫把废铁攒回来（电炉一次吃掉 120 废铁 + 25 芯片）
    step("造太阳能板 ×2", call("POST", "/facilities/build",
                          body={"slot": SLOT, "facility_id": "solar_panel", "count": 2}))
    for _ in range(20):
        call("POST", "/colony/scavenge", query={"slot": SLOT})
    travel(600)
    state = call("GET", "/colony/state", query={"slot": SLOT})["data"]
    report = state["offline_report"]
    print(f"   熔炼结算：{report.get('smelted_batches', 0)} 炉次 → 合金 +{report.get('gained_alloys', 0)}"
          f"（当前合金 {state['resources']['alloys']}）")
    print("== 中期演练完成：极客猫 → 科技 → 熔炼 → 合金 ==")


if __name__ == "__main__":
    main()
