"""Computer Vision Face Tracking & Auto Facecam Box Detector (OpenCV 5.x YuNet + OpenCV 4.x Haar + Saliency Fallback).
Inspired by ClippyMe (Active-Speaker Reframing) & Chopify (Gaming Split-Screen).
"""
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import urllib.request
import cv2
import numpy as np

from backend.config import DATA_DIR

MODELS_DIR = DATA_DIR / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)
YUNET_PATH = MODELS_DIR / "face_detection_yunet_2023mar.onnx"
YUNET_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"


def _ensure_yunet_model() -> Optional[Path]:
    if YUNET_PATH.exists() and YUNET_PATH.stat().st_size > 50_000:
        return YUNET_PATH
    try:
        urllib.request.urlretrieve(YUNET_URL, str(YUNET_PATH))
        if YUNET_PATH.exists() and YUNET_PATH.stat().st_size > 50_000:
            return YUNET_PATH
    except Exception:
        pass
    return None


def _get_detector(input_size: Tuple[int, int] = (640, 360)) -> Tuple[str, Any]:
    """Return ('yunet', detector) on OpenCV 5/4 with YuNet, ('haar', classifier) on OpenCV 4, or ('none', None)."""
    if hasattr(cv2, "FaceDetectorYN"):
        model_path = _ensure_yunet_model()
        if model_path:
            try:
                det = cv2.FaceDetectorYN.create(
                    str(model_path), "", input_size, score_threshold=0.65, nms_threshold=0.3, top_k=10
                )
                return ("yunet", det)
            except Exception:
                pass

    cascade_cls = getattr(cv2, "CascadeClassifier", None)
    haar_dir = getattr(getattr(cv2, "data", None), "haarcascades", None)
    if cascade_cls and haar_dir:
        for name in ("haarcascade_frontalface_default.xml", "haarcascade_frontalface_alt2.xml"):
            p = Path(haar_dir) / name
            if p.exists():
                clf = cascade_cls(str(p))
                if not clf.empty():
                    return ("haar", clf)

    return ("none", None)


def _detect_faces_in_frame(
    frame_bgr: np.ndarray,
    det_type: str,
    detector: Any,
) -> List[Tuple[int, int, int, int]]:
    h, w = frame_bgr.shape[:2]
    if det_type == "yunet" and detector is not None:
        try:
            detector.setInputSize((w, h))
            _ok, faces = detector.detect(frame_bgr)
            if faces is not None and len(faces) > 0:
                results = []
                for f in faces:
                    fx, fy, fw, fh = int(f[0]), int(f[1]), int(f[2]), int(f[3])
                    if fw > 16 and fh > 16:
                        results.append((max(0, fx), max(0, fy), fw, fh))
                return results
        except Exception:
            pass
    elif det_type == "haar" and detector is not None:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        faces = detector.detectMultiScale(gray, scaleFactor=1.12, minNeighbors=5, minSize=(36, 36))
        if len(faces) > 0:
            return [(int(x), int(y), int(fw), int(fh)) for (x, y, fw, fh) in faces]

    return []


