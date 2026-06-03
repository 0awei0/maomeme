from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


FONT_CANDIDATES = [
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
]


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def ease_out(t: float) -> float:
    return 1 - (1 - t) * (1 - t)


def draw_text_center(draw: ImageDraw.ImageDraw, center: tuple[int, int], text: str, font, fill, stroke=3) -> None:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
    draw.text(
        (center[0] - (right - left) / 2, center[1] - (bottom - top) / 2),
        text,
        font=font,
        fill=fill,
        stroke_width=stroke,
        stroke_fill=(0, 0, 0, 210),
    )


def fit_text(text: str, max_chars: int) -> str:
    text = str(text or "").strip()
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"


def draw_card_shadow(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], radius: int = 22) -> None:
    x0, y0, x1, y1 = box
    draw.rounded_rectangle((x0 + 8, y0 + 10, x1 + 8, y1 + 10), radius=radius, fill=(0, 0, 0, 78))


def panel_progress_y(base_y: int, progress: float, distance: int = 42) -> int:
    return int(base_y + distance * (1 - ease_out(progress)))


def draw_resume(draw: ImageDraw.ImageDraw, x: float, y: float, text: str, angle_hint: float) -> None:
    box = (int(x), int(y), int(x + 120), int(y + 82))
    draw.rounded_rectangle(box, radius=10, fill=(255, 255, 245, 235), outline=(44, 64, 82, 220), width=3)
    draw.text((box[0] + 14, box[1] + 10), "RESUME", font=load_font(18), fill=(34, 47, 58, 255))
    draw.line((box[0] + 14, box[1] + 40, box[2] - 14, box[1] + 40), fill=(60, 80, 96, 180), width=2)
    draw.line((box[0] + 14, box[1] + 56, box[2] - 34, box[1] + 56), fill=(60, 80, 96, 150), width=2)
    draw_text_center(draw, (int(x + 60), int(y - 22 + math.sin(angle_hint) * 4)), text, load_font(22), (255, 255, 255, 255), stroke=3)


