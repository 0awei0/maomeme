from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
CANVAS_WIDTH = 960
CANVAS_HEIGHT = 544


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="assets/cat-motions")
    parser.add_argument("--output", default="data/cat-layout-index.json")
    parser.add_argument("--overrides", default="data/cat-layout-overrides.json")
    parser.add_argument("--sample-count", type=int, default=5)
    args = parser.parse_args()

    source_dir = (ROOT / args.source).resolve()
    output_path = (ROOT / args.output).resolve()
    overrides = load_json((ROOT / args.overrides).resolve(), {})
    descriptions = load_json(source_dir / "descriptions.json", {})
    layouts: dict[str, dict[str, Any]] = {}

    for video in sorted(source_dir.glob("*.mp4"), key=lambda item: item.stem.zfill(8)):
        description, metadata = normalize_description_entry(descriptions.get(video.stem, ""))
        layout = analyze_video(video, description, metadata, overrides.get(video.stem, {}), args.sample_count)
        if layout:
            layouts[video.stem] = layout

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "source_dir": relative(source_dir),
                "layouts": layouts,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"scanned": len(list(source_dir.glob("*.mp4"))), "layouts": len(layouts), "output": relative(output_path)}, ensure_ascii=False))


def normalize_description_entry(entry: Any) -> tuple[str, str]:
    if isinstance(entry, dict):
        description = str(entry.get("description") or "").strip()
        metadata = " ".join(flatten_metadata(entry.get("motion_tags", {})))
        return description, f"{description} {metadata}".strip()
    description = str(entry or "").strip()
    return description, description


def flatten_metadata(value: Any) -> list[str]:
    if isinstance(value, dict):
        values: list[str] = []
        for item in value.values():
            values.extend(flatten_metadata(item))
        return values
    if isinstance(value, list):
        values: list[str] = []
        for item in value:
            values.extend(flatten_metadata(item))
        return values
    text = str(value or "").strip()
    return [text] if text else []


def analyze_video(video: Path, description: str, metadata: str, override: dict[str, Any], sample_count: int) -> dict[str, Any]:
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        return {}
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if frame_count <= 0:
        capture.release()
        return {}

    boxes = []
    source_size = None
    for frame_index in sample_indices(frame_count, sample_count):
        capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame = capture.read()
        if not ok or frame is None:
            continue
        source_size = (int(frame.shape[1]), int(frame.shape[0]))
        box = foreground_box(frame)
        if box:
            boxes.append(box)
    capture.release()
    if not boxes or not source_size:
        return {}

    body_source = union_boxes(boxes)
    quality_text = f"{description} {metadata}"
    needs_crop = any(word in quality_text for word in ("黑边", "白底", "需要裁切", "需裁切", "低清", "模糊", "needs_crop", "low_quality"))
    body = transform_box(body_source, source_size, needs_crop)
    if not body:
        return {}
    head = infer_head_box(body)
    face_direction = str(override.get("face_direction") or infer_face_direction(description)).strip().lower()
    if face_direction not in {"left", "right", "center"}:
        face_direction = "center"

    layout = {
        "body_box": round_box(body),
        "head_box": round_box(head),
        "face_direction": face_direction,
        "source_body_box": round_box(body_source),
        "render_profile": "cropped" if needs_crop else "default",
    }
    if isinstance(override.get("head_box"), dict):
        layout["head_box"] = round_box(safe_box(override["head_box"]) or layout["head_box"])
    if isinstance(override.get("body_box"), dict):
        layout["body_box"] = round_box(safe_box(override["body_box"]) or layout["body_box"])
    return layout


