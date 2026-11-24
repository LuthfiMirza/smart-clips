"""Smart-Clips AI — FastAPI Server & REST API."""
import json
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from backend.config import CLIPS_DIR, DOWNLOADS_DIR, FACECAM_PRESETS, FRONTEND_DIR, PREVIEWS_DIR
from backend.database import (
    create_live_monitor,
    create_project,
    delete_clip,
    delete_project,
    get_all_settings,
    get_clip,
    get_live_monitor,
    get_project,
    init_db,
    list_clips,
    list_live_monitors,
    list_post_logs,
    list_projects,
    update_clip,
    update_settings,
)
from backend.models import (
    CreateProjectRequest,
    InspectURLRequest,
    PublishClipRequest,
    SettingsUpdateRequest,
    StartLiveMonitorRequest,
    UpdateClipRequest,
)
from backend.services.live_watcher import start_monitor_thread, stop_monitor_thread
from backend.services.pipeline import reanalyze_project_highlights, rerender_existing_clip, run_vod_pipeline
from backend.services.social_poster import publish_clip_multi
from backend.services.video_editor import SUBTITLE_STYLES
from backend.services.youtube_ingestor import inspect_youtube_url

init_db()

app = FastAPI(
    title="Smart-Clips AI Studio",
    description="Auto-Clip YouTube VOD & Live Stream -> 9:16 Podcast/Gaming Split-Screen + Karaoke Subtitles -> TikTok & Instagram Reels",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount media & static frontend assets
app.mount("/media/clips", StaticFiles(directory=str(CLIPS_DIR)), name="media_clips")
app.mount("/media/previews", StaticFiles(directory=str(PREVIEWS_DIR)), name="media_previews")
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static_frontend")


def _serialize_clip(c: dict) -> dict:
    item = dict(c)
    clip_id = item["id"]
    vp = Path(item.get("video_path") or "")
    tp = Path(item.get("thumbnail_path") or "")
    item["video_url"] = f"/media/clips/{clip_id}/{vp.name}" if vp.exists() else ""
    item["thumbnail_url"] = f"/media/clips/{clip_id}/{tp.name}" if tp.exists() else ""
    try:
        item["facecam_box"] = json.loads(item["facecam_box_json"]) if item.get("facecam_box_json") else None
    except Exception:
        item["facecam_box"] = None
    return item


def _serialize_project(p: dict) -> dict:
    item = dict(p)
    pp = Path(item.get("preview_frame_path") or "")
    item["preview_frame_url"] = f"/media/previews/{pp.name}" if pp.exists() else ""
    try:
        item["facecam_box"] = json.loads(item["facecam_box_json"]) if item.get("facecam_box_json") else None
    except Exception:
        item["facecam_box"] = None
    try:
        item["audio_analysis"] = json.loads(item["audio_spikes_json"]) if item.get("audio_spikes_json") else None
    except Exception:
        item["audio_analysis"] = None
    try:
        item["chat_spikes"] = json.loads(item["chat_spikes_json"]) if item.get("chat_spikes_json") else []
    except Exception:
        item["chat_spikes"] = []
    # Omit huge raw transcript from list payloads unless needed
    item.pop("transcript_json", None)
    item.pop("audio_spikes_json", None)
    item.pop("chat_spikes_json", None)
    return item


@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    index_file = FRONTEND_DIR / "index.html"
    if not index_file.exists():
        return HTMLResponse("<h1>Smart-Clips AI Backend Running</h1><p>Frontend index.html belum ditemukan.</p>")
    return FileResponse(str(index_file))


@app.get("/api/health")
def health_check():
    settings = get_all_settings()
    return {
        "status": "ok",
        "gemini_configured": bool(settings.get("gemini_api_key", "").strip()),
        "tiktok_configured": bool(settings.get("tiktok_access_token", "").strip()),
        "instagram_configured": bool(settings.get("instagram_access_token", "").strip() and settings.get("instagram_user_id", "").strip()),
        "facecam_presets": FACECAM_PRESETS,
        "subtitle_styles": list(SUBTITLE_STYLES.keys()) + ["none"],
    }


# ---- URL Inspection ----

@app.post("/api/inspect")
def api_inspect_url(req: InspectURLRequest):
    try:
        info = inspect_youtube_url(req.youtube_url.strip())
        return {"ok": True, "info": info}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---- Projects (VOD & Instant Live Capture) ----

@app.get("/api/projects")
def api_list_projects():
    projects = [_serialize_project(p) for p in list_projects()]
    return {"projects": projects}


@app.get("/api/projects/{project_id}")
def api_get_project(project_id: str):
    proj = get_project(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Proyek tidak ditemukan")
    clips = [_serialize_clip(c) for c in list_clips(project_id=project_id)]
    return {"project": _serialize_project(proj), "clips": clips}


@app.post("/api/projects")
def api_create_project(req: CreateProjectRequest, background_tasks: BackgroundTasks):
    project_id = f"proj_{uuid.uuid4().hex[:10]}"
    facecam_json = json.dumps(req.facecam_box.model_dump()) if req.facecam_box else ""

    proj = create_project({
        "id": project_id,
        "youtube_url": req.youtube_url.strip(),
        "title": "Memuat informasi video...",
        "content_type": req.content_type,
        "layout_mode": req.layout_mode,
        "subtitle_style": req.subtitle_style,
        "facecam_preset": req.facecam_preset,
        "facecam_box_json": facecam_json,
        "status": "queued",
        "progress": 2,
        "status_message": "Memulai Smart-Clips AI pipeline...",
    })

    background_tasks.add_task(
        run_vod_pipeline,
        project_id=project_id,
        clip_count=req.clip_count,
        min_duration=req.min_duration,
        max_duration=req.max_duration,
        hook_banner=req.hook_banner,
        max_video_minutes=req.max_video_minutes,
    )
    return {"ok": True, "project": _serialize_project(proj)}


@app.post("/api/projects/{project_id}/reanalyze")
def api_reanalyze_project(project_id: str, background_tasks: BackgroundTasks):
    proj = get_project(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Proyek tidak ditemukan")
    from backend.database import update_project
    update_project(
        project_id,
        status="processing",
        progress=10,
        status_message="🎯 Memulai analisis ulang Hook & Momen Viral V2 (tanpa download ulang audio)...",
        error_message="",
    )
    background_tasks.add_task(
        reanalyze_project_highlights,
        project_id=project_id,
        clip_count=4,
        min_duration=25,
        max_duration=45,
        hook_banner=True,
    )
    return {"ok": True, "project": _serialize_project(get_project(project_id))}


@app.post("/api/projects/{project_id}/retry")
def api_retry_project(project_id: str, background_tasks: BackgroundTasks):
    proj = get_project(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Proyek tidak ditemukan")
    from backend.database import update_project
    update_project(
        project_id,
        status="queued",
        progress=2,
        status_message="Memulai ulang dengan Turbo Smart-Clips pipeline...",
        error_message="",
    )
    background_tasks.add_task(
        run_vod_pipeline,
        project_id=project_id,
        clip_count=5,
        min_duration=20,
        max_duration=60,
        hook_banner=True,
    )
    return {"ok": True, "project": _serialize_project(get_project(project_id))}


@app.delete("/api/projects/{project_id}")
def api_delete_project(project_id: str):
    proj = get_project(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Proyek tidak ditemukan")
    # Terminate any active yt-dlp/ffmpeg subprocess targeting this project directory
    subprocess.run(["pkill", "-f", project_id], capture_output=True)
    # Cleanup clips on disk
    for c in list_clips(project_id=project_id):
        cdir = CLIPS_DIR / c["id"]
        if cdir.exists():
            shutil.rmtree(cdir, ignore_errors=True)
    # Cleanup download dir
    ddir = DOWNLOADS_DIR / project_id
    if ddir.exists():
        shutil.rmtree(ddir, ignore_errors=True)
    delete_project(project_id)
    return {"ok": True}


# ---- Clips ----

@app.get("/api/clips")
def api_list_clips(project_id: Optional[str] = None):
    clips = [_serialize_clip(c) for c in list_clips(project_id=project_id)]
    return {"clips": clips}


@app.patch("/api/clips/{clip_id}")
def api_update_clip(clip_id: str, req: UpdateClipRequest, background_tasks: BackgroundTasks):
    clip = get_clip(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Klip tidak ditemukan")

    updates = {}
    if req.title is not None:
        updates["title"] = req.title
    if req.hook_text is not None:
        updates["hook_text"] = req.hook_text
    if req.caption is not None:
        updates["caption"] = req.caption
    if req.hashtags is not None:
        updates["hashtags"] = req.hashtags
    if req.start_time is not None:
        updates["start_time"] = req.start_time
    if req.end_time is not None:
        updates["end_time"] = req.end_time
    if req.layout_mode is not None:
        updates["layout_mode"] = req.layout_mode
    if req.subtitle_style is not None:
        updates["subtitle_style"] = req.subtitle_style
    if req.facecam_box is not None:
        updates["facecam_box_json"] = json.dumps(req.facecam_box.model_dump())

    updated = update_clip(clip_id, **updates)
    if req.rerender:
        update_clip(clip_id, status="rendering")
        background_tasks.add_task(rerender_existing_clip, clip_id)
        updated = get_clip(clip_id)

    return {"ok": True, "clip": _serialize_clip(updated)}


@app.get("/api/clips/{clip_id}/download")
def api_download_clip(clip_id: str):
    clip = get_clip(clip_id)
    if not clip or not clip.get("video_path"):
        raise HTTPException(status_code=404, detail="Video klip belum tersedia")
    vp = Path(clip["video_path"])
    if not vp.exists():
        raise HTTPException(status_code=404, detail="File MP4 tidak ditemukan di server")
    safe_name = "".join(ch if ch.isalnum() or ch in (" ", "-", "_") else "" for ch in (clip.get("title") or clip_id))[:45].strip() or clip_id
    return FileResponse(str(vp), media_type="video/mp4", filename=f"{safe_name}_9x16.mp4")


@app.delete("/api/clips/{clip_id}")
def api_delete_clip(clip_id: str):
    cdir = CLIPS_DIR / clip_id
    if cdir.exists():
        shutil.rmtree(cdir, ignore_errors=True)
    delete_clip(clip_id)
    return {"ok": True}


# ---- Social Auto-Posting (TikTok & Instagram Reels) ----

@app.post("/api/clips/{clip_id}/publish")
def api_publish_clip(clip_id: str, req: PublishClipRequest):
    result = publish_clip_multi(
        clip_id=clip_id,
        platforms=req.platforms,
        caption_override=req.caption_override,
        privacy_level=req.privacy_level,
    )
    return result


@app.get("/api/post-logs")
def api_post_logs(clip_id: Optional[str] = None):
    return {"logs": list_post_logs(clip_id=clip_id)}


# ---- Real-Time YouTube Live Stream Monitors ----

@app.get("/api/live-monitors")
def api_list_live_monitors():
    return {"monitors": list_live_monitors()}


@app.post("/api/live-monitors")
def api_start_live_monitor(req: StartLiveMonitorRequest):
    monitor_id = f"mon_{uuid.uuid4().hex[:10]}"
    facecam_json = json.dumps(req.facecam_box.model_dump()) if req.facecam_box else ""
    mon = create_live_monitor({
        "id": monitor_id,
        "youtube_url": req.youtube_url.strip(),
        "title": "Menghubungkan ke Live Stream...",
        "content_type": req.content_type,
        "layout_mode": req.layout_mode,
        "subtitle_style": req.subtitle_style,
        "facecam_preset": req.facecam_preset,
        "facecam_box_json": facecam_json,
        "chunk_seconds": req.chunk_seconds,
        "auto_clip_threshold": req.auto_clip_threshold,
        "auto_post": 1 if req.auto_post else 0,
        "status": "running",
    })
    start_monitor_thread(monitor_id)
    return {"ok": True, "monitor": mon}


@app.post("/api/live-monitors/{monitor_id}/stop")
def api_stop_live_monitor(monitor_id: str):
    mon = get_live_monitor(monitor_id)
    if not mon:
        raise HTTPException(status_code=404, detail="Live monitor tidak ditemukan")
    stop_monitor_thread(monitor_id)
    return {"ok": True, "monitor": get_live_monitor(monitor_id)}


# ---- Settings ----

@app.get("/api/settings")
def api_get_settings():
    return {"settings": get_all_settings()}


@app.post("/api/settings")
def api_update_settings(req: SettingsUpdateRequest):
    updated = update_settings(req.settings)
    return {"ok": True, "settings": updated}

