"""AI Viral Director V2: Multi-Factor Viral Moment & Hook-First Scoring Engine.
Combines:
  1. 1-Second Sustained Audio Excitement & Opening-Hook Energy Envelope
  2. Streamer & Podcast Viral Lexicon (Rage/Roast Bocil, Police/Action Chase, Shock/Panic, Comedy/Laugh, Podcast Debate/Secret)
  3. Repetition & Exclamation Burst Detection (e.g. "Polisi! Polisi!", "Cabut cabut!")
  4. Anti-Boring Filter (rejects low-speech dead air, setting/loading, casual walking)
  5. Live Chat Velocity & Hype Explosion Lag Correlation
  6. Smart Hook-Sentence Start Snapping (ensures 0:00 opens IMMEDIATELY on the hook trigger line!)
  7. Context-Aware Hook Banner & Viral Title Generator + Google Gemini 4 Hydrogen Structured Output
"""
import json
import re
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

from google import genai
from backend.models import HighlightCandidate, HighlightPlan

# Multi-category Indonesian & English Streamer/Podcast Viral Lexicon
VIRAL_CATEGORIES: Dict[str, Dict[str, Any]] = {
    "rage_roast": {
        "keywords": {
            "bocil": 10, "tolol": 9, "goblok": 9, "anjing": 8, "dolar kuning": 12,
            "kentot": 9, "bangsat": 8, "greget": 10, "gereget": 10, "main sendiri": 11,
            "ngadi-ngadi": 11, "nge-cheat": 9, "ngcheat": 9, "cheat": 7, "prank": 9,
            "troll": 9, "scam": 10, "bohong": 8, "sesat": 9, "caper": 9, "buang-buang time": 10,
            "buang time": 9, "toxic": 8, "anjing-anjingin": 11, "makan tuh": 10,
            "jangan ngajarin": 9, "sok tahu": 9, "bacot": 8,
        },
        "default_hook": "KENA TROLL BOCIL LIVE CHAT?!",
        "category_label": "funny",
    },
    "action_chase": {
        "keywords": {
            "polisi": 9, "ketahuan": 9, "dikejar": 10, "kabur": 8, "ketangkep": 10,
            "busted": 10, "lolos": 9, "cair": 9, "rampok": 8, "ngerampok": 9,
            "nyolong": 8, "senjata": 7, "tembak": 8, "diserang": 10, "mekanik gocek": 11,
            "gocek": 8, "motor dewa": 9, "respray": 7, "wanted": 8, "bintang": 6,
            "perang": 8, "clutch": 10,
        },
        "default_hook": "DETIK-DETIK PANIK DIKEJAR!",
        "category_label": "clutch",
    },
    "shock_panic": {
        "keywords": {
            "anjir": 7, "anjay": 6, "astaga": 8, "buset": 8, "gila": 6, "parah": 6,
            "sumpah": 7, "kaget": 9, "mati": 7, "meledak": 9, "tabrak": 8, "mampus": 9,
            "wasted": 10, "ya tuhan": 8, "ya ampun": 8, "tolong": 8, "awas": 7,
            "panik": 10, "bahaya": 7, "kok bisa": 8, "apa itu": 7, "jumpscare": 10,
            "setan": 8, "hantu": 8, "misteri": 6, "cabut": 7, "keder": 8,
        },
        "default_hook": "PANIK GAK KETULUNGAN!",
        "category_label": "reaction",
    },
    "comedy_absurd": {
        "keywords": {
            "kocak": 9, "ngakak": 9, "wkwk": 8, "haha": 6, "lucu": 7, "absurd": 9,
            "random": 7, "nyangkut": 8, "bug": 7, "glitch": 7, "solaria": 8,
            "nasi padang": 8, "password": 7, "yolo": 7,
        },
        "default_hook": "MOMEN PALING ABSURD & KOCAK!",
        "category_label": "funny",
    },
    "podcast_insight": {
        "keywords": {
            "rahasia": 9, "jujur": 7, "ternyata": 8, "fakta": 8, "bongkar": 9,
            "kontroversi": 9, "kesalahan": 8, "jangan pernah": 9, "miliar": 9,
            "juta": 6, "gaji": 8, "debat": 8, "bahaya": 7, "alasan": 6, "mindset": 8,
        },
        "default_hook": "FAKTA YANG JARANG ORANG TAHU!",
        "category_label": "insight",
    },
}

