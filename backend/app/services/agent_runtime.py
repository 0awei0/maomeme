from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from ..core.config import get_settings
from .agent_tools import (
    asset_search_tool,
    background_tool,
    cat_casting_tool,
    clip_planner_by_id_tool,
    hyperframe_packaging_tool,
    overlay_design_tool,
    shot_critic_tool,
    transition_planner_tool,
)
from .asset_index import ref
from .doubao_client import ark_available, get_async_ark_client, parse_doubao_response


ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass
class AgentRuntimeResult:
    ok: bool
    provider: str
    output: dict[str, Any]
    events: list[dict[str, Any]]
    error: str = ""


def enabled_runtime_order() -> list[str]:
    settings = get_settings()
    requested = settings.AGENT_RUNTIME
    if requested in {"workflow", "local", "none"}:
        return []
    return ["ark"]


async def run_shot_agent(
    *,
    theme: str,
    beat: dict[str, Any],
    index: dict[str, Any],
    previous_slot: dict[str, Any] | None = None,
    used_motion_ids: set[str] | None = None,
    used_background_ids: set[str] | None = None,
) -> AgentRuntimeResult:
    tool_context = ShotToolContext(
        theme=theme,
        beat=beat,
        index=index,
        previous_slot=previous_slot,
        used_motion_ids=set(used_motion_ids or set()),
        used_background_ids=set(used_background_ids or set()),
    )
    prompt = shot_planner_prompt(theme, beat, previous_slot, tool_context)
    return await run_agent_with_fallbacks(
        name="ShotPlannerAgent",
        instructions=SHOT_PLANNER_INSTRUCTIONS,
        user_prompt=prompt,
        tools=tool_schemas(),
        handlers=tool_context.handlers(),
        max_turns=7,
    )


async def run_critic_agent(
    *,
    theme: str,
    beat: dict[str, Any],
    slot: dict[str, Any],
    index: dict[str, Any],
    used_motion_ids: set[str] | None = None,
    used_background_ids: set[str] | None = None,
) -> AgentRuntimeResult:
    tool_context = ShotToolContext(
        theme=theme,
        beat=beat,
        index=index,
        previous_slot=None,
        used_motion_ids=set(used_motion_ids or set()),
        used_background_ids=set(used_background_ids or set()),
    )
    prompt = f"""请质检并必要时修订这个猫 meme 分镜，只输出 JSON。

## 主题
{theme}

## 分镜意图
{json.dumps(public_beat(beat), ensure_ascii=False, indent=2)}

## 当前分镜
{json.dumps(public_slot(slot), ensure_ascii=False, indent=2)}

要求：
- 调用 shot_critic_tool 判断是否合格。
- 如果不合格，调用工具重新选择猫/背景/贴图，并输出 revised_slot。
- 如果合格，输出 revised_slot 等于当前分镜。
- 输出格式：{{"critic": {{"score": 0.0, "passed": true, "issues": []}}, "revised_slot": {{...完整分镜...}}, "notes": []}}
"""
    return await run_agent_with_fallbacks(
        name="CriticAgent",
        instructions=CRITIC_INSTRUCTIONS,
        user_prompt=prompt,
        tools=tool_schemas(),
        handlers=tool_context.handlers(),
        max_turns=6,
    )


async def run_assembler_agent(
    *,
    theme: str,
    timeline: list[dict[str, Any]],
) -> AgentRuntimeResult:
    prompt = f"""请做猫 meme 全片一致性检查，只输出 JSON。

## 主题
{theme}

## 当前 timeline
{json.dumps([public_slot(slot) for slot in timeline], ensure_ascii=False, indent=2)}

重点：
- 同一条视频里贴图文案和道具不要重复超过 2 次。
- 背景切换要有逻辑；同一场景可以连续，换场景要转场。
- 字幕只保留一层：对话镜头用气泡，单猫镜头用顶部主标题。
- 猫不能解决社会问题，只能暴露矛盾、换策略或缓冲情绪。

输出格式：
{{"timeline_patch": [{{"id": "slot-id", "overlay_actions": [], "transition": {{"type":"cut","duration":0}}, "packaging": []}}], "notes": []}}
"""
    return await run_agent_with_fallbacks(
        name="AssemblerAgent",
        instructions=ASSEMBLER_INSTRUCTIONS,
        user_prompt=prompt,
        tools=[],
        handlers={},
        max_turns=2,
    )


