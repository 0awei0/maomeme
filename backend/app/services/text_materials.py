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
    if is_family_memory_theme(theme):
        return family_memory_context(theme)
    topic = select_topic(theme)
    if not topic:
        return {"preset_scenes": matching_preset_scenes(theme)}
    return {
        "id": topic.get("id", ""),
        "title": topic.get("title", ""),
        "keywords": topic.get("keywords", []),
        "facts": topic.get("facts", []),
        "tensions": topic.get("tensions", []),
        "meme_angles": topic.get("meme_angles", []),
        "beat_seed": topic.get("beat_seed", {}),
        "preferred_assets": topic.get("preferred_assets", {}),
        "preset_scenes": matching_preset_scenes(theme, topic),
        "sources": topic.get("sources", []),
    }


def is_family_memory_theme(theme: str) -> bool:
    family_terms = ("父亲", "爸爸", "父母", "妈妈", "母亲", "家里", "家庭", "亲情", "父爱", "无声的爱")
    memory_terms = ("小时候", "童年", "长大", "多年后", "回忆", "偷吃", "默许", "专门", "留给", "最右边")
    return any(word in theme for word in family_terms) and any(word in theme for word in memory_terms)


def family_memory_context(theme: str) -> dict[str, Any]:
    has_adult_pressure = any(word in theme for word in ("升学", "就业", "压力", "独自扛", "自己扛", "家人的支持", "支持一直都在"))
    tensions = [
        "孩子以为自己在偷吃，父亲其实一直默默允许。",
        "热闹烧烤店里的两串食物，承载的是长大后才懂的父爱。",
    ]
    meme_angles = [
        "小时候以为自己瞒天过海，长大才发现大人什么都知道。",
        "最右边两串不是漏洞，是父亲留给孩子的小暗号。",
        "笑点轻轻落在偷吃，情绪落在父亲无声的爱。",
    ]
    beat_seed = {
        "hook": "小时候我总偷最右边两串",
        "setup": "父亲转身招呼客人",
        "escalation": "我以为自己瞒天过海",
        "twist": "多年后才知道那是父亲专门留的",
        "punchline": "长大后才懂那份无声的爱",
    }
    if has_adult_pressure:
        tensions.insert(1, "长大后以为升学就业压力只能自己扛，其实家人的支持一直都在。")
        meme_angles.insert(1, "童年的两串烧烤，和长大后的支持感形成前后呼应。")
        beat_seed["proof"] = "长大后压力也想自己扛"
        beat_seed["punchline"] = "原来家人的支持一直都在"
    return {
        "id": "childhood_family_memory",
        "title": "童年亲情回忆",
        "keywords": ["童年", "父亲", "家庭", "亲情", "烧烤店", "偷吃", "默许", "长大后"],
        "facts": [
            "小时候以为瞒过父亲的小动作，多年后才发现那是父亲主动留下的照顾。",
        ],
        "tensions": tensions,
        "meme_angles": meme_angles,
        "beat_seed": beat_seed,
        "preferred_assets": {
            "backgrounds": ["家庭饭桌", "父母沟通", "小店", "夜晚店铺", "家里", "温暖室内"],
            "motions": ["偷看", "探头", "震惊", "委屈", "可爱", "安静"],
        },
        "preset_scenes": matching_preset_scenes(theme),
        "sources": [],
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
