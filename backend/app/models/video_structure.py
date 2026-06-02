from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ScriptSection(BaseModel):
    type: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    text: str = ""
    purpose: str = ""
    hook_type: str | None = None


class Shot(BaseModel):
    start_time: float = 0.0
    end_time: float = 0.0
    type: str = "medium"
    content: str = ""
    camera_move: str = "静止"
    has_subtitle: bool = False
    visual_effect: str = "无"
    subject_distance: str = ""
    subject_position: str = ""
    subject_motion: str = ""


class BGMInfo(BaseModel):
    name: str = ""
    mood: str = ""
    bpm_range: str = ""


class VoiceoverInfo(BaseModel):
    has: bool = False
    style: str = ""
    language: str = ""


class SoundEffect(BaseModel):
    time: float = 0.0
    description: str = ""


class AudioStructure(BaseModel):
    bgm: BGMInfo = Field(default_factory=BGMInfo)
    voiceover: VoiceoverInfo = Field(default_factory=VoiceoverInfo)
    sound_effects: list[SoundEffect] = Field(default_factory=list)
    rhythm_sync: str = ""


class SubtitleStyle(BaseModel):
    font_size: str = ""
    color: str = ""
    position: str = ""
    animation: str = ""
    outline: str = ""


class Transition(BaseModel):
    time: float = 0.0
    type: str = ""
    description: str = ""


class TextGraphic(BaseModel):
    time_range: str = ""
    type: str = ""
    content: str = ""
    style: str = ""


class CoverStyle(BaseModel):
    main_text: str = ""
    subtitle_text: str = ""
    style: str = ""
    colors: list[str] = Field(default_factory=list)
    layout: str = ""


class PackagingStructure(BaseModel):
    subtitle_style: SubtitleStyle = Field(default_factory=SubtitleStyle)
    transitions: list[Transition] = Field(default_factory=list)
    text_graphics: list[TextGraphic] = Field(default_factory=list)
    cover_style: CoverStyle = Field(default_factory=CoverStyle)
    overall_visual_tone: str = ""


class TransferableFeatures(BaseModel):
    hook_strategy: str = ""
    narrative_pattern: str = ""
    pacing_pattern: str = ""
    spatial_pattern: str = ""
    subject_trajectory: str = ""
    composition_pattern: str = ""
    engagement_techniques: list[str] = Field(default_factory=list)
    suitable_categories: list[str] = Field(default_factory=list)


class VideoMeta(BaseModel):
    duration: float = 0.0
    resolution: str = ""
    fps: float = 30.0
    cover_frame: str | None = None


class VideoStructure(BaseModel):
    id: str
    meta: VideoMeta
    script_structure: list[ScriptSection] = Field(default_factory=list)
    shots: list[Shot] = Field(default_factory=list)
    audio_structure: AudioStructure = Field(default_factory=AudioStructure)
    packaging_structure: PackagingStructure = Field(default_factory=PackagingStructure)
    transferable_features: TransferableFeatures = Field(default_factory=TransferableFeatures)
    raw_response: str | None = None
    analysis_evidence: dict[str, Any] = Field(default_factory=dict)
