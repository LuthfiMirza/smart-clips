"""Real-Time YouTube Live Stream Watcher Service.
Continuously records rolling chunks (e.g. 3 minutes) of an active YouTube Live stream,
runs Audio Hype + Transcription + Gemini Viral Director on each chunk, auto-renders 9:16 clips
above the virality threshold, and optionally auto-posts to TikTok & Instagram Reels!
Inspired by Clipify (sherlock-dev-ai/Clipify) & LXGIC Clipper.
"""
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Dict

from backend.config import DOWNLOADS_DIR, FACECAM_PRESETS
from backend.database import (
    create_clip,
    create_project,
    get_all_settings,
    get_live_monitor,
    update_clip,
    update_live_monitor,
    update_project,
)
from backend.services.ai_director import find_viral_highlights
from backend.services.audio_analyzer import analyze_audio_spikes
from backend.services.face_tracker import auto_detect_facecam_box
from backend.services.social_poster import publish_clip_multi
from backend.services.transcriber import transcribe_audio
from backend.services.video_editor import render_vertical_clip
from backend.services.youtube_ingestor import (
    extract_audio_wav,
    extract_preview_frame,
    get_media_duration,
    inspect_youtube_url,
    record_live_chunk,
)

# Registry of active stop events by monitor_id
_ACTIVE_MONITORS: Dict[str, threading.Event] = {}


def start_monitor_thread(monitor_id: str):
    if monitor_id in _ACTIVE_MONITORS:
        return
    stop_event = threading.Event()
    _ACTIVE_MONITORS[monitor_id] = stop_event
    thread = threading.Thread(
        target=_monitor_loop,
        args=(monitor_id, stop_event),
        daemon=True,
        name=f"live-watcher-{monitor_id}",
    )
    thread.start()


def stop_monitor_thread(monitor_id: str):
    ev = _ACTIVE_MONITORS.pop(monitor_id, None)
    if ev:
        ev.set()
    update_live_monitor(monitor_id, status="stopped", last_log="Pemantauan dihentikan oleh pengguna.")