def auto_detect_facecam_box(video_path: Path, sample_count: int = 8) -> Dict[str, Any]:
    """
    Scan sample frames across a video to detect if there is a fixed streamer Facecam
    in one of the corners/edges, returning a normalized FacecamBox {x, y, w, h, preset, detected}.
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return {"x": 0.72, "y": 0.62, "w": 0.26, "h": 0.34, "preset": "bottom_right", "detected": False}

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1920)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1080)
    small_w = 640
    small_h = max(1, int(height * (small_w / max(1, width))))
    det_type, detector = _get_detector((small_w, small_h))

    face_centers: List[Tuple[float, float, float, float]] = []
    if total_frames > 0:
        indices = np.linspace(max(1, int(total_frames * 0.08)), max(2, int(total_frames * 0.90)), sample_count, dtype=int)
    else:
        indices = [30, 90, 150, 210]

    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        small = cv2.resize(frame, (small_w, small_h))
        faces = _detect_faces_in_frame(small, det_type, detector)
        if faces:
            sh, sw = small.shape[:2]
            for (fx, fy, fw, fh) in faces:
                nx = (fx + fw / 2.0) / sw
                ny = (fy + fh / 2.0) / sh
                nw = fw / sw
                nh = fh / sh
                face_centers.append((nx, ny, nw, nh))

    cap.release()

    if not face_centers:
        return {"x": 0.72, "y": 0.62, "w": 0.26, "h": 0.34, "preset": "bottom_right", "detected": False}

    xs = [c[0] for c in face_centers]
    ys = [c[1] for c in face_centers]
    med_x = float(np.median(xs))
    med_y = float(np.median(ys))
    med_w = float(np.median([c[2] for c in face_centers]))
    med_h = float(np.median([c[3] for c in face_centers]))

    box_w = min(0.38, max(0.24, med_w * 3.2))
    box_h = min(0.45, max(0.30, med_h * 2.8))
    box_x = max(0.0, min(1.0 - box_w, med_x - box_w / 2.0))
    box_y = max(0.0, min(1.0 - box_h, med_y - box_h * 0.45))

    if med_x >= 0.5 and med_y >= 0.5:
        preset = "bottom_right"
    elif med_x < 0.5 and med_y >= 0.5:
        preset = "bottom_left"
    elif med_x >= 0.5 and med_y < 0.5:
        preset = "top_right"
    else:
        preset = "top_left"

    return {
        "x": round(box_x, 3),
        "y": round(box_y, 3),
        "w": round(box_w, 3),
        "h": round(box_h, 3),
        "preset": preset,
        "detected": True,
        "video_width": width,
        "video_height": height,
    }


def track_podcast_crop_centers(
    video_path: Path,
    start_time: float,
    end_time: float,
    sample_fps: float = 1.0,
) -> List[Tuple[float, float]]:
    """
    Track active speaker face X-center (normalized 0..1) across a clip window [start_time, end_time].
    Uses YuNet DNN Face Detection with motion-saliency fallback and temporal deadzone smoothing.
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return [(0.0, 0.5)]

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1920)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1080)
    small_w = 480
    small_h = max(1, int(height * (small_w / max(1, width))))
    det_type, detector = _get_detector((small_w, small_h))

    duration = max(1.0, end_time - start_time)
    n_samples = max(2, min(60, int(duration * sample_fps)))
    times = np.linspace(start_time, max(start_time + 0.5, end_time - 0.2), n_samples)

    raw_points: List[Tuple[float, float]] = []
    last_cx = 0.5
    prev_gray: Optional[np.ndarray] = None

    for t in times:
        frame_idx = int(t * fps)
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ok, frame = cap.read()
        if not ok or frame is None:
            raw_points.append((round(float(t - start_time), 2), last_cx))
            continue

        small = cv2.resize(frame, (small_w, small_h))
        faces = _detect_faces_in_frame(small, det_type, detector)
        sw = small.shape[1]

        cx = None
        if faces:
            best = max(faces, key=lambda f: f[2] * f[3])
            cx = (best[0] + best[2] / 2.0) / max(1, sw)
        else:
            # Subtle motion-weighted center fallback when face turns sideways
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            if prev_gray is not None:
                diff = cv2.absdiff(gray, prev_gray)
                col_energy = diff.mean(axis=0)
                if float(col_energy.max()) > 12.0:
                    xs = np.linspace(0.0, 1.0, sw)
                    cx = float(np.sum(xs * col_energy) / max(1e-5, np.sum(col_energy)))
            prev_gray = gray

        if cx is not None and abs(cx - last_cx) > 0.06:
            if abs(cx - last_cx) > 0.28:
                last_cx = cx
            else:
                last_cx = 0.65 * last_cx + 0.35 * cx

        raw_points.append((round(float(t - start_time), 2), round(float(last_cx), 3)))

    cap.release()
    return raw_points if raw_points else [(0.0, 0.5)]