async def run_agent_with_fallbacks(
    *,
    name: str,
    instructions: str,
    user_prompt: str,
    tools: list[dict[str, Any]],
    handlers: dict[str, ToolHandler],
    max_turns: int,
) -> AgentRuntimeResult:
    errors: list[str] = []
    for provider in enabled_runtime_order():
        if provider == "ark" and not ark_available():
            errors.append("ark_unconfigured")
            continue
        try:
            return await run_ark_tool_agent(
                name=name,
                instructions=instructions,
                user_prompt=user_prompt,
                tools=tools,
                handlers=handlers,
                max_turns=max_turns,
            )
        except Exception as exc:
            errors.append(f"{provider}:{safe_error(exc)}")
    return AgentRuntimeResult(ok=False, provider="workflow", output={}, events=[], error="; ".join(errors))


async def run_ark_tool_agent(
    *,
    name: str,
    instructions: str,
    user_prompt: str,
    tools: list[dict[str, Any]],
    handlers: dict[str, ToolHandler],
    max_turns: int,
) -> AgentRuntimeResult:
    settings = get_settings()
    client = get_async_ark_client()
    events: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": instructions},
        {"role": "user", "content": user_prompt},
    ]
    try:
        for turn in range(max_turns):
            kwargs: dict[str, Any] = {
                "model": settings.chat_model(),
                "messages": messages,
                "temperature": 0.35 if name != "ShotPlannerAgent" else 0.55,
                "stream": True,
            }
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"
            stream = await client.chat.completions.create(**kwargs)
            content_parts: list[str] = []
            tool_buffers: dict[int, dict[str, Any]] = {}
            async for chunk in stream:
                if not getattr(chunk, "choices", None):
                    continue
                delta = getattr(chunk.choices[0], "delta", None)
                if not delta:
                    continue
                text = getattr(delta, "content", "") or ""
                if text:
                    content_parts.append(text)
                    events.append({"type": "delta", "provider": "ark", "text": text})
                for index, call in enumerate(list(getattr(delta, "tool_calls", None) or [])):
                    call_index = int(getattr(call, "index", index) or 0)
                    buffer = tool_buffers.setdefault(call_index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                    if getattr(call, "id", None):
                        buffer["id"] = call.id
                    function = getattr(call, "function", None)
                    if function:
                        if getattr(function, "name", None):
                            buffer["function"]["name"] = function.name
                        if getattr(function, "arguments", None):
                            buffer["function"]["arguments"] += function.arguments

            assistant_content = "".join(content_parts)
            calls = [value for _, value in sorted(tool_buffers.items()) if value["function"].get("name")]
            if calls:
                messages.append({"role": "assistant", "content": assistant_content or "", "tool_calls": calls})
                for call in calls:
                    tool_name = call["function"]["name"]
                    args = parse_tool_arguments(call["function"].get("arguments", ""))
                    result = await call_tool(handlers, tool_name, args)
                    events.append({"type": "tool_result", "provider": "ark", "tool": tool_name, "result": shrink_tool_result(result)})
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.get("id") or tool_name,
                        "name": tool_name,
                        "content": json.dumps(result, ensure_ascii=False),
                    })
                continue
            output = parse_json_object(assistant_content)
            if output:
                return AgentRuntimeResult(ok=True, provider="ark", output=output, events=events)
            messages.append({"role": "assistant", "content": assistant_content})
            messages.append({"role": "user", "content": "上一次不是严格 JSON。请只输出一个 JSON object，不要 Markdown。"})
        return AgentRuntimeResult(ok=False, provider="ark", output={}, events=events, error="ark_max_turns_no_json")
    finally:
        await client.close()