def _monitor_loop(monitor_id: str, stop_event: threading.Event):
    monitor = get_live_monitor(monitor_id)
    if not monitor:
        return

    url = monitor["youtube_url"]
    chunk_idx = int(monitor.get("chunks_processed") or 0)
    total_clips = int(monitor.get("clips_generated") or 0)

    # Create a companion project so all live clips appear neatly in the Dashboard gallery
    project_id = f"liveproj_{monitor_id[-8:]}"
    try:
        meta = inspect_youtube_url(url)
        update_live_monitor(
            monitor_id,
            title=meta["title"],
            channel=meta["channel"],
            last_log=f"Terhubung ke stream: {meta['title']}",
        )
        create_project({
            "id": project_id,
            "youtube_url": url,
            "video_id": meta["video_id"],
            "title": f"[LIVE] {meta['title']}",
            "channel": meta["channel"],
            "thumbnail_url": meta["thumbnail_url"],
            "is_live": 1,
            "content_type": monitor.get("content_type") or "gaming",
            "layout_mode": monitor.get("layout_mode") or "gaming_split",
            "subtitle_style": monitor.get("subtitle_style") or "karaoke_yellow",
            "facecam_preset": monitor.get("facecam_preset") or "auto",
            "facecam_box_json": monitor.get("facecam_box_json") or "",
            "status": "live_watching",
            "progress": 50,
            "status_message": "Memantau Live Stream secara real-time...",
        })
    except Exception as exc:
        update_live_monitor(monitor_id, status="error", last_log=f"Gagal terhubung ke live stream: {exc}")
        _ACTIVE_MONITORS.pop(monitor_id, None)
        return

    consecutive_errors = 0

    while not stop_event.is_set():
        monitor = get_live_monitor(monitor_id)
        if not monitor or monitor.get("status") != "running":
            break

        settings = get_all_settings()
        chunk_sec = int(monitor.get("chunk_seconds") or 180)
        threshold = int(monitor.get("auto_clip_threshold") or 75)
        layout_mode = monitor.get("layout_mode") or "gaming_split"
        subtitle_style = monitor.get("subtitle_style") or "karaoke_yellow"

        chunk_idx += 1
        chunk_dir = DOWNLOADS_DIR / project_id / f"chunk_{chunk_idx:03d}"
        chunk_dir.mkdir(parents=True, exist_ok=True)
        chunk_video = chunk_dir / "chunk.mp4"
        chunk_audio = chunk_dir / "audio.mp3"

        try:
            update_live_monitor(
                monitor_id,
                last_log=f"[Chunk #{chunk_idx}] Merekam {chunk_sec} detik live stream...",
            )
            record_live_chunk(url, chunk_video, duration_seconds=chunk_sec)
            consecutive_errors = 0

            if stop_event.is_set():
                break

            extract_audio_wav(chunk_video, chunk_audio)
            duration = get_media_duration(chunk_video)

            if chunk_idx == 1:
                preview_p = extract_preview_frame(chunk_video, project_id)
                if preview_p:
                    update_project(project_id, preview_frame_path=str(preview_p), video_path=str(chunk_video))

            # Determine facecam box
            facecam_box = None
            if monitor.get("facecam_box_json"):
                try:
                    facecam_box = json.loads(monitor["facecam_box_json"])
                except Exception:
                    facecam_box = None
            if not facecam_box:
                preset = monitor.get("facecam_preset") or "auto"
                if preset in FACECAM_PRESETS and preset != "auto":
                    p = FACECAM_PRESETS[preset]
                    facecam_box = {"x": p["x"], "y": p["y"], "w": p["w"], "h": p["h"]}
                else:
                    det = auto_detect_facecam_box(chunk_video, sample_count=4)
                    facecam_box = {"x": det["x"], "y": det["y"], "w": det["w"], "h": det["h"]}

            update_live_monitor(
                monitor_id,
                last_log=f"[Chunk #{chunk_idx}] Menganalisis teriakan audio & transkrip AI...",
            )
            audio_res = analyze_audio_spikes(chunk_audio)
            transcript = transcribe_audio(
                audio_path=chunk_audio,
                subtitle_files=[],
                gemini_api_key=settings.get("gemini_api_key", ""),
                engine=settings.get("transcribe_engine", "auto"),
                whisper_model_size=settings.get("whisper_model_size", "base"),
            )

            plan = find_viral_highlights(
                segments=transcript.get("segments", []),
                audio_spikes=audio_res.get("spikes", []),
                chat_spikes=[],
                video_title=f"{monitor.get('title') or 'Live'} (Segmen #{chunk_idx})",
                duration=duration,
                gemini_api_key=settings.get("gemini_api_key", ""),
                gemini_model=settings.get("gemini_model", "gemini-4-hydrogen"),
                content_type=monitor.get("content_type") or "gaming",
                clip_count=2,
                min_duration=20,
                max_duration=55,
            )

            # Filter candidates meeting virality threshold
            hot_clips = [c for c in plan.clips if c.virality_score >= threshold]
            if not hot_clips and plan.clips and audio_res.get("spikes"):
                # Keep top 1 if strong audio spike occurred
                if max(s["intensity"] for s in audio_res["spikes"]) >= threshold:
                    hot_clips = [plan.clips[0]]

            for cand in hot_clips:
                if stop_event.is_set():
                    break
                clip_id = f"liveclip_{uuid.uuid4().hex[:9]}"
                clip_words = [
                    w for w in transcript.get("words", [])
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
                    "reason": f"[LIVE Chunk #{chunk_idx}] {cand.reason}",
                    "category": cand.category,
                    "virality_score": cand.virality_score,
                    "start_time": cand.start_time,
                    "end_time": cand.end_time,
                    "duration": round(cand.end_time - cand.start_time, 2),
                    "layout_mode": layout_mode,
                    "subtitle_style": subtitle_style,
                    "facecam_box_json": json.dumps(facecam_box),
                    "words_json": json.dumps(clip_words, ensure_ascii=False),
                    "status": "rendering",
                })
                rendered = render_vertical_clip(
                    source_video=chunk_video,
                    clip_id=clip_id,
                    start_time=cand.start_time,
                    end_time=cand.end_time,
                    layout_mode=layout_mode,
                    subtitle_style=subtitle_style,
                    facecam_box=facecam_box,
                    words=clip_words,
                    hook_text=cand.hook_text,
                    hook_banner_enabled=True,
                )
                update_clip(
                    clip_id,
                    video_path=str(rendered["video_path"]),
                    subtitle_path=str(rendered["subtitle_path"]),
                    thumbnail_path=str(rendered["thumbnail_path"]),
                    status="ready",
                )
                total_clips += 1

                # Optional Auto-Post if user toggled auto_post ON!
                if monitor.get("auto_post"):
                    publish_clip_multi(clip_id, ["tiktok", "instagram"])

            update_live_monitor(
                monitor_id,
                chunks_processed=chunk_idx,
                clips_generated=total_clips,
                last_log=f"[Chunk #{chunk_idx}] Selesai diproses. Total klip otomatis: {total_clips}. Menangkap chunk berikutnya...",
            )

        except Exception as exc:
            consecutive_errors += 1
            update_live_monitor(
                monitor_id,
                last_log=f"[Chunk #{chunk_idx}] Error ({consecutive_errors}/3): {str(exc)[:180]}",
            )
            if consecutive_errors >= 3:
                update_live_monitor(
                    monitor_id,
                    status="completed",
                    last_log=f"Live stream berakhir atau tidak dapat diakses lagi. Total {total_clips} klip dihasilkan.",
                )
                update_project(project_id, status="completed", progress=100, status_message=f"Live selesai — {total_clips} klip dibuat.")
                break
            time.sleep(10)

    _ACTIVE_MONITORS.pop(monitor_id, None)
