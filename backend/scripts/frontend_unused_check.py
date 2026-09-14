"""总检第四刀：前端"写了但没人用"的组件与导出。

检查两类：
1. `frontend/src/components/**/*.vue` 里的组件——除自身外没有任何 `.vue`/`.ts` 引用它（按文件名匹配）；
2. `frontend/src/**/*.ts` 里的 `export function/const/interface` ——除定义文件外全仓零引用。

用法：`python backend/scripts/frontend_unused_check.py`
注意：**动态引用**（字符串拼组件名、vite 的 glob 导入）会漏判，命中后需人工确认。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"


def read_all() -> dict[Path, str]:
    return {
        path: path.read_text(encoding="utf-8")
        for path in SRC.rglob("*")
        if path.suffix in {".ts", ".vue"} and path.is_file()
    }


def main() -> None:
    files = read_all()

    print("== 未引用的组件（.vue）==")
    unused_components: list[str] = []
    for path, text in sorted(files.items()):
        if path.suffix != ".vue":
            continue
        name = path.stem
        referenced = any(
            name in other_text
            for other_path, other_text in files.items()
            if other_path != path
        )
        if not referenced:
            unused_components.append(str(path.relative_to(SRC.parent)))
    for item in unused_components or ["（无）"]:
        print(f"  {item}")

    print("\n== 未被引用的 TS 导出（函数 / 常量）==")
    export_pattern = re.compile(r"^export\s+(?:async\s+)?(?:function|const)\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)
    unused_exports: list[str] = []
    for path, text in sorted(files.items()):
        if path.suffix != ".ts":
            continue
        for name in export_pattern.findall(text):
            # 统计**全仓**引用（含定义文件自身）：常被自己文件里用掉（如 `setInterval(tick, GAME_TICK_MS)`），
            # 只排除定义文件会把这些误判成"未使用"——这个错在死常量扫描里也犯过一次。
            total = sum(len(re.findall(rf"\b{name}\b", content)) for content in files.values())
            if total <= 1:
                unused_exports.append(f"{path.relative_to(SRC.parent)} → {name}")
    for item in unused_exports or ["（无）"]:
        print(f"  {item}")

    print(f"\n汇总：未引用组件 {len(unused_components)} 个、未引用导出 {len(unused_exports)} 个")
    print("提示：动态引用会漏判，逐条人工确认后再决定删除或接线。")


if __name__ == "__main__":
    main()
