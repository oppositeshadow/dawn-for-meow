"""第三方 LLM 接入层（星区推演导演）：低频高影响 + 三道防火墙 + 预算熔断。

口径见《代码结构与核心工程详细设计规范》第 6 章：

1. 校验链：Pydantic Schema（调用方负责）→ DAG 前置校验 → 本地静态池抽卡；
2. 失败重试：校验或超时失败自动重试最多 `LLM_MAX_RETRY`（默认 2）次，仍失败即走兜底，玩家零感知；
3. 预算熔断：达到 `LLM_DAILY_CALL_BUDGET` / `LLM_DAILY_TOKEN_BUDGET` 后**自动停止调用**；
4. 可观测：每次调用记录场景 / 耗时 / prompt token / completion token / 是否走兜底。

密钥只从本地 `.env` 读取，绝不写进代码、日志或仓库。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.core.config import BACKEND_DIR, get_settings

logger = logging.getLogger("dawn_meow.llm")

USAGE_FILE = BACKEND_DIR / "var" / "llm_usage.json"


class LlmScene:
    """六个调用场景的常量（代码结构稿 §6.2）。"""

    PLANET_ENTRY = "PLANET_ENTRY"            # 1 新行星登录：生态环境与词缀
    TECH_CARD = "TECH_CARD"                  # 2 特化科技卡
    PHASE_TEMPLATES = "PHASE_TEMPLATES"      # 3 时代语料批处理（≤200 条）
    FORUM_JUDGE = "FORUM_JUDGE"              # 4 发帖做局裁判
    PLANT_CODEX = "PLANT_CODEX"              # 5 异星植物学图鉴
    EPIC_EPITAPH = "EPIC_EPITAPH"            # 6 通关碑文（唯一允许超预算放行）


@dataclass
class LlmUsage:
    scene: str
    model: str
    ok: bool
    attempts: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    duration_ms: int = 0
    fell_back: bool = False
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LlmResult:
    payload: Any | None
    usage: LlmUsage

    @property
    def ok(self) -> bool:
        return self.usage.ok and self.payload is not None


@dataclass
class BudgetState:
    day: str = ""
    calls: int = 0
    tokens: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)

    def roll(self, today: str) -> None:
        if self.day != today:
            self.day = today
            self.calls = 0
            self.tokens = 0


class LlmBudget:
    """每日预算账本（落盘到 backend/var/llm_usage.json，重启不丢账）。"""

    def __init__(self, path: Path = USAGE_FILE) -> None:
        self.path = path
        self.state = BudgetState()
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.state = BudgetState(
                day=raw.get("day", ""),
                calls=int(raw.get("calls", 0)),
                tokens=int(raw.get("tokens", 0)),
                history=list(raw.get("history", []))[-50:],
            )
        except (OSError, ValueError):
            self.state = BudgetState()

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(
                    {
                        "day": self.state.day,
                        "calls": self.state.calls,
                        "tokens": self.state.tokens,
                        "history": self.state.history[-50:],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError as exc:  # pragma: no cover - 磁盘异常
            logger.warning("预算账本写入失败：%s", exc)

    def snapshot(self) -> dict[str, Any]:
        today = time.strftime("%Y-%m-%d")
        self.state.roll(today)
        settings = get_settings()
        return {
            "day": self.state.day,
            "calls": self.state.calls,
            "tokens": self.state.tokens,
            "call_budget": settings.llm_daily_call_budget,
            "token_budget": settings.llm_daily_token_budget,
            "exhausted": self.exhausted(),
        }

    def exhausted(self) -> bool:
        settings = get_settings()
        self.state.roll(time.strftime("%Y-%m-%d"))
        return (
            self.state.calls >= settings.llm_daily_call_budget
            or self.state.tokens >= settings.llm_daily_token_budget
        )

    def record(self, usage: LlmUsage) -> None:
        self.state.calls += usage.attempts
        self.state.tokens += usage.prompt_tokens + usage.completion_tokens
        self.state.history.append(
            {
                "at": int(time.time()),
                "scene": usage.scene,
                "model": usage.model,
                "ok": usage.ok,
                "fell_back": usage.fell_back,
                "tokens": usage.prompt_tokens + usage.completion_tokens,
                "duration_ms": usage.duration_ms,
                "reason": usage.reason,
            }
        )
        self._save()


class LlmService:
    """OpenAI 兼容的 Chat Completions 客户端（硅基流动 / DeepSeek 通吃）。"""

    def __init__(self, budget: LlmBudget | None = None) -> None:
        self.budget = budget or LlmBudget()

    # ---------------------------------------------------------------- 基础
    @property
    def settings(self):
        return get_settings()

    @property
    def configured(self) -> bool:
        return bool(self.settings.llm_api_key)

    @staticmethod
    def _extract_json(text: str) -> Any | None:
        """从模型输出里抠出 JSON（容忍 ``` 围栏与前后废话）。"""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:]
        try:
            return json.loads(cleaned)
        except ValueError:
            pass
        for opener, closer in (("{", "}"), ("[", "]")):
            start = cleaned.find(opener)
            end = cleaned.rfind(closer)
            if start != -1 and end > start:
                try:
                    return json.loads(cleaned[start : end + 1])
                except ValueError:
                    continue
        return None

    # ---------------------------------------------------------------- 调用
    async def complete_json(
        self,
        *,
        scene: str,
        system: str,
        user: str,
        max_tokens: int = 900,
        temperature: float = 0.9,
    ) -> LlmResult:
        """返回 (payload, usage)。任何异常都不抛给调用方，一律降级为 fell_back。"""
        settings = self.settings
        usage = LlmUsage(scene=scene, model=settings.llm_model, ok=False)

        if not self.configured:
            usage.reason = "LLM_NOT_CONFIGURED"
            usage.fell_back = True
            logger.info("LLM 未配置密钥，场景 %s 直接走本地兜底池", scene)
            return LlmResult(None, usage)

        if self.budget.exhausted() and scene != LlmScene.EPIC_EPITAPH:
            usage.reason = "BUDGET_EXHAUSTED"
            usage.fell_back = True
            logger.warning(
                "LLM 预算耗尽（calls=%s/%s tokens=%s/%s），场景 %s 走本地兜底池",
                self.budget.state.calls,
                settings.llm_daily_call_budget,
                self.budget.state.tokens,
                settings.llm_daily_token_budget,
                scene,
            )
            return LlmResult(None, usage)

        started = time.perf_counter()
        url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"
        body: dict[str, Any] = {
            "model": settings.llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }

        payload: Any | None = None
        for attempt in range(1, settings.llm_max_retry + 2):
            usage.attempts = attempt
            try:
                async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
                    response = await client.post(
                        url,
                        json=body,
                        headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                    )
                response.raise_for_status()
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                raw_usage = data.get("usage") or {}
                usage.prompt_tokens += int(raw_usage.get("prompt_tokens", 0))
                usage.completion_tokens += int(raw_usage.get("completion_tokens", 0))
                parsed = self._extract_json(content)
                if parsed is None:
                    usage.reason = "JSON_PARSE_FAILED"
                    continue
                payload = parsed
                usage.ok = True
                usage.reason = None
                break
            except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
                usage.reason = f"{type(exc).__name__}: {exc}"
                logger.warning("LLM 调用失败（场景 %s，第 %s 次）：%s", scene, attempt, exc)

        usage.duration_ms = int((time.perf_counter() - started) * 1000)
        if not usage.ok:
            usage.fell_back = True
        self.budget.record(usage)
        logger.info(
            "LLM 调用完成 scene=%s model=%s ok=%s attempts=%s tokens=%s+%s duration=%sms fallback=%s reason=%s",
            scene,
            usage.model,
            usage.ok,
            usage.attempts,
            usage.prompt_tokens,
            usage.completion_tokens,
            usage.duration_ms,
            usage.fell_back,
            usage.reason,
        )
        return LlmResult(payload, usage)


_service: LlmService | None = None


def get_llm_service() -> LlmService:
    """进程内单例（预算账本随之复用）。"""
    global _service
    if _service is None:
        _service = LlmService()
    return _service


def reset_llm_service() -> None:
    """测试用：丢掉单例，重建预算账本。"""
    global _service
    _service = None
