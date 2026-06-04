from __future__ import annotations

import json
import asyncio
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.config import get_settings
from ..models.maomeme import CreativeBrief, MaoMemePlan, ScriptCandidate
from ..models.video_structure import VideoStructure
from .agent_tools import (
    asset_search_tool,
    background_fill_tool,
    clip_planner_tool,
    overlay_planner_tool,
    transition_planner_tool,
)
from .agent_runtime import (
    enabled_runtime_order,
    run_assembler_agent,
    run_critic_agent,
    run_shot_agent,
)
from .asset_index import assets_summary, load_assets, pick_background, pick_motion, rank_assets, ref
from .doubao_client import (
    ark_available,
    generate_candidates_with_doubao_context,
    generate_plan_with_doubao_context,
    stream_candidates_with_doubao_context,
)
from .text_materials import matching_preset_scenes, topic_for_agent
from .upload_store import merge_user_assets, migration_context
from .viral_structure_library import (
    build_migration_blueprint,
    infer_theme_category,
    migration_blueprint_prompt,
    viral_reference_notes,
    viral_reference_prompt,
    viral_references_for_theme,
    viral_template_seed,
)
from .video_analyzer import analyze_video_structure, source_summary


@dataclass
class GenerationContext:
    session_id: str | None = None
    viral_analysis_id: str | None = None
    user_material_ids: list[str] | None = None
    creative_brief: CreativeBrief | None = None
    migration: dict[str, Any] | None = None
    material_summary: dict[str, Any] | None = None


async def generate_maomeme_plan(
    theme: str,
    sample_video_path: str | None = None,
    use_doubao: bool = True,
) -> MaoMemePlan:
    index = load_assets()
    source_structure = await _source_structure(sample_video_path, use_doubao)
    source_text = source_summary(source_structure) if source_structure else fallback_source_text()
    text_context = topic_for_agent(theme)

    raw_plan: dict[str, Any] = {}
    if use_doubao and ark_available():
        raw_plan = await generate_plan_with_doubao_context(theme, source_text, assets_summary(index), text_context)

    plan = normalize_plan(raw_plan, theme, source_structure, index, text_context)
    save_plan(plan)
    return plan


async def generate_script_candidates(
    theme: str,
    sample_video_path: str | None = None,
    use_doubao: bool = True,
    duration_mode: str = "short",
    session_id: str | None = None,
    viral_analysis_id: str | None = None,
    user_material_ids: list[str] | None = None,
    creative_brief: CreativeBrief | None = None,
) -> list[ScriptCandidate]:
    index, gen_context = generation_index_and_context(session_id, viral_analysis_id, user_material_ids, creative_brief)
    text_context = topic_for_agent(theme)
    viral_refs = viral_references_for_theme(theme, text_context)
    migration_blueprint = build_migration_blueprint(theme, viral_refs, gen_context.migration or {}, text_context)
    viral_reference_text = combined_viral_reference_text(viral_refs, gen_context, migration_blueprint)
    scripts = []
    provider_note = "local_fallback"
    if use_doubao and ark_available():
        scripts = await generate_doubao_candidate_scripts_parallel(
            theme=theme,
            assets_text=assets_summary(index),
            text_context=text_context,
            duration_mode=normalize_duration_mode(duration_mode),
            viral_reference_text=viral_reference_text,
            creative_context=gen_context.migration or {},
        )
        provider_note = "doubao_agent" if scripts else "doubao_parse_failed_fallback"
    if not scripts:
        scripts = screenwriter_agent(theme, text_context, viral_refs, gen_context.migration or {}, migration_blueprint)
    scored = [(score_script(script, theme, index), script) for script in scripts]
    scored.sort(key=lambda item: item[0], reverse=True)
    candidates = [
        script_to_candidate(script, theme, score, text_context, idx + 1, duration_mode)
        for idx, (score, script) in enumerate(scored[:3])
    ]
    for candidate in candidates:
        candidate.notes.insert(0, f"生成来源：{provider_note}")
        attach_generation_context_to_candidate(candidate, gen_context, viral_refs, migration_blueprint)
        for note in reversed(combined_viral_reference_notes(viral_refs, gen_context)):
            candidate.notes.insert(1, note)
    while len(candidates) < 3:
        base = scripts[0] if scripts else screenwriter_agent(theme, {}, viral_refs, gen_context.migration or {}, migration_blueprint)[0]
        variant = json.loads(json.dumps(base, ensure_ascii=False))
        variant["name"] = f"{base.get('name', '候选')}·变体{len(candidates) + 1}"
        variant["beats"] = revise_beats_for_instruction(variant.get("beats", []), "更轻松一点")
        fallback_candidate = script_to_candidate(variant, theme, score_script(variant, theme, index) - len(candidates), text_context, len(candidates) + 1, duration_mode)
        fallback_candidate.notes.insert(0, f"生成来源：{provider_note}")
        attach_generation_context_to_candidate(fallback_candidate, gen_context, viral_refs, migration_blueprint)
        for note in reversed(combined_viral_reference_notes(viral_refs, gen_context)):
            fallback_candidate.notes.insert(1, note)
        candidates.append(fallback_candidate)
    return candidates


async def stream_script_candidates(
    theme: str,
    sample_video_path: str | None = None,
    use_doubao: bool = True,
    duration_mode: str = "short",
    session_id: str | None = None,
    viral_analysis_id: str | None = None,
    user_material_ids: list[str] | None = None,
    creative_brief: CreativeBrief | None = None,
):
    index, gen_context = generation_index_and_context(session_id, viral_analysis_id, user_material_ids, creative_brief)
    text_context = topic_for_agent(theme)
    mode = normalize_duration_mode(duration_mode)
    viral_refs = viral_references_for_theme(theme, text_context)
    migration_blueprint = build_migration_blueprint(theme, viral_refs, gen_context.migration or {}, text_context)
    viral_reference_text = combined_viral_reference_text(viral_refs, gen_context, migration_blueprint)

    if not use_doubao:
        yield {"type": "stage", "message": "测试模式：使用本地预设候选", "progress": 0.18}
        candidates = build_script_candidates(
            scripts=screenwriter_agent(theme, text_context, viral_refs, gen_context.migration or {}, migration_blueprint),
            theme=theme,
            index=index,
            text_context=text_context,
            duration_mode=mode,
            provider_note="local_fallback_explicit",
            viral_refs=viral_refs,
            gen_context=gen_context,
            migration_blueprint=migration_blueprint,
        )
        yield {"type": "final", "candidates": candidates, "provider_note": "local_fallback_explicit"}
        return

    if not ark_available():
        yield {"type": "stage", "message": "Doubao 未配置，回退本地候选", "progress": 0.18}
        candidates = build_script_candidates(
            scripts=screenwriter_agent(theme, text_context, viral_refs, gen_context.migration or {}, migration_blueprint),
            theme=theme,
            index=index,
            text_context=text_context,
            duration_mode=mode,
            provider_note="local_fallback_no_ark",
            viral_refs=viral_refs,
            gen_context=gen_context,
            migration_blueprint=migration_blueprint,
        )
        yield {"type": "final", "candidates": candidates, "provider_note": "local_fallback_no_ark"}
        return

    yield {"type": "stage", "message": "编剧 Agent 三路并发生成候选", "progress": 0.42}

    preview_candidates = build_script_candidates(
        scripts=screenwriter_agent(theme, text_context, viral_refs, gen_context.migration or {}, migration_blueprint),
        theme=theme,
        index=index,
        text_context=text_context,
        duration_mode=mode,
        provider_note="agent_preview",
        viral_refs=viral_refs,
        gen_context=gen_context,
        migration_blueprint=migration_blueprint,
    )
    for idx, candidate in enumerate(preview_candidates[:3], start=1):
        yield {
            "type": "draft_candidate",
            "candidate": candidate,
            "message": f"候选 {idx}/3 预览草稿已生成，等待真实 Agent 覆盖",
            "progress": 0.43 + idx * 0.02,
        }
        await asyncio.sleep(0)

    scripts: list[dict[str, Any]] = []
    async for event in stream_doubao_candidate_scripts_parallel(theme, assets_summary(index), text_context, mode, viral_reference_text, gen_context.migration or {}):
        if event["type"] == "script":
            script = event["script"]
            scripts.append(script)
            draft = script_to_candidate(
                script,
                theme,
                score_script(script, theme, index),
                text_context,
                int(event.get("position") or len(scripts)),
                mode,
            )
            draft.notes.insert(0, "生成来源：doubao_agent_parallel")
            attach_generation_context_to_candidate(draft, gen_context, viral_refs, migration_blueprint)
            for note in reversed(combined_viral_reference_notes(viral_refs, gen_context)):
                draft.notes.insert(1, note)
            yield {
                "type": "candidate",
                "candidate": draft,
                "message": f"候选 {event.get('position') or len(scripts)}/3 已可选择",
                "progress": event.get("progress", min(0.82, 0.48 + len(scripts) * 0.1)),
            }
        else:
            yield event

    scripts = dedupe_scripts(scripts)
    provider_note = "doubao_agent_parallel"
    if not scripts:
        provider_note = "doubao_parallel_parse_failed_fallback"
        scripts = screenwriter_agent(theme, text_context, viral_refs, gen_context.migration or {}, migration_blueprint)

    candidates = build_script_candidates(
        scripts=scripts,
        theme=theme,
        index=index,
        text_context=text_context,
        duration_mode=mode,
        provider_note=provider_note,
        viral_refs=viral_refs,
        gen_context=gen_context,
        migration_blueprint=migration_blueprint,
    )
    yield {"type": "final", "candidates": candidates, "provider_note": provider_note}


def candidate_angles(theme: str, text_context: dict[str, Any]) -> list[str]:
    tensions = [str(item) for item in text_context.get("tensions", []) if str(item).strip()]
    angles = [str(item) for item in text_context.get("meme_angles", []) if str(item).strip()]
    base = [
        "现实共鸣版：从一个具体生活动作切入，重点写真实压力和普通人的应对。",
        "黑色幽默版：矛盾更荒诞，梗更尖锐，但结尾不能让猫解决社会问题。",
        "双猫对话版：用左右两只猫对话推进冲突，适合做气泡字幕和飞物件包装。",
    ]
    if tensions:
        base[0] += f" 参考矛盾：{tensions[0]}"
    if len(tensions) > 1:
        base[1] += f" 参考矛盾：{tensions[1]}"
    if angles:
        base[2] += f" 参考网络梗角度：{angles[0]}"
    if any(word in theme for word in ("烤肠", "香肠", "摆摊", "小吃摊", "夜市", "地摊", "餐车")):
        base[0] += " 必须写到真实街边摊或校门口小吃摊。"
        base[1] += " 可以写摊位也内卷，但不要把摆摊当万能解法。"
    return base


