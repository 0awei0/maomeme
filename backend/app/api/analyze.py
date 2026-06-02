from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from ..models.maomeme import AnalyzeVideoRequest
from ..services.video_analyzer import analyze_video_structure

router = APIRouter(prefix="/api/analyze", tags=["analyze"])


@router.post("/structure")
async def analyze_structure(request: AnalyzeVideoRequest):
    path = Path(request.video_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"视频不存在: {request.video_path}")
    try:
        structure = await analyze_video_structure(str(path), use_doubao=request.use_doubao)
        return JSONResponse({"status": "success", "structure": structure.model_dump()})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"分析失败: {exc}") from exc
