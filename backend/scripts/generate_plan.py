from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.maomeme_agent import generate_maomeme_plan, generate_script_candidates, plan_from_candidate


async def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a MaoMeme plan with the backend agent.")
    parser.add_argument("theme", help="新视频主题")
    parser.add_argument("--sample", default=None, help="爆款样例视频路径")
    parser.add_argument("--no-doubao", action="store_true", help="禁用豆包，使用本地 fallback")
    parser.add_argument("--duration-mode", default="short", choices=["short", "medium", "minute"], help="目标时长模式")
    parser.add_argument("--candidate-index", type=int, default=1, help="选择第几个候选剧本生成分镜")
    args = parser.parse_args()

    if args.duration_mode != "short" or args.candidate_index:
        candidates = await generate_script_candidates(
            theme=args.theme,
            sample_video_path=args.sample,
            use_doubao=not args.no_doubao,
            duration_mode=args.duration_mode,
        )
        selected = candidates[max(0, min(len(candidates) - 1, args.candidate_index - 1))]
        plan = await plan_from_candidate(
            theme=args.theme,
            candidate=selected,
            sample_video_path=args.sample,
            use_doubao=not args.no_doubao,
            duration_mode=args.duration_mode,
        )
    else:
        plan = await generate_maomeme_plan(
            theme=args.theme,
            sample_video_path=args.sample,
            use_doubao=not args.no_doubao,
        )
    print(plan.model_dump_json(indent=2, by_alias=True))


if __name__ == "__main__":
    asyncio.run(main())
