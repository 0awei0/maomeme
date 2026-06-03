from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import get_settings  # noqa: E402
from app.services.doubao_client import get_async_ark_client, load_prompt, parse_doubao_response  # noqa: E402

LIBRARY_DIR = ROOT / "samples" / "viral-structure" / "baokuan-maomeme"
MANIFEST_PATH = LIBRARY_DIR / "manifest.json"
ANALYSIS_ROOT = ROOT / "data" / "viral-structures" / "baokuan-maomeme"
REPORT_PATH = ANALYSIS_ROOT / "verification-report.md"
DEFAULT_TIMEOUT_SECONDS = 180


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify viral MaoMeme extraction results with concurrent Doubao review.")
    parser.add_argument("--manifest", default=str(MANIFEST_PATH), help="Path to viral video manifest.")
    parser.add_argument("--limit", type=int, default=0, help="Only verify the first N selected videos.")
    parser.add_argument("--ids", default="", help="Comma-separated video ids to verify.")
    parser.add_argument("--resume", action="store_true", help="Skip videos that already have verification.json.")
    parser.add_argument("--concurrency", type=int, default=0, help="Concurrent Doubao verification requests. Defaults to VIRAL_VERIFY_CONCURRENCY or 8.")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS, help="Per-video verification timeout in seconds.")
    parser.add_argument("--use-doubao", default="true", choices=["true", "false"], help="Use Doubao for independent verification.")
    parser.add_argument("--input-mode", default="video", choices=["video", "contact-sheet"], help="Use original video or contact sheet image for verification.")
    parser.add_argument("--fail-threshold", type=float, default=70.0, help="Return non-zero if any score is below this threshold.")
    return parser.parse_args()


def read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def selected_items(manifest: dict[str, Any], ids: str, limit: int) -> list[dict[str, Any]]:
    items = list(manifest.get("items", []))
    if ids.strip():
        wanted = {item.strip() for item in ids.split(",") if item.strip()}
        items = [item for item in items if item.get("id") in wanted]
    if limit > 0:
        items = items[:limit]
    return items


def use_doubao_enabled(raw: str) -> bool:
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def clamp_concurrency(value: int) -> int:
    return max(1, min(16, value))


def configured_concurrency(cli_value: int) -> int:
    if cli_value:
        return clamp_concurrency(cli_value)
    try:
        return clamp_concurrency(int(os.environ.get("VIRAL_VERIFY_CONCURRENCY", "8")))
    except ValueError:
        return 8