async def generate_doubao_candidate_scripts_parallel(
    theme: str,
    assets_text: str,
    text_context: dict[str, Any],
    duration_mode: str,
    viral_reference_text: str = "",
    creative_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    scripts: list[dict[str, Any]] = []
    async for event in stream_doubao_candidate_scripts_parallel(theme, assets_text, text_context, duration_mode, viral_reference_text, creative_context):
        if event["type"] == "script":
            scripts.append(event["script"])
    return dedupe_scripts(scripts)


async def stream_doubao_candidate_scripts_parallel(
    theme: str,
    assets_text: str,
    text_context: dict[str, Any],
    duration_mode: str,
    viral_reference_text: str = "",
    creative_context: dict[str, Any] | None = None,
):
    settings = get_settings()
    semaphore = asyncio.Semaphore(settings.ARK_AGENT_CONCURRENCY)
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def run_angle(position: int, angle: str) -> None:
        async with semaphore:
            content_size = 0
            try:
                async with asyncio.timeout(settings.CANDIDATE_AGENT_TIMEOUT_SEC):
                    async for event in stream_candidates_with_doubao_context(
                        theme=theme,
                        assets_summary=assets_text,
                        text_context=text_context,
                        duration_mode=duration_mode,
                        angle=angle,
                        viral_reference_text=viral_reference_text,
                        creative_context=creative_context or {},
                    ):
                        if event.get("type") == "delta":
                            text = str(event.get("text", ""))
                            content_size += len(text)
                            if text:
                                await queue.put({
                                    "type": "agent_delta",
                                    "position": position,
                                    "angle": angle,
                                    "text": text,
                                    "progress": min(0.74, 0.44 + content_size / 3600),
                                })
                        elif event.get("type") == "final":
                            scripts = normalize_doubao_candidate_scripts(event.get("raw") if isinstance(event.get("raw"), dict) else {})
                            await queue.put({"type": "script", "position": position, "angle": angle, "scripts": scripts})
                            return
            except TimeoutError:
                await queue.put({"type": "timeout", "position": position, "angle": angle})
                return
            except Exception:
                await queue.put({"type": "error", "position": position, "angle": angle})
                return
        await queue.put({"type": "script", "position": position, "angle": angle, "scripts": []})

    tasks = [asyncio.create_task(run_angle(index, angle)) for index, angle in enumerate(candidate_angles(theme, text_context), start=1)]
    completed = 0
    while completed < len(tasks):
        result = await queue.get()
        if result["type"] == "agent_delta":
            yield {
                "type": "agent_delta",
                "position": result.get("position"),
                "angle": result.get("angle"),
                "text": result.get("text", ""),
                "message": f"候选 {result.get('position')}/3 正在流式生成",
                "progress": result.get("progress", 0.54),
            }
            continue
        if result["type"] in {"timeout", "error"}:
            completed += 1
            yield {
                "type": "stage",
                "message": f"候选方向 {result.get('position')}/3 暂未返回可用结果，继续等待其他方向",
                "progress": min(0.78, 0.48 + completed * 0.08),
            }
            continue
        completed += 1
        scripts = dedupe_scripts(result["scripts"])
        if scripts:
            yield {
                "type": "script",
                "script": scripts[0],
                "angle": result["angle"],
                "position": result["position"],
                "progress": min(0.82, 0.48 + completed * 0.1),
            }
        else:
            yield {
                "type": "stage",
                "message": f"第 {completed}/3 路编剧 Agent 结果需要回退清洗",
                "progress": min(0.78, 0.48 + completed * 0.08),
            }
    await asyncio.gather(*tasks, return_exceptions=True)


def dedupe_scripts(scripts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for script in scripts:
        beats = script.get("beats", [])
        signature = "|".join(str(item[1] if isinstance(item, (list, tuple)) and len(item) > 1 else item) for item in beats)
        signature = f"{script.get('name', '')}|{signature}"
        if signature in seen:
            continue
        seen.add(signature)
        unique.append(script)
    return unique


def generation_index_and_context(
    session_id: str | None,
    viral_analysis_id: str | None,
    user_material_ids: list[str] | None,
    creative_brief: CreativeBrief | None,
) -> tuple[dict[str, Any], GenerationContext]:
    base_index = load_assets()
    merged_index, material_summary = merge_user_assets(base_index, session_id, user_material_ids or [])
    migration = migration_context(
        session_id=session_id,
        viral_analysis_id=viral_analysis_id,
        creative_brief=creative_brief,
        material_summary=material_summary,
    )
    context = GenerationContext(
        session_id=session_id,
        viral_analysis_id=viral_analysis_id,
        user_material_ids=user_material_ids or [],
        creative_brief=creative_brief,
        migration=migration,
        material_summary=material_summary,
    )
    return merged_index, context


def combined_viral_reference_text(
    viral_refs: list[dict[str, Any]],
    gen_context: GenerationContext | None,
    migration_blueprint: dict[str, Any] | None = None,
) -> str:
    parts: list[str] = []
    if migration_blueprint:
        parts.append("## 强制迁移蓝图与 3 条 compact few-shot\n" + migration_blueprint_prompt(migration_blueprint))
    migration = (gen_context.migration if gen_context else {}) or {}
    viral = migration.get("viral_analysis") if isinstance(migration.get("viral_analysis"), dict) else {}
    if viral:
        parts.append("## 用户上传爆款结构（最高优先级）\n" + json.dumps(viral, ensure_ascii=False))
    brief = migration.get("creative_brief") if isinstance(migration.get("creative_brief"), dict) else {}
    if brief:
        parts.append("## 用户创作补充\n" + json.dumps(brief, ensure_ascii=False))
    materials = migration.get("user_materials") if isinstance(migration.get("user_materials"), dict) else {}
    if materials and any(materials.get(key, 0) for key in ("user_motion_count", "user_background_count", "user_text_count")):
        parts.append("## 用户素材覆盖\n" + json.dumps(materials, ensure_ascii=False))
    public_refs = viral_reference_prompt(viral_refs)
    if public_refs:
        parts.append("## 项目爆款结构库（次级参考）\n" + public_refs)
    return "\n\n".join(parts)


def combined_viral_reference_notes(viral_refs: list[dict[str, Any]], gen_context: GenerationContext | None) -> list[str]:
    notes = []
    if gen_context and gen_context.viral_analysis_id:
        notes.append("已优先迁移用户上传爆款视频结构。")
    notes.extend(viral_reference_notes(viral_refs or []))
    return notes


def generation_context_notes(gen_context: GenerationContext | None) -> list[str]:
    if not gen_context:
        return []
    notes = []
    materials = gen_context.material_summary or {}
    motion_count = int(materials.get("user_motion_count") or 0)
    background_count = int(materials.get("user_background_count") or 0)
    text_count = int(materials.get("user_text_count") or 0)
    if gen_context.viral_analysis_id:
        notes.append(f"上传爆款结构：{gen_context.viral_analysis_id}")
    if motion_count or background_count or text_count:
        notes.append(f"用户素材优先：猫视频 {motion_count} 个，背景/参考图 {background_count} 个，文案 {text_count} 个。")
    brief = gen_context.creative_brief
    if brief and (brief.target_audience or brief.protagonist or brief.core_conflict or brief.ending_tone):
        notes.append("已读取用户补充信息：受众、主角、冲突和结尾倾向会影响剧本。")
    return notes


def attach_generation_context_to_candidate(
    candidate: ScriptCandidate,
    gen_context: GenerationContext | None,
    viral_refs: list[dict[str, Any]] | None = None,
    migration_blueprint: dict[str, Any] | None = None,
) -> None:
    blueprint = migration_blueprint or candidate.migration_blueprint or {}
    if blueprint:
        candidate.migration_blueprint = blueprint
        primary = blueprint.get("primary_reference") if isinstance(blueprint.get("primary_reference"), dict) else {}
        supporting = blueprint.get("supporting_references") if isinstance(blueprint.get("supporting_references"), list) else []
        if primary:
            candidate.source_reference = {
                "type": "uploaded_viral_video" if blueprint.get("source_priority") == "uploaded_viral_first" else "viral_library",
                "title": primary.get("title", ""),
                "id": primary.get("id", ""),
                "structure_tags": primary.get("structure_tags", []),
                "supporting": [
                    {
                        "id": item.get("id", ""),
                        "title": item.get("title", ""),
                        "structure_tags": item.get("structure_tags", []),
                    }
                    for item in supporting
                    if isinstance(item, dict)
                ],
            }
            candidate.asset_hints["viral_reference_id"] = primary.get("id", "")
            candidate.asset_hints["viral_reference_title"] = primary.get("title", "")
            candidate.asset_hints["viral_structure_tags"] = primary.get("structure_tags", [])
    elif viral_refs:
        primary = viral_refs[0]
        candidate.source_reference = {
            "type": "viral_library",
            "title": primary.get("title", ""),
            "id": primary.get("id", ""),
            "structure_tags": primary.get("structure_tags", []),
        }
    if not gen_context:
        return
    migration = gen_context.migration or {}
    viral = migration.get("viral_analysis") if isinstance(migration.get("viral_analysis"), dict) else {}
    summary = viral.get("summary") if isinstance(viral.get("summary"), dict) else {}
    if summary:
        candidate.asset_hints["uploaded_viral_analysis_id"] = gen_context.viral_analysis_id or ""
        candidate.asset_hints["uploaded_viral_title"] = summary.get("title", "")
    materials = gen_context.material_summary or {}
    if any(materials.get(key, 0) for key in ("user_motion_count", "user_background_count", "user_text_count")):
        candidate.user_material_coverage = {
            "available": True,
            "motion_count": materials.get("user_motion_count", 0),
            "background_count": materials.get("user_background_count", 0),
            "text_count": materials.get("user_text_count", 0),
            "strategy": "分镜阶段优先匹配用户素材，不足时使用内置素材补足。",
        }


def apply_generation_context_to_beats(beats: list[dict[str, Any]], gen_context: GenerationContext | None) -> None:
    if not gen_context:
        return
    migration = gen_context.migration or {}
    viral = migration.get("viral_analysis") if isinstance(migration.get("viral_analysis"), dict) else {}
    storyboard = viral.get("storyboard") if isinstance(viral.get("storyboard"), list) else []
    summary = viral.get("summary") if isinstance(viral.get("summary"), dict) else {}
    for index, beat in enumerate(beats):
        if storyboard:
            source = storyboard[min(index, len(storyboard) - 1)]
            if isinstance(source, dict):
                beat["uploaded_viral_reference"] = {
                    "title": summary.get("title", ""),
                    "beat": source.get("beat", ""),
                    "script": source.get("script", ""),
                    "background": source.get("background_requirement", ""),
                    "cat": source.get("cat_requirement", ""),
                    "packaging": source.get("packaging_requirement", ""),
                }
                background = str(source.get("background_requirement", ""))
                cat = str(source.get("cat_requirement", ""))
                if background and viral_background_fits_beat(background, beat):
                    beat["scene_keywords"] = list(dict.fromkeys([*beat.get("scene_keywords", []), background]))
                if cat:
                    beat["emotion_keywords"] = list(dict.fromkeys([*beat.get("emotion_keywords", []), cat]))
        brief = gen_context.creative_brief
        if brief:
            if brief.required_scenes:
                beat["scene_keywords"] = list(dict.fromkeys([*beat.get("scene_keywords", []), brief.required_scenes]))
            if brief.required_props:
                beat["must_keywords"] = list(dict.fromkeys([*beat.get("must_keywords", []), brief.required_props]))
            if brief.avoid_content:
                beat["forbidden_keywords"] = list(dict.fromkeys([*beat.get("forbidden_keywords", []), brief.avoid_content]))
            if not brief.allow_multi_cat and beat.get("layout") == "dialogue":
                beat["layout"] = "single"
                beat["dialogue"] = []


def apply_migration_blueprint_to_beats(beats: list[dict[str, Any]], blueprint: dict[str, Any] | None) -> None:
    if not blueprint:
        return
    shots = blueprint.get("shots") if isinstance(blueprint.get("shots"), list) else []
    if not shots:
        return
    for index, beat in enumerate(beats):
        source = shots[min(index, len(shots) - 1)]
        if not isinstance(source, dict):
            continue
        beat["source_viral_shot"] = {
            "source": "uploaded_viral" if blueprint.get("source_priority") == "uploaded_viral_first" else "viral_library",
            "viral_id": source.get("source_viral_id", ""),
            "viral_title": source.get("source_viral_title", ""),
            "shot_id": source.get("source_shot_id", ""),
            "beat": source.get("source_beat", ""),
            "joke_point": source.get("source_joke_point", ""),
            "transfer_role": source.get("transfer_role", ""),
        }
        beat["viral_reference"] = {
            "id": source.get("source_viral_id", ""),
            "title": source.get("source_viral_title", ""),
            "beat": source.get("source_beat", ""),
            "joke_point": source.get("source_joke_point", ""),
            "background": source.get("background_requirement", ""),
            "cats": source.get("cat_action_requirement", ""),
            "audio": source.get("audio_requirement", ""),
        }
        background = str(source.get("background_requirement") or "")
        cat = str(source.get("cat_action_requirement") or "")
        if background and viral_background_fits_beat(background, beat):
            beat["scene_keywords"] = list(dict.fromkeys([background, *beat.get("scene_keywords", [])]))
        if cat:
            beat["emotion_keywords"] = list(dict.fromkeys([cat, *beat.get("emotion_keywords", [])]))
        packaging = str(source.get("subtitle_packaging") or "")
        if packaging:
            beat["packaging_requirement"] = packaging


def migration_blueprint_note(blueprint: dict[str, Any] | None) -> str:
    if not blueprint:
        return ""
    primary = blueprint.get("primary_reference") if isinstance(blueprint.get("primary_reference"), dict) else {}
    title = primary.get("title", "")
    tags = "、".join(str(item) for item in primary.get("structure_tags", [])[:3]) if isinstance(primary.get("structure_tags"), list) else ""
    return f"迁移蓝图：主爆款《{title}》{f'（{tags}）' if tags else ''}。"


def build_script_candidates(
    scripts: list[dict[str, Any]],
    theme: str,
    index: dict[str, Any],
    text_context: dict[str, Any],
    duration_mode: str,
    provider_note: str,
    viral_refs: list[dict[str, Any]] | None = None,
    gen_context: GenerationContext | None = None,
    migration_blueprint: dict[str, Any] | None = None,
) -> list[ScriptCandidate]:
    if not scripts:
        scripts = screenwriter_agent(theme, text_context, viral_refs, (gen_context.migration if gen_context else {}) or {}, migration_blueprint)
    scored = [(score_script(script, theme, index), script) for script in scripts]
    scored.sort(key=lambda item: item[0], reverse=True)
    candidates = [
        script_to_candidate(script, theme, score, text_context, idx + 1, duration_mode)
        for idx, (score, script) in enumerate(scored[:3])
    ]
    for candidate in candidates:
        candidate.notes.insert(0, f"生成来源：{provider_note}")
        attach_generation_context_to_candidate(candidate, gen_context, viral_refs or [], migration_blueprint)
        for note in reversed(combined_viral_reference_notes(viral_refs or [], gen_context)):
            candidate.notes.insert(1, note)
    while len(candidates) < 3:
        base = scripts[0] if scripts else screenwriter_agent(theme, {}, viral_refs, (gen_context.migration if gen_context else {}) or {}, migration_blueprint)[0]
        variant = json.loads(json.dumps(base, ensure_ascii=False))
        variant["name"] = f"{base.get('name', '候选')}·变体{len(candidates) + 1}"
        variant["beats"] = revise_beats_for_instruction(variant.get("beats", []), "更轻松一点")
        fallback_candidate = script_to_candidate(
            variant,
            theme,
            score_script(variant, theme, index) - len(candidates),
            text_context,
            len(candidates) + 1,
            duration_mode,
        )
        fallback_candidate.notes.insert(0, f"生成来源：{provider_note}")
        attach_generation_context_to_candidate(fallback_candidate, gen_context, viral_refs or [], migration_blueprint)
        for note in reversed(combined_viral_reference_notes(viral_refs or [], gen_context)):
            fallback_candidate.notes.insert(1, note)
        candidates.append(fallback_candidate)
    return candidates


async def plan_from_candidate(
    theme: str,
    candidate: ScriptCandidate,
    sample_video_path: str | None = None,
    use_doubao: bool = True,
    duration_mode: str = "short",
    session_id: str | None = None,
    viral_analysis_id: str | None = None,
    user_material_ids: list[str] | None = None,
    creative_brief: CreativeBrief | None = None,
) -> MaoMemePlan:
    index, gen_context = generation_index_and_context(session_id, viral_analysis_id, user_material_ids, creative_brief)
    source_structure = await _source_structure(sample_video_path, use_doubao, prefer_fast=sample_video_path is None)
    text_context = topic_for_agent(theme)
    viral_refs = viral_references_for_theme(theme, text_context)
    migration_blueprint = candidate.migration_blueprint or build_migration_blueprint(theme, viral_refs, gen_context.migration or {}, text_context)
    script = candidate_to_script(candidate)
    script["source_blueprint"] = migration_blueprint
    mode = normalize_duration_mode(duration_mode)
    script["beats"] = select_beats_for_mode(script.get("beats", []), mode, theme, text_context)
    beats = director_agent(script, theme, mode)
    apply_generation_context_to_beats(beats, gen_context)
    apply_migration_blueprint_to_beats(beats, migration_blueprint)
    apply_viral_patterns_to_beats(beats, candidate, viral_refs)
    if use_doubao and enabled_runtime_order():
        timeline, notes = await casting_and_validator_agents_agentic(beats, theme, index)
    else:
        timeline, notes = casting_and_validator_agents(beats, theme, index)
    timeline, assemble_notes = assemble_timeline_locally(timeline)
    notes.extend(assemble_notes)
    plan = MaoMemePlan(
        id=f"maomeme-{int(time.time())}",
        theme=theme,
        source_structure=_source_payload(source_structure),
        script=[
            {"type": beat["role"], "text": beat["caption"], "purpose": beat["intent"], "duration": round(beat["end"] - beat["start"], 2)}
            for beat in beats
        ],
        timeline=timeline,
        material_needs=material_needs_from_timeline(timeline),
        agent_notes=[
            f"用户选择剧本：{candidate.title}",
            f"素材覆盖评分：{candidate.score:.1f}",
            f"目标时长模式：{mode}，预计 {round(beats[-1]['end'], 1) if beats else 0}s。",
            *combined_viral_reference_notes(viral_refs, gen_context),
            migration_blueprint_note(migration_blueprint),
            *generation_context_notes(gen_context),
            *context_notes(text_context),
            *notes,
        ],
    )
    save_plan(plan)
    return plan


async def storyboard_stream_from_candidate(
    theme: str,
    candidate: ScriptCandidate,
    sample_video_path: str | None = None,
    use_doubao: bool = True,
    duration_mode: str = "short",
    session_id: str | None = None,
    viral_analysis_id: str | None = None,
    user_material_ids: list[str] | None = None,
    creative_brief: CreativeBrief | None = None,
):
    index, gen_context = generation_index_and_context(session_id, viral_analysis_id, user_material_ids, creative_brief)
    yield {"type": "stage", "message": "素材索引已读取，正在拆解剧本", "progress": 0.08}
    text_context = topic_for_agent(theme)
    viral_refs = viral_references_for_theme(theme, text_context)
    migration_blueprint = candidate.migration_blueprint or build_migration_blueprint(theme, viral_refs, gen_context.migration or {}, text_context)
    script = candidate_to_script(candidate)
    script["source_blueprint"] = migration_blueprint
    mode = normalize_duration_mode(duration_mode)
    script["beats"] = select_beats_for_mode(script.get("beats", []), mode, theme, text_context)
    beats = director_agent(script, theme, mode)
    apply_generation_context_to_beats(beats, gen_context)
    apply_migration_blueprint_to_beats(beats, migration_blueprint)
    apply_viral_patterns_to_beats(beats, candidate, viral_refs)
    source_task = asyncio.create_task(_source_structure(sample_video_path, use_doubao, prefer_fast=sample_video_path is None))
    yield {
        "type": "stage",
        "message": f"导演 Agent 已拆出 {len(beats)} 个镜头",
        "progress": 0.2,
        "script": [
            {"type": beat["role"], "text": beat["caption"], "purpose": beat["intent"], "duration": round(beat["end"] - beat["start"], 2)}
            for beat in beats
        ],
    }
    timeline: list[dict[str, Any]] = []
    notes: list[str] = []
    async for event in casting_and_validator_agents_stream(beats, theme, index, progress_start=0.25, progress_span=0.62, use_agent=use_doubao):
        if event.get("type") == "slot" and event.get("slot"):
            timeline.append(event["slot"])
        if event.get("type") == "slot_patch" and event.get("slot"):
            timeline = [event["slot"] if slot.get("id") == event["slot"].get("id") else slot for slot in timeline]
            timeline = sort_timeline(timeline)
        if event.get("type") == "notes":
            notes.extend(event.get("notes", []))
        yield event
    source_structure = await source_task
    timeline = sort_timeline(timeline)
    plan = MaoMemePlan(
        id=f"maomeme-{int(time.time())}",
        theme=theme,
        source_structure=_source_payload(source_structure),
        script=[
            {"type": beat["role"], "text": beat["caption"], "purpose": beat["intent"], "duration": round(beat["end"] - beat["start"], 2)}
            for beat in beats
        ],
        timeline=timeline,
        material_needs=material_needs_from_timeline(timeline),
        agent_notes=[
            f"用户选择剧本：{candidate.title}",
            f"素材覆盖评分：{candidate.score:.1f}",
            f"目标时长模式：{mode}，预计 {round(beats[-1]['end'], 1) if beats else 0}s。",
            *combined_viral_reference_notes(viral_refs, gen_context),
            migration_blueprint_note(migration_blueprint),
            *generation_context_notes(gen_context),
            *context_notes(text_context),
            *notes,
        ],
    )
    save_plan(plan)
    yield {"type": "done", "message": "分镜时间线生成完成", "progress": 1.0, "plan": plan.model_dump(by_alias=True)}


async def suggest_revisions(
    theme: str,
    plan: MaoMemePlan | None = None,
    candidate: ScriptCandidate | None = None,
    duration_mode: str = "short",
    creative_brief: CreativeBrief | None = None,
) -> list[str]:
    text = f"{theme} "
    if plan:
        text += " ".join(slot.caption for slot in plan.timeline)
    elif candidate:
        text += " ".join(str(item.get("text", "")) for item in candidate.script)
    suggestions = [
        "更讽刺一点，但结尾留一点温暖",
        "减少说教，多一点具体生活细节",
        "加强两只猫对话冲突",
    ]
    if any(word in text for word in ("结婚", "彩礼", "买房", "房贷")):
        suggestions = [
            "把矛盾从爱情转到现实账单，但不要攻击任何一方",
            "结尾改成两只猫一起谈条件，更现实一点",
            "增加父母和银行压力的对话梗",
        ]
    elif any(word in text for word in ("工作", "简历", "岗位", "面试")):
        suggestions = [
            "把岗位要求写得更离谱，但结尾改成识别规则",
            "增加 HR 和同学的双猫对话",
            "减少鸡汤，突出投简历黑洞的荒诞感",
        ]
    elif any(word in text for word in ("上班", "加班", "会议", "内卷")):
        suggestions = [
            "把会议内卷写得更讽刺一点",
            "结尾改成猫设置免打扰，爽感更强",
            "增加老板在吗弹窗和同事同步梗",
        ]
    if duration_mode != "minute":
        suggestions.append("改成一分钟左右，增加现实证据和群体共鸣")
    if creative_brief and creative_brief.ending_tone:
        suggestions.insert(0, f"结尾按“{creative_brief.ending_tone}”方向重写，但不要强行圆满")
    if creative_brief and creative_brief.allow_multi_cat:
        suggestions.append("增加一个双猫对话分镜，主角仍控制在一两只猫")
    return suggestions[:4]


async def revise_maomeme(
    theme: str,
    instruction: str,
    plan: MaoMemePlan | None = None,
    candidate: ScriptCandidate | None = None,
    use_doubao: bool = True,
    duration_mode: str = "short",
    session_id: str | None = None,
    viral_analysis_id: str | None = None,
    user_material_ids: list[str] | None = None,
    creative_brief: CreativeBrief | None = None,
) -> dict[str, Any]:
    if candidate:
        script = candidate_to_script(candidate)
    elif plan:
        script = plan_to_script(plan)
    else:
        candidates = await generate_script_candidates(theme, use_doubao=use_doubao)
        script = candidate_to_script(candidates[0])
    mode = duration_mode_from_instruction(instruction, duration_mode)
    text_context = topic_for_agent(theme)
    script["beats"] = revise_beats_for_instruction(script.get("beats", []), instruction)
    script["beats"] = expand_beats_for_duration(script.get("beats", []), mode, theme, text_context)
    revised_candidate = script_to_candidate(script, theme, 0.0, text_context, 1, mode)
    revised_plan = await plan_from_candidate(
        theme,
        revised_candidate,
        use_doubao=use_doubao,
        duration_mode=mode,
        session_id=session_id,
        viral_analysis_id=viral_analysis_id,
        user_material_ids=user_material_ids,
        creative_brief=creative_brief,
    )
    revised_plan.agent_notes.insert(0, f"自然语言调整：{instruction}")
    save_plan(revised_plan)
    return {"candidate": revised_candidate, "plan": revised_plan}


async def _source_structure(sample_video_path: str | None, use_doubao: bool, prefer_fast: bool = False) -> VideoStructure | None:
    if not sample_video_path:
        default = get_settings().PROJECT_ROOT / "samples" / "viral"
        candidates = sorted(default.glob("*.mp4")) if default.exists() else []
        sample_video_path = str(candidates[0]) if candidates else None
    if not sample_video_path:
        return None
    path = Path(sample_video_path)
    if not path.exists():
        return None
    return await analyze_video_structure(str(path), use_doubao=use_doubao and not prefer_fast)


def normalize_plan(
    raw: dict[str, Any],
    theme: str,
    source_structure: VideoStructure | None,
    index: dict[str, Any],
    text_context: dict[str, Any] | None = None,
) -> MaoMemePlan:
    if isinstance(raw.get("timeline"), list) and raw["timeline"]:
        data = {
            "id": f"maomeme-{int(time.time())}",
            "theme": theme,
            "source_structure": _source_payload(source_structure),
            "script": raw.get("script") if isinstance(raw.get("script"), list) else [],
            "timeline": raw["timeline"],
            "material_needs": raw.get("material_needs") if isinstance(raw.get("material_needs"), dict) else {},
            "agent_notes": raw.get("agent_notes") if isinstance(raw.get("agent_notes"), list) else [],
        }
        try:
            return MaoMemePlan.model_validate(data)
        except Exception:
            pass
    return multi_agent_fallback_plan(theme, source_structure, index, text_context or {})


def script_to_candidate(script: dict[str, Any], theme: str, score: float, text_context: dict[str, Any], index: int, duration_mode: str = "short") -> ScriptCandidate:
    mode = normalize_duration_mode(duration_mode)
    beats = [
        (role, clean_caption(caption), intent)
        for role, caption, intent in expand_beats_for_duration(script.get("beats", []), mode, theme, text_context)
    ]
    durations = durations_for_mode(mode, len(beats))
    beat_seed = [
        {"role": role, "caption": caption, "intent": intent}
        for role, caption, intent in beats
    ]
    return ScriptCandidate(
        id=f"candidate-{index}",
        title=str(script.get("name") or f"候选剧本 {index}"),
        theme=theme,
        social_topic=str(script.get("social_topic") or text_context.get("title", "")) if text_context else str(script.get("social_topic", "")),
        tension=str(script.get("tension") or first_text(text_context.get("tensions", []))) if text_context else str(script.get("tension", "")),
        score=round(float(score), 2),
        script=[
            {"type": role, "text": caption, "purpose": intent, "duration": duration}
            for (role, caption, intent), duration in zip(beats, durations)
        ],
        beat_seed=beat_seed,
        asset_hints={
            "motions": script.get("emotion", []),
            "backgrounds": script.get("scene", []),
            "keywords": script.get("theme_keywords", []),
            "viral_reference_id": script.get("viral_reference_id", ""),
            "viral_reference_title": script.get("viral_reference_title", ""),
            "viral_structure_tags": script.get("viral_structure_tags", []),
        },
        notes=context_notes(text_context),
        migration_blueprint=script.get("source_blueprint", {}) if isinstance(script.get("source_blueprint"), dict) else {},
    )


def normalize_doubao_candidate_scripts(raw: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = raw.get("candidates") if isinstance(raw, dict) else None
    if not isinstance(candidates, list):
        return []
    normalized = []
    for item in candidates[:3]:
        if not isinstance(item, dict):
            continue
        beats = []
        for beat in item.get("beats", []):
            if isinstance(beat, (list, tuple)) and len(beat) >= 3:
                beats.append((str(beat[0]), clean_caption(str(beat[1])), str(beat[2])))
            elif isinstance(beat, dict):
                beats.append((
                    str(beat.get("role") or beat.get("type") or "setup"),
                    clean_caption(str(beat.get("caption") or beat.get("text") or "")),
                    str(beat.get("intent") or beat.get("purpose") or ""),
                ))
        beats = [(role, caption, intent) for role, caption, intent in beats if caption]
        if len(beats) < 3:
            continue
        normalized.append({
            "name": str(item.get("name") or item.get("title") or f"Agent 候选 {len(normalized) + 1}"),
            "beats": [(role, humanize_caption(caption), intent) for role, caption, intent in beats],
            "scene": list_or_empty(item.get("scene")),
            "theme_keywords": list_or_empty(item.get("theme_keywords")),
            "emotion": list_or_empty(item.get("emotion")),
            "social_topic": str(item.get("social_topic") or ""),
            "tension": str(item.get("tension") or ""),
            "viral_reference_id": str(item.get("viral_reference_id") or ""),
            "viral_reference_title": str(item.get("viral_reference_title") or ""),
            "viral_structure_tags": list_or_empty(item.get("viral_structure_tags")),
            "source_blueprint": item.get("migration_blueprint") if isinstance(item.get("migration_blueprint"), dict) else {},
        })
    return normalized


def list_or_empty(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def candidate_to_script(candidate: ScriptCandidate) -> dict[str, Any]:
    return {
        "name": candidate.title,
        "beats": [
            (item.get("role", "setup"), clean_caption(item.get("caption", "")), item.get("intent", ""))
            for item in candidate.beat_seed
        ],
        "scene": candidate.asset_hints.get("backgrounds", []),
        "theme_keywords": candidate.asset_hints.get("keywords", []),
        "emotion": candidate.asset_hints.get("motions", []),
        "viral_reference_id": candidate.asset_hints.get("viral_reference_id", ""),
        "viral_reference_title": candidate.asset_hints.get("viral_reference_title", ""),
        "viral_structure_tags": candidate.asset_hints.get("viral_structure_tags", []),
        "source_blueprint": candidate.migration_blueprint,
    }


def plan_to_script(plan: MaoMemePlan) -> dict[str, Any]:
    return {
        "name": "当前分镜修订版",
        "beats": [
            (slot.role, slot.caption, slot.intent)
            for slot in plan.timeline
        ],
        "scene": [slot.background.id.split("/")[0] for slot in plan.timeline],
        "theme_keywords": [],
        "emotion": [slot.motion.description for slot in plan.timeline],
    }


def revise_beats_for_instruction(beats: list[Any], instruction: str) -> list[tuple[str, str, str]]:
    revised = [(role, caption, intent) for role, caption, intent in beats]
    if "讽刺" in instruction or "锐利" in instruction:
        revised = [(r, sharpen_caption(c), f"{i}，更讽刺") for r, c, i in revised]
    if "温暖" in instruction or "暖" in instruction:
        if revised:
            role, caption, intent = revised[-1]
            revised[-1] = (role, warm_caption(caption), f"{intent}，温暖收束")
    if "减少说教" in instruction or "轻松" in instruction:
        revised = [(r, casual_caption(c), i.replace("数据/细节", "生活细节")) for r, c, i in revised]
    if "一分钟" in instruction or "1分钟" in instruction or "60秒" in instruction or "更长" in instruction:
        revised.extend([
            ("proof", "同学说他也一样", "补充共鸣证据"),
            ("cta", "猫猫明天继续试试", "轻 CTA 收束"),
        ])
    return revised


def normalize_duration_mode(mode: str | None) -> str:
    if mode in {"medium", "minute"}:
        return mode
    return "short"


def duration_mode_from_instruction(instruction: str, fallback: str = "short") -> str:
    if any(word in instruction for word in ("一分钟", "1分钟", "60秒", "一分钟左右")):
        return "minute"
    if any(word in instruction for word in ("30秒", "半分钟", "中等", "稍微长")):
        return "medium"
    if any(word in instruction for word in ("短一点", "十几秒", "12秒")):
        return "short"
    return normalize_duration_mode(fallback)


def durations_for_mode(mode: str, count: int) -> list[float]:
    base = {
        "short": [2.0, 3.0, 3.4, 3.2],
        "medium": [3.8, 5.0, 5.4, 5.0, 5.3, 5.5],
        "minute": [6.5, 7.0, 7.5, 7.0, 7.5, 7.0, 8.0, 7.5],
    }[normalize_duration_mode(mode)]
    if count <= len(base):
        return base[:count]
    return base + [base[-1]] * (count - len(base))


def expand_beats_for_duration(beats: list[Any], mode: str, theme: str, text_context: dict[str, Any] | None = None) -> list[tuple[str, str, str]]:
    normalized = [(role, caption, intent) for role, caption, intent in beats]
    target = {"short": 4, "medium": 6, "minute": 8}[normalize_duration_mode(mode)]
    if len(normalized) >= target:
        return select_representative_beats(normalized, target)

    topic = text_context or {}
    tensions = topic.get("tensions") or []
    angles = topic.get("meme_angles") or []
    facts = topic.get("facts") or []
    theme_short = _setup_copy(theme)
    inserts = [
        ("pressure", tensions[1] if len(tensions) > 1 else f"{theme_short}开始变得离谱", "把社会压力具体化"),
        ("proof", facts[0] if facts else "大家嘴上说不卷，手上都在加速", "补充现实证据"),
        ("twist", angles[1] if len(angles) > 1 else "猫发现不是自己太菜，是规则太绕", "制造反差转折"),
        ("echo", tensions[2] if len(tensions) > 2 else "原来旁边的猫也一样沉默", "扩大群体共鸣"),
        ("cta", angles[-1] if angles else "猫猫先把今天过完", "轻 CTA 和情绪落点"),
    ]

    while len(normalized) < target and inserts:
        role, caption, intent = inserts.pop(0)
        insert_at = max(1, len(normalized) - 1)
        normalized.insert(insert_at, (role, clean_caption(caption), intent))
    return normalized[:target]


def select_beats_for_mode(beats: list[Any], mode: str, theme: str, text_context: dict[str, Any] | None = None) -> list[tuple[str, str, str]]:
    normalized = expand_beats_for_duration(beats, mode, theme, text_context)
    target = {"short": 4, "medium": 6, "minute": 8}[normalize_duration_mode(mode)]
    if len(normalized) > target:
        return select_representative_beats(normalized, target)
    return normalized


def select_representative_beats(beats: list[tuple[str, str, str]], target: int) -> list[tuple[str, str, str]]:
    if len(beats) <= target:
        return beats
    if target <= 4:
        return select_short_arc(beats, target)
    priority = [
        {"hook", "opening", "start"},
        {"setup"},
        {"pressure", "proof", "escalation"},
        {"twist"},
        {"echo"},
        {"punchline", "ending", "cta"},
    ]
    selected: list[tuple[str, str, str]] = []
    used: set[int] = set()

    def add_index(index: int) -> None:
        if 0 <= index < len(beats) and index not in used and len(selected) < target:
            selected.append(beats[index])
            used.add(index)

    for roles in priority:
        if len(selected) >= target:
            break
        if roles & {"punchline", "ending", "cta"} and target <= 4:
            continue
        for index, beat in enumerate(beats):
            if beat[0] in roles:
                add_index(index)
                break

    if target <= 4:
        add_index(next((idx for idx, beat in reversed(list(enumerate(beats))) if beat[0] in {"punchline", "ending", "cta"}), len(beats) - 1))
    while len(selected) < target:
        gap_index = round((len(beats) - 1) * len(selected) / max(1, target - 1))
        add_index(gap_index)
        if len(selected) < target and all(index in used for index in range(len(beats))):
            break
        for index in range(len(beats)):
            if len(selected) >= target:
                break
            add_index(index)
    selected.sort(key=lambda beat: beats.index(beat))
    return selected[:target]


def select_short_arc(beats: list[tuple[str, str, str]], target: int) -> list[tuple[str, str, str]]:
    if target == 4 and any(beat[0] == "twist" for beat in beats):
        arc_roles = [
            {"hook", "opening", "start"},
            {"setup", "pressure"},
            {"twist"},
            {"punchline", "ending", "cta"},
        ]
    else:
        arc_roles = [
            {"hook", "opening", "start"},
            {"setup"},
            {"pressure", "proof", "escalation", "twist"},
            {"punchline", "ending", "cta"},
        ]
    selected: list[tuple[str, str, str]] = []
    used: set[int] = set()
    for roles in arc_roles[:target]:
        for index, beat in enumerate(beats):
            if index not in used and beat[0] in roles:
                selected.append(beat)
                used.add(index)
                break
    if len(selected) < target:
        for index in [0, 1, len(beats) - 2, len(beats) - 1]:
            if 0 <= index < len(beats) and index not in used:
                selected.append(beats[index])
                used.add(index)
            if len(selected) >= target:
                break
    selected.sort(key=lambda beat: beats.index(beat))
    return selected[:target]


def sharpen_caption(text: str) -> str:
    if "岗位" in text:
        return "岗位像在招超人"
    if "会议" in text or "会" in text:
        return "会议把一天吃完"
    if "简历" in text:
        return "简历进了黑洞"
    return text


def warm_caption(text: str) -> str:
    if "猫" in text:
        return "猫猫先活过今天"
    return "明天也许会好一点"


def casual_caption(text: str) -> str:
    return text.replace("数据/细节", "").replace("现实", "今天")


def clean_caption(text: str) -> str:
    text = text.strip().replace("本以为", "以为").replace("结果先奔向银行算贷款", "先摊开账单")
    text = text.replace("银行：你俩流水加起来才8k?", "账单：先别急着办席")
    text = text.replace("合着我毕业就得年薪百万呗", "刚毕业就要满级配置")
    if len(text) > 18:
        return text[:17] + "…"
    return text


def first_text(items: list[Any]) -> str:
    return str(items[0]) if items else ""


def better_punchline_for_theme(theme: str) -> str:
    if any(word in theme for word in ("烤肠", "香肠", "摆摊", "小吃摊", "夜市", "地摊")):
        return "猫改卖情绪价值"
    if any(word in theme for word in ("租房", "房租", "押金", "合租", "通勤", "中介")):
        return "猫先把预算摊开谈"
    if any(word in theme for word in ("考研", "考公", "上岸", "考试")):
        return "猫决定先选一条路"
    if any(word in theme for word in ("上班", "加班", "会议", "内卷", "KPI")):
        return "猫把在吗设成免打扰"
    if any(word in theme for word in ("结婚", "彩礼", "买房", "房")):
        return "猫先学会好好谈条件"
    if any(word in theme for word in ("工作", "岗位", "简历", "面试", "就业")):
        return "猫先把规则看明白"
    return "猫先把今天过明白"


def selection_offset(beat: dict[str, Any]) -> int:
    text = f"{beat.get('id', '')}|{beat.get('caption', '')}|{beat.get('role', '')}"
    return sum(ord(char) for char in text)


def multi_agent_fallback_plan(theme: str, source_structure: VideoStructure | None, index: dict[str, Any], text_context: dict[str, Any]) -> MaoMemePlan:
    """Deterministic multi-agent fallback.

    It mirrors the intended Doubao workflow:
    1. screenwriter proposes several meme scripts
    2. director turns the best script into beats
    3. casting director matches each beat to local cat/background assets
    4. validator rejects weak matches and records gap strategies
    """
    viral_refs = viral_references_for_theme(theme, text_context)
    migration_blueprint = build_migration_blueprint(theme, viral_refs, {}, text_context)
    candidates = screenwriter_agent(theme, text_context, viral_refs, {}, migration_blueprint)
    scored = [(score_script(candidate, theme, index), candidate) for candidate in candidates]
    scored.sort(key=lambda item: item[0], reverse=True)
    script = scored[0][1]
    beats = director_agent(script, theme)
    selected_candidate = script_to_candidate(script, theme, scored[0][0], text_context, 1)
    attach_generation_context_to_candidate(selected_candidate, None, viral_refs, migration_blueprint)
    apply_migration_blueprint_to_beats(beats, migration_blueprint)
    apply_viral_patterns_to_beats(beats, selected_candidate, viral_refs)
    timeline, notes = casting_and_validator_agents(beats, theme, index)
    timeline, assemble_notes = assemble_timeline_locally(timeline)
    notes.extend(assemble_notes)
    return MaoMemePlan(
        id=f"maomeme-{int(time.time())}",
        theme=theme,
        source_structure=_source_payload(source_structure),
        script=[
            {"type": beat["role"], "text": beat["caption"], "purpose": beat["intent"], "duration": round(beat["end"] - beat["start"], 2)}
            for beat in beats
        ],
        timeline=timeline,
        material_needs=material_needs_from_timeline(timeline),
        agent_notes=[
            f"编剧 agent 生成 {len(candidates)} 个候选剧本，选择素材贴合度最高的一版。",
            "导演 agent 将剧本压成 4 个猫 meme 节奏点：hook/setup/escalation/punchline。",
            "素材导演 agent 按情绪动作和场景关键词匹配猫动画与背景。",
            *viral_reference_notes(viral_refs),
            migration_blueprint_note(migration_blueprint),
            *context_notes(text_context),
            *notes,
        ],
    )


def fallback_plan(theme: str, source_structure: VideoStructure | None, index: dict[str, Any]) -> MaoMemePlan:
    office_like = _theme_has(theme, ["上班", "打工", "会议", "电脑", "老板"])
    school_like = _theme_has(theme, ["学校", "教室", "考试", "作业", "同学"])
    car_like = _theme_has(theme, ["开车", "路上", "车", "堵车"])
    background_scenes = ["office", "real_office"] if office_like else ["school", "classroom"] if school_like else ["real_car_interior"] if car_like else ["city", "street", "window"]

    slots = [
        {
            "id": "hook",
            "start": 0.0,
            "end": 2.0,
            "role": "hook",
            "intent": "用强表情停住观众",
            "caption": "先别划走",
            "motion": ref(pick_motion(index, ["震惊", "瞪圆", "错愕", "探头"], "15")),
            "background": ref(pick_background(index, background_scenes)),
            "gap": {"status": "matched", "strategy": "direct_match", "reason": "强表情猫可以直接承担 hook。"},
            "packaging": ["large_caption", "quick_cut"],
            "source_pattern": "爆款开头：2秒内强情绪",
        },
        {
            "id": "setup",
            "start": 2.0,
            "end": 5.0,
            "role": "setup",
            "intent": "把主题落到具体场景",
            "caption": _setup_copy(theme),
            "motion": ref(pick_motion(index, ["电脑", "开车", "冷漠", "碎碎念"], "1")),
            "background": ref(pick_background(index, background_scenes)),
            "gap": {"status": "matched", "strategy": "direct_match", "reason": "背景图能承接主题场景，猫动作表达状态。"},
            "packaging": ["bottom_subtitle", "quick_cut"],
            "source_pattern": "爆款中段：场景化冲突",
        },
        {
            "id": "escalation",
            "start": 5.0,
            "end": 8.5,
            "role": "escalation",
            "intent": "放大情绪制造笑点",
            "caption": "事情开始不对劲",
            "motion": ref(pick_motion(index, ["哭", "委屈", "嚎啕", "疯狂"], "9")),
            "background": ref(pick_background(index, background_scenes)),
            "gap": {"status": "supplemented", "strategy": "reuse_crop_zoom", "reason": "缺少具体剧情镜头时，用哭哭猫和放大复用补足情绪。"},
            "packaging": ["large_caption", "zoom", "quick_cut"],
            "source_pattern": "爆款高潮：重复/放大/情绪升级",
        },
        {
            "id": "punchline",
            "start": 8.5,
            "end": 12.0,
            "role": "punchline",
            "intent": "反转收束形成记忆点",
            "caption": "结果猫赢了",
            "motion": ref(pick_motion(index, ["蹦跳", "欢快", "跳舞", "摇摆"], "13")),
            "background": ref(pick_background(index, ["window", "city", *background_scenes])),
            "gap": {"status": "supplemented", "strategy": "subtitle_card", "reason": "没有对话角色时，用字幕卡完成反转。"},
            "packaging": ["freeze_end", "title_bar"],
            "source_pattern": "爆款结尾：反转/梗收束",
        },
    ]
    return MaoMemePlan(
        id=f"maomeme-{int(time.time())}",
        theme=theme,
        source_structure=_source_payload(source_structure),
        script=[
            {"type": "hook", "text": "先别划走", "purpose": "强情绪停留", "duration": 2.0},
            {"type": "setup", "text": _setup_copy(theme), "purpose": "建立冲突", "duration": 3.0},
            {"type": "escalation", "text": "事情开始不对劲", "purpose": "情绪升级", "duration": 3.5},
            {"type": "punchline", "text": "结果猫赢了", "purpose": "反转收束", "duration": 3.5},
        ],
        timeline=slots,
        material_needs={
            "covered": ["强表情 hook", "主题背景", "情绪升级猫", "收束动作猫"],
            "missing": ["真实对话角色", "精细音效/BGM 卡点"],
            "supplement_strategy": ["字幕卡补对话", "裁切/放大复用猫动画", "后续接 HyperFrames 字幕包装"],
        },
        agent_notes=[
            "当前 fallback 计划会优先跑通 P0 闭环。",
            "配置 ARK_API_KEY 后，豆包会根据样例视频结构和素材库生成更贴近爆款的分镜。",
        ],
    )


def screenwriter_agent(
    theme: str,
    text_context: dict[str, Any] | None = None,
    viral_refs: list[dict[str, Any]] | None = None,
    migration: dict[str, Any] | None = None,
    migration_blueprint: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    theme_short = _setup_copy(theme)
    uploaded_scripts = uploaded_viral_script_seed(theme, migration or {})
    blueprint_scripts = blueprint_script_seeds(theme, migration_blueprint or {}, text_context or {})
    viral_scripts = blueprint_scripts or [viral_template_seed(ref, theme) for ref in (viral_refs or [])[:1]]
    if text_context:
        contextual = contextual_scripts(theme, text_context)
        if contextual:
            return [*uploaded_scripts, *viral_scripts, *contextual][:3] if (uploaded_scripts or viral_scripts) else contextual
    office = _theme_has(theme, ["上班", "打工", "会议", "电脑", "老板", "加班"])
    school = _theme_has(theme, ["学校", "教室", "考试", "作业", "同学"])
    car = _theme_has(theme, ["开车", "堵车", "车里", "路上"])
    if office:
        return [*uploaded_scripts, *viral_scripts, *[
            {
                "name": "会议排满版",
                "beats": [
                    ("hook", "打开日历那一秒", "震惊停顿"),
                    ("setup", "9点周会 10点复盘", "打工冲突"),
                    ("escalation", "下午晚上还在会", "崩溃放大"),
                    ("punchline", "猫把在吗设成免打扰", "边界感收束"),
                ],
                "scene": ["office", "real_office"],
                "theme_keywords": ["会议", "周会", "复盘", "加班", "电脑", "老板"],
                "emotion": ["震惊", "电脑", "哭", "蹦跳"],
            },
            {
                "name": "老板消失版",
                "beats": [
                    ("hook", "老板说简单聊两句", "危险预告"),
                    ("setup", "猫默默打开电脑", "冷漠铺垫"),
                    ("escalation", "会议越开越玄学", "委屈升级"),
                    ("punchline", "猫只回收到明天看", "荒诞但合理收束"),
                ],
                "scene": ["office", "real_office"],
                "theme_keywords": ["老板", "会议", "电脑", "下班"],
                "emotion": ["冷漠", "电脑", "委屈", "可爱"],
            },
            {
                "name": "打工人灵魂出走版",
                "beats": [
                    ("hook", "周一的灵魂先走了", "共鸣 hook"),
                    ("setup", theme_short, "主题落点"),
                    ("escalation", "身体还在会议里", "荒诞升级"),
                    ("punchline", "下班铃一响复活", "快乐收束"),
                ],
                "scene": ["office", "window"],
                "theme_keywords": ["周一", "打工", "会议", "下班"],
                "emotion": ["探头", "冷漠", "哭", "跳舞"],
            },
        ]][:3]
    if school:
        return [*uploaded_scripts, *viral_scripts, *[
            {
                "name": "作业突袭版",
                "beats": [
                    ("hook", "老师突然收作业", "震惊 hook"),
                    ("setup", "猫翻遍书包", "场景冲突"),
                    ("escalation", "发现写在梦里", "崩溃升级"),
                    ("punchline", "同桌猫递来救命纸", "反转"),
                ],
                "scene": ["classroom", "school", "real_school"],
                "theme_keywords": ["老师", "作业", "教室", "同桌"],
                "emotion": ["震惊", "探头", "哭", "蹦跳"],
            }
        ]][:3]
    if car:
        return [*uploaded_scripts, *viral_scripts, *[
            {
                "name": "堵车路怒版",
                "beats": [
                    ("hook", "导航说还有五分钟", "悬念 hook"),
                    ("setup", "猫冷漠握方向盘", "场景冲突"),
                    ("escalation", "五分钟后还是五分钟", "重复笑点"),
                    ("punchline", "猫选择原地开演唱会", "反转"),
                ],
                "scene": ["real_car_interior", "street", "city"],
                "theme_keywords": ["导航", "开车", "堵车", "方向盘"],
                "emotion": ["震惊", "开车", "疯狂", "演奏"],
            }
        ]][:3]
    fallback = [
        {
            "name": "万能反差版",
            "beats": [
                ("hook", "事情突然不对劲", "强 hook"),
                ("setup", theme_short, "建立场景"),
                ("escalation", "猫的表情逐渐失控", "情绪升级"),
                ("punchline", better_punchline_for_theme(theme), "荒诞但合理收束"),
            ],
            "scene": ["city", "street", "window"],
            "theme_keywords": [],
            "emotion": ["震惊", "冷漠", "哭", "跳舞"],
        }
    ]
    return [*uploaded_scripts, *viral_scripts, *fallback][:3]


def uploaded_viral_script_seed(theme: str, migration: dict[str, Any]) -> list[dict[str, Any]]:
    viral = migration.get("viral_analysis") if isinstance(migration.get("viral_analysis"), dict) else {}
    summary = viral.get("summary") if isinstance(viral.get("summary"), dict) else {}
    slots = viral.get("transfer_slots") if isinstance(viral.get("transfer_slots"), list) else []
    brief = migration.get("creative_brief") if isinstance(migration.get("creative_brief"), dict) else {}
    if not summary and not slots:
        return []
    beats = []
    fallback_roles = ["hook", "setup", "pressure", "twist", "punchline", "cta"]
    for index, slot in enumerate(slots[:6]):
        if not isinstance(slot, dict):
            continue
        role = normalize_uploaded_role(str(slot.get("slot") or fallback_roles[min(index, len(fallback_roles) - 1)]))
        source_script = str(slot.get("background") or slot.get("cat") or slot.get("rewrite") or "")
        caption = uploaded_seed_caption(theme, role, source_script, brief)
        intent = f"迁移上传爆款的{slot.get('keep', '节奏和笑点')}，按新主题改写。"
        beats.append((role, caption, intent))
    if len(beats) < 4:
        beats = [
            ("hook", f"{theme[:12]}先给猫整懵", "用上传爆款的停顿方式做开场"),
            ("setup", brief.get("core_conflict") or _setup_copy(theme), "建立新主题现实冲突"),
            ("pressure", "规则一层套一层", "迁移原视频情绪升级"),
            ("punchline", ending_caption_for_brief(brief), "保留爆款反转节奏但换成新主题收束"),
        ]
    return [
        {
            "name": f"上传爆款迁移版：{summary.get('title') or '结构复刻'}",
            "social_topic": summary.get("one_sentence") or theme,
            "tension": brief.get("core_conflict") or "把爆款结构迁移到新主题，但台词和场景全部重写",
            "beats": beats[:6],
            "scene": [slot.get("background", "") for slot in slots[:4] if isinstance(slot, dict) and slot.get("background")],
            "theme_keywords": [theme, brief.get("target_audience", ""), brief.get("protagonist", ""), brief.get("required_props", "")],
            "emotion": [slot.get("cat", "") for slot in slots[:4] if isinstance(slot, dict) and slot.get("cat")],
            "viral_reference_title": summary.get("title", ""),
            "uploaded_viral_reference": True,
        }
    ]


def blueprint_script_seeds(theme: str, blueprint: dict[str, Any], text_context: dict[str, Any]) -> list[dict[str, Any]]:
    shots = blueprint.get("shots") if isinstance(blueprint.get("shots"), list) else []
    primary = blueprint.get("primary_reference") if isinstance(blueprint.get("primary_reference"), dict) else {}
    if not shots or not primary:
        return []
    base = blueprint_beats_for_theme(theme, shots, text_context)
    if len(base) < 4:
        return []
    category = infer_theme_category(theme)
    scenes = blueprint_scenes(theme, shots, category)
    emotions = list(dict.fromkeys(str(shot.get("cat_action_requirement", "")) for shot in shots if str(shot.get("cat_action_requirement", "")).strip()))
    keywords = list(dict.fromkeys([*tokenize_theme_local(theme), *[str(tag) for tag in blueprint.get("structure_tags", [])[:5]]]))
    source = {
        "viral_reference_id": primary.get("id", ""),
        "viral_reference_title": primary.get("title", ""),
        "source_blueprint": blueprint,
    }
    return [
        {
            "name": f"爆款迁移·{primary.get('title') or '结构'}",
            "beats": base,
            "scene": scenes,
            "theme_keywords": keywords,
            "emotion": emotions,
            "social_topic": text_context.get("title") or theme,
            "tension": primary.get("topic") or "把爆款结构迁移到新主题，台词全部重写",
            **source,
        },
        {
            "name": f"现实共鸣迁移·{primary.get('title') or '结构'}",
            "beats": soften_or_sharpen_blueprint_beats(base, "realistic", theme),
            "scene": scenes,
            "theme_keywords": keywords,
            "emotion": emotions,
            "social_topic": text_context.get("title") or theme,
            "tension": "更强调真实生活动作和社会角色冲突",
            **source,
        },
        {
            "name": f"黑色幽默迁移·{primary.get('title') or '结构'}",
            "beats": soften_or_sharpen_blueprint_beats(base, "absurd", theme),
            "scene": scenes,
            "theme_keywords": keywords,
            "emotion": emotions,
            "social_topic": text_context.get("title") or theme,
            "tension": "更荒诞，但仍保留合理转场和现实成本",
            **source,
        },
    ]


def blueprint_beats_for_theme(theme: str, shots: list[dict[str, Any]], text_context: dict[str, Any]) -> list[tuple[str, str, str]]:
    roles = [str(shot.get("slot") or "setup") for shot in shots]
    if any(word in theme for word in ("请假", "不批准", "不批假", "120", "急救")):
        captions = {
            "hook": "请假申请先被驳回",
            "setup": "老板说今天必须到岗",
            "pressure": "体温表都快冒烟了",
            "proof": "工作群还在继续派活",
            "twist": "120比批假先接通",
            "echo": "同事也开始看急诊",
            "punchline": "老板终于学会人话",
            "cta": "先保命再谈敬业",
        }
    elif any(word in theme for word in ("周一", "星期一", "上班综合症", "不想上班", "闹钟")):
        captions = {
            "hook": "闹钟响得像开会",
            "setup": "人坐起灵魂没到岗",
            "pressure": "工作群已经在同步",
            "proof": "地铁口全是低电量",
            "twist": "咖啡也重启失败",
            "echo": "同事头像都灰了",
            "punchline": "今天先低电量运行",
            "cta": "下班再恢复出厂",
        }
    elif any(word in theme for word in ("找工作", "工作难找", "求职", "岗位", "薪资", "招聘", "摆摊", "烤肠")):
        captions = {
            "hook": "刷到薪资还行的岗位",
            "setup": "点开要求三年经验",
            "pressure": "应届生还要带团队",
            "proof": "同学简历也进黑洞",
            "twist": "他转身研究烤肠摊",
            "echo": "校门口摊位也在卷",
            "punchline": "摊位写熟练工优先",
            "cta": "先卖一根热乎的",
        }
    elif any(word in theme for word in ("考研", "考公", "上岸", "考试")):
        captions = {
            "hook": "毕业前弹出三条路",
            "setup": "考研考公都在招手",
            "pressure": "每条路都排长队",
            "proof": "自习室一排沉默",
            "twist": "选择也要先复习",
            "echo": "同学也在原地加载",
            "punchline": "今天先选一页",
            "cta": "别把自己淹了",
        }
    else:
        seed = text_context.get("beat_seed") if isinstance(text_context.get("beat_seed"), dict) else {}
        captions = {
            "hook": str(seed.get("hook") or "现实先弹出难题"),
            "setup": str(seed.get("setup") or _setup_copy(theme)),
            "pressure": str(seed.get("escalation") or "要求开始变离谱"),
            "proof": "旁边的人也沉默了",
            "twist": "他发现规则还会变",
            "echo": "大家都在同一关卡",
            "punchline": str(seed.get("punchline") or human_punchline_for_theme(theme)),
            "cta": "先把今天过明白",
        }
    beats: list[tuple[str, str, str]] = []
    for index, role in enumerate(roles):
        caption = humanize_caption(captions.get(role, captions.get("setup", _setup_copy(theme))), theme)
        source = shots[min(index, len(shots) - 1)]
        purpose = str(source.get("transfer_role") or source.get("rewrite_direction") or f"迁移爆款 {role} 镜头功能")
        beats.append((role, caption, purpose))
    return beats


def soften_or_sharpen_blueprint_beats(beats: list[tuple[str, str, str]], style: str, theme: str) -> list[tuple[str, str, str]]:
    next_beats = list(beats)
    if not next_beats:
        return next_beats
    if style == "realistic":
        next_beats = [(role, humanize_caption(caption, theme), f"{intent}，增加生活细节") for role, caption, intent in next_beats]
        for index, (role, caption, intent) in enumerate(next_beats):
            if role in {"pressure", "proof"}:
                next_beats[index] = (role, realistic_caption_variant(caption, theme), intent)
                break
    elif style == "absurd":
        next_beats = [(role, absurd_caption_variant(caption, role, theme), f"{intent}，增强黑色幽默") for role, caption, intent in next_beats]
    return next_beats


def realistic_caption_variant(caption: str, theme: str) -> str:
    if any(word in theme for word in ("找工作", "求职", "岗位", "招聘")):
        return "投完简历没有回音"
    if any(word in theme for word in ("请假", "120", "老板")):
        return "病假理由又写一遍"
    if any(word in theme for word in ("周一", "上班")):
        return "早会链接先醒了"
    return caption


def absurd_caption_variant(caption: str, role: str, theme: str) -> str:
    if role == "twist" and any(word in theme for word in ("找工作", "求职", "烤肠", "摆摊")):
        return "烤肠摊也要全链路"
    if role == "twist" and any(word in theme for word in ("请假", "120")):
        return "急救电话替他请假"
    if role == "punchline" and any(word in theme for word in ("周一", "上班")):
        return "先上班再做人"
    return caption


def blueprint_scenes(theme: str, shots: list[dict[str, Any]], category: str) -> list[str]:
    scenes = [str(shot.get("background_requirement", "")) for shot in shots if str(shot.get("background_requirement", "")).strip()]
    if category == "career":
        scenes.extend(["招聘软件", "面试等待区", "real_office", "office"])
        if is_food_scene(theme):
            scenes.extend(["street_food_stall", "烤肠摊", "夜市摊位"])
    elif category == "office":
        scenes.extend(["real_office", "meeting_room", "工位", "工作群"])
    elif category == "street_food":
        scenes.extend(["street_food_stall", "烤肠摊", "夜市摊位"])
    elif category == "exam":
        scenes.extend(["自习室", "图书馆", "classroom", "real_school"])
    return list(dict.fromkeys(scenes))[:10]


def tokenize_theme_local(theme: str) -> list[str]:
    words = [
        "工作", "简历", "岗位", "面试", "就业", "求职", "招聘", "HR", "应届生",
        "上班", "老板", "会议", "加班", "周一", "请假", "120",
        "烤肠", "摆摊", "夜市", "小吃摊", "考研", "考公", "租房", "房租",
    ]
    return [word for word in words if word in theme]


def humanize_caption(text: str, theme: str = "") -> str:
    value = str(text or "")
    replacements = {
        "猫猫": "他",
        "猫先": "先",
        "猫把": "他把",
        "猫发现": "他发现",
        "猫决定": "他决定",
        "猫改": "他改",
        "猫去": "他去",
        "猫转身": "他转身",
        "旁边的猫": "旁边同学",
        "同学猫": "同学",
        "室友猫": "室友",
    }
    for source, target in replacements.items():
        value = value.replace(source, target)
    if value.startswith("猫"):
        value = "他" + value[1:]
    if "解决社会问题" in value:
        value = human_punchline_for_theme(theme)
    return value


def human_punchline_for_theme(theme: str) -> str:
    if any(word in theme for word in ("烤肠", "摆摊", "小吃摊", "夜市")):
        return "摊位也写熟练工优先"
    if any(word in theme for word in ("请假", "老板", "120")):
        return "老板终于学会人话"
    if any(word in theme for word in ("周一", "上班", "会议")):
        return "今天先低电量运行"
    if any(word in theme for word in ("工作", "岗位", "简历", "求职")):
        return "先把岗位黑话看懂"
    if any(word in theme for word in ("考研", "考公", "考试")):
        return "今天先选一页"
    return "先把今天过明白"


def normalize_uploaded_role(value: str) -> str:
    text = value.lower()
    if any(word in text for word in ("hook", "开头")):
        return "hook"
    if any(word in text for word in ("pressure", "冲突", "escalation", "升级")):
        return "pressure"
    if any(word in text for word in ("twist", "反转")):
        return "twist"
    if any(word in text for word in ("punchline", "ending", "结尾")):
        return "punchline"
    if any(word in text for word in ("echo", "共鸣")):
        return "echo"
    return "setup"


def uploaded_seed_caption(theme: str, role: str, source_script: str, brief: dict[str, Any]) -> str:
    conflict = str(brief.get("core_conflict") or "").strip()
    protagonist = str(brief.get("protagonist") or "猫").strip()
    if role == "hook":
        return f"{protagonist}刷到现实暴击"[:18]
    if role in {"setup", "pressure"}:
        return (conflict or _setup_copy(theme))[:18]
    if role == "twist":
        return "猫发现规则还会变"[:18]
    if role == "punchline":
        return ending_caption_for_brief(brief)
    return (source_script or theme)[:18]


def ending_caption_for_brief(brief: dict[str, Any]) -> str:
    tone = str(brief.get("ending_tone") or "")
    if "温暖" in tone:
        return "先抱团喘口气"
    if "讽刺" in tone:
        return "猫学会看规则"
    if "荒诞" in tone:
        return "猫把规则贴墙上"
    return "先活过这一集"


def contextual_scripts(theme: str, topic: dict[str, Any]) -> list[dict[str, Any]]:
    specific = specific_contextual_scripts(theme)
    if specific:
        return specific

    beat_seed = topic.get("beat_seed") or {}
    assets = topic.get("preferred_assets") or {}
    title = topic.get("title", "")
    tensions = topic.get("tensions", [])
    angles = topic.get("meme_angles", [])
    facts = topic.get("facts", [])
    scene = assets.get("backgrounds") or ["office", "real_office"]
    emotions = assets.get("motions") or ["震惊", "电脑", "哭", "蹦跳"]
    fact_line = facts[0] if facts else ""
    tension_line = tensions[0] if tensions else title
    angle_line = angles[0] if angles else beat_seed.get("punchline", "猫选择先装可爱")
    if any(word in theme for word in ("烤肠", "香肠", "摆摊", "小吃摊", "夜市", "地摊", "餐车")):
        scene = list(dict.fromkeys([*scene, "street_food_stall", "烤肠摊", "夜市摊位", "real_street"]))
        angle_line = "猫去校门口卖烤肠，发现摊位也在卷"
    return [
        {
            "name": f"{title}现实共鸣版",
            "beats": [
                ("hook", beat_seed.get("hook", "现实突然给猫一拳"), "现实矛盾开场"),
                ("setup", beat_seed.get("setup", tension_line), "具体处境铺垫"),
                ("escalation", beat_seed.get("escalation", fact_line or tension_line), "数据/细节放大压力"),
                ("punchline", angle_line if "烤肠" in angle_line else beat_seed.get("punchline", angle_line), "猫 meme 反差收束"),
            ],
            "scene": scene,
            "theme_keywords": list(topic.get("keywords", []))[:8],
            "emotion": emotions,
        },
        {
            "name": f"{title}黑色幽默版",
            "beats": [
                ("hook", angles[0] if angles else beat_seed.get("hook", "猫打开现实那一秒"), "强共鸣 hook"),
                ("setup", tensions[0] if tensions else beat_seed.get("setup", "问题开始变具体"), "矛盾落地"),
                ("escalation", tensions[1] if len(tensions) > 1 else beat_seed.get("escalation", "压力继续升级"), "荒诞升级"),
                ("punchline", angle_line if "烤肠" in angle_line else angles[-1] if angles else beat_seed.get("punchline", "猫决定换个姿势活下去"), "反差结尾"),
            ],
            "scene": scene,
            "theme_keywords": list(topic.get("keywords", []))[:8],
            "emotion": emotions,
        },
        {
            "name": f"{title}互助喘口气版",
            "beats": [
                ("hook", beat_seed.get("hook", "现实突然弹出难题"), "具体问题开场"),
                ("setup", tensions[0] if tensions else beat_seed.get("setup", "猫先照着规则试一次"), "把困境落到动作"),
                ("pressure", facts[0] if facts else "旁边的猫也遇到同一堵墙", "补现实证据"),
                ("twist", tensions[-1] if tensions else "猫发现不是自己太菜，是规则太绕", "识别结构问题"),
                ("echo", angles[0] if angles else "两只猫开始互相递攻略", "从个体扩到群体"),
                ("punchline", better_punchline_for_theme(title or ""), "荒诞但合理收束"),
            ],
            "scene": scene,
            "theme_keywords": list(topic.get("keywords", []))[:8],
            "emotion": emotions,
        },
    ]


def specific_contextual_scripts(theme: str) -> list[dict[str, Any]]:
    if any(word in theme for word in ("请假", "不批准", "不批假", "120", "急救", "救护车")):
        scene = ["office", "real_office", "meeting_room", "work_chat", "hospital_alert"]
        return [
            {
                "name": "00后请假审批反转版",
                "beats": [
                    ("hook", "请假申请先被驳回", "请假冲突开场"),
                    ("setup", "老板说今天必须到岗", "职场规则具体化"),
                    ("pressure", "猫把病假理由又写一遍", "身体报警被忽视"),
                    ("twist", "猫到工位直接拨120", "荒诞但合理反转"),
                    ("punchline", "老板开始学审批人话", "讽刺老板被现实吓住"),
                ],
                "scene": scene,
                "theme_keywords": ["请假", "老板", "不批准", "120", "急救", "00后"],
                "emotion": ["震惊", "委屈", "碎碎念", "冷漠", "跳舞"],
            },
            {
                "name": "老板不批假急救弹窗版",
                "beats": [
                    ("hook", "猫发烧还在等审批", "身体和制度冲突"),
                    ("setup", "老板回复先坚持一下", "典型职场话术"),
                    ("pressure", "工作群继续弹任务", "压力叠加"),
                    ("twist", "120电话比批假先接通", "反套路转折"),
                    ("punchline", "老板把不批准撤回", "荒诞收束"),
                ],
                "scene": scene,
                "theme_keywords": ["请假", "病假", "工作群", "120", "撤回"],
                "emotion": ["委屈", "冷漠", "电脑", "震惊", "可爱"],
            },
            {
                "name": "00后整顿请假话术版",
                "beats": [
                    ("hook", "请假单写得很客气", "低姿态开场"),
                    ("setup", "老板只看见不批准", "冲突落地"),
                    ("pressure", "猫把体温贴到屏幕上", "身体证据具象化"),
                    ("twist", "急救弹窗替猫说话", "工具反转"),
                    ("punchline", "以后请假先保命", "现实提醒收束"),
                ],
                "scene": scene,
                "theme_keywords": ["00后", "请假", "体温", "急救", "老板"],
                "emotion": ["探头", "委屈", "碎碎念", "震惊", "可爱"],
            },
        ]
    if any(word in theme for word in ("周一", "星期一", "上班综合症", "不想上班", "起床", "闹钟")):
        scene = ["bedroom", "real_office", "commute", "work_chat", "meeting_room"]
        return [
            {
                "name": "周一上班综合症闹钟版",
                "beats": [
                    ("hook", "闹钟响得像开会", "周一身体抗拒开场"),
                    ("setup", "猫坐起来又倒回去", "起床失败具体化"),
                    ("pressure", "工作群已经开始同步", "上班压力提前到达"),
                    ("twist", "猫发现今天才周一", "情绪反转"),
                    ("punchline", "先把灵魂放进工位", "黑色幽默收束"),
                ],
                "scene": scene,
                "theme_keywords": ["周一", "上班综合症", "闹钟", "工作群", "会议"],
                "emotion": ["冷漠", "委屈", "震惊", "电脑", "可爱"],
            },
            {
                "name": "周一灵魂未到岗版",
                "beats": [
                    ("hook", "身体到了地铁口", "通勤开场"),
                    ("setup", "灵魂还在被窝请假", "反差设定"),
                    ("pressure", "老板发来早会链接", "工作压迫具象化"),
                    ("twist", "猫把咖啡当重启键", "荒诞转折"),
                    ("punchline", "今天先低电量运行", "合理收束"),
                ],
                "scene": scene,
                "theme_keywords": ["周一", "通勤", "早会", "咖啡", "低电量"],
                "emotion": ["冷漠", "开车", "电脑", "震惊", "跳舞"],
            },
            {
                "name": "周一会议预告片版",
                "beats": [
                    ("hook", "睁眼先看到会议提醒", "信息暴击"),
                    ("setup", "猫还没洗脸就要同步", "荒诞职场细节"),
                    ("pressure", "待办事项排到床边", "压力可视化"),
                    ("twist", "猫给自己发已读不回", "自嘲转折"),
                    ("punchline", "先上班再做人", "短促梗收束"),
                ],
                "scene": scene,
                "theme_keywords": ["周一", "会议", "同步", "待办", "已读不回"],
                "emotion": ["震惊", "电脑", "委屈", "冷漠", "可爱"],
            },
        ]
    if any(word in theme for word in ("找工作", "工作难找", "求职", "岗位", "薪资", "招聘", "摆摊", "烤肠")):
        if any(word in theme for word in ("烤肠", "摆摊", "小吃摊", "夜市", "地摊", "餐车")):
            scene = ["job_app", "招聘", "面试", "street_food_stall", "烤肠摊", "夜市摊位"]
            return [
                {
                    "name": "离谱岗位转烤肠摊版",
                    "beats": [
                        ("hook", "刷到薪资还行的岗位", "手机招聘 hook"),
                        ("setup", "点开要求写三年经验", "离谱门槛具体化"),
                        ("pressure", "应届生还要会带团队", "荒诞升级"),
                        ("twist", "猫转身研究烤肠摊", "脑洞但合理转场"),
                        ("punchline", "摊位也写熟练工优先", "不把摆摊当万能解法"),
                    ],
                    "scene": scene,
                    "theme_keywords": ["求职", "岗位", "薪资", "应届生", "烤肠", "摆摊"],
                    "emotion": ["震惊", "电脑", "碎碎念", "探头", "哭"],
                },
                {
                    "name": "校门口烤肠再就业版",
                    "beats": [
                        ("hook", "简历投进黑洞", "求职挫败开场"),
                        ("setup", "岗位要求比论文还长", "门槛具象化"),
                        ("pressure", "面试题做到天黑", "时间成本"),
                        ("twist", "猫推着小摊到校门口", "场景迁移"),
                        ("punchline", "烤肠摊也开始考核KPI", "社会现实反讽"),
                    ],
                    "scene": scene,
                    "theme_keywords": ["简历", "黑洞", "面试题", "校门口", "烤肠摊", "KPI"],
                    "emotion": ["委屈", "电脑", "哭", "跳舞", "震惊"],
                },
                {
                    "name": "HR和烤肠摊双猫对话版",
                    "beats": [
                        ("hook", "HR说欢迎应届生", "话术反差开场"),
                        ("setup", "下一行写三年经验", "招聘黑话"),
                        ("pressure", "猫把要求翻译成人话", "结构识别"),
                        ("twist", "烤肠摊主也要全链路", "转场反讽"),
                        ("punchline", "猫先卖热乎的情绪价值", "轻收束"),
                    ],
                    "scene": scene,
                    "theme_keywords": ["HR", "应届生", "三年经验", "全链路", "烤肠"],
                    "emotion": ["探头", "震惊", "碎碎念", "委屈", "可爱"],
                },
            ]
    if any(word in theme for word in ("烤肠", "香肠", "摆摊", "小吃摊", "夜市", "地摊", "餐车")):
        scene = ["street_food_stall", "烤肠摊", "夜市摊位", "小吃摊", "real_street"]
        return [
            {
                "name": "校门口小摊也内卷版",
                "beats": [
                    ("hook", "校门口烤肠开张", "市井场景开场"),
                    ("setup", "隔壁也挂买一送一", "摆摊内卷具体化"),
                    ("pressure", "摊位费先排队扣钱", "现实成本压力"),
                    ("twist", "顾客问能不能赊账", "荒诞反差"),
                    ("punchline", "猫改卖情绪价值", "不把摆摊当万能解法"),
                ],
                "scene": scene,
                "theme_keywords": ["烤肠", "摆摊", "小吃摊", "夜市", "摊位费", "内卷"],
                "emotion": ["震惊", "碎碎念", "委屈", "哭", "跳舞"],
            },
            {
                "name": "烤肠摊成本账本版",
                "beats": [
                    ("hook", "第一根烤肠还没卖", "开场反差"),
                    ("setup", "摊位费先来打招呼", "成本先到"),
                    ("pressure", "隔壁又降一块钱", "竞争升级"),
                    ("twist", "猫发现自己也在打工", "结构转折"),
                    ("punchline", "今晚先卖给同学猫", "温和收束"),
                ],
                "scene": scene,
                "theme_keywords": ["烤肠", "摆摊", "摊位费", "夜市"],
                "emotion": ["震惊", "电脑", "哭", "委屈", "可爱"],
            },
            {
                "name": "夜市双猫对话版",
                "beats": [
                    ("hook", "小摊灯刚亮起来", "场景 hook"),
                    ("setup", "左边烤肠右边冰粉", "具体摊位"),
                    ("pressure", "大家都写今日特价", "内卷升级"),
                    ("twist", "猫开始卖下班安慰", "反差梗"),
                    ("punchline", "同学买的不是烤肠", "情绪价值收束"),
                ],
                "scene": scene,
                "theme_keywords": ["烤肠", "冰粉", "夜市", "小吃摊"],
                "emotion": ["探头", "碎碎念", "委屈", "震惊", "跳舞"],
            },
        ]
    if any(word in theme for word in ("租房", "房租", "押金", "合租", "通勤", "中介", "隔断间")):
        scene = ["rental_room", "出租屋", "building_interior", "real_transit_station", "real_city"]
        return [
            {
                "name": "工资到账就被房租截胡版",
                "beats": [
                    ("hook", "工资刚到账", "生活账单开场"),
                    ("setup", "房租先扣走一半", "具体成本"),
                    ("pressure", "押金中介通勤排队", "压力叠加"),
                    ("twist", "猫发现省钱也要成本", "现实转折"),
                    ("punchline", "猫先把预算摊开谈", "温和收束"),
                ],
                "scene": scene,
                "theme_keywords": ["租房", "房租", "押金", "通勤", "工资", "账单"],
                "emotion": ["震惊", "电脑", "委屈", "哭", "可爱"],
            },
            {
                "name": "合租账单谈判版",
                "beats": [
                    ("hook", "账单比猫先到家", "反差开场"),
                    ("setup", "水电网费一起冒头", "账单具体化"),
                    ("pressure", "通勤每天吞掉两小时", "时间成本"),
                    ("twist", "室友也在算同一笔账", "群体共鸣"),
                    ("punchline", "两只猫先定公共预算", "现实解决一小步"),
                ],
                "scene": scene,
                "theme_keywords": ["合租", "账单", "通勤", "预算"],
                "emotion": ["探头", "冷漠", "委屈", "碎碎念", "可爱"],
            },
            {
                "name": "离公司远一点便宜版",
                "beats": [
                    ("hook", "便宜房源在地图边缘", "空间反差"),
                    ("setup", "房租少了通勤长了", "取舍具体化"),
                    ("pressure", "早八地铁先把猫压扁", "现实压力"),
                    ("twist", "省下的钱买了咖啡", "反差转折"),
                    ("punchline", "猫决定先睡够再说", "轻收束"),
                ],
                "scene": scene,
                "theme_keywords": ["租房", "通勤", "地铁", "房租"],
                "emotion": ["震惊", "开车", "哭", "冷漠", "跳舞"],
            },
        ]
    if any(word in theme for word in ("考研", "考公", "上岸", "考试")):
        scene = ["classroom", "real_school", "自习室", "图书馆", "school"]
        return [
            {
                "name": "三条路同时弹窗版",
                "beats": [
                    ("hook", "毕业前最后一个夜晚", "选择压力开场"),
                    ("setup", "考研考公都在招手", "三岔路具体化"),
                    ("pressure", "每条路都排长队", "现实拥挤"),
                    ("twist", "猫发现选择也要复习", "荒诞转折"),
                    ("punchline", "猫今天先选一页", "合理收束"),
                ],
                "scene": scene,
                "theme_keywords": ["考研", "考公", "就业", "上岸", "自习"],
                "emotion": ["震惊", "探头", "委屈", "哭", "可爱"],
            },
            {
                "name": "自习室沉默版",
                "beats": [
                    ("hook", "自习室一排都沉默", "群体共鸣 hook"),
                    ("setup", "左边刷题右边申论", "具体场景"),
                    ("pressure", "家族群发来上岸攻略", "外部压力"),
                    ("twist", "猫把三条路写成题", "结构转折"),
                    ("punchline", "先做能做的一小题", "温和收束"),
                ],
                "scene": scene,
                "theme_keywords": ["考研", "考公", "自习", "家族群", "上岸"],
                "emotion": ["冷漠", "电脑", "哭", "探头", "可爱"],
            },
            {
                "name": "上岸祝福压力版",
                "beats": [
                    ("hook", "大家都说祝你上岸", "社交压力"),
                    ("setup", "猫还没决定去哪条河", "反差铺垫"),
                    ("pressure", "资料堆到挡住猫脸", "具象焦虑"),
                    ("twist", "不是猫不努力", "识别结构问题"),
                    ("punchline", "先别把自己淹了", "情绪照顾"),
                ],
                "scene": scene,
                "theme_keywords": ["上岸", "考研", "考公", "资料", "焦虑"],
                "emotion": ["震惊", "委屈", "哭", "碎碎念", "可爱"],
            },
        ]
    return []


def context_notes(text_context: dict[str, Any]) -> list[str]:
    if not text_context:
        return []
    notes = [f"文本素材库命中主题：{text_context.get('title', text_context.get('id', 'unknown'))}。"]
    facts = text_context.get("facts") or []
    if facts:
        notes.append(f"剧本参考现实事实：{facts[0]}")
    return notes


def score_script(script: dict[str, Any], theme: str, index: dict[str, Any]) -> float:
    score = 0.0
    scene_hits = sum(len(rank_assets(index.get("backgrounds", []), [scene], limit=3)) for scene in script.get("scene", []))
    emotion_hits = sum(len(rank_assets(index.get("cat_motions", []), [emotion], limit=3)) for emotion in script.get("emotion", []))
    score += min(scene_hits, 6) * 1.2
    score += min(emotion_hits, 8) * 1.5
    for word in ("反转", "崩溃", "震惊", "可爱", "冷漠", "重复"):
        if word in json.dumps(script, ensure_ascii=False):
            score += 0.8
    if any(word in theme for word in ("打工", "会议", "加班")) and "office" in script.get("scene", []):
        score += 3.0
    for keyword in script.get("theme_keywords", []):
        if keyword in theme:
            score += 2.0
    if "最后居然被猫解决" in json.dumps(script, ensure_ascii=False):
        score -= 8.0
    return score


def director_agent(script: dict[str, Any], theme: str, duration_mode: str = "short") -> list[dict[str, Any]]:
    durations = durations_for_mode(duration_mode, len(script["beats"]))
    start = 0.0
    beats = []
    for index, ((role, caption, intent), duration) in enumerate(zip(script["beats"], durations)):
        end = round(start + duration, 2)
        beats.append(
            {
                "id": f"{index + 1:02d}-{role}",
                "start": start,
                "end": end,
                "role": role,
                "theme": theme,
                "intent": intent,
                "caption": polish_caption(role, caption, theme),
                "scene_keywords": scene_keywords_for_beat(theme, caption, role, script.get("scene", [])),
                "emotion_keywords": emotion_keywords_for_role(role, caption, script),
                "must_keywords": role_must_keywords(role, caption, theme),
                "forbidden_keywords": forbidden_keywords_for_context(script, theme),
                "layout": layout_for_role(role, caption),
                "dialogue": dialogue_for_beat(role, caption, theme, intent),
            }
        )
        start = end
    return beats


def apply_viral_patterns_to_beats(
    beats: list[dict[str, Any]],
    candidate: ScriptCandidate,
    viral_refs: list[dict[str, Any]],
) -> None:
    reference_id = str(candidate.asset_hints.get("viral_reference_id") or "")
    reference = next((item for item in viral_refs if str(item.get("id")) == reference_id), None)
    if reference is None and viral_refs:
        reference = viral_refs[0]
    if not reference:
        return
    storyboard = reference.get("storyboard", [])
    for index, beat in enumerate(beats):
        if beat.get("source_viral_shot"):
            continue
        source_shot = storyboard[min(index, len(storyboard) - 1)] if storyboard else {}
        if not isinstance(source_shot, dict):
            source_shot = {}
        beat["viral_reference"] = {
            "id": reference.get("id", ""),
            "title": reference.get("title", ""),
            "beat": source_shot.get("beat", ""),
            "joke_point": source_shot.get("joke_point", ""),
            "background": source_shot.get("background", ""),
            "cats": source_shot.get("cats", ""),
            "audio": source_shot.get("audio", ""),
        }
        cat_text = str(source_shot.get("cats", ""))
        if cat_text:
            beat["emotion_keywords"] = list(dict.fromkeys([*beat.get("emotion_keywords", []), cat_text]))
        background_text = str(source_shot.get("background", ""))
        if background_text and viral_background_fits_beat(background_text, beat):
            beat["scene_keywords"] = list(dict.fromkeys([*beat.get("scene_keywords", []), background_text]))


def layout_for_role(role: str, caption: str) -> str:
    if role in {"setup", "pressure", "twist", "punchline"}:
        return "dialogue"
    if any(word in caption for word in ("老板", "同学", "HR", "面试官", "公司", "老师")):
        return "dialogue"
    return "single"


def viral_background_fits_beat(background_text: str, beat: dict[str, Any]) -> bool:
    """Use viral background notes only when they agree with this shot's local scene."""
    text = f"{background_text} {beat.get('caption', '')} {beat.get('intent', '')}"
    scene_keywords = " ".join(str(item) for item in beat.get("scene_keywords", []))
    local_text = f"{beat.get('caption', '')} {beat.get('intent', '')} {scene_keywords}"
    category = infer_theme_category(local_text)
    if is_food_scene(background_text) and not is_food_scene(local_text):
        return False
    if is_food_scene(local_text):
        return is_food_scene(background_text) or not background_text.strip()
    if category == "career" and any(word in background_text for word in ("招聘", "面试", "办公室", "校招", "简历", "工位")):
        return True
    if category == "office" and any(word in background_text for word in ("办公室", "会议", "工位", "加班")):
        return True
    if category == "exam" and any(word in background_text for word in ("自习", "图书馆", "教室", "学校", "课桌")):
        return True
    if category == "rent" and any(word in background_text for word in ("出租屋", "房租", "账单", "通勤", "地铁")):
        return True
    return bool(set(tokenize_scene_text(background_text)) & set(tokenize_scene_text(text)))


def tokenize_scene_text(text: str) -> list[str]:
    keywords = [
        "招聘", "面试", "办公室", "校招", "简历", "岗位", "会议", "工位", "加班",
        "自习", "图书馆", "教室", "学校", "考研", "考公", "出租屋", "房租", "账单",
        "通勤", "地铁", "烤肠", "香肠", "摆摊", "小吃摊", "夜市", "摊车", "街边摊",
    ]
    return [keyword for keyword in keywords if keyword in text]


def is_food_scene(text: str) -> bool:
    return any(
        word in str(text)
        for word in (
            "烤肠",
            "香肠",
            "摆摊",
            "小吃摊",
            "夜市",
            "摊位",
            "摊车",
            "街边摊",
            "餐车",
            "地摊",
            "street_food",
            "food_stall",
            "stall",
        )
    )


def dialogue_for_beat(role: str, caption: str, theme: str, intent: str = "") -> list[dict[str, str]]:
    if layout_for_role(role, caption) != "dialogue":
        return []
    local_text = f"{caption} {intent}"
    text = f"{local_text} {theme}"
    category = infer_theme_category(local_text) or infer_theme_category(theme)
    if any(word in local_text for word in ("烤肠", "香肠", "摆摊", "小吃摊", "夜市", "摊位")):
        category = "street_food"
    elif any(word in local_text for word in ("工作", "简历", "岗位", "面试", "HR", "要求", "经验", "应届", "团队", "招聘", "薪资")):
        category = "career"
    elif any(word in local_text for word in ("周一", "闹钟", "被窝", "灵魂", "低电量", "上班综合症", "早会", "同步", "工位", "咖啡")):
        category = "monday"
    elif any(word in local_text for word in ("请假", "病假", "120", "急救", "老板", "审批")):
        category = "leave"
    if category == "street_food":
        pairs = {
            "setup": ("猫：今天卖烤肠", "隔壁：我买一送一"),
            "pressure": ("猫：摊位费先扣？", "旁边猫：煤气也要钱"),
            "twist": ("猫：还能赊账吗", "同学：先赊情绪价值"),
            "punchline": ("猫：烤肠不包上岸", "同学：但能先暖手"),
        }
    elif category == "leave":
        pairs = {
            "setup": ("猫：我想请病假", "老板：先坚持一下"),
            "pressure": ("猫：体温在报警", "老板：工作更急"),
            "twist": ("猫：那我打120", "老板：你等一下"),
            "punchline": ("猫：先保命再上班", "老板：批了批了"),
        }
    elif category == "monday":
        pairs = {
            "setup": ("猫：我已经起床了", "被窝：你没有"),
            "pressure": ("猫：群里又同步", "同事：周一先活着"),
            "twist": ("猫：灵魂没到岗", "咖啡：我试试"),
            "punchline": ("猫：先低电量运行", "同事：别自动关机"),
        }
    elif category == "rent":
        pairs = {
            "setup": ("猫：工资刚到账", "账单：我先来"),
            "pressure": ("猫：房租押金通勤", "室友猫：都在排队"),
            "twist": ("猫：远点会便宜吗", "中介：通勤会补刀"),
            "punchline": ("猫：先摊开预算", "室友猫：再谈体面"),
        }
    elif category == "exam":
        pairs = {
            "setup": ("猫：考研还是考公", "同学猫：就业也在闪"),
            "pressure": ("猫：每条路都挤", "同学猫：先别淹了"),
            "twist": ("猫：选择也要复习", "同学猫：先做一页"),
            "punchline": ("猫：今天先选一题", "同学猫：明天再上岸"),
        }
    elif category == "career" or any(word in local_text for word in ("工作", "简历", "岗位", "面试", "要求", "经验", "应届")):
        pairs = {
            "setup": ("猫：我投了100份", "HR：先要3年经验"),
            "pressure": ("猫：岗位好多", "同学：敢投的好少"),
            "twist": ("猫：是我不行吗", "同学：是规则太绕"),
            "punchline": ("猫：先翻译规则", "同学：再换投法"),
        }
    elif any(word in local_text for word in ("上班", "会议", "加班", "老板", "同步", "工位")):
        pairs = {
            "setup": ("猫：今天几场会", "老板：简单聊八场"),
            "pressure": ("猫：进展是啥", "同事：还在同步"),
            "twist": ("猫：下班了吗", "老板：在吗"),
            "punchline": ("猫：明天再同步", "同事：先关电脑"),
        }
    else:
        pairs = {
            "setup": ("猫：这事不对劲", "旁白猫：先别急"),
            "pressure": ("猫：压力来了", "旁边猫：我也一样"),
            "twist": ("猫：还能这样？", "旁边猫：现实就这样"),
            "punchline": ("猫：先活过今天", "旁边猫：明天再说"),
        }
    left, right = pairs.get(role, ("猫：有点离谱", "旁边猫：确实"))
    return [{"speaker": "left", "text": left}, {"speaker": "right", "text": right}]


def overlay_actions_for_beat(beat: dict[str, Any]) -> list[dict[str, Any]]:
    role = beat["role"]
    caption = beat["caption"]
    joined = f"{caption} {beat.get('intent', '')}"
    actions: list[dict[str, Any]] = []
    throw_action = throw_object_for_legacy_overlay(joined, role)
    if throw_action:
        actions.append(throw_action)
    if role in {"pressure", "escalation"}:
        actions.append({
            "type": "stamp_reject",
            "start": 0.7,
            "duration": 1.0,
            "text": "已读不回",
        })
    if role == "twist":
        actions.append({
            "type": "popup",
            "start": 0.5,
            "duration": 1.8,
            "text": "岗位要求 +1",
        })
    if role == "punchline":
        actions.append({
            "type": "impact_burst",
            "start": 0.6,
            "duration": 1.1,
            "text": "离谱",
        })
    return actions


def throw_object_for_legacy_overlay(text: str, role: str) -> dict[str, Any] | None:
    if role not in {"setup", "pressure", "proof", "twist", "escalation", "echo"}:
        return None
    mapping = [
        (("烤肠", "摆摊", "摊位", "夜市"), "sausage_skewer", "烤肠 x3"),
        (("房租", "押金", "租房", "中介", "账单"), "bill_stack", "账单 -2400"),
        (("考研", "考公", "考试", "资料", "自习"), "study_notes", "资料 x3"),
        (("会议", "复盘", "同步", "老板", "PPT"), "meeting_invite", "会议+1"),
        (("要求", "经验", "门槛", "规则"), "requirement_scroll", "要求+1"),
        (("简历", "招聘", "岗位", "面试"), "resume_stack", "简历 x100"),
    ]
    for triggers, obj, label in mapping:
        if any(trigger in text for trigger in triggers):
            return {
                "type": "throw_object",
                "object": obj,
                "from": "left_cat",
                "to": "right_cat",
                "start": 0.8,
                "duration": 1.2,
                "text": label,
            }
    return None


def casting_and_validator_agents(beats: list[dict[str, Any]], theme: str, index: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    notes = []
    timeline = []
    previous_slot: dict[str, Any] | None = None
    used_motion_ids: set[str] = set()
    used_background_ids: set[str] = set()
    timeline: list[dict[str, Any]] = []
    for beat in beats:
        slot, slot_notes = build_timeline_slot(beat, theme, index, previous_slot, used_motion_ids, used_background_ids)
        notes.extend(slot_notes)
        timeline.append(slot)
        previous_slot = slot
        remember_slot_assets(slot, used_motion_ids, used_background_ids)
    return timeline, notes


async def casting_and_validator_agents_agentic(beats: list[dict[str, Any]], theme: str, index: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    timeline: list[dict[str, Any]] = []
    notes: list[str] = []
    async for event in casting_and_validator_agents_stream(beats, theme, index, use_agent=True):
        if event.get("type") == "slot" and event.get("slot"):
            timeline.append(event["slot"])
        elif event.get("type") == "slot_patch" and event.get("slot"):
            timeline = [event["slot"] if slot.get("id") == event["slot"].get("id") else slot for slot in timeline]
        elif event.get("type") == "notes":
            notes.extend(event.get("notes", []))
    if not timeline:
        return casting_and_validator_agents(beats, theme, index)
    return sort_timeline(timeline), notes


async def casting_and_validator_agents_stream(
    beats: list[dict[str, Any]],
    theme: str,
    index: dict[str, Any],
    progress_start: float = 0.25,
    progress_span: float = 0.6,
    use_agent: bool = True,
):
    notes: list[str] = []
    timeline: list[dict[str, Any]] = []
    previous_slot: dict[str, Any] | None = None
    used_motion_ids: set[str] = set()
    used_background_ids: set[str] = set()
    total = max(1, len(beats))
    settings = get_settings()
    runtime_order = enabled_runtime_order()
    if not use_agent or not runtime_order:
        async for event in workflow_casting_and_validator_agents_stream(beats, theme, index, progress_start, progress_span):
            yield event
        return

    semaphore = asyncio.Semaphore(settings.SHOT_AGENT_CONCURRENCY)

    async def workflow_quick(position: int, beat: dict[str, Any]) -> tuple[int, dict[str, Any], dict[str, Any], list[str]]:
        slot, slot_notes = await asyncio.to_thread(build_timeline_slot, beat, theme, index, None, set(), set())
        return position, beat, slot, slot_notes

    async def agent_refine(position: int, beat: dict[str, Any], fallback_slot: dict[str, Any], fallback_notes: list[str]) -> tuple[int, dict[str, Any], dict[str, Any], list[str], str]:
        async with semaphore:
            try:
                async with asyncio.timeout(settings.SHOT_AGENT_TIMEOUT_SEC):
                    result = await run_shot_agent(
                        theme=theme,
                        beat=beat,
                        index=index,
                        previous_slot=None,
                        used_motion_ids=set(),
                        used_background_ids=set(),
                    )
            except TimeoutError:
                return position, beat, fallback_slot, [f"{beat['role']} ShotPlannerAgent 超时，保留快速分镜。", *fallback_notes[:1]], "workflow_timeout"
            if result.ok and isinstance(result.output.get("slot"), dict):
                slot = normalize_agent_slot(
                    raw_slot=result.output["slot"],
                    beat=beat,
                    theme=theme,
                    index=index,
                    fallback_slot=fallback_slot,
                    previous_slot=None,
                    used_motion_ids=set(),
                    used_background_ids=set(),
                )
                if not slot.get("overlay_actions") and fallback_slot.get("overlay_actions"):
                    slot["overlay_actions"] = fallback_slot["overlay_actions"]
                if not slot.get("packaging") and fallback_slot.get("packaging"):
                    slot["packaging"] = fallback_slot["packaging"]
                agent_notes = [f"{beat['role']} ShotPlannerAgent 使用 {result.provider} 自主规划。"]
                agent_notes.extend(str(item) for item in result.output.get("notes", [])[:2] if str(item).strip())
                return position, beat, slot, [*agent_notes, *fallback_notes[:1]], result.provider
            return position, beat, fallback_slot, [f"{beat['role']} Agent runtime 回退 workflow：{result.error}", *fallback_notes[:1]], "workflow"

    yield {
        "type": "stage",
        "message": f"快速生成 {len(beats)} 个分镜初版",
        "progress": round(progress_start, 3),
    }

    quick_items: list[tuple[int, dict[str, Any], dict[str, Any], list[str]] | None] = [None for _ in beats]
    quick_tasks = [asyncio.create_task(workflow_quick(position, beat)) for position, beat in enumerate(beats)]
    quick_completed = 0
    for task in asyncio.as_completed(quick_tasks):
        position, beat, slot, slot_notes = await task
        quick_items[position] = (position, beat, slot, slot_notes)
        quick_completed += 1
        progress = progress_start + progress_span * 0.18 * (quick_completed / total)
        yield {
            "type": "slot",
            "message": f"快速分镜 {position + 1}/{total} 已完成：{beat['caption']}",
            "progress": round(progress, 3),
            "slot": slot,
        }

    agent_tasks = [
        asyncio.create_task(agent_refine(position, beat, slot, slot_notes))
        for item in quick_items
        if item is not None
        for position, beat, slot, slot_notes in [item]
    ]
    yield {
        "type": "stage",
        "message": f"ShotPlannerAgent 正在并发精修 {len(agent_tasks)} 个镜头",
        "progress": round(progress_start + progress_span * 0.2, 3),
    }

    prebuilt: list[tuple[int, dict[str, Any], dict[str, Any], list[str], str] | None] = [None for _ in beats]
    completed = 0
    pending_tasks = set(agent_tasks)
    agent_started = time.monotonic()
    while pending_tasks:
        remaining = max(0.0, float(settings.SHOT_AGENT_SOFT_TIMEOUT_SEC) - (time.monotonic() - agent_started))
        if remaining <= 0:
            break
        done, pending_tasks = await asyncio.wait(pending_tasks, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
        if not done:
            break
        for task in done:
            position, beat, slot, slot_notes, provider = await task
            prebuilt[position] = (position, beat, slot, slot_notes, provider)
            completed += 1
            progress = progress_start + progress_span * (0.22 + 0.32 * (completed / total))
            yield {
                "type": "slot_patch",
                "message": f"Agent 精修镜头 {position + 1}/{total} 已返回：{beat['caption']}",
                "progress": round(progress, 3),
                "slot": slot,
            }
    if pending_tasks:
        for task in pending_tasks:
            task.cancel()
        await asyncio.gather(*pending_tasks, return_exceptions=True)
        yield {
            "type": "stage",
            "message": f"Agent 精修达到软超时，{len(pending_tasks)} 个镜头保留快速分镜",
            "progress": round(progress_start + progress_span * 0.55, 3),
        }

    yield {
        "type": "stage",
        "message": "正在按时间线统一去重、转场和质检",
        "progress": round(progress_start + progress_span * 0.58, 3),
    }

    for index_num, item in enumerate(prebuilt):
        if item is None:
            quick_item = quick_items[index_num]
            if quick_item is None:
                beat = beats[index_num]
                slot, slot_notes = await asyncio.to_thread(build_timeline_slot, beat, theme, index, previous_slot, used_motion_ids, used_background_ids)
            else:
                _, beat, slot, slot_notes = quick_item
            provider = "workflow"
        else:
            _, beat, slot, slot_notes, provider = item
        if slot_needs_repick(slot, used_motion_ids, used_background_ids):
            slot, slot_notes = await asyncio.to_thread(
                build_timeline_slot,
                beat,
                theme,
                index,
                previous_slot,
                used_motion_ids,
                used_background_ids,
            )
        else:
            background_changed = bool(previous_slot and previous_slot.get("background", {}).get("id") != slot.get("background", {}).get("id"))
            slot["transition"] = transition_planner_tool(beat, previous_slot, background_changed)

        local_critic = local_shot_critic(theme, beat, slot, used_motion_ids, used_background_ids)
        critic_notes = [f"{beat['role']} 本地质检 {local_critic['score']:.2f}。"]
        if provider != "workflow" and local_critic["score"] < 0.72:
            critic_notes.append(f"{beat['role']} 实时预览跳过远程 Critic 长尾，保留可流式预览的分镜。")
        slot_notes.extend(critic_notes)
        slot["source_pattern"] = slot.get("source_pattern") or f"Agent 自主分镜：{provider}"
        notes.extend(slot_notes)
        timeline.append(slot)
        previous_slot = slot
        remember_slot_assets(slot, used_motion_ids, used_background_ids)
        progress = progress_start + progress_span * (0.62 + 0.3 * ((index_num + 1) / total))
        yield {
            "type": "slot_patch",
            "message": f"质检完成镜头 {index_num + 1}/{total}：{beat['caption']}",
            "progress": round(progress, 3),
            "slot": slot,
        }

    timeline = sort_timeline(timeline)
    patches, assembled_notes = await maybe_assemble_timeline(theme, timeline)
    for patch in patches:
        slot_id = str(patch.get("id", ""))
        for pos, slot in enumerate(timeline):
            if slot.get("id") == slot_id:
                timeline[pos] = apply_slot_patch(slot, patch)
                yield {
                    "type": "slot_patch",
                    "message": f"全片统筹已微调镜头：{slot_id}",
                    "progress": round(min(0.98, progress_start + progress_span + 0.04), 3),
                    "slot": timeline[pos],
                }
                break
    if assembled_notes:
        notes.extend(assembled_notes)
    yield {"type": "notes", "message": "Agent 分镜质检完成", "progress": round(progress_start + progress_span, 3), "notes": notes}


async def workflow_casting_and_validator_agents_stream(
    beats: list[dict[str, Any]],
    theme: str,
    index: dict[str, Any],
    progress_start: float = 0.25,
    progress_span: float = 0.6,
):
    notes: list[str] = []
    timeline: list[dict[str, Any]] = []
    previous_slot: dict[str, Any] | None = None
    used_motion_ids: set[str] = set()
    used_background_ids: set[str] = set()
    total = max(1, len(beats))
    settings = get_settings()
    semaphore = asyncio.Semaphore(settings.STORYBOARD_MATCH_CONCURRENCY)

    async def prebuild(position: int, beat: dict[str, Any]) -> tuple[int, dict[str, Any], list[str]]:
        async with semaphore:
            slot, slot_notes = await asyncio.to_thread(build_timeline_slot, beat, theme, index, None, set(), set())
            return position, slot, slot_notes

    tasks = [asyncio.create_task(prebuild(position, beat)) for position, beat in enumerate(beats)]
    yield {
        "type": "stage",
        "message": f"workflow 并行预匹配 {len(tasks)} 个镜头素材",
        "progress": round(progress_start, 3),
    }

    quick_items: list[tuple[dict[str, Any], list[str]] | None] = [None for _ in beats]
    completed = 0
    for task in asyncio.as_completed(tasks):
        position, slot, slot_notes = await task
        quick_items[position] = (slot, slot_notes)
        completed += 1
        progress = progress_start + progress_span * 0.42 * (completed / total)
        yield {
            "type": "slot",
            "message": f"预匹配镜头 {position + 1}/{total} 已完成：{beats[position]['caption']}",
            "progress": round(progress, 3),
            "slot": slot,
        }

    yield {
        "type": "stage",
        "message": "正在按时间线去重素材、动画和转场",
        "progress": round(progress_start + progress_span * 0.46, 3),
    }

    for index_num, beat in enumerate(beats):
        item = quick_items[index_num]
        if item is None:
            _, slot, slot_notes = await prebuild(index_num, beat)
        else:
            slot, slot_notes = item
        if slot_needs_repick(slot, used_motion_ids, used_background_ids):
            slot, slot_notes = await asyncio.to_thread(
                build_timeline_slot,
                beat,
                theme,
                index,
                previous_slot,
                used_motion_ids,
                used_background_ids,
            )
        else:
            background_changed = bool(previous_slot and previous_slot.get("background", {}).get("id") != slot.get("background", {}).get("id"))
            slot["transition"] = transition_planner_tool(beat, previous_slot, background_changed)
        notes.extend(slot_notes)
        previous_slot = slot
        remember_slot_assets(slot, used_motion_ids, used_background_ids)
        timeline.append(slot)
        progress = progress_start + progress_span * (0.5 + 0.35 * ((index_num + 1) / total))
        yield {
            "type": "slot_patch",
            "message": f"已质检镜头 {index_num + 1}/{total}：{beat['caption']}",
            "progress": round(progress, 3),
            "slot": slot,
        }
    timeline = sort_timeline(timeline)
    patches, assembled_notes = local_assemble_timeline(timeline)
    for patch in patches:
        slot_id = str(patch.get("id", ""))
        for position, slot in enumerate(timeline):
            if slot.get("id") == slot_id:
                timeline[position] = apply_slot_patch(slot, patch)
                yield {
                    "type": "slot_patch",
                    "message": f"workflow 已优化动画/转场：{slot_id}",
                    "progress": round(min(0.98, progress_start + progress_span * 0.94), 3),
                    "slot": timeline[position],
                }
                break
    if assembled_notes:
        notes.extend(assembled_notes)
    yield {"type": "notes", "message": "素材质检完成", "progress": round(progress_start + progress_span, 3), "notes": notes}


def build_timeline_slot(
    beat: dict[str, Any],
    theme: str,
    index: dict[str, Any],
    previous_slot: dict[str, Any] | None = None,
    used_motion_ids: set[str] | None = None,
    used_background_ids: set[str] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    slot_notes: list[str] = []
    used_motion_ids = used_motion_ids or set()
    used_background_ids = used_background_ids or set()
    motion = choose_motion_for_beat(index, beat, used_motion_ids)
    motion_quality = motion_quality_flags(motion)
    use_secondary_motion = beat["layout"] == "dialogue" and not motion_quality.get("natural_double")
    secondary_motion = choose_secondary_motion_for_beat(index, beat, motion, used_motion_ids) if use_secondary_motion else {}
    background = choose_background_for_beat(index, beat, theme, used_background_ids)
    motion_score = asset_match_score(motion, beat["emotion_keywords"])
    background_score = asset_match_score(background, beat["scene_keywords"])
    background, background_source, background_prompt, fill_note = background_fill_tool(theme, beat, background, background_score)
    if background_source == "generated":
        background_score = 1.0
    if fill_note:
        slot_notes.append(f"{beat['role']} 背景补图：{fill_note}")
    gap = validate_match(beat, motion, background, motion_score, background_score)
    if gap["status"] != "matched":
        slot_notes.append(f"{beat['role']} 使用 {gap['strategy']}：{gap['reason']}")
    background_changed = bool(previous_slot and previous_slot.get("background", {}).get("id") != str(background.get("id", "")))
    slot = {
        "id": beat["id"],
        "start": beat["start"],
        "end": beat["end"],
        "role": beat["role"],
        "intent": beat["intent"],
        "caption": beat["caption"],
        "motion": ref(motion),
        "motion_quality": motion_quality,
        "motion_clip": clip_planner_tool(motion, beat, beat["end"] - beat["start"]),
        "secondary_motion": ref(secondary_motion) if use_secondary_motion else None,
        "secondary_motion_quality": motion_quality_flags(secondary_motion) if use_secondary_motion else {},
        "secondary_motion_clip": clip_planner_tool(secondary_motion, beat, beat["end"] - beat["start"]) if use_secondary_motion else None,
        "background": ref(background),
        "background_source": background_source,
        "background_prompt": background_prompt if background_source in {"generated", "generated_pending"} else "",
        "transition": transition_planner_tool(beat, previous_slot, background_changed),
        "layout": beat["layout"],
        "dialogue": beat["dialogue"],
        "overlay_actions": overlay_planner_tool(beat, motion, background, theme),
        "gap": gap,
        "packaging": packaging_for_gap(beat["role"], gap),
        "source_pattern": pattern_for_beat(beat),
        "source_viral_shot": beat.get("source_viral_shot", {}),
    }
    slot["asset_sources"] = asset_sources_for_slot(slot)
    return slot, slot_notes


def normalize_agent_slot(
    raw_slot: dict[str, Any],
    beat: dict[str, Any],
    theme: str,
    index: dict[str, Any],
    fallback_slot: dict[str, Any],
    previous_slot: dict[str, Any] | None,
    used_motion_ids: set[str] | None,
    used_background_ids: set[str] | None,
) -> dict[str, Any]:
    slot = json.loads(json.dumps(fallback_slot, ensure_ascii=False))
    if not isinstance(raw_slot, dict):
        return slot

    slot.update({
        "id": str(raw_slot.get("id") or beat["id"]),
        "start": float(beat.get("start", raw_slot.get("start", slot["start"]))),
        "end": float(beat.get("end", raw_slot.get("end", slot["end"]))),
        "role": str(beat.get("role", raw_slot.get("role", slot["role"]))),
        "intent": clean_agent_text(raw_slot.get("intent") or beat.get("intent") or slot.get("intent", ""), 60),
        "caption": clean_caption(str(raw_slot.get("caption") or raw_slot.get("copy") or beat.get("caption") or slot.get("caption", ""))),
        "layout": str(raw_slot.get("layout") or beat.get("layout") or slot.get("layout") or "single"),
        "source_pattern": clean_agent_text(raw_slot.get("source_pattern") or "Agent 自主分镜", 120),
    })
    if beat.get("source_viral_shot"):
        slot["source_viral_shot"] = beat.get("source_viral_shot")

    motion = normalize_asset_ref(raw_slot.get("motion"), "motion", index) or slot.get("motion") or {}
    if str(motion.get("id", "")) in (used_motion_ids or set()) and len(used_motion_ids or set()) < 10:
        motion = slot.get("motion") or motion
    slot["motion"] = motion
    slot["motion_quality"] = motion_quality_flags(asset_from_ref(index, "motion", motion) or motion)
    slot["motion_clip"] = normalize_clip(raw_slot.get("motion_clip"), slot_duration(slot), slot.get("motion_clip"))

    secondary = normalize_asset_ref(raw_slot.get("secondary_motion"), "motion", index)
    if slot["layout"] == "dialogue" and secondary:
        slot["secondary_motion"] = secondary
        slot["secondary_motion_quality"] = motion_quality_flags(asset_from_ref(index, "motion", secondary) or secondary)
        slot["secondary_motion_clip"] = normalize_clip(raw_slot.get("secondary_motion_clip"), slot_duration(slot), slot.get("secondary_motion_clip") or slot["motion_clip"])
    elif slot["layout"] == "dialogue" and slot.get("secondary_motion"):
        slot["secondary_motion_clip"] = normalize_clip(raw_slot.get("secondary_motion_clip"), slot_duration(slot), slot.get("secondary_motion_clip") or slot["motion_clip"])
    else:
        slot["secondary_motion"] = None
        slot["secondary_motion_quality"] = {}
        slot["secondary_motion_clip"] = None

    background = normalize_asset_ref(raw_slot.get("background"), "background", index) or slot.get("background") or {}
    if str(background.get("id", "")) in (used_background_ids or set()) and len(used_background_ids or set()) <= 2:
        background = slot.get("background") or background
    slot["background"] = background
    background_source = str(raw_slot.get("background_source") or slot.get("background_source") or "matched")
    slot["background_source"] = background_source if background_source in {"matched", "generated", "generated_pending"} else "matched"
    slot["background_prompt"] = clean_agent_text(raw_slot.get("background_prompt") or slot.get("background_prompt") or "", 260)

    transition = raw_slot.get("transition") if isinstance(raw_slot.get("transition"), dict) else {}
    background_changed = bool(previous_slot and previous_slot.get("background", {}).get("id") != slot.get("background", {}).get("id"))
    slot["transition"] = normalize_transition(transition or transition_planner_tool(beat, previous_slot, background_changed))
    slot["dialogue"] = normalize_dialogue(raw_slot.get("dialogue"), beat, slot["layout"])
    slot["overlay_actions"] = normalize_overlay_actions(raw_slot.get("overlay_actions") or slot.get("overlay_actions") or [], theme, beat)
    slot["packaging"] = normalize_packaging(raw_slot.get("packaging") or slot.get("packaging") or [], slot)

    gap = raw_slot.get("gap") if isinstance(raw_slot.get("gap"), dict) else slot.get("gap", {})
    slot["gap"] = normalize_gap(gap, beat, slot)
    slot["asset_sources"] = asset_sources_for_slot(slot)
    return slot


def local_shot_critic(
    theme: str,
    beat: dict[str, Any],
    slot: dict[str, Any],
    used_motion_ids: set[str],
    used_background_ids: set[str],
) -> dict[str, Any]:
    score = 1.0
    issues: list[str] = []
    motion_text = f"{slot.get('motion', {}).get('id', '')} {slot.get('motion', {}).get('description', '')}"
    background_text = f"{slot.get('background', {}).get('id', '')} {slot.get('background', {}).get('description', '')}"
    if beat.get("emotion_keywords") and not any(str(keyword) in motion_text for keyword in beat.get("emotion_keywords", [])[:8]):
        score -= 0.16
        issues.append("motion_mismatch")
    if beat.get("scene_keywords") and not any(str(keyword) in background_text for keyword in beat.get("scene_keywords", [])[:10]):
        score -= 0.16
        issues.append("background_mismatch")
    if str(slot.get("motion", {}).get("id", "")) in used_motion_ids and len(used_motion_ids) < 10:
        score -= 0.1
        issues.append("motion_repeated")
    if str(slot.get("background", {}).get("id", "")) in used_background_ids and len(used_background_ids) <= 2:
        score -= 0.08
        issues.append("background_repeated")
    if slot.get("layout") == "dialogue" and len(slot.get("dialogue") or []) < 2:
        score -= 0.12
        issues.append("dialogue_missing")
    if not slot.get("overlay_actions"):
        score -= 0.1
        issues.append("overlay_missing")
    if "简历x100" in json.dumps(slot.get("overlay_actions", []), ensure_ascii=False) and "简历" not in f"{theme} {beat.get('caption', '')}":
        score -= 0.18
        issues.append("overlay_repetitive")
    return {"score": round(max(0.0, min(1.0, score)), 3), "issues": issues}


async def maybe_critic_revise_slot(
    *,
    theme: str,
    beat: dict[str, Any],
    slot: dict[str, Any],
    index: dict[str, Any],
    previous_slot: dict[str, Any] | None,
    used_motion_ids: set[str],
    used_background_ids: set[str],
) -> tuple[dict[str, Any], list[str]]:
    settings = get_settings()
    if not enabled_runtime_order() or settings.SHOT_AGENT_MAX_REVISIONS <= 0:
        return slot, []
    notes: list[str] = []
    current = slot
    for revision in range(settings.SHOT_AGENT_MAX_REVISIONS):
        try:
            async with asyncio.timeout(settings.CRITIC_AGENT_TIMEOUT_SEC):
                result = await run_critic_agent(
                    theme=theme,
                    beat=beat,
                    slot=current,
                    index=index,
                    used_motion_ids=used_motion_ids,
                    used_background_ids=used_background_ids,
                )
        except TimeoutError:
            notes.append(f"{beat['role']} CriticAgent 超时，保留本地质检结果。")
            break
        if not result.ok:
            if revision == 0:
                notes.append(f"{beat['role']} CriticAgent 跳过：{result.error}")
            break
        critic = result.output.get("critic") if isinstance(result.output.get("critic"), dict) else {}
        score = float(critic.get("score") or 0)
        if isinstance(result.output.get("revised_slot"), dict):
            before_overlay = list(current.get("overlay_actions") or [])
            before_packaging = list(current.get("packaging") or [])
            current = normalize_agent_slot(
                result.output["revised_slot"],
                beat,
                theme,
                index,
                current,
                previous_slot,
                used_motion_ids,
                used_background_ids,
            )
            if before_overlay and not current.get("overlay_actions"):
                current["overlay_actions"] = before_overlay
            if before_packaging and not current.get("packaging"):
                current["packaging"] = before_packaging
        notes.append(f"{beat['role']} CriticAgent 质检 {score:.2f}。")
        if bool(critic.get("passed", score >= 0.72)):
            break
    return current, notes


async def maybe_assemble_timeline(theme: str, timeline: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    if len(timeline) < 2:
        return [], []
    local_patches, local_notes = local_assemble_timeline(timeline)
    return local_patches, local_notes
    result = await run_assembler_agent(theme=theme, timeline=timeline)
    if not result.ok:
        return local_patches, [*local_notes, f"AssemblerAgent 跳过：{result.error}"]
    patches = result.output.get("timeline_patch") if isinstance(result.output.get("timeline_patch"), list) else []
    notes = [str(item) for item in result.output.get("notes", []) if str(item).strip()] if isinstance(result.output.get("notes"), list) else []
    if patches:
        notes.insert(0, f"AssemblerAgent 已微调 {len(patches)} 个镜头。")
    return [*local_patches, *[patch for patch in patches if isinstance(patch, dict)]], [*local_notes, *notes]


def local_assemble_timeline(timeline: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    patches: list[dict[str, Any]] = []
    notes: list[str] = []
    overlay_counts: dict[str, int] = {}
    overlay_type_counts: dict[str, int] = {}
    previous_background = ""
    primary_types = {
        "phone_job_feed",
        "job_requirement_card",
        "work_chat_stack",
        "chat_stack",
        "choice_panel",
        "study_card",
        "bill_card",
        "commute_card",
        "stall_sign",
        "leave_request",
        "emergency_call",
    }
    for slot in timeline:
        patch: dict[str, Any] = {"id": slot.get("id", "")}
        actions: list[dict[str, Any]] = []
        changed = False
        for action in slot.get("overlay_actions") or []:
            action_type = str(action.get("type") or "")
            signature = f"{action.get('type')}|{action.get('text') or action.get('title') or action.get('object')}"
            overlay_counts[signature] = overlay_counts.get(signature, 0) + 1
            overlay_type_counts[action_type] = overlay_type_counts.get(action_type, 0) + 1
            type_limit = 2 if action_type in primary_types else 1 if action_type in {"throw_object", "impact_burst", "stamp_reject", "popup"} else 2
            if overlay_counts[signature] > 2 or overlay_type_counts[action_type] > type_limit:
                changed = True
                continue
            actions.append(action)
        if actions and len(actions) > 1:
            actions = select_distinct_overlay_actions(actions)
            if len(actions) != len(slot.get("overlay_actions") or []):
                changed = True
        if changed:
            patch["overlay_actions"] = actions[:2]
        background_id = str(slot.get("background", {}).get("id", ""))
        if previous_background and previous_background != background_id and slot.get("transition", {}).get("type") == "cut":
            patch["transition"] = {"type": "fade", "duration": 0.22}
            changed = True
        previous_background = background_id
        if changed and patch.get("id"):
            patches.append(patch)
    if patches:
        notes.append(f"本地全片统筹已去重/补转场 {len(patches)} 个镜头。")
    return patches, notes


def assemble_timeline_locally(timeline: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    ordered = sort_timeline(timeline)
    patches, notes = local_assemble_timeline(ordered)
    if not patches:
        return ordered, notes
    by_id = {str(patch.get("id", "")): patch for patch in patches if patch.get("id")}
    assembled = [
        apply_slot_patch(slot, by_id[str(slot.get("id", ""))]) if str(slot.get("id", "")) in by_id else slot
        for slot in ordered
    ]
    return assembled, notes


def select_distinct_overlay_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen_types: set[str] = set()
    for action in actions:
        action_type = str(action.get("type") or "")
        if action_type in seen_types and action_type in {"throw_object", "popup", "impact_burst", "stamp_reject"}:
            continue
        selected.append(action)
        seen_types.add(action_type)
        if len(selected) >= 2:
            break
    return selected


def timeline_needs_remote_assembler(timeline: list[dict[str, Any]]) -> bool:
    overlay_counts: dict[str, int] = {}
    background_counts: dict[str, int] = {}
    for slot in timeline:
        background_id = str(slot.get("background", {}).get("id", ""))
        if background_id:
            background_counts[background_id] = background_counts.get(background_id, 0) + 1
        for action in slot.get("overlay_actions") or []:
            signature = f"{action.get('type')}|{action.get('text') or action.get('title') or action.get('object')}"
            overlay_counts[signature] = overlay_counts.get(signature, 0) + 1
    if any(count > 2 for count in overlay_counts.values()):
        return True
    if len(timeline) >= 6 and any(count >= max(4, len(timeline) - 1) for count in background_counts.values()):
        return True
    return False


def apply_slot_patch(slot: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    next_slot = json.loads(json.dumps(slot, ensure_ascii=False))
    for key in ("transition", "overlay_actions", "packaging", "dialogue", "caption", "copy"):
        if key in patch:
            if key == "transition" and isinstance(patch[key], dict):
                next_slot[key] = normalize_transition(patch[key])
            elif key == "overlay_actions" and isinstance(patch[key], list):
                next_slot[key] = patch[key][:3]
            elif key in {"caption", "copy"}:
                next_slot["caption"] = clean_caption(str(patch[key]))
            else:
                next_slot[key] = patch[key]
    return next_slot


def sort_timeline(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(timeline, key=lambda slot: (float(slot.get("start") or 0), str(slot.get("id") or "")))


def normalize_asset_ref(value: Any, asset_type: str, index: dict[str, Any]) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    asset_id = str(value.get("id") or "")
    asset = asset_from_id(index, asset_type, asset_id)
    if asset:
        return ref(asset)
    file = str(value.get("file") or "")
    desc = str(value.get("description") or "")
    if asset_id and file:
        return {"id": asset_id, "file": file, "description": desc}
    return None


def asset_from_ref(index: dict[str, Any], asset_type: str, asset_ref: dict[str, Any]) -> dict[str, Any] | None:
    return asset_from_id(index, asset_type, str(asset_ref.get("id", "")))


def asset_from_id(index: dict[str, Any], asset_type: str, asset_id: str) -> dict[str, Any] | None:
    if not asset_id:
        return None
    collection = index.get("cat_motions" if asset_type == "motion" else "backgrounds", [])
    for asset in collection:
        if str(asset.get("id", "")) == asset_id:
            return asset
    for asset in collection:
        if asset_id in f"{asset.get('id', '')} {asset.get('file', '')}":
            return asset
    return None


def normalize_clip(value: Any, duration: float, fallback: Any = None) -> dict[str, Any]:
    source = value if isinstance(value, dict) else fallback if isinstance(fallback, dict) else {}
    start = max(0.0, safe_float(source.get("start"), 0.0))
    clip_duration = safe_float(source.get("duration"), min(4.0, duration))
    if duration <= 3.0:
        clip_duration = max(2.0, min(duration, clip_duration))
    else:
        clip_duration = max(3.0, min(5.0, clip_duration))
    return {
        "start": round(start, 2),
        "duration": round(clip_duration, 2),
        "speed": safe_float(source.get("speed"), None) if source.get("speed") is not None else None,
        "loop": bool(source.get("loop", False)),
    }


def normalize_transition(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        value = {}
    kind = str(value.get("type") or "cut")
    if kind not in {"cut", "fade", "whip", "zoom", "flash"}:
        kind = "cut"
    duration = max(0.0, min(0.5, safe_float(value.get("duration"), 0.0)))
    if kind == "cut":
        duration = 0.0
    return {"type": kind, "duration": round(duration, 2)}


def normalize_dialogue(value: Any, beat: dict[str, Any], layout: str) -> list[dict[str, str]]:
    if layout != "dialogue":
        return []
    if isinstance(value, list):
        lines = []
        for item in value[:2]:
            if not isinstance(item, dict):
                continue
            text = clean_agent_text(item.get("text", ""), 18)
            if text:
                lines.append({"speaker": str(item.get("speaker") or ("left" if len(lines) == 0 else "right")), "text": text})
        if len(lines) >= 2:
            return lines
    return beat.get("dialogue") or []


def normalize_overlay_actions(actions: Any, theme: str, beat: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(actions, list):
        return []
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for action in actions:
        if not isinstance(action, dict) or not action.get("type"):
            continue
        next_action = dict(action)
        if next_action.get("type") == "thrown_prop":
            next_action["type"] = "throw_object"
        if next_action.get("type") == "primitive_card":
            next_action["type"] = "job_requirement_card"
        if next_action.get("type") == "chat_ui":
            next_action["type"] = "chat_stack"
        if next_action.get("type") == "phone_ui":
            next_action["type"] = "phone_job_feed"
        if next_action.get("type") == "stamp":
            next_action["type"] = "stamp_reject"
        kind = str(next_action.get("type"))
        if kind not in {
            "throw_object",
            "stamp_reject",
            "popup",
            "impact_burst",
            "phone_job_feed",
            "job_requirement_card",
            "work_chat_stack",
            "chat_stack",
            "choice_panel",
            "study_card",
            "bill_card",
            "commute_card",
            "stall_sign",
            "leave_request",
            "emergency_call",
            "generated_sticker",
        }:
            continue
        next_action["start"] = round(max(0.0, min(5.0, safe_float(next_action.get("start"), 0.35))), 2)
        next_action["duration"] = round(max(0.4, min(4.8, safe_float(next_action.get("duration"), 1.6))), 2)
        for key in ("text", "title", "salary", "company", "object"):
            if key in next_action:
                next_action[key] = clean_agent_text(next_action[key], 18 if key != "object" else 32)
        for key in ("items", "messages", "options", "tags"):
            if isinstance(next_action.get(key), list):
                next_action[key] = [clean_agent_text(item, 16) for item in next_action[key][:4] if clean_agent_text(item, 16)]
        key = f"{kind}|{next_action.get('text') or next_action.get('title') or next_action.get('object')}"
        if key in seen:
            continue
        seen.add(key)
        normalized.append(next_action)
    if not normalized:
        return overlay_planner_tool(beat, {}, {}, theme)
    return normalized[:3]


def normalize_packaging(items: Any, slot: dict[str, Any]) -> list[str]:
    values = [str(item) for item in items if str(item).strip()] if isinstance(items, list) else []
    if slot.get("layout") == "dialogue":
        values.append("dialogue_bubbles")
    elif slot.get("overlay_actions"):
        values.append("top_title")
    else:
        values.append("bottom_subtitle")
    return list(dict.fromkeys(values))[:5]


def normalize_gap(gap: dict[str, Any], beat: dict[str, Any], slot: dict[str, Any]) -> dict[str, str]:
    status = str(gap.get("status") or "matched")
    if status not in {"matched", "supplemented", "generated_pending"}:
        status = "matched" if slot.get("background_source") in {"matched", "generated"} else "supplemented"
    strategy = str(gap.get("strategy") or ("seedream_background" if slot.get("background_source") == "generated" else "direct_match"))
    reason = clean_agent_text(gap.get("reason") or "Agent 已匹配猫动作、背景和包装。", 100)
    return {"status": status, "strategy": strategy, "reason": reason}


def slot_duration(slot: dict[str, Any]) -> float:
    return max(1.0, safe_float(slot.get("end"), 0.0) - safe_float(slot.get("start"), 0.0))


def safe_float(value: Any, fallback: float | None = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(fallback or 0.0)


def clean_agent_text(value: Any, limit: int = 80) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    text = re.sub(r"[{}<>`$]", "", text)
    return text[:limit]


def motion_quality_flags(asset: dict[str, Any]) -> dict[str, bool]:
    desc = str(asset.get("description", ""))
    return {
        "needs_crop": any(word in desc for word in ("黑边", "白底", "需要裁切", "需裁切", "低清", "模糊")),
        "low_quality": any(word in desc for word in ("低清", "模糊")),
        "non_cat": any(word in desc for word in ("非猫素材", "小狗", "山羊")),
        "natural_double": any(word in desc for word in ("天然双猫", "双猫对话画面")),
    }


def remember_slot_assets(slot: dict[str, Any], used_motion_ids: set[str], used_background_ids: set[str]) -> None:
    used_motion_ids.add(str(slot.get("motion", {}).get("id", "")))
    secondary_id = str((slot.get("secondary_motion") or {}).get("id", ""))
    if secondary_id:
        used_motion_ids.add(secondary_id)
    used_background_ids.add(str(slot.get("background", {}).get("id", "")))


def slot_needs_repick(slot: dict[str, Any], used_motion_ids: set[str], used_background_ids: set[str]) -> bool:
    motion_id = str(slot.get("motion", {}).get("id", ""))
    secondary_id = str((slot.get("secondary_motion") or {}).get("id", ""))
    background_id = str(slot.get("background", {}).get("id", ""))
    return (
        (motion_id and motion_id in used_motion_ids and len(used_motion_ids) < 10)
        or (secondary_id and secondary_id in used_motion_ids and len(used_motion_ids) < 10)
        or (background_id and background_id in used_background_ids and len(used_background_ids) <= 2)
    )


def choose_motion_for_beat(index: dict[str, Any], beat: dict[str, Any], used_ids: set[str] | None = None) -> dict[str, Any]:
    used_ids = used_ids or set()
    ranked = asset_search_tool(index, "motion", beat["emotion_keywords"] + beat.get("must_keywords", []), limit=18)
    all_assets = index.get("cat_motions", [])
    candidates = list({str(asset.get("id", "")): asset for asset in [*ranked, *all_assets]}.values())
    ranked_ids = {str(asset.get("id", "")): order for order, asset in enumerate(ranked)}
    context = beat_context_text(beat)
    category = infer_theme_category(context)
    scored: list[tuple[float, dict[str, Any]]] = []
    for asset in candidates:
        desc = str(asset.get("description", ""))
        score = asset_match_score(asset, beat["emotion_keywords"])
        if str(asset.get("id", "")) in ranked_ids:
            score += max(0.0, 2.5 - ranked_ids[str(asset.get("id", ""))] * 0.12)
        for keyword in beat.get("must_keywords", []):
            if keyword in desc:
                score += 4.0
        for keyword in beat.get("forbidden_keywords", []):
            if keyword in desc:
                score -= 8.0
        if any(word in desc for word in ("非猫素材", "默认避用", "只用于夸张", "过激")):
            score -= 12.0
        if any(word in desc for word in ("黑边", "低清", "模糊", "白底", "需要裁切", "需裁切")):
            score -= 2.5
        if any(word in desc for word in ("近景", "强反应")) and beat["role"] in {"hook", "twist"}:
            score += 1.2
        score += role_motion_bonus(beat["role"], desc)
        score += caption_motion_bonus(str(beat.get("caption", "")), desc)
        score += category_motion_bonus(category, beat["role"], context, desc)
        if beat.get("layout") == "dialogue" and any(word in desc for word in ("天然双猫", "双猫对话画面")):
            score -= 2.0
        if beat["role"] == "setup" and category in {"career", "office"} and any(word in desc for word in ("电脑", "笔记本")):
            score += 2.0
        if beat["role"] == "setup" and "通勤" in context and any(word in desc for word in ("方向盘", "开车")):
            score += 2.0
        if beat["role"] == "punchline" and any(word in desc for word in ("欢快", "跳舞", "蹦跳", "可爱")):
            score += 2.0
        if str(asset.get("id", "")) in used_ids:
            score -= 6.0 if len(used_ids) < 10 else 2.0
        scored.append((score, asset))
    scored.sort(key=lambda item: (-item[0], str(item[1].get("id", ""))))
    viable = [(score, asset) for score, asset in scored if score > 0]
    if viable:
        top_score = viable[0][0]
        pool = [asset for score, asset in viable if score >= top_score - 1.5][:5]
        return pool[selection_offset(beat) % len(pool)]
    return pick_motion(index, beat["emotion_keywords"], fallback_id=fallback_motion_id(beat["role"]))


def beat_context_text(beat: dict[str, Any]) -> str:
    dialogue = " ".join(str(item.get("text", "")) for item in beat.get("dialogue", []) if isinstance(item, dict))
    return " ".join(
        str(item)
        for item in [
            beat.get("theme", ""),
            beat.get("caption", ""),
            beat.get("intent", ""),
            " ".join(str(keyword) for keyword in beat.get("scene_keywords", [])),
            " ".join(str(keyword) for keyword in beat.get("theme_keywords", [])),
            dialogue,
        ]
        if str(item).strip()
    )


def role_motion_bonus(role: str, desc: str) -> float:
    mapping = {
        "hook": ("震惊", "瞪眼", "惊叫", "强 hook", "突然"),
        "setup": ("电脑", "探头", "冷漠", "碎碎念", "双猫"),
        "pressure": ("委屈", "哭", "崩溃", "焦虑", "生无可恋", "强忍"),
        "proof": ("探头", "双猫", "碎碎念", "同学", "委屈"),
        "twist": ("回头", "吐槽", "破防", "错愕", "看穿", "强反应"),
        "echo": ("双猫", "委屈", "旁边", "共鸣", "探头"),
        "escalation": ("崩溃", "嚎啕", "疯狂", "压力爆表", "失控"),
        "punchline": ("跳舞", "蹦跳", "摆烂", "庆祝", "免打扰", "喘口气", "过关"),
        "cta": ("抱奶茶", "休息", "可爱", "温暖", "松一口气"),
    }.get(role, ())
    return sum(1.2 for keyword in mapping if keyword in desc)


def caption_motion_bonus(caption: str, desc: str) -> float:
    mapping = {
        ("招聘", "简历", "岗位", "HR", "黑话", "规则"): ("电脑", "震惊", "吐槽", "看穿", "委屈"),
        ("会议", "复盘", "同步", "老板", "在线待命"): ("电脑", "摆烂", "装听不见", "生无可恋", "免打扰"),
        ("考研", "考公", "上岸", "自习", "资料"): ("探头", "委屈", "哭", "焦虑", "资料"),
        ("租房", "房租", "押金", "账单", "通勤"): ("委屈", "可怜", "压抑", "冷漠", "通勤"),
        ("烤肠", "摆摊", "摊位", "小吃摊", "赊账"): ("吐槽", "魔性", "委屈", "跳舞", "摆烂"),
    }
    score = 0.0
    for triggers, keywords in mapping.items():
        if any(trigger in caption for trigger in triggers):
            score += sum(1.0 for keyword in keywords if keyword in desc)
    return score


def category_motion_bonus(category: str, role: str, text: str, desc: str) -> float:
    positive = {
        "career": ("电脑", "投简历", "震惊", "吐槽", "委屈", "看穿规则", "求职失败"),
        "leave": ("委屈", "电脑", "震惊", "装病", "强忍", "请假", "可怜"),
        "office": ("电脑", "冷漠", "摆烂", "装听不见", "免打扰", "生无可恋", "加班破防"),
        "exam": ("探头", "委屈", "哭", "焦虑", "查成绩", "惊叫"),
        "rent": ("委屈", "可怜", "压抑", "冷漠", "通勤", "喘口气"),
        "street_food": ("吐槽", "魔性", "委屈", "跳舞", "摆烂", "可爱"),
    }.get(category, ())
    negative = {
        "career": ("开车", "山羊", "小狗", "射击", "过激"),
        "leave": ("开车", "山羊", "小狗", "射击", "过激", "跳舞"),
        "office": ("开车", "山羊", "小狗", "射击", "过激"),
        "exam": ("开车", "山羊", "小狗", "射击", "过激", "免打扰"),
        "rent": ("山羊", "小狗", "射击", "过激", "电脑", "笔记本"),
        "street_food": ("开车", "射击", "过激", "电脑", "笔记本"),
    }.get(category, ())
    score = sum(1.1 for keyword in positive if keyword in desc)
    score -= sum(2.5 for keyword in negative if keyword in desc)
    if role in {"punchline", "cta"} and any(word in desc for word in ("欢快", "跳舞", "蹦跳", "喘口气", "休息")):
        score += 1.8
    if role in {"pressure", "proof", "escalation"} and any(word in desc for word in ("哭", "委屈", "崩溃", "焦虑", "生无可恋")):
        score += 1.6
    if any(word in text for word in ("通勤", "地铁", "公交")) and "开车" in desc:
        score += 1.4
    return score


def choose_secondary_motion_for_beat(index: dict[str, Any], beat: dict[str, Any], primary: dict[str, Any], used_ids: set[str] | None = None) -> dict[str, Any]:
    if beat.get("layout") != "dialogue":
        return primary
    used_ids = used_ids or set()
    candidates = index.get("cat_motions", [])
    context = beat_context_text(beat)
    category = infer_theme_category(context)
    keywords = ["冷漠", "探头", "碎碎念", "可爱", "震惊", "电脑"]
    if any(word in f"{beat.get('caption', '')} {beat.get('intent', '')}" for word in ("对话", "同学", "HR", "老板", "室友", "隔壁", "旁边")):
        keywords = ["双猫", "对话", "吐槽", "探头", "碎碎念", "生无可恋", *keywords]
    best: tuple[float, dict[str, Any]] | None = None
    for asset in candidates:
        if str(asset.get("id")) == str(primary.get("id")):
            continue
        desc = str(asset.get("description", ""))
        score = asset_match_score(asset, keywords)
        score += category_motion_bonus(category, beat["role"], context, desc)
        score += role_motion_bonus(beat["role"], desc) * 0.6
        if any(word in desc for word in ("非猫素材", "默认避用", "过激", "小狗", "山羊", "射击")):
            score -= 10.0
        if any(word in desc for word in ("天然双猫", "双猫对话画面")):
            score -= 4.0
        if any(word in desc for word in ("黑边", "低清", "模糊", "白底", "需要裁切", "需裁切")):
            score -= 1.5
        if str(asset.get("id", "")) in used_ids:
            score -= 3.0
        if best is None or score > best[0]:
            best = (score, asset)
    return best[1] if best else pick_motion(index, keywords, fallback_id="10")


def choose_background_for_beat(index: dict[str, Any], beat: dict[str, Any], theme: str, used_ids: set[str] | None = None) -> dict[str, Any]:
    used_ids = used_ids or set()
    scene_keywords = safe_scene_keywords_for_beat(beat)
    candidates = asset_search_tool(index, "background", scene_keywords + [beat.get("caption", "")], limit=12)
    candidates = include_exact_background_candidates(index, candidates, scene_keywords)
    category = local_beat_scene_category(beat, theme)
    scored: list[tuple[float, dict[str, Any]]] = []
    for asset in candidates:
        score = asset_match_score(asset, scene_keywords)
        score += asset_match_score(asset, [beat.get("caption", ""), beat.get("intent", "")]) * 0.4
        score += themed_background_score(category, asset, beat)
        asset_id = str(asset.get("id", ""))
        asset_scene = str(asset.get("scene", ""))
        for keyword in scene_keywords:
            if keyword and (keyword == asset_id or keyword == asset_scene):
                score += 24.0 if "generated/preset-" in asset_id else 8.0
            elif keyword and (asset_id.startswith(keyword) or keyword.startswith(asset_id)):
                score += 12.0 if "generated/preset-" in asset_id else 4.0
            if keyword and keyword in str(asset.get("file", "")):
                score += 1.2
        score += scene_guard_score(category, asset, beat)
        if beat["role"] in str(asset.get("file", "")):
            score += 2.0
        if beat["role"] in {"twist", "punchline"} and "generated" in str(asset.get("scene", "")):
            score += 0.2
        if str(asset.get("id", "")) in used_ids:
            score -= 18.0 if "generated/preset-" in asset_id else 0.8
        scored.append((score, asset))
    scored.sort(key=lambda item: (-item[0], str(item[1].get("id", ""))))
    if needs_food_stall_background(beat):
        for _, asset in scored:
            text = f"{asset.get('id', '')} {asset.get('scene', '')} {asset.get('file', '')} {asset.get('description', '')}"
            if beat["role"] in text and "generated" in text and any(word in text for word in ("烤肠", "香肠", "小吃摊", "夜市", "餐车", "街边摊")):
                return asset
        for _, asset in scored:
            text = f"{asset.get('id', '')} {asset.get('scene', '')} {asset.get('file', '')} {asset.get('description', '')}"
            if "generated" in text and any(word in text for word in ("烤肠", "香肠", "小吃摊", "夜市", "餐车", "街边摊")):
                return asset
    themed = [item for item in scored if themed_background_score(category, item[1], beat) >= 8.0]
    if themed:
        top_score = themed[0][0]
        pool = [asset for score, asset in themed if score >= top_score - 1.5][:3] or [themed[0][1]]
        return pool[selection_offset(beat) % len(pool)]
    if scored:
        top_score = scored[0][0]
        pool = [asset for score, asset in scored if score >= top_score - 0.5][:4] or [scored[0][1]]
        return pool[selection_offset(beat) % len(pool)]
    return pick_background(index, scene_keywords)


def safe_scene_keywords_for_beat(beat: dict[str, Any]) -> list[str]:
    local_text = f"{beat.get('caption', '')} {beat.get('intent', '')}"
    local_category = infer_theme_category(local_text)
    if is_food_scene(local_text):
        local_category = "street_food"
    if not local_category:
        local_category = infer_theme_category(str(beat.get("theme", "")))
    filtered: list[str] = []
    filtered.extend(background_anchors_for_local_category(local_category, str(beat.get("caption", "")), str(beat.get("role", "")), str(beat.get("theme", ""))))
    for keyword in beat.get("scene_keywords", []):
        text = str(keyword).strip()
        if not text:
            continue
        if scene_keyword_conflicts(text, local_category, local_text):
            continue
        filtered.append(text)
    if not filtered:
        filtered.extend(theme_background_anchors(str(beat.get("theme", "")), str(beat.get("caption", "")), str(beat.get("role", ""))))
    return list(dict.fromkeys(filtered))


def background_anchors_for_local_category(category: str, caption: str, role: str, theme: str) -> list[str]:
    proxy_theme = {
        "career": "求职 简历 面试 招聘 岗位",
        "leave": "请假 病假 老板 120 工位 办公室",
        "office": "上班 会议 加班 老板",
        "exam": "考研 考公 自习 图书馆",
        "rent": "租房 房租 押金 通勤",
        "street_food": theme if is_food_scene(theme) else "烤肠 摆摊 小吃摊 夜市",
    }.get(category, "")
    return theme_background_anchors(proxy_theme, caption, role) if proxy_theme else []


def local_beat_scene_category(beat: dict[str, Any], theme: str = "") -> str:
    local_text = f"{beat.get('caption', '')} {beat.get('intent', '')}"
    if is_food_scene(local_text):
        return "street_food"
    return infer_theme_category(local_text) or infer_theme_category(theme)


def scene_keyword_conflicts(keyword: str, local_category: str, local_text: str) -> bool:
    if is_food_scene(keyword) and local_category != "street_food":
        return True
    conflict_map = {
        "career": ("出租屋", "房租", "押金", "考研", "考公", "自习", "图书馆", "烤肠", "小吃摊", "夜市"),
        "leave": ("出租屋", "房租", "押金", "考研", "考公", "自习", "图书馆", "烤肠", "小吃摊", "夜市", "招聘会"),
        "office": ("出租屋", "房租", "押金", "考研", "考公", "自习", "图书馆", "烤肠", "小吃摊", "夜市"),
        "exam": ("招聘", "面试", "HR", "会议室", "工位", "房租", "押金", "烤肠", "小吃摊", "夜市"),
        "rent": ("招聘", "面试", "HR", "会议室", "考研", "考公", "自习", "图书馆", "烤肠", "小吃摊", "夜市"),
        "street_food": ("招聘", "面试", "HR", "会议室", "自习", "图书馆", "出租屋"),
    }
    if local_category in conflict_map and any(word in keyword for word in conflict_map[local_category]):
        return not any(word in local_text for word in ("转去", "想到", "改去", "摆摊", "烤肠", "夜市", "小吃摊"))
    return False


def scene_guard_score(category: str, asset: dict[str, Any], beat: dict[str, Any]) -> float:
    text = f"{asset.get('id', '')} {asset.get('scene', '')} {asset.get('file', '')} {asset.get('description', '')}"
    local_text = f"{beat.get('caption', '')} {beat.get('intent', '')}"
    if is_food_scene(text) and not is_food_scene(local_text) and category != "street_food":
        return -120.0
    if category == "career" and any(word in text for word in ("烤肠", "小吃摊", "夜市", "出租屋", "自习室", "图书馆")):
        return -80.0
    if category == "office" and any(word in text for word in ("烤肠", "小吃摊", "夜市", "出租屋", "自习室", "图书馆")):
        return -80.0
    if category == "leave" and any(word in text for word in ("烤肠", "小吃摊", "夜市", "出租屋", "自习室", "图书馆", "招聘会")):
        return -80.0
    if category == "exam" and any(word in text for word in ("招聘", "面试", "会议室", "办公室", "烤肠", "小吃摊", "出租屋")):
        return -80.0
    if category == "rent" and any(word in text for word in ("招聘", "面试", "会议室", "办公室", "考研", "考公", "烤肠", "小吃摊")):
        return -80.0
    return 0.0


def include_exact_background_candidates(
    index: dict[str, Any],
    candidates: list[dict[str, Any]],
    keywords: list[str],
) -> list[dict[str, Any]]:
    by_id = {str(asset.get("id", "")): asset for asset in index.get("backgrounds", [])}
    by_scene = {str(asset.get("scene", "")): asset for asset in index.get("backgrounds", [])}
    merged = list(candidates)
    seen = {str(asset.get("id", "")) for asset in merged}
    for keyword in keywords:
        asset = exact_background_asset(index, by_id, by_scene, str(keyword))
        if asset and str(asset.get("id", "")) not in seen:
            merged.append(asset)
            seen.add(str(asset.get("id", "")))
    return merged


def exact_background_asset(
    index: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    by_scene: dict[str, dict[str, Any]],
    keyword: str,
) -> dict[str, Any] | None:
    if not keyword:
        return None
    variants = [keyword]
    if keyword.startswith("generated/") and not keyword.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
        variants.extend([f"{keyword}.png", f"{keyword}.jpg", f"{keyword}.jpeg"])
    for variant in variants:
        asset = by_id.get(variant) or by_scene.get(variant)
        if asset:
            return asset
    for asset in index.get("backgrounds", []):
        text = f"{asset.get('id', '')} {asset.get('file', '')}"
        if any(text.endswith(variant) or variant in text for variant in variants):
            return asset
    return None


def theme_background_anchors(theme: str, caption: str, role: str) -> list[str]:
    category = infer_theme_category(theme)
    if category == "street_food":
        role_assets = {
            "hook": "generated/大学生工作难找-最后去学校门口卖烤肠也要内卷-hook-打开招聘软件那一/1780404735.png",
            "setup": "generated/大学生工作难找-最后去学校门口卖烤肠也要内卷-setup-投了100份简/1780404787.png",
            "pressure": "generated/大学生工作难找-最后去学校门口卖烤肠也要内卷-escalation-岗位/1780404825.png",
            "escalation": "generated/大学生工作难找-最后去学校门口卖烤肠也要内卷-escalation-岗位/1780404825.png",
            "twist": "generated/大学生工作难找-最后去学校门口卖烤肠也要内卷-punchline-猫先把/1780404871.png",
            "echo": "generated/大学生工作难找-最后去学校门口卖烤肠也要内卷-punchline-猫先把/1780404871.png",
            "punchline": "generated/大学生工作难找-最后去学校门口卖烤肠也要内卷-punchline-猫先把/1780404871.png",
        }
        return [
            role_assets.get(role, role_assets["hook"]),
            "street_food_stall",
            "烤肠摊",
            "小吃摊",
            "夜市摊位",
            "街边摊",
            "real_city",
            "street",
        ]
    if category == "rent":
        return [
            "generated/preset-rental-bill-room/1780413047.png",
            "rental_bill_room",
            "出租屋",
            "租房",
            "房租",
            "押金",
            "账单",
            "window",
            "building_interior",
            "real_transit_station" if any(word in f"{theme} {caption}" for word in ("通勤", "地铁", "公交")) else "window",
        ]
    if category == "exam":
        return [
            "generated/preset-exam-study-room/1780413328.png",
            "exam_study_room",
            "classroom",
            "real_school",
            "school",
            "street/indoor",
            "自习室",
            "图书馆",
            "课桌",
        ]
    if category == "office":
        return [
            "generated/preset-meeting-room-involution/1780412964.png",
            "meeting_room_involution",
            "real_office",
            "office",
            "会议室",
            "工位",
        ]
    if category == "leave":
        return [
            "real_office",
            "office",
            "工位",
            "老板聊天框",
            "请假审批",
            "hospital_alert",
            "120急救电话",
        ]
    if category == "career":
        return [
            "generated/preset-job-fair-waiting-area/1780413290.png",
            "job_fair_waiting_area",
            "real_office",
            "office",
            "real_school",
            "招聘会",
            "面试等待区",
            "简历",
        ]
    return []


def themed_background_score(category: str, asset: dict[str, Any], beat: dict[str, Any]) -> float:
    if not category:
        return 0.0
    text = f"{asset.get('id', '')} {asset.get('scene', '')} {asset.get('file', '')} {asset.get('description', '')}"
    score = 0.0
    positive = {
        "street_food": ("烤肠", "香肠", "小吃摊", "夜市", "摊车", "街边摊", "street_food_stall"),
        "rent": ("出租屋", "租房", "房租", "押金", "账单", "床铺", "行李箱", "building_interior", "window", "real_transit_station"),
        "exam": ("考研", "考公", "自习", "图书馆", "教室", "classroom", "real_school", "school", "study"),
        "office": ("会议", "加班", "复盘", "同步", "老板", "会议室", "real_office", "office"),
        "leave": ("请假", "病假", "老板", "工位", "办公室", "real_office", "office", "hospital_alert", "120"),
        "career": ("招聘", "简历", "面试", "HR", "校招", "岗位", "job-fair", "real_office", "office"),
    }.get(category, ())
    negative = {
        "street_food": ("招聘", "面试", "会议", "办公室", "自习", "图书馆", "出租屋"),
        "rent": ("招聘", "面试", "会议", "考研", "考公", "自习", "办公室"),
        "exam": ("招聘", "面试", "HR", "会议", "办公室", "房租", "押金", "烤肠", "小吃摊"),
        "office": ("烤肠", "小吃摊", "出租屋", "考研", "考公"),
        "leave": ("烤肠", "小吃摊", "出租屋", "考研", "考公", "招聘会"),
        "career": ("烤肠", "小吃摊", "出租屋", "考研", "考公"),
    }.get(category, ())
    for keyword in positive:
        if keyword in text:
            score += 3.0
    for keyword in negative:
        if keyword in text:
            score -= 4.0
    if f"preset-{category}" in text or (category == "exam" and "preset-exam" in text) or (category == "rent" and "preset-rental" in text):
        score += 8.0
    if category == "street_food" and "generated/大学生工作难找-最后去学校门口卖烤肠" in text:
        score += 10.0
        if beat.get("role") in text:
            score += 2.0
    return score


def needs_food_stall_background(beat: dict[str, Any]) -> bool:
    return local_beat_scene_category(beat, str(beat.get("theme", ""))) == "street_food"


def scene_keywords_for_beat(theme: str, caption: str, role: str, script_scenes: list[str]) -> list[str]:
    joined = f"{theme} {caption}"
    local_joined = caption
    specific_scene_terms = {"street_food_stall", "烤肠摊", "小吃摊", "夜市摊位", "餐车", "街边摊"}
    theme_has_specific_scene = any(word in theme for word in ("烤肠", "香肠", "摆摊", "小吃摊", "夜市", "地摊", "餐车"))
    local_needs_specific_scene = any(word in local_joined for word in ("烤肠", "香肠", "摆摊", "摊子", "小吃摊", "夜市", "地摊", "餐车"))
    if role in {"punchline", "cta"} and theme_has_specific_scene:
        local_needs_specific_scene = True

    keywords = [*theme_background_anchors(theme, caption, role)]
    keywords.extend(
        str(scene)
        for scene in script_scenes
        if str(scene) not in specific_scene_terms or local_needs_specific_scene
    )
    preset_query = local_joined if local_joined.strip() else joined
    for preset in matching_preset_scenes(preset_query, limit=3):
        keywords.extend(str(item) for item in preset.get("keywords", [])[:8])
        keywords.extend(str(item) for item in preset.get("recommended_backgrounds", [])[:4])
    specific_mapping = {
        ("烤肠", "香肠", "摆摊", "摊子", "小吃摊", "夜市", "地摊", "餐车"): ["street_food_stall", "烤肠", "香肠", "烤肠摊", "小吃摊", "夜市摊位", "餐车", "街边摊", "real_street"],
    }
    for triggers, values in specific_mapping.items():
        if local_needs_specific_scene and any(trigger in joined for trigger in triggers):
            keywords.extend(values)
    mapping = {
        ("简历", "招聘", "岗位", "面试", "HR"): ["office", "real_office", "招聘会", "办公楼"],
        ("请假", "病假", "120", "急救", "审批"): ["office", "real_office", "工位", "请假审批", "hospital_alert", "120急救电话"],
        ("上班", "加班", "会议", "老板", "KPI"): ["office", "real_office", "会议室", "工位"],
        ("考研", "考公", "自习", "图书馆", "上岸"): ["classroom", "real_school", "图书馆", "自习室"],
        ("租房", "房租", "押金", "中介"): ["building_interior", "real_city", "出租屋", "中介门店"],
        ("通勤", "地铁", "公交", "高铁"): ["real_transit_station", "地铁", "公交车", "站台"],
    }
    for triggers, values in mapping.items():
        if any(trigger in local_joined for trigger in triggers) or (not local_joined.strip() and any(trigger in joined for trigger in triggers)):
            keywords.extend(values)
    if not keywords:
        keywords.extend(["city", "street", "real_city"])
    return list(dict.fromkeys(str(item) for item in keywords if str(item).strip()))


def emotion_keywords_for_role(role: str, caption: str, script: dict[str, Any]) -> list[str]:
    base = {
        "hook": ["震惊", "瞪圆", "探头", "叫嚷"],
        "setup": ["电脑", "冷漠", "开车", "碎碎念"],
        "pressure": ["电脑", "冷漠", "委屈", "生无可恋"],
        "proof": ["探头", "冷漠", "碎碎念", "电脑"],
        "twist": ["震惊", "探头", "疯狂", "错愕"],
        "echo": ["委屈", "哭", "探头", "冷漠"],
        "escalation": ["哭", "委屈", "疯狂", "嚎啕"],
        "punchline": ["蹦跳", "跳舞", "欢快", "可爱", "演奏"],
        "cta": ["蹦跳", "欢快", "可爱", "跳舞"],
    }.get(role, [])
    return list(dict.fromkeys(base + [caption] + script.get("emotion", [])))


def must_keywords_for_caption(caption: str, theme: str) -> list[str]:
    keywords = []
    mapping = {
        "电脑": ["电脑", "笔记本"],
        "会议": ["电脑", "碎碎念", "生无可恋"],
        "周会": ["电脑", "笔记本"],
        "复盘": ["电脑", "笔记本"],
        "开车": ["开车", "方向盘"],
        "作业": ["探头", "震惊"],
        "卖萌": ["可爱", "蹦跳", "欢快"],
        "加班": ["哭", "委屈", "电脑"],
    }
    joined = f"{caption} {theme}"
    for trigger, values in mapping.items():
        if trigger in joined:
            keywords.extend(values)
    return list(dict.fromkeys(keywords))


def role_must_keywords(role: str, caption: str, theme: str) -> list[str]:
    if role == "setup":
        return must_keywords_for_caption(caption, theme)
    if role == "escalation":
        if any(word in f"{caption} {theme}" for word in ("会", "加班", "下午", "晚上", "崩溃", "玄学")):
            return ["哭", "委屈", "嚎啕", "疯狂"]
    if role == "punchline":
        if any(word in f"{caption} {theme}" for word in ("装可爱", "卖萌", "逃过", "下班", "复活")):
            return ["可爱", "蹦跳", "欢快", "跳舞"]
    return must_keywords_for_caption(caption, theme)


def forbidden_keywords_for_context(script: dict[str, Any], theme: str) -> list[str]:
    forbidden = []
    scenes = set(script.get("scene", []))
    if ("office" in scenes or "real_office" in scenes) and "开车" not in theme:
        forbidden.extend(["开车", "方向盘"])
    if "real_car_interior" not in scenes:
        forbidden.extend(["小狗", "山羊"])
    return list(dict.fromkeys(forbidden))


def polish_caption(role: str, caption: str, theme: str) -> str:
    captions = {
        "hook": caption,
        "setup": caption,
        "escalation": caption,
        "punchline": caption,
    }
    text = captions.get(role, caption).replace("，", " ")
    if len(text) <= 13:
        return text
    return text[:12] + "..."


def fallback_motion_id(role: str) -> str:
    return {"hook": "15", "setup": "1", "pressure": "1", "proof": "16", "twist": "15", "echo": "9", "escalation": "9", "punchline": "13", "cta": "2"}.get(role, "2")


def asset_match_score(asset: dict[str, Any], keywords: list[str]) -> float:
    text = f"{asset.get('id', '')} {asset.get('scene', '')} {asset.get('file', '')} {asset.get('description', '')}"
    return sum(1.0 for keyword in keywords if keyword and keyword in text)


def validate_match(beat: dict[str, Any], motion: dict[str, Any], background: dict[str, Any], motion_score: float, background_score: float) -> dict[str, str]:
    desc = str(motion.get("description", ""))
    forbidden_hit = [keyword for keyword in beat.get("forbidden_keywords", []) if keyword in desc]
    must_hit = not beat.get("must_keywords") or any(keyword in desc for keyword in beat.get("must_keywords", []))
    if forbidden_hit:
        return {"status": "supplemented", "strategy": "structure_reorder", "reason": f"猫动作含有不适合该场景的元素：{','.join(forbidden_hit)}。"}
    if not must_hit:
        return {"status": "supplemented", "strategy": "subtitle_card", "reason": "猫动作情绪可用，但没有直接命中文案里的硬动作，需要字幕补语义。"}
    if motion_score >= 1 and background_score >= 1:
        return {"status": "matched", "strategy": "direct_match", "reason": "猫动作和背景都命中分镜关键词。"}
    if motion_score >= 1:
        return {"status": "supplemented", "strategy": "subtitle_card", "reason": "猫动作贴合，但背景只能表达泛场景，需用字幕补足语境。"}
    if background_score >= 1:
        return {"status": "supplemented", "strategy": "reuse_crop_zoom", "reason": "背景贴合，但猫动作需要通过裁切/重复放大强化情绪。"}
    return {"status": "supplemented", "strategy": "structure_reorder", "reason": "素材没有直接命中，改用更通用的猫表情表达该剧情点。"}


def packaging_for_gap(role: str, gap: dict[str, str]) -> list[str]:
    packaging = ["large_caption" if role in {"hook", "escalation"} else "bottom_subtitle"]
    if gap["strategy"] in {"reuse_crop_zoom", "structure_reorder"}:
        packaging.append("zoom")
    packaging.append("freeze_end" if role == "punchline" else "quick_cut")
    return packaging


def pattern_for_role(role: str) -> str:
    return {
        "hook": "爆款开头：2秒内强情绪/强字幕",
        "setup": "爆款中段：场景化冲突",
        "pressure": "爆款中段：现实压力具体化",
        "proof": "爆款中段：事实/群体证据补强",
        "twist": "爆款转折：荒诞反差",
        "echo": "爆款共鸣：从个体扩到群体",
        "escalation": "爆款高潮：重复、夸张、情绪升级",
        "punchline": "爆款结尾：反转或记忆点收束",
        "cta": "爆款尾声：轻 CTA/情绪回落",
    }.get(role, "爆款结构槽位")


def pattern_for_beat(beat: dict[str, Any]) -> str:
    source_shot = beat.get("source_viral_shot") if isinstance(beat.get("source_viral_shot"), dict) else None
    if source_shot and source_shot.get("viral_title"):
        prefix = "上传爆款" if source_shot.get("source") == "uploaded_viral" else "爆款参考"
        details = " / ".join(
            str(item)
            for item in [source_shot.get("beat"), source_shot.get("joke_point"), source_shot.get("transfer_role")]
            if item
        )
        return f"{prefix}《{source_shot.get('viral_title')}》镜头{source_shot.get('shot_id') or ''}迁移：{details[:80]}"
    uploaded = beat.get("uploaded_viral_reference") if isinstance(beat.get("uploaded_viral_reference"), dict) else None
    if uploaded and uploaded.get("title"):
        details = " / ".join(
            str(item)
            for item in [uploaded.get("beat"), uploaded.get("script")]
            if item
        )
        return f"上传爆款《{uploaded.get('title')}》迁移：{details[:80] or pattern_for_role(beat['role'])}"
    viral = beat.get("viral_reference") if isinstance(beat.get("viral_reference"), dict) else None
    if viral and viral.get("title"):
        details = " / ".join(
            str(item)
            for item in [viral.get("beat"), viral.get("joke_point")]
            if item
        )
        return f"爆款参考《{viral.get('title')}》：{details or pattern_for_role(beat['role'])}"
    return pattern_for_role(beat["role"])


def asset_sources_for_slot(slot: dict[str, Any]) -> dict[str, str]:
    motion_id = str(slot.get("motion", {}).get("id", ""))
    secondary_id = str((slot.get("secondary_motion") or {}).get("id", ""))
    background_id = str(slot.get("background", {}).get("id", ""))
    return {
        "motion": "user_upload" if motion_id.startswith("user/") else "built_in",
        "secondary_motion": "user_upload" if secondary_id.startswith("user/") else "built_in" if secondary_id else "",
        "background": "user_upload" if background_id.startswith("user/") else "generated" if slot.get("background_source") == "generated" else "built_in",
        "structure": "uploaded_viral" if "上传爆款" in str(slot.get("source_pattern", "")) else "viral_library" if "爆款参考" in str(slot.get("source_pattern", "")) else "theme_workflow",
    }


def material_needs_from_timeline(timeline: list[dict[str, Any]]) -> dict[str, Any]:
    missing = [f"{slot['role']}:{slot['gap']['reason']}" for slot in timeline if slot["gap"]["status"] != "matched"]
    return {
        "covered": [slot["role"] for slot in timeline if slot["gap"]["status"] == "matched"],
        "missing": missing,
        "supplement_strategy": list(dict.fromkeys(slot["gap"]["strategy"] for slot in timeline if slot["gap"]["status"] != "matched")),
    }


def save_plan(plan: MaoMemePlan) -> Path:
    out_dir = get_settings().OUTPUT_DIR / "plans"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{plan.id}.json"
    out.write_text(plan.model_dump_json(indent=2, by_alias=True), encoding="utf-8")
    latest = get_settings().PROJECT_ROOT / "data" / "runs" / "latest-backend-plan.json"
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(plan.model_dump_json(indent=2, by_alias=True), encoding="utf-8")
    return out


def fallback_source_text() -> str:
    return "默认结构：强 hook → 场景冲突 → 情绪升级 → 反转收束；节奏为 2-3 秒一切，字幕大字包装。"


def _source_payload(source_structure: VideoStructure | None) -> dict[str, Any]:
    if not source_structure:
        return {"sample_status": "template"}
    return {
        "sample_status": source_structure.analysis_evidence.get("provider", "analyzed"),
        "id": source_structure.id,
        "meta": source_structure.meta.model_dump(),
        "script_count": len(source_structure.script_structure),
        "shot_count": len(source_structure.shots),
        "transferable_features": source_structure.transferable_features.model_dump(),
    }


def _theme_has(theme: str, words: list[str]) -> bool:
    return any(word in theme for word in words)


def _setup_copy(theme: str) -> str:
    if len(theme) <= 15:
        return theme
    return theme[:14] + "..."
