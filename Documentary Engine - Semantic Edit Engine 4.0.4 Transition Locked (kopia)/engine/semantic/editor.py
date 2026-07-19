from __future__ import annotations

import json
import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image
from engine.image_intelligence import ImageIntelligence

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
STOP = {
    "a","an","and","are","as","at","be","been","but","by","did","do","for","from","had","has","have",
    "he","her","hers","him","his","in","into","is","it","its","of","on","one","or","she","that","the",
    "their","them","there","they","this","to","was","were","with","would",
    "att","av","blev","de","den","det","du","en","ett","för","från","han","har","hon","i","inte","med",
    "men","och","om","på","som","till","var","vi","är"
}

# General narrative pivots. These are used only as a weak scene-boundary signal;
# project-specific meaning comes from image_manifest.json rather than hardcoded topics.
TURN_MARKERS = {
    "but", "however", "yet", "instead", "meanwhile", "later", "finally", "eventually",
    "although", "despite", "then", "suddenly", "because", "therefore",
    "men", "dock", "däremot", "samtidigt", "senare", "slutligen", "plötsligt", "därför"
}


@dataclass
class Beat:
    start: float
    end: float
    text: str


def _tokens(text: str) -> list[str]:
    raw = re.findall(r"[a-z0-9]+(?:-[a-z]+)?", text.lower())
    out: list[str] = []
    for token in raw:
        if token in STOP or len(token) < 2:
            continue
        out.append(token)
        if token.endswith("s") and len(token) > 4:
            out.append(token[:-1])
    return out


def build_beats(captions: list[dict[str, Any]], duration: float, cfg: dict[str, Any]) -> list[Beat]:
    """Build visual beats from complete sentences only.

    A scene boundary may only occur at the first spoken word of a new sentence.
    This prevents image transitions from landing in the middle of narration.
    """
    min_len = float(cfg.get("min_beat_duration", 4.5))
    preferred = float(cfg.get("preferred_beat_duration", 7.5))
    max_len = float(cfg.get("max_beat_duration", 12.5))

    # Flatten timed words so sentence starts use true word timestamps rather than
    # caption-group boundaries.
    words: list[dict[str, Any]] = []
    for cap in captions:
        for word in cap.get("words", []):
            text = str(word.get("text", "")).strip()
            if text:
                words.append({"text": text, "start": float(word["start"]), "end": float(word["end"])})
    words.sort(key=lambda x: (x["start"], x["end"]))
    if not words:
        return [Beat(0.0, duration, "documentary")]

    sentences: list[Beat] = []
    current: list[dict[str, Any]] = []
    for word in words:
        current.append(word)
        if re.search(r"[.!?][\"']?$", word["text"]):
            sentences.append(Beat(current[0]["start"], current[-1]["end"], " ".join(w["text"] for w in current)))
            current = []
    if current:
        sentences.append(Beat(current[0]["start"], current[-1]["end"], " ".join(w["text"] for w in current)))

    # Group complete sentences into visual scenes. Never split a sentence.
    beats: list[Beat] = []
    group: list[Beat] = []
    for sentence in sentences:
        proposed = group + [sentence]
        span = proposed[-1].end - proposed[0].start
        current_span = (group[-1].end - group[0].start) if group else 0.0
        first_token = (_tokens(sentence.text) or [""])[0]
        strong_turn = first_token in TURN_MARKERS or bool(re.match(r"^(in|under|during|after|before)\s+(19|20)\d{2}\b", sentence.text, re.I))
        if group and ((current_span >= min_len and strong_turn) or (current_span >= preferred and span > max_len)):
            beats.append(Beat(group[0].start, group[-1].end, " ".join(x.text for x in group)))
            group = [sentence]
        else:
            group = proposed
        if group and group[-1].end - group[0].start >= max_len:
            beats.append(Beat(group[0].start, group[-1].end, " ".join(x.text for x in group)))
            group = []
    if group:
        beats.append(Beat(group[0].start, group[-1].end, " ".join(x.text for x in group)))

    # Scene boundaries are exactly sentence starts. Durations are contiguous.
    if not beats:
        return [Beat(0.0, duration, "documentary")]
    aligned: list[Beat] = []
    for i, beat in enumerate(beats):
        start = 0.0 if i == 0 else beat.start
        end = beats[i + 1].start if i + 1 < len(beats) else duration
        aligned.append(Beat(start, end, beat.text))
    return [b for b in aligned if b.end - b.start >= 0.5]

def load_profiles(project_dir: Path, images: list[Path], manifest_path: Path | None) -> dict[str, dict[str, Any]]:
    profiles: dict[str, dict[str, Any]] = {}
    if manifest_path and manifest_path.exists():
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        for item in raw.get("images", []):
            profiles[str(item.get("file"))] = item
    for image in images:
        p = profiles.setdefault(image.name, {"file": image.name})
        text_parts = [image.stem, str(p.get("description", "")), " ".join(p.get("tags", [])), " ".join(p.get("ocr", []))]
        p["semantic_text"] = " ".join(text_parts)
    return profiles


