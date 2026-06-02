from __future__ import annotations

import argparse
import json
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


def text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, stroke_width: int = 0) -> tuple[int, int]:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
    return right - left, bottom - top


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    lines: list[str] = []
    current = ""
    for char in text:
        trial = current + char
        if text_size(draw, trial, font, stroke_width=2)[0] <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = char
    if current:
        lines.append(current)
    return lines


def fit_wrapped_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    max_lines: int,
    initial_size: int,
    min_size: int = 24,
) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    size = initial_size
    while size >= min_size:
        font = load_font(size)
        lines = wrap_text(draw, text, font, max_width)
        if len(lines) <= max_lines:
            return font, lines
        size -= 2
    font = load_font(min_size)
    lines = wrap_text(draw, text, font, max_width)[:max_lines]
    if lines and len(wrap_text(draw, text, font, max_width)) > max_lines:
        lines[-1] = lines[-1][: max(1, len(lines[-1]) - 1)] + "…"
    return font, lines


def rounded_box(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], fill: tuple[int, int, int, int]) -> None:
    draw.rounded_rectangle(box, radius=16, fill=fill)


def draw_multiline_center(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    center_y: int,
    width: int,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int, int],
    stroke_width: int = 4,
) -> None:
    line_h = text_size(draw, "猫", font, stroke_width=stroke_width)[1] + 8
    top = center_y - (line_h * len(lines)) // 2
    for index, line in enumerate(lines):
        line_w, _ = text_size(draw, line, font, stroke_width=stroke_width)
        draw.text(
            ((width - line_w) // 2, top + index * line_h),
            line,
            font=font,
            fill=fill,
            stroke_width=stroke_width,
            stroke_fill=(0, 0, 0, 230),
        )


def draw_bubble(
    draw: ImageDraw.ImageDraw,
    text: str,
    box: tuple[int, int, int, int],
    align: str,
) -> None:
    x1, y1, x2, y2 = box
    rounded_box(draw, box, (255, 255, 255, 232))
    pointer = [(x1 + 62, y2), (x1 + 92, y2), (x1 + 72, y2 + 20)] if align == "left" else [(x2 - 62, y2), (x2 - 92, y2), (x2 - 72, y2 + 20)]
    draw.polygon(pointer, fill=(255, 255, 255, 232))
    font, lines = fit_wrapped_font(draw, text, x2 - x1 - 36, 2, 28, min_size=20)
    line_h = text_size(draw, "猫", font)[1] + 7
    top = y1 + ((y2 - y1) - line_h * len(lines)) // 2
    for index, line in enumerate(lines):
        draw.text((x1 + 18, top + index * line_h), line, font=font, fill=(18, 24, 29, 255))


def draw_bottom_caption(draw: ImageDraw.ImageDraw, text: str, width: int, height: int) -> None:
    font, lines = fit_wrapped_font(draw, text, width - 150, 2, 32, min_size=22)
    line_h = text_size(draw, "猫", font)[1] + 8
    box_h = max(64, line_h * len(lines) + 24)
    box = (54, height - box_h - 28, width - 54, height - 28)
    rounded_box(draw, box, (255, 255, 255, 218))
    top = box[1] + (box_h - line_h * len(lines)) // 2
    for index, line in enumerate(lines):
        line_w, _ = text_size(draw, line, font, stroke_width=1)
        draw.text(
            ((width - line_w) // 2, top + index * line_h),
            line,
            font=font,
            fill=(18, 24, 29, 255),
            stroke_width=1,
            stroke_fill=(255, 255, 255, 255),
        )


def parse_dialogue(raw: str) -> list[dict[str, str]]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", required=True)
    parser.add_argument("--subtitle", default="")
    parser.add_argument("--show-subtitle", default="true")
    parser.add_argument("--role", default="")
    parser.add_argument("--layout", default="single")
    parser.add_argument("--dialogue", default="")
    parser.add_argument("--out", required=True)
    parser.add_argument("--width", type=int, default=960)
    parser.add_argument("--height", type=int, default=544)
    args = parser.parse_args()

    image = Image.new("RGBA", (args.width, args.height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    title = args.title.strip()
    subtitle = args.subtitle.strip()
    dialogue = parse_dialogue(args.dialogue)
    if args.layout == "dialogue" and len(dialogue) >= 2:
        label_font, label_lines = fit_wrapped_font(draw, title, args.width - 180, 1, 30, min_size=22)
        title_box = (90, 28, args.width - 90, 84)
        rounded_box(draw, title_box, (0, 0, 0, 118))
        draw_multiline_center(draw, label_lines, 54, args.width, label_font, (255, 255, 255, 245), stroke_width=3)
        draw_bubble(draw, dialogue[0].get("text", ""), (48, 140, 418, 218), "left")
        draw_bubble(draw, dialogue[1].get("text", ""), (542, 140, 912, 218), "right")
    else:
        title_font, title_lines = fit_wrapped_font(draw, title, args.width - 120, 2, 44, min_size=26)
        title_box = (44, 24, args.width - 44, 122)
        rounded_box(draw, title_box, (0, 0, 0, 150))
        draw_multiline_center(draw, title_lines, 72, args.width, title_font, (255, 255, 255, 255), stroke_width=4)
        if args.show_subtitle.lower() != "false" and subtitle and subtitle != title:
            draw_bottom_caption(draw, subtitle, args.width, args.height)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    image.save(args.out)


if __name__ == "__main__":
    main()
