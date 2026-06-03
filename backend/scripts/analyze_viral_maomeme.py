from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import get_settings  # noqa: E402
from app.services.doubao_client import get_async_ark_client, load_prompt, parse_doubao_response  # noqa: E402

LIBRARY_DIR = ROOT / "samples" / "viral-structure" / "baokuan-maomeme"
MANIFEST_PATH = LIBRARY_DIR / "manifest.json"
ANALYSIS_ROOT = ROOT / "data" / "viral-structures" / "baokuan-maomeme"
DEFAULT_TIMEOUT_SECONDS = 180
REQUIRED_ROOT_KEYS = {"asset_plan", "video_summary"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze viral MaoMeme videos with concurrent Doubao video understanding.")
    parser.add_argument("--manifest", default=str(MANIFEST_PATH), help="Path to viral video manifest.")
    parser.add_argument("--limit", type=int, default=0, help="Only analyze the first N selected videos.")
    parser.add_argument("--ids", default="", help="Comma-separated video ids to analyze.")
    parser.add_argument("--resume", action="store_true", help="Skip videos that already have completed structure.json.")
    parser.add_argument("--use-doubao", default="true", choices=["true", "false"], help="Use Doubao video understanding.")
    parser.add_argument("--concurrency", type=int, default=0, help="Concurrent Doubao requests. Defaults to VIRAL_DOUBAO_CONCURRENCY or 8.")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS, help="Per-video Doubao timeout in seconds.")
    parser.add_argument("--repair-raw", action="store_true", help="Rebuild structures from local raw_doubao_response.json without calling Doubao.")
    parser.add_argument("--normalize-existing", action="store_true", help="Normalize existing structure files without calling Doubao.")
    return parser.parse_args()


def read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def use_doubao_enabled(raw: str) -> bool:
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def clamp_concurrency(value: int) -> int:
    return max(1, min(16, value))


def configured_concurrency(cli_value: int) -> int:
    if cli_value:
        return clamp_concurrency(cli_value)
    try:
        return clamp_concurrency(int(os.environ.get("VIRAL_DOUBAO_CONCURRENCY", "8")))
    except ValueError:
        return 8


def choose_fps(duration: float) -> float:
    override = os.environ.get("VIRAL_ANALYSIS_FPS")
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


def local_video_path(item: dict[str, Any]) -> Path:
    path = ROOT / str(item["local_path"])
    if not path.exists():
        source = Path(str(item.get("source_path", "")))
        if source.exists():
            return source
    return path


def selected_items(manifest: dict[str, Any], ids: str, limit: int) -> list[dict[str, Any]]:
    items = list(manifest.get("items", []))
    if ids.strip():
        wanted = {item.strip() for item in ids.split(",") if item.strip()}
        items = [item for item in items if item.get("id") in wanted]
    if limit > 0:
        items = items[:limit]
    return items


def completed(out_dir: Path) -> bool:
    structure = read_json(out_dir / "structure.json", {})
    return bool(structure.get("analysis_status") == "done" and structure.get("asset_plan", {}).get("storyboard"))


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def extract_audio(video_path: Path, out_dir: Path) -> str:
    audio_path = out_dir / "audio.m4a"
    if audio_path.exists():
        return str(audio_path.relative_to(ROOT))
    out_dir.mkdir(parents=True, exist_ok=True)
    result = run(["ffmpeg", "-y", "-i", str(video_path), "-vn", "-acodec", "copy", str(audio_path)])
    if result.returncode != 0:
        result = run(["ffmpeg", "-y", "-i", str(video_path), "-vn", "-ac", "2", "-ar", "44100", str(audio_path)])
    return str(audio_path.relative_to(ROOT)) if audio_path.exists() else ""


def frame_times(duration: float) -> list[float]:
    if duration <= 0:
        return [0.0]
    count = 8 if duration <= 30 else 12 if duration <= 70 else 16
    step = duration / count
    return [min(duration - 0.05, max(0.0, step * index + step / 2)) for index in range(count)]


