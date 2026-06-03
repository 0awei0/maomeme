from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from ..models.maomeme import (
    CandidateRequest,
    GeneratePlanRequest,
    GenerateBackgroundRequest,
    RenderJobRequest,
    RevisePlanRequest,
    SelectPlanRequest,
    SuggestRevisionRequest,
    ScriptCandidate,
)
from ..services.asset_index import load_assets
from ..services.maomeme_agent import (
    generate_maomeme_plan,
    generate_script_candidates,
    plan_from_candidate,
    revise_maomeme,
    stream_script_candidates,
    storyboard_stream_from_candidate,
    suggest_revisions,
)
from ..services.doubao_client import ark_available
from ..services.render_jobs import create_render_job, get_render_job
from ..services.seedream_service import generate_background, seedream_available
from ..core.config import get_settings

router = APIRouter(prefix="/api/maomeme", tags=["maomeme"])


@router.get("/assets")
async def assets():
    try:
        return JSONResponse({"status": "success", "assets": load_assets()})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/plan")
async def plan(request: GeneratePlanRequest):
    if not request.theme.strip():
        raise HTTPException(status_code=400, detail="theme 不能为空")
    try:
        result = await generate_maomeme_plan(
            theme=request.theme,
            sample_video_path=request.sample_video_path,
            use_doubao=request.use_doubao,
        )
        return JSONResponse({"status": "success", "plan": result.model_dump(by_alias=True)})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"生成失败: {exc}") from exc


@router.post("/candidates")
async def candidates(request: CandidateRequest):
    if not request.theme.strip():
        raise HTTPException(status_code=400, detail="theme 不能为空")
    try:
        result = await generate_script_candidates(
            theme=request.theme,
            sample_video_path=request.sample_video_path,
            use_doubao=should_use_agent(request.use_doubao, request.generation_mode),
            duration_mode=request.duration_mode,
        )
        return JSONResponse({
            "status": "success",
            "theme": request.theme,
            "candidates": [public_candidate_dump(item) for item in result[:3]],
        })
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"候选生成失败: {exc}") from exc


@router.post("/candidates-stream")
async def candidates_stream(request: CandidateRequest):
    if not request.theme.strip():
        raise HTTPException(status_code=400, detail="theme 不能为空")

    async def event_stream():
        try:
            for step in [
                {"type": "stage", "message": "读取社会现实文本素材库", "progress": 0.18},
                {"type": "stage", "message": "匹配猫动画和背景素材索引", "progress": 0.36},
                {"type": "stage", "message": "编剧 Agent 正在生成候选方向", "progress": 0.46},
            ]:
                yield sse(step)
                await asyncio.sleep(0.22)

            result = []
            try:
                async with asyncio.timeout(max(90.0, float(get_settings().CANDIDATE_AGENT_TIMEOUT_SEC) + 20.0)):
                    async for event in stream_script_candidates(
                        theme=request.theme,
                        sample_video_path=request.sample_video_path,
                        use_doubao=should_use_agent(request.use_doubao, request.generation_mode),
                        duration_mode=request.duration_mode,
                    ):
                        if event["type"] == "agent_delta":
                            yield sse({
                                "type": "agent_delta",
                                "message": event.get("message", "Doubao Agent 正在流式生成剧本"),
                                "progress": event.get("progress", 0.62),
                                "text": event.get("text", ""),
                            })
                        elif event["type"] == "stage":
                            yield sse(event)
                        elif event["type"] == "draft_candidate" and event.get("candidate"):
                            yield sse({
                                "type": "draft_candidate",
                                "message": event.get("message", "草稿方向已生成"),
                                "progress": event.get("progress", 0.5),
                                "candidate": public_candidate_dump(event["candidate"]),
                            })
                        elif event["type"] == "candidate" and event.get("candidate"):
                            yield sse({
                                "type": "candidate",
                                "message": event.get("message", "候选方向已生成"),
                                "progress": event.get("progress", 0.6),
                                "candidate": public_candidate_dump(event["candidate"]),
                            })
                        elif event["type"] == "final":
                            result = event["candidates"][:3]
            except TimeoutError:
                yield sse({"type": "stage", "message": "真实 Agent 超时，回退本地测试候选", "progress": 0.86})
                result = await generate_script_candidates(
                    theme=request.theme,
                    sample_video_path=request.sample_video_path,
                    use_doubao=False,
                    duration_mode=request.duration_mode,
                )
                result = result[:3]

            result = result[:3]
            for index, item in enumerate(result):
                yield sse({
                    "type": "candidate",
                    "message": f"候选 {index + 1}/3：{item.title}",
                    "progress": 0.88 + (index + 1) * 0.03,
                    "candidate": public_candidate_dump(item),
                })
                await asyncio.sleep(0.08)
            yield sse({
                "type": "done",
                "message": "候选剧本生成完成",
                "progress": 1.0,
                "theme": request.theme,
                "candidates": [public_candidate_dump(item) for item in result],
            })
        except Exception as exc:
            yield sse({"type": "error", "message": f"候选生成失败: {exc}", "progress": 1.0})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/select")
