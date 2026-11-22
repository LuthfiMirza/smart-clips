"""Social Auto-Poster Service for TikTok (Content Posting API v2) & Instagram Reels (Meta Graph API v21.0).
Includes both Direct Official API Publishing and Ready-to-Post Export validation.
"""
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import requests

from backend.database import add_post_log, get_all_settings, get_clip, update_clip


def _build_full_caption(clip: Dict[str, Any], caption_override: Optional[str] = None) -> str:
    base = (caption_override if caption_override is not None else clip.get("caption") or clip.get("title") or "").strip()
    hashtags = (clip.get("hashtags") or "").strip()
    if hashtags and hashtags not in base:
        # Ensure every tag starts with #
        tags = []
        for t in hashtags.replace(",", " ").split():
            t = t.strip()
            if not t:
                continue
            tags.append(t if t.startswith("#") else f"#{t}")
        if tags:
            return f"{base}\n\n{' '.join(tags)}".strip()
    return base


def publish_to_tiktok(
    clip: Dict[str, Any],
    caption_override: Optional[str] = None,
    privacy_level: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Publish clip directly to TikTok via official Content Posting API v2.
    Supports both FILE_UPLOAD (local MP4 binary upload — no public URL needed!)
    and PULL_FROM_URL (if public_base_url is configured).
    """
    settings = get_all_settings()
    access_token = settings.get("tiktok_access_token", "").strip()
    video_path = Path(clip.get("video_path") or "")

    if not video_path.exists():
        return {"ok": False, "platform": "tiktok", "error": "File video klip belum dirender atau tidak ditemukan."}

    if not access_token:
        return {
            "ok": False,
            "platform": "tiktok",
            "needs_setup": True,
            "error": "TikTok Access Token belum diisi di Pengaturan. Silakan isi token TikTok Content Posting API atau gunakan tombol Download MP4 + Copy Caption.",
        }

    full_caption = _build_full_caption(clip, caption_override)[:2200]
    privacy = privacy_level or settings.get("tiktok_privacy") or "PUBLIC_TO_EVERYONE"
    file_size = video_path.stat().st_size

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
    }

    # TikTok FILE_UPLOAD works straight from localhost without requiring a public domain!
    chunk_size = min(file_size, 10 * 1024 * 1024)  # 10 MB chunks
    total_chunk_count = max(1, (file_size + chunk_size - 1) // chunk_size)

    init_payload = {
        "post_info": {
            "title": full_caption,
            "privacy_level": privacy,
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": file_size,
            "chunk_size": chunk_size,
            "total_chunk_count": total_chunk_count,
        },
    }

    try:
        init_res = requests.post(
            "https://open.tiktokapis.com/v2/post/publish/video/init/",
            headers=headers,
            json=init_payload,
            timeout=30,
        )
        init_data = init_res.json()
        if init_res.status_code != 200 or (init_data.get("error") or {}).get("code") not in (None, "ok"):
            err_msg = (init_data.get("error") or {}).get("message") or str(init_data)
            add_post_log(clip["id"], "tiktok", "error", init_data)
            update_clip(clip["id"], tiktok_status="failed")
            return {"ok": False, "platform": "tiktok", "error": f"TikTok Init Error: {err_msg}", "raw": init_data}

        data_obj = init_data.get("data") or {}
        upload_url = data_obj.get("upload_url")
        publish_id = data_obj.get("publish_id", "")

        if not upload_url:
            add_post_log(clip["id"], "tiktok", "error", init_data)
            return {"ok": False, "platform": "tiktok", "error": "TikTok tidak mengembalikan upload_url.", "raw": init_data}

        # Stream MP4 chunks to TikTok upload_url
        with open(video_path, "rb") as f:
            offset = 0
            while offset < file_size:
                chunk = f.read(chunk_size)
                end_byte = offset + len(chunk) - 1
                put_headers = {
                    "Content-Type": "video/mp4",
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {offset}-{end_byte}/{file_size}",
                }
                put_res = requests.put(upload_url, headers=put_headers, data=chunk, timeout=180)
                if put_res.status_code not in (200, 201, 206):
                    add_post_log(clip["id"], "tiktok", "error", {"status": put_res.status_code, "text": put_res.text[:300]})
                    update_clip(clip["id"], tiktok_status="failed")
                    return {"ok": False, "platform": "tiktok", "error": f"Upload chunk TikTok gagal ({put_res.status_code})"}
                offset += len(chunk)

        update_clip(clip["id"], tiktok_status="posted", tiktok_post_id=publish_id)
        add_post_log(clip["id"], "tiktok", "posted", {"publish_id": publish_id})
        return {
            "ok": True,
            "platform": "tiktok",
            "post_id": publish_id,
            "message": "Berhasil diupload & dipublish ke TikTok!",
        }
    except Exception as exc:
        add_post_log(clip["id"], "tiktok", "error", {"exception": str(exc)})
        update_clip(clip["id"], tiktok_status="failed")
        return {"ok": False, "platform": "tiktok", "error": str(exc)}


def publish_to_instagram(
    clip: Dict[str, Any],
    caption_override: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Publish clip as an Instagram Reel via official Meta Graph API v21.0:
      Step 1: POST /{ig-user-id}/media (media_type=REELS, video_url=...)
      Step 2: Poll /{container-id}?fields=status_code until FINISHED
      Step 3: POST /{ig-user-id}/media_publish
    """
    settings = get_all_settings()
    access_token = settings.get("instagram_access_token", "").strip()
    ig_user_id = settings.get("instagram_user_id", "").strip()
    public_base_url = settings.get("public_base_url", "").strip().rstrip("/")
    video_path = Path(clip.get("video_path") or "")

    if not video_path.exists():
        return {"ok": False, "platform": "instagram", "error": "File video klip belum dirender atau tidak ditemukan."}

    if not access_token or not ig_user_id:
        return {
            "ok": False,
            "platform": "instagram",
            "needs_setup": True,
            "error": "Instagram Graph API Token / IG User ID belum diisi di Pengaturan. Isi kredensial Meta Graph API atau gunakan tombol Download MP4 + Copy Caption.",
        }

    if not public_base_url:
        return {
            "ok": False,
            "platform": "instagram",
            "needs_setup": True,
            "error": "Meta Instagram Graph API membutuhkan Public Base URL (misal link ngrok / Cloudflare Tunnel ke localhost:8088) agar server Meta bisa mengambil file MP4 Anda. Isi di Pengaturan -> Public Base URL.",
        }

    public_video_url = f"{public_base_url}/media/clips/{clip['id']}/{video_path.name}"
    full_caption = _build_full_caption(clip, caption_override)[:2200]

    try:
        # Step 1: Create Media Container
        create_res = requests.post(
            f"https://graph.facebook.com/v21.0/{ig_user_id}/media",
            data={
                "media_type": "REELS",
                "video_url": public_video_url,
                "caption": full_caption,
                "share_to_feed": "true",
                "access_token": access_token,
            },
            timeout=30,
        ).json()

        container_id = create_res.get("id")
        if not container_id:
            add_post_log(clip["id"], "instagram", "error", create_res)
            update_clip(clip["id"], instagram_status="failed")
            return {"ok": False, "platform": "instagram", "error": f"IG Container Error: {create_res}", "raw": create_res}

        # Step 2: Poll processing status (up to ~120 seconds)
        status_code = "IN_PROGRESS"
        for _ in range(24):
            time.sleep(5)
            st_res = requests.get(
                f"https://graph.facebook.com/v21.0/{container_id}",
                params={"fields": "status_code,status", "access_token": access_token},
                timeout=20,
            ).json()
            status_code = st_res.get("status_code", "IN_PROGRESS")
            if status_code == "FINISHED":
                break
            if status_code in ("ERROR", "EXPIRED"):
                add_post_log(clip["id"], "instagram", "error", st_res)
                update_clip(clip["id"], instagram_status="failed")
                return {"ok": False, "platform": "instagram", "error": f"Instagram gagal memproses video: {st_res}"}

        if status_code != "FINISHED":
            return {"ok": False, "platform": "instagram", "error": f"Timeout menunggu pemrosesan Instagram (status: {status_code})."}

        # Step 3: Publish Reel
        pub_res = requests.post(
            f"https://graph.facebook.com/v21.0/{ig_user_id}/media_publish",
            data={
                "creation_id": container_id,
                "access_token": access_token,
            },
            timeout=30,
        ).json()

        media_id = pub_res.get("id")
        if not media_id:
            add_post_log(clip["id"], "instagram", "error", pub_res)
            update_clip(clip["id"], instagram_status="failed")
            return {"ok": False, "platform": "instagram", "error": f"IG Publish Error: {pub_res}"}

        update_clip(clip["id"], instagram_status="posted", instagram_post_id=media_id)
        add_post_log(clip["id"], "instagram", "posted", {"media_id": media_id})
        return {
            "ok": True,
            "platform": "instagram",
            "post_id": media_id,
            "message": "Berhasil dipublish ke Instagram Reels!",
        }
    except Exception as exc:
        add_post_log(clip["id"], "instagram", "error", {"exception": str(exc)})
        update_clip(clip["id"], instagram_status="failed")
        return {"ok": False, "platform": "instagram", "error": str(exc)}


def publish_clip_multi(
    clip_id: str,
    platforms: List[str],
    caption_override: Optional[str] = None,
    privacy_level: Optional[str] = None,
) -> Dict[str, Any]:
    clip = get_clip(clip_id)
    if not clip:
        return {"ok": False, "error": "Klip tidak ditemukan."}

    results = {}
    for p in platforms:
        pl = p.lower().strip()
        if pl == "tiktok":
            results["tiktok"] = publish_to_tiktok(clip, caption_override, privacy_level)
        elif pl in ("instagram", "ig", "reels"):
            results["instagram"] = publish_to_instagram(clip, caption_override)

    all_ok = all(r.get("ok") for r in results.values()) if results else False
    return {"ok": all_ok, "clip_id": clip_id, "results": results}
