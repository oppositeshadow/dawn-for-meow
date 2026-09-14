"""一键总检：把项目里所有只读审计工具串起来跑一遍。

用法（在 backend/ 下）：
    python scripts/audit_all.py              # 只读审计（不需要起服务）
    python scripts/audit_all.py --with-server # 额外跑"契约扫查"（需要 8010 在跑）
    python scripts/audit_all.py --with-tests  # 额外跑全量 pytest（约 1~2 分钟）

产出：每项的结论 + 末尾汇总；任何一项 ❌ 都值得马上看。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]  # backend/


def run(label: str, args: list[str]) -> tuple[str, bool]:
    print(f"\n===== {label} =====", flush=True)
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    output = (result.stdout or "") + (result.stderr or "")
    print(output.strip()[-1500:], flush=True)
    ok = result.returncode == 0 and "❌" not in output
    return label, ok


def main() -> None:
    python = sys.executable
    checks: list[tuple[str, list[str]]] = [
        ("接口账本（文档 ⇄ 代码）", [python, "scripts/api_doc_consistency.py"]),
        ("文档声明核对（已落地/✅ 段落）", [python, "scripts/docs_claim_check.py"]),
        ("科技节奏与内容时间线", [python, "scripts/pacing_analysis.py"]),
        ("减伤曲线对比", [python, "scripts/armor_curve_analysis.py"]),
    ]
    if "--with-server" in sys.argv:
        checks.append(("契约扫查（最小请求 / 5xx）", [python, "scripts/contract_sweep.py", "3"]))
    if "--with-tests" in sys.argv:
        checks.append(("全量测试", [python, "-m", "pytest", "-p", "no:cacheprovider", "-q"]))

    results = [run(label, args) for label, args in checks]
    print("\n===== 总检汇总 =====")
    for label, ok in results:
        print(f"  {'✅' if ok else '❌'} {label}")
    failed = [label for label, ok in results if not ok]
    if failed:
        print(f"\n需要人工看的项目：{'、'.join(failed)}")
        sys.exit(1)
    print("\n✅ 全部通过")


if __name__ == "__main__":
    main()
