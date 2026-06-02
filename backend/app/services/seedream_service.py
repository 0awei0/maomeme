from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.request
from pathlib import Path

from ..core.config import get_settings
from .doubao_client import ark_available, get_ark_client


def seedream_available() -> bool:
    return ark_available() and bool(get_settings().SEEDREAM_MODEL)


def generate_background(prompt: str, description: str = "", slug: str = "agent-fill") -> dict[str, str]:
    if not seedream_available():
        raise RuntimeError("Seedream is not configured")

    settings = get_settings()
    safe_slug = slugify(slug)
    timestamp = int(time.time())
    out_dir = settings.PROJECT_ROOT / "assets" / "generated" / "backgrounds" / safe_slug
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{timestamp}.png"

    response = get_ark_client().images.generate(
        model=settings.SEEDREAM_MODEL,
        prompt=prompt,
        size="1440x2560",
        response_format="url",
        output_format="png",
        watermark=False,
    )
    download_url(response.data[0].url, out_file)

    desc_file = out_dir / "descriptions.json"
    descriptions = []
    if desc_file.exists():
        try:
            descriptions = json.loads(desc_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            descriptions = []
    descriptions.append({"file": out_file.name, "description": description or prompt})
    desc_file.write_text(json.dumps(descriptions, ensure_ascii=False, indent=2), encoding="utf-8")
    refresh_assets_index(settings.PROJECT_ROOT)

    return {
        "file": str(out_file.relative_to(settings.PROJECT_ROOT)),
        "description": description or prompt,
    }


def refresh_assets_index(project_root: Path) -> None:
    subprocess.run(
        ["node", str(project_root / "scripts" / "index-assets.mjs")],
        cwd=str(project_root),
        check=True,
        capture_output=True,
        text=True,
    )


def download_url(url: str, out_file: Path, attempts: int = 3) -> None:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "MaoMeme/1.0"})
            with urllib.request.urlopen(request, timeout=90) as response:
                out_file.write_bytes(response.read())
            return
        except Exception as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(1.5 * attempt)
    raise RuntimeError(f"Seedream image download failed: {safe_error(last_error)}")


def slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff_-]+", "-", value).strip("-")
    return value[:48] or "agent-fill"


def safe_error(exc: Exception | None) -> str:
    text = str(exc or "")
    text = re.sub(r"https?://\S+", "[url]", text)
    text = re.sub(r"(api[_-]?key|authorization|bearer)\S*", "[secret]", text, flags=re.I)
    return text[:180]
