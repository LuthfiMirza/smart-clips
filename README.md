# Smart-Clips AI Studio

Aplikasi Web Dashboard Auto-Clipper untuk YouTube (Video Biasa, Rekaman Live / VOD, dan YouTube yang **Sedang Live**) menjadi klip vertikal **9:16 (1080×1920)** lengkap dengan subtitle karaoke dan auto-posting ke **TikTok** & **Instagram Reels**.

Dibangun dengan menggabungkan arsitektur terbaik dari 6 proyek open-source: **ClippyMe**, **SharkyClips**, **Clipify**, **Chopify**, **OpenShorts**, dan **Stream-Clipper**.

## Fitur Utama
1. **Dual-Mode Ingestion:**
   - **VOD / Rekaman Live:** Download video via `yt-dlp`, ekstrak audio, subtitle `json3`/VTT, serta `live_chat.json`.
   - **Real-Time YouTube Live Watcher:** Merekam stream yang sedang berlangsung per segmen (rolling chunks 1–10 menit) dan langsung memotong momen viral saat live masih jalan!
2. **Multimodal Highlight Detection (3 Sinyal):**
   - **Transkrip + AI Viral Director (`gemini-4-hydrogen`)**: Mencari hook kuat, debat, cerita lucu, skor viral 1–100, judul, caption, dan hashtag.
   - **Audio Energy Spike (RMS)**: Mendeteksi teriakan, ketawa keras, dan momen clutch.
   - **Live Chat Velocity Spike**: Mendeteksi lonjakan spam chat penonton (`wkwk`, `gg`, `anjir`, dll).
3. **3 Layout Auto-Edit 9:16 + Subtitle Karaoke:**
   - **Podcast / Ngobrol**: Dynamic Face-Tracking (kamera melacak wajah pembicara secara mulus).
   - **Gaming / Reaction Split-Screen**: Atas 62% Gameplay/Layar + Bawah 38% Zoom Facecam Streamer (dengan Auto-Deteksi Wajah + **Interactive Visual Facecam Crop Picker** di Dashboard).
   - **Blur Canvas**: Video 16:9 utuh di tengah + latar blur.
   - **Karaoke ASS Subtitle & Viral Hook Banner**.
4. **Auto-Posting TikTok & Instagram Reels:**
   - TikTok Content Posting API v2 (`FILE_UPLOAD` langsung dari localhost).
   - Instagram Reels via Meta Graph API v21.0.
   - Plus 1-klik **Copy Caption** & **Download MP4 9:16**.

## Cara Menjalankan
```bash
cd /Applications/XAMPP/xamppfiles/htdocs/smart-clips
chmod +x start.sh
./start.sh
```
Lalu buka browser ke: **http://127.0.0.1:8088**
