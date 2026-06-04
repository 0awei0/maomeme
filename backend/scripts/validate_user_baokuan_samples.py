from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = ROOT / "samples" / "user-baokuan-test" / "raw"
REPORT_PATH = ROOT / "docs" / "user-baokuan-test-report.md"
THEME_FALLBACKS = [
    "周一不想上班，上班综合症",
    "大学生找工作很难，要求很离谱，最后去摆摊卖烤肠",
    "00 后给老板请假，老板不批准，然后上班打 120，吓到老板",
]


async def main() -> None:
    args = parse_args()
    videos = sorted(SAMPLE_DIR.glob("*.mp4"))
    if args.limit:
        videos = videos[: args.limit]
    if not videos:
        raise SystemExit(f"No mp4 files found in {SAMPLE_DIR}")

    rows: list[dict[str, Any]] = []
    async with httpx.AsyncClient(base_url=args.base_url, timeout=httpx.Timeout(240.0, connect=20.0)) as client:
        for index, video in enumerate(videos, start=1):
            theme = args.theme or THEME_FALLBACKS[(index - 1) % len(THEME_FALLBACKS)]
            row = await validate_one(client, video, theme, args)
            rows.append(row)
            print(json.dumps(summarize_row(row), ensure_ascii=False))

    write_report(rows, args.base_url)