async def call_tool(handlers: dict[str, ToolHandler], tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    handler = handlers.get(tool_name)
    if not handler:
        return {"status": "error", "error": f"tool_not_allowed:{tool_name}"}
    try:
        return await handler(args)
    except Exception as exc:
        return {"status": "error", "error": safe_error(exc)}


def tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "asset_search_tool",
                "description": "Search local cat-motion or background asset descriptions.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "asset_type": {"type": "string", "enum": ["motion", "background"]},
                        "keywords": {"type": "array", "items": {"type": "string"}},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 8},
                    },
                    "required": ["asset_type", "keywords"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "shot_bundle_tool",
                "description": "Fast path: plan cat casting, background, clips, overlays, HyperFrames packaging and critic in one tool call.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "emotion_keywords": {"type": "array", "items": {"type": "string"}},
                        "scene_keywords": {"type": "array", "items": {"type": "string"}},
                        "caption": {"type": "string"},
                        "intent": {"type": "string"},
                        "background_need": {
                            "type": "string",
                            "description": "Concrete missing background need, e.g. 校门口夜市烤肠摊，求职失败后转去摆摊也被内卷.",
                        },
                        "seedream_prompt": {
                            "type": "string",
                            "description": "Optional concrete Seedream prompt. Backend will sanitize, append hard constraints, and fall back if vague or unsafe.",
                        },
                        "negative_constraints": {"type": "array", "items": {"type": "string"}},
                        "slug_hint": {
                            "type": "string",
                            "description": "Short stable slug hint such as school-gate-sausage-stall.",
                        },
                        "count": {"type": "integer", "minimum": 1, "maximum": 2},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "cat_casting_tool",
                "description": "Choose one or two cat motion clips for the shot.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "emotion_keywords": {"type": "array", "items": {"type": "string"}},
                        "caption": {"type": "string"},
                        "intent": {"type": "string"},
                        "count": {"type": "integer", "minimum": 1, "maximum": 2},
                    },
                    "required": ["emotion_keywords"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "background_tool",
                "description": "Choose an existing background or request Seedream fill when the scene is missing.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "scene_keywords": {"type": "array", "items": {"type": "string"}},
                        "caption": {"type": "string"},
                        "intent": {"type": "string"},
                        "background_need": {
                            "type": "string",
                            "description": "Concrete missing background need.",
                        },
                        "seedream_prompt": {
                            "type": "string",
                            "description": "Optional concrete Seedream prompt; backend enforces constraints and fallback.",
                        },
                        "negative_constraints": {"type": "array", "items": {"type": "string"}},
                        "slug_hint": {"type": "string"},
                    },
                    "required": ["scene_keywords"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "clip_planner_tool",
                "description": "Plan clip start and duration for a selected asset.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "asset_type": {"type": "string", "enum": ["motion", "background"]},
                        "asset_id": {"type": "string"},
                        "slot_duration": {"type": "number"},
                    },
                    "required": ["asset_type", "asset_id", "slot_duration"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "overlay_design_tool",
                "description": "Normalize dynamic overlays such as phone UI, cards, thrown props, stamps, stickers.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "requested_actions": {
                            "type": "array",
                            "items": {"type": "object", "additionalProperties": True},
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "hyperframe_packaging_tool",
                "description": "Select safe HyperFrames packaging preset and subtitle policy.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "overlay_actions": {
                            "type": "array",
                            "items": {"type": "object", "additionalProperties": True},
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "shot_critic_tool",
                "description": "Score whether the shot's script, cat motion, background, overlays and subtitles fit.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "slot": {"type": "object", "additionalProperties": True},
                    },
                    "required": ["slot"],
                },
            },
        },
    ]


