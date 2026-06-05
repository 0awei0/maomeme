from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from ..models.maomeme import (
    BriefSuggestionsRequest,
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
from ..services.doubao_client import ark_available, generate_brief_suggestions_with_mini
from ..services.render_jobs import create_render_job, get_render_job
from ..services.seedream_service import constrain_background_prompt, generate_background, seedream_available
from ..core.config import get_settings
from ..services.upload_store import migration_context

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
            session_id=request.session_id,
            viral_analysis_id=request.viral_analysis_id,
            user_material_ids=request.user_material_ids,
            creative_brief=request.creative_brief,
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
                        session_id=request.session_id,
                        viral_analysis_id=request.viral_analysis_id,
                        user_material_ids=request.user_material_ids,
                        creative_brief=request.creative_brief,
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
                    session_id=request.session_id,
                    viral_analysis_id=request.viral_analysis_id,
                    user_material_ids=request.user_material_ids,
                    creative_brief=request.creative_brief,
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
            session_id=request.session_id,
            viral_analysis_id=request.viral_analysis_id,
            user_material_ids=request.user_material_ids,
            creative_brief=request.creative_brief,
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
                session_id=request.session_id,
                viral_analysis_id=request.viral_analysis_id,
                user_material_ids=request.user_material_ids,
                creative_brief=request.creative_brief,
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
            creative_brief=request.creative_brief,
        )
        return JSONResponse({"status": "success", "suggestions": suggestions})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"调整建议生成失败: {exc}") from exc


@router.post("/brief-suggestions")
async def brief_suggestions(request: BriefSuggestionsRequest):
    if not request.theme.strip():
        return JSONResponse({"status": "success", "provider": "fallback", "suggestions": {}})
    fallback = fallback_brief_suggestions(request.theme)
    if not ark_available():
        return JSONResponse({"status": "success", "provider": "fallback", "suggestions": fallback})
    try:
        context = migration_context(
            session_id=request.session_id,
            viral_analysis_id=request.viral_analysis_id,
            creative_brief=request.creative_brief,
            material_summary={},
        )
        async with asyncio.timeout(4.0):
            raw = await generate_brief_suggestions_with_mini(
                theme=request.theme,
                creative_brief=request.creative_brief.model_dump(),
                viral_context=context.get("viral_analysis", {}),
            )
        suggestions = sanitize_brief_suggestions(raw.get("suggestions") if isinstance(raw, dict) else {}, fallback)
        if not brief_suggestions_fit_theme(request.theme, suggestions):
            return JSONResponse({"status": "success", "provider": "fallback", "suggestions": fallback})
        return JSONResponse({"status": "success", "provider": "mini", "suggestions": suggestions})
    except Exception:
        return JSONResponse({"status": "success", "provider": "fallback", "suggestions": fallback})


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
            session_id=request.session_id,
            viral_analysis_id=request.viral_analysis_id,
            user_material_ids=request.user_material_ids,
            creative_brief=request.creative_brief,
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
    if not any(
        str(value).strip()
        for value in (
            request.prompt,
            request.seedream_prompt,
            request.background_need,
            request.caption,
            request.fallback_prompt,
        )
    ):
        raise HTTPException(status_code=400, detail="prompt 或 background_need 不能为空")
    if not seedream_available():
        raise HTTPException(status_code=400, detail="Seedream 未配置")
    try:
        constrained = constrain_background_prompt(
            theme=request.theme,
            caption=request.caption,
            scene_keywords=request.scene_keywords,
            background_need=request.background_need,
            seedream_prompt=request.seedream_prompt or request.prompt,
            negative_constraints=request.negative_constraints,
            slug_hint=request.slug_hint or request.slug,
            fallback_prompt=request.fallback_prompt,
            fallback_slug=request.slug,
        )
        asset = generate_background(
            prompt=str(constrained["prompt"]),
            description=request.description or str(constrained["description"]),
            slug=str(constrained["slug"]),
        )
        return JSONResponse({"status": "success", "asset": asset, "prompt": constrained})
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
        "viral",
        "等待真实",
        "草稿预览",
    ]
    return not any(keyword in note for keyword in hidden_keywords)


