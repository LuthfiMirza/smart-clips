"""End-to-End VOD Processing Pipeline Orchestrator for Smart-Clips AI.
Automatically switches between:
  - Standard Mode (videos <= 20 min): full download -> detect -> render
  - Turbo Long-VOD Mode (videos > 20 min, e.g. 5h+ live streams!):
      subtitles + live chat + lightweight audio -> detect highlights FIRST ->
      targeted --download-sections ONLY for winning clips -> render 9:16!
"""
import json
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.config import DOWNLOADS_DIR, FACECAM_PRESETS
from backend.database import (
    create_clip,
    delete_clip,
    get_all_settings,
    get_clip,
    get_project,
    list_clips,
    update_clip,
    update_project,
)
from backend.services.ai_director import find_viral_highlights
from backend.services.audio_analyzer import analyze_audio_spikes
from backend.services.face_tracker import auto_detect_facecam_box
from backend.services.transcriber import transcribe_audio
from backend.services.video_editor import render_vertical_clip
from backend.services.youtube_ingestor import (
    download_lightweight_audio,
    download_video_section,
    download_vod,
    extract_audio_wav,
    extract_preview_frame,
    get_media_duration,
    inspect_youtube_url,
    parse_live_chat_spikes,
    record_live_chunk,
)

TURBO_THRESHOLD_SECONDS = 1200  # > 20 minutes triggers Turbo Mode automatically


def _resolve_facecam_box(
    facecam_preset: str,
    custom_box: Optional[Dict[str, float]],
    video_path: Optional[Path] = None,
) -> Dict[str, float]:
    if custom_box and all(k in custom_box for k in ("x", "y", "w", "h")):
        return {k: float(custom_box[k]) for k in ("x", "y", "w", "h")}
    if facecam_preset in FACECAM_PRESETS and facecam_preset != "auto":
        p = FACECAM_PRESETS[facecam_preset]
        return {"x": p["x"], "y": p["y"], "w": p["w"], "h": p["h"]}
    if video_path and video_path.exists():
        detected = auto_detect_facecam_box(video_path)
        return {
            "x": float(detected["x"]),
            "y": float(detected["y"]),
            "w": float(detected["w"]),
            "h": float(detected["h"]),
        }
    return {"x": 0.72, "y": 0.62, "w": 0.26, "h": 0.34}


