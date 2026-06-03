from __future__ import annotations

import json
import asyncio
import time
from pathlib import Path
from typing import Any

from ..core.config import get_settings
from ..models.maomeme import MaoMemePlan, ScriptCandidate
from ..models.video_structure import VideoStructure
from .agent_tools import (
    asset_search_tool,
    background_fill_tool,
    clip_planner_tool,
    overlay_planner_tool,
    transition_planner_tool,
)
from .asset_index import assets_summary, load_assets, pick_background, pick_motion, rank_assets, ref
from .doubao_client import (
    ark_available,
    generate_candidates_with_doubao_context,
    generate_plan_with_doubao_context,
    stream_candidates_with_doubao_context,
)
from .text_materials import matching_preset_scenes, topic_for_agent
from .viral_structure_library import (
    viral_reference_notes,
    viral_reference_prompt,
    viral_references_for_theme,
    viral_template_seed,
)
from .video_analyzer import analyze_video_structure, source_summary


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
) -> list[ScriptCandidate]:
    index = load_assets()
    text_context = topic_for_agent(theme)
    viral_refs = viral_references_for_theme(theme, text_context)
    scripts = []
    provider_note = "local_fallback"
    if use_doubao and ark_available():
        scripts = await generate_doubao_candidate_scripts_parallel(
            theme=theme,
            assets_text=assets_summary(index),
            text_context=text_context,
            duration_mode=normalize_duration_mode(duration_mode),
            viral_reference_text=viral_reference_prompt(viral_refs),
        )
        provider_note = "doubao_agent" if scripts else "doubao_parse_failed_fallback"
    if not scripts:
        scripts = screenwriter_agent(theme, text_context, viral_refs)
    scored = [(score_script(script, theme, index), script) for script in scripts]
    scored.sort(key=lambda item: item[0], reverse=True)
    candidates = [
        script_to_candidate(script, theme, score, text_context, idx + 1, duration_mode)
        for idx, (score, script) in enumerate(scored[:3])
    ]
    for candidate in candidates:
        candidate.notes.insert(0, f"生成来源：{provider_note}")
        for note in reversed(viral_reference_notes(viral_refs)):
            candidate.notes.insert(1, note)
    while len(candidates) < 3:
        base = scripts[0] if scripts else screenwriter_agent(theme, {}, viral_refs)[0]
        variant = json.loads(json.dumps(base, ensure_ascii=False))
        variant["name"] = f"{base.get('name', '候选')}·变体{len(candidates) + 1}"
        variant["beats"] = revise_beats_for_instruction(variant.get("beats", []), "更轻松一点")
        fallback_candidate = script_to_candidate(variant, theme, score_script(variant, theme, index) - len(candidates), text_context, len(candidates) + 1, duration_mode)
        fallback_candidate.notes.insert(0, f"生成来源：{provider_note}")
        for note in reversed(viral_reference_notes(viral_refs)):
            fallback_candidate.notes.insert(1, note)
        candidates.append(fallback_candidate)
    return candidates


