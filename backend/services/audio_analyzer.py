"""Audio Energy & Sustained Hype Window Analyzer (Detects Screams, Laughter, Rage & Sustained Excitement).
Inspired by SharkyClips (utkualsir/sharkyclips) & Stream-Clipper (nirvagold/stream-clipper).
Upgraded with 1-second Z-score envelope + Sustained Multi-Second Hype Window scoring
to reject isolated 1-second coughs/gunshots and prioritize genuine vocal excitement.
"""
import subprocess
from pathlib import Path
from typing import Any, Dict, List
import numpy as np


def analyze_audio_spikes(
    audio_path: Path,
    window_sec: float = 1.0,
    top_k: int = 20,
    min_gap_sec: float = 25.0,
) -> Dict[str, Any]:
    """
    Decode audio via FFmpeg into raw 4kHz mono int16 PCM, compute 1-second RMS + Peak energy,
    and score both sustained multi-second excitement windows and peak bursts.
    """
    sample_rate = 4000
    cmd = [
        "ffmpeg", "-v", "error",
        "-i", str(audio_path),
        "-ac", "1",
        "-ar", str(sample_rate),
        "-f", "s16le",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=180)
    if proc.returncode != 0 or not proc.stdout:
        return {"spikes": [], "energy_timeline": [], "z_timeline_1s": [], "duration": 0.0}

    pcm = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    if pcm.size == 0:
        return {"spikes": [], "energy_timeline": [], "z_timeline_1s": [], "duration": 0.0}

    samples_per_win = max(1, int(sample_rate * window_sec))
    n_windows = pcm.size // samples_per_win
    if n_windows < 4:
        return {"spikes": [], "energy_timeline": [], "z_timeline_1s": [], "duration": pcm.size / sample_rate}

    trimmed = pcm[: n_windows * samples_per_win].reshape((n_windows, samples_per_win))
    rms = np.sqrt(np.mean(trimmed ** 2, axis=1))
    peak_amp = np.max(np.abs(trimmed), axis=1)

    # Combined vocal excitement signal (65% sustained RMS + 35% transient peak)
    energy = 0.65 * rms + 0.35 * peak_amp
    mean_e = float(np.mean(energy))
    std_e = float(np.std(energy)) or 1e-5
    max_e = float(np.max(energy)) or 1e-5

    z_scores = (energy - mean_e) / std_e

    # Compute 8-second rolling sustained excitement score so isolated 1s mic bumps are penalized
    win_sustained = 8
    sustained_z = np.zeros(n_windows, dtype=np.float32)
    for i in range(n_windows):
        seg_z = z_scores[max(0, i - 3) : min(n_windows, i + win_sustained - 3)]
        if seg_z.size > 0:
            top_half = np.sort(seg_z)[-max(1, seg_z.size // 2):]
            active_cnt = float(np.sum(seg_z > 1.0))
            # Require at least 3 active seconds in an 8s neighborhood for a real vocal burst
            sustained_z[i] = float(np.mean(top_half)) * (1.0 if active_cnt >= 3 else 0.55)

    candidate_indices = np.where((sustained_z >= 1.15) & (energy > 0.03))[0]
    sorted_candidates = sorted(candidate_indices.tolist(), key=lambda idx: float(sustained_z[idx]), reverse=True)

    selected: List[Dict[str, Any]] = []
    for idx in sorted_candidates:
        ts = float(idx) * window_sec
        if any(abs(ts - s["timestamp"]) < min_gap_sec for s in selected):
            continue
        sz = float(sustained_z[idx])
        pz = float(z_scores[idx])
        norm_intensity = min(99, max(55, int(56 + sz * 15)))
        label = (
            "Ledakan Emosi / Teriakan Beruntun"
            if sz >= 2.0
            else ("Reaksi Keras / Ketawa" if sz >= 1.45 else "Energi Suara Tinggi")
        )
        selected.append({
            "timestamp": round(ts, 1),
            "z_score": round(pz, 2),
            "sustained_z": round(sz, 2),
            "intensity": norm_intensity,
            "rms": round(float(rms[idx]), 4),
            "label": label,
        })
        if len(selected) >= top_k:
            break

    # Downsampled 100-point timeline for the Web Dashboard waveform visualizer
    timeline_points = 100
    step = max(1, n_windows // timeline_points)
    timeline = []
    for i in range(0, n_windows, step):
        seg = energy[i : i + step]
        val = float(np.max(seg)) / max_e if seg.size else 0.0
        timeline.append({
            "t": round(i * window_sec, 1),
            "v": round(min(1.0, val), 3),
        })

    selected.sort(key=lambda x: x["timestamp"])
    return {
        "spikes": selected,
        "energy_timeline": timeline[:120],
        "z_timeline_1s": [round(float(x), 2) for x in z_scores.tolist()],
        "duration": round(pcm.size / sample_rate, 2),
    }
