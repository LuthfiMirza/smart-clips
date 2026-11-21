"""Multi-Engine Speech-to-Text Transcription Service with Word-Level Timestamps.
Supports:
  1. YouTube Native JSON3 / VTT Subtitles (instant, free, includes word-level offsets in json3!)
  2. Google Gemini 3.5 Transcribe (gemini-3.5-transcribe via v1beta Interactions API)
  3. Local Faster-Whisper (optional, if installed)
"""
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


def _interpolate_words(text: str, start: float, end: float) -> List[Dict[str, Any]]:
    """Split a sentence segment cleanly into evenly paced word-level timestamps."""
    tokens = [t for t in re.split(r"\s+", text.strip()) if t]
    if not tokens:
        return []
    dur = max(0.2, end - start)
    total_chars = sum(max(1, len(t)) for t in tokens)
    words = []
    cur = start
    for tok in tokens:
        w_dur = dur * (max(1, len(tok)) / total_chars)
        words.append({
            "word": tok,
            "start": round(cur, 3),
            "end": round(cur + w_dur, 3),
        })
        cur += w_dur
    return words


def parse_json3_subtitles(json3_path: Path) -> Dict[str, Any]:
    """Parse YouTube's json3 caption format which contains exact word-level offsets (tOffsetMs)."""
    data = json.loads(json3_path.read_text(encoding="utf-8", errors="ignore"))
    events = data.get("events") or []
    segments: List[Dict[str, Any]] = []
    words: List[Dict[str, Any]] = []

    for ev in events:
        t_start_ms = ev.get("tStartMs")
        d_dur_ms = ev.get("dDurationMs", 2000)
        segs = ev.get("segs") or []
        if t_start_ms is None or not segs:
            continue

        seg_start = float(t_start_ms) / 1000.0
        seg_end = seg_start + max(0.3, float(d_dur_ms) / 1000.0)
        seg_text_parts = []
        ev_words = []

        for idx, s in enumerate(segs):
            txt = str(s.get("utf8", "")).replace("\n", " ").strip()
            if not txt:
                continue
            seg_text_parts.append(txt)
            w_offset_ms = s.get("tOffsetMs")
            if w_offset_ms is not None:
                w_start = seg_start + float(w_offset_ms) / 1000.0
                # End is next word start or seg_end
                next_offset = None
                for nxt in segs[idx + 1:]:
                    if nxt.get("tOffsetMs") is not None and str(nxt.get("utf8", "")).strip():
                        next_offset = float(nxt["tOffsetMs"]) / 1000.0
                        break
                w_end = (seg_start + next_offset) if next_offset is not None else min(seg_end, w_start + 0.45)
                for sub_tok in txt.split():
                    ev_words.append({
                        "word": sub_tok,
                        "start": round(w_start, 3),
                        "end": round(max(w_start + 0.08, w_end), 3),
                    })

        full_text = " ".join(seg_text_parts).strip()
        if not full_text:
            continue

        segments.append({
            "start": round(seg_start, 2),
            "end": round(seg_end, 2),
            "text": full_text,
        })
        if ev_words:
            words.extend(ev_words)
        else:
            words.extend(_interpolate_words(full_text, seg_start, seg_end))

    return {"engine": "youtube_json3", "segments": segments, "words": words}


def _vtt_ts_to_sec(ts: str) -> float:
    ts = ts.strip().replace(",", ".")
    parts = ts.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return float(parts[0])


