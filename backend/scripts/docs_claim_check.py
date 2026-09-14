"""文档声明核对（总检第三刀）：把三份定稿文档里"已落地 / 已接线 / ✅"段落中提到的**代码标识符**
逐个拿去代码里找；**找不到的**列出来人工判断——可能是"文档说了但没实现"，也可能只是命名写法不同。

用法（在项目根或 backend/ 下均可）：`python backend/scripts/docs_claim_check.py`
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
DOCS = ("数值平衡表.md", "数据库设计定稿.md", "代码结构与核心工程详细设计规范.md")
CLAIM_MARKERS = ("已落地", "已接线", "✅")

IDENTIFIER = re.compile(r"`([A-Za-z_][A-Za-z0-9_./]{2,})`")


def needles(token: str) -> list[str]:
    """把文档里的标识符化成"能在代码里找到"的候选片段。

    文档写法千奇百怪：`app/core/offline_engine.py`（带路径前缀）、`balance.ACHIEVEMENTS`（带模块前缀）、
    `app/services/{llm_service,radio_service}.py`（花括号缩写）——统一拆成末段标识符再去比对。
    """
    text = token.strip().rstrip("/")
    if not text or text.startswith(("http", "GET", "POST")):
        return []
    text = re.sub(r"\{([^}]*)\}", lambda m: m.group(1).split(",")[0], text)  # {a,b}.py → a.py
    parts = re.split(r"[/.]", text)
    out = [part for part in parts if len(part) > 2 and not part.endswith("py")]
    if text.endswith(".py"):
        out.append(Path(text).stem)
    return list(dict.fromkeys(out))


def code_text() -> str:
    parts: list[str] = []
    for base in (ROOT / "backend", ROOT / "frontend" / "src"):
        for path in base.rglob("*"):
            if path.is_file():
                parts.append(path.name)  # 文件名也要算：文档常直接引用 `test_xxx.py`
            if path.suffix in {".py", ".ts", ".vue", ".json"} and path.is_file():
                try:
                    parts.append(path.read_text(encoding="utf-8"))
                except UnicodeDecodeError:  # pragma: no cover
                    continue
    return "\n".join(parts)


def main() -> None:
    haystack = code_text()
    missing: dict[str, list[str]] = {}
    checked = 0
    for name in DOCS:
        for line in (ROOT / name).read_text(encoding="utf-8").splitlines():
            if not any(marker in line for marker in CLAIM_MARKERS):
                continue
            for token in IDENTIFIER.findall(line):
                checked += 1
                candidates = needles(token)
                if not candidates:
                    continue
                if not any(candidate in haystack for candidate in candidates):
                    missing.setdefault(token.rstrip("/"), []).append(f"{name}:{line.strip()[:70]}")

    print(f"从'已落地/已接线/✅'段落提取标识符 {checked} 处，其中代码里找不到的 {len(missing)} 个：")
    for token, where in sorted(missing.items()):
        print(f"\n  ❓ {token}")
        for item in where[:2]:
            print(f"      ← {item}")
    if not missing:
        print("✅ 文档声明的标识符都能在代码里找到")


if __name__ == "__main__":
    main()
