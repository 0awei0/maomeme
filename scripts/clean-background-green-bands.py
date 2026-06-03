from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGETS = [
    ROOT / "assets" / "backgrounds",
    ROOT / "assets" / "generated" / "backgrounds",
]
REPORT_PATH = ROOT / "data" / "runs" / "background-green-cleanup.json"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def is_green_screen_pixel(r: int, g: int, b: int) -> bool:
    return g > 135 and g > r * 1.45 and g > b * 1.25 and (g - max(r, b)) > 45


def bottom_green_rows(image: Image.Image) -> int:
    rgba = image.convert("RGBA")
    width, height = rgba.size
    pixels = rgba.load()
    rows = 0

    sample_step = max(1, width // 260)
    for y in range(height - 1, -1, -1):
        sampled = 0
        green = 0
        for x in range(0, width, sample_step):
            r, g, b, a = pixels[x, y]
            if a < 8:
                continue
            sampled += 1
            if is_green_screen_pixel(r, g, b):
                green += 1
        ratio = green / max(1, sampled)
        if ratio >= 0.58:
            rows += 1
        else:
            break
    return rows


def image_paths(targets: list[Path]) -> list[Path]:
    files: list[Path] = []
    for target in targets:
        if target.is_file() and target.suffix.lower() in IMAGE_EXTENSIONS:
            files.append(target)
        elif target.is_dir():
            files.extend(
                item
                for item in target.rglob("*")
                if item.is_file() and item.suffix.lower() in IMAGE_EXTENSIONS
            )
    return sorted(files)


def clean_image(path: Path, dry_run: bool) -> dict[str, Any]:
    try:
        with Image.open(path) as image:
            width, height = image.size
            if width < 16 or height < 16:
                return report_item(path, changed=False, reason="invalid image dimensions")

            green_rows = bottom_green_rows(image)
            min_rows = max(18, round(height * 0.04))
            max_rows = round(height * 0.55)
            if green_rows < min_rows:
                return report_item(path, changed=False)
            if green_rows > max_rows:
                return report_item(path, changed=False, reason=f"green band too tall ({green_rows}px), skipped")

            new_height = height - green_rows
            if new_height < round(height * 0.45):
                return report_item(path, changed=False, reason=f"crop would leave only {new_height}px, skipped")

            if not dry_run:
                cropped = image.crop((0, 0, width, new_height))
                save_image(cropped, path)

            return report_item(
                path,
                changed=True,
                original={"width": width, "height": height},
                cropped={"width": width, "height": new_height},
                removed_bottom_rows=green_rows,
            )
    except Exception as exc:
        return report_item(path, changed=False, reason=f"read failed: {type(exc).__name__}")


def save_image(image: Image.Image, path: Path) -> None:
    suffix = path.suffix.lower()
    path.parent.mkdir(parents=True, exist_ok=True)
    if suffix in {".jpg", ".jpeg"}:
        image.convert("RGB").save(path, quality=94, optimize=True)
    elif suffix == ".webp":
        image.save(path, quality=94, method=6)
    else:
        image.save(path)


def report_item(path: Path, changed: bool, **extra: Any) -> dict[str, Any]:
    return {
        "file": str(path.relative_to(ROOT)),
        "changed": changed,
        **extra,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crop obvious green-screen bands from background images.")
    parser.add_argument("--dry-run", action="store_true", help="Scan only; do not modify images.")
    parser.add_argument(
        "--target",
        action="append",
        type=Path,
        help="Image file or directory to scan. Can be passed multiple times.",
    )
    parser.add_argument("--report", type=Path, default=REPORT_PATH, help="JSON report output path.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    targets = [target if target.is_absolute() else ROOT / target for target in args.target] if args.target else DEFAULT_TARGETS
    files = image_paths(targets)
    items = [clean_image(path, args.dry_run) for path in files]
    changed = [item for item in items if item.get("changed")]
    reported = [item for item in items if item.get("changed") or item.get("reason")]

    report_path = args.report if args.report.is_absolute() else ROOT / args.report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": args.dry_run,
        "scanned": len(files),
        "changed": len(changed),
        "items": reported,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "scanned": len(files),
                "changed": len(changed),
                "reported": len(reported),
                "report": str(report_path.relative_to(ROOT)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
