"""校验 + 存档脚本：跑后端测试、构建前端、并做一次 git 提交。

配套 `finish_tech_wiring.py`（先接线，再跑本脚本）。用法（普通 PowerShell 里）::

    cd E:\\tmp\\dawn-for-meow
    python backend/scripts/verify_and_commit.py             # 只校验（pytest + npm build）
    python backend/scripts/verify_and_commit.py --commit    # 校验通过后再提交 git

要点：

* Windows 上 `npm` 是 `npm.cmd`（批处理），必须 `shell=True` 才能被 subprocess 调起——本脚本已处理；
* git 用 `-c safe.directory=<仓库>` 绕过"目录属主 ≠ 沙箱用户"的 dubious ownership；
* 提交前会打印 `git status --short`，并**拒绝**在暂存区里携带 `.env`（含 API Key）。
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"

OK = "[ OK ]"
FAIL = "[FAIL]"
SKIP = "[SKIP]"


def run(cmd: list[str] | str, cwd: Path, *, shell: bool = False) -> tuple[int, str]:
    label = cmd if isinstance(cmd, str) else " ".join(cmd)
    print(f"\n$ {label}   (cwd={cwd})")
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        shell=shell,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    for stream in (proc.stdout, proc.stderr):
        lines = (stream or "").strip().splitlines()
        if lines:
            print("\n".join(lines[-15:]))
    return proc.returncode, proc.stdout or ""


def git(*args: str) -> tuple[int, str]:
    return run(["git", "-c", f"safe.directory={ROOT.as_posix()}", *args], ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description="喵星破晓 · 校验与存档脚本")
    parser.add_argument("--commit", action="store_true", help="校验通过后执行 git 提交")
    parser.add_argument("--message", default="Milestone 1 后端 + 前端 + 公频电台 + 科技树", help="提交信息")
    args = parser.parse_args()

    failures = 0

    # ---- 后端测试 ----
    code, _ = run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q"], BACKEND)
    print(f"{OK if code == 0 else FAIL} 后端测试")
    failures += code != 0

    # ---- 前端构建（npm 是 .cmd，Windows 必须 shell=True）----
    if not (FRONTEND / "node_modules").exists():
        print(f"{SKIP} frontend/node_modules 不存在，跳过前端构建（先 cd frontend && npm install）")
    else:
        npm = shutil.which("npm") or shutil.which("npm.cmd") or "npm.cmd"
        code, _ = run([npm, "run", "build"], FRONTEND, shell=True)
        print(f"{OK if code == 0 else FAIL} 前端构建")
        failures += code != 0

    if failures:
        print(f"\n{FAIL} 有 {failures} 项校验未通过，先修好再提交。")
        return 1

    if not args.commit:
        print(f"\n{OK}   校验通过（未提交；要提交加 --commit）")
        return 0

    print("\n---- git 提交 ----")
    git("config", "user.name", "Dawn Dev")
    git("config", "user.email", "dawn-dev@localhost")
    git("add", "-A")
    _, status = git("status", "--short")
    staged = [line for line in status.splitlines() if line.strip()]
    print(f"待提交 {len(staged)} 项")
    if any(re.search(r"(^|[/\\])\.env$", line) for line in staged):
        print(f"{FAIL} 暂存区里出现了 .env（含 API Key）——已中止提交，请检查 .gitignore")
        return 1

    code, _ = git("commit", "-m", args.message)
    if code != 0:
        print(f"{FAIL} 提交失败（可能没有改动，或 git 身份未配置）")
        return 1
    print(f"{OK}   已提交：{args.message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