async def stream_script_candidates(
    theme: str,
    sample_video_path: str | None = None,
    use_doubao: bool = True,
    duration_mode: str = "short",
):
    index = load_assets()
    text_context = topic_for_agent(theme)
    mode = normalize_duration_mode(duration_mode)
    viral_refs = viral_references_for_theme(theme, text_context)

    if not use_doubao:
        yield {"type": "stage", "message": "测试模式：使用本地预设候选", "progress": 0.18}
        candidates = build_script_candidates(
            scripts=screenwriter_agent(theme, text_context, viral_refs),
            theme=theme,
            index=index,
            text_context=text_context,
            duration_mode=mode,
            provider_note="local_fallback_explicit",
            viral_refs=viral_refs,
        )
        yield {"type": "final", "candidates": candidates, "provider_note": "local_fallback_explicit"}
        return

    if not ark_available():
        yield {"type": "stage", "message": "Doubao 未配置，回退本地候选", "progress": 0.18}
        candidates = build_script_candidates(
            scripts=screenwriter_agent(theme, text_context, viral_refs),
            theme=theme,
            index=index,
            text_context=text_context,
            duration_mode=mode,
            provider_note="local_fallback_no_ark",
            viral_refs=viral_refs,
        )
        yield {"type": "final", "candidates": candidates, "provider_note": "local_fallback_no_ark"}
        return

    yield {"type": "stage", "message": "正在组织剧本结构，约束冲突走向", "progress": 0.42}

    raw_result: dict[str, Any] | None = None
    content_size = 0
    async for event in stream_candidates_with_doubao_context(theme, assets_summary(index), text_context, mode, viral_reference_text=viral_reference_prompt(viral_refs)):
        if event["type"] == "delta":
            text = event.get("text", "")
            content_size += len(text)
            yield {
                "type": "agent_delta",
                "text": text,
                "message": "Doubao Agent 正在协调生成 3 个差异化候选",
                "progress": min(0.82, 0.28 + content_size / 1800),
            }
        elif event["type"] == "final":
            raw_result = event.get("raw") if isinstance(event.get("raw"), dict) else None

    scripts = normalize_doubao_candidate_scripts(raw_result or {})
    provider_note = "doubao_agent_stream"
    if not scripts:
        provider_note = "doubao_stream_parse_failed_fallback"
        scripts = screenwriter_agent(theme, text_context, viral_refs)

    candidates = build_script_candidates(
        scripts=scripts,
        theme=theme,
        index=index,
        text_context=text_context,
        duration_mode=mode,
        provider_note=provider_note,
        viral_refs=viral_refs,
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
) -> list[dict[str, Any]]:
    scripts: list[dict[str, Any]] = []
    async for event in stream_doubao_candidate_scripts_parallel(theme, assets_text, text_context, duration_mode, viral_reference_text):
        if event["type"] == "script":
            scripts.append(event["script"])
    return dedupe_scripts(scripts)


async def stream_doubao_candidate_scripts_parallel(
    theme: str,
    assets_text: str,
    text_context: dict[str, Any],
    duration_mode: str,
    viral_reference_text: str = "",
):
    settings = get_settings()
    semaphore = asyncio.Semaphore(settings.ARK_AGENT_CONCURRENCY)

    async def run_angle(position: int, angle: str) -> dict[str, Any]:
        async with semaphore:
            raw = await generate_candidates_with_doubao_context(
                theme=theme,
                assets_summary=assets_text,
                text_context=text_context,
                duration_mode=duration_mode,
                angle=angle,
                viral_reference_text=viral_reference_text,
            )
        scripts = normalize_doubao_candidate_scripts(raw)
        return {"position": position, "angle": angle, "scripts": scripts}

    tasks = [asyncio.create_task(run_angle(index, angle)) for index, angle in enumerate(candidate_angles(theme, text_context), start=1)]
    completed = 0
    for task in asyncio.as_completed(tasks):
        try:
            result = await task
        except Exception:
            completed += 1
            yield {
                "type": "stage",
                "message": f"第 {completed}/3 路编剧 Agent 未返回可用结果，继续等待其他方向",
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
                "progress": min(0.82, 0.48 + completed * 0.1),
            }
        else:
            yield {
                "type": "stage",
                "message": f"第 {completed}/3 路编剧 Agent 结果需要回退清洗",
                "progress": min(0.78, 0.48 + completed * 0.08),
            }


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


def build_script_candidates(
    scripts: list[dict[str, Any]],
    theme: str,
    index: dict[str, Any],
    text_context: dict[str, Any],
    duration_mode: str,
    provider_note: str,
    viral_refs: list[dict[str, Any]] | None = None,
) -> list[ScriptCandidate]:
    if not scripts:
        scripts = screenwriter_agent(theme, text_context, viral_refs)
    scored = [(score_script(script, theme, index), script) for script in scripts]
    scored.sort(key=lambda item: item[0], reverse=True)
    candidates = [
        script_to_candidate(script, theme, score, text_context, idx + 1, duration_mode)
        for idx, (score, script) in enumerate(scored[:3])
    ]
    for candidate in candidates:
        candidate.notes.insert(0, f"生成来源：{provider_note}")
        for note in reversed(viral_reference_notes(viral_refs or [])):
            candidate.notes.insert(1, note)
    while len(candidates) < 3:
        base = scripts[0] if scripts else screenwriter_agent(theme, {}, viral_refs)[0]
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
        for note in reversed(viral_reference_notes(viral_refs or [])):
            fallback_candidate.notes.insert(1, note)
        candidates.append(fallback_candidate)
    return candidates


