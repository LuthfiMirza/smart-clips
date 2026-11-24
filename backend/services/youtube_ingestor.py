"""YouTube Ingestion Service:
- Live progress streaming (yt-dlp --newline --progress)
- Separated non-fatal Subtitle (json3/vtt) & Live Chat JSON download
- Turbo Long-VOD Mode: lightweight audio-only download + targeted --download-sections per clip!
- Standard VOD download for short videos (<= 20 min)
- Real-Time Rolling Chunk Recording for active YouTube Live streams
"""
import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.config import DOWNLOADS_DIR, PREVIEWS_DIR

HYPE_KEYWORDS = {
    "wkwk", "wkwkwk", "haha", "hahaha", "ngakak", "kocak", "anjir", "anjay",
    "gila", "parah", "gg", "clutch", "wtf", "omg", "lol", "lmao", "bro",
    "mantap", "gokil", "buset", "astaga", "ya ampun", "no way", "insane",
    "epic", "nice", "kill", "ace", "pentakill", "savage", "maniac", "kaget",
    "serem", "jumpscare", "🔥", "😂", "💀", "🤣", "😱", "🤯", "👏",
}

_PCT_RE = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%(?:\s+of\s+\S+)?(?:\s+at\s+(\S+))?(?:\s+ETA\s+(\S+))?")


def _yt_dlp_bin() -> str:
    venv_bin = Path(__file__).resolve().parent.parent.parent / ".venv" / "bin" / "yt-dlp"
    if venv_bin.exists():
        return str(venv_bin)
    return "yt-dlp"


