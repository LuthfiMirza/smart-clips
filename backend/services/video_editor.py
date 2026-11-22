"""FFmpeg 9:16 Auto-Edit & Karaoke Subtitle Engine.
Supports 3 vertical 1080x1920 layouts:
  1. podcast       -> Dynamic Face-Tracked 9:16 crop (follows speaker horizontally)
  2. gaming_split  -> Split-Screen 9:16 (Top 62% Gameplay + Bottom 38% Streamer Facecam)
  3. blur_canvas   -> Blurred 9:16 background + full 16:9 video centered
Plus: Word-by-word Karaoke ASS Subtitles (Opus Clip / OpenShorts style) & Viral Hook Banner!
"""
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.config import CLIPS_DIR
from backend.services.face_tracker import track_podcast_crop_centers
from backend.services.youtube_ingestor import get_video_dimensions

OUT_W = 1080
OUT_H = 1920

SUBTITLE_STYLES = {
    "karaoke_yellow": {
        "primary": "&H00FFFFFF",     # White base
        "highlight": "&H0000EAFF",   # Bright Yellow (ASS uses BGR: 00 EA FF = #FFEA00)
        "outline": "&H00000000",
        "back": "&H90000000",
        "font": "Arial Black",
        "size": 74,
    },
    "karaoke_green": {
        "primary": "&H00FFFFFF",
        "highlight": "&H0055FF33",   # Vibrant Green (#33FF55)
        "outline": "&H00000000",
        "back": "&H90000000",
        "font": "Arial Black",
        "size": 74,
    },
    "neon_cyan": {
        "primary": "&H00FFFFFF",
        "highlight": "&H00FFF000",   # Electric Cyan (#00F0FF)
        "outline": "&H00180800",
        "back": "&H85000000",
        "font": "Arial Black",
        "size": 74,
    },
    "clean_white": {
        "primary": "&H00FFFFFF",
        "highlight": "&H00FFFFFF",
        "outline": "&H00000000",
        "back": "&H75000000",
        "font": "Helvetica Neue",
        "size": 68,
    },
}


def _fmt_ass_time(sec: float) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    cs = int(round((sec - int(sec)) * 100))
    if cs >= 100:
        cs = 99
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _escape_ass(text: str) -> str:
    return (
        text.replace("\\", "")
        .replace("{", "(")
        .replace("}", ")")
        .replace("\n", " ")
        .strip()
    )


def generate_karaoke_ass(
    words: List[Dict[str, Any]],
    clip_start: float,
    clip_end: float,
    ass_path: Path,
    style_name: str = "karaoke_yellow",
    layout_mode: str = "podcast",
    hook_text: Optional[str] = None,
    hook_banner_enabled: bool = True,
) -> Path:
    """
    Generate an Advanced SubStation Alpha (.ass) subtitle file with dynamic
    word-by-word color highlight (Karaoke pop style) + optional viral Hook Banner!
    """
    style = SUBTITLE_STYLES.get(style_name, SUBTITLE_STYLES["karaoke_yellow"])
    # Place subtitles in the sweet spot: middle seam for gaming_split, lower-third for podcast/blur
    margin_v = 680 if layout_mode == "gaming_split" else 420

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {OUT_W}
PlayResY: {OUT_H}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,{style['font']},{style['size']},{style['primary']},{style['highlight']},{style['outline']},{style['back']},-1,0,0,0,100,100,1,0,1,7,4,2,60,60,{margin_v},1
Style: Hook,{style['font']},54,&H00000000,&H00000000,&H0000EAFF,&H0000EAFF,-1,0,0,0,100,100,1,0,3,18,0,8,70,70,150,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]

    # Add viral Hook Banner at top for the first 4.2 seconds
    if hook_banner_enabled and hook_text and hook_text.strip():
        clean_hook = _escape_ass(hook_text.strip().upper())
        # Wrap long hook into 2 lines max
        hook_words = clean_hook.split()
        if len(hook_words) > 4:
            mid = len(hook_words) // 2
            clean_hook = " ".join(hook_words[:mid]) + r"\N" + " ".join(hook_words[mid:])
        hook_end = min(4.2, max(1.5, clip_end - clip_start))
        lines.append(
            f"Dialogue: 1,0:00:00.00,{_fmt_ass_time(hook_end)},Hook,,0,0,0,,{{\\fad(180,300)}}{clean_hook}\n"
        )

    if style_name != "none" and words:
        # Filter words falling within this clip window & shift timestamps relative to clip_start
        rel_words = []
        for w in words:
            ws = float(w.get("start", 0))
            we = float(w.get("end", ws + 0.3))
            txt = _escape_ass(str(w.get("word", "")))
            if not txt:
                continue
            if we <= clip_start or ws >= clip_end:
                continue
            rel_s = max(0.0, ws - clip_start)
            rel_e = min(clip_end - clip_start, max(rel_s + 0.08, we - clip_start))
            rel_words.append({"word": txt.upper(), "start": rel_s, "end": rel_e})

        # Group into punchy 3-4 word chunks (classic TikTok/Opus Clip pacing)
        chunks: List[List[Dict[str, Any]]] = []
        curr: List[Dict[str, Any]] = []
        for rw in rel_words:
            curr.append(rw)
            char_len = sum(len(x["word"]) + 1 for x in curr)
            gap_break = len(curr) >= 4 or char_len >= 22 or rw["word"].endswith((".", "!", "?"))
            if gap_break:
                chunks.append(curr)
                curr = []
        if curr:
            chunks.append(curr)

        # Render each chunk with active-word highlighting!
        hi_color = style["highlight"]
        base_color = style["primary"]
        for chunk in chunks:
            for active_idx, active_w in enumerate(chunk):
                seg_start = active_w["start"]
                seg_end = chunk[active_idx + 1]["start"] if active_idx + 1 < len(chunk) else active_w["end"]
                if seg_end <= seg_start:
                    seg_end = seg_start + 0.12
                parts = []
                for idx, item in enumerate(chunk):
                    if idx == active_idx:
                        parts.append(f"{{\\c{hi_color}\\fscx112\\fscy112}}{item['word']}{{\\c{base_color}\\fscx100\\fscy100}}")
                    else:
                        parts.append(item["word"])
                text_line = " ".join(parts)
                lines.append(
                    f"Dialogue: 0,{_fmt_ass_time(seg_start)},{_fmt_ass_time(seg_end)},Karaoke,,0,0,0,,{text_line}\n"
                )

    ass_path.parent.mkdir(parents=True, exist_ok=True)
    ass_path.write_text("".join(lines), encoding="utf-8")
    return ass_path