async def select_plan(request: SelectPlanRequest):
    try:
        result = await plan_from_candidate(
            theme=request.theme,
            candidate=request.candidate,
            sample_video_path=request.sample_video_path,
            use_doubao=should_use_agent(request.use_doubao, request.generation_mode),
            duration_mode=request.duration_mode,
        )
        return JSONResponse({"status": "success", "plan": result.model_dump(by_alias=True)})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"分镜生成失败: {exc}") from exc


@router.post("/select-stream")
async def select_plan_stream(request: SelectPlanRequest):
    async def event_stream():
        try:
            async for event in storyboard_stream_from_candidate(
                theme=request.theme,
                candidate=request.candidate,
                sample_video_path=request.sample_video_path,
                use_doubao=should_use_agent(request.use_doubao, request.generation_mode),
                duration_mode=request.duration_mode,
            ):
                yield sse(event)
                await asyncio.sleep(0.08)
        except Exception as exc:
            yield sse({"type": "error", "message": f"分镜生成失败: {exc}", "progress": 1.0})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/revision-suggestions")
async def revision_suggestions(request: SuggestRevisionRequest):
    try:
        suggestions = await suggest_revisions(
            theme=request.theme,
            plan=request.plan,
            candidate=request.candidate,
            duration_mode=request.duration_mode,
        )
        return JSONResponse({"status": "success", "suggestions": suggestions})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"调整建议生成失败: {exc}") from exc


@router.post("/revise")
async def revise(request: RevisePlanRequest):
    if not request.instruction.strip():
        raise HTTPException(status_code=400, detail="instruction 不能为空")
    try:
        result = await revise_maomeme(
            theme=request.theme,
            instruction=request.instruction,
            plan=request.plan,
            candidate=request.candidate,
            use_doubao=should_use_agent(request.use_doubao, request.generation_mode),
            duration_mode=request.duration_mode,
        )
        return JSONResponse({
            "status": "success",
            "candidate": result["candidate"].model_dump(),
            "plan": result["plan"].model_dump(by_alias=True),
        })
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"调整失败: {exc}") from exc


@router.post("/render-jobs")
async def render_job(request: RenderJobRequest):
    try:
        job = create_render_job(
            plan=request.plan,
            packaging_engine=request.packaging_engine,
            allow_ai_fill=request.allow_ai_fill,
        )
        return JSONResponse({"status": "success", "job": job.model_dump()})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"创建渲染任务失败: {exc}") from exc


@router.post("/generate-background")
async def generate_background_asset(request: GenerateBackgroundRequest):
    if not request.allow_ai_fill:
        raise HTTPException(status_code=400, detail="allow_ai_fill 必须为 true 才能生成素材")
    if not request.prompt.strip():
        raise HTTPException(status_code=400, detail="prompt 不能为空")
    if not seedream_available():
        raise HTTPException(status_code=400, detail="Seedream 未配置")
    try:
        asset = generate_background(
            prompt=request.prompt,
            description=request.description,
            slug=request.slug,
        )
        return JSONResponse({"status": "success", "asset": asset})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"背景生成失败: {exc}") from exc


@router.get("/render-jobs/{job_id}")
async def render_job_status(job_id: str):
    job = get_render_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="渲染任务不存在")
    return JSONResponse({"status": "success", "job": job.model_dump()})


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def should_use_agent(use_doubao: bool, generation_mode: str = "agent") -> bool:
    return bool(use_doubao) and str(generation_mode or "agent").lower() not in {"workflow", "local", "deterministic", "false"}


def public_candidate_dump(candidate: ScriptCandidate) -> dict:
    data = candidate.model_dump()
    data["notes"] = [
        note for note in data.get("notes", [])
        if is_public_candidate_note(str(note))
    ]
    return data


def is_public_candidate_note(note: str) -> bool:
    if not note.strip():
        return False
    hidden_keywords = [
        "生成来源",
        "doubao",
        "Doubao",
        "Agent",
        "文本素材库",
        "爆款",
        "viral",
        "等待真实",
        "草稿预览",
    ]
    return not any(keyword in note for keyword in hidden_keywords)
