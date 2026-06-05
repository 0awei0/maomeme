from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from ..core.config import get_settings
from ..models.maomeme import MaoMemePlan, RenderJobStatus
from .asset_index import load_assets, ref
from .maomeme_agent import motion_quality_flags, visual_summary_for_slot
from .viral_structure_library import (
    infer_theme_category,
    is_emotional_relationship_context,
    is_financial_relationship_context,
)

JOBS: dict[str, RenderJobStatus] = {}


def create_render_job(plan: MaoMemePlan, packaging_engine: str = "auto", allow_ai_fill: bool = False) -> RenderJobStatus:
    job_id = str(uuid.uuid4())[:8]
    engine = resolve_packaging_engine(packaging_engine)
    prepared_plan = prepare_plan_for_render(plan)
    status = RenderJobStatus(
        job_id=job_id,
        status="queued",
        progress=0.05,
        message="渲染任务已创建",
        packaging_engine=engine,
        fallback_reason=fallback_reason(packaging_engine, engine),
    )
    JOBS[job_id] = status
    asyncio.create_task(run_render_job(job_id, prepared_plan))
    return status


def get_render_job(job_id: str) -> RenderJobStatus | None:
    job = JOBS.get(job_id)
    if job:
        return job
    recovered = recover_render_job(job_id)
    if recovered:
        JOBS[job_id] = recovered
    return recovered


def prepare_plan_for_render(plan: MaoMemePlan) -> MaoMemePlan:
    data = plan.model_dump(by_alias=True)
    theme = str(data.get("theme", ""))
    safe_relationship_background = None
    timeline = []
    for slot in data.get("timeline", []):
        if not isinstance(slot, dict):
            continue
        next_slot = json.loads(json.dumps(slot, ensure_ascii=False))
        if emotional_relationship_slot(theme, next_slot):
            next_slot["overlay_actions"] = [
                action
                for action in next_slot.get("overlay_actions", [])
                if isinstance(action, dict) and not relationship_bill_overlay(action)
            ]
            if relationship_bill_background(next_slot.get("background", {})):
                if safe_relationship_background is None:
                    safe_relationship_background = fallback_relationship_background()
                if safe_relationship_background:
                    next_slot["background"] = safe_relationship_background
        motion = next_slot.get("motion") if isinstance(next_slot.get("motion"), dict) else {}
        quality = motion_quality_flags(motion)
        next_slot["motion_quality"] = {**next_slot.get("motion_quality", {}), **quality}
        if next_slot.get("layout") == "dialogue" and quality.get("natural_double"):
            next_slot["secondary_motion"] = None
            next_slot["secondary_motion_quality"] = {}
            next_slot["secondary_motion_clip"] = None
        next_slot["visual_summary"] = visual_summary_for_slot(next_slot)
        timeline.append(next_slot)
    data["timeline"] = timeline
    return MaoMemePlan.model_validate(data)


def emotional_relationship_slot(theme: str, slot: dict[str, Any]) -> bool:
    text = f"{theme} {slot.get('copy', '')} {slot.get('caption', '')} {slot.get('intent', '')}"
    return (
        infer_theme_category(text) == "relationship"
        and is_emotional_relationship_context(text)
        and not is_financial_relationship_context(text)
    )


def relationship_bill_overlay(action: dict[str, Any]) -> bool:
    text = json.dumps(action, ensure_ascii=False)
    return any(word in text for word in ("bill_card", "bill_stack", "账单", "房租", "押金", "预算", "首付", "房贷", "彩礼"))


def relationship_bill_background(background: Any) -> bool:
    if not isinstance(background, dict):
        return False
    text = f"{background.get('id', '')} {background.get('scene', '')} {background.get('file', '')} {background.get('description', '')}"
    return any(word in text for word in ("rental-bill", "rental_bill", "出租屋", "房租", "押金", "租房", "账单", "预算", "首付", "房贷", "彩礼"))


