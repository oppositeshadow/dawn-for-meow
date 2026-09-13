"""接口账本核对：把《代码结构稿》第 4 章的接口清单与代码里的真实路由对一遍。

用法（在 backend/ 下）：`python scripts/api_doc_consistency.py`
输出：文档条目数 / 代码真实接口数 / **文档写了但没实现** / **实现了但没登记**。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main import app  # noqa: E402

# Windows 控制台默认 GBK，emoji 会直接抛 UnicodeEncodeError
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DOC = Path(__file__).resolve().parents[2] / "代码结构与核心工程详细设计规范.md"
ROW = re.compile(r"^\|\s*`(GET|POST|PUT|DELETE)`\s*\|\s*`([^`]+)`", re.M)


def doc_endpoints() -> set[tuple[str, str]]:
    text = DOC.read_text(encoding="utf-8")
    return {
        (method, path.split("?")[0])
        for method, path in ROW.findall(text)
        if path.startswith("/")
    }


def code_endpoints() -> set[tuple[str, str]]:
    """从 OpenAPI 规范枚举真实接口。

    注意：本项目的 FastAPI 版本把 `include_router` 收成 `_IncludedRouter` 对象、**不摊平**到
    `app.routes`（直接遍历只能看到 7 条），所以要读 `app.openapi()` 的 `paths`。
    """
    result: set[tuple[str, str]] = set()
    for path, operations in app.openapi()["paths"].items():
        if not path.startswith("/api/v1"):
            continue
        for method in operations:
            if method.upper() in {"GET", "POST", "PUT", "DELETE"}:
                result.add((method.upper(), path.replace("/api/v1", "", 1)))
    return result


def main() -> None:
    doc = doc_endpoints()
    code = {(method, path) for method, path in code_endpoints()}
    print(f"文档清单条目：{len(doc)}　代码真实接口：{len(code)}")

    missing = sorted(doc - code)
    if missing:
        print(f"\n❌ 文档写了但**代码没实现**（{len(missing)} 个）：")
        for method, path in missing:
            print(f"   {method:<5} {path}")

    undocumented = sorted(code - doc)
    if undocumented:
        print(f"\n⚠️ 代码实现了但**文档没登记**（{len(undocumented)} 个）：")
        for method, path in undocumented:
            print(f"   {method:<5} {path}")

    if not missing and not undocumented:
        print("✅ 接口清单与代码完全一致")


if __name__ == "__main__":
    main()
