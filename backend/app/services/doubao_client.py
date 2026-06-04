from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any, AsyncIterator

from volcenginesdkarkruntime import Ark, AsyncArk

from ..core.config import get_settings
from ..models.video_structure import VideoMeta

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

KEY_MAP = {
    "脚本结构": "sections",
    "节奏结构": "shots",
    "音频结构": "audio_structure",
    "包装结构": "packaging_structure",
    "可迁移特征": "transferable_features",
    "script_structure": "sections",
    "rhythm_structure": "shots",
    "新脚本": "script",
    "新分镜": "timeline",
    "素材需求": "material_needs",
}


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def ark_available() -> bool:
    return bool(get_settings().ARK_API_KEY)


def get_ark_client() -> Ark:
    settings = get_settings()
    return Ark(base_url=settings.ARK_BASE_URL, api_key=settings.ARK_API_KEY)


def get_async_ark_client() -> AsyncArk:
    settings = get_settings()
    return AsyncArk(base_url=settings.ARK_BASE_URL, api_key=settings.ARK_API_KEY)


def encode_video_base64(video_path: str) -> str:
    with open(video_path, "rb") as handle:
        return base64.b64encode(handle.read()).decode("utf-8")


def choose_analysis_fps(meta: VideoMeta) -> float:
    override = os.environ.get("VIDEO_ANALYSIS_FPS")
    if override:
        try:
            return max(0.2, min(5.0, float(override)))
        except ValueError:
            pass
    if meta.duration <= 25:
        return 4.0
    if meta.duration <= 70:
        return 3.0
    return max(1.0, min(3.0, 180.0 / max(meta.duration, 1.0)))


async def analyze_video_with_doubao(video_path: str, meta: VideoMeta, context: dict[str, Any] | None = None) -> dict:
    settings = get_settings()
    client = get_async_ark_client()
    fps = choose_analysis_fps(meta)
    video_data = encode_video_base64(video_path)
    user_text = f"""请分析这个猫 meme 或短视频样例的爆款结构。

视频信息：
- 时长: {meta.duration:.1f}秒
- 分辨率: {meta.resolution}
- 帧率: {meta.fps}fps
- 抽帧率: {fps}fps

请重点输出 hook、情绪递进、反转、字幕包装、镜头节奏、可迁移特征。"""
    if context:
        user_text += "\n\n补充上下文：\n" + json.dumps(context, ensure_ascii=False, indent=2)

    try:
        response = await client.chat.completions.create(
            model=settings.chat_model(),
            messages=[
                {"role": "system", "content": load_prompt("video_analysis")},
                {
                    "role": "user",
                    "content": [
                        {"type": "video_url", "video_url": {"url": f"data:video/mp4;base64,{video_data}", "fps": fps}},
                        {"type": "text", "text": user_text},
                    ],
                },
            ],
            temperature=0.3,
        )
    finally:
        await client.close()
    content = response.choices[0].message.content
    result = parse_doubao_response(content)
    result["_analysis_sample_fps"] = fps
    return result


