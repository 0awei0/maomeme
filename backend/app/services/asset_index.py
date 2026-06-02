from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core.config import get_settings


def load_assets() -> dict[str, Any]:
    path = get_settings().ASSETS_INDEX
    if not path.exists():
        raise FileNotFoundError(f"素材索引不存在，请先运行 npm run index:assets: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def assets_summary(index: dict[str, Any], max_items: int = 80) -> str:
    motions = index.get("cat_motions", [])[:max_items]
    backgrounds = index.get("backgrounds", [])[:max_items]
    lines = [
        f"猫动画素材: {len(index.get('cat_motions', []))} 个",
        f"背景素材: {len(index.get('backgrounds', []))} 张",
        "",
        "### 猫动画",
    ]
    for item in motions:
        lines.append(f"- {item.get('id')}: {item.get('file')}｜{item.get('description')}")
    lines.append("")
    lines.append("### 背景")
    for item in backgrounds:
        lines.append(f"- {item.get('id')}: {item.get('file')}｜{item.get('description')}")
    return "\n".join(lines)


def pick_motion(index: dict[str, Any], keywords: list[str], fallback_id: str = "1") -> dict[str, Any]:
    motions = index.get("cat_motions", [])
    ranked = rank_assets(motions, keywords)
    if ranked:
        return ranked[0]
    for asset in motions:
        if str(asset.get("id")) == fallback_id:
            return asset
    return motions[0] if motions else {}


def pick_background(index: dict[str, Any], scenes: list[str]) -> dict[str, Any]:
    backgrounds = index.get("backgrounds", [])
    for scene in scenes:
        for asset in backgrounds:
            if str(asset.get("scene", "")).startswith(scene):
                return asset
    return backgrounds[0] if backgrounds else {}


def rank_assets(assets: list[dict[str, Any]], keywords: list[str], limit: int = 8) -> list[dict[str, Any]]:
    scored: list[tuple[float, dict[str, Any]]] = []
    for asset in assets:
        text = f"{asset.get('id', '')} {asset.get('scene', '')} {asset.get('file', '')} {asset.get('description', '')}"
        score = 0.0
        for keyword in keywords:
            if keyword and keyword in text:
                score += 1.0
        if score:
            scored.append((score, asset))
    scored.sort(key=lambda item: (-item[0], str(item[1].get("id", ""))))
    return [asset for _, asset in scored[:limit]]


def ref(asset: dict[str, Any]) -> dict[str, str]:
    return {
        "id": str(asset.get("id", "")),
        "file": str(asset.get("file", "")),
        "description": str(asset.get("description", "")),
    }