def fallback_relationship_background() -> dict[str, Any] | None:
    index = load_assets()
    preferred_ids = ("building_interior/5", "city/5", "city/2", "building_interior/3")
    for preferred_id in preferred_ids:
        for asset in index.get("backgrounds", []):
            if str(asset.get("id", "")) == preferred_id:
                return ref(asset)
    return None


async def run_render_job(job_id: str, plan: MaoMemePlan) -> None:
    settings = get_settings()
    status = JOBS[job_id]
    try:
        status.status = "running"
        status.progress = 0.18
        status.message = "正在写入渲染计划"
        plans_dir = settings.OUTPUT_DIR / "render_jobs"
        plans_dir.mkdir(parents=True, exist_ok=True)
        plan_path = plans_dir / f"{job_id}.json"
        plan_path.write_text(plan.model_dump_json(indent=2, by_alias=True), encoding="utf-8")

        output_dir = settings.PUBLIC_OUTPUT_DIR / "jobs"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{job_id}.mp4"

        status.progress = 0.35
        status.message = "正在合成字幕、绿幕和音频"
        render_task = asyncio.create_task(asyncio.to_thread(render_with_engine, settings.PROJECT_ROOT, plan_path, output_path, status.packaging_engine))
        await animate_render_progress(status, render_task, len(plan.timeline))
        runtime_fallback = render_task.result()
        if runtime_fallback:
            status.packaging_engine = "ffmpeg"
            status.fallback_reason = runtime_fallback

        status.progress = 0.88
        status.message = "正在校验视频"
        probe = ffprobe(output_path)
        if not probe.get("has_video") or not probe.get("has_audio"):
            raise RuntimeError(f"视频校验失败: {probe}")
        duration_delta = abs(float(probe.get("video_duration") or 0) - float(probe.get("audio_duration") or 0))
        if duration_delta > 0.25:
            raise RuntimeError(f"音视频时长不一致: {probe}")

        status.status = "done"
        status.progress = 1.0
        status.message = "视频生成完成"
        status.output_path = str(output_path)
        status.video_url = f"/output/jobs/{output_path.name}"
    except Exception as exc:
        status.status = "error"
        status.progress = 1.0
        status.message = "视频生成失败"
        status.error = str(exc)


def render_with_engine(root: Path, plan_path: Path, output_path: Path, engine: str) -> str | None:
    if engine == "hyperframes":
        try:
            render_with_hyperframes(root, plan_path, output_path)
            return None
        except Exception as exc:
            render_with_node(root, plan_path, output_path)
            return f"HyperFrames 渲染失败，已回退 FFmpeg/Pillow: {safe_subprocess_error(exc)}"
    render_with_node(root, plan_path, output_path)
    return None