async def analyze_viral_maomeme_with_doubao(video_path: str, meta: VideoMeta, context: dict[str, Any] | None = None) -> dict:
    settings = get_settings()
    client = get_async_ark_client()
    fps = choose_analysis_fps(meta)
    video_data = encode_video_base64(video_path)
    user_text = f"""请按多轨方式拆解这个用户上传的爆款猫 meme 视频。

视频信息：
- 时长: {meta.duration:.1f}秒
- 分辨率: {meta.resolution}
- 帧率: {meta.fps}fps
- 抽帧率: {fps}fps

重点提取每个分镜的具体剧本、背景、猫素材需求、BGM/配音/音效、字幕包装和可迁移梗点。"""
    if context:
        user_text += "\n\n用户补充上下文：\n" + json.dumps(context, ensure_ascii=False, indent=2)

    try:
        response = await client.chat.completions.create(
            model=settings.chat_model(),
            messages=[
                {"role": "system", "content": load_prompt("viral_maomeme_analysis")},
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
        )
    finally:
        await client.close()
    result = parse_doubao_response(response.choices[0].message.content)
    result["_analysis_sample_fps"] = fps
    return result


async def generate_plan_with_doubao(theme: str, source_summary: str, assets_summary: str) -> dict:
    settings = get_settings()
    client = get_async_ark_client()
    try:
        response = await client.chat.completions.create(
            model=settings.chat_model(),
            messages=[
                {"role": "system", "content": load_prompt("maomeme_plan")},
                {
                    "role": "user",
                    "content": f"""## 爆款样例结构

{source_summary}

## 本地素材库

{assets_summary}

## 新主题

{theme}

请输出严格 JSON。""",
                },
            ],
            temperature=0.45,
        )
    finally:
        await client.close()
    return parse_doubao_response(response.choices[0].message.content)


async def generate_plan_with_doubao_context(
    theme: str,
    source_summary: str,
    assets_summary: str,
    text_context: dict,
) -> dict:
    settings = get_settings()
    client = get_async_ark_client()
    try:
        response = await client.chat.completions.create(
            model=settings.chat_model(),
            messages=[
                {"role": "system", "content": load_prompt("maomeme_plan")},
                {
                    "role": "user",
                    "content": f"""## 爆款样例结构

{source_summary}

## 本地素材库

{assets_summary}

## 社会现实文本素材

{json.dumps(text_context, ensure_ascii=False, indent=2)}

## 新主题

{theme}

请让剧本逻辑更完整，必须包含现实矛盾、具体细节、猫 meme 反差和合理收束。输出严格 JSON。""",
                },
            ],
            temperature=0.5,
        )
    finally:
        await client.close()
    return parse_doubao_response(response.choices[0].message.content)


async def generate_candidates_with_doubao_context(
    theme: str,
    assets_summary: str,
    text_context: dict,
    duration_mode: str = "short",
    angle: str | None = None,
    viral_reference_text: str = "",
    creative_context: dict[str, Any] | None = None,
) -> dict:
    settings = get_settings()
    client = get_async_ark_client()
    try:
        response = await client.chat.completions.create(
            model=settings.chat_model(),
            messages=candidate_messages(theme, assets_summary, text_context, duration_mode, angle=angle, viral_reference_text=viral_reference_text, creative_context=creative_context),
            temperature=0.72,
        )
    finally:
        await client.close()
    return parse_doubao_response(response.choices[0].message.content)


async def stream_candidates_with_doubao_context(
    theme: str,
    assets_summary: str,
    text_context: dict,
    duration_mode: str = "short",
    angle: str | None = None,
    viral_reference_text: str = "",
    creative_context: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    settings = get_settings()
    client = get_async_ark_client()
    content_parts: list[str] = []
    try:
        stream = await client.chat.completions.create(
            model=settings.chat_model(),
            messages=candidate_messages(theme, assets_summary, text_context, duration_mode, angle=angle, viral_reference_text=viral_reference_text, creative_context=creative_context),
            temperature=0.72,
            stream=True,
        )
        async for chunk in stream:
            if not getattr(chunk, "choices", None):
                continue
            delta = getattr(chunk.choices[0], "delta", None)
            text = getattr(delta, "content", "") if delta else ""
            if not text:
                continue
            content_parts.append(text)
            yield {"type": "delta", "text": text}
    finally:
        await client.close()

    content = "".join(content_parts)
    yield {"type": "final", "raw": parse_doubao_response(content), "content": content}


async def generate_brief_suggestions_with_mini(
    theme: str,
    creative_brief: dict[str, Any] | None = None,
    viral_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    client = get_async_ark_client()
    try:
        response = await client.chat.completions.create(
            model=settings.mini_model(),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是猫 meme 短视频创作助理。只输出严格 JSON，不要 Markdown。"
                        "你的任务是根据主题给用户可点击的补全建议，不要直接生成完整剧本。"
                        "建议要具体、短、适合猫 meme 分镜和背景素材匹配。"
                    ),
                },
                {
                    "role": "user",
                    "content": f"""主题：
{theme}

当前用户补充：
{json.dumps(creative_brief or {}, ensure_ascii=False)}

上传爆款参考摘要：
{json.dumps(viral_context or {}, ensure_ascii=False)[:1200]}

请输出 JSON：
{{
  "suggestions": {{
    "target_audience": ["建议1", "建议2"],
    "protagonist": ["建议1", "建议2"],
    "core_conflict": ["建议1", "建议2"],
    "ending_tone": ["建议1", "建议2"],
    "required_scenes": ["建议1", "建议2"],
    "required_props": ["建议1", "建议2"]
  }}
}}

要求：
- 每个字段 2 条以内。
- 每条不超过 16 个汉字。
- 不要输出空泛词，比如“年轻人”“有趣”。
- 如果主题提到请假/老板/120，建议必须包含请假审批、老板消息、急救电话或工位场景。
- 如果主题提到找工作/摆摊/烤肠，建议必须包含招聘软件、离谱要求、校门口烤肠摊。
- 如果主题提到周一/不想上班，建议必须包含闹钟、地铁、工作群或周会。""",
                },
            ],
            temperature=0.35,
            max_tokens=900,
        )
    finally:
        await client.close()
    return parse_doubao_response(response.choices[0].message.content)


