"""End-to-End VOD Processing Pipeline Orchestrator for Smart-Clips AI."""
import json
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.config import FACECAM_PRESETS
from backend.database import (
    create_clip,
    get_all_settings,
    get_clip,
    get_project,
    update_clip,
    update_project,
)
from backend.services.ai_director import find_viral_highlights
from backend.services.audio_analyzer import analyze_audio_spikes
from backend.services.face_tracker import auto_detect_facecam_box
from backend.services.transcriber import transcribe_audio
from backend.services.video_editor import render_vertical_clip
from backend.services.youtube_ingestor import (
    download_vod,
    inspect_youtube_url,
    parse_live_chat_spikes,
)


def _resolve_facecam_box(
    facecam_preset: str,
    custom_box: Optional[Dict[str, float]],
    video_path: Path,
) -> Dict[str, float]:
    if custom_box and all(k in custom_box for k in ("x", "y", "w", "h")):
        return {k: float(custom_box[k]) for k in ("x", "y", "w", "h")}
    if facecam_preset in FACECAM_PRESETS and facecam_preset != "auto":
        p = FACECAM_PRESETS[facecam_preset]
        return {"x": p["x"], "y": p["y"], "w": p["w"], "h": p["h"]}
    detected = auto_detect_facecam_box(video_path)
    return {
        "x": float(detected["x"]),
        "y": float(detected["y"]),
        "w": float(detected["w"]),
        "h": float(detected["h"]),
    }


