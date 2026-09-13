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
    """把测试槽位的时间锚点往回拨，等价于"离开了这么久"。"""
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
    _ = time


if __name__ == "__main__":
    main()
