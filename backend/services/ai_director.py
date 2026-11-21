"""AI Viral Director: Multimodal Highlight Engine powered by Google Gemini 4 Hydrogen (Interactions API)
with automatic Audio + Live Chat Spike fusion and zero-key heuristic fallback.
Inspired by ClippyMe, OpenShorts, and SharkyClips.
"""
import json
from typing import Any, Dict, List, Optional

from google import genai
from backend.models import HighlightCandidate, HighlightPlan


def _format_transcript_for_llm(segments: List[Dict[str, Any]], max_chars: int = 120_000) -> str:
    """Format timestamped transcript segments cleanly for Gemini."""
    lines = []
    total = 0
    for seg in segments:
        st = float(seg.get("start", 0))
        et = float(seg.get("end", st + 1))
        txt = str(seg.get("text", "")).strip()
        if not txt:
            continue
        line = f"[{st:.1f}s - {et:.1f}s] {txt}"
        total += len(line) + 1
        if total > max_chars:
            break
        lines.append(line)
    return "\n".join(lines)


def _heuristic_fallback_highlights(
    segments: List[Dict[str, Any]],
    audio_spikes: List[Dict[str, Any]],
    chat_spikes: List[Dict[str, Any]],
    duration: float,
    clip_count: int = 5,
    min_duration: int = 25,
    max_duration: int = 60,
    content_type: str = "auto",
) -> HighlightPlan:
    """
    Zero-API-key fallback highlight detector combining Audio Spikes, Chat Spikes, and Transcript pacing.
    Ensures Smart-Clips works out-of-the-box even before the user enters a Gemini API key!
    """
    target_dur = min(max_duration, max(min_duration, 42))
    detected_type = content_type if content_type in ("podcast", "gaming") else ("gaming" if chat_spikes or len(audio_spikes) >= 4 else "podcast")

    # Gather peak centers from chat + audio spikes
    anchors: List[Dict[str, Any]] = []
    for cs in chat_spikes:
        anchors.append({
            "t": float(cs["timestamp"]),
            "score": min(98, int(cs.get("intensity", 80)) + 8),
            "category": "hype",
            "reason": f"Lonjakan Live Chat ({cs.get('message_count', 0)} pesan/10 detik) menandakan momen sangat seru!",
        })
    for asp in audio_spikes:
        anchors.append({
            "t": float(asp["timestamp"]),
            "score": int(asp.get("intensity", 75)),
            "category": "reaction" if detected_type == "gaming" else "funny",
            "reason": f"{asp.get('label', 'Lonjakan audio')} terdeteksi pada detik {asp['timestamp']:.0f}s (Z-score {asp.get('z_score', 1.5)}).",
        })

    # If still not enough anchors (e.g. calm podcast), distribute evenly across transcript/duration
    if duration > 0 and len(anchors) < clip_count * 2:
        step = max(target_dur + 5, duration / (clip_count + 1))
        t_cursor = min(15.0, duration * 0.1)
        idx = 1
        while t_cursor < max(10.0, duration - min_duration) and len(anchors) < clip_count * 3:
            anchors.append({
                "t": round(t_cursor, 1),
                "score": max(65, 84 - idx * 3),
                "category": "insight" if detected_type == "podcast" else "highlight",
                "reason": "Segmen narasi utama dengan potensi retensi tinggi.",
            })
            t_cursor += step
            idx += 1

    anchors.sort(key=lambda a: a["score"], reverse=True)

    selected_clips: List[HighlightCandidate] = []
    used_ranges: List[tuple] = []

    for anchor in anchors:
        if len(selected_clips) >= clip_count:
            break
        center = anchor["t"]
        # Start slightly before the peak so viewers get context + payoff!
        st = max(0.0, center - target_dur * 0.45)
        et = min(duration if duration > 0 else st + target_dur, st + target_dur)
        if et - st < min_duration and duration >= min_duration:
            st = max(0.0, et - min_duration)

        # Snap to nearest transcript segment boundaries if available
        if segments:
            near_starts = [float(s["start"]) for s in segments if abs(float(s["start"]) - st) <= 6.0]
            if near_starts:
                st = min(near_starts, key=lambda x: abs(x - st))
            near_ends = [float(s["end"]) for s in segments if abs(float(s["end"]) - et) <= 6.0 and float(s["end"]) - st >= min_duration]
            if near_ends:
                et = min(near_ends, key=lambda x: abs(x - et))

        # Avoid overlapping clips
        if any(not (et <= u0 + 4 or st >= u1 - 4) for (u0, u1) in used_ranges):
            continue
        used_ranges.append((st, et))

        # Extract snippet text for title/caption
        snippet = " ".join(
            str(s.get("text", "")).strip()
            for s in segments
            if float(s.get("end", 0)) > st and float(s.get("start", 0)) < et
        ).strip()
        snippet_short = (snippet[:52] + "...") if len(snippet) > 55 else snippet
        clip_num = len(selected_clips) + 1

        title = snippet_short if len(snippet_short) >= 12 else f"Momen Seru #{clip_num} ({int(st // 60)}:{int(st % 60):02d})"
        hook = "TONTON SAMPAI HABIS!" if anchor["category"] in ("hype", "reaction") else "FAKTA MENGEJUTKAN INI!"
        caption = f"{title} 🔥 Menurut kalian gimana? Tulis di komentar!"
        hashtags = (
            ["gamingindonesia", "clips", "streamer", "fyp", "reels", "viral"]
            if detected_type == "gaming"
            else ["podcastindonesia", "clips", "edukasi", "fyp", "reels", "viral"]
        )

        selected_clips.append(
            HighlightCandidate(
                start_time=round(st, 2),
                end_time=round(et, 2),
                title=title[:60],
                hook_text=hook,
                caption=caption,
                hashtags=hashtags,
                virality_score=anchor["score"],
                category=anchor["category"],
                reason=anchor["reason"],
            )
        )

    return HighlightPlan(
        detected_content_type=detected_type,
        summary="Highlight terdeteksi otomatis berdasarkan analisis lonjakan audio, live chat, dan ritme pembicaraan (mode lokal).",
        clips=selected_clips,
    )


