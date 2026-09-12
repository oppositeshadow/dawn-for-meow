"""一键收尾脚本：把「模块 E 科技树」接进现有工程，并跑测试 + 前端构建 + git 存档。

为什么会有这个脚本：本机 Codex 沙箱 helper 故障期间，Agent 只能新建文件、无法修改已有文件，
于是把"改已有文件"的动作收敛成这个幂等脚本，交给你在普通终端里执行（不受沙箱限制）。

用法（**在你自己的 PowerShell 里**，不是沙箱里）::

    cd E:\\tmp\\dawn-for-meow
    python backend/scripts/finish_tech_wiring.py            # 只接线（幂等，可重复跑）
    python backend/scripts/finish_tech_wiring.py --verify    # 接线 + pytest + 前端构建
    python backend/scripts/finish_tech_wiring.py --commit    # 接线 + 校验 + git 首次提交（推荐）

脚本做的事：

1. `backend/app/api/api_v1.py` 注册 `tech` 路由（`/tech/tree`、`/tech/research`、`/tech/reroll`）；
2. `backend/app/services/colony_service.py`：
   * 离线结算把 `gained_research` 注入当前在研科技节点（极客猫才真的推进研发）；
   * 建造进阶设施前调用 `tech_service.build_gate_check`（科技门槛正式生效）；
3. `frontend/src/App.vue` 左栏同时挂上「设施建造 + 科技树」两个面板；
4. `--verify` 跑 `pytest` 与 `npm run build`；`--commit` 再做一次 git 提交
   （用 `-c safe.directory=...` 绕过 Windows 属主错配，且提交前打印 `git status` 供复核）。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"

OK = "[ OK ]"
SKIP = "[SKIP]"
FAIL = "[FAIL]"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="")


def patch(path: Path, replacements: list[tuple[str, str, str]], markers: list[str]) -> str:
    """按 (说明, 原文, 新文) 精确替换；已含 marker 则跳过（幂等）。"""
    text = read(path)
    if all(marker in text for marker in markers):
        return f"{SKIP} {path.relative_to(ROOT)}（已接线）"
    for label, old, new in replacements:
        if new.splitlines()[0] in text and old not in text:
            continue
        if old not in text:
            return f"{FAIL} {path.relative_to(ROOT)}：找不到锚点「{label}」，请手动补"
        text = text.replace(old, new, 1)
    write(path, text)
    return f"{OK}   {path.relative_to(ROOT)}"


def run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    print(f"\n$ {' '.join(cmd)}   (cwd={cwd})")
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = "\n".join((proc.stdout or "").strip().splitlines()[-12:])
    if tail:
        print(tail)
    if proc.returncode != 0 and (proc.stderr or "").strip():
        print("\n".join((proc.stderr or "").strip().splitlines()[-12:]))
    return proc.returncode, proc.stdout or ""


def git(*args: str) -> tuple[int, str]:
    return run(["git", "-c", f"safe.directory={ROOT.as_posix()}", *args], ROOT)


def wire() -> list[str]:
    results: list[str] = []

    # ---- 1. 注册 tech 路由 ----
    api_v1 = BACKEND / "app" / "api" / "api_v1.py"
    results.append(
        patch(
            api_v1,
            [
                (
                    "导入 tech",
                    "from app.api.endpoints import colony, radio\n",
                    "from app.api.endpoints import colony, radio, tech\n",
                ),
                (
                    "挂载路由",
                    "api_router.include_router(radio.router)\n",
                    "api_router.include_router(radio.router)\napi_router.include_router(tech.router)\n",
                ),
            ],
            markers=["import colony, radio, tech", "api_router.include_router(tech.router)"],
        )
    )

    # ---- 2. colony_service：研发算力注入 + 建造科技门槛 ----
    colony_service = BACKEND / "app" / "services" / "colony_service.py"
    results.append(
        patch(
            colony_service,
            [
                (
                    "导入 tech_service",
                    "from app.schemas.colony import SnapshotRequest\n",
                    "from app.schemas.colony import SnapshotRequest\nfrom app.services import tech_service\n",
                ),
                (
                    "离线结算注入算力",
                    "    await _accumulate_career_stats(\n"
                    "        session, save.slot_id, report, elapsed_seconds=max(0, delta_seconds)\n"
                    "    )\n"
                    "    await session.flush()\n",
                    "    await _accumulate_career_stats(\n"
                    "        session, save.slot_id, report, elapsed_seconds=max(0, delta_seconds)\n"
                    "    )\n"
                    "    # 研究算力一次性注入当前在研科技节点（模块 E：极客猫 → 科技解锁）\n"
                    "    research_event = await tech_service.accumulate_research(\n"
                    "        session,\n"
                    "        slot_id=save.slot_id,\n"
                    "        planet_id=planet_id,\n"
                    "        points=float(report.get(\"gained_research\", 0.0)),\n"
                    "    )\n"
                    "    if research_event:\n"
                    "        report[\"research\"] = research_event\n"
                    "    await session.flush()\n",
                ),
                (
                    "建造科技门槛",
                    "    current_level = int(facilities.get(facility_id, 0))\n"
                    "    max_level = B.facility_max_level(facility_id)\n",
                    "    current_level = int(facilities.get(facility_id, 0))\n"
                    "    # 模块 E 解锁效果：进阶设施首次建造需要对应科技（开荒部件豁免）\n"
                    "    await tech_service.build_gate_check(\n"
                    "        session,\n"
                    "        facility_id,\n"
                    "        slot_id=slot_id,\n"
                    "        planet_id=target_planet,\n"
                    "        current_level=current_level,\n"
                    "    )\n"
                    "    max_level = B.facility_max_level(facility_id)\n",
                ),
            ],
            markers=["from app.services import tech_service", "tech_service.accumulate_research", "build_gate_check"],
        )
    )

    # ---- 3. 前端：左栏同时挂设施建造与科技树 ----
    app_vue = FRONTEND / "src" / "App.vue"
    results.append(
        patch(
            app_vue,
            [
                (
                    "导入 TechPanel",
                    "import FacilitiesPanel from '@/components/panels/FacilitiesPanel.vue'\n",
                    "import FacilitiesPanel from '@/components/panels/FacilitiesPanel.vue'\n"
                    "import TechPanel from '@/components/panels/TechPanel.vue'\n",
                ),
                (
                    "左栏布局",
                    '      <FacilitiesPanel class="col-span-4 min-h-0" />\n',
                    '      <div class="col-span-4 flex min-h-0 flex-col gap-2">\n'
                    '        <FacilitiesPanel class="min-h-0 flex-1" />\n'
                    '        <TechPanel class="min-h-0 flex-1" />\n'
                    '      </div>\n',
                ),
            ],
            markers=["panels/TechPanel.vue", "<TechPanel"],
        )
    )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="喵星破晓 · 科技树接线收尾脚本")
    parser.add_argument("--verify", action="store_true", help="接线后跑 pytest 与 npm run build")
    parser.add_argument("--commit", action="store_true", help="接线 + 校验 + git 提交（含 --verify）")
    args = parser.parse_args()
    verify = args.verify or args.commit

    print(f"项目根目录：{ROOT}")
    results = wire()
    for line in results:
        print(line)
    if any(line.startswith(FAIL) for line in results):
        print("\n接线未全部完成：请按上面的提示手动补锚点后再跑一次。")
        return 1

    if verify:
        # 前端构建前先确认依赖在
        if not (FRONTEND / "node_modules").exists():
            print(f"{SKIP} frontend/node_modules 不存在，跳过 npm run build（先 cd frontend && npm install）")
        else:
            code, _ = run(["npm", "run", "build"], FRONTEND)
            print(f"{OK if code == 0 else FAIL} 前端构建")
            if code != 0:
                return 1

        code, _ = run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q"], BACKEND)
        print(f"{OK if code == 0 else FAIL} 后端测试")
        if code != 0:
            return 1

    if args.commit:
        print("\n---- git 提交 ----")
        git("config", "user.name", "Dawn Dev")
        git("config", "user.email", "dawn-dev@localhost")
        git("add", "-A")
        _, status = git("status", "--short")
        if re.search(r"(^|\W)\.env$", status, flags=re.MULTILINE):
            print(f"{FAIL} 暂存区里出现了 .env（含 API Key）——已中止提交，请检查 .gitignore")
            return 1
        code, _ = git("commit", "-m", "Milestone 1 后端 + 前端 + 公频电台 + 科技树")
        if code != 0:
            print(f"{FAIL} 提交失败（可能没有改动或身份未配置）")
            return 1
        print(f"{OK}   已提交")

    print("\n完成。启动方式：")
    print("  cd backend  && python -m uvicorn main:app --reload --port 8010")
    print("  cd frontend && $env:VITE_BACKEND_TARGET='http://127.0.0.1:8010'; npm run dev")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
