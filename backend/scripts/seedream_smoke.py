from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.services.doubao_client import get_ark_client


def main() -> None:
    settings = get_settings()
    if not settings.ARK_API_KEY:
        raise SystemExit("ARK_API_KEY is not configured")

    out_dir = settings.PROJECT_ROOT / "assets" / "generated" / "backgrounds" / "seedream-smoke"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "1.png"

    prompt = (
        "竖屏短视频背景，一间明亮但略显拥挤的现代办公室，电脑、工位、窗外城市，"
        "高级猫 meme 社会现实短剧背景，无人物，无文字，动漫写实融合风格，"
        "画面下方保持自然地面或桌面无遮挡，不要绿色幕布或纯色块"
    )
    response = get_ark_client().images.generate(
        model=settings.SEEDREAM_MODEL,
        prompt=prompt,
        size="1440x2560",
        response_format="url",
        output_format="png",
        watermark=False,
    )
    url = response.data[0].url
    urllib.request.urlretrieve(url, out_file)

    description = [
        {
            "file": out_file.name,
            "description": "Seedream 生成的现代办公室背景，适合上班、内卷、会议、求职等猫 meme 对话场景。",
        }
    ]
    (out_dir / "descriptions.json").write_text(json.dumps(description, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "ok", "file": str(out_file), "bytes": out_file.stat().st_size}, ensure_ascii=False))


if __name__ == "__main__":
    main()
