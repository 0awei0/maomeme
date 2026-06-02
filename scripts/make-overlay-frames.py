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
