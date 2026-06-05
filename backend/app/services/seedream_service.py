from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.request
from pathlib import Path

from ..core.config import get_settings
from .doubao_client import ark_available, get_ark_client


PROMPT_MAX_CHARS = 420
PROMPT_BASE_MAX_CHARS = 210
PROMPT_FIELD_MAX_CHARS = 180

MANDATORY_BACKGROUND_CONSTRAINTS = [
    "9:16竖屏构图",
    "写实短视频背景",
    "无可读文字",
    "无人物主体",
    "画面下方留出无遮挡的自然地面或桌面",
    "适合叠加抠像猫动画",
    "不要绿色幕布",
    "不要纯色块",
]

DEFAULT_NEGATIVE_CONSTRAINTS = [
    "无可读文字",
    "无人物主体",
    "不要绿色幕布",
    "不要纯色块",
]

UNSAFE_PROMPT_PATTERNS = (
    "色情",
    "裸露",
    "血腥",
    "暴恐",
    "恐怖袭击",
    "仇恨",
    "歧视",
    "自残",
    "自杀",
    "毒品",
    "赌博",
    "枪支",
    "政治人物",
    "真实名人",
    "未成年人性化",
)

PROMPT_INJECTION_PATTERNS = (
    "忽略以上",
    "忽略前面",
    "无视约束",
    "越过限制",
    "system prompt",
    "developer message",
    ".env",
    "api key",
    "apikey",
    "authorization",
)


def seedream_available() -> bool:
    return ark_available() and bool(get_settings().SEEDREAM_MODEL)


def constrain_background_prompt(
    *,
    theme: str = "",
    caption: str = "",
    scene_keywords: list[str] | None = None,
    background_need: str = "",
    seedream_prompt: str = "",
    negative_constraints: list[str] | None = None,
    slug_hint: str = "",
    fallback_prompt: str = "",
    fallback_slug: str = "agent-background",
) -> dict[str, object]:
    scenes = [clean_prompt_text(item, 48) for item in scene_keywords or [] if clean_prompt_text(item, 48)]
    need = clean_prompt_text(background_need, PROMPT_FIELD_MAX_CHARS)
    agent_prompt = clean_prompt_text(seedream_prompt, PROMPT_BASE_MAX_CHARS)
    fallback = clean_prompt_text(fallback_prompt, PROMPT_BASE_MAX_CHARS) or fallback_background_prompt(theme, caption, scenes)
    fallback = clean_prompt_text(fallback, PROMPT_BASE_MAX_CHARS)
    fallback_reason = ""

    if agent_prompt and prompt_is_usable(agent_prompt, need, scenes):
        base = agent_prompt
        source = "agent"
    else:
        base = fallback
        source = "fallback"
        fallback_reason = "agent_prompt_empty_or_too_vague" if not prompt_has_unsafe_text(agent_prompt) else "agent_prompt_unsafe"

    safe_negatives = normalize_negative_constraints(negative_constraints)
    prompt = append_prompt_constraints(base, safe_negatives)
    slug_base = clean_prompt_text(slug_hint, 72) or clean_prompt_text(need, 72) or fallback_slug
    description = background_description(theme, caption, need, scenes, prompt, source)

    return {
        "prompt": prompt,
        "description": description,
        "slug": slugify(slug_base),
        "background_need": need,
        "negative_constraints": safe_negatives,
        "source": source,
        "fallback_reason": fallback_reason,
    }


