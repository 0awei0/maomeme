from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AssetRef(BaseModel):
    id: str = ""
    file: str = ""
    description: str = ""


class GapInfo(BaseModel):
    status: str = "matched"
    strategy: str = "direct_match"
    reason: str = ""


class MotionClipSpec(BaseModel):
    start: float = 0.0
    duration: float = 4.0
    speed: float | None = None
    loop: bool = False


class TransitionSpec(BaseModel):
    type: str = "cut"
    duration: float = 0.0


class TimelineSlot(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    start: float
    end: float
    role: str
    intent: str
    caption: str = Field(alias="copy")
    motion: AssetRef
    motion_quality: dict[str, bool] = Field(default_factory=dict)
    motion_clip: MotionClipSpec = Field(default_factory=MotionClipSpec)
    secondary_motion: AssetRef | None = None
    secondary_motion_quality: dict[str, bool] = Field(default_factory=dict)
    secondary_motion_clip: MotionClipSpec | None = None
    background: AssetRef
    background_source: str = "matched"
    background_prompt: str = ""
    transition: TransitionSpec = Field(default_factory=TransitionSpec)
    layout: str = "single"
    dialogue: list[dict[str, str]] = Field(default_factory=list)
    overlay_actions: list[dict[str, Any]] = Field(default_factory=list)
    gap: GapInfo = Field(default_factory=GapInfo)
    packaging: list[str] = Field(default_factory=list)
    source_pattern: str = ""


class MaoMemePlan(BaseModel):
    id: str
    theme: str
    source_structure: dict[str, Any] = Field(default_factory=dict)
    script: list[dict[str, Any]] = Field(default_factory=list)
    timeline: list[TimelineSlot] = Field(default_factory=list)
    material_needs: dict[str, Any] = Field(default_factory=dict)
    agent_notes: list[str] = Field(default_factory=list)


class ScriptCandidate(BaseModel):
    id: str
    title: str
    theme: str
    social_topic: str = ""
    tension: str = ""
    score: float = 0.0
    script: list[dict[str, Any]] = Field(default_factory=list)
    beat_seed: list[dict[str, Any]] = Field(default_factory=list)
    asset_hints: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class CandidateRequest(BaseModel):
    theme: str
    sample_video_path: str | None = None
    use_doubao: bool = True
    duration_mode: str = "short"


class CandidateResponse(BaseModel):
    status: str = "success"
    theme: str
    candidates: list[ScriptCandidate]


class SelectPlanRequest(BaseModel):
    theme: str
    candidate: ScriptCandidate
    sample_video_path: str | None = None
    use_doubao: bool = True
    allow_ai_fill: bool = False
    duration_mode: str = "short"


class RevisePlanRequest(BaseModel):
    theme: str
    instruction: str
    plan: MaoMemePlan | None = None
    candidate: ScriptCandidate | None = None
    use_doubao: bool = True
    duration_mode: str = "short"


class SuggestRevisionRequest(BaseModel):
    theme: str
    plan: MaoMemePlan | None = None
    candidate: ScriptCandidate | None = None
    duration_mode: str = "short"


class RenderJobRequest(BaseModel):
    plan: MaoMemePlan
    packaging_engine: str = "auto"
    allow_ai_fill: bool = False


class GenerateBackgroundRequest(BaseModel):
    prompt: str
    description: str = ""
    slug: str = "agent-fill"
    allow_ai_fill: bool = False


class RenderJobStatus(BaseModel):
    job_id: str
    status: str
    progress: float = 0.0
    message: str = ""
    output_path: str | None = None
    video_url: str | None = None
    error: str | None = None
    packaging_engine: str = "ffmpeg"
    fallback_reason: str | None = None


class GeneratePlanRequest(BaseModel):
    theme: str
    sample_video_path: str | None = None
    use_doubao: bool = True


class AnalyzeVideoRequest(BaseModel):
    video_path: str
    use_doubao: bool = True