BORING_FILLER_PHRASES = [
    "sekalian jalan-jalan",
    "gak usah cepat-cepat",
    "tamatnya 5 tahun lagi",
    "adjustnya dulu",
    "setting dulu",
    "cek suara",
    "tes mic",
    "tunggu bentar",
    "kita gym dulu",
    "baru seadanya sih",
]


def _extract_z_timeline(audio_spikes_or_data: Any, duration: float) -> np.ndarray:
    """Reconstruct 1-second Z-score array from audio_result dict or spike list."""
    n_sec = max(60, int(duration) + 5)
    z_arr = np.zeros(n_sec, dtype=np.float32)

    if isinstance(audio_spikes_or_data, dict):
        raw_1s = audio_spikes_or_data.get("z_timeline_1s")
        if raw_1s and len(raw_1s) >= 10:
            return np.array(raw_1s, dtype=np.float32)
        spikes = audio_spikes_or_data.get("spikes") or []
    elif isinstance(audio_spikes_or_data, list):
        spikes = audio_spikes_or_data
    else:
        spikes = []

    for sp in spikes:
        t = int(float(sp.get("timestamp", 0)))
        z = float(sp.get("sustained_z") or sp.get("z_score") or 1.8)
        for dt in range(-4, 7):
            idx = t + dt
            if 0 <= idx < n_sec:
                decay = 1.0 - (abs(dt) / 8.0)
                z_arr[idx] = max(z_arr[idx], z * decay)
    return z_arr


def _score_segment_hook_power(text: str) -> float:
    """Score how strong a single transcript segment is as the 0:00 opening hook line."""
    low = text.lower().strip()
    if not low or len(low) < 6:
        return 0.0
    score = 0.0
    for cat_data in VIRAL_CATEGORIES.values():
        for kw, pts in cat_data["keywords"].items():
            if kw in low:
                score += pts * 1.4
    if "?" in text or "!" in text:
        score += 6.0
    # Repetition inside the opening line (e.g. "Polisi! Polisi!", "Cabut cabut!")
    if re.search(r"\b(\w{3,})\s+\1\b", low):
        score += 8.0
    # Penalize weak connector starts
    if low.startswith(("terus ", "oke di sini", "jadi ", "nah ", "eh ", "hmm", "butuh.", "nanti ")):
        score -= 5.0
    return score


