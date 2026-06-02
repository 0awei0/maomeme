from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.seedream_service import generate_background, seedream_available


PRESET_BACKGROUNDS = [
    {
        "slug": "preset-job-fair-waiting-area",
        "prompt": (
            "写实竖屏短视频背景，校园招聘会或公司面试等待区，桌上有简历夹、"
            "招聘展架但无可读文字，空间真实明亮，画面下方是自然地面或桌面并保持无遮挡，"
            "不要绿色幕布、不要纯色块，适合猫 meme 求职短剧。"
        ),
        "description": (
            "Seedream 生成的校园招聘会/面试等待区背景，简历夹、招聘展架和等候区氛围明显，"
            "无可读文字，适合求职、HR 对话、岗位要求离谱。关键词：招聘会、面试等待区、简历、HR、校招。"
        ),
    },
    {
        "slug": "preset-meeting-room-involution",
        "prompt": (
            "写实会议室背景，长桌、投影幕、椅子、白板和咖啡杯，氛围像开不完的同步会，"
            "无人物主体，无可读文字，画面下方是自然会议室地面并保持无遮挡，不要绿色幕布或纯色块。"
        ),
        "description": (
            "Seedream 生成的真实会议室内卷背景，长桌、白板、投影和咖啡杯，"
            "适合会议、复盘、同步、老板在吗、隐形加班。"
        ),
    },
    {
        "slug": "preset-exam-study-room",
        "prompt": (
            "写实高校图书馆或考研自习室背景，桌面有书本、台灯、水杯和便签，"
            "无可读文字，座位安静密集，画面下方是自然桌面或地面并保持无遮挡，"
            "不要绿色幕布、不要纯色块。"
        ),
        "description": (
            "Seedream 生成的考研考公自习室背景，书本、台灯和密集座位，"
            "适合备考、查成绩、上岸焦虑、同学互助。"
        ),
    },
    {
        "slug": "preset-rental-bill-room",
        "prompt": (
            "写实小出租屋背景，床边小桌、账单纸、行李箱和简易衣架，无可读文字，"
            "空间真实稍拥挤，画面下方是自然地板并保持无遮挡，不要绿色幕布或纯色块。"
        ),
        "description": (
            "Seedream 生成的小出租屋账单背景，床边桌、账单、行李箱和简易衣架，"
            "适合房租、押金、合租、搬家压力。"
        ),
    },
    {
        "slug": "preset-family-budget-table",
        "prompt": (
            "写实家庭餐桌背景，桌上有账本、计算器、水杯和晚饭碗筷，无可读文字，"
            "氛围现实但不压迫，画面下方是自然桌面并保持无遮挡，不要绿色幕布或纯色块。"
        ),
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
        asset = generate_background(
            prompt=item["prompt"],
            description=item["description"],
            slug=item["slug"],
        )
        print(asset["file"])


if __name__ == "__main__":
    main()