class ShotToolContext:
    def __init__(
        self,
        *,
        theme: str,
        beat: dict[str, Any],
        index: dict[str, Any],
        previous_slot: dict[str, Any] | None,
        used_motion_ids: set[str],
        used_background_ids: set[str],
    ) -> None:
        self.theme = theme
        self.beat = beat
        self.index = index
        self.previous_slot = previous_slot
        self.used_motion_ids = used_motion_ids
        self.used_background_ids = used_background_ids

    def handlers(self) -> dict[str, ToolHandler]:
        async def bundle(args: dict[str, Any]) -> dict[str, Any]:
            beat = {**self.beat, **limited_beat_args(args)}
            count = int(args.get("count") or (2 if beat.get("layout") == "dialogue" else 1))
            motions = cat_casting_tool(
                self.index,
                self.theme,
                beat,
                count=count,
                avoid_ids=list(self.used_motion_ids),
            )
            background_result = background_tool(
                self.index,
                self.theme,
                beat,
                avoid_ids=list(self.used_background_ids),
            )
            motion = motions[0] if motions else {}
            secondary = motions[1] if len(motions) > 1 and beat.get("layout") == "dialogue" else None
            slot_duration = float(self.beat.get("end", 4) or 4) - float(self.beat.get("start", 0) or 0)
            overlay_actions = overlay_design_tool(
                self.theme,
                beat,
                motion=motion,
                background=background_result.get("asset", {}),
                requested_actions=args.get("requested_actions") if isinstance(args.get("requested_actions"), list) else [],
            )
            packaging = hyperframe_packaging_tool(self.theme, beat, overlay_actions=overlay_actions)
            slot = {
                "id": self.beat.get("id"),
                "start": self.beat.get("start", 0),
                "end": self.beat.get("end", slot_duration),
                "role": self.beat.get("role", "setup"),
                "intent": beat.get("intent") or self.beat.get("intent", ""),
                "caption": beat.get("caption") or self.beat.get("caption", ""),
                "motion": motion,
                "motion_clip": clip_planner_by_id_tool(self.index, "motion", str(motion.get("id", "")), self.beat, slot_duration),
                "secondary_motion": secondary,
                "secondary_motion_clip": clip_planner_by_id_tool(self.index, "motion", str((secondary or {}).get("id", "")), self.beat, slot_duration) if secondary else None,
                "background": background_result.get("asset", {}),
                "background_source": background_result.get("background_source", "matched"),
                "background_prompt": background_result.get("background_prompt", ""),
                "transition": packaging.get("transition_hint", {"type": "cut", "duration": 0}),
                "layout": self.beat.get("layout", "single"),
                "dialogue": self.beat.get("dialogue", []),
                "overlay_actions": overlay_actions,
                "gap": {
                    "status": "matched" if background_result.get("background_source") in {"matched", "generated"} else "generated_pending",
                    "strategy": "agent_tool_bundle",
                    "reason": background_result.get("reason", "工具已完成猫动作、背景和包装匹配。"),
                },
                "packaging": [packaging.get("packaging_preset", "default-cat-meme"), packaging.get("caption_style", "top_title")],
                "source_pattern": "ShotPlannerAgent 工具包快路径",
            }
            critic = shot_critic_tool(
                self.theme,
                self.beat,
                slot,
                used_motion_ids=list(self.used_motion_ids),
                used_background_ids=list(self.used_background_ids),
            )
            return {"status": "success", "slot": slot, "critic": critic, "packaging": packaging}

        async def search(args: dict[str, Any]) -> dict[str, Any]:
            result = asset_search_tool(
                self.index,
                str(args.get("asset_type") or "motion"),
                listify(args.get("keywords")),
                limit=int(args.get("limit") or 5),
            )
            return {"status": "success", "assets": [public_asset(item) for item in result[:8]]}

        async def cast(args: dict[str, Any]) -> dict[str, Any]:
            beat = {**self.beat, **limited_beat_args(args)}
            count = int(args.get("count") or (2 if beat.get("layout") == "dialogue" else 1))
            result = cat_casting_tool(
                self.index,
                self.theme,
                beat,
                count=count,
                avoid_ids=list(self.used_motion_ids),
            )
            return {"status": "success", "motions": result}

        async def background(args: dict[str, Any]) -> dict[str, Any]:
            beat = {**self.beat, **limited_beat_args(args)}
            result = background_tool(
                self.index,
                self.theme,
                beat,
                avoid_ids=list(self.used_background_ids),
            )
            return {"status": "success", **result}

        async def clip(args: dict[str, Any]) -> dict[str, Any]:
            result = clip_planner_by_id_tool(
                self.index,
                str(args.get("asset_type") or "motion"),
                str(args.get("asset_id") or ""),
                self.beat,
                float(args.get("slot_duration") or (self.beat.get("end", 4) - self.beat.get("start", 0))),
            )
            return {"status": "success", "clip": result}

        async def overlay(args: dict[str, Any]) -> dict[str, Any]:
            actions = overlay_design_tool(
                self.theme,
                self.beat,
                requested_actions=args.get("requested_actions") if isinstance(args.get("requested_actions"), list) else [],
            )
            return {"status": "success", "overlay_actions": actions}

        async def packaging(args: dict[str, Any]) -> dict[str, Any]:
            result = hyperframe_packaging_tool(
                self.theme,
                self.beat,
                overlay_actions=args.get("overlay_actions") if isinstance(args.get("overlay_actions"), list) else [],
            )
            return {"status": "success", **result}

        async def critic(args: dict[str, Any]) -> dict[str, Any]:
            result = shot_critic_tool(
                self.theme,
                self.beat,
                args.get("slot") if isinstance(args.get("slot"), dict) else {},
                used_motion_ids=list(self.used_motion_ids),
                used_background_ids=list(self.used_background_ids),
            )
            return {"status": "success", "critic": result}

        return {
            "shot_bundle_tool": bundle,
            "asset_search_tool": search,
            "cat_casting_tool": cast,
            "background_tool": background,
            "clip_planner_tool": clip,
            "overlay_design_tool": overlay,
            "hyperframe_packaging_tool": packaging,
            "shot_critic_tool": critic,
        }