def _build_piecewise_crop_x_expr(
    points: List[Tuple[float, float]],
    src_w: int,
    crop_w: int,
) -> str:
    """Build an FFmpeg expression for smooth horizontal face-tracked crop x(t)."""
    max_x = max(0, src_w - crop_w)
    if max_x == 0 or not points:
        return "0"

    def to_x(norm_cx: float) -> int:
        x = int(round(norm_cx * src_w - crop_w / 2.0))
        return max(0, min(max_x, x))

    if len(points) == 1:
        return str(to_x(points[0][1]))

    # Simplify consecutive points with nearly identical X to keep FFmpeg filter expression compact
    simplified: List[Tuple[float, int]] = []
    for t, cx in points:
        px = to_x(cx)
        if not simplified or abs(px - simplified[-1][1]) >= 18:
            simplified.append((t, px))
    if len(simplified) == 1:
        return str(simplified[0][1])

    # Cap segments to 14 to stay well within FFmpeg expression depth limits
    if len(simplified) > 14:
        step = len(simplified) / 14.0
        simplified = [simplified[int(i * step)] for i in range(14)]

    # Build nested linear interpolation expression: if(lt(t,t1), x0+(x1-x0)*(t-t0)/(t1-t0), ...)
    expr = str(simplified[-1][1])
    for i in range(len(simplified) - 2, -1, -1):
        t0, x0 = simplified[i]
        t1, x1 = simplified[i + 1]
        dt = max(0.1, round(t1 - t0, 2))
        if abs(x1 - x0) > int(src_w * 0.25):
            # Scene cut -> snap cleanly without sweeping across empty space
            seg = f"{x0}"
        else:
            seg = f"({x0}+({x1}-{x0})*(t-{t0:.2f})/{dt:.2f})"
        expr = f"if(lt(t,{t1:.2f}),{seg},{expr})"
    return expr


