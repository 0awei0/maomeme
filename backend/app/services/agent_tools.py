from __future__ import annotations

import hashlib
import json
import random
import re
from typing import Any

from .asset_index import load_assets, rank_assets, ref
from .seedream_service import generate_background, seedream_available
from .viral_structure_library import infer_theme_category

MIN_CLIP_DURATION = 2.0
MAX_CLIP_DURATION = 5.0


def asset_search_tool(
    index: dict[str, Any],
    asset_type: str,
    keywords: list[str],
    limit: int = 5,
) -> list[dict[str, Any]]:
    collection = index.get("cat_motions" if asset_type == "motion" else "backgrounds", [])
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
    for asset in candidates:
        desc = str(asset.get("description", ""))
        score = lightweight_asset_score(asset, keywords)
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
    keywords = [keyword for keyword in keywords if keyword and not scene_keyword_conflicts(keyword, local_category)]
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
    actions = [normalize_agent_overlay_action(action, theme, beat) for action in requested_actions or []]
    actions = [action for action in actions if action]
    if not actions:
        actions = overlay_planner_tool(beat, motion or {}, background or {}, theme)
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

    motion_text = f"{slot.get('motion', {}).get('id', '')} {slot.get('motion', {}).get('description', '')}"
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
    if background_id and background_id in used_background and len(used_background) <= 2:
        score -= 0.08
        issues.append("背景重复偏多")
        hints.append("同场景可保留，场景变化时换背景")
    if beat.get("layout") == "dialogue" and not slot.get("dialogue"):
        score -= 0.12
        issues.append("对话镜头缺少左右气泡台词")
        hints.append("补两句短对话，一句推动冲突，一句形成反差")
    if not slot.get("overlay_actions"):
        score -= 0.1
        issues.append("缺少贴图/弹窗包装")
        hints.append("根据主题生成具体道具，不要重复使用同一飞物件")
    if "简历 x100" in json_dump_safe(slot) and "简历" not in f"{theme} {beat.get('caption', '')}":
        score -= 0.18
        issues.append("贴图文案和主题不匹配")
        hints.append("把飞物件换成该主题的具体物件")

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
    core_text = f"{theme} {caption} {intent} {dialogue} {scene_text}"
    asset_text = f"{core_text} {motion.get('description', '')} {background.get('description', '')}"
    category = overlay_category(theme, local_text, core_text)

    primary = primary_overlay_for_context(category, role, caption, local_text, core_text)
    actions: list[dict[str, Any]] = [primary] if primary else []

    for action in [
        throw_object_for_context(core_text, role, category),
        stamp_for_context(core_text, role, category),
        popup_for_context(asset_text, role, category),
        burst_for_context(core_text, role, category),
    ]:
        if action:
            actions.append(action)

    return select_overlay_actions(actions)


def overlay_category(theme: str, local_text: str, text: str) -> str:
    theme_category = infer_theme_category(theme)
    theme_local = f"{theme} {local_text}"
    if theme_category in {"street_food", "office", "exam", "rent", "career"}:
        if theme_category == "office" and is_workplace_context(theme_local):
            return "office"
        if theme_category == "street_food" and is_street_food_context(theme_local):
            return "street_food"
        if theme_category == "exam" and is_exam_context(theme_local):
            return "exam"
        if theme_category == "rent" and is_rent_context(theme_local):
            return "rent"
        if theme_category == "career" and is_career_context(theme_local):
            return "career"
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


def scene_keyword_conflicts(keyword: str, category: str) -> bool:
    text = str(keyword)
    if is_street_food_context(text) and category != "street_food":
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
    if category in {"relationship", "family"}:
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
    primary_types = {"phone_job_feed", "job_requirement_card", "work_chat_stack", "chat_stack", "choice_panel", "study_card", "bill_card", "commute_card", "stall_sign"}
    primary = next((action for action in unique if action.get("type") in primary_types), None)
    if primary:
        secondary = next((action for action in unique if action is not primary and action.get("type") == "throw_object"), None)
        if not secondary:
            secondary = next((action for action in unique if action is not primary and action.get("type") in {"impact_burst", "stamp_reject", "popup"}), None)
        return [item for item in (primary, secondary) if item]
    return unique[:2]


def throw_object_for_context(text: str, role: str, category: str = "") -> dict[str, Any] | None:
    if role not in {"setup", "pressure", "proof", "twist", "escalation", "echo"}:
        return None
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
    return any(word in text for word in ("工作", "就业", "求职", "招聘", "简历", "岗位", "面试", "HR", "offer", "校招", "薪资", "工资"))


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
    if needs_specific_background(theme, beat, background):
        prompt = background_prompt_for_beat(theme, beat)
        return background, "generated_pending", prompt, "需要更具体的真实场景背景，已记录补图 prompt，分镜阶段先快速返回。"
    if score >= threshold:
        return background, "matched", "", None

    prompt = background_prompt_for_beat(theme, beat)
    reason = "现有背景素材匹配分低，自动尝试 Seedream 补图。"
    if not seedream_available():
        return background, "matched", prompt, "Seedream 未配置，保留最佳现有背景并用字幕补语义。"

    try:
        generated = generate_background(
            prompt=prompt,
            description=f"{theme}｜{beat.get('caption', '')}｜竖屏猫 meme 背景，无文字，适合绿幕猫叠加",
            slug=slug_from_theme_scene(theme, beat),
        )
        refreshed = load_assets()
        for item in refreshed.get("backgrounds", []):
            if item.get("file") == generated.get("file"):
                return item, "generated", prompt, reason
        return {
            "id": f"generated/{slug_from_theme_scene(theme, beat)}",
            "file": generated.get("file", ""),
            "description": generated.get("description", prompt),
        }, "generated", prompt, reason
    except Exception as exc:
        return background, "matched", prompt, f"Seedream 补图失败，已回退现有背景：{safe_error(exc)}"


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
    text = f"{asset.get('id', '')} {asset.get('scene', '')} {asset.get('file', '')} {asset.get('description', '')}"
    return sum(1.0 for keyword in keywords if keyword and keyword in text)


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
    }.get(category, ())
    negative = {
        "career": ("烤肠", "出租屋", "考研"),
        "office": ("烤肠", "出租屋", "考研"),
        "exam": ("招聘", "烤肠", "出租屋", "会议"),
        "rent": ("招聘", "会议", "烤肠", "考研"),
        "street_food": ("招聘", "会议", "自习", "出租屋"),
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


def normalize_agent_overlay_action(action: dict[str, Any], theme: str, beat: dict[str, Any]) -> dict[str, Any]:
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

    if kind == "throw_object":
        obj = str(action.get("object") or action.get("prop") or object_for_theme(theme, beat))
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
        prompt = str(action.get("prompt") or sticker_prompt_for_theme(theme, beat, text))
        return {
            "type": "generated_sticker",
            "start": start,
            "duration": duration,
            "text": text or clean_overlay_text(action.get("object") or "贴纸"),
            "prompt": prompt[:260],
        }
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


def json_dump_safe(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)