def generate_background(prompt: str, description: str = "", slug: str = "agent-fill") -> dict[str, str]:
    if not seedream_available():
        raise RuntimeError("Seedream is not configured")

    settings = get_settings()
    safe_slug = slugify(slug)
    timestamp = int(time.time())
    out_dir = settings.PROJECT_ROOT / "assets" / "generated" / "backgrounds" / safe_slug
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{timestamp}.png"

    response = get_ark_client().images.generate(
        model=settings.SEEDREAM_MODEL,
        prompt=prompt,
        size="1440x2560",
        response_format="url",
        output_format="png",
        watermark=False,
    )
    download_url(response.data[0].url, out_file)

    desc_file = out_dir / "descriptions.json"
    descriptions = []
    if desc_file.exists():
        try:
            descriptions = json.loads(desc_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            descriptions = []
    descriptions.append({"file": out_file.name, "description": description or prompt})
    desc_file.write_text(json.dumps(descriptions, ensure_ascii=False, indent=2), encoding="utf-8")
    refresh_assets_index(settings.PROJECT_ROOT)

    return {
        "file": str(out_file.relative_to(settings.PROJECT_ROOT)),
        "description": description or prompt,
    }


def prompt_is_usable(prompt: str, need: str, scenes: list[str]) -> bool:
    if not prompt or prompt_has_unsafe_text(prompt):
        return False
    if len(prompt) < 24:
        return False
    if len(meaningful_visual_terms(prompt)) < 2:
        return False
    return True


def prompt_has_unsafe_text(text: str) -> bool:
    lowered = text.lower()
    return any(pattern.lower() in lowered for pattern in (*UNSAFE_PROMPT_PATTERNS, *PROMPT_INJECTION_PATTERNS))


def clean_prompt_text(value: object, limit: int) -> str:
    text = str(value or "")
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def normalize_negative_constraints(values: list[str] | None) -> list[str]:
    safe_values: list[str] = []
    for value in values or []:
        cleaned = clean_prompt_text(value, 32)
        if cleaned and not prompt_has_unsafe_text(cleaned):
            safe_values.append(cleaned)
    safe_values = safe_values[:4]
    safe_values.extend(DEFAULT_NEGATIVE_CONSTRAINTS)
    return list(dict.fromkeys(safe_values))[:10]


def append_prompt_constraints(base_prompt: str, negative_constraints: list[str]) -> str:
    constraints = list(dict.fromkeys([*MANDATORY_BACKGROUND_CONSTRAINTS, *negative_constraints]))
    suffix = "硬性约束：" + "，".join(constraints) + "。"
    available = max(80, PROMPT_MAX_CHARS - len(suffix) - 1)
    base = clean_prompt_text(base_prompt, available)
    prompt = f"{base}。{suffix}" if base and not base.endswith(("。", ".", "！", "!")) else f"{base}{suffix}"
    return prompt[:PROMPT_MAX_CHARS]


def meaningful_visual_terms(text: str) -> list[str]:
    terms: list[str] = []
    generic_fragments = (
        "写实",
        "真实",
        "竖屏",
        "横屏",
        "短视频",
        "背景",
        "猫",
        "meme",
        "无文字",
        "无可读文字",
        "无人物",
        "无人物主体",
        "干净",
        "好看",
        "高清",
        "构图",
        "画面",
        "下方",
        "无遮挡",
        "自然",
        "适合",
        "叠加",
        "抠像",
        "动画",
        "不要",
        "绿色幕布",
        "纯色块",
        "留出",
    )
    for chunk in re.split(r"[，,。、；;\s]+", clean_prompt_text(text, PROMPT_BASE_MAX_CHARS)):
        reduced = chunk.lower()
        for fragment in generic_fragments:
            reduced = reduced.replace(fragment.lower(), "")
        if len(reduced.strip("-_:/()（）")) >= 2:
            terms.append(chunk)
    return terms


def fallback_background_prompt(theme: str, caption: str, scenes: list[str]) -> str:
    scene_text = "，".join(scenes[:4]) or "城市生活"
    return (
        f"竖屏短视频背景，猫 meme 社会现实主题：{clean_prompt_text(theme, 72)}。"
        f"分镜：{clean_prompt_text(caption, 72)}，场景关键词：{scene_text}。"
        "写实但略带荒诞喜剧感，方便后期叠加抠像猫动画。"
    )


def background_description(
    theme: str,
    caption: str,
    need: str,
    scenes: list[str],
    prompt: str,
    source: str,
) -> str:
    parts = [
        need,
        clean_prompt_text(caption, 64),
        "，".join(scenes[:4]),
        "Agent 受约束提示词" if source == "agent" else "规则兜底提示词",
    ]
    text = "｜".join(item for item in parts if item)
    return clean_prompt_text(text or prompt or theme, 180)


def refresh_assets_index(project_root: Path) -> None:
    subprocess.run(
        ["node", str(project_root / "scripts" / "index-assets.mjs")],
        cwd=str(project_root),
        check=True,
        capture_output=True,
        text=True,
    )


def download_url(url: str, out_file: Path, attempts: int = 3) -> None:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "MaoMeme/1.0"})
            with urllib.request.urlopen(request, timeout=90) as response:
                out_file.write_bytes(response.read())
            return
        except Exception as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(1.5 * attempt)
    raise RuntimeError(f"Seedream image download failed: {safe_error(last_error)}")


def slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff_-]+", "-", value).strip("-")
    return value[:48] or "agent-fill"


def safe_error(exc: Exception | None) -> str:
    text = str(exc or "")
    text = re.sub(r"https?://\S+", "[url]", text)
    text = re.sub(r"(api[_-]?key|authorization|bearer)\S*", "[secret]", text, flags=re.I)
    return text[:180]