def parse_vtt_subtitles(vtt_path: Path) -> Dict[str, Any]:
    """Parse WebVTT / SRT subtitle files into segments + word-level timestamps."""
    raw = vtt_path.read_text(encoding="utf-8", errors="ignore")
    blocks = re.split(r"\r?\n\r?\n", raw)
    segments: List[Dict[str, Any]] = []
    words: List[Dict[str, Any]] = []
    seen_texts = set()

    ts_re = re.compile(r"(\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3})\s*-->\s*(\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3})")

    for block in blocks:
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        ts_line_idx = -1
        m = None
        for i, l in enumerate(lines):
            m = ts_re.search(l)
            if m:
                ts_line_idx = i
                break
        if ts_line_idx < 0 or not m:
            continue

        start = _vtt_ts_to_sec(m.group(1))
        end = _vtt_ts_to_sec(m.group(2))
        text_lines = lines[ts_line_idx + 1:]
        # Strip VTT inline tags like <c>, <00:00:01.200>
        cleaned = re.sub(r"<[^>]+>", "", " ".join(text_lines))
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if not cleaned:
            continue

        # Deduplicate rolling YouTube auto-caption lines
        dedup_key = (round(start, 1), cleaned.lower())
        if dedup_key in seen_texts:
            continue
        seen_texts.add(dedup_key)

        segments.append({"start": round(start, 2), "end": round(end, 2), "text": cleaned})
        words.extend(_interpolate_words(cleaned, start, end))

    return {"engine": "youtube_vtt", "segments": segments, "words": words}


def _transcribe_with_gemini(audio_path: Path, gemini_api_key: str) -> Dict[str, Any]:
    """
    Transcribe audio using Google Gemini 3.5 Transcribe (gemini-3.5-transcribe)
    via v1beta Interactions API with word-level timestamps.
    """
    from google import genai
    from backend.services.youtube_ingestor import get_media_duration

    client = genai.Client(api_key=gemini_api_key.strip(), http_options={"api_version": "v1beta"})
    uploaded = client.files.upload(file=str(audio_path))
    try:
        response = client.interactions.create(
            model="gemini-3.5-transcribe",
            input=[uploaded],
            generation_config={
                "transcription_config": {
                    "mode": {
                        "type": "verbatim",
                        "timestamp_granularities": ["word"],
                    }
                }
            },
        )
    finally:
        try:
            client.files.delete(name=uploaded.name)
        except Exception:
            pass

    words: List[Dict[str, Any]] = []
    segments: List[Dict[str, Any]] = []

    # Try extracting structured word-level timestamps from interaction steps/annotations
    try:
        steps = getattr(response, "steps", None) or []
        for step in steps:
            contents = getattr(step, "content", None) or []
            for item in contents:
                annotations = getattr(item, "annotations", None) or []
                for ann in annotations:
                    w_text = getattr(ann, "word", None) or getattr(ann, "text", None)
                    w_start = getattr(ann, "start_time", None) or getattr(ann, "start_offset", None)
                    w_end = getattr(ann, "end_time", None) or getattr(ann, "end_offset", None)
                    if w_text and w_start is not None:
                        s_val = float(str(w_start).rstrip("s"))
                        e_val = float(str(w_end).rstrip("s")) if w_end is not None else s_val + 0.3
                        words.append({"word": str(w_text).strip(), "start": round(s_val, 3), "end": round(e_val, 3)})
    except Exception:
        pass

    output_text = (getattr(response, "output_text", None) or "").strip()

    # Check if output_text itself contains timestamped JSON or [mm:ss] format
    if not words and output_text:
        duration = get_media_duration(audio_path) or 60.0
        # Split into sentences and distribute evenly across audio duration
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", output_text) if s.strip()]
        if not sentences:
            sentences = [output_text]
        total_chars = sum(max(1, len(s)) for s in sentences)
        cur = 0.0
        for sent in sentences:
            s_dur = duration * (max(1, len(sent)) / total_chars)
            segments.append({"start": round(cur, 2), "end": round(cur + s_dur, 2), "text": sent})
            words.extend(_interpolate_words(sent, cur, cur + s_dur))
            cur += s_dur
    elif words:
        # Group word timestamps into readable ~5-second segments
        cur_seg_words = []
        for w in words:
            cur_seg_words.append(w)
            if len(cur_seg_words) >= 14 or str(w["word"]).endswith((".", "!", "?")):
                segments.append({
                    "start": round(cur_seg_words[0]["start"], 2),
                    "end": round(cur_seg_words[-1]["end"], 2),
                    "text": " ".join(x["word"] for x in cur_seg_words),
                })
                cur_seg_words = []
        if cur_seg_words:
            segments.append({
                "start": round(cur_seg_words[0]["start"], 2),
                "end": round(cur_seg_words[-1]["end"], 2),
                "text": " ".join(x["word"] for x in cur_seg_words),
            })

    return {"engine": "gemini-3.5-transcribe", "segments": segments, "words": words}