def foreground_box(frame: np.ndarray) -> dict[str, float]:
    b, g, r = cv2.split(frame)
    green = (g > 90) & (g > r * 1.25) & (g > b * 1.25)
    mask = (~green).astype(np.uint8) * 255
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    height, width = mask.shape
    boxes = []
    for label in range(1, count):
        x, y, w, h, area = stats[label]
        if area < 180:
            continue
        touches_edge = x <= 2 or y <= 2 or x + w >= width - 2 or y + h >= height - 2
        if touches_edge and (w > width * 0.85 or h > height * 0.85):
            continue
        boxes.append({"x": float(x), "y": float(y), "w": float(w), "h": float(h)})
    if not boxes:
        ys, xs = np.where(mask > 0)
        if len(xs) == 0:
            return {}
        return {"x": float(xs.min()), "y": float(ys.min()), "w": float(xs.max() - xs.min() + 1), "h": float(ys.max() - ys.min() + 1)}
    return union_boxes(boxes)


def transform_box(box: dict[str, float], source_size: tuple[int, int], needs_crop: bool) -> dict[str, float]:
    width, height = source_size
    if needs_crop:
        crop_x = width * 0.19
        crop_y = height * 0.08
        crop_w = width * 0.62
        crop_h = height * 0.76
        target_w = 310.0
    else:
        crop_x = width * 0.25
        crop_y = 0.0
        crop_w = width * 0.5
        crop_h = max(1.0, height - 36.0)
        target_w = 360.0
    clipped = intersect_box(box, {"x": crop_x, "y": crop_y, "w": crop_w, "h": crop_h})
    if not clipped:
        return {}
    scale = target_w / crop_w
    target_h = crop_h * scale
    offset_x = (CANVAS_WIDTH - target_w) / 2
    offset_y = CANVAS_HEIGHT - target_h - 58
    return {
        "x": offset_x + (clipped["x"] - crop_x) * scale,
        "y": offset_y + (clipped["y"] - crop_y) * scale,
        "w": clipped["w"] * scale,
        "h": clipped["h"] * scale,
    }


def infer_head_box(body: dict[str, float]) -> dict[str, float]:
    return {
        "x": body["x"] + body["w"] * 0.18,
        "y": body["y"] + body["h"] * 0.08,
        "w": body["w"] * 0.64,
        "h": body["h"] * 0.42,
    }


def infer_face_direction(description: str) -> str:
    if any(word in description for word in ("正面", "对镜头", "瞪眼", "张嘴")):
        return "center"
    if any(word in description for word in ("左侧", "左边", "向左", "看左")):
        return "left"
    if any(word in description for word in ("右侧", "右边", "向右", "看右")):
        return "right"
    return "center"


def sample_indices(frame_count: int, sample_count: int) -> list[int]:
    sample_count = max(1, min(sample_count, frame_count))
    if sample_count == 1:
        return [frame_count // 2]
    return sorted({round((frame_count - 1) * index / (sample_count - 1)) for index in range(sample_count)})


def union_boxes(boxes: list[dict[str, float]]) -> dict[str, float]:
    left = min(box["x"] for box in boxes)
    top = min(box["y"] for box in boxes)
    right = max(box["x"] + box["w"] for box in boxes)
    bottom = max(box["y"] + box["h"] for box in boxes)
    return {"x": left, "y": top, "w": right - left, "h": bottom - top}


def intersect_box(box: dict[str, float], clip: dict[str, float]) -> dict[str, float]:
    left = max(box["x"], clip["x"])
    top = max(box["y"], clip["y"])
    right = min(box["x"] + box["w"], clip["x"] + clip["w"])
    bottom = min(box["y"] + box["h"], clip["y"] + clip["h"])
    if right <= left or bottom <= top:
        return {}
    return {"x": left, "y": top, "w": right - left, "h": bottom - top}


def safe_box(value: dict[str, Any]) -> dict[str, float]:
    try:
        x = float(value["x"])
        y = float(value["y"])
        w = float(value.get("w", value.get("width")))
        h = float(value.get("h", value.get("height")))
    except (KeyError, TypeError, ValueError):
        return {}
    if w <= 0 or h <= 0:
        return {}
    return {"x": x, "y": y, "w": w, "h": h}


def round_box(box: dict[str, float]) -> dict[str, int]:
    return {key: int(round(float(box[key]))) for key in ("x", "y", "w", "h")}


def load_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return fallback


def relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    main()