def _craft_dynamic_hook_and_title(
    cat_key: str,
    window_segs: List[Dict[str, Any]],
    matched_keywords: List[str],
) -> Tuple[str, str, str]:
    """
    Generate a scroll-stopping Upper Hook Banner, Viral Clip Title, and Social Caption
    based on the actual trigger phrases inside the chosen clip!
    """
    full_text = " ".join(str(s.get("text", "")).strip() for s in window_segs).strip()
    low = full_text.lower()

    # Custom high-precision hook banner rules for iconic moments
    if "dolar kuning" in low or ("password" in low and "bocil" in low):
        hook_banner = "KENA TROLL BOCIL DOLAR KUNING?!"
        title_prefix = "Ngakak! Kena Prank Bocil Live Chat:"
    elif "greget" in low or "gereget" in low or "main sendiri" in low:
        hook_banner = "EMOSI ROASTING BOCIL GREGET!"
        title_prefix = "Auto Emosi Baca Komen Bocil Greget:"
    elif ("kosmetik" in low or "cosmetik" in low or "respray" in low) and "bocil" in low:
        hook_banner = "DEBAT PANAS VS BOCIL LIVE CHAT!"
        title_prefix = "Momen Kocak Debat Sama Bocil Chat:"
    elif "diserang" in low or "mekanik gocek" in low or "parkir situ" in low:
        hook_banner = "PANIK TIBA-TIBA DISERANG?!"
        title_prefix = "Panik Diserang & Mekanik Gocek:"
    elif "polisi" in low and ("ketahuan" in low or "lolos" in low or "motor" in low):
        hook_banner = "DETIK-DETIK LOLOS DARI POLISI!"
        title_prefix = "Tegang! Panik Dikejar Polisi:"
    elif "scam" in low or "hiu" in low or "caper" in low:
        hook_banner = "KENA SCAM BOCIL LIVE CHAT?!"
        title_prefix = "Kena Jebakan Batman Bocil Chat:"
    elif "solaria" in low or "nasi padang" in low or "paela" in low:
        hook_banner = "MOMEN RANDOM PALING ABSURD!"
        title_prefix = "Reaksi Absurd Paling Ngakak:"
    else:
        hook_banner = VIRAL_CATEGORIES.get(cat_key, VIRAL_CATEGORIES["shock_panic"])["default_hook"]
        title_prefix = "Momen Paling Pecah:"

    # Pick the punchiest sentence from the clip to quote in the title
    best_seg_text = ""
    best_seg_sc = -999.0
    for s in window_segs[:8]:
        stxt = str(s.get("text", "")).strip()
        sc = _score_segment_hook_power(stxt)
        if len(stxt) >= 12 and sc > best_seg_sc:
            best_seg_sc = sc
            best_seg_text = stxt

    if not best_seg_text and window_segs:
        best_seg_text = str(window_segs[0].get("text", "")).strip()

    clean_quote = re.sub(r"\s+", " ", best_seg_text).strip(" .,")
    if len(clean_quote) > 36:
        clean_quote = clean_quote[:34].rstrip() + "..."

    viral_title = f'{title_prefix} "{clean_quote}"'
    if len(viral_title) > 64:
        viral_title = viral_title[:61] + '..."'

    caption = (
        f'{hook_banner} 🔥 {viral_title} — Bagian paling pecah di menit ini! '
        f'Menurut kalian gimana? Tulis di kolom komentar! 👇'
    )
    return hook_banner, viral_title, caption


