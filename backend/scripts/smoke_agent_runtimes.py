from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.core.config import get_settings  # noqa: E402
from app.services.agent_runtime import run_shot_agent  # noqa: E402
from app.services.asset_index import load_assets  # noqa: E402


TEST_BEAT = {
    "id": "01-hook",
    "start": 0.0,
    "end": 3.8,
    "role": "hook",
    "theme": "大学生工作难找，投简历像进黑洞",
    "intent": "用具体招聘软件动作切入求职压力",
    "caption": "刷到薪资还行的岗位",
    "scene_keywords": ["招聘软件", "校招", "办公楼", "手机"],
    "emotion_keywords": ["震惊", "电脑", "探头", "求职"],
    "must_keywords": [],
    "forbidden_keywords": ["开车", "小狗", "山羊"],
    "layout": "single",
    "dialogue": [],
}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=["ark", "openai_agents"], default="ark")
    parser.add_argument("--openai-provider", choices=["auto", "ark", "openai"], default=None)
    parser.add_argument("--model-mode", choices=["pro", "lite"], default=None)
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()

    if args.provider == "ark":
        os.environ["AGENT_RUNTIME"] = "ark"
    else:
        os.environ["AGENT_RUNTIME"] = "openai_agents"
        if args.openai_provider:
            os.environ["OPENAI_AGENTS_PROVIDER"] = args.openai_provider
    if args.model_mode:
        os.environ["ARK_MODEL_MODE"] = args.model_mode
    get_settings.cache_clear()
    settings = get_settings()

    configured = {
        "ark_key": bool(settings.ARK_API_KEY),
        "openai_key": bool(settings.OPENAI_API_KEY),
        "agent_runtime": settings.AGENT_RUNTIME,
        "openai_agents_provider": settings.OPENAI_AGENTS_PROVIDER,
        "model_mode": settings.ARK_MODEL_MODE,
        "model": "configured" if settings.chat_model() else "missing",
    }
    print(json.dumps({"configured": configured}, ensure_ascii=False))

    if args.provider == "ark" and not settings.ARK_API_KEY:
        print(json.dumps({"status": "skipped", "reason": "ARK_API_KEY not configured"}, ensure_ascii=False))
        return
    if args.provider == "openai_agents" and not (settings.OPENAI_API_KEY or settings.ARK_API_KEY):
        print(json.dumps({"status": "skipped", "reason": "no compatible API key configured"}, ensure_ascii=False))
        return

    index = load_assets()
    started = time.monotonic()
    try:
        async with asyncio.timeout(args.timeout):
            result = await run_shot_agent(theme=TEST_BEAT["theme"], beat=TEST_BEAT, index=index)
    except TimeoutError:
        print(json.dumps({"status": "error", "provider": args.provider, "error": "timeout"}, ensure_ascii=False))
        return
    elapsed = round(time.monotonic() - started, 2)
    slot = result.output.get("slot") if isinstance(result.output, dict) else None
    print(json.dumps({
        "status": "ok" if result.ok and isinstance(slot, dict) else "error",
        "provider": result.provider,
        "elapsed_sec": elapsed,
        "slot_id": slot.get("id") if isinstance(slot, dict) else "",
        "motion_id": (slot.get("motion") or {}).get("id") if isinstance(slot, dict) else "",
        "background_id": (slot.get("background") or {}).get("id") if isinstance(slot, dict) else "",
        "overlay_count": len(slot.get("overlay_actions") or []) if isinstance(slot, dict) else 0,
        "error": result.error,
    }, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
