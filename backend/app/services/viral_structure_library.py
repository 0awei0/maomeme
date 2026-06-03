from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..core.config import get_settings


LIBRARY_ROOT = Path("data") / "viral-structures" / "baokuan-maomeme"


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
    return {
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


def compact_storyboard(storyboard: list[Any]) -> list[dict[str, str]]:
    slots: list[dict[str, str]] = []
    for item in storyboard[:8]:
        if not isinstance(item, dict):
            continue
        slots.append(
            {
                "beat": str(item.get("beat", "")),
                "script": str(item.get("script", "")),
                "joke_point": str(item.get("joke_point", "")),
                "background": stringify_short(item.get("background")),
                "cats": stringify_short(item.get("cats")),
                "audio": stringify_short(item.get("audio")),
            }
        )
    return slots


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
    for score, entry in scored:
        if score <= 0:
            continue
        if excluded_for_category(category, entry):
            continue
        if entry not in selected:
            selected.append(entry)
        if len(selected) >= limit:
            break
    if len(selected) < limit:
        for _, entry in scored:
            if entry not in selected:
                selected.append(entry)
            if len(selected) >= limit:
                break
    return selected[:limit]


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
        "career": ("裸贷", "冲食堂", "班主任", "童年", "生日"),
        "office": ("裸贷", "冲食堂", "班主任", "童年", "生日"),
        "exam": ("裸贷", "烤鸡腿", "销售", "压岁钱"),
        "street_food": ("请假", "打120", "裸贷", "班主任"),
    }.get(category, ())
    return any(word in text for word in excluded)


def curated_references(category: str, entries: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    preferred_ids = {
        "career": ["bkmm-001-抖音202662-534438", "bkmm-003-抖音202663-009401", "bkmm-024-抖音202663-525083"],
        "office": ["bkmm-003-抖音202663-009401", "bkmm-001-抖音202662-534438", "bkmm-017-抖音202663-378934"],
        "exam": ["bkmm-012-抖音202663-122472", "bkmm-033-抖音202663-666778", "bkmm-011-抖音202663-117937"],
        "street_food": ["bkmm-034-抖音202663-693881", "bkmm-029-抖音202663-574311", "bkmm-025-抖音202663-534479"],
        "relationship": ["bkmm-027-抖音202663-550698", "bkmm-030-抖音202663-628799", "bkmm-022-抖音202663-502975"],
        "family": ["bkmm-041-抖音202663-963163", "bkmm-021-抖音202663-494793", "bkmm-042-抖音202663-978042"],
    }.get(category, [])
    by_id = {str(item.get("id", "")): item for item in entries}
    return [by_id[item_id] for item_id in preferred_ids if item_id in by_id][: max(0, min(limit, 2))]


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
        "career": ("职场", "打工", "销售", "办公室", "老板", "销冠", "上班", "工作"),
        "office": ("职场", "打工", "销售", "办公室", "老板", "销冠", "上班", "工作"),
        "exam": ("考试", "知识", "学生", "校园", "教室", "大学"),
        "campus": ("大学", "宿舍", "学生", "校园", "教室", "食堂"),
        "street_food": ("摊", "夜市", "市井", "小吃", "鸡腿", "餐馆", "街头"),
        "relationship": ("情侣", "结婚", "房", "情感", "家庭"),
        "family": ("家庭", "妈妈", "爸爸", "压岁钱", "亲子", "春节"),
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
        (("父母", "妈妈", "爸爸", "家庭", "亲戚", "压岁钱"), ("家庭", "妈妈", "爸爸", "压岁钱", "亲子", "春节")),
    ]
    for triggers, targets in buckets:
        if any(word in theme for word in triggers) and any(word in text for word in targets):
            score += 10.0
    if any(word in theme for word in ("工作", "简历", "岗位", "面试", "就业", "招聘", "offer")):
        if any(word in text for word in ("职场", "打工", "销售", "办公室", "老板", "销冠")):
            score += 16.0
        if any(word in text for word in ("宿舍", "童年", "暑假", "考试")):
            score -= 6.0
    score += min(float(entry.get("score") or 0) / 20.0, 5.0)
    return score


def infer_theme_category(theme: str) -> str:
    if any(word in theme for word in ("烤肠", "香肠", "摆摊", "夜市", "小吃", "地摊", "餐车")):
        return "street_food"
    if any(word in theme for word in ("工作", "简历", "岗位", "面试", "就业", "招聘", "offer")):
        return "career"
    if any(word in theme for word in ("上班", "老板", "加班", "会议", "KPI", "内卷")):
        return "office"
    if any(word in theme for word in ("考研", "考公", "上岸", "考试")):
        return "exam"
    if any(word in theme for word in ("结婚", "彩礼", "买房", "恋爱", "情侣")):
        return "relationship"
    if any(word in theme for word in ("父母", "妈妈", "爸爸", "家庭", "压岁钱", "亲戚")):
        return "family"
    if any(word in theme for word in ("大学", "宿舍", "食堂", "同学", "校园")):
        return "campus"
    return ""


def tokenize_theme(text: str) -> set[str]:
    terms = {item for item in text.replace("/", " ").replace("、", " ").replace("，", " ").split() if len(item) >= 2}
    common = [
        "工作", "简历", "岗位", "面试", "就业", "上班", "老板", "会议", "加班", "内卷",
        "大学", "宿舍", "食堂", "同学", "校园", "考研", "考公", "考试", "焦虑",
        "烤肠", "香肠", "摆摊", "夜市", "小吃", "地摊", "餐车",
        "结婚", "彩礼", "买房", "租房", "房贷", "恋爱", "情侣",
        "父母", "妈妈", "爸爸", "家庭", "压岁钱", "亲戚",
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
                f"   猫动作模板：{'；'.join(ref.get('cat_action_templates', [])[:4])}",
                f"   声音/字幕：{short(ref.get('audio_style', ''), 60)}｜{short(ref.get('subtitle_style', ''), 60)}",
            ]
        )
    return "\n".join(lines)


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
        "social_topic": reference.get("topic", ""),
        "tension": reference.get("script_templates", [""])[0] if reference.get("script_templates") else "",
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
    return []


def adapt_caption_to_theme(caption: str, theme: str) -> str:
    caption = caption.strip()
    if not caption:
        return ""
    if any(word in theme for word in ("工作", "简历", "岗位", "面试", "就业")):
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
    elif any(word in theme for word in ("考研", "考公", "上岸", "考试")):
        replacements = {
            "老板": "考官",
            "老师": "监考老师",
            "压岁钱": "复习时间",
            "传单": "资料",
            "辣酱": "复习笔记",
        }
    elif any(word in theme for word in ("烤肠", "香肠", "摆摊", "小吃", "夜市")):
        replacements = {
            "烤鸡腿": "烤肠",
            "传单": "烤肠券",
            "老板": "摊主",
            "生活费": "摊位费",
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