def score_multimodal_viral_windows(
    segments: List[Dict[str, Any]],
    audio_spikes: Any,
    chat_spikes: List[Dict[str, Any]],
    duration: float,
    clip_count: int = 5,
    min_duration: int = 25,
    max_duration: int = 50,
    content_type: str = "auto",
) -> List[HighlightCandidate]:
    """
    Slide a window across the entire stream, fuse Sustained Audio Z-Score + Viral Lexicon +
    Repetition + Opening Hook Strength + Live Chat Spikes, and snap start_time directly
    to the exact hook sentence so second 0:00 is immediately exciting!
    """
    target_dur = min(max_duration, max(min_duration, 36))
    n_sec = max(int(duration), int(segments[-1]["end"]) if segments else 120)
    z_1s = _extract_z_timeline(audio_spikes, float(n_sec))
    if z_1s.size < n_sec:
        z_1s = np.pad(z_1s, (0, n_sec - z_1s.size))

    step = 4 if n_sec > 1800 else 2
    raw_windows: List[Dict[str, Any]] = []

    for t0 in range(15, max(20, n_sec - target_dur - 5), step):
        t1 = t0 + target_dur
        wz = z_1s[t0:t1]
        if wz.size == 0:
            continue

        max_z = float(np.max(wz))
        top6_z = float(np.mean(np.sort(wz)[-min(6, wz.size):]))
        active_secs = int(np.sum(wz > 0.95))
        open_z = float(np.max(wz[:min(10, wz.size)]))

        w_segs = [s for s in segments if float(s.get("end", 0)) > t0 and float(s.get("start", 0)) < t1]
        if len(w_segs) < 3:
            continue

        full_txt = " ".join(str(s.get("text", "")) for s in w_segs).strip()
        low = full_txt.lower()
        if any(bp in low for bp in BORING_FILLER_PHRASES):
            continue

        word_cnt = len(low.split())
        wps = word_cnt / max(1.0, float(target_dur))
        if wps < 1.3:
            continue

        # Score across all viral categories
        cat_scores: Dict[str, float] = {}
        matched_kws: List[str] = []
        open_txt = " ".join(str(s.get("text", "")) for s in w_segs[:3]).lower()

        for cat_key, cat_data in VIRAL_CATEGORIES.items():
            sc = 0.0
            for kw, pts in cat_data["keywords"].items():
                cnt = low.count(kw)
                if cnt > 0:
                    sc += pts * min(3, cnt)
                    matched_kws.append(kw)
                    if kw in open_txt:
                        sc += pts * 0.75  # Strong opening hook bonus!
            cat_scores[cat_key] = sc

        best_cat = max(cat_scores, key=cat_scores.get)
        lex_total = sum(cat_scores.values())

        # Repetition & Exclamation burst bonus
        reps = re.findall(r"\b(\w{3,})\s+\1\b", low)
        rep_pts = min(18.0, len(reps) * 5.0)

        # Live chat explosion correlation (viewer reaction lags 0..20s after action)
        chat_pts = 0.0
        chat_msg_cnt = 0
        for cs in chat_spikes:
            ct = float(cs.get("timestamp", 0))
            if (t0 - 5) <= ct <= (t1 + 20):
                c_sc = min(24.0, float(cs.get("intensity", 70)) * 0.24)
                if c_sc > chat_pts:
                    chat_pts = c_sc
                    chat_msg_cnt = int(cs.get("message_count", 0))

        # Require real excitement (either strong lexical + vocal energy, or strong chat + vocal energy)
        if lex_total < 14.0 and chat_pts < 12.0 and top6_z < 1.6:
            continue
        if max_z < 1.35 and chat_pts < 15.0:
            continue

        audio_pts = (top6_z * 11.5) + (max_z * 4.5) + (open_z * 3.5) + min(10.0, active_secs * 0.8)
        raw_score = audio_pts + (lex_total * 0.78) + rep_pts + chat_pts

        raw_windows.append({
            "t0": float(t0),
            "t1": float(t1),
            "raw_score": raw_score,
            "audio_pts": audio_pts,
            "lex_pts": lex_total,
            "rep_pts": rep_pts,
            "chat_pts": chat_pts,
            "chat_msg_cnt": chat_msg_cnt,
            "max_z": max_z,
            "top6_z": top6_z,
            "active_secs": active_secs,
            "best_cat": best_cat,
            "matched_kws": list(dict.fromkeys(matched_kws))[:6],
            "w_segs": w_segs,
        })

    raw_windows.sort(key=lambda x: x["raw_score"], reverse=True)

    # Deduplicate non-overlapping windows (at least 90s apart on long streams)
    min_sep = 90.0 if n_sec > 1800 else 35.0
    chosen_windows: List[Dict[str, Any]] = []
    for win in raw_windows:
        if any(abs(win["t0"] - c["t0"]) < min_sep for c in chosen_windows):
            continue
        chosen_windows.append(win)
        if len(chosen_windows) >= clip_count:
            break

    # Fallback if video has very sparse speech/captions
    if not chosen_windows and duration > 0:
        step_fb = max(target_dur + 10, duration / (clip_count + 1))
        for i in range(clip_count):
            st = min(max(0.0, duration - target_dur), (i + 1) * step_fb)
            et = min(duration, st + target_dur)
            w_segs = [s for s in segments if float(s.get("end", 0)) > st and float(s.get("start", 0)) < et]
            chosen_windows.append({
                "t0": st,
                "t1": et,
                "raw_score": 65.0 - i * 3,
                "audio_pts": 20.0,
                "lex_pts": 10.0,
                "rep_pts": 0.0,
                "chat_pts": 0.0,
                "chat_msg_cnt": 0,
                "max_z": 1.5,
                "top6_z": 1.2,
                "active_secs": 5,
                "best_cat": "shock_panic",
                "matched_kws": [],
                "w_segs": w_segs,
            })

    # Convert to HighlightCandidate with Smart Hook-Sentence Start Snapping!
    results: List[HighlightCandidate] = []
    max_raw = chosen_windows[0]["raw_score"] if chosen_windows else 100.0

    for rank_idx, win in enumerate(chosen_windows):
        w_segs = win["w_segs"]
        st = win["t0"]
        et = win["t1"]

        # SMART HOOK-SENTENCE SNAPPING:
        # Look among the first 4 segments in the window and snap start_time to the first strong hook segment
        # so the viewer hears an exciting hook at 0:00 instead of dead air or filler!
        if len(w_segs) >= 3:
            best_hook_idx = 0
            best_hook_sc = -999.0
            for idx_s, seg in enumerate(w_segs[:4]):
                seg_st = float(seg.get("start", st))
                if et - seg_st < min_duration:
                    break
                sc = _score_segment_hook_power(str(seg.get("text", ""))) - (idx_s * 1.5)
                if sc > best_hook_sc:
                    best_hook_sc = sc
                    best_hook_idx = idx_s
            snapped_start = float(w_segs[best_hook_idx].get("start", st))
            if snapped_start >= st - 2.0 and (et - snapped_start) >= min_duration - 2:
                st = max(0.0, snapped_start - 0.25)
                w_segs = w_segs[best_hook_idx:]

        # Extend end_time to keep target duration & snap to clean sentence end
        desired_end = min(float(n_sec), st + target_dur)
        end_cands = [
            float(s.get("end", desired_end))
            for s in segments
            if abs(float(s.get("end", 0)) - desired_end) <= 5.0 and (float(s.get("end", 0)) - st) >= min_duration
        ]
        et = min(end_cands, key=lambda x: abs(x - desired_end)) if end_cands else desired_end

        hook_banner, viral_title, caption = _craft_dynamic_hook_and_title(
            win["best_cat"], w_segs, win["matched_kws"]
        )

        # Calibrated virality score (76..97) reflecting true relative strength
        rel_ratio = min(1.0, win["raw_score"] / max(85.0, max_raw))
        calibrated_score = min(97, max(74, int(round(75 + rel_ratio * 21 - rank_idx * 1.5))))

        kws_str = ", ".join(f'"{k}"' for k in win["matched_kws"][:4]) or "dialog intens"
        chat_note = f" • 💬 Ledakan Live Chat ({win['chat_msg_cnt']} pesan)" if win["chat_msg_cnt"] > 0 else ""
        reason = (
            f"🔥 Hook Kuat di 0:00 ({kws_str}) • "
            f"🔊 Audio Hype Z={win['max_z']:.2f} ({win['active_secs']} dtk teriakan/emosi aktif)"
            f"{chat_note}"
        )

        cat_label = VIRAL_CATEGORIES.get(win["best_cat"], {}).get("category_label", "highlight")
        hashtags = (
            ["gamingindonesia", "windahbasudara", "gtavi", "streamer", "kocak", "fyp", "reels"]
            if content_type != "podcast"
            else ["podcastindonesia", "clips", "viral", "fyp", "reels", "edukasi"]
        )

        results.append(
            HighlightCandidate(
                start_time=round(st, 2),
                end_time=round(et, 2),
                title=viral_title,
                hook_text=hook_banner,
                caption=caption,
                hashtags=hashtags,
                virality_score=calibrated_score,
                category=cat_label,
                reason=reason,
            )
        )

    return results


