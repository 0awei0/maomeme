from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

from ..models.video_structure import (
    AudioStructure,
    PackagingStructure,
    ScriptSection,
    Shot,
    TransferableFeatures,
    VideoMeta,
    VideoStructure,
)
from .doubao_client import analyze_video_with_doubao, ark_available


def get_video_meta(video_path: str) -> VideoMeta:
    result = subprocess.run(
        [
            "ffprobe",
            "-hide_banner",
            "-loglevel",
            "error",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            video_path,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    info = json.loads(result.stdout)
    video_stream = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None) or {}
    fmt = info.get("format", {})
    duration = float(fmt.get("duration", 0) or 0)
    width = int(video_stream.get("width", 0) or 0)
    height = int(video_stream.get("height", 0) or 0)
    fps_raw = str(video_stream.get("r_frame_rate") or "30/1")
    try:
        num, den = map(int, fps_raw.split("/"))
        fps = round(num / den, 2) if den else 30.0
    except Exception:
        fps = 30.0
    return VideoMeta(duration=duration, resolution=f"{width}x{height}", fps=fps)


async def analyze_video_structure(video_path: str, use_doubao: bool = True) -> VideoStructure:
    task_id = str(uuid.uuid4())[:8]
    meta = get_video_meta(video_path)
    if use_doubao and ark_available():
        raw = await analyze_video_with_doubao(video_path, meta)
        structure = build_video_structure(task_id, meta, raw)
        structure.analysis_evidence["provider"] = "doubao"
        structure.analysis_evidence["sample_fps"] = raw.get("_analysis_sample_fps")
        return structure
    structure = fallback_structure(task_id, meta, video_path)
    structure.analysis_evidence["provider"] = "local_fallback"
    structure.analysis_evidence["reason"] = "ARK_API_KEY missing or use_doubao=false"
    return structure


def build_video_structure(task_id: str, meta: VideoMeta, raw: dict) -> VideoStructure:
    sections = [
        ScriptSection(
            type=_s(sec.get("type", "")),
            start_time=_f(sec.get("start_time")),
            end_time=_f(sec.get("end_time")),
            text=_s(sec.get("text", "")),
            purpose=_s(sec.get("purpose", "")),
            hook_type=_s(sec.get("hook_type", "")) or None,
        )
        for sec in raw.get("sections", [])
        if isinstance(sec, dict)
    ]
    shots = [
        Shot(
            start_time=_f(shot.get("start_time")),
            end_time=_f(shot.get("end_time")),
            type=_s(shot.get("type", "medium")),
            content=_s(shot.get("content", "")),
            camera_move=_s(shot.get("camera_move", "静止")),
            has_subtitle=bool(shot.get("has_subtitle", False)),
            visual_effect=_s(shot.get("visual_effect", "无")),
            subject_distance=_s(shot.get("subject_distance", "")),
            subject_position=_s(shot.get("subject_position", "")),
            subject_motion=_s(shot.get("subject_motion", "")),
        )
        for shot in raw.get("shots", [])
        if isinstance(shot, dict)
    ]
    tf_raw = raw.get("transferable_features") if isinstance(raw.get("transferable_features"), dict) else {}
    tf = TransferableFeatures(
        hook_strategy=_s(tf_raw.get("hook_strategy", "")),
        narrative_pattern=_s(tf_raw.get("narrative_pattern", "")),
        pacing_pattern=_s(tf_raw.get("pacing_pattern", "")),
        spatial_pattern=_s(tf_raw.get("spatial_pattern", "")),
        subject_trajectory=_s(tf_raw.get("subject_trajectory", "")),
        composition_pattern=_s(tf_raw.get("composition_pattern", "")),
        engagement_techniques=_list(tf_raw.get("engagement_techniques", [])),
        suitable_categories=_list(tf_raw.get("suitable_categories", [])),
    )
    return VideoStructure(
        id=task_id,
        meta=meta,
        script_structure=sections,
        shots=shots,
        audio_structure=AudioStructure(),
        packaging_structure=PackagingStructure(),
        transferable_features=tf,
        raw_response=raw.get("raw_response"),
    )


def fallback_structure(task_id: str, meta: VideoMeta, video_path: str) -> VideoStructure:
    duration = max(meta.duration, 1.0)
    cuts = [0, min(2.2, duration), min(5.2, duration), min(8.5, duration), duration]
    roles = [
        ("hook", "强表情/强字幕开场", "先让观众停住", "冲突式"),
        ("setup", "建立猫 meme 场景", "说明问题和语境", None),
        ("escalation", "情绪升级或重复动作", "制造节奏和笑点", None),
        ("punchline", "反转收束或 CTA", "留下记忆点", None),
    ]
    sections = []
    shots = []
    for index, (role, text, purpose, hook_type) in enumerate(roles):
        start = cuts[index]
        end = cuts[index + 1]
        if end <= start:
            continue
        sections.append(ScriptSection(type=role, start_time=start, end_time=end, text=text, purpose=purpose, hook_type=hook_type))
        shots.append(
            Shot(
                start_time=start,
                end_time=end,
                type="close-up" if role == "hook" else "medium",
                content=f"{Path(video_path).name} 的 {role} 段，等待豆包补充精细画面理解。",
                camera_move="未知",
                has_subtitle=True,
                visual_effect="待分析",
                subject_distance="near" if role == "hook" else "mid",
            )
        )
    return VideoStructure(
        id=task_id,
        meta=meta,
        script_structure=sections,
        shots=shots,
        transferable_features=TransferableFeatures(
            hook_strategy="2秒内用强表情或强字幕抓住注意力",
            narrative_pattern="hook→冲突铺垫→情绪升级→反转收束",
            pacing_pattern="短 hook，2-3 秒一切，中后段加速",
            engagement_techniques=["反差", "拟人", "重复", "情绪夸张"],
            suitable_categories=["猫meme", "搞笑", "打工人", "校园生活"],
        ),
    )


def source_summary(structure: VideoStructure) -> str:
    lines = [f"视频时长: {structure.meta.duration:.1f}s, 分辨率: {structure.meta.resolution}", ""]
    lines.append("### 脚本结构")
    for sec in structure.script_structure:
        lines.append(f"- [{sec.type}] {sec.start_time:.1f}-{sec.end_time:.1f}s: {sec.text}；作用：{sec.purpose}")
    lines.append("")
    lines.append("### 镜头结构")
    for shot in structure.shots:
        spatial = " ".join(x for x in [shot.subject_distance, shot.subject_position, shot.subject_motion] if x)
        lines.append(f"- [{shot.type}] {shot.start_time:.1f}-{shot.end_time:.1f}s: {shot.content} {spatial}".strip())
    lines.append("")
    tf = structure.transferable_features
    lines.append("### 可迁移特征")
    lines.append(f"- hook: {tf.hook_strategy}")
    lines.append(f"- 叙事: {tf.narrative_pattern}")
    lines.append(f"- 节奏: {tf.pacing_pattern}")
    if tf.engagement_techniques:
        lines.append(f"- 技巧: {', '.join(tf.engagement_techniques)}")
    return "\n".join(lines)


def _s(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value) if value else ""


def _f(value, default: float = 0.0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.rstrip("s").strip())
        except ValueError:
            return default
    return default


def _list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str) and value:
        return [value]
    return []