async def plan_from_candidate(
    theme: str,
    candidate: ScriptCandidate,
    sample_video_path: str | None = None,
    use_doubao: bool = True,
    duration_mode: str = "short",
) -> MaoMemePlan:
    index = load_assets()
    source_structure = await _source_structure(sample_video_path, use_doubao, prefer_fast=sample_video_path is None)
    text_context = topic_for_agent(theme)
    viral_refs = viral_references_for_theme(theme, text_context)
    script = candidate_to_script(candidate)
    mode = normalize_duration_mode(duration_mode)
    script["beats"] = expand_beats_for_duration(script.get("beats", []), mode, theme, text_context)
    beats = director_agent(script, theme, mode)
    apply_viral_patterns_to_beats(beats, candidate, viral_refs)
    timeline, notes = casting_and_validator_agents(beats, theme, index)
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
            *viral_reference_notes(viral_refs),
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
):
    index = load_assets()
    yield {"type": "stage", "message": "素材索引已读取，正在拆解剧本", "progress": 0.08}
    text_context = topic_for_agent(theme)
    viral_refs = viral_references_for_theme(theme, text_context)
    script = candidate_to_script(candidate)
    mode = normalize_duration_mode(duration_mode)
    script["beats"] = expand_beats_for_duration(script.get("beats", []), mode, theme, text_context)
    beats = director_agent(script, theme, mode)
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
    async for event in casting_and_validator_agents_stream(beats, theme, index, progress_start=0.25, progress_span=0.62):
        if event.get("type") == "slot" and event.get("slot"):
            timeline.append(event["slot"])
        if event.get("type") == "notes":
            notes.extend(event.get("notes", []))
        yield event
    source_structure = await source_task
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
            *viral_reference_notes(viral_refs),
            *context_notes(text_context),
            *notes,
        ],
    )
    save_plan(plan)
    yield {"type": "done", "message": "分镜时间线生成完成", "progress": 1.0, "plan": plan.model_dump(by_alias=True)}


async def suggest_revisions(theme: str, plan: MaoMemePlan | None = None, candidate: ScriptCandidate | None = None, duration_mode: str = "short") -> list[str]:
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
    return suggestions[:4]


