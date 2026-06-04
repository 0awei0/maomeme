from __future__ import annotations

from pathlib import Path
import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from ..models.maomeme import AnalyzeVideoRequest, ViralAnalysisJobRequest
from ..services.video_analyzer import analyze_video_structure
from ..services.upload_store import create_viral_analysis_job, get_viral_analysis_job

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


@router.post("/viral-jobs")
async def create_viral_job(request: ViralAnalysisJobRequest):
    try:
        job = create_viral_analysis_job(
            session_id=request.session_id,
            upload_id=request.upload_id,
            use_doubao=request.use_doubao,
            creative_brief=request.creative_brief,
        )
        return JSONResponse({"status": "success", "job": job.model_dump()})
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"创建爆款分析任务失败: {exc}") from exc


@router.get("/viral-jobs/{job_id}")
async def viral_job_status(job_id: str):
    job = get_viral_analysis_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="爆款分析任务不存在")
    return JSONResponse({"status": "success", "job": job.model_dump()})


@router.get("/viral-jobs/{job_id}/stream")
async def viral_job_stream(job_id: str):
    async def event_stream():
        last = ""
        while True:
            job = get_viral_analysis_job(job_id)
            if not job:
                yield sse({"type": "error", "message": "爆款分析任务不存在", "progress": 1.0})
                return
            payload = {
                "type": "done" if job.status in {"done", "error"} else "stage",
                "message": job.message,
                "progress": job.progress,
                "job": job.model_dump(),
            }
            encoded = json.dumps(payload, ensure_ascii=False)
            if encoded != last:
                yield f"data: {encoded}\n\n"
                last = encoded
            if job.status in {"done", "error"}:
                return
            await asyncio.sleep(0.7)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
