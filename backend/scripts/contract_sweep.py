"""契约扫查：给每个接口发一次"最小请求"，找出 **500（崩溃）** 与契约不明的地方。

用法（先启动 uvicorn）：`python scripts/contract_sweep.py [slot]`（默认槽位 3，**只动测试槽位**）。

设计要点：
* 只发"最小合法请求"（GET 带 slot 参数、POST 带 `{"slot": N}`），**故意不构造完整业务载荷**；
* 因此**预期大多是 400/409**（校验层正常工作），重点是抓 **5xx**——那才是"输入没防住"的崩溃；
* `/save/*` 与 `/game/load` 这类会改档的接口默认跳过（用 `--include-save` 打开）。
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main import app  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8010/api/v1"
SLOT = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 3
INCLUDE_SAVE = "--include-save" in sys.argv
SKIP_PATHS = {"/save/import", "/save/export", "/save/slots"}


def call(method: str, path: str, payload: dict | None = None) -> tuple[int, str]:
    url = BASE + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if data:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, response.read().decode("utf-8")[:160]
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")[:160]
    except Exception as exc:  # 连接层错误
        return 0, str(exc)[:160]


def main() -> None:
    spec = app.openapi()
    results: list[tuple[str, str, int, str]] = []
    for path, operations in sorted(spec["paths"].items()):
        if not path.startswith("/api/v1"):
            continue
        short = path.replace("/api/v1", "", 1)
        if short in SKIP_PATHS and not INCLUDE_SAVE:
            continue
        for method in operations:
            method = method.upper()
            if method not in {"GET", "POST"}:
                continue
            if method == "GET":
                status, body = call("GET", short + ("&" if "?" in short else "?") + urllib.parse.urlencode({"slot": SLOT}))
            else:
                status, body = call("POST", short, {"slot": SLOT})
            results.append((method, short, status, body))

    crashes = [row for row in results if row[2] >= 500 or row[2] == 0]
    print(f"共扫查 {len(results)} 个接口：{len(crashes)} 个 5xx/连接失败，其余为 2xx/4xx（校验正常工作）")
    for method, path, status, body in crashes:
        print(f"  ❌ {status} {method} {path} → {body}")

    if not crashes:
        print("✅ 没有接口在最小请求下崩溃")
    counts: dict[int, int] = {}
    for _, _, status, _ in results:
        counts[status] = counts.get(status, 0) + 1
    print("状态码分布：" + "　".join(f"{code}:{count}" for code, count in sorted(counts.items())))


if __name__ == "__main__":
    main()
