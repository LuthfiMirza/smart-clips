"""SQLite Database Layer for Smart-Clips AI."""
import json
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional
from contextlib import contextmanager

from backend.config import DB_PATH, DEFAULT_SETTINGS


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


@contextmanager
def get_db():
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                youtube_url TEXT NOT NULL,
                video_id TEXT,
                title TEXT,
                channel TEXT,
                thumbnail_url TEXT,
                duration REAL DEFAULT 0,
                is_live INTEGER DEFAULT 0,
                content_type TEXT DEFAULT 'auto',
                layout_mode TEXT DEFAULT 'auto',
                subtitle_style TEXT DEFAULT 'karaoke_yellow',
                facecam_preset TEXT DEFAULT 'auto',
                facecam_box_json TEXT,
                status TEXT DEFAULT 'queued',
                progress INTEGER DEFAULT 0,
                status_message TEXT DEFAULT 'Menunggu antrian...',
                error_message TEXT,
                video_path TEXT,
                audio_path TEXT,
                preview_frame_path TEXT,
                transcript_json TEXT,
                audio_spikes_json TEXT,
                chat_spikes_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS clips (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                title TEXT NOT NULL,
                hook_text TEXT,
                caption TEXT,
                hashtags TEXT,
                reason TEXT,
                category TEXT DEFAULT 'highlight',
                virality_score INTEGER DEFAULT 80,
                start_time REAL NOT NULL,
                end_time REAL NOT NULL,
                duration REAL NOT NULL,
                layout_mode TEXT DEFAULT 'podcast',
                subtitle_style TEXT DEFAULT 'karaoke_yellow',
                facecam_box_json TEXT,
                words_json TEXT,
                video_path TEXT,
                subtitle_path TEXT,
                thumbnail_path TEXT,
                status TEXT DEFAULT 'pending',
                tiktok_status TEXT DEFAULT 'not_posted',
                tiktok_post_id TEXT,
                instagram_status TEXT DEFAULT 'not_posted',
                instagram_post_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS live_monitors (
                id TEXT PRIMARY KEY,
                youtube_url TEXT NOT NULL,
                title TEXT,
                channel TEXT,
                content_type TEXT DEFAULT 'gaming_split',
                layout_mode TEXT DEFAULT 'gaming_split',
                subtitle_style TEXT DEFAULT 'karaoke_yellow',
                facecam_preset TEXT DEFAULT 'auto',
                facecam_box_json TEXT,
                chunk_seconds INTEGER DEFAULT 180,
                auto_clip_threshold INTEGER DEFAULT 75,
                auto_post INTEGER DEFAULT 0,
                status TEXT DEFAULT 'running',
                chunks_processed INTEGER DEFAULT 0,
                clips_generated INTEGER DEFAULT 0,
                last_log TEXT DEFAULT 'Memulai pemantauan live stream...',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS post_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                clip_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                status TEXT NOT NULL,
                response_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(clip_id) REFERENCES clips(id) ON DELETE CASCADE
            );
            """
        )

        # Seed default settings if missing
        now = _now()
        for k, v in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                (k, str(v), now),
            )


# ---- Settings CRUD ----

def get_all_settings() -> Dict[str, str]:
    with get_db() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        data = dict(DEFAULT_SETTINGS)
        for r in rows:
            data[r["key"]] = r["value"]
        return data


def get_setting(key: str, default: str = "") -> str:
    with get_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row:
            return row["value"]
        return DEFAULT_SETTINGS.get(key, default)


def update_settings(updates: Dict[str, Any]) -> Dict[str, str]:
    now = _now()
    with get_db() as conn:
        for k, v in updates.items():
            if v is None:
                continue
            conn.execute(
                """
                INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (k, str(v), now),
            )
    return get_all_settings()


# ---- Projects CRUD ----

def create_project(project_data: Dict[str, Any]) -> Dict[str, Any]:
    now = _now()
    project_data.setdefault("created_at", now)
    project_data.setdefault("updated_at", now)
    cols = ", ".join(project_data.keys())
    placeholders = ", ".join(["?"] * len(project_data))
    with get_db() as conn:
        conn.execute(
            f"INSERT INTO projects ({cols}) VALUES ({placeholders})",
            list(project_data.values()),
        )
    return get_project(project_data["id"])


