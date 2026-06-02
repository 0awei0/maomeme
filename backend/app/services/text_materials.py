from __future__ import annotations

import json
from typing import Any

from ..core.config import get_settings


def load_text_materials() -> dict[str, Any]:
    path = get_settings().PROJECT_ROOT / "data" / "text-materials" / "social-reality.json"
    if not path.exists():
        return {"topics": []}
    return json.loads(path.read_text(encoding="utf-8"))


def load_preset_scenes() -> dict[str, Any]:
    path = get_settings().PROJECT_ROOT / "data" / "preset-scenes" / "social-scenes.json"
    if not path.exists():
        return {"scenes": []}
    return json.loads(path.read_text(encoding="utf-8"))


def select_topic(theme: str, materials: dict[str, Any] | None = None) -> dict[str, Any] | None:
    materials = materials or load_text_materials()
    weak_keywords = {"年轻人", "压力", "父母", "选择", "稳定"}
    best_score = 0.0
    best_topic = None
    for topic in materials.get("topics", []):
        score = 0.0
        for keyword in topic.get("keywords", []):
            if keyword in theme:
                score += 0.25 if keyword in weak_keywords else min(3.0, max(1.0, len(keyword) / 2))
        if topic.get("title", "") and topic["title"] in theme:
            score += 3
        if any(strong in theme and strong in " ".join(topic.get("keywords", [])) for strong in ("租房", "催婚", "彩礼", "买房", "考研", "考公", "加班", "简历")):
            score += 2
        if score > best_score:
            best_score = score
            best_topic = topic
    return best_topic


def topic_for_agent(theme: str) -> dict[str, Any]:
    topic = select_topic(theme)
    if not topic:
        return {"preset_scenes": matching_preset_scenes(theme)}
    return {
        "id": topic.get("id", ""),
        "title": topic.get("title", ""),
        "facts": topic.get("facts", []),
        "tensions": topic.get("tensions", []),
        "meme_angles": topic.get("meme_angles", []),
        "beat_seed": topic.get("beat_seed", {}),
        "preferred_assets": topic.get("preferred_assets", {}),
        "preset_scenes": matching_preset_scenes(theme, topic),
        "sources": topic.get("sources", []),
    }


def matching_preset_scenes(theme: str, topic: dict[str, Any] | None = None, limit: int = 4) -> list[dict[str, Any]]:
    scene_data = load_preset_scenes()
    topic_keywords = set(str(item) for item in (topic or {}).get("keywords", []))
    scored: list[tuple[float, dict[str, Any]]] = []
    for scene in scene_data.get("scenes", []):
        score = 0.0
        for trigger in scene.get("triggers", []):
            if trigger in theme:
                score += 3.0
            if trigger in topic_keywords:
                score += 1.2
        for keyword in scene.get("keywords", []):
            if keyword in theme:
                score += 1.0
        if score:
            scored.append((score, scene))
    scored.sort(key=lambda item: (-item[0], item[1].get("id", "")))
    return [
        {
            "id": scene.get("id", ""),
            "title": scene.get("title", ""),
            "keywords": scene.get("keywords", []),
            "recommended_backgrounds": scene.get("recommended_backgrounds", []),
            "seedream_prompt": scene.get("seedream_prompt", ""),
            "use_cases": scene.get("use_cases", []),
        }
        for _, scene in scored[:limit]
    ]