def candidate_messages(
    theme: str,
    assets_summary: str,
    text_context: dict,
    duration_mode: str = "short",
    angle: str | None = None,
    viral_reference_text: str = "",
    creative_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    duration_hint = {
        "short": "4个镜头，12秒左右",
        "medium": "6个镜头，30秒左右",
        "minute": "8个镜头，60秒左右",
    }.get(duration_mode, "4个镜头，12秒左右")
    angle_hint = f"\n## 创作角度\n{angle}\n" if angle else ""
    count_hint = "必须给 1 个候选，且只输出 candidates 数组中的 1 项。" if angle else "必须给 3 个候选。"
    compact_context = compact_text_context(text_context)
    upload_context_text = json.dumps(compact_creative_context(creative_context or {}), ensure_ascii=False)
    return [
        {
            "role": "system",
            "content": (
                "你是猫 meme 短视频编剧 Agent。只输出严格 JSON，不要 Markdown。"
                "生成的剧本要有社会现实矛盾、具体细节、猫 meme 反差和合理收束，"
                "避免万能句、空泛励志、机械反转。所有台词必须像短视频字幕，"
                "短、准、口语化，不要堆砌生硬数字，不要把无关机构强行写进剧情。"
                "剧本文案默认写人类社会角色，比如学生、打工人、老板、同事、HR、摊主。"
                "猫只作为后续素材表现层，不要在字幕里反复说猫。"
                "猫不能直接解决社会问题，只能用荒诞动作暴露矛盾、缓冲情绪或推动角色换策略。"
            ),
        },
        {
            "role": "user",
            "content": f"""## 主题
{theme}

## 目标时长
{duration_hint}
{angle_hint}

## 社会现实文本素材
{json.dumps(compact_context, ensure_ascii=False)}

## 强制迁移蓝图与已验证爆款猫 meme few-shot
{shorten_text(viral_reference_text, 4200) or "暂无。"}

## 用户上传与创作补充
{shorten_text(upload_context_text, 1200) or "暂无。"}

## 素材能力提示
本地有绿幕猫动作、双猫对话、电脑/手机/震惊/委屈/发呆等常见猫素材；也有办公室、教室、自习室、出租屋、夜市小摊、招聘会等背景。这里只写剧本，不要做具体素材 ID 决策，选中后会由分镜 Agent 单独匹配猫、背景和贴图。

请输出 JSON：
{{
  "candidates": [
    {{
      "name": "标题",
      "social_topic": "现实母题",
      "tension": "核心矛盾",
      "beats": [["hook","字幕","分镜目的"], ["setup","字幕","分镜目的"], ...],
      "scene": ["office", "classroom"],
      "theme_keywords": ["关键词"],
      "emotion": ["震惊", "电脑", "委屈"],
      "viral_reference_id": "主爆款 id",
      "viral_reference_title": "主爆款标题",
      "viral_structure_tags": ["结构标签"]
    }}
  ]
}}

要求：
- {count_hint}
- 每个候选都必须贴合主题，不要写“最后被猫解决”这类无逻辑结尾。
- 必须遵循 migration_blueprint：主爆款每个镜头的剧情功能、冲突推进、字幕包装、背景/猫动作需求都要迁移，但不要照抄原视频台词或情节。
- 每个候选都要能看出“主爆款结构 + 1 个辅助爆款梗点”的迁移关系。
- 如果用户上传了爆款视频，优先迁移“用户上传爆款”的结构；公共爆款库只做次级参考。
- 如果用户上传了素材，剧本要尽量设计能用上这些素材的场景，但不要为了用素材牺牲逻辑。
- 用户 brief 里如果写了受众、主角设定、冲突、结尾倾向、必备场景或禁忌内容，必须遵守。
- 结尾可以荒诞，但要合理：比如先缓一口气、换策略、互相抱团、识别规则问题。
- 结尾禁止写“猫解决了求职/上班/请假/租房等社会问题”；只能写识别规则、换策略、互助、喘口气或荒诞反讽。
- 可以有脑洞跨场景，但必须有逻辑桥和具体画面；比如求职失败想到摆摊可以成立，但要写清“为什么想到、摆摊也有什么现实成本”，不要突然跳场。
- 每条字幕不超过 18 个汉字，尽量用具体动作承载现实压力。
- 如果主题是彩礼/买房/婚恋，重点是双方和家庭如何面对现实账单，不要写成单纯攻击某一方。
- 分镜字幕短、具体、口语化。""",
        },
    ]


def compact_text_context(text_context: dict) -> dict:
    if not isinstance(text_context, dict):
        return {}
    beat_seed = text_context.get("beat_seed", {})
    if isinstance(beat_seed, dict):
        compact_beat_seed: Any = {str(key): str(value) for key, value in list(beat_seed.items())[:6]}
    elif isinstance(beat_seed, list):
        compact_beat_seed = [str(item) for item in beat_seed[:6]]
    else:
        compact_beat_seed = {}
    return {
        "title": text_context.get("title", ""),
        "keywords": list(text_context.get("keywords", []) or [])[:8],
        "facts": list(text_context.get("facts", []) or [])[:3],
        "tensions": list(text_context.get("tensions", []) or [])[:4],
        "meme_angles": list(text_context.get("meme_angles", []) or [])[:4],
        "beat_seed": compact_beat_seed,
    }


def compact_creative_context(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    viral = value.get("viral_analysis") if isinstance(value.get("viral_analysis"), dict) else {}
    summary = viral.get("summary") if isinstance(viral.get("summary"), dict) else {}
    brief = value.get("creative_brief") if isinstance(value.get("creative_brief"), dict) else {}
    user_materials = value.get("user_materials") if isinstance(value.get("user_materials"), dict) else {}
    return {
        "uploaded_viral_summary": {
            "title": summary.get("title", ""),
            "one_sentence": summary.get("one_sentence", ""),
            "script_outline": list(summary.get("script_outline", []) or [])[:5],
            "transferable_features": list(summary.get("transferable_features", []) or [])[:5],
            "audio_style": summary.get("audio_style", ""),
        },
        "uploaded_viral_slots": list(viral.get("transfer_slots", []) or [])[:6],
        "creative_brief": brief,
        "user_materials": user_materials,
    }


def shorten_text(text: str, limit: int) -> str:
    text = str(text or "").strip()
    return text if len(text) <= limit else text[:limit] + "..."


def parse_doubao_response(content: str) -> dict:
    if "```json" in content:
        json_str = content.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in content:
        json_str = content.split("```", 1)[1].split("```", 1)[0].strip()
    else:
        json_str = content.strip()
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        return {"raw_response": content}
    if not isinstance(data, dict):
        return {"raw_response": content}
    return {KEY_MAP.get(k, k): v for k, v in data.items()}
