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
