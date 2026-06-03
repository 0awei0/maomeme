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
) -> dict:
    settings = get_settings()
    client = get_async_ark_client()
    try:
        response = await client.chat.completions.create(
            model=settings.chat_model(),
            messages=candidate_messages(theme, assets_summary, text_context, duration_mode, angle=angle, viral_reference_text=viral_reference_text),
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
) -> AsyncIterator[dict[str, Any]]:
    settings = get_settings()
    client = get_async_ark_client()
    content_parts: list[str] = []
    try:
        stream = await client.chat.completions.create(
            model=settings.chat_model(),
            messages=candidate_messages(theme, assets_summary, text_context, duration_mode, angle=angle, viral_reference_text=viral_reference_text),
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


def candidate_messages(
    theme: str,
    assets_summary: str,
    text_context: dict,
    duration_mode: str = "short",
    angle: str | None = None,
    viral_reference_text: str = "",
) -> list[dict[str, Any]]:
    duration_hint = {
        "short": "4个镜头，12秒左右",
        "medium": "6个镜头，30秒左右",
        "minute": "8个镜头，60秒左右",
    }.get(duration_mode, "4个镜头，12秒左右")
    angle_hint = f"\n## 创作角度\n{angle}\n" if angle else ""
    count_hint = "必须给 1 个候选，且只输出 candidates 数组中的 1 项。" if angle else "必须给 3 个候选。"
    compact_context = compact_text_context(text_context)
    return [
        {
            "role": "system",
            "content": (
                "你是猫 meme 短视频编剧 Agent。只输出严格 JSON，不要 Markdown。"
                "生成的剧本要有社会现实矛盾、具体细节、猫 meme 反差和合理收束，"
                "避免万能句、空泛励志、机械反转。所有台词必须像短视频字幕，"
                "短、准、口语化，不要堆砌生硬数字，不要把无关机构强行写进剧情。"
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

## 已验证爆款猫 meme 结构参考
{shorten_text(viral_reference_text, 900) or "暂无。"}

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
      "emotion": ["震惊", "电脑", "委屈"]
    }}
  ]
}}

要求：
- {count_hint}
- 每个候选都必须贴合主题，不要写“最后被猫解决”这类无逻辑结尾。
- 必须借鉴上面的爆款结构参考：迁移它们的节奏、冲突推进、字幕包装、背景/猫动作类型，但不要照抄原视频台词或情节。
- 结尾可以荒诞，但要合理：比如先缓一口气、换策略、互相抱团、识别规则问题。
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
