from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any
import json
import cv2
import numpy as np

@dataclass(frozen=True)
class SceneAnalysis:
    start: float
    end: float
    focus_x: float
    focus_y: float
    confidence: float
    face_count: int
    subject_scale: float
    composition: str
    source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _histogram(frame: np.ndarray) -> np.ndarray:
    small = cv2.resize(frame, (160, 90))
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [32, 16], [0, 180, 0, 256])
    cv2.normalize(hist, hist)
    return hist.flatten()


def _focus(frame: np.ndarray, cascade: cv2.CascadeClassifier) -> tuple[float,float,float,int,float,str,str]:
    h, w = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    min_side = max(24, int(min(w, h) * 0.055))
    faces = cascade.detectMultiScale(gray, scaleFactor=1.12, minNeighbors=5, minSize=(min_side, min_side))
    if len(faces):
        weighted = []
        total = 0.0
        area = 0.0
        for x,y,fw,fh in faces:
            wt = float(fw*fh)
            weighted.append(((x+fw/2)/w, (y+fh/2)/h, wt))
            total += wt
            area += wt/(w*h)
        fx = sum(x*wt for x,y,wt in weighted)/total
        fy = sum(y*wt for x,y,wt in weighted)/total
        confidence = min(1.0, 0.72 + 0.08*len(faces))
        scale = min(1.0, area)
        source = 'face'
    else:
        # Visual-attention fallback: detail + local contrast + saturation, with a soft centre prior.
        small = cv2.resize(frame, (240, max(120, round(240*h/w))))
        g = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
        lap = np.abs(cv2.Laplacian(g, cv2.CV_32F))
        contrast = np.abs(g - cv2.GaussianBlur(g, (0,0), 7))
        sat = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)[:,:,1].astype(np.float32)/255.0
        score = cv2.GaussianBlur(lap + 0.65*contrast, (0,0), 3) * (0.65 + 0.35*sat)
        sh, sw = score.shape
        yy, xx = np.mgrid[0:sh, 0:sw]
        prior = np.exp(-(((xx/sw)-0.5)**2/0.20 + ((yy/sh)-0.46)**2/0.24))
        score *= (0.45 + 0.55*prior)
        threshold = np.percentile(score, 82)
        mask = np.where(score >= threshold, score, 0)
        total = float(mask.sum())
        if total > 1e-6:
            fx = float((mask*xx).sum()/total/sw)
            fy = float((mask*yy).sum()/total/sh)
            concentration = float(mask.max()/(mask.mean()+1e-6))
            confidence = min(0.68, 0.30 + concentration/55.0)
        else:
            fx, fy, confidence = 0.5, 0.46, 0.20
        scale = 0.0
        source = 'saliency'
    dx, dy = fx-0.5, fy-0.5
    if abs(dx) < 0.10 and abs(dy) < 0.10: comp = 'centered'
    elif abs(dx) > abs(dy): comp = 'left_weighted' if dx < 0 else 'right_weighted'
    else: comp = 'top_weighted' if dy < 0 else 'bottom_weighted'
    return fx,fy,confidence,len(faces),scale,comp,source


def analyze_video(video: Path, cfg: dict[str, Any], cache_path: Path | None = None) -> list[SceneAnalysis]:
    if cache_path and cache_path.exists() and bool(cfg.get('reuse_analysis', True)):
        payload = json.loads(cache_path.read_text(encoding='utf-8'))
        cached = [SceneAnalysis(**x) for x in payload.get('scenes', [])]
        if cached:
            return cached
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened(): raise RuntimeError(f'Kunde inte analysera videon: {video}')
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = frames/fps if frames else 0.0
    sample_interval = max(0.20, float(cfg.get('analysis_interval', 0.5)))
    sample_every = max(1, round(fps * sample_interval))
    cut_threshold = float(cfg.get('cut_threshold', 0.53))
    min_scene = max(0.6, float(cfg.get('min_scene_duration', 1.2)))
    times=[]; reps=[]; prev_hist=None; scene_start=0.0; best=None; best_detail=-1.0
    frame_index = 0
    while True:
        ok, frame = cap.read()
        if not ok: break
        if frame_index % sample_every != 0:
            frame_index += 1
            continue
        t = frame_index / fps
        hist = _histogram(frame)
        gray_small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (240, 135))
        detail = float(cv2.Laplacian(gray_small, cv2.CV_32F).var())
        distance = 0.0 if prev_hist is None else cv2.compareHist(prev_hist.astype(np.float32), hist.astype(np.float32), cv2.HISTCMP_BHATTACHARYYA)
        is_cut = prev_hist is not None and distance > cut_threshold and t-scene_start >= min_scene
        if is_cut:
            times.append((scene_start,t)); reps.append(best if best is not None else frame.copy())
            scene_start=t; best=None; best_detail=-1.0
        if detail > best_detail: best, best_detail = frame.copy(), detail
        prev_hist=hist
        frame_index += 1
    if duration > scene_start + 0.05 and best is not None:
        times.append((scene_start,duration)); reps.append(best)
    cap.release()
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    scenes=[]
    for (start,end), frame in zip(times,reps):
        fx,fy,conf,faces,scale,comp,source = _focus(frame,cascade)
        scenes.append(SceneAnalysis(round(start,3),round(end,3),round(fx,4),round(fy,4),round(conf,4),faces,round(scale,4),comp,source))
    if cache_path:
        cache_path.parent.mkdir(parents=True,exist_ok=True)
        cache_path.write_text(json.dumps({'schema_version':'3.0','video':video.name,'scenes':[s.to_dict() for s in scenes]},indent=2),encoding='utf-8')
    return scenes
