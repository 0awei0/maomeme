from __future__ import annotations

import asyncio
import json
import mimetypes
import re
import time
import uuid
from pathlib import Path
from typing import Any, BinaryIO

from fastapi import UploadFile

from ..core.config import get_settings
from ..models.maomeme import CreativeBrief, UploadAsset, ViralAnalysisJobStatus
from .doubao_client import analyze_viral_maomeme_with_doubao, ark_available
from .video_analyzer import analyze_video_structure, get_video_meta
from .video_analyzer import fallback_structure as fallback_video_structure

MAX_VIRAL_VIDEO_BYTES = 80 * 1024 * 1024
MAX_MATERIAL_BYTES = 60 * 1024 * 1024
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_TEXT_EXTENSIONS = {".txt", ".md", ".json"}

VIRAL_JOBS: dict[str, ViralAnalysisJobStatus] = {}


def ensure_session_id(session_id: str | None = None) -> str:
    value = safe_slug(session_id or "")
    return value if value else f"session-{uuid.uuid4().hex[:10]}"


def session_dir(session_id: str) -> Path:
    path = get_settings().UPLOAD_DIR / "sessions" / ensure_session_id(session_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def manifest_path(session_id: str) -> Path:
    return session_dir(session_id) / "manifest.json"


def read_session_manifest(session_id: str) -> dict[str, Any]:
    path = manifest_path(session_id)
    if not path.exists():
        return {"session_id": ensure_session_id(session_id), "uploads": [], "analyses": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data.setdefault("session_id", ensure_session_id(session_id))
    data.setdefault("uploads", [])
    data.setdefault("analyses", [])
    return data


def write_session_manifest(session_id: str, manifest: dict[str, Any]) -> None:
    path = manifest_path(session_id)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


async def save_viral_upload(file: UploadFile, session_id: str | None = None, description: str = "") -> UploadAsset:
    current_session = ensure_session_id(session_id)
    asset = await save_upload_file(
        file=file,
        session_id=current_session,
        folder="viral",
        kind="viral_video",
        allowed_extensions=ALLOWED_VIDEO_EXTENSIONS,
        max_bytes=MAX_VIRAL_VIDEO_BYTES,
        description=description,
    )
    return asset


async def save_material_uploads(files: list[UploadFile], session_id: str | None = None, description: str = "") -> list[UploadAsset]:
    current_session = ensure_session_id(session_id)
    uploads: list[UploadAsset] = []
    for file in files:
        ext = Path(file.filename or "").suffix.lower()
        if ext in ALLOWED_VIDEO_EXTENSIONS:
            kind = "user_cat_motion"
            folder = "materials/cat-motions"
        elif ext in ALLOWED_IMAGE_EXTENSIONS:
            kind = "user_background"
            folder = "materials/backgrounds"
        elif ext in ALLOWED_TEXT_EXTENSIONS:
            kind = "user_text"
            folder = "materials/text"
        else:
            raise ValueError(f"不支持的素材格式: {ext or file.filename}")
        uploads.append(
            await save_upload_file(
                file=file,
                session_id=current_session,
                folder=folder,
                kind=kind,
                allowed_extensions=ALLOWED_VIDEO_EXTENSIONS | ALLOWED_IMAGE_EXTENSIONS | ALLOWED_TEXT_EXTENSIONS,
                max_bytes=MAX_MATERIAL_BYTES,
                description=description,
            )
        )
    return uploads


async def save_upload_file(
    *,
    file: UploadFile,
    session_id: str,
    folder: str,
    kind: str,
    allowed_extensions: set[str],
    max_bytes: int,
    description: str,
) -> UploadAsset:
    filename = file.filename or "upload"
    ext = Path(filename).suffix.lower()
    if ext not in allowed_extensions:
        raise ValueError(f"不支持的文件格式: {ext or filename}")
    upload_id = f"up-{uuid.uuid4().hex[:10]}"
    safe_name = f"{upload_id}-{safe_filename(filename)}"
    target_dir = session_dir(session_id) / folder
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / safe_name
    size = await write_upload_stream(file.file, target_path, max_bytes)
    if size <= 0:
        target_path.unlink(missing_ok=True)
        raise ValueError("上传文件为空")
    metadata = await asyncio.to_thread(probe_upload_metadata, target_path, kind)
    asset = UploadAsset(
        upload_id=upload_id,
        session_id=session_id,
        kind=kind,
        filename=filename,
        file_path=relative_to_project(target_path),
        description=description.strip(),
        size_bytes=size,
        content_type=file.content_type or mimetypes.guess_type(filename)[0] or "",
        metadata=metadata,
    )
    append_upload(session_id, asset)
    return asset


async def write_upload_stream(handle: BinaryIO, target_path: Path, max_bytes: int) -> int:
    total = 0
    with target_path.open("wb") as out:
        while True:
            chunk = await asyncio.to_thread(handle.read, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                out.close()
                target_path.unlink(missing_ok=True)
                raise ValueError(f"文件太大，最大允许 {max_bytes // 1024 // 1024}MB")
            out.write(chunk)
    return total


def append_upload(session_id: str, asset: UploadAsset) -> None:
    manifest = read_session_manifest(session_id)
    uploads = [item for item in manifest.get("uploads", []) if item.get("upload_id") != asset.upload_id]
    uploads.append(asset.model_dump())
    manifest["uploads"] = uploads
    manifest["updated_at"] = time.time()
    write_session_manifest(session_id, manifest)


def probe_upload_metadata(path: Path, kind: str) -> dict[str, Any]:
    if kind in {"viral_video", "user_cat_motion"}:
        try:
            meta = get_video_meta(str(path))
            return meta.model_dump()
        except Exception as exc:
            return {"probe_error": safe_error(exc)}
    if kind == "user_background":
        try:
            from PIL import Image

            with Image.open(path) as image:
                return {"width": image.width, "height": image.height, "mode": image.mode}
        except Exception as exc:
            return {"probe_error": safe_error(exc)}
    if kind == "user_text":
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
            return {"chars": len(text), "preview": text[:160]}
        except Exception as exc:
            return {"probe_error": safe_error(exc)}
    return {}


def run_local_video_structure_analysis(video_path: str):
    meta = get_video_meta(video_path)
    task_id = f"upload-{uuid.uuid4().hex[:8]}"
    structure = fallback_video_structure(task_id, meta, video_path)
    structure.analysis_evidence["provider"] = "local_fallback"
    structure.analysis_evidence["reason"] = "ARK_API_KEY missing or use_doubao=false"
    return structure


def create_viral_analysis_job(
    session_id: str,
    upload_id: str,
    use_doubao: bool = True,
    creative_brief: CreativeBrief | None = None,
) -> ViralAnalysisJobStatus:
    upload = find_upload(session_id, upload_id)
    if not upload:
        raise FileNotFoundError("上传视频不存在")
    if upload.get("kind") != "viral_video":
        raise ValueError("只能分析爆款参考视频")
    job_id = f"vaj-{uuid.uuid4().hex[:8]}"
    status = ViralAnalysisJobStatus(
        job_id=job_id,
        session_id=session_id,
        upload_id=upload_id,
        status="queued",
        progress=0.04,
        message="爆款分析任务已创建",
    )
    VIRAL_JOBS[job_id] = status
    asyncio.create_task(run_viral_analysis_job(job_id, creative_brief or CreativeBrief(), use_doubao))
    return status


def get_viral_analysis_job(job_id: str) -> ViralAnalysisJobStatus | None:
    job = VIRAL_JOBS.get(job_id)
    if job:
        return job
    return recover_viral_analysis_job(job_id)


async def run_viral_analysis_job(job_id: str, creative_brief: CreativeBrief, use_doubao: bool) -> None:
    status = VIRAL_JOBS[job_id]
    try:
        upload = find_upload(status.session_id, status.upload_id)
        if not upload:
            raise FileNotFoundError("上传视频不存在")
        video_path = get_settings().PROJECT_ROOT / upload["file_path"]
        if not video_path.exists():
            raise FileNotFoundError("上传视频文件不存在")

        status.status = "running"
        status.progress = 0.12
        status.message = "正在读取视频基础信息"
        await asyncio.sleep(0)

        status.progress = 0.28
        status.message = "正在拆解剧本、分镜和声音节奏"
        meta = await asyncio.to_thread(get_video_meta, str(video_path))
        if use_doubao and ark_available():
            structure = await wait_with_analysis_heartbeat(
                status,
                analyze_viral_maomeme_with_doubao(
                    str(video_path),
                    meta,
                    context={"creative_brief": creative_brief.model_dump(), "upload": upload},
                ),
            )
            structure["analysis_evidence"] = {
                **(structure.get("analysis_evidence") if isinstance(structure.get("analysis_evidence"), dict) else {}),
                "provider": "doubao",
                "video_input": "base64_data_url",
                "sample_fps": structure.get("_analysis_sample_fps"),
            }
        else:
            structure_model = await asyncio.to_thread(run_local_video_structure_analysis, str(video_path))
            structure = structure_model.model_dump()
        structure["upload"] = upload
        structure["creative_brief"] = creative_brief.model_dump()
        structure["migration_summary"] = migration_summary_from_structure(structure)
        structure["storyboard_for_agent"] = storyboard_for_agent(structure)
        structure["transfer_slots"] = transfer_slots_from_structure(structure)

        status.progress = 0.78
        status.message = "正在保存爆款结构分析"
        analysis_id = f"analysis-{uuid.uuid4().hex[:10]}"
        out_dir = session_dir(status.session_id) / "analyses" / analysis_id
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "structure.json").write_text(json.dumps(structure, ensure_ascii=False, indent=2), encoding="utf-8")
        (out_dir / "storyboard.md").write_text(storyboard_markdown(structure), encoding="utf-8")
        save_analysis_manifest(status.session_id, analysis_id, status.upload_id, structure, job_id)

        status.status = "done"
        status.progress = 1.0
        status.message = "爆款结构分析完成"
        status.analysis_id = analysis_id
        status.structure = structure
        status.summary = public_analysis_summary(structure)
    except Exception as exc:
        status.status = "error"
        status.progress = 1.0
        status.message = "爆款结构分析失败"
        status.error = safe_error(exc)


async def wait_with_analysis_heartbeat(status: ViralAnalysisJobStatus, awaitable: Any) -> dict[str, Any]:
    task = asyncio.create_task(awaitable)
    started = time.monotonic()
    messages = [
        "Doubao 正在识别台词和字幕节奏",
        "Doubao 正在拆分具体分镜和猫动作",
        "Doubao 正在提取背景、道具和画面结构",
        "Doubao 正在整理 BGM、配音和音效提示",
        "正在清洗可迁移的爆款结构模板",
    ]
    last_bucket = -1
    try:
        while not task.done():
            elapsed = time.monotonic() - started
            bucket = int(elapsed // 8)
            if bucket != last_bucket:
                status.message = messages[bucket % len(messages)]
                last_bucket = bucket
            ratio = min(0.96, elapsed / 150.0)
            status.progress = max(status.progress, min(0.72, 0.28 + ratio * 0.44))
            await asyncio.sleep(1.2)
        return await task
    except Exception:
        if not task.done():
            task.cancel()
        raise


def recover_viral_analysis_job(job_id: str) -> ViralAnalysisJobStatus | None:
    root = get_settings().UPLOAD_DIR / "sessions"
    if not root.exists():
        return None
    for manifest_file in root.glob("*/manifest.json"):
        manifest = read_json(manifest_file, {})
        session_id = str(manifest.get("session_id") or manifest_file.parent.name)
        for analysis in manifest.get("analyses", []):
            if analysis.get("job_id") != job_id:
                continue
            structure = load_analysis(session_id, analysis.get("analysis_id", ""))
            return ViralAnalysisJobStatus(
                job_id=job_id,
                session_id=session_id,
                upload_id=str(analysis.get("upload_id", "")),
                status="done",
                progress=1.0,
                message="爆款结构分析完成",
                analysis_id=str(analysis.get("analysis_id", "")),
                structure=structure,
                summary=public_analysis_summary(structure),
            )
    return None


def save_analysis_manifest(session_id: str, analysis_id: str, upload_id: str, structure: dict[str, Any], job_id: str) -> None:
    manifest = read_session_manifest(session_id)
    analyses = [item for item in manifest.get("analyses", []) if item.get("analysis_id") != analysis_id]
    analyses.append({
        "analysis_id": analysis_id,
        "upload_id": upload_id,
        "job_id": job_id,
        "title": structure.get("migration_summary", {}).get("title", ""),
        "created_at": time.time(),
    })
    manifest["analyses"] = analyses
    manifest["updated_at"] = time.time()
    write_session_manifest(session_id, manifest)


def load_analysis(session_id: str | None, analysis_id: str | None) -> dict[str, Any] | None:
    if not session_id or not analysis_id:
        return None
    path = session_dir(session_id) / "analyses" / safe_slug(analysis_id) / "structure.json"
    return read_json(path, None)


def find_upload(session_id: str, upload_id: str) -> dict[str, Any] | None:
    manifest = read_session_manifest(session_id)
    for item in manifest.get("uploads", []):
        if item.get("upload_id") == upload_id:
            return item
    return None


def user_asset_index(session_id: str | None, material_ids: list[str] | None = None) -> dict[str, Any]:
    if not session_id:
        return {"cat_motions": [], "backgrounds": [], "texts": []}
    manifest = read_session_manifest(session_id)
    wanted = {str(item) for item in material_ids or [] if str(item)}
    cat_motions: list[dict[str, Any]] = []
    backgrounds: list[dict[str, Any]] = []
    texts: list[dict[str, Any]] = []
    for item in manifest.get("uploads", []):
        if wanted and item.get("upload_id") not in wanted:
            continue
        kind = item.get("kind")
        if kind == "user_cat_motion":
            cat_motions.append(upload_to_motion_asset(item))
        elif kind == "user_background":
            backgrounds.append(upload_to_background_asset(item))
        elif kind == "user_text":
            texts.append(upload_to_text_asset(item))
    return {"cat_motions": cat_motions, "backgrounds": backgrounds, "texts": texts}


def merge_user_assets(base_index: dict[str, Any], session_id: str | None, material_ids: list[str] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    user_index = user_asset_index(session_id, material_ids)
    merged = json.loads(json.dumps(base_index, ensure_ascii=False))
    merged["cat_motions"] = [*user_index["cat_motions"], *merged.get("cat_motions", [])]
    merged["backgrounds"] = [*user_index["backgrounds"], *merged.get("backgrounds", [])]
    summary = {
        "user_motion_count": len(user_index["cat_motions"]),
        "user_background_count": len(user_index["backgrounds"]),
        "user_text_count": len(user_index["texts"]),
        "user_material_ids": [item.get("id") for item in [*user_index["cat_motions"], *user_index["backgrounds"], *user_index["texts"]]],
    }
    return merged, summary


def upload_to_motion_asset(item: dict[str, Any]) -> dict[str, Any]:
    meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    description = material_description(item, "用户上传猫动作素材")
    return {
        "id": f"user/{item.get('upload_id')}",
        "type": "cat_motion",
        "file": item.get("file_path", ""),
        "description": f"{description}。来源：用户素材。优先用于本次生成。",
        "duration": float(meta.get("duration") or 0),
        "fps": meta.get("fps", 30),
        "width": parse_resolution(meta.get("resolution", ""))[0],
        "height": parse_resolution(meta.get("resolution", ""))[1],
        "source": "user_upload",
        "upload_id": item.get("upload_id", ""),
    }


def upload_to_background_asset(item: dict[str, Any]) -> dict[str, Any]:
    meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    description = material_description(item, "用户上传背景或参考图")
    return {
        "id": f"user/{item.get('upload_id')}",
        "type": "background",
        "scene": "user_upload",
        "file": item.get("file_path", ""),
        "description": f"{description}。来源：用户素材。优先用于本次生成。",
        "width": meta.get("width", 0),
        "height": meta.get("height", 0),
        "source": "user_upload",
        "upload_id": item.get("upload_id", ""),
    }


def upload_to_text_asset(item: dict[str, Any]) -> dict[str, Any]:
    meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    return {
        "id": f"user/{item.get('upload_id')}",
        "type": "text",
        "file": item.get("file_path", ""),
        "description": material_description(item, "用户上传文案素材"),
        "preview": meta.get("preview", ""),
        "source": "user_upload",
        "upload_id": item.get("upload_id", ""),
    }


def migration_context(
    *,
    session_id: str | None,
    viral_analysis_id: str | None,
    creative_brief: CreativeBrief | None,
    material_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    analysis = load_analysis(session_id, viral_analysis_id)
    brief = creative_brief.model_dump() if creative_brief else {}
    return {
        "session_id": session_id or "",
        "viral_analysis_id": viral_analysis_id or "",
        "viral_analysis": compact_analysis_for_agent(analysis),
        "creative_brief": {key: value for key, value in brief.items() if value not in ("", [], None)},
        "user_materials": material_summary or {},
    }


def compact_analysis_for_agent(structure: dict[str, Any] | None) -> dict[str, Any]:
    if not structure:
        return {}
    return {
        "summary": structure.get("migration_summary", {}),
        "transfer_slots": structure.get("transfer_slots", [])[:10],
        "storyboard": structure.get("storyboard_for_agent", [])[:10],
        "source": "uploaded_viral_video",
    }


def public_analysis_summary(structure: dict[str, Any] | None) -> dict[str, Any]:
    if not structure:
        return {}
    summary = structure.get("migration_summary") if isinstance(structure.get("migration_summary"), dict) else {}
    return {
        "title": summary.get("title", ""),
        "one_sentence": summary.get("one_sentence", ""),
        "duration": structure.get("meta", {}).get("duration", structure.get("meta", {}).get("duration_seconds", "")) if isinstance(structure.get("meta"), dict) else "",
        "shot_count": len(structure.get("storyboard_for_agent", []) or structure.get("shots", [])),
        "script_outline": summary.get("script_outline", []),
        "transferable_features": summary.get("transferable_features", []),
        "background_needs": summary.get("background_needs", []),
        "cat_needs": summary.get("cat_needs", []),
        "audio_style": summary.get("audio_style", ""),
    }


def migration_summary_from_structure(structure: dict[str, Any]) -> dict[str, Any]:
    raw_summary = structure.get("video_summary") if isinstance(structure.get("video_summary"), dict) else {}
    reusable = structure.get("reusable_patterns") if isinstance(structure.get("reusable_patterns"), dict) else {}
    audio = structure.get("audio_track") if isinstance(structure.get("audio_track"), dict) else {}
    tf = structure.get("transferable_features") if isinstance(structure.get("transferable_features"), dict) else {}
    sections = structure.get("script_structure") if isinstance(structure.get("script_structure"), list) else []
    shots = structure.get("shots") if isinstance(structure.get("shots"), list) else []
    storyboard = raw_storyboard(structure)
    if storyboard:
        return {
            "title": raw_summary.get("title") or "用户上传爆款参考",
            "one_sentence": raw_summary.get("one_sentence") or first_nonempty([item.get("script") for item in storyboard if isinstance(item, dict)]),
            "script_outline": [f"{item.get('beat', 'shot')}：{item.get('script', '')}" for item in storyboard[:6] if isinstance(item, dict)],
            "transferable_features": unique_texts([
                *list(reusable.get("script_templates", []) or []),
                *list(reusable.get("shot_templates", []) or []),
                *list(reusable.get("cat_action_templates", []) or [])[:2],
            ])[:8],
            "background_needs": unique_texts([stringify_short(item.get("background")) for item in storyboard if isinstance(item, dict)])[:6],
            "cat_needs": unique_texts([stringify_short(item.get("cats")) for item in storyboard if isinstance(item, dict)])[:6],
            "audio_style": stringify_audio(audio),
        }
    return {
        "title": raw_summary.get("title") or raw_summary.get("one_sentence") or "用户上传爆款参考",
        "one_sentence": raw_summary.get("one_sentence") or first_nonempty([sec.get("text") for sec in sections if isinstance(sec, dict)]),
        "script_outline": [
            f"{item.get('type', '段落')}：{item.get('text', '')}"
            for item in sections[:6]
            if isinstance(item, dict)
        ],
        "transferable_features": [
            item
            for item in [
                tf.get("hook_strategy"),
                tf.get("narrative_pattern"),
                tf.get("pacing_pattern"),
                tf.get("composition_pattern"),
            ]
            if item
        ],
        "background_needs": unique_texts([shot.get("content", "") for shot in shots if isinstance(shot, dict)])[:6],
        "cat_needs": unique_texts([shot.get("subject_motion", "") or shot.get("subject_position", "") for shot in shots if isinstance(shot, dict)])[:6],
        "audio_style": stringify_audio(structure.get("audio_structure", {})),
    }


def storyboard_for_agent(structure: dict[str, Any]) -> list[dict[str, Any]]:
    storyboard = raw_storyboard(structure)
    if storyboard:
        values = []
        for index, item in enumerate(storyboard[:10]):
            if not isinstance(item, dict):
                continue
            values.append({
                "beat": item.get("beat") or f"shot-{index + 1}",
                "script": stringify_short(item.get("script") or item.get("subtitle")),
                "replace_instruction": "迁移这条分镜的节奏、角色关系和笑点机制，台词必须按新主题重写。",
                "background_requirement": stringify_short(item.get("background")),
                "cat_requirement": stringify_short(item.get("cats")),
                "packaging_requirement": stringify_short(item.get("subtitle") or item.get("joke_point")),
                "duration": max(1.0, safe_float(item.get("duration"), 3.0)),
            })
        if values:
            return values
    shots = structure.get("shots") if isinstance(structure.get("shots"), list) else []
    sections = structure.get("script_structure") if isinstance(structure.get("script_structure"), list) else []
    values: list[dict[str, Any]] = []
    for index, shot in enumerate(shots[:10]):
        section = nearest_section(shot, sections)
        values.append({
            "beat": section.get("type") or f"shot-{index + 1}",
            "script": section.get("text") or shot.get("content", ""),
            "replace_instruction": "迁移节奏和笑点机制，台词必须按新主题重写。",
            "background_requirement": shot.get("content", ""),
            "cat_requirement": "；".join(str(shot.get(key, "")) for key in ("subject_distance", "subject_position", "subject_motion") if shot.get(key)),
            "packaging_requirement": f"字幕={shot.get('has_subtitle', False)}；特效={shot.get('visual_effect', '')}",
            "duration": max(1.0, float(shot.get("end_time", 0) or 0) - float(shot.get("start_time", 0) or 0)),
        })
    if values:
        return values
    for index, section in enumerate(sections[:8]):
        values.append({
            "beat": section.get("type", f"beat-{index + 1}"),
            "script": section.get("text", ""),
            "replace_instruction": "按新主题替换人物、场景和梗点。",
            "background_requirement": "",
            "cat_requirement": "",
            "packaging_requirement": "",
            "duration": max(1.0, float(section.get("end_time", 0) or 0) - float(section.get("start_time", 0) or 0)),
        })
    return values


def raw_storyboard(structure: dict[str, Any]) -> list[Any]:
    asset_plan = structure.get("asset_plan") if isinstance(structure.get("asset_plan"), dict) else {}
    storyboard = asset_plan.get("storyboard") if isinstance(asset_plan.get("storyboard"), list) else []
    return storyboard


def transfer_slots_from_structure(structure: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "slot": item.get("beat", f"shot-{index + 1}"),
            "keep": "节奏、情绪推进、字幕密度、角色关系",
            "rewrite": "台词、现实主题、具体道具和背景",
            "background": item.get("background_requirement", ""),
            "cat": item.get("cat_requirement", ""),
        }
        for index, item in enumerate(storyboard_for_agent(structure))
    ]


def storyboard_markdown(structure: dict[str, Any]) -> str:
    summary = public_analysis_summary(structure)
    lines = [
        f"# {summary.get('title') or '用户上传爆款分析'}",
        "",
        f"- 一句话：{summary.get('one_sentence', '')}",
        f"- 分镜数：{summary.get('shot_count', 0)}",
        f"- 音频风格：{summary.get('audio_style', '')}",
        "",
        "## 可迁移结构",
    ]
    for item in summary.get("transferable_features", []):
        lines.append(f"- {item}")
    lines.extend(["", "## 分镜拆解"])
    for item in structure.get("storyboard_for_agent", []):
        lines.append(f"- [{item.get('beat')}] {item.get('script')} | 背景：{item.get('background_requirement')} | 猫：{item.get('cat_requirement')}")
    return "\n".join(lines)


def nearest_section(shot: dict[str, Any], sections: list[Any]) -> dict[str, Any]:
    shot_start = float(shot.get("start_time", 0) or 0)
    for section in sections:
        if not isinstance(section, dict):
            continue
        start = float(section.get("start_time", 0) or 0)
        end = float(section.get("end_time", 0) or 0)
        if start <= shot_start <= end:
            return section
    return sections[0] if sections and isinstance(sections[0], dict) else {}


def material_description(item: dict[str, Any], fallback: str) -> str:
    text = str(item.get("description") or "").strip()
    if text:
        return text
    filename = Path(str(item.get("filename", ""))).stem
    return f"{fallback}：{filename}" if filename else fallback


def parse_resolution(value: str) -> tuple[int, int]:
    match = re.match(r"(\d+)x(\d+)", str(value or ""))
    if not match:
        return 0, 0
    return int(match.group(1)), int(match.group(2))


def relative_to_project(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(get_settings().PROJECT_ROOT.resolve()))
    except ValueError:
        return str(path)


def safe_filename(filename: str) -> str:
    path = Path(filename)
    stem = re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+", "-", path.stem).strip("-")[:80] or "upload"
    ext = re.sub(r"[^0-9A-Za-z.]+", "", path.suffix.lower())[:12]
    return f"{stem}{ext}"


def safe_slug(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "-", str(value or "")).strip("-")[:80]


def safe_error(exc: Exception) -> str:
    return str(exc)[:300]


def read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def first_nonempty(values: list[Any]) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def unique_texts(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def stringify_audio(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "；".join(str(item) for item in value.values() if str(item).strip())[:220]
    return ""


def stringify_short(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "；".join(stringify_short(item) for item in value if stringify_short(item))[:240]
    if isinstance(value, dict):
        for key in ("setting", "description", "text", "script", "subtitle", "voice", "bgm", "role", "action"):
            text = stringify_short(value.get(key))
            if text:
                return text
        return json.dumps(value, ensure_ascii=False)[:240]
    return "" if value is None else str(value)


def safe_float(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback
