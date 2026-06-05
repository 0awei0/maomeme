from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..core.config import get_settings


LIBRARY_ROOT = Path("data") / "viral-structures" / "baokuan-maomeme"
LOW_PRIORITY_IDS = {"bkmm-007-抖音202663-083115"}

RELATIONSHIP_TERMS = (
    "情侣", "恋爱", "男友", "女友", "对象", "男生", "女生", "亲密关系", "伴侣",
)
EMOTIONAL_RELATIONSHIP_TERMS = (
    "态度", "安慰", "情绪价值", "情绪", "委屈", "沟通", "频道", "脑回路", "吵架",
    "解释", "解决问题", "先别问", "我只是想", "不安慰", "受委屈", "冷战", "哄",
)
FINANCIAL_RELATIONSHIP_TERMS = (
    "彩礼", "首付", "房贷", "买房", "婚房", "共同预算", "预算", "账单", "存款",
    "AA", "aa", "工资", "支出", "开销", "花钱", "房租", "租房", "押金", "水电",
    "网费", "贷款", "还贷", "婚礼钱", "彩礼钱",
)
STREET_FOOD_BUSINESS_TERMS = (
    "摆摊", "摊位费", "摊位", "摊车", "街边摊", "小吃摊", "地摊", "餐车",
    "卖烤肠", "烤肠摊", "小摊", "开张", "出摊", "收摊", "成本", "利润",
    "降价", "特价", "买一送一", "竞争", "内卷", "赊账", "顾客", "煤气",
)
NON_FINANCIAL_RELATIONSHIP_MARKERS = (
    "不是金钱", "不是钱", "不为钱", "不是账单", "不是预算", "不是房租", "不是彩礼",
    "不是买房", "不是金钱账单", "不是现实账单",
)


def is_relationship_context(text: str) -> bool:
    return any(word in str(text) for word in RELATIONSHIP_TERMS)


def is_emotional_relationship_context(text: str) -> bool:
    value = str(text)
    return is_relationship_context(value) and any(word in value for word in EMOTIONAL_RELATIONSHIP_TERMS)


def is_financial_relationship_context(text: str) -> bool:
    value = str(text)
    if not any(word in value for word in FINANCIAL_RELATIONSHIP_TERMS):
        return False
    if is_emotional_relationship_context(value) and any(marker in value for marker in NON_FINANCIAL_RELATIONSHIP_MARKERS):
        return False
    return True


def is_street_food_business_context(text: str) -> bool:
    value = str(text)
    if is_emotional_relationship_context(value) and not is_financial_relationship_context(value):
        return False
    return any(word in value for word in STREET_FOOD_BUSINESS_TERMS)


@lru_cache(maxsize=1)
def load_viral_structures() -> list[dict[str, Any]]:
    root = get_settings().PROJECT_ROOT / LIBRARY_ROOT
    index_path = root / "index.json"
    verification_path = root / "verification-report.json"
    if not index_path.exists():
        return []
    index = read_json(index_path, {})
    verification = read_json(verification_path, {})
    verification_by_id = {
        str(item.get("id", "")): item
        for item in verification.get("entries", [])
        if isinstance(item, dict)
    }
    entries: list[dict[str, Any]] = []
    for item in index.get("entries", []):
        if not isinstance(item, dict):
            continue
        video_id = str(item.get("id", ""))
        structure = read_json(root / video_id / "structure.json", {})
        if not structure:
            continue
        verification_item = verification_by_id.get(video_id, {})
        if verification_item and verification_item.get("verdict") not in {"pass", "review"}:
            continue
        entries.append(compact_entry(item, structure, verification_item))
    return entries


def read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def compact_entry(index_item: dict[str, Any], structure: dict[str, Any], verification: dict[str, Any]) -> dict[str, Any]:
    summary = structure.get("video_summary") if isinstance(structure.get("video_summary"), dict) else {}
    reusable = structure.get("reusable_patterns") if isinstance(structure.get("reusable_patterns"), dict) else {}
    storyboard = structure.get("asset_plan", {}).get("storyboard", []) if isinstance(structure.get("asset_plan"), dict) else []
    audio = structure.get("audio_track") if isinstance(structure.get("audio_track"), dict) else {}
    packaging = structure.get("subtitle_packaging") if isinstance(structure.get("subtitle_packaging"), dict) else {}
    entry = {
        "id": index_item.get("id") or structure.get("video_id", ""),
        "title": summary.get("title") or index_item.get("title", ""),
        "topic": summary.get("primary_topic") or index_item.get("primary_topic", ""),
        "tone": summary.get("overall_tone") or index_item.get("overall_tone", ""),
        "meme_type": summary.get("meme_type") or index_item.get("meme_type", ""),
        "one_sentence": summary.get("one_sentence", ""),
        "shot_count": len(storyboard),
        "score": float(verification.get("score") or 0) if verification else 0.0,
        "script_templates": list_of_text(reusable.get("script_templates")),
        "shot_templates": list_of_text(reusable.get("shot_templates")),
        "cat_action_templates": list_of_text(reusable.get("cat_action_templates")),
        "background_templates": list_of_text(reusable.get("background_templates")),
        "audio_templates": list_of_text(reusable.get("audio_templates")),
        "suitable_topics": list_of_text(reusable.get("suitable_topics")),
        "storyboard": compact_storyboard(storyboard),
        "audio_style": "；".join(
            item
            for item in [
                str(audio.get("bgm_style", "")),
                str(audio.get("voice_style", "")),
                "、".join(str(sfx) for sfx in audio.get("sfx", [])[:4]) if isinstance(audio.get("sfx"), list) else "",
            ]
            if item
        ),
        "subtitle_style": "；".join(
            item
            for item in [
                str(packaging.get("subtitle_style", "")),
                str(packaging.get("bubble_or_dialogue_style", "")),
                "、".join(str(word) for word in packaging.get("emphasis_words", [])[:5]) if isinstance(packaging.get("emphasis_words"), list) else "",
            ]
            if item
        ),
    }
    entry["structure_tags"] = structure_tags_for_entry(entry)
    if str(entry.get("id", "")) in LOW_PRIORITY_IDS or len(storyboard) < 4:
        entry["priority"] = "low"
        entry["low_priority_reason"] = "storyboard 镜头过少，适合作辅助梗点，不作为主迁移结构。"
    else:
        entry["priority"] = "normal"
    return entry


