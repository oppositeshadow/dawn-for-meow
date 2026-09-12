"""LLM 真机冒烟：跑一次"时代语料批处理"（默认只发 6 条，成本极低）。

用法（backend/ 目录下）::

    python scripts/llm_smoke.py            # 6 条
    python scripts/llm_smoke.py --count 20 # 想多要点语料

会真实调用 .env 里配置的 LLM（默认硅基流动 + 便宜小模型），并把结果写入 dev 库的 `event_templates`。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.core.database import configure_database, dispose_engine, get_sessionmaker  # noqa: E402
from app.services import radio_service  # noqa: E402


async def run(count: int) -> int:
    settings = get_settings()
    print(f"LLM: {settings.llm_provider} / {settings.llm_model} / {settings.llm_base_url}")
    print(f"每日预算：{settings.llm_daily_call_budget} 次调用 / {settings.llm_daily_token_budget} tokens")
    if not settings.llm_api_key:
        print("[提示] 未配置 LLM_API_KEY，本次只会走本地兜底池")

    await configure_database()
    factory = get_sessionmaker()
    async with factory() as session:
        result = await radio_service.generate_batch(session, count=count)
        print(
            f"生成结果：source={result['source']} generated={result['generated']} "
            f"pool={result['pool_size']} fell_back={result['fell_back']}"
        )
        usage = result["usage"]
        print(
            f"调用账本：attempts={usage['attempts']} tokens={usage['prompt_tokens']}+"
            f"{usage['completion_tokens']} duration={usage['duration_ms']}ms reason={usage['reason']}"
        )
        if result.get("note"):
            print(f"说明：{result['note']}")
        print(f"今日预算：{result['budget']}")

        feed = await radio_service.feed(session, limit=5)
        print(f"\n公频试播（池 {feed['pool_size']} 条，本次新增种子 {feed['seeded']} 条）：")
        for item in feed["items"]:
            print(f"  [{item['category']}] {item['text']}")
    await dispose_engine()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="喵星破晓 LLM 冒烟脚本")
    parser.add_argument("--count", type=int, default=6, help="本次生成的语料条数（默认 6，省钱）")
    args = parser.parse_args()
    return asyncio.run(run(args.count))


if __name__ == "__main__":
    raise SystemExit(main())