def find_viral_highlights(
    segments: List[Dict[str, Any]],
    audio_spikes: List[Dict[str, Any]],
    chat_spikes: List[Dict[str, Any]],
    video_title: str,
    duration: float,
    gemini_api_key: str = "",
    gemini_model: str = "gemini-4-hydrogen",
    content_type: str = "auto",
    clip_count: int = 5,
    min_duration: int = 25,
    max_duration: int = 65,
) -> HighlightPlan:
    """
    Use Google Gemini Interactions API (gemini-4-hydrogen) with Structured Outputs
    to fuse Transcript + Audio Hype Spikes + Live Chat Explosions into ranked Viral Clips.
    Automatically falls back to local multimodal heuristics if no API key is configured.
    """
    if not gemini_api_key or not gemini_api_key.strip():
        return _heuristic_fallback_highlights(
            segments=segments,
            audio_spikes=audio_spikes,
            chat_spikes=chat_spikes,
            duration=duration,
            clip_count=clip_count,
            min_duration=min_duration,
            max_duration=max_duration,
            content_type=content_type,
        )

    transcript_text = _format_transcript_for_llm(segments)
    audio_summary = json.dumps(audio_spikes[:15], ensure_ascii=False)
    chat_summary = json.dumps(chat_spikes[:12], ensure_ascii=False)

    prompt = f"""Kamu adalah Viral Short-Form Video Director profesional (spesialis TikTok, Instagram Reels, dan YouTube Shorts) untuk kreator Indonesia & internasional.

Analisis data video berikut untuk menemukan **{clip_count} momen PALING SERU dan BERPOTENSI VIRAL**:

=== INFO VIDEO ===
- Judul: {video_title}
- Total Durasi: {duration:.1f} detik
- Mode Konten Pilihan User: {content_type} (jika 'auto', tentukan apakah ini 'podcast' atau 'gaming')

=== SINYAL 1: LONJAKAN AUDIO (TERIAKAN / KETAWA / CLUTCH) ===
{audio_summary}

=== SINYAL 2: LONJAKAN LIVE CHAT PENONTON ===
{chat_summary}

=== SINYAL 3: TRANSKRIP PERCAKAPAN DENGAN TIMESTAMP ===
{transcript_text if transcript_text else "(Tidak ada transkrip teks — prioritaskan Sinyal 1 Audio Spikes dan Sinyal 2 Live Chat Spikes!)"}

=== ATURAN KETAT ===
1. Pilih maksimal {clip_count} klip terbaik yang TIDAK SALING TUMPANG TINDIH.
2. Setiap klip WAJIB berdurasi antara {min_duration} sampai {max_duration} detik, dan berada dalam rentang 0 sampai {max(duration, 30):.1f} detik.
3. Mulai klip tepat di awal kalimat/konteks (3-8 detik sebelum punchline/teriakan) dan akhiri setelah payoff/ketawa selesai (jangan terpotong di tengah kalimat).
4. Untuk konten **Podcast/Ngobrol**: cari hook kontroversial, cerita lucu, plot twist, perdebatan panas, atau nasihat tajam.
5. Untuk konten **Gaming/Reaction Streamer**: prioritaskan momen di mana Audio Spike + Live Chat Spike + reaksi kocak/kaget/clutch bertemu!
6. Buat `title` (maks 60 karakter), `hook_text` (3-6 kata super menarik untuk banner atas video), `caption` (gaya bahasa natural TikTok/IG Reels Indonesia sesuai bahasa video), dan 5-8 `hashtags`.
7. Berikan `virality_score` (1-100) yang objektif dan urutkan dari skor tertinggi ke terendah.
"""

    try:
        client = genai.Client(api_key=gemini_api_key.strip())
        # Use modern Interactions API with Structured Output JSON Schema
        interaction = client.interactions.create(
            model=gemini_model or "gemini-4-hydrogen",
            input=prompt,
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": HighlightPlan.model_json_schema(),
            },
        )
        raw_json = interaction.output_text
        plan = HighlightPlan.model_validate_json(raw_json)

        # Sanitize timestamps to stay safely within video duration
        valid_clips: List[HighlightCandidate] = []
        for c in plan.clips:
            st = max(0.0, float(c.start_time))
            et = float(c.end_time)
            if duration > 0:
                et = min(duration, et)
            if et - st < 10.0:
                et = min(duration if duration > 0 else st + min_duration, st + min_duration)
            if et > st + 5.0:
                c.start_time = round(st, 2)
                c.end_time = round(et, 2)
                valid_clips.append(c)

        if valid_clips:
            plan.clips = sorted(valid_clips, key=lambda x: x.virality_score, reverse=True)[:clip_count]
            return plan
    except Exception as exc:
        print(f"[AI Director] Gemini API fallback triggered: {exc}")

    return _heuristic_fallback_highlights(
        segments=segments,
        audio_spikes=audio_spikes,
        chat_spikes=chat_spikes,
        duration=duration,
        clip_count=clip_count,
        min_duration=min_duration,
        max_duration=max_duration,
        content_type=content_type,
    )
