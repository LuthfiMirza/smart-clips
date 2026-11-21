"""Configuration & Directory Setup for Smart-Clips AI."""
from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DOWNLOADS_DIR = DATA_DIR / "downloads"
CLIPS_DIR = DATA_DIR / "clips"
PREVIEWS_DIR = DATA_DIR / "previews"
FRONTEND_DIR = BASE_DIR / "frontend"
DB_PATH = DATA_DIR / "smart_clips.db"

for _dir in (DATA_DIR, DOWNLOADS_DIR, CLIPS_DIR, PREVIEWS_DIR):
    _dir.mkdir(parents=True, exist_ok=True)

DEFAULT_SETTINGS = {
    "gemini_api_key": os.environ.get("GEMINI_API_KEY", ""),
    "gemini_model": "gemini-4-hydrogen",
    "transcribe_engine": "auto",  # auto | gemini | whisper | youtube_subs
    "whisper_model_size": "base",
    "default_layout": "auto",  # auto | podcast | gaming_split | blur_canvas
    "default_clip_count": "5",
    "min_clip_duration": "25",
    "max_clip_duration": "65",
    "subtitle_style": "karaoke_yellow",  # karaoke_yellow | karaoke_green | neon_cyan | clean_white | none
    "subtitle_font_size": "66",
    "hook_banner_enabled": "true",
    "live_chunk_seconds": "180",  # 3 minutes per rolling chunk for live streams
    "live_auto_clip_threshold": "75",  # auto-render clips with score >= 75 during live
    "live_auto_post": "false",
    # Facecam default box (normalized 0..1 on 16:9 frame) - bottom-right typical streamer
    "facecam_x": "0.72",
    "facecam_y": "0.62",
    "facecam_w": "0.26",
    "facecam_h": "0.34",
    # Social API credentials
    "tiktok_access_token": "",
    "tiktok_open_id": "",
    "tiktok_privacy": "PUBLIC_TO_EVERYONE",
    "instagram_access_token": "",
    "instagram_user_id": "",
    "public_base_url": "",  # Required for Meta Graph API / TikTok PULL_FROM_URL
}

# Facecam quick presets (normalized coordinates x, y, w, h)
FACECAM_PRESETS = {
    "bottom_right": {"x": 0.72, "y": 0.62, "w": 0.26, "h": 0.34, "label": "Pojok Kanan Bawah"},
    "bottom_left": {"x": 0.02, "y": 0.62, "w": 0.26, "h": 0.34, "label": "Pojok Kiri Bawah"},
    "top_right": {"x": 0.72, "y": 0.04, "w": 0.26, "h": 0.34, "label": "Pojok Kanan Atas"},
    "top_left": {"x": 0.02, "y": 0.04, "w": 0.26, "h": 0.34, "label": "Pojok Kiri Atas"},
    "center_right": {"x": 0.68, "y": 0.30, "w": 0.30, "h": 0.40, "label": "Tengah Kanan"},
    "auto": {"x": 0.72, "y": 0.62, "w": 0.26, "h": 0.34, "label": "Auto Deteksi Wajah"},
}