def assign_images(beats: list[Beat], images: list[Path], profiles: dict[str, dict[str, Any]], cfg: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cfg = cfg or {}
    usage: dict[str, int] = {}
    semantic_usage: dict[str, int] = {}
    repeat_weight = float(cfg.get("repeat_penalty", 3.5))
    immediate_weight = float(cfg.get("immediate_repeat_penalty", 25.0))
    previous: str | None = None
    semantic_previous: str | None = None
    scenes: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    intelligence = ImageIntelligence()
    candidates = [(image, profiles[image.name]) for image in images]
    for bi, beat in enumerate(beats):
        decision = intelligence.rank_scene(
            bi, len(beats), beat.text, candidates, usage, previous,
            repeat_weight, immediate_weight, semantic_usage, semantic_previous,
        )
        selected = decision.candidate_ranking[0]
        usage[selected.image] = usage.get(selected.image, 0) + 1
        previous = selected.image
        semantic_usage[decision.semantic_reference_image] = semantic_usage.get(decision.semantic_reference_image, 0) + 1
        semantic_previous = decision.semantic_reference_image
        semantic_profile = profiles[decision.semantic_reference_image]
        scenes.append({
            "start": round(beat.start, 3), "end": round(beat.end, 3), "duration": round(beat.end-beat.start, 3),
            "image": selected.image, "voiceover_text": beat.text, "image_description": semantic_profile.get("description", ""),
            "match_terms": list(decision.semantic_match_terms), "semantic_score": decision.semantic_score,
        })
        diagnostics.append(decision.to_dict())
    return scenes, diagnostics


def ffconcat_quote(path: Path) -> str:
    return str(path.resolve()).replace("'", "'\\''")


def build_video(project_dir: Path, audio: Path, scenes: list[dict[str, Any]], target: Path, work_dir: Path) -> None:
    concat = work_dir / "semantic_timeline.ffconcat"
    concat.parent.mkdir(parents=True, exist_ok=True)
    lines = ["ffconcat version 1.0"]
    for scene in scenes:
        image = project_dir / scene["image"]
        lines.append(f"file '{ffconcat_quote(image)}'")
        lines.append(f"duration {float(scene['duration']):.6f}")
    lines.append(f"file '{ffconcat_quote(project_dir / scenes[-1]['image'])}'")
    concat.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temp = target.with_name(target.stem + "_semantic_tmp.mp4")
    cmd = [
        "ffmpeg","-y","-hide_banner","-loglevel","warning","-f","concat","-safe","0","-i",str(concat),"-i",str(audio),
        "-vf","scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,fps=30,format=yuv420p",
        "-map","0:v:0","-map","1:a:0","-c:v","libx264","-preset","veryfast","-crf","18","-c:a","aac","-b:a","192k",
        "-t",f"{float(scenes[-1]['end']):.6f}","-movflags","+faststart",str(temp)
    ]
    subprocess.run(cmd, check=True)
    temp.replace(target)


def build_semantic_edit(cfg: dict[str, Any], captions_path: Path, video: Path, root: Path) -> Path:
    sem = cfg.get("semantic_edit_engine", {})
    if not bool(sem.get("enabled", True)):
        return video
    project_dir = root / str(cfg.get("project_dir", ""))
    audio = root / str(cfg.get("project_audio", ""))
    if not project_dir.is_dir() or not audio.exists():
        print("Semantic Edit Engine: projektbilder/ljud saknas – behåller grundvideon.")
        return video
    images = sorted([p for p in project_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS], key=lambda p: [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", p.name)])
    data = json.loads(captions_path.read_text(encoding="utf-8"))
    captions = data.get("captions", [])
    if not images or not captions:
        return video
    duration = float(subprocess.check_output(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(audio)], text=True).strip())
    manifest = project_dir / str(sem.get("manifest", "image_manifest.json"))
    profiles = load_profiles(project_dir, images, manifest if manifest.exists() else None)
    manifest_files = {str(item.get("file")) for item in json.loads(manifest.read_text(encoding="utf-8")).get("images", [])} if manifest.exists() else set()
    missing = sorted(name for name in manifest_files if not (project_dir / name).exists())
    if missing:
        raise FileNotFoundError("Bildmanifestet refererar till filer som saknas: " + ", ".join(missing))
    undescribed = [img.name for img in images if not str(profiles[img.name].get("description", "")).strip() and not profiles[img.name].get("tags")]
    if undescribed:
        print("Semantic Edit Engine: varning – bilder utan beskrivning/taggar matchas främst kronologiskt: " + ", ".join(undescribed))
    beats = build_beats(captions, duration, sem)
    scenes, image_decisions = assign_images(beats, images, profiles, sem)
    diagnostics_path = root / str(sem.get("image_intelligence_json", "output/image_intelligence_plan.json"))
    diagnostics_path.parent.mkdir(parents=True, exist_ok=True)
    diagnostics_path.write_text(json.dumps({
        "schema_version": "4.4.0",
        "image_intelligence_version": ImageIntelligence.version,
        "project": project_dir.name,
        "scenes": image_decisions,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    plan_path = root / str(sem.get("plan_json", "output/semantic_edit_plan.json"))
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps({"schema_version":"4.4.0","project":project_dir.name,"duration":round(duration,3),"scenes":scenes}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nImage Intelligence 4.4.0: {len(beats)} berättelseblock rankade mot {len(images)} bilder")
    for i, s in enumerate(scenes, 1):
        print(f"  {i:02d}. {s['start']:5.1f}-{s['end']:5.1f}s  {s['image']:<16}  match={','.join(s['match_terms'][:4]) or 'chronology'}")
    build_video(project_dir, audio, scenes, video, root / "work")
    # Semantic cuts changed; old visual analysis is invalid.
    motion = cfg.get("motion_engine", {})
    analysis_path = root / str(motion.get("analysis_json", "output/motion_analysis_v300.json"))
    if analysis_path.exists():
        analysis_path.unlink()
    return video