def run_vod_pipeline(
    project_id: str,
    clip_count: int = 5,
    min_duration: int = 20,
    max_duration: int = 65,
    hook_banner: bool = True,
    max_video_minutes: Optional[int] = None,
):
    """Background worker that processes a YouTube URL from download -> clips rendered."""
    project = get_project(project_id)
    if not project:
        return

    settings = get_all_settings()
    url = project["youtube_url"]

    def _progress(pct: int, msg: str, status: str = "processing"):
        update_project(project_id, status=status, progress=pct, status_message=msg)

    try:
        # Step 1: Inspect Metadata
        _progress(5, "Membaca metadata & status video YouTube...")
        meta = inspect_youtube_url(url)
        update_project(
            project_id,
            video_id=meta["video_id"],
            title=meta["title"],
            channel=meta["channel"],
            thumbnail_url=meta["thumbnail_url"],
            duration=meta["duration"],
            is_live=1 if meta["is_live"] else 0,
        )

        # If URL is an active live stream, capture an initial rolling chunk so VOD mode still works seamlessly!
        if meta["is_live"]:
            from backend.config import DOWNLOADS_DIR
            from backend.services.youtube_ingestor import extract_audio_wav, extract_preview_frame, get_media_duration, record_live_chunk

            chunk_sec = int(settings.get("live_chunk_seconds", "180") or 180)
            chunk_sec = min(300, max(60, chunk_sec))
            _progress(12, f"Stream sedang LIVE! Merekam segmen {chunk_sec} detik secara real-time...")
            proj_dir = DOWNLOADS_DIR / project_id
            proj_dir.mkdir(parents=True, exist_ok=True)
            video_path = proj_dir / "source.mp4"
            record_live_chunk(url, video_path, duration_seconds=chunk_sec)
            audio_path = extract_audio_wav(video_path, proj_dir / "audio.mp3")
            preview_path = extract_preview_frame(video_path, project_id)
            duration = get_media_duration(video_path)
            dl_info = {
                "video_path": video_path,
                "audio_path": audio_path,
                "preview_path": preview_path,
                "live_chat_path": None,
                "subtitle_files": [],
                "duration": duration,
            }
        else:
            # Step 2: Download VOD + Audio + Subtitles + Live Chat Replay
            dl_info = download_vod(
                url=url,
                project_id=project_id,
                max_video_minutes=max_video_minutes,
                progress_cb=lambda p, m: _progress(p, m),
            )

        video_path: Path = dl_info["video_path"]
        audio_path: Path = dl_info["audio_path"]
        preview_path: Optional[Path] = dl_info["preview_path"]
        duration: float = dl_info["duration"] or meta["duration"]

        # Auto-detect facecam box right after download
        custom_box = None
        if project.get("facecam_box_json"):
            try:
                custom_box = json.loads(project["facecam_box_json"])
            except Exception:
                custom_box = None

        facecam_box = _resolve_facecam_box(
            project.get("facecam_preset") or "auto",
            custom_box,
            video_path,
        )

        update_project(
            project_id,
            duration=duration,
            video_path=str(video_path),
            audio_path=str(audio_path),
            preview_frame_path=str(preview_path) if preview_path else "",
            facecam_box_json=json.dumps(facecam_box),
        )

        # Step 3: Audio Energy Spike & Live Chat Spike Analysis
        _progress(35, "Menganalisis lonjakan suara (teriakan/ketawa) & Live Chat...")
        audio_result = analyze_audio_spikes(audio_path)
        audio_spikes = audio_result.get("spikes", [])
        chat_spikes = parse_live_chat_spikes(dl_info.get("live_chat_path"), duration)

        update_project(
            project_id,
            audio_spikes_json=json.dumps(audio_result, ensure_ascii=False),
            chat_spikes_json=json.dumps(chat_spikes, ensure_ascii=False),
        )

        # Step 4: Speech-to-Text Transcription (Word-level timestamps for Karaoke subtitles)
        _progress(48, "Mentranskripsi suara ke teks (word-level timestamp)...")
        transcript = transcribe_audio(
            audio_path=audio_path,
            subtitle_files=dl_info.get("subtitle_files") or [],
            gemini_api_key=settings.get("gemini_api_key", ""),
            engine=settings.get("transcribe_engine", "auto"),
            whisper_model_size=settings.get("whisper_model_size", "base"),
        )
        segments = transcript.get("segments", [])
        words = transcript.get("words", [])
        update_project(
            project_id,
            transcript_json=json.dumps(transcript, ensure_ascii=False),
        )

        # Step 5: AI Viral Director (Gemini 4 Hydrogen / Multimodal Heuristic)
        _progress(62, "AI Viral Director sedang memilih momen paling seru & skor viral...")
        plan = find_viral_highlights(
            segments=segments,
            audio_spikes=audio_spikes,
            chat_spikes=chat_spikes,
            video_title=meta["title"],
            duration=duration,
            gemini_api_key=settings.get("gemini_api_key", ""),
            gemini_model=settings.get("gemini_model", "gemini-4-hydrogen"),
            content_type=project.get("content_type") or "auto",
            clip_count=clip_count,
            min_duration=min_duration,
            max_duration=max_duration,
        )

        # Determine final layout mode
        requested_layout = project.get("layout_mode") or "auto"
        if requested_layout == "auto":
            final_layout = "gaming_split" if plan.detected_content_type == "gaming" else "podcast"
        else:
            final_layout = requested_layout

        subtitle_style = project.get("subtitle_style") or settings.get("subtitle_style", "karaoke_yellow")
        total_clips = max(1, len(plan.clips))

        # Step 6: Render each selected viral clip to 1080x1920 vertical MP4 + Karaoke subtitles
        for idx, cand in enumerate(plan.clips, start=1):
            pct = 65 + int((idx - 1) / total_clips * 32)
            _progress(pct, f"Auto-Edit & Render Klip {idx}/{total_clips}: {cand.title[:35]}...")

            clip_id = f"clip_{uuid.uuid4().hex[:10]}"
            clip_words = [
                w for w in words
                if float(w.get("end", 0)) >= cand.start_time and float(w.get("start", 0)) <= cand.end_time
            ]
            hashtags_str = " ".join(
                (t if t.startswith("#") else f"#{t.lstrip('#')}") for t in cand.hashtags
            )

            create_clip({
                "id": clip_id,
                "project_id": project_id,
                "title": cand.title,
                "hook_text": cand.hook_text,
                "caption": cand.caption,
                "hashtags": hashtags_str,
                "reason": cand.reason,
                "category": cand.category,
                "virality_score": cand.virality_score,
                "start_time": cand.start_time,
                "end_time": cand.end_time,
                "duration": round(cand.end_time - cand.start_time, 2),
                "layout_mode": final_layout,
                "subtitle_style": subtitle_style,
                "facecam_box_json": json.dumps(facecam_box),
                "words_json": json.dumps(clip_words, ensure_ascii=False),
                "status": "rendering",
            })

            try:
                rendered = render_vertical_clip(
                    source_video=video_path,
                    clip_id=clip_id,
                    start_time=cand.start_time,
                    end_time=cand.end_time,
                    layout_mode=final_layout,
                    subtitle_style=subtitle_style,
                    facecam_box=facecam_box,
                    words=clip_words,
                    hook_text=cand.hook_text,
                    hook_banner_enabled=hook_banner,
                )
                update_clip(
                    clip_id,
                    video_path=str(rendered["video_path"]),
                    subtitle_path=str(rendered["subtitle_path"]),
                    thumbnail_path=str(rendered["thumbnail_path"]),
                    status="ready",
                )
            except Exception as render_exc:
                update_clip(clip_id, status="failed", reason=f"Render gagal: {render_exc}")

        _progress(100, f"Selesai! {len(plan.clips)} klip 9:16 siap ditonton & diposting ke TikTok/IG.", status="completed")

    except Exception as exc:
        traceback.print_exc()
        update_project(
            project_id,
            status="failed",
            status_message=f"Gagal memproses: {str(exc)[:220]}",
            error_message=str(exc),
        )