def compact_storyboard(storyboard: list[Any]) -> list[dict[str, Any]]:
    slots: list[dict[str, Any]] = []
    for index, item in enumerate(storyboard[:8], start=1):
        if not isinstance(item, dict):
            continue
        slots.append(
            {
                "shot_id": str(item.get("shot_id") or item.get("id") or index),
                "beat": str(item.get("beat", "")),
                "script": str(item.get("script", "")),
                "joke_point": str(item.get("joke_point", "")),
                "background": stringify_short(item.get("background")),
                "cats": stringify_short(item.get("cats")),
                "audio": stringify_short(item.get("audio")),
                "subtitle": stringify_short(item.get("subtitle")),
                "duration": safe_float(item.get("duration"), 3.0),
            }
        )
    return slots


def safe_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def list_of_text(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def stringify_short(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "；".join(stringify_short(item) for item in value if stringify_short(item))
    if isinstance(value, dict):
        for key in ("setting", "description", "text", "voice", "bgm"):
            text = stringify_short(value.get(key))
            if text:
                return text
        return json.dumps(value, ensure_ascii=False)
    return "" if value is None else str(value)


def viral_references_for_theme(theme: str, text_context: dict[str, Any] | None = None, limit: int = 3) -> list[dict[str, Any]]:
    entries = load_viral_structures()
    if not entries:
        return []
    category = infer_theme_category(theme)
    context = text_context or {}
    query_terms = tokenize_theme(
        " ".join(
            [
                theme,
                str(context.get("title", "")),
                " ".join(str(item) for item in context.get("keywords", [])[:10]),
                " ".join(str(item) for item in context.get("meme_angles", [])[:5]),
                " ".join(str(item) for item in context.get("tensions", [])[:5]),
            ]
        )
    )
    scored = [(viral_score(entry, query_terms, theme), entry) for entry in entries]
    scored.sort(key=lambda item: (-item[0], -float(item[1].get("score") or 0), str(item[1].get("id", ""))))
    selected = curated_references(category, entries, limit)
    for hybrid in hybrid_references_for_theme(theme, category, entries):
        if hybrid not in selected:
            selected.append(hybrid)
        if len(selected) >= limit:
            break
    for score, entry in scored:
        if score <= 0:
            continue
        if len(selected) == 0 and is_low_priority_reference(entry):
            continue
        if excluded_for_category(category, entry):
            continue
        if entry not in selected:
            selected.append(entry)
        if len(selected) >= limit:
            break
    if len(selected) < limit:
        for _, entry in scored:
            if len(selected) == 0 and is_low_priority_reference(entry):
                continue
            if excluded_for_category(category, entry):
                continue
            if entry not in selected:
                selected.append(entry)
            if len(selected) >= limit:
                break
    return selected[:limit]


def is_low_priority_reference(entry: dict[str, Any]) -> bool:
    return str(entry.get("priority", "")) == "low" or str(entry.get("id", "")) in LOW_PRIORITY_IDS or int(entry.get("shot_count") or 0) < 4


def excluded_for_category(category: str, entry: dict[str, Any]) -> bool:
    text = " ".join(
        str(item)
        for item in [
            entry.get("title", ""),
            entry.get("topic", ""),
            entry.get("one_sentence", ""),
            " ".join(entry.get("script_templates", [])),
        ]
    )
    excluded = {
        "leave": ("裸贷", "冲食堂", "班主任", "童年", "生日", "情侣"),
        "career": ("裸贷", "冲食堂", "班主任", "童年", "生日"),
        "office": ("裸贷", "冲食堂", "班主任", "童年", "生日"),
        "exam": ("裸贷", "烤鸡腿", "销售", "压岁钱"),
        "street_food": ("请假", "打120", "裸贷", "班主任"),
        "family": ("招聘", "求职", "岗位", "HR", "销售", "老干妈", "室友", "摊位内卷", "三年经验"),
    }.get(category, ())
    return any(word in text for word in excluded)


def curated_references(category: str, entries: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    preferred_ids = {
        "leave": ["bkmm-001-抖音202662-534438", "bkmm-017-抖音202663-378934", "bkmm-003-抖音202663-009401"],
        "career": ["bkmm-003-抖音202663-009401", "bkmm-024-抖音202663-525083", "bkmm-001-抖音202662-534438"],
        "office": ["bkmm-001-抖音202662-534438", "bkmm-003-抖音202663-009401", "bkmm-017-抖音202663-378934"],
        "exam": ["bkmm-012-抖音202663-122472", "bkmm-033-抖音202663-666778", "bkmm-011-抖音202663-117937"],
        "street_food": ["bkmm-034-抖音202663-693881", "bkmm-029-抖音202663-574311", "bkmm-025-抖音202663-534479"],
        "relationship": ["bkmm-027-抖音202663-550698", "bkmm-030-抖音202663-628799", "bkmm-022-抖音202663-502975"],
        "family": ["bkmm-041-抖音202663-963163", "bkmm-021-抖音202663-494793", "bkmm-042-抖音202663-978042"],
    }.get(category, [])
    by_id = {str(item.get("id", "")): item for item in entries}
    selected = [by_id[item_id] for item_id in preferred_ids if item_id in by_id and not is_low_priority_reference(by_id[item_id])]
    return selected[: max(0, min(limit, 2))]


def hybrid_references_for_theme(theme: str, category: str, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(item.get("id", "")): item for item in entries}
    refs: list[dict[str, Any]] = []
    if category == "career" and any(word in theme for word in ("烤肠", "香肠", "摆摊", "夜市", "小吃摊", "地摊", "餐车")):
        for item_id in ("bkmm-034-抖音202663-693881", "bkmm-029-抖音202663-574311"):
            item = by_id.get(item_id)
            if item and not is_low_priority_reference(item):
                refs.append(item)
    if category == "leave":
        item = by_id.get("bkmm-003-抖音202663-009401")
        if item:
            refs.append(item)
    return refs


def viral_score(entry: dict[str, Any], query_terms: set[str], theme: str) -> float:
    core_text = " ".join(
        [
            str(entry.get("title", "")),
            str(entry.get("topic", "")),
            str(entry.get("tone", "")),
            str(entry.get("meme_type", "")),
            str(entry.get("one_sentence", "")),
            " ".join(entry.get("script_templates", [])),
            " ".join(entry.get("shot_templates", [])),
            " ".join(entry.get("background_templates", [])),
        ]
    )
    portable_text = " ".join(
        [
            " ".join(entry.get("suitable_topics", [])),
            " ".join(entry.get("cat_action_templates", [])),
            " ".join(entry.get("audio_templates", [])),
        ]
    )
    text = f"{core_text} {portable_text}"
    score = sum(3.0 for term in query_terms if term and term in text)
    category = infer_theme_category(theme)
    category_targets = {
        "leave": ("请假", "病假", "老板", "不批", "120", "职场", "打工", "办公室"),
        "career": ("职场", "打工", "销售", "办公室", "老板", "销冠", "上班", "工作"),
        "office": ("职场", "打工", "销售", "办公室", "老板", "销冠", "上班", "工作"),
        "exam": ("考试", "知识", "学生", "校园", "教室", "大学"),
        "campus": ("大学", "宿舍", "学生", "校园", "教室", "食堂"),
        "street_food": ("摊", "夜市", "市井", "小吃", "鸡腿", "餐馆", "街头"),
        "relationship": ("情侣", "结婚", "房", "情感", "家庭"),
        "family": ("家庭", "妈妈", "爸爸", "父亲", "父母", "亲子", "亲情", "父爱", "童年", "春节"),
    }
    if category:
        targets = category_targets.get(category, ())
        if any(word in core_text for word in targets):
            score += 30.0
        elif any(word in portable_text for word in targets):
            score += 5.0
        elif category in {"career", "office"} and any(word in text for word in ("宿舍", "童年", "暑假", "生日", "考试")):
            score -= 14.0
        elif category == "exam" and any(word in text for word in ("销售", "夜市", "情侣", "压岁钱")):
            score -= 8.0
    buckets = [
        (("工作", "简历", "岗位", "面试", "HR", "就业", "招聘", "offer"), ("职场", "打工", "销售", "上班", "办公室", "加班", "老板", "销冠")),
        (("上班", "老板", "加班", "会议", "KPI", "内卷"), ("职场", "打工", "销售", "上班", "办公室", "加班", "老板", "销冠")),
        (("大学", "宿舍", "食堂", "同学", "校园"), ("大学", "宿舍", "学生", "校园", "教室")),
        (("考研", "考公", "考试", "上岸"), ("大学", "学生", "校园", "考试", "教室", "知识")),
        (("烤肠", "香肠", "摆摊", "小吃", "夜市", "地摊", "餐车"), ("摊", "夜市", "市井", "小吃", "鸡腿", "餐馆")),
        (("结婚", "彩礼", "买房", "房贷", "恋爱", "情侣"), ("情侣", "结婚", "房", "情感", "家庭")),
        (("父母", "妈妈", "爸爸", "父亲", "母亲", "家庭", "亲情", "父爱", "童年", "小时候", "亲戚", "压岁钱"), ("家庭", "妈妈", "爸爸", "父亲", "父母", "亲子", "亲情", "春节")),
    ]
    for triggers, targets in buckets:
        if any(word in theme for word in triggers) and any(word in text for word in targets):
            score += 10.0
    if any(word in theme for word in ("工作", "简历", "岗位", "面试", "就业", "招聘", "offer")):
        if any(word in text for word in ("职场", "打工", "销售", "办公室", "老板", "销冠")):
            score += 16.0
        if any(word in text for word in ("宿舍", "童年", "暑假", "考试")):
            score -= 6.0
    if any(word in theme for word in ("周一", "星期一", "上班综合症", "不想上班", "闹钟")):
        if any(word in text for word in ("上班", "办公室", "老板", "请假", "职场", "打工")):
            score += 16.0
        if any(word in text for word in ("销售", "招聘", "求职", "摊", "夜市")):
            score -= 5.0
    if any(word in theme for word in ("请假", "病假", "120", "不批准", "不批假")):
        if any(word in text for word in ("请假", "病假", "120", "老板", "不批")):
            score += 28.0
        if any(word in text for word in ("销售", "烤鸡腿", "情侣", "童年")):
            score -= 8.0
    score += min(float(entry.get("score") or 0) / 20.0, 5.0)
    if is_low_priority_reference(entry):
        score -= 40.0
    return score


def infer_theme_category(theme: str) -> str:
    if is_family_memory_theme(theme):
        return "family"
    if any(word in theme for word in ("请假", "病假", "老板不批", "不批准", "120", "急救")):
        return "leave"
    if any(word in theme for word in ("工作", "简历", "岗位", "面试", "就业", "招聘", "offer", "HR", "应届生", "求职")):
        return "career"
    if any(word in theme for word in ("租房", "房租", "押金", "合租", "通勤", "搬家", "中介", "隔断间")):
        return "rent"
    if any(word in theme for word in ("考研", "考公", "上岸", "考试")):
        return "exam"
    if any(word in theme for word in ("上班", "老板", "加班", "会议", "KPI", "内卷")):
        return "office"
    if any(word in theme for word in ("结婚", "彩礼", "买房", "恋爱", "情侣")):
        return "relationship"
    if any(word in theme for word in ("父母", "妈妈", "爸爸", "父亲", "母亲", "家庭", "亲情", "父爱", "压岁钱", "亲戚")):
        return "family"
    if any(word in theme for word in ("烤肠", "香肠", "烧烤", "摆摊", "夜市", "小吃", "地摊", "餐车")):
        return "street_food"
    if any(word in theme for word in ("工作", "简历", "岗位", "面试", "就业", "招聘", "offer")):
        return "career"
    if any(word in theme for word in ("大学", "宿舍", "食堂", "同学", "校园")):
        return "campus"
    return ""


def is_family_memory_theme(theme: str) -> bool:
    family_terms = ("父亲", "爸爸", "父母", "妈妈", "母亲", "家里", "家庭", "亲情", "父爱", "无声的爱")
    memory_terms = ("小时候", "童年", "长大", "多年后", "回忆", "偷吃", "默许", "专门", "留给", "最右边")
    return any(word in theme for word in family_terms) and any(word in theme for word in memory_terms)


def tokenize_theme(text: str) -> set[str]:
    terms = {item for item in text.replace("/", " ").replace("、", " ").replace("，", " ").split() if len(item) >= 2}
    common = [
        "工作", "简历", "岗位", "面试", "就业", "上班", "老板", "会议", "加班", "内卷",
        "大学", "宿舍", "食堂", "同学", "校园", "考研", "考公", "考试", "焦虑",
        "烤肠", "香肠", "摆摊", "夜市", "小吃", "地摊", "餐车",
        "结婚", "彩礼", "买房", "租房", "房租", "押金", "通勤", "房贷", "恋爱", "情侣",
        "父母", "妈妈", "爸爸", "父亲", "母亲", "家庭", "亲情", "父爱", "童年", "小时候", "压岁钱", "亲戚",
    ]
    for word in common:
        if word in text:
            terms.add(word)
    return terms


def viral_reference_prompt(references: list[dict[str, Any]]) -> str:
    if not references:
        return "暂无可用爆款结构参考。"
    lines: list[str] = []
    for index, ref in enumerate(references, start=1):
        storyboard = ref.get("storyboard", [])
        beats = " -> ".join(
            f"{item.get('beat', '')}:{short(item.get('script', ''), 16)}"
            for item in storyboard[:6]
            if isinstance(item, dict)
        )
        lines.extend(
            [
                f"{index}. [{ref.get('id')}] {ref.get('title')}｜{ref.get('topic')}｜{ref.get('tone')}",
                f"   剧本模板：{'；'.join(ref.get('script_templates', [])[:2])}",
                f"   分镜模板：{'；'.join(ref.get('shot_templates', [])[:2])}",
                f"   分镜节奏：{beats}",
                f"   背景模板：{'；'.join(ref.get('background_templates', [])[:3])}",
                "   猫动作策略：只学习镜头功能和表演节奏，新视频猫动作按新主题重新匹配。",
                f"   声音/字幕：{short(ref.get('audio_style', ''), 60)}｜{short(ref.get('subtitle_style', ''), 60)}",
            ]
        )
    return "\n".join(lines)


def compact_fewshot_examples(references: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    for ref in references[:limit]:
        storyboard = ref.get("storyboard", []) if isinstance(ref.get("storyboard"), list) else []
        shots = []
        for item in storyboard[:8]:
            if not isinstance(item, dict):
                continue
            shots.append(
                {
                    "shot_id": item.get("shot_id", ""),
                    "beat": item.get("beat", ""),
                    "script": item.get("script", ""),
                    "joke_point": item.get("joke_point", ""),
                    "background_slot": item.get("background", ""),
                    "cat_action_need": "按新主题和镜头功能重新选择猫动作，不继承原 cats。",
                    "audio_need": item.get("audio", ""),
                    "subtitle_packaging": item.get("subtitle", ""),
                    "rewrite_note": "迁移节奏、角色关系和笑点机制；台词、社会角色和场景必须按新主题重写。",
                }
            )
        examples.append(
            {
                "id": ref.get("id", ""),
                "title": ref.get("title", ""),
                "topic": ref.get("topic", ""),
                "structure_tags": ref.get("structure_tags", []),
                "role_relationship": role_relationship_for_entry(ref),
                "joke_mechanism": first_text_value(ref.get("script_templates")) or ref.get("one_sentence", ""),
                "shot_scripts": shots,
                "background_slots": list(ref.get("background_templates", []) or [])[:5],
                "cat_action_needs": list(ref.get("cat_action_templates", []) or [])[:5],
                "audio_subtitle_rhythm": "；".join(item for item in [str(ref.get("audio_style", "")), str(ref.get("subtitle_style", ""))] if item),
                "suitable_topics": list(ref.get("suitable_topics", []) or [])[:6],
                "priority": ref.get("priority", "normal"),
            }
        )
    return examples


def build_migration_blueprint(
    theme: str,
    references: list[dict[str, Any]],
    migration: dict[str, Any] | None = None,
    text_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    uploaded = uploaded_reference_from_migration(migration or {})
    public_refs = [ref for ref in references if isinstance(ref, dict)]
    if uploaded:
        primary = uploaded
        supporting = public_refs[:2]
        source_priority = "uploaded_viral_first"
    else:
        primary = next((ref for ref in public_refs if not is_low_priority_reference(ref)), public_refs[0] if public_refs else {})
        supporting = [ref for ref in public_refs if ref is not primary][:2]
        source_priority = "public_viral_library"

    selected = [item for item in [primary, *supporting] if item]
    primary_storyboard = primary.get("storyboard", []) if isinstance(primary.get("storyboard"), list) else []
    roles = blueprint_roles_for_theme(theme, len(primary_storyboard))
    shots: list[dict[str, Any]] = []
    for index, role in enumerate(roles):
        source = primary_storyboard[min(index, len(primary_storyboard) - 1)] if primary_storyboard else {}
        if not isinstance(source, dict):
            source = {}
        shots.append(
            {
                "slot": role,
                "source_viral_id": primary.get("id", ""),
                "source_viral_title": primary.get("title", ""),
                "source_shot_id": source.get("shot_id", index + 1),
                "source_beat": source.get("beat", ""),
                "source_script": source.get("script", ""),
                "source_joke_point": source.get("joke_point", ""),
                "source_background": source.get("background", ""),
                "transfer_role": transfer_role_for_slot(role, theme),
                "rewrite_direction": rewrite_direction_for_slot(theme, role, source, text_context or {}),
                "background_requirement": background_requirement_for_slot(theme, role),
                "cat_action_requirement": cat_requirement_for_slot(role),
                "audio_requirement": source.get("audio", "") or "轻快 BGM，字幕卡点清楚。",
                "subtitle_packaging": source.get("subtitle", "") or subtitle_requirement_for_slot(role),
                "do_not_copy": "不得复制原台词、原人物关系、剧情母题、关键物件、原始场景或原 cats；只学习脚本结构、镜头节奏、字幕样式、画面包装、转场、BGM卡点、情绪递进和反转机制。",
            }
        )

    return {
        "version": "viral-fewshot-blueprint-v1",
        "theme": theme,
        "source_priority": source_priority,
        "primary_reference": reference_summary(primary),
        "supporting_references": [reference_summary(item) for item in supporting],
        "fewshot_examples": compact_fewshot_examples(selected, limit=3),
        "structure_tags": list(dict.fromkeys(tag for item in selected for tag in item.get("structure_tags", [])))[:8],
        "human_role_policy": "剧本文案写学生、打工人、老板、同事、HR、摊主等人类社会角色；猫只在分镜素材层表现这些角色。",
        "ending_policy": "结尾只能是识别规则、换策略、互助、喘口气或荒诞反讽，不能写猫解决社会问题。",
        "shots": shots,
    }


def migration_blueprint_prompt(blueprint: dict[str, Any]) -> str:
    if not blueprint:
        return "暂无迁移蓝图。"
    return json.dumps(
        {
            "migration_blueprint": blueprint,
            "instructions": [
                "必须先遵循 migration_blueprint 的主结构，再生成候选剧本。",
                "参考视频是创作方法库，不是剧情素材库；学习的是脚本结构、镜头节奏、字幕样式、画面包装、转场、BGM卡点、情绪递进和反转机制。",
                "每个候选都要迁移 primary_reference 的镜头功能，并吸收 supporting_references 的一个辅助技法。",
                "剧本文案写人类社会角色，不要把猫写成解决社会问题的主体。",
                "不得复制 few-shot 的剧情母题、人物关系、关键物件、原始场景或原台词；新剧本必须服从新主题的核心人物关系、场景和情绪落点。",
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


def uploaded_reference_from_migration(migration: dict[str, Any]) -> dict[str, Any]:
    viral = migration.get("viral_analysis") if isinstance(migration.get("viral_analysis"), dict) else {}
    summary = viral.get("summary") if isinstance(viral.get("summary"), dict) else {}
    storyboard = viral.get("storyboard") if isinstance(viral.get("storyboard"), list) else []
    transfer_slots = viral.get("transfer_slots") if isinstance(viral.get("transfer_slots"), list) else []
    if not summary and not storyboard and not transfer_slots:
        return {}
    compact_board: list[dict[str, Any]] = []
    source_items = storyboard or transfer_slots
    for index, item in enumerate(source_items[:8], start=1):
        if not isinstance(item, dict):
            continue
        compact_board.append(
            {
                "shot_id": str(index),
                "beat": str(item.get("beat") or item.get("slot") or f"shot-{index}"),
                "script": str(item.get("script") or item.get("rewrite") or ""),
                "joke_point": str(item.get("packaging_requirement") or item.get("keep") or ""),
                "background": str(item.get("background_requirement") or item.get("background") or ""),
                "cats": str(item.get("cat_requirement") or item.get("cat") or ""),
                "audio": str(item.get("audio_requirement") or ""),
                "subtitle": str(item.get("packaging_requirement") or ""),
                "duration": safe_float(item.get("duration"), 3.0),
            }
        )
    return {
        "id": str(migration.get("viral_analysis_id") or "uploaded-viral"),
        "title": summary.get("title") or "用户上传爆款参考",
        "topic": summary.get("one_sentence") or "",
        "tone": "",
        "meme_type": "uploaded_reference",
        "one_sentence": summary.get("one_sentence") or "",
        "shot_count": len(compact_board),
        "score": 100.0,
        "script_templates": list_of_text(summary.get("script_outline")),
        "shot_templates": list_of_text(summary.get("transferable_features")),
        "cat_action_templates": list_of_text(summary.get("cat_needs")),
        "background_templates": list_of_text(summary.get("background_needs")),
        "audio_templates": list_of_text(summary.get("audio_style")),
        "suitable_topics": [],
        "storyboard": compact_board,
        "audio_style": str(summary.get("audio_style") or ""),
        "subtitle_style": "",
        "structure_tags": ["用户上传爆款", *structure_tags_for_text(summary.get("one_sentence") or summary.get("title") or "")],
        "priority": "uploaded",
        "source": "uploaded_viral_video",
    }


def reference_summary(ref: dict[str, Any]) -> dict[str, Any]:
    if not ref:
        return {}
    return {
        "id": ref.get("id", ""),
        "title": ref.get("title", ""),
        "topic": ref.get("topic", ""),
        "structure_tags": list(ref.get("structure_tags", []) or [])[:6],
        "shot_count": ref.get("shot_count", 0),
        "priority": ref.get("priority", "normal"),
    }


def structure_tags_for_entry(entry: dict[str, Any]) -> list[str]:
    text = " ".join(
        str(item)
        for item in [
            entry.get("title", ""),
            entry.get("topic", ""),
            entry.get("one_sentence", ""),
            " ".join(entry.get("script_templates", [])),
            " ".join(entry.get("shot_templates", [])),
            " ".join(item.get("script", "") for item in entry.get("storyboard", []) if isinstance(item, dict)),
        ]
    )
    return structure_tags_for_text(text)


def structure_tags_for_text(text: str) -> list[str]:
    mapping = [
        (("请假", "老板", "120", "病假", "00后"), "职场请假反转"),
        (("工作", "简历", "岗位", "招聘", "HR", "销售", "销冠"), "职场求职压力"),
        (("上班", "会议", "加班", "老板", "KPI", "周一"), "上班内卷"),
        (("烤肠", "烤鸡腿", "夜市", "摆摊", "餐馆", "小吃"), "市井摊位反差"),
        (("考研", "考公", "考试", "上岸", "自习"), "考试选择焦虑"),
        (("大学", "宿舍", "室友", "食堂", "校园"), "校园生活共鸣"),
        (("情侣", "女友", "男友", "结婚", "恋爱"), "关系对话反差"),
        (("妈妈", "爸爸", "父亲", "母亲", "家庭", "亲情", "父爱", "压岁钱", "父母", "童年", "小时候"), "家庭关系反转"),
    ]
    tags = [label for triggers, label in mapping if any(word in text for word in triggers)]
    if any(word in text for word in ("反转", "整活", "荒诞", "误会")):
        tags.append("反转整活")
    if any(word in text for word in ("字幕", "弹窗", "大字", "黑边", "黄条")):
        tags.append("强字幕包装")
    return list(dict.fromkeys(tags))[:6] or ["通用猫 meme 节奏"]


def role_relationship_for_entry(ref: dict[str, Any]) -> str:
    text = " ".join(
        str(item)
        for item in [
            ref.get("title", ""),
            ref.get("topic", ""),
            " ".join(item.get("script", "") for item in ref.get("storyboard", []) if isinstance(item, dict)),
        ]
    )
    if any(word in text for word in ("老板", "请假", "工作", "销售", "HR")):
        return "打工人/学生 与 老板/HR/客户 的权力关系"
    if any(word in text for word in ("室友", "同学", "大学", "宿舍")):
        return "学生与同学/室友的同辈关系"
    if any(word in text for word in ("摊", "餐馆", "顾客", "买")):
        return "摊主/服务者 与 顾客/路人的交易关系"
    if any(word in text for word in ("情侣", "女友", "男友")):
        return "亲密关系中的试探和反差"
    if any(word in text for word in ("妈妈", "爸爸", "父亲", "母亲", "父母", "家庭", "亲情")):
        return "家庭成员之间的误会和反转"
    return "主角与外部规则/旁观者的反差关系"


def first_text_value(value: Any) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value or "")


def blueprint_roles_for_theme(theme: str, source_count: int) -> list[str]:
    target = 5 if source_count >= 5 else 4
    if any(word in theme for word in ("一分钟", "60秒")):
        target = 8
    roles = ["hook", "setup", "pressure", "twist", "punchline"]
    if target >= 6:
        roles.insert(3, "proof")
    if target >= 8:
        roles.insert(-1, "echo")
        roles.insert(-1, "cta")
    return roles[: max(4, min(target, 8))]


def transfer_role_for_slot(role: str, theme: str = "") -> str:
    if (
        infer_theme_category(theme) == "relationship"
        and is_emotional_relationship_context(theme)
        and not is_financial_relationship_context(theme)
    ):
        return {
            "hook": "2 秒内点出亲密关系里的沟通错位，停住观众。",
            "setup": "把人物关系落到一句具体对话和一个可拍的室内/日常场景。",
            "pressure": "把情绪落差、误解或沟通频道错位具体化。",
            "proof": "补一个双方各说各话的证据或旁观者共鸣。",
            "twist": "让角色意识到对方要的不是解决方案而是被理解。",
            "echo": "从这一对扩到很多亲密关系里的表达错位。",
            "punchline": "用一句反差台词收束，不把矛盾改成金钱账单冲突。",
            "cta": "轻落点，留一个情绪回落或沟通梗。",
        }.get(role, "迁移原镜头的节奏功能，按新主题重写情绪对话。")
    return {
        "hook": "2 秒内给出具体现实暴击，停住观众。",
        "setup": "把冲突落到一个可拍画面和一句短字幕。",
        "pressure": "把要求、账单、审批或排队压力具体化。",
        "proof": "补一个现实证据或旁观者共鸣。",
        "twist": "让角色发现规则更离谱，形成脑洞转向。",
        "echo": "从个人扩到群体，增加共鸣。",
        "punchline": "荒诞但合理收束，不能让猫万能解决。",
        "cta": "轻落点，留梗或情绪回落。",
    }.get(role, "迁移原镜头的剧情功能。")


def rewrite_direction_for_slot(theme: str, role: str, source: dict[str, Any], text_context: dict[str, Any]) -> str:
    title = str(text_context.get("title") or theme)
    if (
        infer_theme_category(theme) == "relationship"
        and is_emotional_relationship_context(theme)
        and not is_financial_relationship_context(theme)
    ):
        if role == "hook":
            return f"用“{title}”里的核心沟通错位开场，第一句直接点出情绪落差。"
        if role == "setup":
            return "写清情侣/亲密关系双方在同一件事上各说各话，不引入账单、房租或预算。"
        if role in {"pressure", "proof"}:
            return "把一方需要安慰、另一方只想解决问题的错频感铺具体。"
        if role == "twist":
            return "反转到“我要的是态度/理解，不是具体方案”，不转成财务压力。"
        if role == "punchline":
            return "用短促反差台词收束，让观众共鸣亲密沟通的错位。"
    if infer_theme_category(theme) == "family":
        if role == "hook":
            return f"用“{title}”里的童年小动作开场，第一句落到具体物件。"
        if role == "setup":
            return "写清亲子关系和烧烤店/饭桌场景，冲突是孩子误以为自己在偷吃。"
        if role in {"pressure", "proof"}:
            return "把孩子的小心虚、父亲的默许和多年后回忆铺具体。"
        if role == "twist":
            return "揭示最右边两串是父亲专门留下的，不写社会规则反讽。"
        if role == "punchline":
            return "温暖收束到长大后才懂父亲无声的爱。"
    if role == "hook":
        return f"用“{title}”里的具体动作开场，第一句就给现实暴击。"
    if role == "setup":
        return "写清人物关系和规则：学生/打工人/老板/HR/摊主等人类角色。"
    if role in {"pressure", "proof"}:
        return "把离谱要求、成本、审批、排队或群体共鸣说具体。"
    if role == "twist":
        return "可以脑洞转场，但要交代角色为什么会想到下一步。"
    if role == "punchline":
        return "用识别规则、换策略、互助或荒诞反讽收束，不写猫解决社会问题。"
    return "按新主题重写台词，保留原镜头节奏和包装密度。"


def background_requirement_for_slot(theme: str, role: str) -> str:
    category = infer_theme_category(theme)
    if (
        category == "relationship"
        and is_emotional_relationship_context(theme)
        and not is_financial_relationship_context(theme)
    ):
        return "室内对话/客厅/卧室/餐桌/街边停下说话背景，突出亲密关系沟通错位，避免财务压力画面。"
    defaults = {
        "career": "招聘软件/面试等待区/办公楼背景",
        "office": "工位/会议室/工作群弹窗背景",
        "street_food": "真实街边烤肠摊/夜市小吃摊背景",
        "exam": "自习室/教室/图书馆背景",
        "rent": "出租屋/账单桌面/通勤站台背景",
        "relationship": "室内对话/客厅/卧室/餐桌背景；如果主题明确是彩礼、首付、预算，才使用账本或预算桌面。",
        "family": "家庭饭桌/老家小店/暖光室内/童年烧烤店回忆背景",
    }
    if category == "career" and role in {"twist", "punchline"} and is_street_food_business_context(theme):
        return "校门口真实烤肠摊或夜市小吃摊背景"
    return defaults.get(category, "城市生活场景或室内对话背景")


def cat_requirement_for_slot(role: str) -> str:
    return {
        "hook": "强反应猫，震惊或停顿",
        "setup": "对话或操作电脑/手机的猫",
        "pressure": "委屈、破防、焦虑猫",
        "proof": "旁边同学/同事猫共鸣",
        "twist": "突然看穿规则或转头的猫",
        "punchline": "松一口气、摆烂或轻微庆祝猫",
    }.get(role, "贴合字幕情绪的猫动作")


def subtitle_requirement_for_slot(role: str) -> str:
    return "顶部短标题 + 关键词强调" if role == "hook" else "单层字幕或左右对话气泡，避免上下双字幕"


def viral_reference_notes(references: list[dict[str, Any]]) -> list[str]:
    if not references:
        return []
    return [
        "爆款结构参考：" + "；".join(
            f"{ref.get('title')}({ref.get('id')})" for ref in references[:3]
        )
    ]


def viral_template_seed(reference: dict[str, Any], theme: str) -> dict[str, Any]:
    beats = themed_template_beats(reference, theme)
    storyboard = reference.get("storyboard", [])
    role_map = {
        "opening": "hook",
        "start": "hook",
        "setup": "setup",
        "pressure": "pressure",
        "escalation": "escalation",
        "twist": "twist",
        "punchline": "punchline",
        "ending": "cta",
        "cta": "cta",
    }
    if not beats:
        beats = []
    for item in ([] if beats else storyboard):
        if not isinstance(item, dict):
            continue
        raw_beat = str(item.get("beat", "setup"))
        role = role_map.get(raw_beat, raw_beat.split("+", 1)[0] or "setup")
        script = adapt_caption_to_theme(str(item.get("script", "")), theme)
        if not script:
            continue
        beats.append((role, script, f"迁移自爆款《{reference.get('title', '')}》：{item.get('joke_point', '')}"))
    if not beats:
        beats = [
            ("hook", "现实突然给猫一拳", "爆款强 hook"),
            ("setup", adapt_caption_to_theme(theme, theme), "场景化铺垫"),
            ("twist", "猫发现规则有点绕", "荒诞反差"),
            ("punchline", "先把今天过明白", "合理收束"),
        ]
    return {
        "name": f"爆款迁移·{reference.get('title', '模板')}",
        "beats": beats[:8],
        "scene": reference.get("background_templates", []),
        "theme_keywords": list(tokenize_theme(theme))[:8],
        "emotion": reference.get("cat_action_templates", []),
        "social_topic": theme,
        "tension": "按新主题改写剧情内容，只迁移爆款结构方法。",
        "viral_reference_id": reference.get("id", ""),
        "viral_reference_title": reference.get("title", ""),
    }


def themed_template_beats(reference: dict[str, Any], theme: str) -> list[tuple[str, str, str]]:
    title = str(reference.get("title", "爆款模板"))
    topic = str(reference.get("topic", ""))
    category = infer_theme_category(theme)
    if category == "career":
        return [
            ("hook", "打开招聘软件那一秒", f"迁移自《{title}》的强冲突开场"),
            ("setup", "投了100份简历", "把求职动作具体化"),
            ("pressure", "岗位要求像满级账号", "现实压力具体化"),
            ("twist", "HR说还差三年经验", "荒诞反差转折"),
            ("echo", "旁边同学也沉默了", "从个人扩到群体"),
            ("punchline", "猫先翻译岗位黑话", "合理收束，不解决社会问题"),
        ]
    if category == "office":
        return [
            ("hook", "会议弹窗又亮了", f"迁移自《{title}》的职场冲突节奏"),
            ("setup", "9点同步10点复盘", "具体化上班内卷"),
            ("pressure", "午饭也在会里吃", "重复升级压力"),
            ("twist", "老板说简单聊两句", "反差转折"),
            ("echo", "同事头像全灰了", "群体共鸣"),
            ("punchline", "猫把在吗设免打扰", "边界感收束"),
        ]
    if category == "exam":
        return [
            ("hook", "书刚翻开就焦虑", f"迁移自《{title}》的考试/答题节奏"),
            ("setup", "考研考公都在招手", "双线选择压力"),
            ("pressure", "资料堆到挡住猫脸", "具象化焦虑"),
            ("twist", "猫发现不是题少", "转向结构问题"),
            ("echo", "自习室一排都沉默", "群体共鸣"),
            ("punchline", "猫今天先选一页", "轻收束"),
        ]
    if category == "street_food":
        return [
            ("hook", "校门口烤肠开张", f"迁移自《{title}》的市井摊位结构"),
            ("setup", "隔壁也挂买一送一", "具体化摆摊内卷"),
            ("pressure", "摊位费先把猫烤熟", "现实压力"),
            ("twist", "顾客只问能不能赊账", "反差转折"),
            ("echo", "三条街都在卷淀粉肠", "扩大共鸣"),
            ("punchline", "猫改卖情绪价值", "荒诞但不万能"),
        ]
    if category == "family":
        return [
            ("hook", "小时候我总偷最右边两串", f"迁移自《{title}》的童年回忆开场"),
            ("setup", "父亲转身去招呼客人", "把烧烤店和亲子关系落到具体动作"),
            ("pressure", "我以为自己瞒天过海", "放大小孩偷吃成功的错觉"),
            ("twist", "多年后才知道那是父亲专门留的", "揭示默许和无声照顾"),
            ("echo", "原来大人什么都知道", "从个人回忆扩到亲情共鸣"),
            ("punchline", "长大拼搏百天才懂那份无声的爱", "温暖收束"),
        ]
    if category == "rent":
        return [
            ("hook", "工资刚到账", f"迁移自《{title}》的生活压力开场"),
            ("setup", "房租先扣走一半", "把成本压力落到具体账单"),
            ("pressure", "押金中介通勤排队", "多重支出叠加"),
            ("twist", "猫发现家离公司更远", "反差转折"),
            ("echo", "室友也在算账", "群体共鸣"),
            ("punchline", "猫先把预算摊开谈", "现实但温和收束"),
        ]
    return []


def adapt_caption_to_theme(caption: str, theme: str) -> str:
    caption = caption.strip()
    if not caption:
        return ""
    if not is_family_memory_theme(theme) and is_street_food_business_context(theme):
        replacements = {
            "烤鸡腿": "烤肠",
            "传单": "烤肠券",
            "老板": "摊主",
            "生活费": "摊位费",
        }
    elif any(word in theme for word in ("租房", "房租", "押金", "合租", "通勤", "中介")):
        replacements = {
            "老板": "中介",
            "老师": "房东",
            "压岁钱": "押金",
            "传单": "租房合同",
            "烤鸡腿": "房租",
            "生活费": "房租",
        }
    elif any(word in theme for word in ("考研", "考公", "上岸", "考试")):
        replacements = {
            "老板": "考官",
            "老师": "监考老师",
            "压岁钱": "复习时间",
            "传单": "资料",
            "辣酱": "复习笔记",
        }
    elif any(word in theme for word in ("工作", "简历", "岗位", "面试", "就业")):
        replacements = {
            "老板": "HR",
            "老师": "面试官",
            "压岁钱": "岗位要求",
            "传单": "简历",
            "烤鸡腿": "实习机会",
            "辣酱": "offer",
            "生活费": "房租",
        }
    elif any(word in theme for word in ("上班", "加班", "会议", "内卷")):
        replacements = {
            "老师": "老板",
            "压岁钱": "KPI",
            "传单": "会议纪要",
            "辣酱": "下班时间",
            "烤鸡腿": "加班餐",
        }
    else:
        replacements = {}
    text = caption
    for source, target in replacements.items():
        text = text.replace(source, target)
    if text == caption and len(text) > 16:
        return theme[:16]
    return text[:18]


def short(text: Any, limit: int) -> str:
    value = str(text or "").replace("\n", " ")
    return value if len(value) <= limit else value[: limit - 1] + "…"
