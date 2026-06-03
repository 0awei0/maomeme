from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
LIBRARY_DIR = ROOT / "samples" / "viral-structure" / "baokuan-maomeme"
MANIFEST_PATH = LIBRARY_DIR / "manifest.json"
ANALYSIS_ROOT = ROOT / "data" / "viral-structures" / "baokuan-maomeme"
REPORT_PATH = ANALYSIS_ROOT / "audit-report.md"
PLACEHOLDER_TERMS = ("待 Doubao", "待识别", "fallback", "精拆")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit viral MaoMeme structure extraction results.")
    parser.add_argument("--manifest", default=str(MANIFEST_PATH), help="Path to viral video manifest.")
    parser.add_argument("--output", default=str(REPORT_PATH), help="Markdown report output path.")
    parser.add_argument("--fail-on-issues", action="store_true", help="Return non-zero when severe issues are found.")
    return parser.parse_args()


def read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value).strip()
    return json.dumps(value, ensure_ascii=False)


def duration_value(value: Any) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    text = clean_text(value)
    if not text:
        return 0.0
    import re

    match = re.search(r"(\d+(?:\.\d+)?)", text)
    return max(0.0, float(match.group(1))) if match else 0.0


def has_audio_stream(path: Path) -> bool:
    if not path.exists():
        return False
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return False
    payload = json.loads(result.stdout or "{}")
    return bool(payload.get("streams"))


def item_paths(video_id: str) -> dict[str, Path]:
    out_dir = ANALYSIS_ROOT / video_id
    return {
        "dir": out_dir,
        "structure": out_dir / "structure.json",
        "asset_plan": out_dir / "asset_plan.json",
        "storyboard": out_dir / "storyboard.md",
        "contact_sheet": out_dir / "contact_sheet.jpg",
        "audio": out_dir / "audio.m4a",
    }


def audit_item(item: dict[str, Any]) -> dict[str, Any]:
    video_id = str(item.get("id", ""))
    paths = item_paths(video_id)
    structure = read_json(paths["structure"], {})
    storyboard = structure.get("asset_plan", {}).get("storyboard", []) if isinstance(structure.get("asset_plan"), dict) else []
    shot_track = structure.get("shot_track") if isinstance(structure.get("shot_track"), list) else []
    severe: list[str] = []
    warnings: list[str] = []
    placeholders: list[str] = []

    if not structure:
        severe.append("missing structure.json")
    if structure.get("analysis_status") != "done":
        severe.append(f"analysis_status={structure.get('analysis_status')}")
    if not isinstance(storyboard, list) or not storyboard:
        severe.append("missing storyboard")

    for key in ("asset_plan", "storyboard", "contact_sheet"):
        if not paths[key].exists():
            severe.append(f"missing {paths[key].name}")
    if not paths["audio"].exists():
        warnings.append("missing audio reference")
    elif not has_audio_stream(paths["audio"]):
        warnings.append("audio reference has no audio stream")

    plan_duration = 0.0
    for index, shot in enumerate(storyboard, start=1):
        if not isinstance(shot, dict):
            severe.append(f"shot {index}: not object")
            continue
        shot_id = clean_text(shot.get("shot_id")) or f"shot {index}"
        for field in ("script", "subtitle", "joke_point", "background", "cats", "audio"):
            value = shot.get(field)
            if field == "cats" and isinstance(value, list) and value:
                continue
            if not clean_text(value):
                severe.append(f"{shot_id}: missing {field}")
        dur = duration_value(shot.get("duration"))
        if dur <= 0:
            warnings.append(f"{shot_id}: non-positive duration")
        plan_duration += dur
        text_blob = json.dumps(shot, ensure_ascii=False)
        if any(term in text_blob for term in PLACEHOLDER_TERMS):
            placeholders.append(shot_id)

    source_duration = float(item.get("duration", 0) or 0)
    coverage_duration = timeline_coverage_seconds(shot_track) or plan_duration
    duration_delta = abs(coverage_duration - source_duration)
    if source_duration > 0 and duration_delta > max(6.0, source_duration * 0.3):
        source = "shot_track" if shot_track else "asset_plan"
        warnings.append(f"{source} duration differs from source by {duration_delta:.1f}s")

    return {
        "id": video_id,
        "filename": item.get("filename", ""),
        "title": structure.get("video_summary", {}).get("title", "") if isinstance(structure.get("video_summary"), dict) else "",
        "topic": structure.get("video_summary", {}).get("primary_topic", "") if isinstance(structure.get("video_summary"), dict) else "",
        "status": structure.get("analysis_status", ""),
        "provider": structure.get("analysis_evidence", {}).get("provider", "") if isinstance(structure.get("analysis_evidence"), dict) else "",
        "source_duration": source_duration,
        "coverage_duration": round(coverage_duration, 2),
        "plan_duration": round(plan_duration, 2),
        "shot_count": len(storyboard) if isinstance(storyboard, list) else 0,
        "severe": severe,
        "warnings": warnings,
        "placeholders": placeholders,
        "contact_sheet": str(paths["contact_sheet"].relative_to(ROOT)) if paths["contact_sheet"].exists() else "",
        "storyboard": str(paths["storyboard"].relative_to(ROOT)) if paths["storyboard"].exists() else "",
    }