def draw_stamp(draw: ImageDraw.ImageDraw, progress: float, text: str) -> None:
    scale = 0.7 + 0.45 * min(progress * 2, 1)
    cx, cy = 690, 302
    w, h = int(230 * scale), int(92 * scale)
    box = (cx - w // 2, cy - h // 2, cx + w // 2, cy + h // 2)
    draw.rounded_rectangle(box, radius=8, outline=(214, 39, 40, 245), width=8)
    draw_text_center(draw, (cx, cy), text, load_font(int(38 * scale)), (214, 39, 40, 245), stroke=1)


def draw_popup(draw: ImageDraw.ImageDraw, progress: float, text: str) -> None:
    y = int(118 - 34 * (1 - ease_out(progress)))
    box = (594, y, 900, y + 86)
    draw.rounded_rectangle(box, radius=16, fill=(29, 111, 99, 235))
    draw.ellipse((box[0] + 18, box[1] + 24, box[0] + 54, box[1] + 60), fill=(255, 255, 255, 240))
    draw_text_center(draw, (box[0] + 182, box[1] + 44), text, load_font(25), (255, 255, 255, 255), stroke=1)


def draw_burst(draw: ImageDraw.ImageDraw, progress: float, text: str) -> None:
    cx, cy = 480, 300
    radius = int(92 + 18 * math.sin(progress * math.pi))
    points = []
    for i in range(18):
        angle = i / 18 * math.tau
        r = radius if i % 2 == 0 else int(radius * 0.68)
        points.append((cx + math.cos(angle) * r, cy + math.sin(angle) * r))
    draw.polygon(points, fill=(255, 218, 66, 238), outline=(31, 31, 31, 245))
    draw_text_center(draw, (cx, cy), text, load_font(46), (29, 31, 36, 255), stroke=2)


def draw_phone_job_feed(draw: ImageDraw.ImageDraw, progress: float, action: dict) -> None:
    y = panel_progress_y(74, progress, 60)
    box = (586, y, 886, y + 356)
    draw_card_shadow(draw, box, 28)
    draw.rounded_rectangle(box, radius=28, fill=(24, 29, 36, 242), outline=(255, 255, 255, 210), width=3)
    draw.rounded_rectangle((box[0] + 22, box[1] + 20, box[2] - 22, box[1] + 58), radius=18, fill=(245, 248, 250, 255))
    draw.text((box[0] + 42, box[1] + 28), "招聘", font=load_font(22), fill=(25, 32, 41, 255))
    draw.ellipse((box[2] - 62, box[1] + 29, box[2] - 44, box[1] + 47), fill=(28, 176, 116, 255))

    card = (box[0] + 22, box[1] + 82, box[2] - 22, box[1] + 250)
    draw.rounded_rectangle(card, radius=20, fill=(255, 255, 255, 255))
    draw.text((card[0] + 22, card[1] + 18), fit_text(action.get("title", "刷到薪资还行的岗位"), 13), font=load_font(26), fill=(20, 28, 38, 255))
    draw.text((card[0] + 22, card[1] + 58), str(action.get("salary", "薪资还行")), font=load_font(30), fill=(228, 71, 45, 255))
    draw.text((card[0] + 22, card[1] + 100), str(action.get("company", "校招热岗")), font=load_font(18), fill=(84, 96, 112, 255))
    tags = action.get("tags") if isinstance(action.get("tags"), list) else ["双休", "经验不限", "立即沟通"]
    x = card[0] + 22
    for tag in tags[:3]:
        width = 22 + min(82, len(str(tag)) * 17)
        if x + width > card[2] - 12:
            width = card[2] - 12 - x
        if width < 52:
            break
        draw.rounded_rectangle((x, card[1] + 128, x + width, card[1] + 158), radius=14, fill=(236, 244, 255, 255))
        draw.text((x + 11, card[1] + 133), fit_text(tag, max(2, int((width - 18) / 16))), font=load_font(15), fill=(36, 93, 184, 255))
        x += width + 8
    draw.rounded_rectangle((box[0] + 62, box[1] + 276, box[2] - 62, box[1] + 324), radius=22, fill=(28, 176, 116, 255))
    draw_text_center(draw, ((box[0] + box[2]) // 2, box[1] + 300), "立即投递", load_font(24), (255, 255, 255, 255), stroke=1)


def draw_job_requirement_card(draw: ImageDraw.ImageDraw, progress: float, action: dict) -> None:
    y = panel_progress_y(104, progress, 54)
    box = (560, y, 902, y + 270)
    draw_card_shadow(draw, box, 20)
    draw.rounded_rectangle(box, radius=20, fill=(255, 253, 244, 245), outline=(35, 47, 61, 230), width=3)
    draw.text((box[0] + 28, box[1] + 24), fit_text(action.get("title", "岗位要求"), 10), font=load_font(30), fill=(30, 39, 50, 255))
    draw.line((box[0] + 28, box[1] + 68, box[2] - 28, box[1] + 68), fill=(40, 54, 70, 120), width=2)
    items = action.get("items") if isinstance(action.get("items"), list) else ["经验不限但要满级", "能抗压", "会很多"]
    for index, item in enumerate(items[:4]):
        yy = box[1] + 92 + index * 40
        draw.ellipse((box[0] + 32, yy + 4, box[0] + 48, yy + 20), fill=(236, 70, 70, 245))
        draw.text((box[0] + 62, yy), fit_text(item, 13), font=load_font(23), fill=(36, 44, 54, 255))


def draw_message_stack(draw: ImageDraw.ImageDraw, progress: float, action: dict, palette: str = "work") -> None:
    y = panel_progress_y(92, progress, 48)
    box = (566, y, 900, y + 282)
    bg = (238, 248, 255, 244) if palette == "work" else (246, 250, 255, 244)
    accent = (47, 129, 247, 255) if palette == "work" else (34, 168, 116, 255)
    draw_card_shadow(draw, box, 24)
    draw.rounded_rectangle(box, radius=24, fill=bg, outline=(255, 255, 255, 230), width=3)
    draw.rounded_rectangle((box[0], box[1], box[2], box[1] + 58), radius=24, fill=accent)
    draw.text((box[0] + 24, box[1] + 16), fit_text(action.get("title", "工作群"), 11), font=load_font(24), fill=(255, 255, 255, 255))
    messages = action.get("messages") if isinstance(action.get("messages"), list) else ["老板：在吗", "再同步一次", "今晚辛苦下"]
    for index, message in enumerate(messages[:3]):
        yy = box[1] + 82 + index * 58
        bubble = (box[0] + 24, yy, box[2] - 26 - index * 18, yy + 42)
        draw.rounded_rectangle(bubble, radius=18, fill=(255, 255, 255, 250))
        draw.text((bubble[0] + 16, bubble[1] + 9), fit_text(message, 14), font=load_font(21), fill=(35, 45, 58, 255))


def draw_choice_panel(draw: ImageDraw.ImageDraw, progress: float, action: dict) -> None:
    y = panel_progress_y(96, progress, 48)
    box = (560, y, 900, y + 264)
    draw_card_shadow(draw, box, 22)
    draw.rounded_rectangle(box, radius=22, fill=(247, 255, 248, 244), outline=(42, 157, 86, 240), width=4)
    draw.text((box[0] + 26, box[1] + 22), fit_text(action.get("title", "请选择今天焦虑"), 13), font=load_font(27), fill=(25, 75, 46, 255))
    options = action.get("options") if isinstance(action.get("options"), list) else ["考研", "考公", "就业"]
    for index, option in enumerate(options[:3]):
        yy = box[1] + 76 + index * 54
        draw.rounded_rectangle((box[0] + 28, yy, box[2] - 28, yy + 42), radius=18, fill=(255, 255, 255, 255), outline=(64, 178, 105, 160), width=2)
        draw.text((box[0] + 48, yy + 8), fit_text(option, 12), font=load_font(23), fill=(31, 91, 54, 255))


def draw_bill_card(draw: ImageDraw.ImageDraw, progress: float, action: dict) -> None:
    y = panel_progress_y(102, progress, 48)
    box = (576, y, 890, y + 256)
    draw_card_shadow(draw, box, 18)
    draw.rounded_rectangle(box, radius=18, fill=(255, 248, 239, 246), outline=(225, 118, 48, 245), width=3)
    draw.text((box[0] + 28, box[1] + 22), fit_text(action.get("title", "现实账单"), 12), font=load_font(29), fill=(111, 58, 23, 255))
    items = action.get("items") if isinstance(action.get("items"), list) else ["房租", "通勤", "押金"]
    for index, item in enumerate(items[:3]):
        yy = box[1] + 78 + index * 44
        draw.text((box[0] + 30, yy), fit_text(item, 8), font=load_font(23), fill=(79, 54, 36, 255))
        draw.text((box[2] - 106, yy), f"-{(index + 1) * 800}", font=load_font(23), fill=(217, 75, 37, 255))
    draw.line((box[0] + 28, box[3] - 54, box[2] - 28, box[3] - 54), fill=(150, 92, 52, 80), width=2)


def draw_stall_sign(draw: ImageDraw.ImageDraw, progress: float, action: dict) -> None:
    y = panel_progress_y(92, progress, 50)
    box = (562, y, 904, y + 230)
    draw_card_shadow(draw, box, 18)
    draw.rounded_rectangle(box, radius=18, fill=(255, 244, 221, 248), outline=(184, 69, 32, 245), width=4)
    draw_text_center(draw, ((box[0] + box[2]) // 2, box[1] + 42), str(action.get("title", "校门口小摊")), load_font(30), (126, 44, 22, 255), stroke=1)
    items = action.get("items") if isinstance(action.get("items"), list) else ["烤肠 3元", "加料 +1", "今日也内卷"]
    for index, item in enumerate(items[:3]):
        yy = box[1] + 84 + index * 38
        draw.rounded_rectangle((box[0] + 46, yy, box[2] - 46, yy + 30), radius=12, fill=(255, 255, 255, 210))
        draw_text_center(draw, ((box[0] + box[2]) // 2, yy + 15), fit_text(item, 12), load_font(20), (95, 42, 20, 255), stroke=1)


def draw_action(draw: ImageDraw.ImageDraw, action: dict, local_t: float) -> None:
    start = float(action.get("start", 0))
    duration = max(0.1, float(action.get("duration", 1)))
    if local_t < start or local_t > start + duration:
        return
    progress = max(0, min(1, (local_t - start) / duration))
    kind = action.get("type")
    text = str(action.get("text", ""))
    if kind == "throw_object":
        eased = ease_out(progress)
        x = 190 + (610 - 190) * eased
        y = 332 - math.sin(progress * math.pi) * 120
        draw_resume(draw, x, y, text or "简历", progress * math.tau)
    elif kind == "stamp_reject":
        draw_stamp(draw, progress, text or "已读不回")
    elif kind == "popup":
        draw_popup(draw, progress, text or "新通知")
    elif kind == "impact_burst":
        draw_burst(draw, progress, text or "离谱")
    elif kind == "phone_job_feed":
        draw_phone_job_feed(draw, progress, action)
    elif kind == "job_requirement_card":
        draw_job_requirement_card(draw, progress, action)
    elif kind == "chat_stack":
        draw_message_stack(draw, progress, action, palette="chat")
    elif kind == "work_chat_stack":
        draw_message_stack(draw, progress, action, palette="work")
    elif kind == "choice_panel":
        draw_choice_panel(draw, progress, action)
    elif kind == "bill_card":
        draw_bill_card(draw, progress, action)
    elif kind == "stall_sign":
        draw_stall_sign(draw, progress, action)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--actions", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--width", type=int, default=960)
    parser.add_argument("--height", type=int, default=544)
    args = parser.parse_args()

    actions = json.loads(args.actions or "[]")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    frame_count = max(1, int(args.duration * args.fps))
    for frame in range(frame_count):
        image = Image.new("RGBA", (args.width, args.height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        local_t = frame / args.fps
        for action in actions:
            draw_action(draw, action, local_t)
        image.save(out_dir / f"{frame + 1:04d}.png")


if __name__ == "__main__":
    main()