def run_vod_pipeline(
    project_id: str,
    clip_count: int = 5,
    min_duration: int = 20,
    max_duration: int = 65,
    hook_banner: bool = True,
    max_video_minutes: Optional[int] = None,
):
    """Background worker that processes a YouTube URL from inspect -> highlight -> 9:16 render."""
    project = get_project(project_id)
    if not project:
        return

    settings = get_all_settings()
    url = project["youtube_url"]

    def _progress(pct: int, msg: str, status: str = "processing"):
        # Abort gracefully if user deleted the project mid-run
        if not get_project(project_id):
            raise InterruptedError("Proyek dihapus oleh pengguna.")
        update_project(project_id, status=status, progress=pct, status_message=msg)

    try:
        # Step 1: Inspect Metadata
        _progress(4, "Membaca metadata & status video YouTube...")
        meta = inspect_youtube_url(url)
        effective_dur = meta["duration"]
        if max_video_minutes and max_video_minutes > 0:
            effective_dur = min(effective_dur, max_video_minutes * 60) if effective_dur > 0 else max_video_minutes * 60

        update_project(
            project_id,
            video_id=meta["video_id"],
            title=meta["title"],
            channel=meta["channel"],
            thumbnail_url=meta["thumbnail_url"],
            duration=meta["duration"],
            is_live=1 if meta["is_live"] else 0,
        )

        turbo_mode = (not meta["is_live"]) and (effective_dur > TURBO_THRESHOLD_SECONDS)
        video_path: Optional[Path] = None
        preview_path: Optional[Path] = None

        # Step 2: Media Ingestion (Active Live vs Turbo Long-VOD vs Standard Short VOD)
        if meta["is_live"]:
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
                "audio_path": audio_path,
                "live_chat_path": None,
                "subtitle_files": [],
                "duration": duration,
            }
        elif turbo_mode:
            hrs = int(effective_dur // 3600)
            mins = int((effective_dur % 3600) // 60)
            dur_txt = f"{hrs}j {mins}m" if hrs > 0 else f"{mins}m"
            _progress(7, f"⚡ Turbo Mode aktif (video {dur_txt}): hanya unduh audio ringan dulu, lalu potong klip HD terpilih!")
            dl_info = download_lightweight_audio(
                url=url,
                project_id=project_id,
                max_video_minutes=max_video_minutes,
                progress_cb=lambda p, m: _progress(p, m),
            )
            audio_path = dl_info["audio_path"]
            duration = dl_info["duration"] or effective_dur
        else:
            dl_info = download_vod(
                url=url,
                project_id=project_id,
                max_video_minutes=max_video_minutes,
                progress_cb=lambda p, m: _progress(p, m),
            )
            video_path = dl_info["video_path"]
            audio_path = dl_info["audio_path"]
            preview_path = dl_info["preview_path"]
            duration = dl_info["duration"] or meta["duration"]

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
            video_path=str(video_path) if video_path else "",
            audio_path=str(audio_path),
            preview_frame_path=str(preview_path) if preview_path else "",
            facecam_box_json=json.dumps(facecam_box),
        )

        # Step 3: Audio Energy Spike & Live Chat Spike Analysis
        _progress(38, "Menganalisis lonjakan suara (teriakan/ketawa) & Live Chat...")
        audio_result = analyze_audio_spikes(audio_path)
        audio_spikes = audio_result.get("spikes", [])
        chat_spikes = parse_live_chat_spikes(dl_info.get("live_chat_path"), duration)

        update_project(
            project_id,
            audio_spikes_json=json.dumps(audio_result, ensure_ascii=False),
            chat_spikes_json=json.dumps(chat_spikes, ensure_ascii=False),
        )

        # Step 4: Speech-to-Text Transcription (Word-level timestamps for Karaoke subtitles)
        _progress(48, "Membaca transkrip kata-per-kata (untuk Karaoke Subtitle & AI)...")
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

        # Step 5: AI Viral Director (Gemini 4 Hydrogen / Local Multimodal Heuristic)
        ai_label = "Gemini AI" if settings.get("gemini_api_key", "").strip() else "Mode Lokal (Audio + Chat + Sub)"
        _progress(58, f"{ai_label} sedang memilih {clip_count} momen paling seru...")
        plan = find_viral_highlights(
            segments=segments,
            audio_spikes=audio_result,
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

        requested_layout = project.get("layout_mode") or "auto"
        if requested_layout == "auto":
            final_layout = "gaming_split" if plan.detected_content_type == "gaming" else "podcast"
        else:
            final_layout = requested_layout

        subtitle_style = project.get("subtitle_style") or settings.get("subtitle_style", "karaoke_yellow")
        total_clips = max(1, len(plan.clips))
        slices_dir = DOWNLOADS_DIR / project_id / "slices"

        # Step 6: Download targeted clip slice (if Turbo) + Render each clip to 1080x1920 9:16
        for idx, cand in enumerate(plan.clips, start=1):
            base_pct = 62 + int((idx - 1) / total_clips * 35)
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
                if turbo_mode:
                    _progress(base_pct, f"⚡ Mengambil segmen HD klip {idx}/{total_clips} ({int(cand.start_time//60)}:{int(cand.start_time%60):02d})...")
                    slice_path = download_video_section(
                        url=url,
                        start_time=cand.start_time,
                        end_time=cand.end_time,
                        output_path=slices_dir / f"{clip_id}_src.mp4",
                    )
                    # Extract 16:9 preview frame & auto-detect facecam box on the first downloaded HD slice!
                    if not preview_path:
                        preview_path = extract_preview_frame(slice_path, project_id, timestamp=3.0)
                        if (project.get("facecam_preset") or "auto") == "auto" and not custom_box:
                            facecam_box = _resolve_facecam_box("auto", None, slice_path)
                        update_project(
                            project_id,
                            preview_frame_path=str(preview_path) if preview_path else "",
                            facecam_box_json=json.dumps(facecam_box),
                        )
                        update_clip(clip_id, facecam_box_json=json.dumps(facecam_box))

                    # Shift word timestamps to match the downloaded slice (starts at 0.0)
                    shifted_words = [
                        {
                            "word": w["word"],
                            "start": max(0.0, round(float(w["start"]) - cand.start_time, 3)),
                            "end": max(0.08, round(float(w["end"]) - cand.start_time, 3)),
                        }
                        for w in clip_words
                    ]
                    slice_dur = get_media_duration(slice_path) or (cand.end_time - cand.start_time)

                    _progress(base_pct + int(18 / total_clips), f"🎬 Auto-Edit 9:16 Klip {idx}/{total_clips}: {cand.title[:32]}...")
                    rendered = render_vertical_clip(
                        source_video=slice_path,
                        clip_id=clip_id,
                        start_time=0.0,
                        end_time=min(slice_dur, cand.end_time - cand.start_time),
                        layout_mode=final_layout,
                        subtitle_style=subtitle_style,
                        facecam_box=facecam_box,
                        words=shifted_words,
                        hook_text=cand.hook_text,
                        hook_banner_enabled=hook_banner,
                    )
                else:
                    assert video_path is not None
                    _progress(base_pct, f"🎬 Auto-Edit & Render Klip {idx}/{total_clips}: {cand.title[:35]}...")
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
                traceback.print_exc()
                update_clip(clip_id, status="failed", reason=f"Render gagal: {render_exc}")

        _progress(100, f"Selesai! {len(plan.clips)} klip 9:16 siap ditonton & diposting ke TikTok/IG.", status="completed")

    except InterruptedError:
        return
    except Exception as exc:
        traceback.print_exc()
        if get_project(project_id):
            update_project(
                project_id,
                status="failed",
                status_message=f"Gagal memproses: {str(exc)[:220]}",
                error_message=str(exc),
            )


def reanalyze_project_highlights(
    project_id: str,
    clip_count: int = 4,
    min_duration: int = 25,
    max_duration: int = 48,
    hook_banner: bool = True,
):
    """
    Re-run Audio Spike V2 + Live Chat Spike + AI Viral Director V2 on an already-ingested project
    WITHOUT re-downloading the full audio/video! Deletes old clips and renders fresh Hook-First clips.
    """
    import shutil
    from backend.config import CLIPS_DIR

    project = get_project(project_id)
    if not project:
        return

    settings = get_all_settings()
    url = project["youtube_url"]
    proj_dir = DOWNLOADS_DIR / project_id

    def _progress(pct: int, msg: str, status: str = "processing"):
        update_project(project_id, status=status, progress=pct, status_message=msg)

    try:
        _progress(15, "🎯 Menganalisis ulang sinyal audio beruntun, emosi dialog, & Live Chat...")

        # Remove old clips for this project
        for old_c in list_clips(project_id=project_id):
            cdir = CLIPS_DIR / old_c["id"]
            if cdir.exists():
                shutil.rmtree(cdir, ignore_errors=True)
            delete_clip(old_c["id"])

        audio_path = Path(project["audio_path"]) if project.get("audio_path") else None
        if not audio_path or not audio_path.exists():
            cands = list(proj_dir.glob("audio*.*"))
            audio_path = cands[0] if cands else None

        duration = float(project.get("duration") or 0)
        if audio_path and audio_path.exists():
            audio_result = analyze_audio_spikes(audio_path)
            if not duration:
                duration = float(audio_result.get("duration") or 0)
        elif project.get("audio_spikes_json"):
            audio_result = json.loads(project["audio_spikes_json"])
        else:
            audio_result = {"spikes": [], "energy_timeline": [], "z_timeline_1s": [], "duration": duration}

        live_chat_files = sorted(
            proj_dir.glob("*live_chat*.json*"),
            key=lambda p: (p.name.endswith(".part"), -p.stat().st_size),
        )
        chat_spikes = parse_live_chat_spikes(live_chat_files[0] if live_chat_files else None, duration)

        update_project(
            project_id,
            audio_spikes_json=json.dumps(audio_result, ensure_ascii=False),
            chat_spikes_json=json.dumps(chat_spikes, ensure_ascii=False),
        )

        # Load cached transcript or re-parse subtitles
        segments: List[Dict[str, Any]] = []
        words: List[Dict[str, Any]] = []
        if project.get("transcript_json"):
            try:
                tr = json.loads(project["transcript_json"])
                segments = tr.get("segments", [])
                words = tr.get("words", [])
            except Exception:
                pass
        if not segments and audio_path:
            sub_files = [
                p for p in proj_dir.glob("source.*")
                if p.suffix.lower() in {".json3", ".vtt", ".srt"} and "live_chat" not in p.name
            ]
            tr = transcribe_audio(
                audio_path=audio_path,
                subtitle_files=sub_files,
                gemini_api_key=settings.get("gemini_api_key", ""),
                engine=settings.get("transcribe_engine", "auto"),
            )
            segments = tr.get("segments", [])
            words = tr.get("words", [])
            update_project(project_id, transcript_json=json.dumps(tr, ensure_ascii=False))

        _progress(45, "🧠 AI Viral Director V2 sedang memilih momen dengan Hook 0:00 terkuat...")
        plan = find_viral_highlights(
            segments=segments,
            audio_spikes=audio_result,
            chat_spikes=chat_spikes,
            video_title=project.get("title") or "YouTube Video",
            duration=duration,
            gemini_api_key=settings.get("gemini_api_key", ""),
            gemini_model=settings.get("gemini_model", "gemini-4-hydrogen"),
            content_type=project.get("content_type") or "auto",
            clip_count=clip_count,
            min_duration=min_duration,
            max_duration=max_duration,
        )

        requested_layout = project.get("layout_mode") or "auto"
        final_layout = (
            ("gaming_split" if plan.detected_content_type == "gaming" else "podcast")
            if requested_layout == "auto"
            else requested_layout
        )
        subtitle_style = project.get("subtitle_style") or settings.get("subtitle_style", "karaoke_yellow")
        facecam_box = json.loads(project["facecam_box_json"]) if project.get("facecam_box_json") else {"x": 0.72, "y": 0.62, "w": 0.26, "h": 0.34}
        full_video = Path(project["video_path"]) if project.get("video_path") else None
        has_full_video = bool(full_video and full_video.exists())
        slices_dir = proj_dir / "slices"
        total_clips = max(1, len(plan.clips))

        for idx, cand in enumerate(plan.clips, start=1):
            base_pct = 52 + int((idx - 1) / total_clips * 44)
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

            if not has_full_video:
                _progress(base_pct, f"⚡ Mengambil segmen HD klip {idx}/{total_clips} ({int(cand.start_time//60)}:{int(cand.start_time%60):02d} - {cand.hook_text})...")
                slice_path = download_video_section(
                    url=url,
                    start_time=cand.start_time,
                    end_time=cand.end_time,
                    output_path=slices_dir / f"{clip_id}_src.mp4",
                )
                shifted_words = [
                    {
                        "word": w["word"],
                        "start": max(0.0, round(float(w["start"]) - cand.start_time, 3)),
                        "end": max(0.08, round(float(w["end"]) - cand.start_time, 3)),
                    }
                    for w in clip_words
                ]
                slice_dur = get_media_duration(slice_path) or (cand.end_time - cand.start_time)
                _progress(base_pct + int(20 / total_clips), f"🎬 Render 9:16 Klip {idx}/{total_clips}: {cand.hook_text}...")
                rendered = render_vertical_clip(
                    source_video=slice_path,
                    clip_id=clip_id,
                    start_time=0.0,
                    end_time=min(slice_dur, cand.end_time - cand.start_time),
                    layout_mode=final_layout,
                    subtitle_style=subtitle_style,
                    facecam_box=facecam_box,
                    words=shifted_words,
                    hook_text=cand.hook_text,
                    hook_banner_enabled=hook_banner,
                )
            else:
                assert full_video is not None
                _progress(base_pct, f"🎬 Render 9:16 Klip {idx}/{total_clips}: {cand.hook_text}...")
                rendered = render_vertical_clip(
                    source_video=full_video,
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

        _progress(100, f"Selesai! {len(plan.clips)} klip Hook-First V2 siap ditonton & diposting.", status="completed")
    except Exception as exc:
        traceback.print_exc()
        update_project(
            project_id,
            status="failed",
            status_message=f"Gagal analisis ulang: {str(exc)[:200]}",
            error_message=str(exc),
        )


def rerender_existing_clip(clip_id: str) -> Dict[str, Any]:
    """Re-render an existing clip (works for both Standard VOD & Turbo Long-VOD Mode!)."""
    clip = get_clip(clip_id)
    if not clip:
        raise ValueError("Klip tidak ditemukan")
    project = get_project(clip["project_id"])
    if not project:
        raise ValueError("Proyek tidak ditemukan")

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

    full_source = Path(project["video_path"]) if project.get("video_path") else None
    if full_source and full_source.exists():
        rendered = render_vertical_clip(
            source_video=full_source,
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
    else:
        # Turbo mode: fetch exact fresh slice for updated timestamps
        slice_path = download_video_section(
            url=project["youtube_url"],
            start_time=float(clip["start_time"]),
            end_time=float(clip["end_time"]),
            output_path=DOWNLOADS_DIR / project["id"] / "slices" / f"{clip_id}_rerender_src.mp4",
        )
        shifted_words = [
            {
                "word": w["word"],
                "start": max(0.0, round(float(w["start"]) - float(clip["start_time"]), 3)),
                "end": max(0.08, round(float(w["end"]) - float(clip["start_time"]), 3)),
            }
            for w in words
        ]
        slice_dur = get_media_duration(slice_path) or (float(clip["end_time"]) - float(clip["start_time"]))
        rendered = render_vertical_clip(
            source_video=slice_path,
            clip_id=clip_id,
            start_time=0.0,
            end_time=min(slice_dur, float(clip["end_time"]) - float(clip["start_time"])),
            layout_mode=clip.get("layout_mode") or "podcast",
            subtitle_style=clip.get("subtitle_style") or "karaoke_yellow",
            facecam_box=facecam_box,
            words=shifted_words,
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