def _transcribe_with_faster_whisper(audio_path: Path, model_size: str = "base") -> Dict[str, Any]:
    """Optional local transcription using faster-whisper if installed."""
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    raw_segments, _info = model.transcribe(str(audio_path), word_timestamps=True, vad_filter=True)
    segments: List[Dict[str, Any]] = []
    words: List[Dict[str, Any]] = []

    for seg in raw_segments:
        txt = seg.text.strip()
        if not txt:
            continue
        segments.append({"start": round(seg.start, 2), "end": round(seg.end, 2), "text": txt})
        if seg.words:
            for w in seg.words:
                words.append({"word": w.word.strip(), "start": round(w.start, 3), "end": round(w.end, 3)})
        else:
            words.extend(_interpolate_words(txt, seg.start, seg.end))

    return {"engine": f"faster-whisper-{model_size}", "segments": segments, "words": words}


def _pick_best_subtitle_file(subtitle_files: List[Path]) -> Optional[Path]:
    if not subtitle_files:
        return None
    # Prefer Indonesian json3 -> English json3 -> any json3 -> Indonesian vtt -> any vtt
    def score(p: Path) -> int:
        n = p.name.lower()
        sc = 0
        if p.suffix.lower() == ".json3":
            sc += 50
        if ".id." in n:
            sc += 25
        elif ".en." in n:
            sc += 15
        return sc

    return sorted(subtitle_files, key=score, reverse=True)[0]


def transcribe_audio(
    audio_path: Path,
    subtitle_files: Optional[List[Path]] = None,
    gemini_api_key: str = "",
    engine: str = "auto",
    whisper_model_size: str = "base",
) -> Dict[str, Any]:
    """
    Unified transcription router:
      - 'auto': Prefer YouTube JSON3 (instant + word-level timing) -> Gemini 3.5 Transcribe -> Faster-Whisper -> VTT
      - 'gemini': Gemini 3.5 Transcribe -> fallback to YouTube subs
      - 'whisper': Faster-Whisper local -> fallback to YouTube subs
      - 'youtube_subs': YouTube JSON3/VTT only
    """
    sub_files = subtitle_files or []
    best_sub = _pick_best_subtitle_file(sub_files)

    def _try_yt_subs() -> Optional[Dict[str, Any]]:
        if not best_sub or not best_sub.exists():
            return None
        try:
            res = parse_json3_subtitles(best_sub) if best_sub.suffix.lower() == ".json3" else parse_vtt_subtitles(best_sub)
            if res.get("segments"):
                return res
        except Exception as exc:
            print(f"[Transcriber] YouTube subtitle parse warning: {exc}")
        return None

    # 1. Explicit YouTube subs mode
    if engine == "youtube_subs":
        res = _try_yt_subs()
        return res or {"engine": "none", "segments": [], "words": []}

    # 2. Auto mode: if YouTube JSON3 exists, it is instantaneous and accurate!
    if engine == "auto" and best_sub and best_sub.suffix.lower() == ".json3":
        res = _try_yt_subs()
        if res and len(res.get("words", [])) >= 15:
            return res

    # 3. Gemini 3.5 Transcribe (if requested or auto with API key)
    if engine in ("auto", "gemini") and gemini_api_key.strip():
        try:
            res = _transcribe_with_gemini(audio_path, gemini_api_key)
            if res.get("segments"):
                return res
        except Exception as exc:
            print(f"[Transcriber] Gemini 3.5 Transcribe fallback: {exc}")

    # 4. Local Faster-Whisper (if installed)
    if engine in ("auto", "whisper"):
        try:
            res = _transcribe_with_faster_whisper(audio_path, whisper_model_size)
            if res.get("segments"):
                return res
        except ImportError:
            pass
        except Exception as exc:
            print(f"[Transcriber] Faster-Whisper fallback: {exc}")

    # 5. Final fallback to any YouTube subtitle file (VTT/SRT/JSON3)
    res = _try_yt_subs()
    if res:
        return res

    return {"engine": "none", "segments": [], "words": []}