def update_project(project_id: str, **fields) -> Optional[Dict[str, Any]]:
    if not fields:
        return get_project(project_id)
    fields["updated_at"] = _now()
    set_clause = ", ".join([f"{k} = ?" for k in fields.keys()])
    values = list(fields.values()) + [project_id]
    with get_db() as conn:
        conn.execute(f"UPDATE projects SET {set_clause} WHERE id = ?", values)
    return get_project(project_id)


def get_project(project_id: str) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return dict(row) if row else None


def list_projects(limit: int = 50) -> List[Dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT p.*,
                   (SELECT COUNT(*) FROM clips c WHERE c.project_id = p.id) AS clip_count
            FROM projects p
            ORDER BY p.created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def delete_project(project_id: str) -> bool:
    with get_db() as conn:
        cur = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        return cur.rowcount > 0


# ---- Clips CRUD ----

def create_clip(clip_data: Dict[str, Any]) -> Dict[str, Any]:
    now = _now()
    clip_data.setdefault("created_at", now)
    clip_data.setdefault("updated_at", now)
    cols = ", ".join(clip_data.keys())
    placeholders = ", ".join(["?"] * len(clip_data))
    with get_db() as conn:
        conn.execute(
            f"INSERT INTO clips ({cols}) VALUES ({placeholders})",
            list(clip_data.values()),
        )
    return get_clip(clip_data["id"])


def update_clip(clip_id: str, **fields) -> Optional[Dict[str, Any]]:
    if not fields:
        return get_clip(clip_id)
    fields["updated_at"] = _now()
    set_clause = ", ".join([f"{k} = ?" for k in fields.keys()])
    values = list(fields.values()) + [clip_id]
    with get_db() as conn:
        conn.execute(f"UPDATE clips SET {set_clause} WHERE id = ?", values)
    return get_clip(clip_id)


def get_clip(clip_id: str) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
        return dict(row) if row else None


def list_clips(project_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    with get_db() as conn:
        if project_id:
            rows = conn.execute(
                "SELECT * FROM clips WHERE project_id = ? ORDER BY virality_score DESC, start_time ASC",
                (project_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT c.*, p.title AS project_title, p.youtube_url AS source_url
                FROM clips c
                JOIN projects p ON p.id = c.project_id
                ORDER BY c.created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


def delete_clip(clip_id: str) -> bool:
    with get_db() as conn:
        cur = conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
        return cur.rowcount > 0


# ---- Live Monitors CRUD ----

def create_live_monitor(data: Dict[str, Any]) -> Dict[str, Any]:
    now = _now()
    data.setdefault("created_at", now)
    data.setdefault("updated_at", now)
    cols = ", ".join(data.keys())
    placeholders = ", ".join(["?"] * len(data))
    with get_db() as conn:
        conn.execute(f"INSERT INTO live_monitors ({cols}) VALUES ({placeholders})", list(data.values()))
    return get_live_monitor(data["id"])


def update_live_monitor(monitor_id: str, **fields) -> Optional[Dict[str, Any]]:
    if not fields:
        return get_live_monitor(monitor_id)
    fields["updated_at"] = _now()
    set_clause = ", ".join([f"{k} = ?" for k in fields.keys()])
    values = list(fields.values()) + [monitor_id]
    with get_db() as conn:
        conn.execute(f"UPDATE live_monitors SET {set_clause} WHERE id = ?", values)
    return get_live_monitor(monitor_id)


def get_live_monitor(monitor_id: str) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM live_monitors WHERE id = ?", (monitor_id,)).fetchone()
        return dict(row) if row else None


def list_live_monitors() -> List[Dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM live_monitors ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


# ---- Post Logs ----

def add_post_log(clip_id: str, platform: str, status: str, response_data: Any):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO post_logs (clip_id, platform, status, response_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (clip_id, platform, status, json.dumps(response_data, ensure_ascii=False), _now()),
        )


def list_post_logs(clip_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    with get_db() as conn:
        if clip_id:
            rows = conn.execute(
                "SELECT * FROM post_logs WHERE clip_id = ? ORDER BY created_at DESC LIMIT ?",
                (clip_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM post_logs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