async def revise_maomeme(
    theme: str,
    instruction: str,
    plan: MaoMemePlan | None = None,
    candidate: ScriptCandidate | None = None,
    use_doubao: bool = True,
    duration_mode: str = "short",
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
    revised_plan = await plan_from_candidate(theme, revised_candidate, use_doubao=use_doubao, duration_mode=mode)
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
        },
        notes=context_notes(text_context),
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
            "beats": beats,
            "scene": list_or_empty(item.get("scene")),
            "theme_keywords": list_or_empty(item.get("theme_keywords")),
            "emotion": list_or_empty(item.get("emotion")),
            "social_topic": str(item.get("social_topic") or ""),
            "tension": str(item.get("tension") or ""),
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
        return normalized[:target]

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
    if any(word in theme for word in ("工作", "岗位", "简历", "面试", "就业")):
        return "猫先把规则看明白"
    if any(word in theme for word in ("上班", "加班", "会议", "内卷", "KPI")):
        return "猫把在吗设成免打扰"
    if any(word in theme for word in ("考研", "考公", "上岸", "考试")):
        return "猫决定先选一条路"
    if any(word in theme for word in ("结婚", "彩礼", "买房", "房")):
        return "猫先学会好好谈条件"
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
    candidates = screenwriter_agent(theme, text_context, viral_refs)
    scored = [(score_script(candidate, theme, index), candidate) for candidate in candidates]
    scored.sort(key=lambda item: item[0], reverse=True)
    script = scored[0][1]
    beats = director_agent(script, theme)
    selected_candidate = script_to_candidate(script, theme, scored[0][0], text_context, 1)
    apply_viral_patterns_to_beats(beats, selected_candidate, viral_refs)
    timeline, notes = casting_and_validator_agents(beats, theme, index)
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
) -> list[dict[str, Any]]:
    theme_short = _setup_copy(theme)
    viral_scripts = [viral_template_seed(ref, theme) for ref in (viral_refs or [])[:1]]
    if text_context:
        contextual = contextual_scripts(theme, text_context)
        if contextual:
            return [*viral_scripts, *contextual][:3] if viral_scripts else contextual
    office = _theme_has(theme, ["上班", "打工", "会议", "电脑", "老板", "加班"])
    school = _theme_has(theme, ["学校", "教室", "考试", "作业", "同学"])
    car = _theme_has(theme, ["开车", "堵车", "车里", "路上"])
    if office:
        return [*viral_scripts, *[
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
        return [*viral_scripts, *[
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
        return [*viral_scripts, *[
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
    return [*viral_scripts, *fallback][:3]


def contextual_scripts(theme: str, topic: dict[str, Any]) -> list[dict[str, Any]]:
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
                "intent": intent,
                "caption": polish_caption(role, caption, theme),
                "scene_keywords": scene_keywords_for_beat(theme, caption, role, script.get("scene", [])),
                "emotion_keywords": emotion_keywords_for_role(role, caption, script),
                "must_keywords": role_must_keywords(role, caption, theme),
                "forbidden_keywords": forbidden_keywords_for_context(script, theme),
                "layout": layout_for_role(role, caption),
                "dialogue": dialogue_for_beat(role, caption, theme),
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
        for key, field in (("background", "scene_keywords"), ("cats", "emotion_keywords")):
            text = str(source_shot.get(key, ""))
            if text:
                beat[field] = list(dict.fromkeys([*beat.get(field, []), text]))


def layout_for_role(role: str, caption: str) -> str:
    if role in {"setup", "pressure", "twist", "punchline"}:
        return "dialogue"
    if any(word in caption for word in ("老板", "同学", "HR", "面试官", "公司", "老师")):
        return "dialogue"
    return "single"


def dialogue_for_beat(role: str, caption: str, theme: str) -> list[dict[str, str]]:
    if layout_for_role(role, caption) != "dialogue":
        return []
    text = f"{caption} {theme}"
    if any(word in text for word in ("工作", "简历", "岗位", "面试")):
        pairs = {
            "setup": ("猫：我投了100份", "HR：先要3年经验"),
            "pressure": ("猫：岗位好多", "同学：敢投的好少"),
            "twist": ("猫：是我不行吗", "同学：是规则太绕"),
            "punchline": ("猫：先翻译规则", "同学：再换投法"),
        }
    elif any(word in text for word in ("上班", "会议", "加班", "老板")):
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
    actions: list[dict[str, Any]] = []
    if role == "setup" and any(word in caption for word in ("简历", "招聘", "岗位")):
        actions.append({
            "type": "throw_object",
            "object": "resume_stack",
            "from": "left_cat",
            "to": "right_cat",
            "start": 0.8,
            "duration": 1.2,
            "text": "简历 x100",
        })
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


def casting_and_validator_agents(beats: list[dict[str, Any]], theme: str, index: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    notes = []
    timeline = []
    previous_slot: dict[str, Any] | None = None
    used_motion_ids: set[str] = set()
    used_background_ids: set[str] = set()
    for beat in beats:
        slot, slot_notes = build_timeline_slot(beat, theme, index, previous_slot, used_motion_ids, used_background_ids)
        notes.extend(slot_notes)
        timeline.append(slot)
        previous_slot = slot
        remember_slot_assets(slot, used_motion_ids, used_background_ids)
    return timeline, notes


async def casting_and_validator_agents_stream(
    beats: list[dict[str, Any]],
    theme: str,
    index: dict[str, Any],
    progress_start: float = 0.25,
    progress_span: float = 0.6,
):
    notes: list[str] = []
    previous_slot: dict[str, Any] | None = None
    used_motion_ids: set[str] = set()
    used_background_ids: set[str] = set()
    total = max(1, len(beats))
    settings = get_settings()
    semaphore = asyncio.Semaphore(settings.STORYBOARD_MATCH_CONCURRENCY)

    async def prebuild(beat: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        async with semaphore:
            return await asyncio.to_thread(build_timeline_slot, beat, theme, index, None, set(), set())

    tasks = [asyncio.create_task(prebuild(beat)) for beat in beats]
    yield {
        "type": "stage",
        "message": f"并行预匹配 {len(tasks)} 个镜头素材",
        "progress": round(progress_start, 3),
    }

    for index_num, beat in enumerate(beats):
        slot, slot_notes = await tasks[index_num]
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
        progress = progress_start + progress_span * ((index_num + 1) / total)
        yield {
            "type": "slot",
            "message": f"已匹配镜头 {index_num + 1}/{total}：{beat['caption']}",
            "progress": round(progress, 3),
            "slot": slot,
        }
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
    secondary_motion = choose_secondary_motion_for_beat(index, beat, motion, used_motion_ids)
    background = choose_background_for_beat(index, beat, used_background_ids)
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
        "motion_clip": clip_planner_tool(motion, beat, beat["end"] - beat["start"]),
        "secondary_motion": ref(secondary_motion) if beat["layout"] == "dialogue" else None,
        "secondary_motion_clip": clip_planner_tool(secondary_motion, beat, beat["end"] - beat["start"]) if beat["layout"] == "dialogue" else None,
        "background": ref(background),
        "background_source": background_source,
        "background_prompt": background_prompt if background_source in {"generated", "generated_pending"} else "",
        "transition": transition_planner_tool(beat, previous_slot, background_changed),
        "layout": beat["layout"],
        "dialogue": beat["dialogue"],
        "overlay_actions": overlay_planner_tool(beat, motion, background),
        "gap": gap,
        "packaging": packaging_for_gap(beat["role"], gap),
        "source_pattern": pattern_for_beat(beat),
    }
    return slot, slot_notes


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
        (motion_id and motion_id in used_motion_ids)
        or (secondary_id and secondary_id in used_motion_ids)
        or (background_id and background_id in used_background_ids)
    )


def choose_motion_for_beat(index: dict[str, Any], beat: dict[str, Any], used_ids: set[str] | None = None) -> dict[str, Any]:
    used_ids = used_ids or set()
    candidates = asset_search_tool(index, "motion", beat["emotion_keywords"] + beat.get("must_keywords", []), limit=16)
    scored: list[tuple[float, dict[str, Any]]] = []
    for asset in candidates:
        desc = str(asset.get("description", ""))
        score = asset_match_score(asset, beat["emotion_keywords"])
        for keyword in beat.get("must_keywords", []):
            if keyword in desc:
                score += 4.0
        for keyword in beat.get("forbidden_keywords", []):
            if keyword in desc:
                score -= 8.0
        if beat["role"] == "setup" and any(word in desc for word in ("电脑", "方向盘", "开车")):
            score += 2.0
        if beat["role"] == "punchline" and any(word in desc for word in ("欢快", "跳舞", "蹦跳", "可爱")):
            score += 2.0
        if str(asset.get("id", "")) in used_ids:
            score -= 1.8
        scored.append((score, asset))
    scored.sort(key=lambda item: (-item[0], str(item[1].get("id", ""))))
    viable = [(score, asset) for score, asset in scored if score > 0]
    if viable:
        top_score = viable[0][0]
        pool = [asset for score, asset in viable if score >= top_score - 1.5][:5]
        return pool[selection_offset(beat) % len(pool)]
    return pick_motion(index, beat["emotion_keywords"], fallback_id=fallback_motion_id(beat["role"]))


def choose_secondary_motion_for_beat(index: dict[str, Any], beat: dict[str, Any], primary: dict[str, Any], used_ids: set[str] | None = None) -> dict[str, Any]:
    if beat.get("layout") != "dialogue":
        return primary
    used_ids = used_ids or set()
    candidates = index.get("cat_motions", [])
    keywords = ["冷漠", "探头", "碎碎念", "可爱", "震惊", "电脑"]
    best: tuple[float, dict[str, Any]] | None = None
    for asset in candidates:
        if str(asset.get("id")) == str(primary.get("id")):
            continue
        score = asset_match_score(asset, keywords)
        if str(asset.get("id", "")) in used_ids:
            score -= 1.2
        if best is None or score > best[0]:
            best = (score, asset)
    return best[1] if best else pick_motion(index, keywords, fallback_id="16")


def choose_background_for_beat(index: dict[str, Any], beat: dict[str, Any], used_ids: set[str] | None = None) -> dict[str, Any]:
    used_ids = used_ids or set()
    candidates = asset_search_tool(index, "background", beat["scene_keywords"] + [beat.get("caption", "")], limit=12)
    candidates = include_exact_background_candidates(index, candidates, beat["scene_keywords"])
    scored: list[tuple[float, dict[str, Any]]] = []
    for asset in candidates:
        score = asset_match_score(asset, beat["scene_keywords"])
        score += asset_match_score(asset, [beat.get("caption", ""), beat.get("intent", "")]) * 0.4
        asset_id = str(asset.get("id", ""))
        asset_scene = str(asset.get("scene", ""))
        for keyword in beat["scene_keywords"]:
            if keyword and (keyword == asset_id or keyword == asset_scene):
                score += 24.0 if "generated/preset-" in asset_id else 8.0
            elif keyword and (asset_id.startswith(keyword) or keyword.startswith(asset_id)):
                score += 12.0 if "generated/preset-" in asset_id else 4.0
            if keyword and keyword in str(asset.get("file", "")):
                score += 1.2
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
    if scored:
        top_score = scored[0][0]
        pool = [asset for score, asset in scored if score >= top_score - 0.5][:4] or [scored[0][1]]
        return pool[selection_offset(beat) % len(pool)]
    return pick_background(index, beat["scene_keywords"])


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
        asset = by_id.get(str(keyword)) or by_scene.get(str(keyword))
        if asset and str(asset.get("id", "")) not in seen:
            merged.append(asset)
            seen.add(str(asset.get("id", "")))
    return merged


def needs_food_stall_background(beat: dict[str, Any]) -> bool:
    text = f"{beat.get('caption', '')} {beat.get('intent', '')} {' '.join(str(item) for item in beat.get('scene_keywords', []))}"
    return any(word in text for word in ("烤肠", "香肠", "摆摊", "小吃摊", "夜市", "街边摊", "餐车"))


def scene_keywords_for_beat(theme: str, caption: str, role: str, script_scenes: list[str]) -> list[str]:
    joined = f"{theme} {caption}"
    local_joined = caption
    specific_scene_terms = {"street_food_stall", "烤肠摊", "小吃摊", "夜市摊位", "餐车", "街边摊"}
    theme_has_specific_scene = any(word in theme for word in ("烤肠", "香肠", "摆摊", "小吃摊", "夜市", "地摊", "餐车"))
    local_needs_specific_scene = any(word in local_joined for word in ("烤肠", "香肠", "摆摊", "摊子", "小吃摊", "夜市", "地摊", "餐车"))
    if role in {"punchline", "cta"} and theme_has_specific_scene:
        local_needs_specific_scene = True

    keywords = [
        str(scene)
        for scene in script_scenes
        if str(scene) not in specific_scene_terms or local_needs_specific_scene
    ]
    for preset in matching_preset_scenes(joined, limit=3):
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
        ("上班", "加班", "会议", "老板", "KPI"): ["office", "real_office", "会议室", "工位"],
        ("考研", "考公", "自习", "图书馆", "上岸"): ["classroom", "real_school", "图书馆", "自习室"],
        ("租房", "房租", "押金", "中介"): ["building_interior", "real_city", "出租屋", "中介门店"],
        ("通勤", "地铁", "公交", "高铁"): ["real_transit_station", "地铁", "公交车", "站台"],
    }
    for triggers, values in mapping.items():
        if any(trigger in joined for trigger in triggers):
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
    viral = beat.get("viral_reference") if isinstance(beat.get("viral_reference"), dict) else None
    if viral and viral.get("title"):
        details = " / ".join(
            str(item)
            for item in [viral.get("beat"), viral.get("joke_point")]
            if item
        )
        return f"爆款参考《{viral.get('title')}》：{details or pattern_for_role(beat['role'])}"
    return pattern_for_role(beat["role"])


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