def render_with_node(root: Path, plan_path: Path, output_path: Path) -> None:
    env = render_env()
    subprocess.run(
        [
            "node",
            str(root / "scripts" / "render-demo-video.mjs"),
            "--plan",
            str(plan_path),
            "--output",
            str(output_path),
        ],
        cwd=str(root),
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


def render_with_hyperframes(root: Path, plan_path: Path, output_path: Path) -> None:
    env = render_env()
    subprocess.run(
        [
            "node",
            str(root / "hyperframes" / "render.mjs"),
            "--plan",
            str(plan_path),
            "--output",
            str(output_path),
        ],
        cwd=str(root),
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


async def animate_render_progress(status: RenderJobStatus, render_task: asyncio.Task, slot_count: int) -> None:
    started = time.monotonic()
    estimated_seconds = max(10.0, slot_count * 4.5)
    settings = get_settings()
    render_id = status.job_id
    runtime_dir = settings.PUBLIC_OUTPUT_DIR / "runtime" / render_id
    while not render_task.done():
        elapsed = time.monotonic() - started
        eased = min(0.96, elapsed / estimated_seconds)
        file_progress = render_file_progress(runtime_dir, slot_count)
        status.progress = max(status.progress, min(0.86, 0.35 + max(eased, file_progress) * 0.48))
        status.message = render_progress_message(status.progress)
        await asyncio.sleep(0.7)
    await render_task


def render_file_progress(runtime_dir: Path, slot_count: int) -> float:
    if slot_count <= 0 or not runtime_dir.exists():
        return 0.0
    segments = len(list((runtime_dir / "segments").glob("*.mp4"))) if (runtime_dir / "segments").exists() else 0
    captions = len(list((runtime_dir / "captions").glob("*.png"))) if (runtime_dir / "captions").exists() else 0
    overlays = sum(1 for _ in (runtime_dir / "overlays").glob("*/*.png")) if (runtime_dir / "overlays").exists() else 0
    segment_ratio = min(1.0, segments / max(1, slot_count))
    caption_ratio = min(1.0, captions / max(1, slot_count))
    overlay_ratio = min(1.0, overlays / max(1, slot_count * 20))
    return max(segment_ratio, caption_ratio * 0.45, overlay_ratio * 0.35)


def render_progress_message(progress: float) -> str:
    if progress < 0.48:
        return "正在生成字幕与包装帧"
    if progress < 0.62:
        return "正在裁剪猫素材并保留原声"
    if progress < 0.76:
        return "正在叠加背景、绿幕和转场"
    return "正在拼接视频片段"


def render_env() -> dict[str, str]:
    env = os.environ.copy()
    conda_prefix = env.get("CONDA_PREFIX", "")
    conda_python = Path(conda_prefix) / "bin" / "python" if conda_prefix else None
    if conda_python and conda_python.exists():
        env.setdefault("MAOMEME_PYTHON", str(conda_python))
    elif sys.executable:
        env.setdefault("MAOMEME_PYTHON", sys.executable)
    env.setdefault("RENDER_SEGMENT_CONCURRENCY", "2")
    env.setdefault("RENDER_FFMPEG_PRESET", "veryfast")
    return env


def ffprobe(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,duration",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    parsed = json.loads(result.stdout)
    streams = parsed.get("streams", [])
    video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), {})
    return {
        "has_video": any(stream.get("codec_type") == "video" for stream in streams),
        "has_audio": any(stream.get("codec_type") == "audio" for stream in streams),
        "video_duration": float(video_stream.get("duration") or 0),
        "audio_duration": float(audio_stream.get("duration") or 0),
    }


def resolve_packaging_engine(requested: str) -> str:
    if requested in {"auto", "hyperframes"} and hyperframes_available():
        return "hyperframes"
    return "ffmpeg"


def fallback_reason(requested: str, engine: str) -> str | None:
    if requested in {"auto", "hyperframes"} and engine == "ffmpeg":
        return "HyperFrames 未安装，已使用稳定 FFmpeg/Pillow 渲染"
    return None


def hyperframes_available() -> bool:
    root = get_settings().PROJECT_ROOT / "hyperframes"
    return (root / "package.json").exists() and (root / "render.mjs").exists()


def safe_subprocess_error(exc: Exception) -> str:
    text = str(exc)
    return text[:300]


def recover_render_job(job_id: str) -> RenderJobStatus | None:
    settings = get_settings()
    output_path = settings.PUBLIC_OUTPUT_DIR / "jobs" / f"{job_id}.mp4"
    plan_path = settings.OUTPUT_DIR / "render_jobs" / f"{job_id}.json"
    if output_path.exists():
        return RenderJobStatus(
            job_id=job_id,
            status="done",
            progress=1.0,
            message="视频生成完成",
            output_path=str(output_path),
            video_url=f"/output/jobs/{output_path.name}",
            packaging_engine="hyperframes" if (settings.PROJECT_ROOT / "backend" / "outputs" / "hyperframes" / f"{job_id}.json").exists() else "ffmpeg",
        )
    if plan_path.exists():
        return RenderJobStatus(
            job_id=job_id,
            status="error",
            progress=1.0,
            message="渲染任务已中断，请重新生成视频",
            error="后端重启后运行中任务状态已丢失，未找到完成视频。",
        )
    return None