def _format_transcript_for_llm(segments: List[Dict[str, Any]], max_chars: int = 110_000) -> str:
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


def find_viral_highlights(
    segments: List[Dict[str, Any]],
    audio_spikes: Any,
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
    Multi-Factor Viral Director V2:
    1. Always runs the Multi-Factor Viral & Hook Scoring Engine first to discover
       the highest-intensity acoustic + lexical + chat windows with 0:00 hook snapping.
    2. If Gemini API Key is configured, passes those top pre-scored candidate moments +
       transcript to Gemini 4 Hydrogen (Interactions API) for even sharper editorial polish.
    """
    detected_type = (
        content_type
        if content_type in ("podcast", "gaming")
        else ("gaming" if chat_spikes or "game" in video_title.lower() or "gta" in video_title.lower() else "podcast")
    )

    pre_scored_clips = score_multimodal_viral_windows(
        segments=segments,
        audio_spikes=audio_spikes,
        chat_spikes=chat_spikes,
        duration=duration,
        clip_count=max(clip_count, 6),
        min_duration=min_duration,
        max_duration=max_duration,
        content_type=detected_type,
    )

    if not gemini_api_key or not gemini_api_key.strip():
        return HighlightPlan(
            detected_content_type=detected_type,
            summary="Momen viral dipilih menggunakan Multi-Factor Viral & Hook Engine V2 (Sustained Audio Hype + Deteksi Emosi/Roast/Action + Hook 0:00 Snapping + Live Chat).",
            clips=pre_scored_clips[:clip_count],
        )

    pre_candidates_json = json.dumps(
        [c.model_dump() for c in pre_scored_clips[:10]], ensure_ascii=False, indent=2
    )
    transcript_text = _format_transcript_for_llm(segments)

    prompt = f"""Kamu adalah Senior Viral TikTok & Instagram Reels Editor untuk streamer dan podcaster top Indonesia (seperti Windah Basudara, Deddy Corbuzier, MiawAug).

Pilih **{clip_count} KLIP PALING PECAH, LUCU, EMOSI, ATAU TEGANG** dari video berikut:
- Judul Video: {video_title}
- Total Durasi: {duration:.1f} detik

=== TOP KANDIDAT HASIL ANALISIS MULTIMODAL (AUDIO TERIAKAN + EMOSI DIALOG + LIVE CHAT) ===
{pre_candidates_json}

=== TRANSKRIP LENGKAP ===
{transcript_text[:95000]}

=== ATURAN WAJIB VIRAL & HOOK 0:00 ===
1. **DETIK 0:00 WAJIB LANGSUNG HOOK!** Jangan pernah memulai klip dari kalimat basa-basi atau jalan santai. `start_time` harus tepat di kalimat pemicu emosi/roasting/panik/pertanyaan mengejutkan!
2. Prioritaskan momen:
   - Streamer kena troll/prank bocil live chat, emosi roasting komentar lucu, dolar kuning, atau debat kocak.
   - Momen panik dikejar polisi, diserang mendadak, mekanik gocek, atau clutch lolos di detik terakhir.
   - Jangan pilih segmen jalan-jalan biasa, setting game, atau obrolan datar.
3. Buat `hook_text` (3-5 kata HURUF KAPITAL yang spesifik & bikin penasaran untuk banner atas video, JANGAN gunakan kalimat generik "TONTON SAMPAI HABIS!").
4. Buat `title` yang mengutip punchline paling lucu/tegang di klip tersebut.
5. Durasi tiap klip wajib antara {min_duration} sampai {max_duration} detik dan tidak tumpang tindih.
"""

    try:
        client = genai.Client(api_key=gemini_api_key.strip())
        interaction = client.interactions.create(
            model=gemini_model or "gemini-4-hydrogen",
            input=prompt,
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": HighlightPlan.model_json_schema(),
            },
        )
        plan = HighlightPlan.model_validate_json(interaction.output_text)
        valid_clips: List[HighlightCandidate] = []
        for c in plan.clips:
            st = max(0.0, float(c.start_time))
            et = min(duration if duration > 0 else float(c.end_time), float(c.end_time))
            if et - st >= 12.0:
                c.start_time = round(st, 2)
                c.end_time = round(et, 2)
                valid_clips.append(c)
        if valid_clips:
            plan.clips = sorted(valid_clips, key=lambda x: x.virality_score, reverse=True)[:clip_count]
            return plan
    except Exception as exc:
        print(f"[AI Director V2] Fallback ke Multi-Factor Viral Engine V2: {exc}")

    return HighlightPlan(
        detected_content_type=detected_type,
        summary="Momen viral dipilih menggunakan Multi-Factor Viral & Hook Engine V2.",
        clips=pre_scored_clips[:clip_count],
    )