def _escape_ffmpeg_path(p: Path) -> str:
    """Escape path for FFmpeg ass= filter."""
    s = str(p.resolve())
    return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def render_vertical_clip(
    source_video: Path,
    clip_id: str,
    start_time: float,
    end_time: float,
    layout_mode: str = "podcast",
    subtitle_style: str = "karaoke_yellow",
    facecam_box: Optional[Dict[str, float]] = None,
    words: Optional[List[Dict[str, Any]]] = None,
    hook_text: Optional[str] = None,
    hook_banner_enabled: bool = True,
) -> Dict[str, Path]:
    """
    Render a broadcast-ready 1080x1920 vertical MP4 clip for TikTok & Instagram Reels!
    """
    clip_dir = CLIPS_DIR / clip_id
    clip_dir.mkdir(parents=True, exist_ok=True)
    out_video = clip_dir / f"{clip_id}.mp4"
    out_ass = clip_dir / f"{clip_id}.ass"
    out_thumb = clip_dir / f"{clip_id}_thumb.jpg"

    src_w, src_h = get_video_dimensions(source_video)
    duration = max(2.0, end_time - start_time)

    # 1. Generate Karaoke ASS Subtitles + Hook Banner
    generate_karaoke_ass(
        words=words or [],
        clip_start=start_time,
        clip_end=end_time,
        ass_path=out_ass,
        style_name=subtitle_style,
        layout_mode=layout_mode,
        hook_text=hook_text,
        hook_banner_enabled=hook_banner_enabled,
    )
    ass_escaped = _escape_ffmpeg_path(out_ass)
    has_ass = (subtitle_style != "none" and bool(words)) or (hook_banner_enabled and bool(hook_text))
    ass_filter = f",ass='{ass_escaped}'" if has_ass else ""

    # 2. Build FFmpeg filter_complex according to layout_mode
    if layout_mode == "gaming_split":
        # Top 62% (1080x1190): Gameplay center/action area
        # Divider 8px dark/gold glow -> Total 1920
        # Bottom 38% (1080x730): Streamer Facecam crop
        top_h = 1190
        bot_h = OUT_H - top_h  # 730

        fc = facecam_box or {"x": 0.72, "y": 0.62, "w": 0.26, "h": 0.34}
        fc_x = max(0, min(src_w - 64, int(float(fc.get("x", 0.72)) * src_w)))
        fc_y = max(0, min(src_h - 64, int(float(fc.get("y", 0.62)) * src_h)))
        fc_w = max(64, min(src_w - fc_x, int(float(fc.get("w", 0.26)) * src_w)))
        fc_h = max(64, min(src_h - fc_y, int(float(fc.get("h", 0.34)) * src_h)))

        # Make gameplay crop preserve crisp action area (aspect 1080:1190 ~= 0.907)
        gp_crop_w = min(src_w, int(src_h * (OUT_W / top_h)))
        gp_crop_x = max(0, (src_w - gp_crop_w) // 2)

        filter_complex = (
            f"[0:v]split=2[v_gp][v_fc];"
            f"[v_gp]crop={gp_crop_w}:{src_h}:{gp_crop_x}:0,scale={OUT_W}:{top_h}:flags=lanczos,setsar=1[top];"
            f"[v_fc]crop={fc_w}:{fc_h}:{fc_x}:{fc_y},"
            f"scale={OUT_W}:{bot_h}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={OUT_W}:{bot_h},setsar=1[bot];"
            f"[top][bot]vstack=inputs=2,"
            f"drawbox=x=0:y={top_h - 4}:w={OUT_W}:h=8:color=0xFFD700@0.85:t=fill"
            f"{ass_filter}[vout]"
        )

    elif layout_mode == "blur_canvas":
        # Full 16:9 video centered over a fast, silky Gaussian-blurred 9:16 background
        filter_complex = (
            f"[0:v]split=2[bg][fg];"
            f"[bg]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=10:2,scale={OUT_W}:{OUT_H}:flags=bilinear,eq=brightness=-0.08[bg_blur];"
            f"[fg]scale={OUT_W}:-2:flags=lanczos[fg_scaled];"
            f"[bg_blur][fg_scaled]overlay=(W-w)/2:(H-h)/2,setsar=1"
            f"{ass_filter}[vout]"
        )

    else:
        # Default: "podcast" -> Dynamic Face-Tracked 9:16 Vertical Crop
        crop_w = min(src_w, int(round(src_h * (OUT_W / OUT_H))))
        if crop_w % 2 != 0:
            crop_w -= 1
        crop_w = max(64, crop_w)

        track_points = track_podcast_crop_centers(source_video, start_time, end_time, sample_fps=1.0)
        x_expr = _build_piecewise_crop_x_expr(track_points, src_w, crop_w)

        filter_complex = (
            f"[0:v]crop={crop_w}:{src_h}:'{x_expr}':0,"
            f"scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1"
            f"{ass_filter}[vout]"
        )

    # 3. Execute FFmpeg Render (TikTok & IG Reels compliant H.264 Main / AAC 44.1kHz)
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start_time:.3f}",
        "-t", f"{duration:.3f}",
        "-i", str(source_video),
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "21",
        "-pix_fmt", "yuv420p",
        "-r", "30",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "44100",
        "-movflags", "+faststart",
        str(out_video),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg render error ({layout_mode}): {proc.stderr[-500:]}")

    # 4. Generate 9:16 Thumbnail from ~25% into the rendered clip
    thumb_ts = min(2.0, duration * 0.25)
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-ss", f"{thumb_ts:.2f}",
            "-i", str(out_video),
            "-vframes", "1",
            "-q:v", "3",
            str(out_thumb),
        ],
        capture_output=True,
        timeout=30,
    )

    return {
        "video_path": out_video,
        "subtitle_path": out_ass,
        "thumbnail_path": out_thumb,
    }