def rerender_existing_clip(clip_id: str) -> Dict[str, Any]:
    """Re-render an existing clip after the user edits timing, layout, facecam crop, hook, or subtitle style."""
    clip = get_clip(clip_id)
    if not clip:
        raise ValueError("Klip tidak ditemukan")
    project = get_project(clip["project_id"])
    if not project or not project.get("video_path"):
        raise ValueError("Video sumber proyek tidak ditemukan")

    source_video = Path(project["video_path"])
    if not source_video.exists():
        raise ValueError("File video sumber sudah terhapus di disk")

    settings = get_all_settings()
    hook_banner = settings.get("hook_banner_enabled", "true").lower() == "true"

    # Pull fresh words from project transcript if available
    words: List[Dict[str, Any]] = []
    if project.get("transcript_json"):
        try:
            tr = json.loads(project["transcript_json"])
            words = [
                w for w in tr.get("words", [])
                if float(w.get("end", 0)) >= clip["start_time"] and float(w.get("start", 0)) <= clip["end_time"]
            ]
        except Exception:
            pass
    if not words and clip.get("words_json"):
        try:
            words = json.loads(clip["words_json"])
        except Exception:
            words = []

    facecam_box = None
    if clip.get("facecam_box_json"):
        try:
            facecam_box = json.loads(clip["facecam_box_json"])
        except Exception:
            pass

    update_clip(clip_id, status="rendering")
    rendered = render_vertical_clip(
        source_video=source_video,
        clip_id=clip_id,
        start_time=float(clip["start_time"]),
        end_time=float(clip["end_time"]),
        layout_mode=clip.get("layout_mode") or "podcast",
        subtitle_style=clip.get("subtitle_style") or "karaoke_yellow",
        facecam_box=facecam_box,
        words=words,
        hook_text=clip.get("hook_text"),
        hook_banner_enabled=hook_banner,
    )
    return update_clip(
        clip_id,
        video_path=str(rendered["video_path"]),
        subtitle_path=str(rendered["subtitle_path"]),
        thumbnail_path=str(rendered["thumbnail_path"]),
        duration=round(float(clip["end_time"]) - float(clip["start_time"]), 2),
        status="ready",
    )