def timeline_coverage_seconds(shot_track: list[Any]) -> float:
    starts: list[float] = []
    ends: list[float] = []
    duration_sum = 0.0
    for shot in shot_track:
        if not isinstance(shot, dict):
            continue
        start = duration_value(shot.get("start_time"))
        end = duration_value(shot.get("end_time"))
        duration = duration_value(shot.get("duration"))
        if end > start:
            starts.append(start)
            ends.append(end)
        if duration > 0:
            duration_sum += duration
    if starts and ends:
        return max(ends) - min(starts)
    return duration_sum


def write_report(path: Path, manifest: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    status_counts = Counter(row["status"] for row in rows)
    provider_counts = Counter(row["provider"] for row in rows)
    severe_count = sum(len(row["severe"]) for row in rows)
    warning_count = sum(len(row["warnings"]) for row in rows)
    placeholder_count = sum(len(row["placeholders"]) for row in rows)
    avg_shots = sum(row["shot_count"] for row in rows) / len(rows) if rows else 0
    total_duration = sum(row["source_duration"] for row in rows)
    lines = [
        "# 爆款猫 Meme Doubao 拆解审计报告",
        "",
        f"- 生成时间: {datetime.now(timezone.utc).isoformat()}",
        f"- 视频总数: {len(rows)} / manifest {manifest.get('total')}",
        f"- 状态统计: {dict(status_counts)}",
        f"- Provider 统计: {dict(provider_counts)}",
        f"- 原视频总时长: {total_duration:.1f}s，平均分镜数: {avg_shots:.1f}",
        f"- 严重问题数: {severe_count}",
        f"- 警告数: {warning_count}",
        f"- 占位符镜头数: {placeholder_count}",
        "",
        "## 逐条结果",
        "",
        "| ID | 标题/主题 | 分镜 | 时间线覆盖 | 复刻计划时长 | 状态 | 问题 |",
        "| --- | --- | ---: | ---: | --- | --- |",
    ]
    for row in rows:
        delta = row["coverage_duration"] - row["source_duration"]
        issue_parts = []
        if row["severe"]:
            issue_parts.append("严重: " + "；".join(row["severe"]))
        if row["warnings"]:
            issue_parts.append("警告: " + "；".join(row["warnings"]))
        if row["placeholders"]:
            issue_parts.append("占位符: " + "、".join(row["placeholders"]))
        issue = "<br>".join(issue_parts) if issue_parts else "OK"
        title = row["title"] or row["filename"]
        topic = row["topic"]
        title_cell = f"{title}<br>{topic}" if topic else title
        lines.append(
            f"| {row['id']} | {escape_md(title_cell)} | {row['shot_count']} | {delta:+.1f}s | {row['plan_duration']:.1f}s | {row['status']}/{row['provider']} | {escape_md(issue)} |"
        )
    lines.extend(
        [
            "",
            "## 抽检建议",
            "",
            "- 优先看 `storyboard.md` 对照 `contact_sheet.jpg`：检查台词是否完整、背景是否具体、猫数量/位置/动作是否可映射到素材库。",
            "- 结尾页、无猫镜头、真实素材收尾已经归一化成明确描述，后续可以作为剪辑/引流模板，但不应当被当成猫动作素材。",
            "- BGM/配音目前是风格级描述，并保留 `audio.m4a` 参考轨；如果要做可复用音乐库，下一步需要 ASR/人声分离或人工标注。",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def escape_md(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def main() -> int:
    args = parse_args()
    manifest_path = Path(args.manifest)
    manifest = read_json(manifest_path, {})
    rows = [audit_item(item) for item in manifest.get("items", [])]
    write_report(Path(args.output), manifest, rows)
    severe_count = sum(len(row["severe"]) for row in rows)
    warning_count = sum(len(row["warnings"]) for row in rows)
    print(
        json.dumps(
            {
                "total": len(rows),
                "status": dict(Counter(row["status"] for row in rows)),
                "provider": dict(Counter(row["provider"] for row in rows)),
                "severe": severe_count,
                "warnings": warning_count,
                "report": str(Path(args.output)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if args.fail_on_issues and severe_count:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
