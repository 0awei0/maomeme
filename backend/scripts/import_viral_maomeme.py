from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = Path("/Users/a1-6/Desktop/code/douyin/videos/baokuan-maomeme")
LIBRARY_DIR = ROOT / "samples" / "viral-structure" / "baokuan-maomeme"
RAW_DIR = LIBRARY_DIR / "raw"
MANIFEST_PATH = LIBRARY_DIR / "manifest.json"
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm"}


def run_ffprobe(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def video_meta(path: Path) -> dict[str, Any]:
    info = run_ffprobe(path)
    video_stream = next((item for item in info.get("streams", []) if item.get("codec_type") == "video"), {}) or {}
    audio_stream = next((item for item in info.get("streams", []) if item.get("codec_type") == "audio"), {}) or {}
    fmt = info.get("format", {}) or {}
    fps_raw = str(video_stream.get("r_frame_rate") or "30/1")
    try:
        numerator, denominator = [int(part) for part in fps_raw.split("/", 1)]
        fps = round(numerator / denominator, 3) if denominator else 30.0
    except Exception:
        fps = 30.0
    return {
        "duration": round(float(fmt.get("duration", 0) or 0), 3),
        "size_bytes": int(fmt.get("size", path.stat().st_size) or path.stat().st_size),
        "width": int(video_stream.get("width", 0) or 0),
        "height": int(video_stream.get("height", 0) or 0),
        "fps": fps,
        "video_codec": str(video_stream.get("codec_name", "")),
        "audio_codec": str(audio_stream.get("codec_name", "")),
        "audio_sample_rate": str(audio_stream.get("sample_rate", "")),
        "audio_channels": int(audio_stream.get("channels", 0) or 0),
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_id(index: int, path: Path) -> str:
    return f"bkmm-{index:03d}-{path.stem}"


def build_manifest(source_dir: Path, dry_run: bool, force: bool) -> dict[str, Any]:
    if not source_dir.exists():
        raise FileNotFoundError(f"source directory not found: {source_dir}")

    videos = sorted(path for path in source_dir.iterdir() if path.is_file() and path.suffix.lower() in VIDEO_EXTS)
    items: list[dict[str, Any]] = []
    if not dry_run:
        RAW_DIR.mkdir(parents=True, exist_ok=True)

    for index, source in enumerate(videos, start=1):
        video_id = stable_id(index, source)
        target = RAW_DIR / source.name
        if not dry_run and (force or not target.exists()):
            shutil.copy2(source, target)
        inspect_path = source if dry_run else target
        meta = video_meta(inspect_path)
        items.append(
            {
                "id": video_id,
                "filename": source.name,
                "source_path": str(source),
                "local_path": str(target.relative_to(ROOT)),
                "analysis_dir": str((ROOT / "data" / "viral-structures" / "baokuan-maomeme" / video_id).relative_to(ROOT)),
                "sha256": sha256_file(inspect_path),
                "analysis_status": "pending",
                **meta,
            }
        )

    manifest = {
        "library": "baokuan-maomeme",
        "source_dir": str(source_dir),
        "raw_dir": str(RAW_DIR.relative_to(ROOT)),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(items),
        "items": items,
    }
    return manifest


def write_manifest(manifest: dict[str, Any]) -> None:
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import viral MaoMeme reference videos into the local structure library.")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE), help="Source directory containing viral cat meme videos.")
    parser.add_argument("--dry-run", action="store_true", help="Inspect videos and print manifest summary without copying or writing.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing local raw videos.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = build_manifest(Path(args.source).expanduser(), dry_run=args.dry_run, force=args.force)
    if args.dry_run:
        print(json.dumps({"total": manifest["total"], "raw_dir": manifest["raw_dir"]}, ensure_ascii=False, indent=2))
        for item in manifest["items"]:
            print(f"{item['id']} {item['duration']:.2f}s {item['size_bytes'] / 1024 / 1024:.2f}MB {item['filename']}")
        return 0

    write_manifest(manifest)
    print(f"Imported {manifest['total']} videos into {RAW_DIR}")
    print(f"Wrote manifest: {MANIFEST_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