SHOT_PLANNER_INSTRUCTIONS = """你是猫 meme 短视频 ShotPlannerAgent。
你必须通过白名单工具选择素材和包装，然后只输出严格 JSON。
禁止读取 env、禁止写代码、禁止写 FFmpeg/HTML 命令、禁止输出 Markdown。
你的目标是让每个分镜具体、可拍、可渲染：猫动作、背景、字幕、贴图都要贴合剧本。
如果没有完全匹配素材，优先用背景补图 prompt、字幕包装、裁剪和结构重排补足。
猫不能直接解决社会问题，只能暴露矛盾、缓冲情绪、换小策略或形成共鸣。
动态贴图必须服务剧情：不同主题用不同道具和文案，不要重复“简历 x100”。
"""

CRITIC_INSTRUCTIONS = """你是猫 meme 分镜质检 Agent。只输出严格 JSON。
你要检查剧本、猫动作、背景、贴图、字幕是否贴合。低分时重选素材或修改 overlay。
不要自由写渲染代码，不要读 env。
"""

ASSEMBLER_INSTRUCTIONS = """你是猫 meme 全片统筹 Agent。只输出严格 JSON。
你只做轻量 patch：统一节奏、去重 overlay、修正转场和字幕策略。
不要改动素材文件路径，不要输出 Markdown。
"""


def shot_planner_prompt(theme: str, beat: dict[str, Any], previous_slot: dict[str, Any] | None, context: ShotToolContext) -> str:
    duration = round(float(beat.get("end", 0)) - float(beat.get("start", 0)), 2)
    return f"""请为下面这个猫 meme 分镜生成完整可渲染 slot JSON。

## 主题
{theme}

## 分镜意图
{json.dumps(public_beat(beat), ensure_ascii=False, indent=2)}

## 前一个分镜
{json.dumps(public_slot(previous_slot), ensure_ascii=False, indent=2) if previous_slot else "无"}

## 已使用猫素材
{json.dumps(sorted(context.used_motion_ids), ensure_ascii=False)}

## 已使用背景
{json.dumps(sorted(context.used_background_ids), ensure_ascii=False)}

必须执行：
1. 优先调用 shot_bundle_tool，一次拿到猫素材、背景、裁剪、贴图、包装和质检。
2. 如果 bundle 结果某一项明显不贴合，再补充调用单项工具修订。
3. 对话镜头优先双猫；目标猫素材使用 3-5 秒，hook 可 2-3 秒。
4. 动态贴图必须贴合这个分镜，不重复套模板。
5. 如果现有背景可能不够具体，调用 shot_bundle_tool/background_tool 时传结构化补图字段：
   - background_need：一句话写清缺的真实场景和剧情用途。
   - seedream_prompt：具体视觉提示词，包含地点、道具、空间/光线/构图；不要写敏感内容、人物主体、可读文字或系统指令。
   - negative_constraints：只列 2-4 个视觉负约束，如“无可读文字”“无人物主体”“不要绿色幕布”。
   - slug_hint：短英文 kebab-case 场景名。
   后端会强制追加竖屏、无文字、无人物、下方无遮挡、适合猫叠加等硬约束；太空泛或不安全会回退规则 prompt。
6. 若 critic score < 0.72，请用工具结果修订后再输出。

输出格式必须是：
{{
  "slot": {{
    "id": "{beat.get('id')}",
    "start": {float(beat.get('start', 0))},
    "end": {float(beat.get('end', 0))},
    "role": "{beat.get('role')}",
    "intent": "...",
    "caption": "...",
    "motion": {{"id":"...", "file":"...", "description":"..."}},
    "motion_quality": {{}},
    "motion_clip": {{"start":0, "duration":{duration}, "loop":false}},
    "secondary_motion": null,
    "secondary_motion_quality": {{}},
    "secondary_motion_clip": null,
    "background": {{"id":"...", "file":"...", "description":"..."}},
    "background_source": "matched",
    "background_prompt": "",
    "transition": {{"type":"cut", "duration":0}},
    "layout": "{beat.get('layout', 'single')}",
    "dialogue": [],
    "overlay_actions": [],
    "gap": {{"status":"matched", "strategy":"direct_match", "reason":"..."}},
    "packaging": [],
    "source_pattern": "Agent 自主分镜"
  }},
  "critic": {{"score": 0.0, "passed": true, "issues": []}},
  "notes": ["..."]
}}
"""