def extract_contact_sheet(video_path: Path, out_dir: Path, duration: float) -> str:
    sheet_path = out_dir / "contact_sheet.jpg"
    if sheet_path.exists():
        return str(sheet_path.relative_to(ROOT))
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    frames: list[dict[str, Any]] = []
    for index, ts in enumerate(frame_times(duration), start=1):
        frame_path = frames_dir / f"{index:02d}_{ts:.2f}.jpg"
        if not frame_path.exists():
            run(
                [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    f"{ts:.3f}",
                    "-i",
                    str(video_path),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=320:-1",
                    "-q:v",
                    "3",
                    str(frame_path),
                ]
            )
        if frame_path.exists():
            frames.append({"time": round(ts, 3), "file": str(frame_path.relative_to(ROOT))})
    write_json(out_dir / "frames_manifest.json", frames)
    make_contact_sheet([ROOT / frame["file"] for frame in frames], sheet_path)
    return str(sheet_path.relative_to(ROOT)) if sheet_path.exists() else ""


def make_contact_sheet(frames: list[Path], output: Path) -> None:
    images: list[Image.Image] = []
    for frame in frames:
        try:
            image = Image.open(frame).convert("RGB")
        except Exception:
            continue
        draw = ImageDraw.Draw(image)
        label = frame.stem.split("_", 1)[-1].replace("_", ".") + "s"
        draw.rectangle([0, 0, 72, 24], fill=(0, 0, 0))
        draw.text((6, 5), label, fill=(255, 255, 255))
        images.append(image)
    if not images:
        return
    width, height = images[0].size
    cols = 4
    rows = (len(images) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * width, rows * height), (24, 24, 24))
    for index, image in enumerate(images):
        sheet.paste(image, ((index % cols) * width, (index // cols) * height))
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, quality=88)


def encode_video(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def analyze_with_doubao(item: dict[str, Any], video_path: Path, fps: float, timeout: int) -> dict[str, Any]:
    settings = get_settings()
    prompt = load_prompt("viral_maomeme_analysis")
    video_data = encode_video(video_path)
    user_text = f"""请按多轨方式拆解这个爆款猫 meme 视频。

视频元信息：
- id: {item.get("id")}
- 文件名: {item.get("filename")}
- 时长: {float(item.get("duration", 0)):.2f}s
- 分辨率: {item.get("width")}x{item.get("height")}
- fps: {item.get("fps")}
- 视频理解抽帧率: {fps}

请重点抽取每个分镜的具体剧本、背景、猫素材需求、BGM/配音/音效、字幕包装和可复用梗点。"""
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
                            {"type": "video_url", "video_url": {"url": f"data:video/mp4;base64,{video_data}", "fps": fps}},
                            {"type": "text", "text": user_text},
                        ],
                    },
                ],
                temperature=0.2,
                max_tokens=12000,
            ),
            timeout=timeout,
        )
    finally:
        await client.close()
    content = response.choices[0].message.content
    parsed = parse_viral_response(content)
    parsed["_raw_content_sha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
    parsed["_raw_content"] = content
    return parsed


def parse_viral_response(content: str) -> dict[str, Any]:
    parsed = parse_doubao_response(content)
    if has_required_shape(parsed):
        return parsed
    json_text = extract_json_object(content)
    if json_text:
        value = load_json_with_repairs(json_text)
        if isinstance(value, dict):
            return value
    return {"raw_response": content}


def load_json_with_repairs(json_text: str) -> Any:
    variants = [json_text, repair_common_json_issues(json_text)]
    for variant in variants:
        try:
            return json.loads(variant)
        except json.JSONDecodeError:
            continue
    return None


def repair_common_json_issues(json_text: str) -> str:
    text = json_text
    text = re.sub(r'"([^"\n]*need_generated_background)"\s*:\s*(false|true)', r'"\1=\2"', text)
    text = re.sub(r'"need_generated_background":\s*false,\s*"', '"need_generated_background=false, ', text)
    text = re.sub(r'"need_generated_background":\s*true,\s*"', '"need_generated_background=true, ', text)
    text = text.replace('"actions":["买花","说话","震惊","emotions"', '"actions":["买花","说话","震惊"],"emotions"')
    return text


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


def has_required_shape(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if not REQUIRED_ROOT_KEYS.issubset(value.keys()):
        return False
    asset_plan = value.get("asset_plan")
    return isinstance(asset_plan, dict) and isinstance(asset_plan.get("storyboard"), list) and bool(asset_plan.get("storyboard"))


def fallback_structure(item: dict[str, Any], video_path: Path, fps: float, reason: str) -> dict[str, Any]:
    duration = float(item.get("duration", 0) or 0)
    cuts = timeline_cuts(duration)
    storyboard = []
    script_track = []
    shot_track = []
    cat_track = []
    background_track = []
    beats = ["hook", "setup", "escalation", "twist", "punchline", "ending"]
    for index, (start, end) in enumerate(cuts, start=1):
        beat = beats[min(index - 1, len(beats) - 1)]
        shot_id = f"s{index:02d}"
        script = f"待 Doubao 精拆：{video_path.name} 第 {index} 镜头"
        cat = {
            "shot_id": shot_id,
            "cat_count": "",
            "cats": [
                {
                    "role": "待识别猫角色",
                    "position": "待识别",
                    "action": "待识别",
                    "expression": "待识别",
                    "emotion": "待识别",
                    "layout": "待识别",
                    "asset_keywords": ["待识别"],
                }
            ],
        }
        bg = {
            "shot_id": shot_id,
            "setting": "待识别背景",
            "props": [],
            "composition": "",
            "mood": "",
            "existing_asset_keywords": [],
            "seedream_prompt": "",
            "need_generated_background": False,
        }
        storyboard.append(
            {
                "shot_id": shot_id,
                "duration": round(end - start, 2),
                "beat": beat,
                "script": script,
                "joke_point": "待 Doubao 精拆",
                "background": bg,
                "cats": cat["cats"],
                "audio": {"voice": "", "bgm": "", "sfx": []},
                "subtitle": script,
                "seedream_prompt": "",
                "local_cat_asset_keywords": [],
                "local_background_keywords": [],
            }
        )
        script_track.append({"start_time": start, "end_time": end, "text": script, "speaker": "", "tone": "", "emotion": "", "function": beat, "confidence": "low"})
        shot_track.append({"shot_id": shot_id, "start_time": start, "end_time": end, "duration": round(end - start, 2), "beat": beat, "script": script, "joke_point": "", "visual_description": "待 Doubao 精拆", "pacing_note": ""})
        cat_track.append(cat)
        background_track.append(bg)
    return {
        "video_summary": {
            "title": video_path.stem,
            "one_sentence": "本地 fallback 仅生成结构骨架，等待 Doubao 精拆。",
            "primary_topic": "",
            "meme_type": "cat meme",
            "overall_tone": "",
        },
        "script_track": script_track,
        "shot_track": shot_track,
        "cat_track": cat_track,
        "background_track": background_track,
        "audio_track": {"bgm_style": "", "bgm_mood": "", "voice_style": "", "voice_presence": "", "sfx": [], "rhythm_sync": ""},
        "subtitle_packaging": {"subtitle_style": "", "subtitle_density": "", "emphasis_words": [], "bubble_or_dialogue_style": "", "stickers_or_overlays": [], "transition_notes": []},
        "reusable_patterns": {"script_templates": [], "shot_templates": [], "cat_action_templates": [], "background_templates": [], "audio_templates": [], "suitable_topics": []},
        "asset_plan": {"storyboard": storyboard},
        "quality_notes": [reason],
        "analysis_evidence": {"provider": "local_fallback", "fps": fps, "reason": reason},
    }


def timeline_cuts(duration: float) -> list[tuple[float, float]]:
    duration = max(duration, 1.0)
    if duration <= 14:
        count = 3
    elif duration <= 35:
        count = 5
    elif duration <= 70:
        count = 7
    else:
        count = 9
    step = duration / count
    return [(round(step * index, 2), round(min(duration, step * (index + 1)), 2)) for index in range(count)]


def normalize_structure(
    item: dict[str, Any],
    raw: dict[str, Any],
    provider: str,
    fps: float,
    video_path: Path,
    contact_sheet: str,
    audio_path: str,
) -> dict[str, Any]:
    structure = raw if isinstance(raw, dict) else {}
    asset_plan = structure.get("asset_plan") if isinstance(structure.get("asset_plan"), dict) else {"storyboard": []}
    parse_fallback = False
    if not isinstance(asset_plan.get("storyboard"), list) or not asset_plan.get("storyboard"):
        fallback = fallback_structure(item, video_path, fps, "Doubao result missing asset_plan.storyboard")
        asset_plan = fallback["asset_plan"]
        structure.setdefault("quality_notes", []).append("asset_plan 为空，已补本地骨架")
        parse_fallback = provider == "doubao"
    structure["video_id"] = item.get("id")
    structure["filename"] = item.get("filename")
    structure["analysis_status"] = "parse_fallback" if parse_fallback else "done" if provider == "doubao" else "fallback"
    structure["meta"] = {
        "duration": item.get("duration"),
        "size_bytes": item.get("size_bytes"),
        "width": item.get("width"),
        "height": item.get("height"),
        "fps": item.get("fps"),
        "video_codec": item.get("video_codec"),
        "audio_codec": item.get("audio_codec"),
        "sha256": item.get("sha256") or sha256_file(video_path),
    }
    structure["asset_plan"] = asset_plan
    normalize_storyboard_fields(structure)
    structure["analysis_evidence"] = {
        **(structure.get("analysis_evidence") if isinstance(structure.get("analysis_evidence"), dict) else {}),
        "provider": provider,
        "video_input": "base64_data_url" if provider == "doubao" else "local_fallback",
        "fps": fps,
        "contact_sheet": contact_sheet,
        "audio_reference": audio_path,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }
    return structure


def normalize_existing_structure(item: dict[str, Any]) -> dict[str, Any]:
    out_dir = ANALYSIS_ROOT / str(item["id"])
    structure = read_json(out_dir / "structure.json", {})
    if not structure:
        return {"id": item.get("id"), "status": "missing"}
    normalize_storyboard_fields(structure)
    structure["analysis_status"] = structure.get("analysis_status") or "done"
    evidence = structure.get("analysis_evidence") if isinstance(structure.get("analysis_evidence"), dict) else {}
    evidence["normalized_at"] = datetime.now(timezone.utc).isoformat()
    structure["analysis_evidence"] = evidence
    write_json(out_dir / "structure.json", structure)
    write_json(out_dir / "asset_plan.json", structure.get("asset_plan", {"storyboard": []}))
    write_storyboard_markdown(out_dir, structure)
    return {
        "id": item.get("id"),
        "status": structure.get("analysis_status"),
        "provider": evidence.get("provider", structure.get("analysis_provider", "")),
        "shots": len(structure.get("asset_plan", {}).get("storyboard", [])) if isinstance(structure.get("asset_plan"), dict) else 0,
    }


def normalize_storyboard_fields(structure: dict[str, Any]) -> None:
    asset_plan = structure.get("asset_plan") if isinstance(structure.get("asset_plan"), dict) else {}
    storyboard = asset_plan.get("storyboard") if isinstance(asset_plan.get("storyboard"), list) else []
    if not storyboard:
        return

    notes = structure.get("quality_notes")
    if not isinstance(notes, list):
        notes = []
    changed = 0
    for index, shot in enumerate(storyboard, start=1):
        if not isinstance(shot, dict):
            continue
        shot.setdefault("shot_id", f"s{index:02d}")

        normalized_duration = parse_duration_seconds(shot.get("duration"))
        if normalized_duration is not None:
            shot["duration"] = normalized_duration

        script = clean_text(shot.get("script"))
        subtitle = clean_text(shot.get("subtitle"))
        joke_point = clean_text(shot.get("joke_point"))
        background = clean_text(shot.get("background"))
        cats = shot.get("cats")
        beat = clean_text(shot.get("beat")) or "shot"

        if not script and subtitle and subtitle != "无台词":
            shot["script"] = subtitle
            changed += 1
        elif not script:
            shot["script"] = infer_shot_text(beat, joke_point, background, cats, fallback="无台词剧情镜头")
            changed += 1

        if not subtitle:
            shot["subtitle"] = clean_text(shot.get("script")) or infer_shot_text(beat, joke_point, background, cats, fallback="无台词")
            changed += 1

        if not joke_point:
            shot["joke_point"] = infer_joke_point(beat, background, cats)
            changed += 1

        if missing_cats(cats):
            shot["cats"] = infer_no_cat_description(beat, background)
            changed += 1

        if missing_background(shot.get("background")):
            shot["background"] = "未识别具体背景，需参考 contact sheet 人工复核"
            changed += 1

        if missing_audio(shot.get("audio")):
            shot["audio"] = "原视频声音参考，需人工复核 BGM/配音/音效"
            changed += 1

    if changed:
        note = f"normalized_storyboard_fields: filled {changed} blank or inconsistent fields"
        if note not in notes:
            notes.append(note)
        structure["quality_notes"] = notes


def parse_duration_seconds(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return round(max(0.0, float(value)), 2)
    text = clean_text(value)
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    return round(max(0.0, float(match.group(1))), 2)


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value).strip()
    if isinstance(value, dict):
        for key in ("setting", "description", "voice", "bgm", "text", "summary"):
            if clean_text(value.get(key)):
                return clean_text(value.get(key))
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "；".join(clean_text(item) for item in value if clean_text(item))
    return str(value).strip()


def missing_cats(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return len(value) == 0
    return False


def missing_background(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, dict):
        return not any(clean_text(value.get(key)) for key in ("setting", "description", "composition", "mood"))
    return False


def missing_audio(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, dict):
        if any(clean_text(value.get(key)) for key in ("voice", "bgm", "style", "rhythm_sync")):
            return False
        sfx = value.get("sfx")
        return not (isinstance(sfx, list) and len(sfx) > 0)
    return False


def infer_shot_text(beat: str, joke_point: str, background: str, cats: Any, fallback: str) -> str:
    if joke_point:
        return f"{beat_label(beat)}：{joke_point}"
    if background:
        return f"{beat_label(beat)}：{background}"
    cats_text = clean_text(cats)
    if cats_text and cats_text != "无":
        return f"{beat_label(beat)}：{cats_text}"
    return fallback


def infer_joke_point(beat: str, background: str, cats: Any) -> str:
    beat_text = beat_label(beat)
    bg = background
    cats_text = clean_text(cats)
    if "作者" in bg or "抖音" in bg or "引流" in bg:
        return "结尾引流/作者页收束"
    if "转场" in bg:
        return "节奏转场承接反转"
    if cats_text in {"无", ""}:
        return f"{beat_text}：真实素材或无猫镜头增强反差"
    return f"{beat_text}：猫动作和台词形成反差"


def infer_no_cat_description(beat: str, background: str) -> str:
    if "转场" in background:
        return "无猫镜头/文字转场页"
    if "作者" in background or "抖音" in background or "引流" in background:
        return "无猫镜头/平台作者信息页"
    if beat in {"ending", "punchline"}:
        return "无猫镜头/真实素材收尾"
    return "无猫镜头/环境或道具镜头"


def beat_label(beat: str) -> str:
    labels = {
        "hook": "开场钩子",
        "setup": "铺垫",
        "escalation": "升级",
        "transition": "转场",
        "twist": "反转",
        "punchline": "笑点",
        "ending": "收尾",
    }
    return labels.get(beat, beat or "镜头")


def write_storyboard_markdown(out_dir: Path, structure: dict[str, Any]) -> None:
    summary = structure.get("video_summary") if isinstance(structure.get("video_summary"), dict) else {}
    lines = [
        f"# {summary.get('title') or structure.get('filename') or structure.get('video_id')}",
        "",
        f"- 一句话: {summary.get('one_sentence', '')}",
        f"- 主题: {summary.get('primary_topic', '')}",
        f"- 类型: {summary.get('meme_type', '')}",
        f"- 调性: {summary.get('overall_tone', '')}",
        "",
        "## 分镜素材计划",
    ]
    storyboard = structure.get("asset_plan", {}).get("storyboard", []) if isinstance(structure.get("asset_plan"), dict) else []
    for shot in storyboard:
        if not isinstance(shot, dict):
            continue
        lines.extend(
            [
                "",
                f"### {shot.get('shot_id', '')} {shot.get('beat', '')} ({shot.get('duration', '')}s)",
                f"- 剧本: {shot.get('script', '')}",
                f"- 梗点: {shot.get('joke_point', '')}",
                f"- 背景: {stringify_short(shot.get('background'))}",
                f"- 猫: {stringify_short(shot.get('cats'))}",
                f"- 声音: {stringify_short(shot.get('audio'))}",
                f"- 字幕: {shot.get('subtitle', '')}",
                f"- Seedream: {shot.get('seedream_prompt', '')}",
            ]
        )
    (out_dir / "storyboard.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def stringify_short(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


async def analyze_one(
    item: dict[str, Any],
    semaphore: asyncio.Semaphore,
    use_doubao: bool,
    timeout: int,
    repair_raw: bool = False,
) -> dict[str, Any]:
    video_path = local_video_path(item)
    out_dir = ANALYSIS_ROOT / str(item["id"])
    out_dir.mkdir(parents=True, exist_ok=True)
    if not video_path.exists():
        error = f"video missing: {video_path}"
        write_json(out_dir / "structure.json", {"video_id": item.get("id"), "analysis_status": "error", "error": error})
        return {"id": item.get("id"), "status": "error", "error": error}

    fps = choose_fps(float(item.get("duration", 0) or 0))
    contact_sheet = extract_contact_sheet(video_path, out_dir, float(item.get("duration", 0) or 0))
    audio_path = extract_audio(video_path, out_dir)
    provider = "local_fallback"
    raw: dict[str, Any]

    raw_response_path = out_dir / "raw_doubao_response.json"
    if repair_raw and raw_response_path.exists():
        content = read_json(raw_response_path, {}).get("content", "")
        raw = parse_viral_response(str(content))
        provider = "doubao"
    elif use_doubao and get_settings().ARK_API_KEY:
        last_error = ""
        for attempt in range(1, 4):
            try:
                async with semaphore:
                    print(f"[{item['id']}] Doubao analyze attempt {attempt}")
                    raw = await analyze_with_doubao(item, video_path, fps, timeout)
                provider = "doubao"
                break
            except Exception as exc:
                last_error = safe_error(exc)
                print(f"[{item['id']}] Doubao failed attempt {attempt}: {last_error}")
                if attempt < 3:
                    await asyncio.sleep(2 ** attempt)
        else:
            raw = fallback_structure(item, video_path, fps, f"Doubao failed after retries: {last_error}")
    else:
        reason = "Doubao disabled" if not use_doubao else "ARK_API_KEY not configured"
        raw = fallback_structure(item, video_path, fps, reason)

    raw_content = detach_raw_content(raw)
    if raw_content:
        write_json(
            out_dir / "raw_doubao_response.json",
            {
                "sha256": hashlib.sha256(raw_content.encode("utf-8")).hexdigest(),
                "content": raw_content,
            },
        )

    structure = normalize_structure(item, raw, provider, fps, video_path, contact_sheet, audio_path)
    write_json(out_dir / "structure.json", structure)
    write_json(out_dir / "asset_plan.json", structure.get("asset_plan", {"storyboard": []}))
    write_storyboard_markdown(out_dir, structure)
    return {"id": item.get("id"), "status": structure.get("analysis_status"), "provider": provider, "shots": len(structure.get("asset_plan", {}).get("storyboard", []))}


def detach_raw_content(raw: dict[str, Any]) -> str:
    content = str(raw.pop("_raw_content", "") or "")
    if "raw_response" in raw and not has_required_shape(raw):
        content = str(raw.pop("raw_response", "") or content)
    return content


def safe_error(exc: Exception) -> str:
    text = str(exc)
    settings = get_settings()
    if settings.ARK_API_KEY:
        text = text.replace(settings.ARK_API_KEY, "***")
    return text[:500]


def update_manifest(manifest_path: Path, manifest: dict[str, Any], results: list[dict[str, Any]]) -> None:
    result_by_id = {item["id"]: item for item in results}
    for item in manifest.get("items", []):
        result = result_by_id.get(item.get("id"))
        if not result:
            continue
        item["analysis_status"] = result.get("status", "")
        item["analysis_provider"] = result.get("provider", "")
        item["analysis_updated_at"] = datetime.now(timezone.utc).isoformat()
    manifest["generated_at"] = datetime.now(timezone.utc).isoformat()
    write_json(manifest_path, manifest)


def build_index(manifest: dict[str, Any]) -> None:
    entries = []
    for item in manifest.get("items", []):
        structure_path = ANALYSIS_ROOT / str(item["id"]) / "structure.json"
        structure = read_json(structure_path, {})
        if not structure:
            continue
        summary = structure.get("video_summary") if isinstance(structure.get("video_summary"), dict) else {}
        reusable = structure.get("reusable_patterns") if isinstance(structure.get("reusable_patterns"), dict) else {}
        storyboard = structure.get("asset_plan", {}).get("storyboard", []) if isinstance(structure.get("asset_plan"), dict) else []
        entries.append(
            {
                "id": item.get("id"),
                "filename": item.get("filename"),
                "duration": item.get("duration"),
                "status": structure.get("analysis_status"),
                "title": summary.get("title", ""),
                "primary_topic": summary.get("primary_topic", ""),
                "meme_type": summary.get("meme_type", ""),
                "overall_tone": summary.get("overall_tone", ""),
                "shot_count": len(storyboard),
                "script_templates": reusable.get("script_templates", []),
                "shot_templates": reusable.get("shot_templates", []),
                "cat_action_templates": reusable.get("cat_action_templates", []),
                "background_templates": reusable.get("background_templates", []),
                "audio_templates": reusable.get("audio_templates", []),
                "suitable_topics": reusable.get("suitable_topics", []),
            }
        )
    write_json(
        ANALYSIS_ROOT / "index.json",
        {
            "library": "baokuan-maomeme",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total": len(entries),
            "entries": entries,
        },
    )


async def run_analysis(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest)
    manifest = read_json(manifest_path, {})
    if not manifest.get("items"):
        raise FileNotFoundError(f"manifest has no items: {manifest_path}")

    items = selected_items(manifest, args.ids, args.limit)
    if args.normalize_existing:
        results = [normalize_existing_structure(item) for item in items]
        update_manifest(manifest_path, manifest, results)
        build_index(manifest)
        print(f"Normalized {len(results)} existing analyses")
        for result in results:
            print(f"[normalized] {result}")
        return 0

    if args.resume:
        items = [item for item in items if not completed(ANALYSIS_ROOT / str(item["id"]))]

    concurrency = configured_concurrency(args.concurrency)
    use_doubao = use_doubao_enabled(args.use_doubao)
    print(f"Analyzing {len(items)} videos with concurrency={concurrency}, use_doubao={use_doubao}")
    semaphore = asyncio.Semaphore(concurrency)
    tasks = [asyncio.create_task(analyze_one(item, semaphore, use_doubao, args.timeout, repair_raw=args.repair_raw)) for item in items]
    results: list[dict[str, Any]] = []
    started = time.monotonic()
    for task in asyncio.as_completed(tasks):
        result = await task
        results.append(result)
        print(f"[done] {result}")
    update_manifest(manifest_path, manifest, results)
    build_index(manifest)
    print(f"Finished {len(results)} videos in {time.monotonic() - started:.1f}s")
    return 0


def main() -> int:
    args = parse_args()
    return asyncio.run(run_analysis(args))


if __name__ == "__main__":
    sys.exit(main())
