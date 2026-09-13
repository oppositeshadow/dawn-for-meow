"""小游戏通用引擎（模块 O）：目前实现【密电译码】的密钥生成与反馈判定。

* Mastermind 式推理：4 位密钥、6 种符号，反馈只有"位置对／符号对"两个数字；
* **确定性密钥**：按 (slot, planet, 日期) 生成 ⇒ 同一天重开游戏密钥不变，可复现、可测试；
* 反馈规则与 Mastermind 一致：先算位置对，再从剩余符号里算符号对。
"""

from __future__ import annotations

import hashlib
import random
from collections import Counter
from collections.abc import Sequence

from app.core import balance as B


def code_length() -> int:
    return int(B.MINIGAME_SPECS["cipher_decode"]["code_length"])


def symbol_count() -> int:
    return int(B.MINIGAME_SPECS["cipher_decode"]["symbol_count"])


def generate_secret(seed_key: str) -> list[int]:
    """按种子生成当天密钥（符号允许重复，符合 Mastermind 经典规则）。"""
    digest = hashlib.sha256(seed_key.encode("utf-8")).hexdigest()
    rng = random.Random(int(digest[:16], 16))
    return [rng.randrange(1, symbol_count() + 1) for _ in range(code_length())]


def daily_seed(slot_id: int, planet_id: int, day: str) -> str:
    return f"cipher:{slot_id}:{planet_id}:{day}"


def evaluate_guess(secret: Sequence[int], guess: Sequence[int]) -> tuple[int, int]:
    """返回（位置对 exact，符号对 partial）。长度不合法直接报错由调用方负责。"""
    exact = sum(1 for index, value in enumerate(guess) if value == secret[index])
    secret_rest = Counter(value for index, value in enumerate(secret) if value != guess[index])
    guess_rest = Counter(value for index, value in enumerate(guess) if value != secret[index])
    partial = sum(min(count, secret_rest[symbol]) for symbol, count in guess_rest.items())
    return exact, partial


def render_symbols(code: Sequence[int]) -> str:
    """给日志用的符号串（1~6 → 甲/乙/丙/丁/戊/己）。"""
    alphabet = "甲乙丙丁戊己庚辛"
    return "".join(alphabet[value - 1] if 1 <= value <= len(alphabet) else "?" for value in code)


# ----------------------------------------------------------------------
# 矿脉扫描（四号小行星带 · 扫雷式 8×8 推理探矿）
# ----------------------------------------------------------------------
def vein_spec() -> dict:
    return dict(B.MINIGAME_SPECS["vein_scan"])


def generate_vein_board(seed_key: str) -> list[list[bool]]:
    """确定性生成 8×8 棋盘与 12 处矿脉（同一种子 ⇒ 同一张图，可复现）。"""
    size = int(vein_spec()["grid_size"])
    veins = int(vein_spec()["vein_count"])
    digest = hashlib.sha256(f"vein:{seed_key}".encode("utf-8")).hexdigest()
    rng = random.Random(int(digest[:16], 16))
    cells = [(x, y) for y in range(size) for x in range(size)]
    chosen = set(rng.sample(cells, veins))
    return [[(x, y) in chosen for x in range(size)] for y in range(size)]


def neighbor_coords(x: int, y: int, size: int) -> list[tuple[int, int]]:
    return [
        (nx, ny)
        for ny in range(max(0, y - 1), min(size, y + 2))
        for nx in range(max(0, x - 1), min(size, x + 2))
        if (nx, ny) != (x, y)
    ]


def vein_hint(board: Sequence[Sequence[bool]], x: int, y: int) -> int:
    """扫雷式数字：相邻 8 格里的矿脉数量。"""
    size = len(board)
    return sum(1 for nx, ny in neighbor_coords(x, y, size) if board[ny][nx])


# ----------------------------------------------------------------------
# 熔炉配比（二号熔岩星 · 按比例投料逼近隐藏配方）
# ----------------------------------------------------------------------
def forge_spec() -> dict:
    return dict(B.MINIGAME_SPECS["forge_recipe"])


def generate_forge_recipe(seed_key: str) -> list[int]:
    """隐藏配方：三种投料的比例（0~100，和为 100）。"""
    digest = hashlib.sha256(f"forge:{seed_key}".encode("utf-8")).hexdigest()
    rng = random.Random(int(digest[:16], 16))
    first = rng.randrange(10, 81)
    second = rng.randrange(10, 91 - first)
    return [first, second, 100 - first - second]


def evaluate_mix(recipe: Sequence[int], mix: Sequence[int], *, tolerance: int = 8) -> dict:
    """反馈只有三类：太热 / 太冷 / 比例偏差（命中时才算找到配方）。"""
    total = sum(mix)
    if total > 100 + tolerance:
        return {"result": "TOO_HOT", "hint": "炉温过高：总投料偏多", "distance": total - 100}
    if total < 100 - tolerance:
        return {"result": "TOO_COLD", "hint": "炉温过低：总投料偏少", "distance": 100 - total}
    deviations = [mix[index] - recipe[index] for index in range(3)]
    if all(abs(value) <= tolerance for value in deviations):
        return {"result": "HIT", "hint": "配比稳定：高炉出料了", "distance": max(abs(v) for v in deviations)}
    worst = max(range(3), key=lambda index: abs(deviations[index]))
    direction = "偏多" if deviations[worst] > 0 else "偏少"
    return {
        "result": "RATIO_OFF",
        "hint": f"比例偏差：第 {worst + 1} 种投料{direction}",
        "worst_slot": worst,
        "distance": abs(deviations[worst]),
    }