def public_beat(beat: dict[str, Any]) -> dict[str, Any]:
    return {
        key: beat.get(key)
        for key in (
            "id",
            "start",
            "end",
            "role",
            "intent",
            "caption",
            "scene_keywords",
            "background_need",
            "seedream_prompt",
            "negative_constraints",
            "slug_hint",
            "emotion_keywords",
            "must_keywords",
            "layout",
            "dialogue",
            "viral_reference",
        )
        if key in beat
    }


def public_slot(slot: dict[str, Any] | None) -> dict[str, Any]:
    if not slot:
        return {}
    return {
        key: slot.get(key)
        for key in (
            "id",
            "start",
            "end",
            "role",
            "intent",
            "caption",
            "copy",
            "motion",
            "motion_clip",
            "secondary_motion",
            "secondary_motion_clip",
            "background",
            "background_source",
            "transition",
            "layout",
            "dialogue",
            "overlay_actions",
            "gap",
            "packaging",
        )
        if key in slot
    }


def public_asset(asset: dict[str, Any]) -> dict[str, Any]:
    payload = ref(asset)
    if "duration" in asset:
        payload["duration"] = round(float(asset.get("duration") or 0), 2)
    if asset.get("scene"):
        payload["scene"] = str(asset.get("scene"))
    return payload


def limited_beat_args(args: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if isinstance(args.get("scene_keywords"), list):
        result["scene_keywords"] = listify(args.get("scene_keywords"))
    if isinstance(args.get("emotion_keywords"), list):
        result["emotion_keywords"] = listify(args.get("emotion_keywords"))
    if isinstance(args.get("negative_constraints"), list):
        result["negative_constraints"] = listify(args.get("negative_constraints"))[:6]
    for key, limit in (
        ("caption", 80),
        ("intent", 100),
        ("background_need", 160),
        ("seedream_prompt", 260),
        ("slug_hint", 72),
    ):
        if isinstance(args.get(key), str) and args[key].strip():
            result[key] = args[key][:limit]
    return result


def parse_tool_arguments(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        repaired = raw.strip()
        try:
            parsed = json.loads(repaired)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}


def parse_json_object(text: str) -> dict[str, Any]:
    if isinstance(text, dict):
        return text
    parsed = parse_doubao_response(text or "")
    if isinstance(parsed, dict) and "raw_response" not in parsed:
        return parsed
    match = re.search(r"\{.*\}", text or "", flags=re.S)
    if match:
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def shrink_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps(result, ensure_ascii=False)
    if len(text) <= 900:
        return result
    return {"status": result.get("status", "success"), "preview": text[:900]}


def listify(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item)[:80] for item in value if str(item).strip()][:12]
    if isinstance(value, str) and value.strip():
        return [value[:80]]
    return []


def safe_error(exc: Exception) -> str:
    text = str(exc)
    text = re.sub(r"(api[_-]?key|authorization|bearer)\S*", "[secret]", text, flags=re.I)
    text = re.sub(r"https?://\S+", "[url]", text)
    return text[:260]
