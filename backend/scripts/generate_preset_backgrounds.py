from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.seedream_service import constrain_background_prompt, generate_background, seedream_available


PRESET_BACKGROUNDS = [
    {
        "slug_hint": "preset-job-fair-waiting-area",
        "theme": "大学生求职、校招面试和岗位要求离谱",
        "caption": "投出去的简历像进了黑洞",
        "scene_keywords": ["校园招聘会", "面试等待区", "简历", "HR", "校招"],
        "background_need": "校园招聘会或公司面试等待区，适合求职猫等待、投简历和 HR 对话分镜",
        "seedream_prompt": (
            "写实竖屏短视频背景，校园招聘会或公司面试等待区，桌上有简历夹、"
            "招聘展架但无可读文字，空间真实明亮，画面下方是自然地面或桌面并保持无遮挡，"
            "不要绿色幕布、不要纯色块，适合猫 meme 求职短剧。"
        ),
        "negative_constraints": ["无可读文字", "无人物主体", "不要绿色幕布", "不要纯色块"],
        "description": (
            "Seedream 生成的校园招聘会/面试等待区背景，简历夹、招聘展架和等候区氛围明显，"
            "无可读文字，适合求职、HR 对话、岗位要求离谱。关键词：招聘会、面试等待区、简历、HR、校招。"
        ),
    },
    {
        "slug_hint": "preset-meeting-room-involution",
        "theme": "职场开会、同步会和隐形加班",
        "caption": "会议从早上排到晚上",
        "scene_keywords": ["会议室", "长桌", "投影", "白板", "咖啡杯"],
        "background_need": "开不完会的真实会议室，适合职场内卷和加班分镜",
        "seedream_prompt": (
            "写实会议室背景，长桌、投影幕、椅子、白板和咖啡杯，氛围像开不完的同步会，"
            "无人物主体，无可读文字，画面下方是自然会议室地面并保持无遮挡，不要绿色幕布或纯色块。"
        ),
        "negative_constraints": ["无可读文字", "无人物主体", "不要绿色幕布", "不要纯色块"],
        "description": (
            "Seedream 生成的真实会议室内卷背景，长桌、白板、投影和咖啡杯，"
            "适合会议、复盘、同步、老板在吗、隐形加班。"
        ),
    },
    {
        "slug_hint": "preset-exam-study-room",
        "theme": "考研考公、备考焦虑和查成绩",
        "caption": "选择也要复习",
        "scene_keywords": ["高校图书馆", "考研自习室", "书本", "台灯", "便签"],
        "background_need": "高校图书馆或考研自习室，适合备考焦虑和查成绩分镜",
        "seedream_prompt": (
            "写实高校图书馆或考研自习室背景，桌面有书本、台灯、水杯和便签，"
            "无可读文字，座位安静密集，画面下方是自然桌面或地面并保持无遮挡，"
            "不要绿色幕布、不要纯色块。"
        ),
        "negative_constraints": ["无可读文字", "无人物主体", "不要绿色幕布", "不要纯色块"],
        "description": (
            "Seedream 生成的考研考公自习室背景，书本、台灯和密集座位，"
            "适合备考、查成绩、上岸焦虑、同学互助。"
        ),
    },
    {
        "slug_hint": "preset-rental-bill-room",
        "theme": "租房、押金、合租和账单压力",
        "caption": "省钱也要成本",
        "scene_keywords": ["小出租屋", "床边小桌", "账单纸", "行李箱", "简易衣架"],
        "background_need": "小出租屋账单角落，适合房租押金和合租压力分镜",
        "seedream_prompt": (
            "写实小出租屋背景，床边小桌、账单纸、行李箱和简易衣架，无可读文字，"
            "空间真实稍拥挤，画面下方是自然地板并保持无遮挡，不要绿色幕布或纯色块。"
        ),
        "negative_constraints": ["无可读文字", "无人物主体", "不要绿色幕布", "不要纯色块"],
        "description": (
            "Seedream 生成的小出租屋账单背景，床边桌、账单、行李箱和简易衣架，"
            "适合房租、押金、合租、搬家压力。"
        ),
    },
    {
        "slug_hint": "preset-family-budget-table",
        "theme": "家庭预算、买房房贷和彩礼现实沟通",
        "caption": "现实账单摊开在饭桌上",
        "scene_keywords": ["家庭餐桌", "账本", "计算器", "晚饭碗筷", "水杯"],
        "background_need": "家庭餐桌预算场景，适合现实账单和父母沟通分镜",
        "seedream_prompt": (
            "写实家庭餐桌背景，桌上有账本、计算器、水杯和晚饭碗筷，无可读文字，"
            "氛围现实但不压迫，画面下方是自然桌面并保持无遮挡，不要绿色幕布或纯色块。"
        ),
        "negative_constraints": ["无可读文字", "无人物主体", "不要绿色幕布", "不要纯色块"],
        "description": (
            "Seedream 生成的家庭预算饭桌背景，账本、计算器和餐具，"
            "适合彩礼、买房、房贷、父母沟通和现实账单对话。"
        ),
    },
]


def main() -> None:
    if not seedream_available():
        raise SystemExit("Seedream is not configured; skipped preset background generation.")

    for item in PRESET_BACKGROUNDS:
        constrained = constrain_background_prompt(
            theme=item["theme"],
            caption=item["caption"],
            scene_keywords=item["scene_keywords"],
            background_need=item["background_need"],
            seedream_prompt=item["seedream_prompt"],
            negative_constraints=item["negative_constraints"],
            slug_hint=item["slug_hint"],
            fallback_slug=item["slug_hint"],
        )
        asset = generate_background(
            prompt=str(constrained["prompt"]),
            description=item["description"],
            slug=str(constrained["slug"]),
        )
        print(f"{asset['file']} <- {constrained['source']}")


if __name__ == "__main__":
    main()