BRIEF_FIELDS = {
    "target_audience",
    "protagonist",
    "core_conflict",
    "ending_tone",
    "required_scenes",
    "required_props",
}


def sanitize_brief_suggestions(raw: object, fallback: dict[str, list[str]]) -> dict[str, list[str]]:
    suggestions: dict[str, list[str]] = {key: [] for key in BRIEF_FIELDS}
    if isinstance(raw, dict):
        for key in BRIEF_FIELDS:
            value = raw.get(key)
            items = value if isinstance(value, list) else [value] if isinstance(value, str) else []
            for item in items:
                text = str(item or "").strip()
                if text and text not in suggestions[key]:
                    suggestions[key].append(text[:28])
    for key, items in fallback.items():
        if key not in suggestions:
            continue
        for item in items:
            if len(suggestions[key]) >= 3:
                break
            if item not in suggestions[key]:
                suggestions[key].append(item)
    return {key: value[:3] for key, value in suggestions.items() if value}


def brief_suggestions_fit_theme(theme: str, suggestions: dict[str, list[str]]) -> bool:
    theme_text = str(theme or "")
    suggestion_text = " ".join(item for values in suggestions.values() for item in values)
    checks = [
        (("请假", "老板", "120", "病假", "急救"), ("请假", "老板", "120", "病假", "急救", "审批")),
        (("周一", "不想上班", "上班综合症", "闹钟"), ("周一", "闹钟", "上班", "通勤", "工作群", "咖啡")),
        (("烤肠", "摆摊", "找工作", "招聘", "简历", "岗位"), ("烤肠", "摆摊", "招聘", "简历", "岗位", "薪资", "HR")),
    ]
    for theme_words, expected_words in checks:
        if any(word in theme_text for word in theme_words):
            return any(word in suggestion_text for word in expected_words)
    return True


def fallback_brief_suggestions(theme: str) -> dict[str, list[str]]:
    text = str(theme or "")
    if any(word in text for word in ("请假", "老板", "120", "病假", "00")):
        return {
            "target_audience": ["刚上班的年轻人", "不敢请假的打工人"],
            "protagonist": ["会装淡定的00后猫", "被消息追着跑的猫"],
            "core_conflict": ["请假被拒和身体报警", "老板不批假反被吓到"],
            "ending_tone": ["荒诞但解气", "讽刺职场边界感"],
            "required_scenes": ["工位和老板聊天框", "120急救电话弹窗"],
            "required_props": ["请假审批", "急救电话", "老板在吗"],
        }
    if any(word in text for word in ("烤肠", "摆摊", "找工作", "招聘", "简历", "岗位")):
        return {
            "target_audience": ["应届生和毕业生", "投简历投麻的人"],
            "protagonist": ["投简历投到怀疑猫生", "嘴硬但破防的毕业猫"],
            "core_conflict": ["岗位要求离谱到摆摊", "求职门槛和摆摊成本都卷"],
            "ending_tone": ["荒诞但现实", "黑色幽默收束"],
            "required_scenes": ["招聘软件", "校门口烤肠摊"],
            "required_props": ["岗位要求卡", "烤肠价签", "简历"],
        }
    if any(word in text for word in ("周一", "不想上班", "上班综合症", "上班")):
        return {
            "target_audience": ["周一通勤打工人", "上班综合症患者"],
            "protagonist": ["被闹钟击穿的打工猫", "嘴上上班心里请假的猫"],
            "core_conflict": ["身体周一心还在周末", "工作群比闹钟先醒"],
            "ending_tone": ["丧中带一点好笑", "讽刺但不鸡汤"],
            "required_scenes": ["卧室闹钟", "地铁通勤", "工作群"],
            "required_props": ["闹钟", "周会通知", "咖啡"],
        }
    return {
        "target_audience": ["大学生和刚上班的人"],
        "protagonist": ["普通但嘴硬的猫"],
        "core_conflict": ["努力和现实回报错位"],
        "ending_tone": ["讽刺但留一点温暖"],
        "required_scenes": ["现实生活场景"],
        "required_props": ["手机弹窗", "压力卡片"],
    }
