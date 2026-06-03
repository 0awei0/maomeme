from __future__ import annotations

import hashlib
import random
import re
from typing import Any

from .asset_index import load_assets, rank_assets, ref
from .seedream_service import generate_background, seedream_available

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
    return collection[:limit]


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


def overlay_planner_tool(beat: dict[str, Any], motion: dict[str, Any], background: dict[str, Any]) -> list[dict[str, Any]]:
    role = str(beat.get("role", ""))
    caption = str(beat.get("caption", ""))
    intent = str(beat.get("intent", ""))
    joined = f"{caption} {intent} {motion.get('description', '')} {background.get('description', '')}"
    actions: list[dict[str, Any]] = []

    if any(word in joined for word in ("要求", "经验", "全链路", "全栈", "团队", "门槛")):
        actions.append({
            "type": "job_requirement_card",
            "start": 0.35,
            "duration": 2.2,
            "title": "岗位要求",
            "items": requirement_items_for_text(joined),
        })
    elif any(word in joined for word in ("刷", "招聘软件", "招聘APP", "薪资", "工资", "心仪岗位")):
        actions.append({
            "type": "phone_job_feed",
            "start": 0.25,
            "duration": 2.2,
            "title": caption or "刷到薪资还行的岗位",
            "salary": "薪资还行",
            "company": "校招热岗",
            "tags": ["应届可投", "双休", "立即沟通"],
        })
    if any(word in joined for word in ("简历", "招聘", "岗位", "投递", "面试")):
        actions.append({
            "type": "throw_object",
            "object": "resume_stack",
            "from": "left_cat",
            "to": "right_cat",
            "start": 0.65,
            "duration": 1.2,
            "text": "简历 x100",
        })
    if role in {"pressure", "proof", "escalation"} or any(word in joined for word in ("已读", "拒", "压力", "加班", "焦虑")):
        actions.append({
            "type": "stamp_reject",
            "start": 0.7,
            "duration": 1.0,
            "text": "已读不回" if "工作" in joined or "简历" in joined else "压力+1",
        })
    if role in {"twist", "echo"} or any(word in joined for word in ("要求", "规则", "突然", "反转")):
        actions.append({
            "type": "popup",
            "start": 0.45,
            "duration": 1.8,
            "text": "规则更新",
        })
    if any(word in joined for word in ("会议", "加班", "复盘", "同步", "老板", "KPI")):
        actions.append({
            "type": "work_chat_stack",
            "start": 0.3,
            "duration": 2.2,
            "title": "工作群",
            "messages": ["老板：在吗", "再同步一次", "今晚辛苦下"],
        })
    if any(word in joined for word in ("考研", "考公", "上岸", "考试", "就业")) and "招聘" not in joined:
        actions.append({
            "type": "choice_panel",
            "start": 0.3,
            "duration": 2.1,
            "title": "请选择今天焦虑",
            "options": ["考研", "考公", "就业"],
        })
    if any(word in joined for word in ("烤肠", "香肠", "摆摊", "小吃摊", "夜市", "摊位")):
        actions.append({
            "type": "stall_sign",
            "start": 0.3,
            "duration": 2.2,
            "title": "校门口小摊",
            "items": ["烤肠 3元", "加料 +1", "今日也内卷"],
        })
    if role in {"hook", "punchline", "cta"}:
        actions.append({
            "type": "impact_burst",
            "start": 0.55,
            "duration": 1.1,
            "text": "离谱" if role != "cta" else "明天再说",
        })

    return actions[:2]


def requirement_items_for_text(text: str) -> list[str]:
    items: list[str] = []
    if any(word in text for word in ("3年", "三年", "5年", "五年", "经验")):
        items.append("3年以上经验")
    if any(word in text for word in ("全链路", "全栈", "运营")):
        items.append("会全链路运营")
    if any(word in text for word in ("团队", "管理")):
        items.append("带过团队")
    if any(word in text for word in ("应届", "校招", "毕业")):
        items.append("欢迎应届生")
    return items[:4] or ["经验不限但要满级", "能抗压", "会很多"]


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