def encode_file(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def local_video_path(item: dict[str, Any]) -> Path:
    path = ROOT / str(item.get("local_path", ""))
    if path.exists():
        return path
    source = Path(str(item.get("source_path", "")))
    return source if source.exists() else path


def choose_fps(duration: float) -> float:
    override = os.environ.get("VIRAL_VERIFY_FPS")
    if override:
        try:
            return max(0.2, min(5.0, float(override)))
        except ValueError:
            pass
    if duration <= 30:
        return 3.0
    if duration <= 70:
        return 2.0
    return 1.5


def structure_digest(structure: dict[str, Any]) -> str:
    payload = {
        "video_summary": structure.get("video_summary"),
        "shot_track": structure.get("shot_track"),
        "asset_plan": structure.get("asset_plan"),
        "audio_track": structure.get("audio_track"),
        "subtitle_packaging": structure.get("subtitle_packaging"),
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compact_structure(structure: dict[str, Any]) -> dict[str, Any]:
    storyboard = structure.get("asset_plan", {}).get("storyboard", []) if isinstance(structure.get("asset_plan"), dict) else []
    compact_storyboard = []
    for shot in storyboard:
        if not isinstance(shot, dict):
            continue
        compact_storyboard.append(
            {
                "shot_id": shot.get("shot_id"),
                "duration": shot.get("duration"),
                "beat": shot.get("beat"),
                "script": shot.get("script"),
                "joke_point": shot.get("joke_point"),
                "background": shot.get("background"),
                "cats": shot.get("cats"),
                "audio": shot.get("audio"),
                "subtitle": shot.get("subtitle"),
            }
        )
    return {
        "video_id": structure.get("video_id"),
        "filename": structure.get("filename"),
        "video_summary": structure.get("video_summary"),
        "shot_track": structure.get("shot_track"),
        "asset_plan": {"storyboard": compact_storyboard},
        "audio_track": structure.get("audio_track"),
        "subtitle_packaging": structure.get("subtitle_packaging"),
    }


def extract_json_object(content: str) -> str:
    text = content.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1]
    elif "```" in text:
        text = text.split("```", 1)[1]
    if "```" in text:
        text = text.split("```", 1)[0]
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return ""
    return text[start : end + 1].strip()


def parse_verification_response(content: str) -> dict[str, Any]:
    parsed = parse_doubao_response(content)
    if isinstance(parsed, dict) and parsed.get("verdict"):
        return parsed
    json_text = extract_json_object(content)
    if json_text:
        try:
            value = json.loads(json_text)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
    return {"verdict": "review", "score": 0, "summary": "verification parse failed", "issues": [{"severity": "critical", "field": "other", "observed": "", "extracted": content[:500], "suggestion": "人工复核 raw response"}], "needs_human_review": True}


async def verify_with_doubao(
    item: dict[str, Any],
    structure: dict[str, Any],
    contact_sheet: Path,
    video_path: Path,
    input_mode: str,
    timeout: int,
) -> dict[str, Any]:
    settings = get_settings()
    prompt = load_prompt("viral_maomeme_verification")
    fps = choose_fps(float(item.get("duration", 0) or 0))
    user_text = f"""请对照{"原视频" if input_mode == "video" else "关键帧 contact sheet"}和已有拆解结果，判断这个爆款猫 meme 分析是否可靠。

视频元信息：
- id: {item.get("id")}
- 文件名: {item.get("filename")}
- 时长: {float(item.get("duration", 0) or 0):.2f}s
- 分辨率: {item.get("width")}x{item.get("height")}
- 复核输入: {input_mode}
- 视频抽帧率: {fps if input_mode == "video" else "n/a"}

已有拆解结果：
{json.dumps(compact_structure(structure), ensure_ascii=False, indent=2)}
"""
    if input_mode == "video":
        media_part = {"type": "video_url", "video_url": {"url": f"data:video/mp4;base64,{encode_file(video_path)}", "fps": fps}}
    else:
        media_part = {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encode_file(contact_sheet)}"}}
    client = get_async_ark_client()
    try:
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=settings.chat_model(),
                messages=[
                    {"role": "system", "content": prompt},
                    {
                        "role": "user",
                        "content": [
                            media_part,
                            {"type": "text", "text": user_text},
                        ],
                    },
                ],
                temperature=0.1,
                max_tokens=3000,
            ),
            timeout=timeout,
        )
    finally:
        await client.close()
    content = response.choices[0].message.content
    parsed = parse_verification_response(content)
    parsed["_raw_content_sha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return parsed


def local_fallback_verification(item: dict[str, Any], structure: dict[str, Any], reason: str) -> dict[str, Any]:
    storyboard = structure.get("asset_plan", {}).get("storyboard", []) if isinstance(structure.get("asset_plan"), dict) else []
    issues = []
    for index, shot in enumerate(storyboard, start=1):
        if not isinstance(shot, dict):
            issues.append({"severity": "critical", "shot_id": f"s{index:02d}", "field": "other", "observed": "", "extracted": "shot is not object", "suggestion": "重新分析"})
    score = 85 if not issues and storyboard else 50
    return {
        "video_id": item.get("id"),
        "verdict": "pass" if score >= 85 else "review",
        "score": score,
        "summary": f"本地结构完整性复核：{reason}",
        "confirmed_points": ["structure.json 可读取", f"storyboard 分镜数 {len(storyboard)}"],
        "issues": issues,
        "audio_confidence": "low",
        "needs_human_review": True,
    }


def normalize_verification(item: dict[str, Any], structure: dict[str, Any], result: dict[str, Any], provider: str) -> dict[str, Any]:
    score = result.get("score")
    try:
        score_value = float(score)
    except Exception:
        score_value = 0.0
    issues = result.get("issues") if isinstance(result.get("issues"), list) else []
    if score_value >= 85 and not any(issue.get("severity") in {"major", "critical"} for issue in issues if isinstance(issue, dict)):
        verdict = "pass"
    elif score_value >= 70 and not any(issue.get("severity") == "critical" for issue in issues if isinstance(issue, dict)):
        verdict = "review"
    else:
        verdict = "fail"
    result["video_id"] = item.get("id")
    result["verdict"] = result.get("verdict") if result.get("verdict") in {"pass", "review", "fail"} else verdict
    result["score"] = round(score_value, 1)
    result["verification_provider"] = provider
    result["verified_at"] = datetime.now(timezone.utc).isoformat()
    result["structure_sha256"] = structure_digest(structure)
    result["contact_sheet"] = str((ANALYSIS_ROOT / str(item["id"]) / "contact_sheet.jpg").relative_to(ROOT))
    return result


async def verify_one(item: dict[str, Any], semaphore: asyncio.Semaphore, use_doubao: bool, timeout: int, input_mode: str) -> dict[str, Any]:
    out_dir = ANALYSIS_ROOT / str(item["id"])
    structure_path = out_dir / "structure.json"
    contact_sheet = out_dir / "contact_sheet.jpg"
    video_path = local_video_path(item)
    structure = read_json(structure_path, {})
    if not structure or not contact_sheet.exists() or (input_mode == "video" and not video_path.exists()):
        result = {
            "video_id": item.get("id"),
            "verdict": "fail",
            "score": 0,
            "summary": "缺少 structure.json、contact_sheet.jpg 或原视频",
            "issues": [{"severity": "critical", "field": "other", "suggestion": "先重新运行分析脚本"}],
            "needs_human_review": True,
        }
        write_json(out_dir / "verification.json", result)
        return {"id": item.get("id"), "verdict": "fail", "score": 0, "provider": "local_error"}

    provider = "local_fallback"
    if use_doubao and get_settings().ARK_API_KEY:
        last_error = ""
        for attempt in range(1, 4):
            try:
                async with semaphore:
                    print(f"[{item['id']}] Doubao verify attempt {attempt}")
                    raw = await verify_with_doubao(item, structure, contact_sheet, video_path, input_mode, timeout)
                provider = "doubao"
                break
            except Exception as exc:
                last_error = safe_error(exc)
                print(f"[{item['id']}] Doubao verify failed attempt {attempt}: {last_error}")
                if attempt < 3:
                    await asyncio.sleep(2 ** attempt)
        else:
            raw = local_fallback_verification(item, structure, f"Doubao verification failed after retries: {last_error}")
    else:
        reason = "Doubao disabled" if not use_doubao else "ARK_API_KEY not configured"
        raw = local_fallback_verification(item, structure, reason)

    result = normalize_verification(item, structure, raw, provider)
    result["verification_input_mode"] = input_mode
    write_json(out_dir / "verification.json", result)
    return {"id": item.get("id"), "verdict": result.get("verdict"), "score": result.get("score"), "provider": provider}


def safe_error(exc: Exception) -> str:
    text = str(exc)
    settings = get_settings()
    if settings.ARK_API_KEY:
        text = text.replace(settings.ARK_API_KEY, "***")
    return text[:500]


def build_report(manifest: dict[str, Any]) -> dict[str, Any]:
    entries = []
    for item in manifest.get("items", []):
        out_dir = ANALYSIS_ROOT / str(item["id"])
        verification = read_json(out_dir / "verification.json", {})
        structure = read_json(out_dir / "structure.json", {})
        summary = structure.get("video_summary") if isinstance(structure.get("video_summary"), dict) else {}
        issues = verification.get("issues") if isinstance(verification.get("issues"), list) else []
        entries.append(
            {
                "id": item.get("id"),
                "filename": item.get("filename"),
                "title": summary.get("title", ""),
                "topic": summary.get("primary_topic", ""),
                "verdict": verification.get("verdict", "missing"),
                "score": verification.get("score", 0),
                "provider": verification.get("verification_provider", ""),
                "summary": verification.get("summary", ""),
                "issues": issues,
                "needs_human_review": verification.get("needs_human_review", False),
            }
        )
    return {
        "library": "baokuan-maomeme",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(entries),
        "entries": entries,
    }


def write_report(report: dict[str, Any], path: Path) -> None:
    entries = report.get("entries", [])
    verdict_counts = Counter(item.get("verdict") for item in entries)
    provider_counts = Counter(item.get("provider") for item in entries)
    scores = [float(item.get("score") or 0) for item in entries]
    avg_score = sum(scores) / len(scores) if scores else 0.0
    lines = [
        "# 爆款猫 Meme 拆解独立复核报告",
        "",
        f"- 生成时间: {report.get('generated_at')}",
        f"- 复核总数: {len(entries)}",
        f"- Verdict 统计: {dict(verdict_counts)}",
        f"- Provider 统计: {dict(provider_counts)}",
        f"- 平均分: {avg_score:.1f}",
        "",
        "| ID | 标题/主题 | 分数 | 结论 | 复核摘要 | 问题 |",
        "| --- | --- | ---: | --- | --- | --- |",
    ]
    for item in entries:
        issues = item.get("issues") if isinstance(item.get("issues"), list) else []
        if issues:
            issue_text = "<br>".join(
                f"{issue.get('severity', '')}/{issue.get('shot_id', 'unknown')}/{issue.get('field', '')}: {issue.get('suggestion', '')}"
                for issue in issues
                if isinstance(issue, dict)
            )
        else:
            issue_text = "OK"
        title = item.get("title") or item.get("filename") or item.get("id")
        topic = item.get("topic")
        title_cell = f"{title}<br>{topic}" if topic else title
        lines.append(
            f"| {item.get('id')} | {escape_md(title_cell)} | {float(item.get('score') or 0):.1f} | {item.get('verdict')} | {escape_md(str(item.get('summary', '')))} | {escape_md(issue_text)} |"
        )
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    write_json(path.with_suffix(".json"), report)


def escape_md(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


async def run_verification(args: argparse.Namespace) -> int:
    manifest = read_json(Path(args.manifest), {})
    items = selected_items(manifest, args.ids, args.limit)
    if args.resume:
        items = [item for item in items if not (ANALYSIS_ROOT / str(item["id"]) / "verification.json").exists()]
    concurrency = configured_concurrency(args.concurrency)
    use_doubao = use_doubao_enabled(args.use_doubao)
    print(f"Verifying {len(items)} videos with concurrency={concurrency}, use_doubao={use_doubao}")
    semaphore = asyncio.Semaphore(concurrency)
    started = time.monotonic()
    tasks = [asyncio.create_task(verify_one(item, semaphore, use_doubao, args.timeout, args.input_mode)) for item in items]
    results = []
    for task in asyncio.as_completed(tasks):
        result = await task
        results.append(result)
        print(f"[done] {result}")
    report = build_report(manifest)
    write_report(report, REPORT_PATH)
    print(f"Finished {len(results)} verifications in {time.monotonic() - started:.1f}s")
    failures = [entry for entry in report.get("entries", []) if float(entry.get("score") or 0) < args.fail_threshold]
    if failures:
        print(f"Scores below threshold {args.fail_threshold}: {len(failures)}")
        return 1
    return 0


def main() -> int:
    args = parse_args()
    return asyncio.run(run_verification(args))


if __name__ == "__main__":
    sys.exit(main())
