from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.maomeme_agent import generate_maomeme_plan


async def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a MaoMeme plan with the backend agent.")
    parser.add_argument("theme", help="新视频主题")
    parser.add_argument("--sample", default=None, help="爆款样例视频路径")
    parser.add_argument("--no-doubao", action="store_true", help="禁用豆包，使用本地 fallback")
    args = parser.parse_args()

    plan = await generate_maomeme_plan(
        theme=args.theme,
        sample_video_path=args.sample,
        use_doubao=not args.no_doubao,
    )
    print(plan.model_dump_json(indent=2, by_alias=True))


if __name__ == "__main__":
    asyncio.run(main())