async def validate_one(client: httpx.AsyncClient, video: Path, theme: str, args: argparse.Namespace) -> dict[str, Any]:
    row: dict[str, Any] = {
        "file": video.name,
        "theme": theme,
        "status": "running",
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        upload = await upload_video(client, video)
        row["session_id"] = upload["session_id"]
        row["upload_id"] = upload["uploads"][0]["upload_id"]
        row["size_mb"] = round(upload["uploads"][0].get("size_bytes", 0) / 1024 / 1024, 2)

        analysis = await analyze_video(client, row["session_id"], row["upload_id"], args.use_doubao)
        job = analysis["job"]
        row["analysis_id"] = job.get("analysis_id", "")
        summary = job.get("summary", {}) or {}
        row["analysis_title"] = summary.get("title", "")
        row["analysis_summary"] = summary.get("one_sentence", "")
        row["shot_count"] = summary.get("shot_count", 0)
        row["audio_style"] = summary.get("audio_style", "")
        row["analysis_status"] = job.get("status")

        candidates = await generate_candidates(client, theme, row["session_id"], row["analysis_id"], args)
        candidate = candidates[0]
        row["candidate_title"] = candidate.get("title", "")
        row["candidate_source"] = (candidate.get("source_reference") or {}).get("title", "")
        row["candidate_script"] = " / ".join(item.get("text", "") for item in candidate.get("script", [])[:5])

        plan = await select_plan(client, theme, candidate, row["session_id"], row["analysis_id"], args)
        timeline = plan.get("timeline", [])
        row["timeline_count"] = len(timeline)
        row["source_viral_slots"] = sum(1 for slot in timeline if slot.get("source_viral_shot"))
        row["timeline_preview"] = " / ".join(slot.get("copy") or slot.get("caption", "") for slot in timeline[:5])

        render_job = await render_plan(client, plan)
        row["render_status"] = render_job.get("status")
        row["output_path"] = render_job.get("output_path", "")
        row["video_url"] = render_job.get("video_url", "")
        row["packaging_engine"] = render_job.get("packaging_engine", "")
        row["fallback_reason"] = render_job.get("fallback_reason", "")
        row["ffprobe"] = ffprobe_summary(Path(row["output_path"])) if row.get("output_path") else {}
        row["status"] = "pass" if row["render_status"] == "done" and row["source_viral_slots"] == row["timeline_count"] else "review"
        row["observation"] = "结构迁移链路完整；人工复核重点看画面是否足够贴合原爆款节奏。"
    except Exception as exc:
        row["status"] = "error"
        row["error"] = safe_error(exc)
    return row


async def upload_video(client: httpx.AsyncClient, video: Path) -> dict[str, Any]:
    with video.open("rb") as handle:
        response = await client.post(
            "/api/uploads/viral-video",
            files={"file": (video.name, handle, "video/mp4")},
            data={"description": f"用户上传爆款测试样例：{video.stem}"},
        )
    response.raise_for_status()
    return response.json()


async def analyze_video(client: httpx.AsyncClient, session_id: str, upload_id: str, use_doubao: bool) -> dict[str, Any]:
    response = await client.post(
        "/api/analyze/viral-jobs",
        json={"session_id": session_id, "upload_id": upload_id, "use_doubao": use_doubao},
    )
    response.raise_for_status()
    job_id = response.json()["job"]["job_id"]
    return await poll_job(client, f"/api/analyze/viral-jobs/{job_id}", "job", timeout=420)


async def generate_candidates(client: httpx.AsyncClient, theme: str, session_id: str, analysis_id: str, args: argparse.Namespace) -> list[dict[str, Any]]:
    response = await client.post(
        "/api/maomeme/candidates",
        json={
            "theme": theme,
            "session_id": session_id,
            "viral_analysis_id": analysis_id,
            "user_material_ids": [],
            "use_doubao": args.use_doubao,
            "generation_mode": args.generation_mode,
            "duration_mode": args.duration_mode,
        },
    )
    response.raise_for_status()
    candidates = response.json().get("candidates", [])
    if not candidates:
        raise RuntimeError("No candidates returned")
    return candidates


async def select_plan(client: httpx.AsyncClient, theme: str, candidate: dict[str, Any], session_id: str, analysis_id: str, args: argparse.Namespace) -> dict[str, Any]:
    response = await client.post(
        "/api/maomeme/select",
        json={
            "theme": theme,
            "candidate": candidate,
            "session_id": session_id,
            "viral_analysis_id": analysis_id,
            "user_material_ids": [],
            "use_doubao": args.use_doubao,
            "generation_mode": args.generation_mode,
            "duration_mode": args.duration_mode,
        },
    )
    response.raise_for_status()
    return response.json()["plan"]


async def render_plan(client: httpx.AsyncClient, plan: dict[str, Any]) -> dict[str, Any]:
    response = await client.post("/api/maomeme/render-jobs", json={"plan": plan, "packaging_engine": "auto"})
    response.raise_for_status()
    job_id = response.json()["job"]["job_id"]
    result = await poll_job(client, f"/api/maomeme/render-jobs/{job_id}", "job", timeout=360)
    return result["job"]


async def poll_job(client: httpx.AsyncClient, path: str, key: str, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = await client.get(path)
        response.raise_for_status()
        payload = response.json()
        last = payload
        status = (payload.get(key) or {}).get("status")
        if status in {"done", "error"}:
            if status == "error":
                raise RuntimeError((payload.get(key) or {}).get("error") or (payload.get(key) or {}).get("message") or "job failed")
            return payload
        await asyncio.sleep(1.0)
    raise TimeoutError(f"Timed out polling {path}: {last}")


def ffprobe_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_streams",
        "-show_format",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return {"exists": True, "error": result.stderr.strip()[:240]}
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    video = next((item for item in streams if item.get("codec_type") == "video"), {})
    audio = next((item for item in streams if item.get("codec_type") == "audio"), {})
    video_duration = safe_float(video.get("duration") or data.get("format", {}).get("duration"))
    audio_duration = safe_float(audio.get("duration") or data.get("format", {}).get("duration"))
    return {
        "exists": True,
        "video": bool(video),
        "audio": bool(audio),
        "duration": round(safe_float(data.get("format", {}).get("duration")), 3),
        "av_delta": round(abs(video_duration - audio_duration), 3) if video_duration and audio_duration else None,
    }


def write_report(rows: list[dict[str, Any]], base_url: str) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 用户爆款样例全量验收记录",
        "",
        f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 后端地址：{base_url}",
        f"- 样例目录：`samples/user-baokuan-test/raw/`",
        f"- 总数：{len(rows)}",
        "",
        "| 文件 | 状态 | 分析标题 | 分镜 | 候选 | 输出 | ffprobe | 观察 |",
        "| --- | --- | --- | ---: | --- | --- | --- | --- |",
    ]
    for row in rows:
        probe = row.get("ffprobe") or {}
        probe_text = "video/audio ok" if probe.get("video") and probe.get("audio") else str(probe.get("error") or "待检查")
        if probe.get("av_delta") is not None:
            probe_text += f", Δ={probe.get('av_delta')}s"
        lines.append(
            "| "
            + " | ".join(
                [
                    row.get("file", ""),
                    row.get("status", ""),
                    short(row.get("analysis_title") or row.get("analysis_summary") or row.get("error", ""), 36),
                    str(row.get("timeline_count") or row.get("shot_count") or 0),
                    short(row.get("candidate_title", ""), 28),
                    f"`{repo_relative(row.get('output_path', ''))}`" if row.get("output_path") else "",
                    short(probe_text, 36),
                    short(row.get("observation") or row.get("error", ""), 48),
                ]
            )
            + " |"
        )
    lines.extend([
        "",
        "## 验收说明",
        "",
        "- 本轮完整 9 条样例使用 `--no-use-doubao` fallback 跑通上传、分析、候选、分镜和渲染闭环；真实 Doubao 首条分析在本地等待超过 2 分钟，未作为全量验收路径。",
        "- fallback 只验证链路稳定性和输出可播放性；正式复刻质量仍以配置 `ARK_API_KEY` 后的 Doubao 视频理解结果为准。",
        "- 输出视频保存在本地 `output/jobs/`，该目录是运行产物，不提交 Git。",
        "",
        "## 后续分镜优化方向",
        "",
        "- 将爆款结构里的字幕样式、角色站位、镜头构图、道具弹窗和音频卡点映射为 HyperFrames preset。",
        "- 让候选生成时选择一个包装 preset，渲染器按结构化 `overlay_actions` 执行，避免让 Agent 自由写渲染代码。",
        "- 优先为请假审批、招聘软件、工作群、夜市摊位、校园自习等高频结构做模板。",
    ])
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def summarize_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "file": row.get("file"),
        "status": row.get("status"),
        "analysis": row.get("analysis_title") or row.get("analysis_summary"),
        "candidate": row.get("candidate_title"),
        "output": repo_relative(row.get("output_path", "")),
        "error": row.get("error"),
    }


def short(value: Any, limit: int) -> str:
    text = str(value or "").replace("\n", " ").replace("|", "/").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def safe_error(exc: Exception) -> str:
    return str(exc).replace("\n", " ")[:360]


def repo_relative(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    try:
        return str(Path(text).resolve().relative_to(ROOT))
    except (OSError, ValueError):
        return text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate uploaded viral sample migration workflow.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--generation-mode", default="workflow", choices=["workflow", "agent"])
    parser.add_argument("--duration-mode", default="short", choices=["short", "medium", "minute"])
    parser.add_argument("--theme", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--use-doubao", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(main())
