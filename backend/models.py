"""Pydantic Schemas for Smart-Clips AI API & Gemini Structured Output."""
from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class FacecamBox(BaseModel):
    x: float = Field(0.72, ge=0.0, le=1.0, description="Normalized left X (0..1)")
    y: float = Field(0.62, ge=0.0, le=1.0, description="Normalized top Y (0..1)")
    w: float = Field(0.26, ge=0.05, le=1.0, description="Normalized width (0..1)")
    h: float = Field(0.34, ge=0.05, le=1.0, description="Normalized height (0..1)")


class InspectURLRequest(BaseModel):
    youtube_url: str


class CreateProjectRequest(BaseModel):
    youtube_url: str
    content_type: str = Field("auto", description="auto | podcast | gaming")
    layout_mode: str = Field("auto", description="auto | podcast | gaming_split | blur_canvas")
    subtitle_style: str = Field("karaoke_yellow", description="karaoke_yellow | karaoke_green | neon_cyan | clean_white | none")
    facecam_preset: str = Field("auto", description="auto | bottom_right | bottom_left | top_right | top_left | custom")
    facecam_box: Optional[FacecamBox] = None
    clip_count: int = Field(5, ge=1, le=15)
    min_duration: int = Field(20, ge=10, le=90)
    max_duration: int = Field(65, ge=15, le=180)
    hook_banner: bool = True
    max_video_minutes: Optional[int] = Field(None, description="Optional limit in minutes for very long VODs")


class StartLiveMonitorRequest(BaseModel):
    youtube_url: str
    content_type: str = Field("gaming", description="podcast | gaming")
    layout_mode: str = Field("gaming_split", description="podcast | gaming_split | blur_canvas")
    subtitle_style: str = "karaoke_yellow"
    facecam_preset: str = "auto"
    facecam_box: Optional[FacecamBox] = None
    chunk_seconds: int = Field(180, ge=60, le=600)
    auto_clip_threshold: int = Field(75, ge=50, le=99)
    auto_post: bool = False


class UpdateClipRequest(BaseModel):
    title: Optional[str] = None
    hook_text: Optional[str] = None
    caption: Optional[str] = None
    hashtags: Optional[str] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    layout_mode: Optional[str] = None
    subtitle_style: Optional[str] = None
    facecam_box: Optional[FacecamBox] = None
    rerender: bool = False


class PublishClipRequest(BaseModel):
    platforms: List[str] = Field(..., description="List containing 'tiktok' and/or 'instagram'")
    caption_override: Optional[str] = None
    privacy_level: Optional[str] = "PUBLIC_TO_EVERYONE"


class SettingsUpdateRequest(BaseModel):
    settings: Dict[str, str]


# ---- Structured Output Schemas for Gemini AI Viral Director ----

class HighlightCandidate(BaseModel):
    start_time: float = Field(..., description="Start timestamp in seconds (must align to sentence start)")
    end_time: float = Field(..., description="End timestamp in seconds (20 to 65 seconds after start_time)")
    title: str = Field(..., description="Catchy short Indonesian/original viral title (max 60 chars)")
    hook_text: str = Field(..., description="Punchy 3-6 word upper banner hook text to stop scrolling")
    caption: str = Field(..., description="Engaging social media caption for TikTok and Instagram Reels")
    hashtags: List[str] = Field(..., description="5 to 8 relevant viral hashtags without # prefix or with #")
    virality_score: int = Field(..., ge=1, le=100, description="Predicted virality score from 1 to 100")
    category: str = Field(..., description="Category: funny, hype, clutch, debate, insight, emotional, reaction")
    reason: str = Field(..., description="Brief explanation why this clip will perform well on TikTok/IG Reels")


class HighlightPlan(BaseModel):
    detected_content_type: str = Field(..., description="'podcast' or 'gaming' based on transcript & energy")
    summary: str = Field(..., description="1-sentence summary of the video content")
    clips: List[HighlightCandidate] = Field(..., description="Ranked list of best viral clip moments")