def extract_video_id(url: str) -> str:
    patterns = [
        r"(?:v=|/v/|youtu\.be/|/live/|/shorts/)([A-Za-z0-9_-]{11})",
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return re.sub(r"[^A-Za-z0-9_-]", "", url)[-11:] or "video"


def inspect_youtube_url(url: str) -> Dict[str, Any]:
    """Inspect a YouTube URL (VOD, Shorts, or Active Live Stream) without downloading."""
    cmd = [
        _yt_dlp_bin(),
        "--dump-single-json",
        "--no-warnings",
        "--no-playlist",
        "--skip-download",
        url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "Gagal membaca URL YouTube").strip()
        raise RuntimeError(f"yt-dlp inspect gagal: {err[-400:]}")

    info = json.loads(proc.stdout)
    is_live = bool(info.get("is_live"))
    was_live = bool(info.get("was_live"))
    live_status = info.get("live_status") or ("is_live" if is_live else ("was_live" if was_live else "not_live"))
    duration = float(info.get("duration") or 0)

    title_lower = (info.get("title") or "").lower()
    categories = [c.lower() for c in (info.get("categories") or [])]
    tags_str = " ".join(info.get("tags") or []).lower()
    combined_text = f"{title_lower} {' '.join(categories)} {tags_str}"

    gaming_signals = [
        "gaming", "gameplay", "mobile legends", "mlbb", "valorant", "pubg",
        "dota", "gta", "minecraft", "roblox", "free fire", "stream", "mabar",
        "ranked", "esports", "horror game", "react", "tamat",
    ]
    podcast_signals = [
        "podcast", "obrolan", "wawancara", "interview", "talkshow", "diskusi",
        "curhat", "bincang", "close the door", "deddy", "pwk", "debat",
    ]

    suggested_mode = "podcast"
    if any(k in combined_text for k in gaming_signals) or "gaming" in categories:
        suggested_mode = "gaming_split"
    elif any(k in combined_text for k in podcast_signals):
        suggested_mode = "podcast"
    elif is_live or was_live:
        suggested_mode = "gaming_split"

    subtitles = list((info.get("subtitles") or {}).keys())
    auto_captions = list((info.get("automatic_captions") or {}).keys())
    has_live_chat = "live_chat" in subtitles

    return {
        "video_id": info.get("id") or extract_video_id(url),
        "title": info.get("title") or "YouTube Video",
        "channel": info.get("uploader") or info.get("channel") or "Unknown Channel",
        "thumbnail_url": info.get("thumbnail") or "",
        "duration": duration,
        "is_live": is_live,
        "was_live": was_live,
        "live_status": live_status,
        "view_count": info.get("view_count") or 0,
        "has_live_chat": has_live_chat,
        "has_subtitles": bool(subtitles or auto_captions),
        "turbo_recommended": duration > 1200,
        "suggested_layout": suggested_mode,
        "webpage_url": info.get("webpage_url") or url,
    }


def _run_ytdlp_with_progress(
    cmd: List[str],
    progress_cb: Optional[Callable[[int, str], None]] = None,
    pct_start: int = 10,
    pct_end: int = 30,
    label: str = "Mengunduh media",
    timeout: int = 1200,
) -> Tuple[int, str]:
    """Run yt-dlp streaming line-by-line so the UI receives real-time % and speed updates."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    tail_lines: List[str] = []
    last_reported = -1

    try:
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            line = raw_line.strip()
            if not line:
                continue
            tail_lines.append(line)
            if len(tail_lines) > 30:
                tail_lines.pop(0)

            m = _PCT_RE.search(line)
            if m and progress_cb:
                dl_pct = float(m.group(1))
                speed = m.group(2) or ""
                eta = m.group(3) or ""
                mapped = pct_start + int((dl_pct / 100.0) * max(1, pct_end - pct_start))
                if mapped != last_reported or dl_pct >= 99.5:
                    last_reported = mapped
                    extra = f" ({speed}, sisa {eta})" if speed and eta else (f" ({speed})" if speed else "")
                    progress_cb(mapped, f"{label}... {dl_pct:.1f}%{extra}")
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        return 124, "Timeout saat mengunduh dari YouTube"

    return proc.returncode, "\n".join(tail_lines[-10:])


def extract_preview_frame(video_path: Path, project_id: str, timestamp: float = 10.0) -> Optional[Path]:
    """Extract a representative 16:9 frame JPG so the user can visually pick the Facecam crop."""
    out_path = PREVIEWS_DIR / f"{project_id}_preview.jpg"
    probe_dur = get_media_duration(video_path)
    ts = min(timestamp, max(0.5, probe_dur * 0.25)) if probe_dur > 0 else 1.5

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{ts:.2f}",
        "-i", str(video_path),
        "-vframes", "1",
        "-q:v", "3",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if proc.returncode == 0 and out_path.exists():
        return out_path
    return None


def get_media_duration(media_path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(media_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    try:
        return float(proc.stdout.strip())
    except Exception:
        return 0.0


def get_video_dimensions(video_path: Path) -> Tuple[int, int]:
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=s=x:p=0",
        str(video_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    try:
        w_str, h_str = proc.stdout.strip().splitlines()[0].split("x")
        return int(w_str), int(h_str)
    except Exception:
        return 1920, 1080


def extract_audio_wav(video_path: Path, audio_path: Path) -> Path:
    """Extract 16kHz mono MP3 audio for fast transcription & RMS hype analysis."""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-b:a", "48k",
        str(audio_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg audio extraction failed: {proc.stderr[-300:]}")
    return audio_path


def download_subtitles_and_chat(
    url: str,
    proj_dir: Path,
    include_live_chat: bool = True,
) -> Tuple[List[Path], Optional[Path]]:
    """
    Download YouTube subtitles (id/en JSON3/VTT) and optional live_chat.json separately & non-fatally.
    Runs in ~2-5 seconds and never blocks the main media download!
    """
    proj_dir.mkdir(parents=True, exist_ok=True)
    sub_template = str(proj_dir / "source.%(ext)s")

    # 1. Subtitles (fast, ~2s)
    cmd_subs = [
        _yt_dlp_bin(),
        "--no-playlist",
        "--no-warnings",
        "--ignore-errors",
        "--skip-download",
        "--write-auto-subs",
        "--write-subs",
        "--sub-langs", "id,en",
        "--sub-format", "json3/vtt/best",
        "-o", sub_template,
        url,
    ]
    subprocess.run(cmd_subs, capture_output=True, text=True, timeout=45)

    # 2. Live chat replay (bounded timeout so 6-hour chat replays don't stall forever)
    if include_live_chat:
        cmd_chat = [
            _yt_dlp_bin(),
            "--no-playlist",
            "--no-warnings",
            "--ignore-errors",
            "--skip-download",
            "--write-subs",
            "--sub-langs", "live_chat",
            "-o", sub_template,
            url,
        ]
        try:
            subprocess.run(cmd_chat, capture_output=True, text=True, timeout=45)
        except subprocess.TimeoutExpired:
            pass

    live_chat_files = sorted(
        proj_dir.glob("*live_chat*.json*"),
        key=lambda p: (p.name.endswith(".part"), -p.stat().st_size),
    )
    sub_files = [
        p for p in proj_dir.glob("source.*")
        if p.suffix.lower() in {".json3", ".vtt", ".srt"} and "live_chat" not in p.name
    ]
    return sub_files, (live_chat_files[0] if live_chat_files else None)


def download_lightweight_audio(
    url: str,
    project_id: str,
    max_video_minutes: Optional[int] = None,
    progress_cb: Optional[Callable[[int, str], None]] = None,
) -> Dict[str, Any]:
    """
    TURBO LONG-VOD MODE:
    For long videos/streams (>20 min, e.g. 5h 46m GTA VI live!), download ONLY subtitles + live chat +
    lightweight audio track (~100 MB instead of 6-10 GB!) to detect viral moments first!
    """
    proj_dir = DOWNLOADS_DIR / project_id
    proj_dir.mkdir(parents=True, exist_ok=True)

    if progress_cb:
        progress_cb(8, "⚡ Turbo Mode: Mengambil subtitle YouTube & data Live Chat...")
    sub_files, live_chat_path = download_subtitles_and_chat(url, proj_dir, include_live_chat=True)

    audio_template = str(proj_dir / "audio_raw.%(ext)s")
    cmd = [
        _yt_dlp_bin(),
        "--no-playlist",
        "--no-warnings",
        "--newline",
        "--progress",
        "-f", "worstaudio[ext=m4a]/worstaudio/bestaudio[abr<=96]/bestaudio",
        "-o", audio_template,
    ]
    if max_video_minutes and max_video_minutes > 0:
        cmd.extend(["--download-sections", f"*0-{max_video_minutes * 60}"])
    cmd.append(url)

    rc, tail = _run_ytdlp_with_progress(
        cmd,
        progress_cb=progress_cb,
        pct_start=12,
        pct_end=34,
        label="⚡ Turbo Mode: Mengunduh audio ringan untuk analisis teriakan & momen seru",
        timeout=600,
    )
    if rc != 0:
        raise RuntimeError(f"Gagal mengunduh audio ringan: {tail[-350:]}")

    audio_candidates = [p for p in proj_dir.glob("audio_raw.*") if not p.name.endswith(".part")]
    if not audio_candidates:
        raise RuntimeError("File audio tidak ditemukan setelah proses download.")
    audio_path = audio_candidates[0]
    duration = get_media_duration(audio_path)

    return {
        "audio_path": audio_path,
        "live_chat_path": live_chat_path,
        "subtitle_files": sub_files,
        "duration": duration,
    }


def download_video_section(
    url: str,
    start_time: float,
    end_time: float,
    output_path: Path,
    max_height: int = 1080,
) -> Path:
    """
    Download ONLY a targeted clip range [start_time, end_time] in HD MP4 directly from YouTube!
    Saves 98% bandwidth & time on multi-hour live streams!
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Remove any previous file at output_path
    if output_path.exists():
        output_path.unlink()

    st = max(0.0, float(start_time))
    et = max(st + 2.0, float(end_time))
    out_template = str(output_path.with_suffix(".%(ext)s"))

    # Prefer H.264 (avc1) over AV1 to avoid slow software decoding/re-encoding during section download;
    # omit --force-keyframes-at-cuts because render_vertical_clip re-encodes accurately to H.264.
    cmd = [
        _yt_dlp_bin(),
        "--no-playlist",
        "--no-warnings",
        "-f", f"bestvideo[height<={max_height}][vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<={max_height}][ext=mp4]+bestaudio[ext=m4a]/best[height<={max_height}][ext=mp4]/best[height<={max_height}]/best",
        "--download-sections", f"*{st:.2f}-{et:.2f}",
        "--merge-output-format", "mp4",
        "-o", out_template,
        url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
    if proc.returncode != 0:
        # Fallback to 720p if 1080p fails
        cmd_fb = [
            _yt_dlp_bin(),
            "--no-playlist",
            "--no-warnings",
            "-f", "bestvideo[height<=720][vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]/best",
            "--download-sections", f"*{st:.2f}-{et:.2f}",
            "--merge-output-format", "mp4",
            "-o", out_template,
            url,
        ]
        proc2 = subprocess.run(cmd_fb, capture_output=True, text=True, timeout=240)
        if proc2.returncode != 0:
            raise RuntimeError(f"Gagal mengambil segmen klip ({st:.0f}s-{et:.0f}s): {(proc2.stderr or proc.stderr)[-350:]}")

    # Find resulting mp4/mkv file
    candidates = [
        p for p in output_path.parent.glob(f"{output_path.stem}.*")
        if p.suffix.lower() in {".mp4", ".mkv", ".webm"} and not p.name.endswith(".part")
    ]
    if not candidates:
        raise RuntimeError(f"Segmen klip tidak ditemukan di disk untuk {st:.0f}s-{et:.0f}s")
    return candidates[0]


def download_vod(
    url: str,
    project_id: str,
    max_video_minutes: Optional[int] = None,
    progress_cb: Optional[Callable[[int, str], None]] = None,
) -> Dict[str, Any]:
    """
    Standard VOD download for short/medium videos (<= 20 min) with live % progress streaming.
    """
    proj_dir = DOWNLOADS_DIR / project_id
    proj_dir.mkdir(parents=True, exist_ok=True)
    video_template = str(proj_dir / "source.%(ext)s")

    if progress_cb:
        progress_cb(8, "Mengambil subtitle & metadata Live Chat...")
    sub_files, live_chat_path = download_subtitles_and_chat(url, proj_dir, include_live_chat=True)

    cmd = [
        _yt_dlp_bin(),
        "--no-playlist",
        "--no-warnings",
        "--newline",
        "--progress",
        "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best[height<=1080]/best",
        "--merge-output-format", "mp4",
        "-o", video_template,
    ]
    if max_video_minutes and max_video_minutes > 0:
        end_sec = max_video_minutes * 60
        cmd.extend(["--download-sections", f"*0-{end_sec}"])
    cmd.append(url)

    rc, tail = _run_ytdlp_with_progress(
        cmd,
        progress_cb=progress_cb,
        pct_start=12,
        pct_end=28,
        label="Mengunduh video YouTube",
        timeout=1200,
    )
    if rc != 0:
        raise RuntimeError(f"Gagal mengunduh video: {tail[-400:]}")

    video_candidates = [
        p for p in proj_dir.glob("source.*")
        if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"} and not p.name.endswith(".part")
    ]
    if not video_candidates:
        raise RuntimeError("File video tidak ditemukan setelah proses download.")
    video_path = video_candidates[0]

    if progress_cb:
        progress_cb(30, "Mengekstrak audio & frame preview 16:9...")

    audio_path = proj_dir / "audio.mp3"
    extract_audio_wav(video_path, audio_path)
    preview_path = extract_preview_frame(video_path, project_id)
    duration = get_media_duration(video_path)

    return {
        "video_path": video_path,
        "audio_path": audio_path,
        "preview_path": preview_path,
        "live_chat_path": live_chat_path,
        "subtitle_files": sub_files,
        "duration": duration,
    }


def record_live_chunk(url: str, output_path: Path, duration_seconds: int = 180) -> Path:
    """
    Record a rolling chunk (e.g. 3 minutes) from an ACTIVE YouTube Live stream!
    Inspired by Clipify (sherlock-dev-ai/Clipify).
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd_url = [
        _yt_dlp_bin(),
        "--no-warnings",
        "-f", "best[height<=1080][protocol^=m3u8]/best[height<=1080]/best",
        "-g",
        url,
    ]
    proc = subprocess.run(cmd_url, capture_output=True, text=True, timeout=45)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(f"Tidak bisa mengambil stream live URL: {proc.stderr[-300:]}")

    stream_urls = [line.strip() for line in proc.stdout.strip().splitlines() if line.strip()]
    if len(stream_urls) >= 2:
        ff_cmd = [
            "ffmpeg", "-y",
            "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
            "-i", stream_urls[0],
            "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
            "-i", stream_urls[1],
            "-t", str(duration_seconds),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            str(output_path),
        ]
    else:
        ff_cmd = [
            "ffmpeg", "-y",
            "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
            "-i", stream_urls[0],
            "-t", str(duration_seconds),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            str(output_path),
        ]

    ff_proc = subprocess.run(ff_cmd, capture_output=True, text=True, timeout=duration_seconds + 120)
    if ff_proc.returncode != 0 or not output_path.exists() or output_path.stat().st_size < 10_000:
        raise RuntimeError(f"Gagal merekam chunk live stream: {ff_proc.stderr[-350:]}")

    return output_path


def parse_live_chat_spikes(
    chat_path: Optional[Path],
    duration: float,
    bucket_seconds: int = 10,
    top_k: int = 12,
) -> List[Dict[str, Any]]:
    """
    Analyze YouTube live_chat.json (NDJSON from yt-dlp) to find chat velocity & hype emote explosions.
    """
    if not chat_path or not chat_path.exists():
        return []

    buckets: Dict[int, Dict[str, Any]] = {}
    try:
        with open(chat_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue

                replay_action = obj.get("replayChatItemAction") or {}
                offset_ms = replay_action.get("videoOffsetTimeMsec")
                if offset_ms is None:
                    continue
                sec = float(offset_ms) / 1000.0
                if sec < 0 or (duration > 0 and sec > duration):
                    continue

                msg_text = ""
                for act in replay_action.get("actions", []):
                    item = (act.get("addChatItemAction") or {}).get("item") or {}
                    renderer = item.get("liveChatTextMessageRenderer") or {}
                    runs = (renderer.get("message") or {}).get("runs") or []
                    for r in runs:
                        msg_text += r.get("text", "") + " "

                b_idx = int(sec // bucket_seconds)
                if b_idx not in buckets:
                    buckets[b_idx] = {"count": 0, "hype": 0, "samples": []}
                buckets[b_idx]["count"] += 1

                low = msg_text.lower()
                if any(kw in low for kw in HYPE_KEYWORDS):
                    buckets[b_idx]["hype"] += 1
                if msg_text.strip() and len(buckets[b_idx]["samples"]) < 4:
                    buckets[b_idx]["samples"].append(msg_text.strip()[:60])
    except Exception:
        return []

    if not buckets:
        return []

    scores = []
    for b_idx, data in buckets.items():
        weighted = data["count"] + (data["hype"] * 2.0)
        scores.append((b_idx, weighted, data))

    vals = [s[1] for s in scores]
    mean = sum(vals) / len(vals)
    variance = sum((v - mean) ** 2 for v in vals) / max(1, len(vals))
    std = math.sqrt(variance) or 1.0

    peaks = []
    for b_idx, weighted, data in scores:
        z = (weighted - mean) / std
        if z >= 0.8 or weighted >= mean * 1.6:
            peaks.append({
                "timestamp": round(b_idx * bucket_seconds + bucket_seconds / 2, 1),
                "start": round(b_idx * bucket_seconds, 1),
                "end": round((b_idx + 1) * bucket_seconds, 1),
                "message_count": data["count"],
                "hype_count": data["hype"],
                "intensity": min(100, int(50 + z * 18)),
                "sample_messages": data["samples"],
            })

    peaks.sort(key=lambda x: x["intensity"], reverse=True)
    return peaks[:top_k]
