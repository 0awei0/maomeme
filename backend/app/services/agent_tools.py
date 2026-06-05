from __future__ import annotations

import hashlib
import json
import random
import re
from typing import Any

from .asset_index import load_assets, rank_assets, ref
from .seedream_service import constrain_background_prompt, generate_background, seedream_available
from .viral_structure_library import (
    infer_theme_category,
    is_emotional_relationship_context,
    is_financial_relationship_context,
)

MIN_CLIP_DURATION = 2.0
MAX_CLIP_DURATION = 5.0


def asset_search_tool(
    index: dict[str, Any],
    asset_type: str,
    keywords: list[str],
    limit: int = 5,
) -> list[dict[str, Any]]:
    collection_key = {
        "motion": "cat_motions",
        "background": "backgrounds",
        "sticker": "stickers",
    }.get(asset_type, "backgrounds")
    collection = index.get(collection_key, [])
    ranked = rank_assets(collection, keywords, limit=limit)
    if ranked:
        return ranked
    if asset_type == "motion":
        return collection[: max(limit, min(len(collection), 24))]
    return collection[:limit]


def cat_casting_tool(
    index: dict[str, Any],
    theme: str,
    beat: dict[str, Any],
    count: int = 1,
    avoid_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    avoid = {str(item) for item in avoid_ids or [] if str(item)}
    keywords = list(dict.fromkeys([
        *[str(item) for item in beat.get("emotion_keywords", [])],
        *[str(item) for item in beat.get("must_keywords", [])],
        str(beat.get("caption", "")),
        str(beat.get("intent", "")),
    ]))
    candidates = asset_search_tool(index, "motion", keywords, limit=18)
    if not candidates:
        candidates = index.get("cat_motions", [])[:18]

    category = infer_theme_category(f"{theme} {beat.get('caption', '')} {beat.get('intent', '')}")
    scored: list[tuple[float, dict[str, Any]]] = []
    profile = lightweight_motion_profile(theme, str(beat.get("caption", "")), str(beat.get("role", "")), str(beat.get("intent", "")))
    prefer = [str(item) for item in profile.get("prefer", []) if str(item).strip()]
    avoid_profile = [str(item) for item in profile.get("avoid", []) if str(item).strip()]
    for asset in candidates:
        desc = lightweight_motion_text(asset)
        score = lightweight_asset_score(asset, keywords)
        score += sum(2.4 for keyword in prefer if keyword in desc)
        score -= sum(4.8 for keyword in avoid_profile if keyword in desc)
        score += lightweight_role_motion_bonus(str(beat.get("role", "")), desc)
        score += lightweight_category_motion_bonus(category, desc)
        if str(asset.get("id", "")) in avoid:
            score -= 4.0
        if any(word in desc for word in ("非猫素材", "小狗", "山羊", "过激", "默认避用")):
            score -= 12.0
        if any(word in desc for word in ("黑边", "低清", "模糊", "白底")):
            score -= 1.4
        scored.append((score, asset))
    scored.sort(key=lambda item: (-item[0], str(item[1].get("id", ""))))
    return [
        {
            **ref(asset),
            "duration": float(asset.get("duration") or 0),
            "match_score": round(score, 3),
            "reason": casting_reason(theme, beat, asset),
        }
        for score, asset in scored[: max(1, min(3, count))]
    ]


def background_tool(
    index: dict[str, Any],
    theme: str,
    beat: dict[str, Any],
    avoid_ids: list[str] | None = None,
) -> dict[str, Any]:
    avoid = {str(item) for item in avoid_ids or [] if str(item)}
    local_category = local_scene_category(theme, beat)
    keywords = list(dict.fromkeys([
        *[str(item) for item in beat.get("scene_keywords", [])],
        str(beat.get("caption", "")),
        str(beat.get("intent", "")),
    ]))
    story_text = f"{theme} {beat.get('caption', '')} {beat.get('intent', '')}"
    keywords = [keyword for keyword in keywords if keyword and not scene_keyword_conflicts(keyword, local_category, story_text)]
    candidates = asset_search_tool(index, "background", keywords, limit=16)
    if not candidates:
        candidates = index.get("backgrounds", [])[:16]
    scored: list[tuple[float, dict[str, Any]]] = []
    for asset in candidates:
        score = lightweight_asset_score(asset, keywords)
        score += lightweight_category_background_bonus(local_category, asset)
        score += local_background_guard_score(local_category, asset, beat)
        if str(asset.get("id", "")) in avoid:
            score -= 1.2
        scored.append((score, asset))
    scored.sort(key=lambda item: (-item[0], str(item[1].get("id", ""))))
    base_score, base = scored[0] if scored else (0.0, {})
    filled, source, prompt, note = background_fill_tool(theme, beat, base, base_score)
    return {
        "asset": ref(filled),
        "background_source": source,
        "background_prompt": prompt,
        "background_need": str(beat.get("background_need") or ""),
        "match_score": round(max(base_score, 1.0 if source == "generated" else base_score), 3),
        "reason": note or background_reason(theme, beat, filled),
    }


def clip_planner_by_id_tool(
    index: dict[str, Any],
    asset_type: str,
    asset_id: str,
    beat: dict[str, Any],
    slot_duration: float,
) -> dict[str, Any]:
    asset = find_asset(index, asset_type, asset_id)
    if not asset:
        collection = index.get("cat_motions" if asset_type == "motion" else "backgrounds", [])
        asset = collection[0] if collection else {}
    return clip_planner_tool(asset, beat, slot_duration)


def overlay_design_tool(
    theme: str,
    beat: dict[str, Any],
    motion: dict[str, Any] | None = None,
    background: dict[str, Any] | None = None,
    requested_actions: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    actions = [normalize_agent_overlay_action(action, theme, beat, motion or {}) for action in requested_actions or []]
    actions = [action for action in actions if action]
    if not actions:
        actions = overlay_planner_tool(beat, motion or {}, background or {}, theme)
    if not any(action.get("type") == "sticker" for action in actions):
        sticker = sticker_for_context(theme, beat, primary_type=primary_overlay_type(actions), motion=motion or {})
        if sticker:
            actions.append(sticker)
    return select_overlay_actions(actions)


def hyperframe_packaging_tool(
    theme: str,
    beat: dict[str, Any],
    overlay_actions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    text = f"{theme} {beat.get('caption', '')} {beat.get('intent', '')}"
    category = overlay_category(theme, str(beat.get("caption", "")), text)
    preset = {
        "career": "job-hunt-black-hole",
        "office": "meeting-involution",
        "exam": "exam-choice-anxiety",
        "rent": "rent-bill-pressure",
        "street_food": "street-food-stall-involution",
    }.get(category, "default-cat-meme")
    role = str(beat.get("role", ""))
    return {
        "packaging_preset": preset,
        "hyperframe_role": category or "cat_meme",
        "caption_style": "dialogue_bubbles" if beat.get("layout") == "dialogue" else "top_title",
        "subtitle_policy": "single_layer",
        "overlay_actions": overlay_actions or [],
        "transition_hint": {
            "type": "flash" if role in {"twist", "punchline"} else "zoom" if role in {"pressure", "escalation"} else "cut",
            "duration": 0.18 if role in {"twist", "punchline"} else 0.22 if role in {"pressure", "escalation"} else 0.0,
        },
    }


def shot_critic_tool(
    theme: str,
    beat: dict[str, Any],
    slot: dict[str, Any],
    used_motion_ids: list[str] | None = None,
    used_background_ids: list[str] | None = None,
) -> dict[str, Any]:
    used_motion = {str(item) for item in used_motion_ids or [] if str(item)}
    used_background = {str(item) for item in used_background_ids or [] if str(item)}
    issues: list[str] = []
    hints: list[str] = []
    score = 1.0

    motion_text = lightweight_motion_text(slot.get("motion", {}))
    background_text = f"{slot.get('background', {}).get('id', '')} {slot.get('background', {}).get('description', '')}"
    scene_keywords = [str(item) for item in beat.get("scene_keywords", []) if str(item)]
    emotion_keywords = [str(item) for item in beat.get("emotion_keywords", []) if str(item)]

    if emotion_keywords and not any(keyword in motion_text for keyword in emotion_keywords[:8]):
        score -= 0.18
        issues.append("猫动作没有明显命中分镜情绪")
        hints.append("重新选择更贴近表情/动作关键词的猫素材")
    if scene_keywords and not any(keyword in background_text for keyword in scene_keywords[:10]):
        score -= 0.18
        issues.append("背景和具体场景不够贴合")
        hints.append("优先匹配具体背景，必要时生成背景 prompt")
    motion_id = str(slot.get("motion", {}).get("id", ""))
    background_id = str(slot.get("background", {}).get("id", ""))
    if motion_id and motion_id in used_motion and len(used_motion) < 10:
        score -= 0.12
        issues.append("猫素材与前文重复")
        hints.append("换一个同情绪但动作不同的猫素材")
    profile = lightweight_motion_profile(theme, str(beat.get("caption", "")), str(beat.get("role", "")), str(beat.get("intent", "")))
    profile_avoid = [str(item) for item in profile.get("avoid", []) if str(item).strip()]
    profile_prefer = [str(item) for item in profile.get("prefer", []) if str(item).strip()]
    if profile_avoid and any(keyword in motion_text for keyword in profile_avoid):
        score -= 0.2
        issues.append("猫动作和剧情语义不贴合")
        hints.append("避开和本镜头语义冲突的猫动作，优先选择主题动作 profile 命中的素材")
    elif profile_prefer and not any(keyword in motion_text for keyword in profile_prefer):
        score -= 0.16
        issues.append("猫动作缺少剧情语义动作")
        hints.append("优先选择命中主题动作 profile 的猫素材")
    if background_id and background_id in used_background and len(used_background) <= 2:
        score -= 0.08
        issues.append("背景重复偏多")
        hints.append("同场景可保留，场景变化时换背景")
    if beat.get("layout") == "dialogue" and not slot.get("dialogue"):
        score -= 0.12
        issues.append("对话镜头缺少左右气泡台词")
        hints.append("补两句短对话，一句推动冲突，一句形成反差")
    if "简历 x100" in json_dump_safe(slot) and "简历" not in f"{theme} {beat.get('caption', '')}":
        score -= 0.18
        issues.append("贴图文案和主题不匹配")
        hints.append("把飞物件换成该主题的具体物件")
    story_text = f"{theme} {beat.get('caption', '')} {beat.get('intent', '')}"
    if (
        infer_theme_category(story_text) == "relationship"
        and is_emotional_relationship_context(story_text)
        and not is_financial_relationship_context(story_text)
        and any(word in json_dump_safe(slot) for word in ("bill_card", "bill_stack", "账单", "房租", "押金", "预算", "首付", "房贷", "彩礼"))
    ):
        score -= 0.24
        issues.append("关系分镜被财务账单素材干扰")
        hints.append("情绪沟通主题优先用对话、态度、安慰等包装，不要飞账单或账本卡片")

    return {
        "score": round(max(0.0, min(1.0, score)), 3),
        "passed": score >= 0.72,
        "issues": issues,
        "revision_hints": hints,
    }


def clip_planner_tool(asset: dict[str, Any], beat: dict[str, Any], slot_duration: float) -> dict[str, Any]:
    asset_duration = float(asset.get("duration") or 0)
    role = str(beat.get("role", ""))
    target = target_clip_duration(role, slot_duration)
    if asset_duration and asset_duration < target:
        return {"start": 0.0, "duration": round(max(MIN_CLIP_DURATION, target), 2), "loop": True}

    max_start = max(0.0, asset_duration - target) if asset_duration else 0.0
    seed = stable_seed(asset.get("id", ""), beat.get("id", ""), beat.get("caption", ""))
    start = seeded_start(seed, max_start, role)
    return {"start": round(start, 2), "duration": round(target, 2), "loop": False}


def overlay_planner_tool(beat: dict[str, Any], motion: dict[str, Any], background: dict[str, Any], theme: str = "") -> list[dict[str, Any]]:
    role = str(beat.get("role", ""))
    caption = str(beat.get("caption", ""))
    intent = str(beat.get("intent", ""))
    dialogue = " ".join(str(item.get("text", "")) for item in beat.get("dialogue", []) if isinstance(item, dict))
    scene_text = " ".join(str(item) for item in beat.get("scene_keywords", []))
    local_text = f"{caption} {intent} {dialogue}"
    local_context = f"{caption} {intent} {dialogue} {scene_text}"
    story_context = f"{theme} {caption} {intent} {dialogue}"
    core_text = f"{theme} {local_context}"
    asset_text = f"{core_text} {motion.get('description', '')} {background.get('description', '')}"
    category = overlay_category(theme, local_text, story_context)
    if not overlay_needed_for_beat(role, local_text, core_text, category):
        return []

    primary = primary_overlay_for_context(category, role, caption, local_text, story_context)
    actions: list[dict[str, Any]] = [primary] if primary else []

    for action in [
        throw_object_for_context(story_context, role, category),
        sticker_for_context(theme, beat, category, primary_type=primary.get("type") if primary else "", motion=motion),
        stamp_for_context(story_context, role, category),
        popup_for_context(asset_text, role, category),
        burst_for_context(story_context, role, category),
    ]:
        if action:
            actions.append(action)

    return select_overlay_actions(actions)


def overlay_needed_for_beat(role: str, local_text: str, text: str, category: str) -> bool:
    if role in {"hook", "pressure", "twist", "escalation", "punchline"}:
        return True
    strong_words = (
        "离谱", "突然", "已读", "不回", "拒", "老板", "120", "请假", "急救",
        "要求", "岗位", "薪资", "烤肠", "摆摊", "周一", "闹钟", "会议", "加班",
    )
    if any(word in local_text for word in strong_words):
        return True
    return category in {"street_food", "career", "office"} and role in {"setup", "proof"}


def overlay_category(theme: str, local_text: str, text: str) -> str:
    theme_category = infer_theme_category(theme)
    relationship_story = f"{theme} {local_text}"
    if (
        theme_category == "relationship"
        and is_emotional_relationship_context(relationship_story)
        and not is_financial_relationship_context(relationship_story)
    ):
        return "relationship"
    if is_street_food_context(local_text):
        return "street_food"
    if is_exam_context(local_text):
        return "exam"
    if is_workplace_context(local_text):
        return "office"
    if is_rent_context(local_text):
        return "rent"
    if is_career_context(local_text):
        return "career"
    if theme_category in {"street_food", "office", "exam", "rent", "career"}:
        return theme_category
    if is_street_food_context(text):
        return "street_food"
    if is_exam_context(text):
        return "exam"
    if is_workplace_context(text):
        return "office"
    if is_rent_context(text):
        return "rent"
    if is_career_context(text):
        return "career"
    return theme_category


def local_scene_category(theme: str, beat: dict[str, Any]) -> str:
    local_text = f"{beat.get('caption', '')} {beat.get('intent', '')}"
    if is_street_food_context(local_text):
        return "street_food"
    category = infer_theme_category(local_text)
    return category or infer_theme_category(theme)


def scene_keyword_conflicts(keyword: str, category: str, local_text: str = "") -> bool:
    text = str(keyword)
    if is_street_food_context(text) and category != "street_food":
        return True
    if (
        category == "relationship"
        and any(word in text for word in ("出租屋", "房租", "押金", "租房", "账单", "预算", "首付", "房贷", "彩礼"))
        and not is_financial_relationship_context(local_text)
    ):
        return True
    conflicts = {
        "career": ("出租屋", "房租", "押金", "考研", "考公", "自习", "图书馆", "烤肠", "小吃摊", "夜市"),
        "office": ("出租屋", "房租", "押金", "考研", "考公", "自习", "图书馆", "烤肠", "小吃摊", "夜市"),
        "exam": ("招聘", "面试", "HR", "会议室", "工位", "房租", "押金", "烤肠", "小吃摊", "夜市"),
        "rent": ("招聘", "面试", "HR", "会议室", "考研", "考公", "自习", "图书馆", "烤肠", "小吃摊", "夜市"),
        "street_food": ("招聘", "面试", "HR", "会议室", "自习", "图书馆", "出租屋"),
    }.get(category, ())
    return any(word in text for word in conflicts)


def local_background_guard_score(category: str, asset: dict[str, Any], beat: dict[str, Any]) -> float:
    text = f"{asset.get('id', '')} {asset.get('scene', '')} {asset.get('file', '')} {asset.get('description', '')}"
    local_text = f"{beat.get('caption', '')} {beat.get('intent', '')}"
    if is_street_food_context(text) and not is_street_food_context(local_text) and category != "street_food":
        return -120.0
    if (
        category == "relationship"
        and is_emotional_relationship_context(f"{beat.get('theme', '')} {local_text}")
        and not is_financial_relationship_context(f"{beat.get('theme', '')} {local_text}")
        and any(word in text for word in ("rental-bill", "rental_bill", "出租屋", "房租", "押金", "租房", "账单", "预算", "首付", "房贷", "彩礼", "招聘", "面试", "公司楼下", "找工作", "通勤"))
    ):
        return -120.0
    negative = {
        "career": ("烤肠", "小吃摊", "夜市", "出租屋", "自习室", "图书馆"),
        "office": ("烤肠", "小吃摊", "夜市", "出租屋", "自习室", "图书馆"),
        "exam": ("招聘", "面试", "会议室", "办公室", "烤肠", "小吃摊", "出租屋"),
        "rent": ("招聘", "面试", "会议室", "办公室", "考研", "考公", "烤肠", "小吃摊"),
        "street_food": ("招聘", "面试", "会议室", "办公室", "自习室", "图书馆", "出租屋"),
    }.get(category, ())
    return -80.0 if any(word in text for word in negative) else 0.0


def primary_overlay_for_context(category: str, role: str, caption: str, local_text: str, text: str) -> dict[str, Any] | None:
    duration = 2.2 if role != "hook" else 1.85
    if any(word in text for word in ("120", "急救", "救护车")):
        return {
            "type": "emergency_call",
            "start": 0.25,
            "duration": min(2.4, duration + 0.2),
            "title": "急救电话",
            "caller": "00后猫",
            "status": "老板已沉默",
        }
    if any(word in text for word in ("请假", "不批准", "不批假", "病假", "审批")):
        return {
            "type": "leave_request",
            "start": 0.28,
            "duration": duration,
            "title": "请假审批",
            "status": "老板：不批准",
            "reason": "身体报警",
        }
    if category == "street_food":
        return {
            "type": "stall_sign",
            "start": 0.28,
            "duration": duration,
            "title": stall_title_for_text(text),
            "items": stall_items_for_text(text, role),
        }
    if category == "rent":
        if any(word in text for word in ("通勤", "地铁", "公交", "站台")) and role in {"pressure", "twist", "echo"}:
            return {
                "type": "commute_card",
                "start": 0.3,
                "duration": duration,
                "title": "通勤账单",
                "items": ["早八地铁", "单程 2h", "咖啡续命"],
            }
        return {
            "type": "bill_card",
            "start": 0.32,
            "duration": duration,
            "title": "现实账单",
            "items": bill_items_for_text(text),
        }
    if category == "exam":
        if role in {"pressure", "proof", "escalation"} or any(word in text for word in ("资料", "刷题", "倒计时", "复习")):
            return {
                "type": "study_card",
                "start": 0.3,
                "duration": duration,
                "title": "今日复习",
                "items": study_items_for_text(text),
            }
        return {
            "type": "choice_panel",
            "start": 0.3,
            "duration": duration,
            "title": "请选择今天焦虑",
            "options": exam_options_for_text(text),
        }
    if category == "office":
        return {
            "type": "work_chat_stack",
            "start": 0.3,
            "duration": duration,
            "title": "工作群",
            "messages": office_messages_for_text(text),
        }
    if category == "relationship":
        relationship_text = f"{caption} {local_text} {text}"
        if is_financial_relationship_context(relationship_text):
            return {
                "type": "bill_card",
                "start": 0.32,
                "duration": duration,
                "title": "现实账本",
                "items": family_bill_items_for_text(relationship_text),
            }
        return {
            "type": "chat_stack",
            "start": 0.32,
            "duration": duration,
            "title": "沟通频道",
            "messages": relationship_messages_for_text(relationship_text),
        }
    if category == "family":
        return {
            "type": "bill_card",
            "start": 0.32,
            "duration": duration,
            "title": "现实账本",
            "items": family_bill_items_for_text(text),
        }
    if category == "career":
        caption_has_requirement = any(word in caption for word in ("要求", "经验", "全链路", "全栈", "团队", "门槛", "黑话", "规则", "翻译", "满级", "应届"))
        if any(word in caption for word in ("投", "简历", "已读", "不回", "拒", "HR", "面试", "黑洞", "沟通")):
            return {
                "type": "chat_stack",
                "start": 0.32,
                "duration": duration,
                "title": "招聘消息",
                "messages": job_messages_for_text(local_text),
            }
        if caption_has_requirement or any(word in local_text for word in ("全链路", "全栈", "团队", "门槛", "黑话", "规则", "翻译", "满级")):
            return {
                "type": "job_requirement_card",
                "start": 0.35,
                "duration": duration,
                "title": "岗位要求",
                "items": requirement_items_for_text(text),
            }
        if any(word in local_text for word in ("刷", "招聘软件", "招聘APP", "APP", "薪资", "工资", "心仪岗位", "看到", "岗位列表")) or role == "hook":
            return {
                "type": "phone_job_feed",
                "start": 0.25,
                "duration": duration,
                "title": caption or "刷到薪资还行的岗位",
                "salary": salary_for_text(text),
                "company": company_for_text(text),
                "tags": job_tags_for_text(text),
            }
    return None


def select_overlay_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for action in actions:
        if not action or not action.get("type"):
            continue
        key = "|".join(str(action.get(item, "")) for item in ("type", "object", "text", "title"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(action)
    primary_types = {"phone_job_feed", "job_requirement_card", "work_chat_stack", "chat_stack", "choice_panel", "study_card", "bill_card", "commute_card", "stall_sign", "leave_request", "emergency_call"}
    primary = next((action for action in unique if action.get("type") in primary_types), None)
    if primary:
        secondary = next((action for action in unique if action is not primary and action.get("type") == "sticker"), None)
        if not secondary:
            secondary = next((action for action in unique if action is not primary and action.get("type") == "throw_object"), None)
        if not secondary:
            secondary = next((action for action in unique if action is not primary and action.get("type") in {"impact_burst", "stamp_reject", "popup"}), None)
        return [item for item in (primary, secondary) if item]
    sticker = next((action for action in unique if action.get("type") == "sticker"), None)
    if sticker:
        other = next((action for action in unique if action is not sticker), None)
        return [item for item in (sticker, other) if item]
    return unique[:2]


def throw_object_for_context(text: str, role: str, category: str = "") -> dict[str, Any] | None:
    if role not in {"setup", "pressure", "proof", "twist", "escalation", "echo"}:
        return None
    if any(word in text for word in ("请假", "病假", "不批准", "不批假", "审批", "体温", "发烧", "120", "急救")):
        return throw_object_payload("leave_form", "病假单")
    category_defaults = {
        "street_food": ("price_tag" if any(word in text for word in ("降价", "买一送一", "特价", "卷")) else "sausage_skewer", "今日特价" if any(word in text for word in ("降价", "买一送一", "特价", "卷")) else "烤肠 x3"),
        "rent": ("bill_stack", "账单 -2400"),
        "exam": ("study_notes", "资料 x3"),
        "office": office_throw_object(text, role),
        "career": career_throw_object(text, role),
    }
    if category in category_defaults:
        obj, label = category_defaults[category]
        return {
            "type": "throw_object",
            "object": obj,
            "from": "left_cat",
            "to": "right_cat",
            "start": 0.68,
            "duration": 1.05,
            "text": label,
        }
    if any(word in text for word in ("投了", "投递", "简历", "改简历")):
        return throw_object_payload("resume_stack", "简历 x3")
    if any(word in text for word in ("要求", "经验", "门槛", "黑话", "规则", "全栈", "团队")):
        return throw_object_payload("requirement_scroll", "要求+1")
    if any(word in text for word in ("已读", "拒", "不回", "HR")):
        return throw_object_payload("reject_notice", "暂不合适")
    catalog: list[tuple[tuple[str, ...], str, str]] = [
        (("房租", "租房", "押金", "中介", "出租屋"), "bill_stack", "账单 -2400"),
        (("通勤", "地铁", "公交", "站台"), "metro_card", "通勤 2h"),
        (("考研", "考公", "上岸", "自习", "刷题", "申论", "资料"), "study_notes", "资料 x3"),
        (("考试", "准考证", "成绩"), "exam_ticket", "准考证"),
        (("会议", "周会", "复盘", "同步", "老板", "在线待命"), "meeting_invite", "会议+1"),
        (("PPT", "方案", "汇报"), "ppt_deck", "PPT x99"),
        (("烤肠", "香肠", "摊位", "小吃摊", "夜市", "摆摊"), "sausage_skewer", "烤肠 x3"),
        (("赊账", "降价", "买一送一", "特价"), "price_tag", "今日特价"),
        (("简历", "投递", "招聘", "岗位", "面试"), "resume_stack", "简历 x100"),
    ]
    for triggers, obj, label in catalog:
        if any(trigger in text for trigger in triggers):
            return throw_object_payload(obj, label)
    return None


def career_throw_object(text: str, role: str) -> tuple[str, str]:
    if role == "setup" and any(word in text for word in ("投了", "投递", "简历", "改简历")):
        return ("resume_stack", "简历 x3")
    if any(word in text for word in ("已读", "拒", "不回", "黑洞", "HR")):
        return ("reject_notice", "暂不合适")
    if any(word in text for word in ("要求", "经验", "门槛", "规则", "全栈")):
        return ("requirement_scroll", "要求+1")
    return ("resume_stack", "简历 x3")


def office_throw_object(text: str, role: str) -> tuple[str, str]:
    if any(word in text for word in ("PPT", "汇报", "方案")):
        return ("ppt_deck", "PPT x9")
    if any(word in text for word in ("在吗", "待命", "在线", "下班")) or role in {"twist", "echo"}:
        return ("meeting_invite", "老板：在吗")
    if any(word in text for word in ("复盘", "同步")) or role in {"pressure", "escalation"}:
        return ("meeting_invite", "再同步")
    if any(word in text for word in ("周会", "早会")):
        return ("meeting_invite", "9点周会")
    return ("meeting_invite", "会议+1")


def throw_object_payload(obj: str, label: str) -> dict[str, Any]:
    return {
        "type": "throw_object",
        "object": obj,
        "from": "left_cat",
        "to": "right_cat",
        "start": 0.68,
        "duration": 1.05,
        "text": label,
    }


def is_street_food_context(text: str) -> bool:
    return any(word in text for word in ("烤肠", "香肠", "摆摊", "小吃摊", "夜市", "摊位", "摊车", "街边摊", "餐车", "冰粉", "street_food", "food_stall", "stall"))


def is_rent_context(text: str) -> bool:
    return any(word in text for word in ("租房", "房租", "押金", "合租", "中介", "通勤", "出租屋", "水电", "网费"))


def is_exam_context(text: str) -> bool:
    return any(word in text for word in ("考研", "考公", "上岸", "考试", "自习", "刷题", "申论", "图书馆"))


def is_workplace_context(text: str) -> bool:
    return any(word in text for word in ("会议", "加班", "复盘", "同步", "老板", "KPI", "PPT", "在线待命"))


def is_career_context(text: str) -> bool:
    if is_street_food_context(text) or is_rent_context(text) or is_exam_context(text) or is_workplace_context(text):
        return False
    return any(word in text for word in ("工作", "就业", "求职", "招聘", "简历", "岗位", "面试", "HR", "offer", "校招", "薪资", "工资", "要求", "经验", "应届", "团队", "全链路", "全栈"))


def is_job_context(text: str) -> bool:
    return not (is_street_food_context(text) or is_rent_context(text) or is_exam_context(text) or is_workplace_context(text))


def requirement_items_for_text(text: str) -> list[str]:
    items: list[str] = []
    if any(word in text for word in ("黑话", "规则", "翻译")):
        items.extend(["经验不限=最好满级", "抗压=随时在线", "年轻团队=都很能卷"])
    if any(word in text for word in ("3年", "三年", "5年", "五年", "经验")):
        items.append("3年以上经验")
    if any(word in text for word in ("全链路", "全栈", "运营")):
        items.append("会全链路运营")
    if any(word in text for word in ("团队", "管理")):
        items.append("带过团队")
    if any(word in text for word in ("应届", "校招", "毕业")):
        items.append("欢迎应届生")
    return items[:4] or ["经验不限但要满级", "能抗压", "会很多"]


def bill_items_for_text(text: str) -> list[str]:
    items: list[str] = []
    if any(word in text for word in ("房租", "租房", "出租屋")):
        items.append("房租")
    if any(word in text for word in ("押金", "中介")):
        items.append("押金")
    if any(word in text for word in ("通勤", "地铁", "公交")):
        items.append("通勤")
    if any(word in text for word in ("水电", "网费")):
        items.append("水电网")
    return items[:3] or ["房租", "通勤", "押金"]


def family_bill_items_for_text(text: str) -> list[str]:
    items: list[str] = []
    if any(word in text for word in ("彩礼", "婚礼")):
        items.append("婚礼")
    if any(word in text for word in ("买房", "房贷", "首付")):
        items.append("首付")
    if any(word in text for word in ("父母", "家庭", "亲戚")):
        items.append("家庭")
    return items[:3] or ["首付", "账单", "沟通"]


def stall_title_for_text(text: str) -> str:
    if any(word in text for word in ("夜市", "地摊")):
        return "夜市小摊"
    if any(word in text for word in ("校门", "大学", "同学")):
        return "校门口小摊"
    return "街边小摊"


def stall_items_for_text(text: str, role: str) -> list[str]:
    if any(word in text for word in ("买一送一", "降价", "特价", "竞争")):
        return ["隔壁买一送一", "我也降一块", "摊主也卷"]
    if any(word in text for word in ("摊位费", "成本", "煤气", "房租")):
        return ["摊位费先扣", "煤气也要钱", "利润先沉默"]
    if role in {"punchline", "cta"}:
        return ["烤肠不包上岸", "但能先暖手", "明天再摆"]
    return ["烤肠 3元", "加料 +1", "今日也内卷"]


def study_items_for_text(text: str) -> list[str]:
    items: list[str] = []
    if "考研" in text:
        items.append("考研英语")
    if "考公" in text or "申论" in text:
        items.append("申论资料")
    if any(word in text for word in ("就业", "简历", "投")):
        items.append("简历待改")
    if any(word in text for word in ("家族群", "父母")):
        items.append("家族群攻略")
    return items[:3] or ["刷题 x3", "倒计时", "选择题"]


def exam_options_for_text(text: str) -> list[str]:
    options = []
    if "考研" in text:
        options.append("考研")
    if "考公" in text:
        options.append("考公")
    if any(word in text for word in ("就业", "工作", "简历")):
        options.append("就业")
    if not options:
        options = ["考研", "考公", "就业"]
    while len(options) < 3:
        for item in ("二战", "实习", "先睡觉"):
            if item not in options:
                options.append(item)
            if len(options) >= 3:
                break
    return options[:3]


def office_messages_for_text(text: str) -> list[str]:
    if any(word in text for word in ("周会", "复盘", "同步")):
        return ["9点周会", "10点复盘", "再同步一次"]
    if any(word in text for word in ("下班", "在线", "待命", "在吗")):
        return ["老板：在吗", "简单看一下", "今晚辛苦下"]
    if "PPT" in text:
        return ["PPT再改版", "颜色再活泼", "五分钟后要"]
    return ["老板：在吗", "再同步一次", "今晚辛苦下"]


def job_messages_for_text(text: str) -> list[str]:
    if any(word in text for word in ("已读", "不回", "黑洞")):
        return ["HR：已读", "系统：暂无回复", "猫：我还在吗"]
    if any(word in text for word in ("面试", "沟通")):
        return ["先发作品集", "再做测试题", "下周等通知"]
    if any(word in text for word in ("拒", "暂不合适")):
        return ["很遗憾", "暂不合适", "保持联系"]
    return ["已投递", "对方已读", "要求又加一条"]


def relationship_messages_for_text(text: str) -> list[str]:
    if any(word in text for word in ("态度", "情绪价值", "安慰", "委屈")):
        return ["她：我要的是态度", "他：我在想办法", "频道：正在错位"]
    if any(word in text for word in ("沟通", "频道", "脑回路")):
        return ["她：你先听我说", "他：那我问清楚", "频道：连接失败"]
    if any(word in text for word in ("冷战", "不回", "已读")):
        return ["消息：已读", "情绪：未处理", "猫：先别急"]
    return ["她：先别急着解题", "他：那我怎么做", "频道：差一点"]


def salary_for_text(text: str) -> str:
    match = re.search(r"(\d{1,2}\s*[kK万wW][-~到至]?\s*\d{0,2}\s*[kK万wW]?|\d{3,5}\s*元?)", text)
    if match:
        return re.sub(r"\s+", "", match.group(1))
    if any(word in text for word in ("四千", "4000", "4k", "4K")):
        return "4K"
    if any(word in text for word in ("薪资还行", "工资还行", "心仪")):
        return "薪资还行"
    return "面议但很卷"


def company_for_text(text: str) -> str:
    if any(word in text for word in ("校招", "应届", "毕业")):
        return "校招热岗"
    if "实习" in text:
        return "实习转正"
    return "普通公司"


def job_tags_for_text(text: str) -> list[str]:
    if any(word in text for word in ("应届", "校招", "毕业")):
        return ["应届可投", "经验优先", "立即沟通"]
    if any(word in text for word in ("全栈", "运营", "团队", "管理")):
        return ["全链路", "带团队", "抗压"]
    if any(word in text for word in ("双休", "不加班")):
        return ["双休", "不加班", "经验不限"]
    return ["经验不限", "最好满级", "立即沟通"]


def stamp_for_context(text: str, role: str, category: str) -> dict[str, Any] | None:
    if role not in {"pressure", "proof", "escalation"} and not any(word in text for word in ("已读", "拒", "压力", "加班", "焦虑", "排队")):
        return None
    label = {
        "career": "已读不回",
        "office": "再同步",
        "exam": "倒计时",
        "rent": "余额不足",
        "street_food": "利润-1",
    }.get(category, "压力+1")
    if any(word in text for word in ("请假", "不批准", "不批假")):
        label = "不批准"
    if "120" in text or "急救" in text:
        label = "老板慌了"
    return {"type": "stamp_reject", "start": 0.72, "duration": 0.95, "text": label}


def popup_for_context(text: str, role: str, category: str) -> dict[str, Any] | None:
    if role not in {"twist", "echo"} and not any(word in text for word in ("规则", "突然", "反转", "又", "更新")):
        return None
    label = {
        "career": "要求又更新",
        "office": "会议又加一场",
        "exam": "选择也要复习",
        "rent": "省钱也要成本",
        "street_food": "隔壁又降价",
    }.get(category, "规则更新")
    if any(word in text for word in ("请假", "不批准", "不批假")):
        label = "审批被打回"
    if "120" in text or "急救" in text:
        label = "正在呼叫120"
    return {"type": "popup", "start": 0.45, "duration": 1.65, "text": label}


def burst_for_context(text: str, role: str, category: str) -> dict[str, Any] | None:
    if role not in {"hook", "punchline", "cta"}:
        return None
    if role == "cta":
        label = "明天再说"
    elif role == "punchline":
        label = {
            "career": "先看规则",
            "office": "免打扰",
            "exam": "先做一题",
            "rent": "摊开预算",
            "street_food": "先暖手",
        }.get(category, "先过今天")
    else:
        label = "离谱"
    return {"type": "impact_burst", "start": 0.58, "duration": 1.0, "text": label}


def transition_planner_tool(beat: dict[str, Any], previous: dict[str, Any] | None, background_changed: bool) -> dict[str, Any]:
    role = str(beat.get("role", ""))
    if not previous:
        return {"type": "cut", "duration": 0.0}
    if role in {"twist", "punchline"}:
        return {"type": "flash", "duration": 0.18}
    if role in {"pressure", "escalation"}:
        return {"type": "zoom", "duration": 0.22}
    if background_changed:
        return {"type": "fade", "duration": 0.25}
    return {"type": "cut", "duration": 0.0}


def background_fill_tool(
    theme: str,
    beat: dict[str, Any],
    background: dict[str, Any],
    score: float,
    threshold: float = 1.0,
) -> tuple[dict[str, Any], str, str, str | None]:
    request = constrained_background_request(theme, beat)
    prompt = str(request.get("prompt") or "")
    description = str(request.get("description") or prompt)
    slug = str(request.get("slug") or slug_from_theme_scene(theme, beat))
    prompt_source = str(request.get("source") or "fallback")
    prompt_note = "Agent 受约束提示词" if prompt_source == "agent" else "规则提示词"
    if request.get("fallback_reason"):
        prompt_note = f"{prompt_note}（Agent prompt 已回退：{request['fallback_reason']}）"

    specific_missing = needs_specific_background(theme, beat, background)
    if score >= threshold and not specific_missing:
        return background, "matched", "", None

    if not seedream_available():
        if specific_missing:
            return background, "generated_pending", prompt, f"需要更具体的真实场景背景，已记录{prompt_note}；Seedream 未配置，暂用现有背景。"
        return background, "matched", prompt, "Seedream 未配置，保留最佳现有背景并用字幕补语义。"

    reason = (
        f"现有背景缺少具体场景，已用 Seedream 补图并刷新素材索引，使用{prompt_note}。"
        if specific_missing
        else f"现有背景素材匹配分低，自动尝试 Seedream 补图并刷新素材索引，使用{prompt_note}。"
    )

    try:
        generated = generate_background(
            prompt=prompt,
            description=description,
            slug=slug,
        )
        refreshed = load_assets()
        for item in refreshed.get("backgrounds", []):
            if item.get("file") == generated.get("file"):
                return item, "generated", prompt, reason
        return {
            "id": f"generated/{slug}",
            "file": generated.get("file", ""),
            "description": generated.get("description", prompt),
        }, "generated", prompt, reason
    except Exception as exc:
        source = "generated_pending" if specific_missing else "matched"
        return background, source, prompt, f"Seedream 补图失败，已回退现有背景并保留补图 prompt：{safe_error(exc)}"


def target_clip_duration(role: str, slot_duration: float) -> float:
    if role == "hook":
        target = min(3.0, slot_duration)
    elif role in {"setup", "proof", "echo"}:
        target = min(5.0, slot_duration)
    elif role in {"pressure", "twist", "escalation"}:
        target = min(4.0, slot_duration)
    else:
        target = min(5.0, slot_duration)
    return max(MIN_CLIP_DURATION, min(MAX_CLIP_DURATION, target))


def background_prompt_for_beat(theme: str, beat: dict[str, Any]) -> str:
    scenes = "，".join(str(item) for item in beat.get("scene_keywords", [])[:4])
    specific = ""
    joined = f"{theme} {beat.get('caption', '')} {scenes}"
    if any(word in joined for word in ("烤肠", "香肠", "摆摊", "小吃摊", "夜市", "街边摊", "餐车")):
        specific = "画面必须是写实街边烤肠摊或小吃摊，有烤肠机器、摊车、价目牌但不要可读文字，夜市或学校门口氛围。"
    return (
        f"竖屏短视频背景，猫 meme 社会现实主题：{theme}。"
        f"分镜：{beat.get('caption', '')}，场景关键词：{scenes or '城市生活'}。"
        f"{specific}"
        "写实但略带荒诞喜剧感，无人物主体，无文字，画面下方保持自然地面或桌面无遮挡，"
        "不要绿色幕布、不要纯色块，方便后期叠加抠像猫动画。"
    )


def constrained_background_request(theme: str, beat: dict[str, Any]) -> dict[str, object]:
    return constrain_background_prompt(
        theme=theme,
        caption=str(beat.get("caption") or ""),
        scene_keywords=[str(item) for item in beat.get("scene_keywords", []) if str(item).strip()],
        background_need=str(beat.get("background_need") or ""),
        seedream_prompt=str(beat.get("seedream_prompt") or ""),
        negative_constraints=[str(item) for item in beat.get("negative_constraints", [])] if isinstance(beat.get("negative_constraints"), list) else [],
        slug_hint=str(beat.get("slug_hint") or ""),
        fallback_prompt=background_prompt_for_beat(theme, beat),
        fallback_slug=slug_from_theme_scene(theme, beat),
    )


def needs_specific_background(theme: str, beat: dict[str, Any], background: dict[str, Any]) -> bool:
    beat_text = f"{beat.get('caption', '')} {beat.get('intent', '')} {' '.join(str(item) for item in beat.get('scene_keywords', []))}"
    if not any(word in beat_text for word in ("烤肠", "香肠", "摆摊", "小吃摊", "夜市", "街边摊", "餐车")):
        return False
    desc = f"{background.get('scene', '')} {background.get('description', '')}"
    if "generated" in str(background.get("scene", "")) and any(word in desc for word in ("烤肠", "香肠", "小吃摊", "夜市", "餐车", "摆摊")):
        return False
    return not any(word in desc for word in ("烤肠", "香肠", "小吃摊", "摊", "夜市", "餐车", "摊车"))


def slug_from_theme_scene(theme: str, beat: dict[str, Any]) -> str:
    raw = f"{theme}-{beat.get('role', '')}-{beat.get('caption', '')}"
    slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff_-]+", "-", raw).strip("-")
    return slug[:36] or "agent-background"


def seeded_start(seed: int, max_start: float, role: str) -> float:
    if max_start <= 0:
        return 0.0
    if role == "hook":
        return 0.0
    rng = random.Random(seed)
    return rng.uniform(0, max_start)


def stable_seed(*parts: Any) -> int:
    digest = hashlib.sha1("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def safe_error(exc: Exception) -> str:
    text = str(exc)
    text = re.sub(r"(api[_-]?key|authorization|bearer)\S*", "[secret]", text, flags=re.I)
    text = re.sub(r"https?://\S+", "[url]", text)
    return text[:180]


def find_asset(index: dict[str, Any], asset_type: str, asset_id: str) -> dict[str, Any] | None:
    collection = index.get("cat_motions" if asset_type == "motion" else "backgrounds", [])
    for asset in collection:
        if str(asset.get("id", "")) == str(asset_id):
            return asset
    for asset in collection:
        if str(asset_id) and str(asset_id) in f"{asset.get('id', '')} {asset.get('file', '')}":
            return asset
    return None


def lightweight_asset_score(asset: dict[str, Any], keywords: list[str]) -> float:
    text = lightweight_motion_text(asset)
    return sum(1.0 for keyword in keywords if keyword and keyword in text)


def lightweight_motion_tags(asset: dict[str, Any]) -> dict[str, list[str]]:
    tags = {"actions": [], "emotions": [], "contexts": [], "quality": [], "avoid": []}

    def add(key: str, value: str) -> None:
        if key in tags and value and value not in tags[key]:
            tags[key].append(value)

    raw = asset.get("motion_tags") if isinstance(asset.get("motion_tags"), dict) else {}
    aliases = {"action": "actions", "actions": "actions", "emotion": "emotions", "emotions": "emotions", "tone": "emotions", "context": "contexts", "contexts": "contexts", "quality": "quality", "avoid": "avoid"}
    for key, value in raw.items():
        target = aliases.get(str(key), "")
        values = value if isinstance(value, list) else [value]
        for item in values:
            add(target, str(item or "").strip())

    desc = str(asset.get("description", ""))
    rules = {
        "actions": ("偷看", "探头", "试探", "回头", "发呆", "抱奶茶", "休息", "电脑", "笔记本", "蹦跳", "跳舞", "弹琴", "演奏", "哭", "嚎啕", "开车", "方向盘", "叫嚷", "吐槽", "双猫"),
        "emotions": ("安静", "委屈", "可爱", "震惊", "冷漠", "崩溃", "焦虑", "欢快", "温暖", "生无可恋", "破防", "错愕", "可怜", "压抑", "强忍", "忐忑", "松一口气"),
        "contexts": ("职场", "求职", "考试", "通勤", "办公", "回忆", "亲情"),
        "quality": ("黑边", "白底", "需要裁切", "需裁切", "低清", "模糊"),
        "avoid": ("非猫素材", "默认避用", "过激", "只用于夸张"),
    }
    for key, values in rules.items():
        for value in values:
            if value in desc:
                add(key, value)
    return tags


def lightweight_motion_text(asset: dict[str, Any]) -> str:
    tags = lightweight_motion_tags(asset)
    tag_text = " ".join(item for values in tags.values() for item in values)
    return f"{asset.get('id', '')} {asset.get('scene', '')} {asset.get('file', '')} {asset.get('description', '')} {tag_text}"


def lightweight_motion_profile(theme: str, caption: str = "", role: str = "", intent: str = "") -> dict[str, list[str]]:
    text = f"{theme} {caption} {intent}"
    local_text = f"{caption} {intent}"
    prefer: list[str] = []
    avoid: list[str] = []

    def add(values: tuple[str, ...], blocked: tuple[str, ...]) -> None:
        for value in values:
            if value not in prefer:
                prefer.append(value)
        for value in blocked:
            if value not in avoid:
                avoid.append(value)

    quiet_avoid = ("电脑", "笔记本", "跳舞", "蹦跳", "弹琴", "演奏", "香蕉猫", "嚎啕", "疯狂", "射击", "开车", "方向盘")
    profiles = [
        (("偷", "偷吃", "躲", "偷看", "试探", "瞒天过海"), ("偷看", "探头", "试探", "忐忑", "委屈", "安静"), quiet_avoid, {"hook", "setup", "pressure", "proof"}),
        (("父亲", "妈妈", "父母", "家人", "亲情", "支持", "温暖", "无声的爱"), ("安静", "发呆", "回头", "委屈", "抱奶茶", "休息", "松一口气"), quiet_avoid, None),
        (("回头", "才知道", "发现", "原来", "真相", "多年后", "长大后"), ("回头", "迟疑", "发呆", "安静", "震惊"), quiet_avoid, None),
        (
            ("医院", "门诊", "病房", "检查", "报告", "等结果", "检查结果", "化验", "手心冒汗", "冒汗", "求助", "开口求助", "请假", "病假", "药", "发烧", "不舒服", "疼", "难受"),
            ("求救", "求助", "叫唤", "委屈", "可怜", "强忍", "压抑", "安静", "病痛求助"),
            ("电脑", "笔记本", "跳舞", "蹦跳", "弹琴", "演奏", "香蕉猫", "嚎啕", "喷泪", "大哭", "疯狂", "射击", "开车", "方向盘"),
            None,
        ),
        (
            ("免打扰", "静音", "假装没看见", "假装没听见", "不回复", "不想回", "已读不回", "拒绝", "边界", "别催", "轰炸", "消息轰炸"),
            ("摆手", "假装没听见", "拒绝", "免打扰", "边界拒绝", "无语", "冷眼", "摆烂"),
            ("电脑", "笔记本", "跳舞", "蹦跳", "弹琴", "演奏", "香蕉猫", "嚎啕", "疯狂", "射击", "开车", "方向盘"),
            None,
        ),
        (("电脑", "简历", "招聘", "岗位", "会议", "工作群"), ("电脑", "笔记本", "冷漠", "碎碎念"), ("开车", "方向盘", "山羊", "小狗"), None),
        (("通勤", "地铁", "公交", "堵车", "开车"), ("开车", "方向盘", "冷漠", "通勤"), ("电脑", "笔记本", "跳舞", "蹦跳"), None),
    ]
    for triggers, values, blocked, theme_roles in profiles:
        if any(trigger in local_text for trigger in triggers) or (any(trigger in theme for trigger in triggers) and (theme_roles is None or role in theme_roles)):
            add(values, blocked)
    return {"prefer": prefer[:12], "avoid": avoid[:14]}


def lightweight_role_motion_bonus(role: str, desc: str) -> float:
    mapping = {
        "hook": ("震惊", "瞪眼", "强反应", "探头", "惊叫"),
        "setup": ("电脑", "冷漠", "碎碎念", "探头"),
        "pressure": ("委屈", "哭", "崩溃", "焦虑", "生无可恋"),
        "proof": ("探头", "碎碎念", "双猫", "委屈"),
        "twist": ("震惊", "错愕", "看穿", "吐槽", "回头"),
        "echo": ("双猫", "委屈", "共鸣", "探头"),
        "escalation": ("哭", "嚎啕", "疯狂", "崩溃"),
        "punchline": ("跳舞", "欢快", "可爱", "蹦跳", "喘口气"),
        "cta": ("可爱", "休息", "欢快", "温暖"),
    }.get(role, ())
    return sum(0.9 for keyword in mapping if keyword in desc)


def lightweight_category_motion_bonus(category: str, desc: str) -> float:
    positive = {
        "career": ("电脑", "投简历", "吐槽", "看穿", "委屈"),
        "office": ("电脑", "会议", "摆烂", "生无可恋", "冷漠"),
        "exam": ("探头", "焦虑", "委屈", "哭", "查成绩"),
        "rent": ("委屈", "压抑", "冷漠", "可怜"),
        "street_food": ("吐槽", "魔性", "摆烂", "跳舞", "委屈"),
    }.get(category, ())
    negative = {
        "career": ("开车", "山羊", "小狗", "射击"),
        "office": ("开车", "山羊", "小狗", "射击"),
        "exam": ("开车", "射击", "免打扰"),
        "rent": ("电脑", "射击", "山羊"),
        "street_food": ("电脑", "开车", "射击"),
    }.get(category, ())
    return sum(0.8 for keyword in positive if keyword in desc) - sum(1.8 for keyword in negative if keyword in desc)


def lightweight_category_background_bonus(category: str, asset: dict[str, Any]) -> float:
    text = f"{asset.get('id', '')} {asset.get('scene', '')} {asset.get('file', '')} {asset.get('description', '')}"
    positive = {
        "career": ("招聘", "面试", "办公室", "校招", "简历", "job_fair"),
        "office": ("办公室", "会议", "工位", "加班", "meeting"),
        "exam": ("自习", "图书馆", "教室", "学校", "study"),
        "rent": ("出租屋", "房租", "账单", "床铺", "通勤"),
        "street_food": ("烤肠", "香肠", "小吃摊", "夜市", "摊车", "街边摊"),
        "relationship": ("室内", "对话", "客厅", "卧室", "餐桌", "房间", "日常", "building_interior", "city", "real_city"),
    }.get(category, ())
    negative = {
        "career": ("烤肠", "出租屋", "考研"),
        "office": ("烤肠", "出租屋", "考研"),
        "exam": ("招聘", "烤肠", "出租屋", "会议"),
        "rent": ("招聘", "会议", "烤肠", "考研"),
        "street_food": ("招聘", "会议", "自习", "出租屋"),
        "relationship": ("招聘", "面试", "公司楼下", "找工作", "通勤", "烤肠", "小吃摊", "自习", "图书馆"),
    }.get(category, ())
    return sum(2.2 for keyword in positive if keyword in text) - sum(2.6 for keyword in negative if keyword in text)


def casting_reason(theme: str, beat: dict[str, Any], asset: dict[str, Any]) -> str:
    desc = str(asset.get("description", ""))
    role = str(beat.get("role", ""))
    caption = str(beat.get("caption", ""))
    hits = [keyword for keyword in [role, caption, *beat.get("emotion_keywords", [])] if keyword and keyword in desc]
    if hits:
        return f"命中动作/情绪：{', '.join(str(item) for item in hits[:3])}"
    category = infer_theme_category(f"{theme} {caption}")
    if category:
        return f"按 {category} 场景选择相近猫动作"
    return "选择可泛化的猫表情动作"


def background_reason(theme: str, beat: dict[str, Any], asset: dict[str, Any]) -> str:
    desc = str(asset.get("description", ""))
    hits = [keyword for keyword in beat.get("scene_keywords", []) if keyword and keyword in desc]
    if hits:
        return f"命中背景关键词：{', '.join(str(item) for item in hits[:3])}"
    return f"作为“{theme}”分镜的最佳现有背景"


def normalize_agent_overlay_action(
    action: dict[str, Any],
    theme: str,
    beat: dict[str, Any],
    motion: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(action, dict):
        return {}
    raw_type = str(action.get("type") or "").strip()
    kind_map = {
        "primitive_card": "job_requirement_card",
        "chat_ui": "chat_stack",
        "phone_ui": "phone_job_feed",
        "thrown_prop": "throw_object",
        "stamp": "stamp_reject",
        "generated_sticker": "generated_sticker",
        "burst": "impact_burst",
    }
    kind = kind_map.get(raw_type, raw_type)
    start = clamp_float(action.get("start", 0.32), 0.0, 5.0)
    duration = clamp_float(action.get("duration", 1.8), 0.4, 4.8)
    text = clean_overlay_text(action.get("text") or action.get("label") or action.get("title") or "")
    story_text = f"{theme} {beat.get('caption', '')} {beat.get('intent', '')}"
    emotional_relationship = (
        infer_theme_category(story_text) == "relationship"
        and is_emotional_relationship_context(story_text)
        and not is_financial_relationship_context(story_text)
    )

    if kind == "throw_object":
        obj = str(action.get("object") or action.get("prop") or object_for_theme(theme, beat))
        if emotional_relationship and safe_object_name(obj) == "bill_stack":
            return None
        return {
            "type": "throw_object",
            "object": safe_object_name(obj),
            "from": str(action.get("from") or "left_cat"),
            "to": str(action.get("to") or "right_cat"),
            "start": start,
            "duration": duration,
            "text": text or label_for_object(obj, theme, beat),
        }
    if kind == "generated_sticker":
        sticker = sticker_for_context(theme, beat, preferred_text=text or str(action.get("object") or ""), motion=motion or {})
        if sticker:
            sticker["start"] = start
            sticker["duration"] = duration
            return sticker
        return None
    if kind in {"sticker", "local_sticker", "sticker_asset", "asset_sticker"}:
        file = safe_sticker_file(action.get("file") or action.get("sticker_file") or "")
        sticker = sticker_for_context(theme, beat, preferred_text=text or str(action.get("object") or ""), preferred_file=file, motion=motion or {})
        if not sticker:
            return None
        sticker["start"] = start
        sticker["duration"] = duration
        if action.get("motion"):
            sticker["motion"] = safe_sticker_motion(action.get("motion"))
        return sticker
    if kind in {"stamp_reject", "popup", "impact_burst"}:
        return {"type": kind, "start": start, "duration": duration, "text": text or default_overlay_label(kind, theme, beat)}
    if kind in {
        "phone_job_feed",
        "job_requirement_card",
        "work_chat_stack",
        "chat_stack",
        "choice_panel",
        "study_card",
        "bill_card",
        "commute_card",
        "stall_sign",
    }:
        if emotional_relationship and kind == "bill_card":
            return None
        normalized = {"type": kind, "start": start, "duration": duration}
        for key in ("title", "salary", "company"):
            if action.get(key):
                normalized[key] = clean_overlay_text(action.get(key), limit=18)
        for key in ("items", "messages", "options", "tags"):
            if isinstance(action.get(key), list):
                normalized[key] = [clean_overlay_text(item, limit=16) for item in action[key][:4] if clean_overlay_text(item, limit=16)]
        return normalized
    return {}


def clamp_float(value: Any, min_value: float, max_value: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = min_value
    return round(max(min_value, min(max_value, number)), 2)


def clean_overlay_text(value: Any, limit: int = 12) -> str:
    text = re.sub(r"\s+", "", str(value or "").strip())
    text = re.sub(r"[{}<>`$]", "", text)
    return text[:limit]


def object_for_theme(theme: str, beat: dict[str, Any]) -> str:
    text = f"{theme} {beat.get('caption', '')} {beat.get('intent', '')}"
    mapping = [
        (("烤肠", "香肠", "摊"), "sausage_skewer"),
        (("房租", "押金", "租房", "中介", "出租屋"), "bill_stack"),
        (("通勤", "地铁", "公交"), "metro_card"),
        (("考研", "考公", "自习", "资料"), "study_notes"),
        (("会议", "复盘", "同步", "老板"), "meeting_invite"),
        (("PPT", "汇报"), "ppt_deck"),
        (("要求", "经验", "门槛"), "requirement_scroll"),
        (("拒", "已读", "不回"), "reject_notice"),
        (("简历", "招聘", "岗位"), "resume_stack"),
    ]
    for triggers, obj in mapping:
        if any(trigger in text for trigger in triggers):
            return obj
    return "note_card"


def safe_object_name(value: str) -> str:
    allowed = {
        "resume_stack",
        "bill_stack",
        "metro_card",
        "study_notes",
        "exam_ticket",
        "meeting_invite",
        "ppt_deck",
        "requirement_scroll",
        "reject_notice",
        "price_tag",
        "sausage_skewer",
        "note_card",
    }
    value = re.sub(r"[^a-zA-Z0-9_-]", "", str(value))
    return value if value in allowed else "note_card"


def label_for_object(obj: str, theme: str, beat: dict[str, Any]) -> str:
    text = f"{theme} {beat.get('caption', '')}"
    labels = {
        "resume_stack": "简历x3",
        "bill_stack": "账单-1",
        "metro_card": "通勤2h",
        "study_notes": "资料x3",
        "exam_ticket": "准考证",
        "meeting_invite": "会议+1",
        "ppt_deck": "PPTx9",
        "requirement_scroll": "要求+1",
        "reject_notice": "暂不合适",
        "price_tag": "今日特价",
        "sausage_skewer": "烤肠x3",
    }
    if "赊账" in text:
        return "赊账申请"
    if "摊位费" in text:
        return "摊位费"
    return labels.get(obj, "现实+1")


def sticker_prompt_for_theme(theme: str, beat: dict[str, Any], label: str) -> str:
    return (
        f"透明背景猫 meme 贴纸，道具：{label or object_for_theme(theme, beat)}，"
        f"主题：{theme}，分镜：{beat.get('caption', '')}。"
        "扁平可爱短视频贴纸风，无文字或只有极少中文大字，适合叠加到竖屏视频。"
    )


def default_overlay_label(kind: str, theme: str, beat: dict[str, Any]) -> str:
    text = f"{theme} {beat.get('caption', '')}"
    if kind == "stamp_reject":
        if any(word in text for word in ("考研", "考公")):
            return "倒计时"
        if any(word in text for word in ("租房", "房租")):
            return "余额不足"
        if any(word in text for word in ("烤肠", "摊")):
            return "利润-1"
        if any(word in text for word in ("会议", "上班")):
            return "再同步"
        return "已读不回"
    if kind == "popup":
        return "规则更新"
    return "离谱"


def sticker_for_context(
    theme: str,
    beat: dict[str, Any],
    category: str = "",
    preferred_text: str = "",
    preferred_file: str = "",
    primary_type: str = "",
    motion: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    try:
        index = load_assets()
    except Exception:
        return None
    stickers = index.get("stickers", [])
    if not stickers:
        return None
    beat_text = sticker_beat_text(beat, preferred_text, primary_type)
    local_text = f"{theme} {beat_text}"
    category = category or overlay_category(theme, str(beat.get("caption", "")), local_text)
    profile = sticker_profile_for_context(local_text, category, primary_type)
    if not profile:
        return None
    if preferred_file:
        for asset in stickers:
            if asset.get("file") == preferred_file:
                if sticker_asset_score(asset, profile) >= float(profile.get("min_score", 6.0)):
                    return sticker_action_from_asset(
                        asset,
                        theme,
                        beat,
                        str(profile["anchor"]),
                        str(profile["motion"]),
                        motion or {},
                    )
                return None

    keywords = [str(item) for item in [*profile.get("keywords", []), *profile.get("target_terms", [])] if str(item)]
    ranked = rank_assets(stickers, keywords, limit=80)
    direct = [asset for asset in stickers if sticker_asset_score(asset, profile) >= float(profile.get("min_score", 6.0))]
    candidates = []
    seen_files: set[str] = set()
    for asset in [*ranked, *direct]:
        file = str(asset.get("file", ""))
        if file in seen_files:
            continue
        seen_files.add(file)
        candidates.append(asset)
    scored: list[tuple[float, dict[str, Any]]] = []
    for asset in candidates:
        score = sticker_asset_score(asset, profile)
        if score >= float(profile.get("min_score", 6.0)):
            scored.append((score, asset))
    scored.sort(key=lambda item: (-item[0], str(item[1].get("id", ""))))
    if not scored:
        return None
    if profile.get("intent") == "phone_message" and not phone_in_hand_asset(scored[0][1]):
        composite = phone_in_hand_composite_action(stickers, theme, beat, str(profile["anchor"]), str(profile["motion"]), motion or {})
        if composite:
            return composite
    return sticker_action_from_asset(
        scored[0][1],
        theme,
        beat,
        str(profile["anchor"]),
        str(profile["motion"]),
        motion or {},
    )


def sticker_action_from_asset(
    asset: dict[str, Any],
    theme: str,
    beat: dict[str, Any],
    anchor: str,
    motion: str = "static",
    cat_motion: dict[str, Any] | None = None,
) -> dict[str, Any]:
    role = str(beat.get("role", ""))
    motion = safe_sticker_motion(motion or sticker_motion_for_anchor(anchor, role))
    x, y, scale = sticker_layout_for_anchor(anchor, role, cat_motion or {})
    return {
        "type": "sticker",
        "sticker_id": str(asset.get("id", "")),
        "file": safe_sticker_file(asset.get("file", "")),
        "anchor": anchor,
        "motion": motion,
        "start": sticker_start_for_role(role),
        "duration": sticker_duration_for_role(role),
        "x": x,
        "y": y,
        "scale": scale,
        "rotation": 0,
        "text": clean_overlay_text(beat.get("caption") or theme, limit=10),
    }


def phone_in_hand_composite_action(
    stickers: list[dict[str, Any]],
    theme: str,
    beat: dict[str, Any],
    anchor: str,
    motion: str,
    cat_motion: dict[str, Any],
) -> dict[str, Any] | None:
    phone = best_component_asset(stickers, ("手机", "phone", "卡通手机"))
    hand = best_component_asset(stickers, ("主体是手。", "主体是手这一", "手势", "ok-hand", "-hand", "/hand"))
    if not phone or not hand:
        return None
    action = sticker_action_from_asset(
        {
            "id": "composite/phone-in-hand",
            "file": "",
            "description": "组合贴纸：手 + 手机",
        },
        theme,
        beat,
        anchor,
        motion,
        cat_motion,
    )
    action["sticker_id"] = "composite/phone-in-hand"
    action["file"] = ""
    action["composite"] = "phone_in_hand"
    action["components"] = [
        {
            "role": "phone",
            "file": safe_sticker_file(phone.get("file", "")),
            "x": 6,
            "y": -18,
            "scale": 0.94,
            "rotation": -4,
        },
        {
            "role": "hand",
            "file": safe_sticker_file(hand.get("file", "")),
            "x": -12,
            "y": 30,
            "scale": 0.88,
            "rotation": 8,
        },
    ]
    action["scale"] = round(float(action.get("scale", 0.78)) * 1.08, 2)
    return action


def best_component_asset(stickers: list[dict[str, Any]], terms: tuple[str, ...]) -> dict[str, Any] | None:
    scored: list[tuple[float, dict[str, Any]]] = []
    for asset in stickers:
        desc = sticker_asset_text(asset)
        hits = sum(1 for term in terms if sticker_text_contains(desc, term))
        if not hits:
            continue
        score = float(hits)
        if str(asset.get("category")) in {"digital-communication", "emotion-effects"}:
            score += 0.5
        if any(sticker_text_contains(desc, term) for term in ("ok-hand", "ok手势")):
            score -= 0.4
        scored.append((score, asset))
    scored.sort(key=lambda item: (-item[0], str(item[1].get("id", ""))))
    return scored[0][1] if scored else None


def phone_in_hand_asset(asset: dict[str, Any]) -> bool:
    desc = sticker_asset_text(asset)
    return any(sticker_text_contains(desc, term) for term in ("手持手机", "手拿手机", "phone-in-hand", "hand phone"))


def sticker_profile_for_context(text: str, category: str, primary_type: str = "") -> dict[str, Any] | None:
    normalized = str(text or "")
    if is_medical_sticker_context(normalized):
        return {
            "intent": "medical_leave",
            "category": "medical-emergency",
            "keywords": ["救护车", "药箱", "口罩", "120", "急救", "医疗"],
            "target_terms": ["救护车", "ambulance", "药箱", "medical-kit", "first-aid", "口罩", "mask"],
            "anchor": "corner",
            "motion": "static",
            "min_score": 6.0,
        }
    if is_exam_context(normalized):
        return {
            "intent": "exam",
            "category": "campus-study",
            "keywords": ["试卷", "书本", "铅笔", "考试", "刷题", "考研"],
            "target_terms": ["试卷", "exam-paper", "书本", "book", "铅笔", "pencil"],
            "anchor": "beside_card" if primary_type in {"study_card", "choice_panel"} else "near_cat",
            "motion": "static",
            "min_score": 6.0,
        }
    if is_street_food_context(normalized):
        return {
            "intent": "street_food",
            "category": "food-drinks",
            "keywords": ["烤肠", "香肠", "串串", "价签", "摊位"],
            "target_terms": ["烤肠", "香肠", "sausage", "串串", "skewer", "价签", "price-tag", "价格牌"],
            "anchor": "on_sign" if primary_type == "stall_sign" else "near_cat",
            "motion": "static",
            "min_score": 6.0,
        }
    if is_requirement_sticker_context(normalized):
        return {
            "intent": "job_requirement",
            "category": "emotion-effects",
            "keywords": ["问号", "感叹号", "简历", "纸张", "岗位要求", "三年经验"],
            "target_terms": ["问号", "question", "感叹号", "exclamation", "简历", "resume", "纸张", "文件", "document"],
            "anchor": "beside_card" if primary_type in {"job_requirement_card", "phone_job_feed"} else "above_cat",
            "motion": "static",
            "min_score": 6.0,
        }
    if is_phone_sticker_context(normalized):
        return {
            "intent": "phone_message",
            "category": "digital-communication",
            "keywords": ["手持手机", "手拿手机", "手机", "聊天气泡", "消息", "已读不回", "招聘APP"],
            "target_terms": ["手持手机", "手拿手机", "phone-in-hand", "hand phone", "手机", "phone", "聊天气泡", "chat-bubble", "气泡", "消息"],
            "anchor": "near_cat",
            "motion": "static",
            "min_score": 6.0,
        }
    if is_pressure_sticker_context(normalized):
        return {
            "intent": "pressure_burst",
            "category": "emotion-effects",
            "keywords": ["汗滴", "裂开", "闪电", "压力爆发", "崩溃"],
            "target_terms": ["汗滴", "sweat", "裂开", "crack", "闪电", "lightning", "爆炸", "explosion"],
            "anchor": "above_cat",
            "motion": "shake",
            "min_score": 6.0,
        }
    return None


def sticker_asset_score(asset: dict[str, Any], profile: dict[str, Any]) -> float:
    desc = sticker_asset_text(asset)
    target_terms = [str(item) for item in profile.get("target_terms", []) if str(item)]
    target_hits = [term for term in target_terms if sticker_text_contains(desc, term)]
    if not target_hits:
        return 0.0
    score = 6.0 + min(len(target_hits), 3) * 1.2
    category = str(profile.get("category") or "")
    if category and str(asset.get("category", "")) == category:
        score += 1.2
    keywords = [str(item) for item in profile.get("keywords", []) if str(item)]
    score += min(sum(1 for item in keywords if sticker_text_contains(desc, item)), 4) * 0.35
    if profile.get("intent") == "phone_message" and any(
        sticker_text_contains(desc, item)
        for item in ("手持手机", "手拿手机", "phone-in-hand", "hand phone")
    ):
        score += 3.0
    return score


def sticker_asset_text(asset: dict[str, Any]) -> str:
    return f"{asset.get('id', '')} {asset.get('file', '')} {asset.get('description', '')}".lower()


def sticker_text_contains(text: str, term: str) -> bool:
    return str(term or "").lower() in text


def sticker_keywords_for_context(text: str, category: str) -> list[str]:
    profile = sticker_profile_for_context(text, category)
    if profile:
        return [str(item) for item in profile.get("keywords", []) if str(item)][:30]
    base = [item for item in re.split(r"[\s，。！？、,.!?/|]+", text) if item]
    mapping = {
        "career": ["手机", "电脑", "鼠标", "工牌", "眼镜", "消息", "工作办公", "问号", "感叹号"],
        "office": ["电脑", "鼠标", "耳机", "工牌", "麦克风", "工作办公", "怒气", "闪电"],
        "exam": ["书本", "试卷", "课桌", "铅笔", "作业", "校园", "问号", "汗滴"],
        "rent": ["床", "桌", "手机", "账单", "行李箱", "交通", "汗滴", "裂开"],
        "street_food": ["烤肠", "碗筷", "零食", "外卖", "厨师帽", "摊", "食物饮品"],
        "medical": ["救护车", "药箱", "针筒", "口罩", "红十字", "医疗急救"],
    }
    if "120" in text or "急救" in text:
        category = "medical"
    semantic = []
    if is_requirement_sticker_context(text):
        semantic.extend(["问号", "感叹号", "离谱", "汗滴", "崩溃", "震惊"])
    if is_phone_sticker_context(text):
        semantic.extend(["手机", "卡通手机", "指针", "聊天", "消息"])
    if is_street_food_context(text):
        semantic.extend(["烤肠", "价签", "厨师帽", "食物饮品", "小吃摊"])
    if is_pressure_sticker_context(text):
        semantic.extend(["汗滴", "裂开", "闪电", "怒气", "爆炸"])
    return list(dict.fromkeys([*base, *semantic, *mapping.get(category, ["问号", "感叹号", "手机", "贴纸"])]))[:30]


def sticker_category_bonus(category: str, asset: dict[str, Any]) -> float:
    folder = str(asset.get("category", ""))
    preferred = {
        "career": {"digital-communication": 3.4, "career-identity": 2.2, "emotion-effects": 1.6},
        "office": {"digital-communication": 2.8, "career-identity": 2.0, "emotion-effects": 1.6},
        "exam": {"campus-study": 3.4, "emotion-effects": 1.6, "digital-communication": 0.8},
        "rent": {"home-daily": 3.0, "transport-travel": 1.8, "emotion-effects": 1.4},
        "street_food": {"food-drinks": 3.8, "career-identity": 1.4, "emotion-effects": 1.2},
        "medical": {"medical-emergency": 4.0, "emotion-effects": 1.4},
    }.get(category, {})
    return preferred.get(folder, 0.0)


def sticker_keyword_bonus(text: str, asset: dict[str, Any]) -> float:
    desc = f"{asset.get('id', '')} {asset.get('file', '')} {asset.get('description', '')}"
    bonus = 0.0
    folder = str(asset.get("category", ""))
    if any(item in text for item in ("APP", "手机", "已读", "不回", "消息", "招聘软件")) and any(item in desc for item in ("手机", "phone")):
        bonus += 4.0
    if any(item in text for item in ("APP", "手机", "已读", "不回", "消息")) and any(item in desc for item in ("mouse", "鼠标")):
        bonus -= 1.0
    if is_requirement_sticker_context(text):
        if folder == "emotion-effects" or any(item in desc for item in ("问号", "感叹号", "汗滴", "裂开", "闪电", "离谱", "崩溃")):
            bonus += 6.0
        if folder == "digital-communication" and not any(item in desc for item in ("指针", "cursor")):
            bonus -= 2.0
    if is_street_food_context(text):
        if folder == "food-drinks" or any(item in desc for item in ("烤肠", "价签", "厨师帽", "食物饮品")):
            bonus += 5.0
    if is_pressure_sticker_context(text) and (folder == "emotion-effects" or any(item in desc for item in ("汗滴", "裂开", "闪电", "怒气", "爆炸"))):
        bonus += 4.5
    direct_pairs = [
        (("招聘", "APP", "手机", "已读", "不回", "消息"), ("手机", "phone", "聊天", "消息")),
        (("电脑", "投简历", "工作"), ("电脑", "computer", "鼠标")),
        (("烤肠", "摆摊", "小吃摊"), ("烤肠", "零食", "碗筷", "厨师帽")),
        (("请假", "120", "急救"), ("救护车", "药箱", "红十字", "口罩")),
        (("考研", "考试", "刷题"), ("书本", "试卷", "课桌", "铅笔")),
    ]
    for triggers, targets in direct_pairs:
        if any(item in text for item in triggers) and any(item in desc for item in targets):
            bonus += 2.4
    return bonus


def sticker_anchor_for_context(text: str, category: str, role: str, primary_type: str = "") -> str:
    profile = sticker_profile_for_context(text, category, primary_type)
    return str(profile["anchor"]) if profile else "corner"


def sticker_beat_text(beat: dict[str, Any], preferred_text: str = "", primary_type: str = "") -> str:
    text = f"{beat.get('caption', '')} {beat.get('intent', '')} {preferred_text}"
    hints = {
        "phone_job_feed": "招聘APP 手机 消息 聊天气泡",
        "job_requirement_card": "岗位要求 问号 感叹号 离谱",
        "stall_sign": "烤肠 价签 小吃摊",
        "chat_stack": "手机 消息 聊天 已读不回",
        "work_chat_stack": "手机 工作群 消息 聊天气泡",
        "bill_card": "账单 汗滴 裂开",
        "commute_card": "地铁 通勤 汗滴",
        "study_card": "书本 试卷 铅笔",
        "choice_panel": "考试 试卷 书本 铅笔",
        "leave_request": "请假 药箱 口罩 医疗",
        "emergency_call": "120 救护车 药箱 口罩",
    }.get(primary_type, "")
    return f"{text} {hints}".strip()


def primary_overlay_type(actions: list[dict[str, Any]]) -> str:
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
    for action in actions:
        if action.get("type") in primary_types:
            return str(action.get("type"))
    return ""


def sticker_motion_for_anchor(anchor: str, role: str) -> str:
    if anchor == "above_cat" and role in {"pressure", "escalation"}:
        return "shake"
    return "static"


def sticker_motion_for_role(role: str) -> str:
    return {
        "hook": "static",
        "setup": "static",
        "proof": "static",
        "pressure": "shake",
        "escalation": "shake",
        "twist": "static",
        "punchline": "static",
        "cta": "fade",
        "echo": "fade",
    }.get(role, "static")


def sticker_layout_for_role(role: str) -> tuple[int, int, float]:
    return (742, 132, 0.78)


def sticker_layout_for_anchor(anchor: str, role: str, motion: dict[str, Any] | None = None) -> tuple[int, int, float]:
    layout = cat_layout_for_motion(motion or {})
    if layout:
        dynamic = sticker_layout_from_cat_layout(anchor, layout)
        if dynamic:
            return dynamic
    layouts = {
        "near_cat": (410, 400, 0.78),
        "above_cat": (500, 132, 0.82),
        "beside_card": (512, 154, 0.74),
        "on_sign": (506, 340, 0.92),
        "corner": (742, 132, 0.78),
    }
    return layouts.get(anchor, sticker_layout_for_role(role))


def cat_layout_for_motion(motion: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(motion, dict):
        return {}
    layout = motion.get("cat_layout") or motion.get("layout")
    if not isinstance(layout, dict):
        return {}
    body = safe_layout_box(layout.get("body_box") or layout.get("bbox") or layout.get("box"))
    if not body:
        return {}
    head = safe_layout_box(layout.get("head_box") or layout.get("face_box")) or infer_head_box(body)
    face_direction = str(layout.get("face_direction") or layout.get("gaze") or "center").strip().lower()
    if face_direction not in {"left", "right", "center"}:
        face_direction = "center"
    return {"body_box": body, "head_box": head, "face_direction": face_direction}


def sticker_layout_from_cat_layout(anchor: str, layout: dict[str, Any]) -> tuple[int, int, float] | None:
    body = layout.get("body_box") or {}
    head = layout.get("head_box") or {}
    direction = str(layout.get("face_direction") or "center")
    if anchor == "near_cat":
        head_center_y = float(head.get("y", body.get("y", 220))) + float(head.get("h", body.get("h", 240))) * 0.68
        body_bottom = float(body.get("y", 260)) + float(body.get("h", 220))
        y = clamp_number(max(head_center_y, body_bottom - 90), 300, 458)
        if direction == "left":
            x = float(body.get("x", 430)) - 34
        elif direction == "right":
            x = float(body.get("x", 430)) + float(body.get("w", 220)) + 34
        else:
            x = float(body.get("x", 430)) + float(body.get("w", 220)) * 0.5
        return round(clamp_number(x, 96, 864)), round(y), 0.78
    if anchor == "above_cat":
        x = float(head.get("x", body.get("x", 430))) + float(head.get("w", body.get("w", 220))) * 0.5
        y = float(head.get("y", body.get("y", 220))) - 28
        return round(clamp_number(x, 120, 840)), round(clamp_number(y, 92, 250)), 0.8
    return None


def safe_layout_box(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    x = optional_float(value.get("x"))
    y = optional_float(value.get("y"))
    w = optional_float(value.get("w", value.get("width")))
    h = optional_float(value.get("h", value.get("height")))
    if x is None or y is None or w is None or h is None or w <= 0 or h <= 0:
        return {}
    return {
        "x": clamp_number(x, 0, 960),
        "y": clamp_number(y, 0, 544),
        "w": clamp_number(w, 1, 960),
        "h": clamp_number(h, 1, 544),
    }


def infer_head_box(body: dict[str, float]) -> dict[str, float]:
    return {
        "x": body["x"] + body["w"] * 0.18,
        "y": body["y"] + body["h"] * 0.08,
        "w": body["w"] * 0.64,
        "h": body["h"] * 0.42,
    }


def clamp_number(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, float(value)))


def optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def is_phone_sticker_context(text: str) -> bool:
    return any(word in text for word in ("招聘APP", "招聘软件", "APP", "手机", "已读", "不回", "消息", "群通知", "聊天"))


def is_requirement_sticker_context(text: str) -> bool:
    return any(word in text for word in ("岗位要求", "任职要求", "要求", "三年经验", "3年经验", "经验", "门槛", "全栈", "全链路", "JD"))


def is_pressure_sticker_context(text: str) -> bool:
    return any(word in text for word in ("压力爆发", "崩溃", "破防", "焦虑", "爆发", "裂开", "汗", "救命"))


def is_medical_sticker_context(text: str) -> bool:
    return any(word in text for word in ("医疗", "请假", "120", "急救", "救护车", "药箱", "口罩", "生病"))


def sticker_start_for_role(role: str) -> float:
    return 0.22 if role == "hook" else 0.38 if role in {"setup", "proof"} else 0.52


def sticker_duration_for_role(role: str) -> float:
    return 1.65 if role == "hook" else 1.35 if role in {"punchline", "cta"} else 1.85


def safe_sticker_motion(value: Any) -> str:
    motion = str(value or "").strip()
    return motion if motion in {"static", "fly_in", "bounce", "stamp", "shake", "rotate", "fade"} else "static"


def safe_sticker_file(value: Any) -> str:
    file = str(value or "").replace("\\", "/").strip()
    if not file or ".." in file:
        return ""
    if not file.startswith("assets/stickers/"):
        return ""
    if not re.search(r"\.(png|jpe?g|webp)$", file, flags=re.I):
        return ""
    return file


def json_dump_safe(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)
