#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INPUT = ROOT / "input" / "build"
CONFIG = ROOT / "config.json"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac"}


def natural_key(path: Path):
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", path.name)]


def slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", normalized).strip("_").lower()
    return slug or "documentary"


def media_folders() -> list[Path]:
    found = []
    for folder in sorted((p for p in INPUT.iterdir() if p.is_dir()), key=lambda p: p.name.lower()):
        images = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
        audio = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS]
        if images and audio:
            found.append(folder)
    return found


def audio_duration(path: Path) -> float:
    output = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path)
    ], text=True).strip()
    return float(output)


def ffconcat_quote(path: Path) -> str:
    return str(path.resolve()).replace("'", "'\\''")


def build_video(folder: Path, images: list[Path], audio: Path, target: Path) -> None:
    duration = audio_duration(audio)
    if duration <= 0:
        raise RuntimeError(f"Ljudfilen har ogiltig längd: {audio.name}")
    scene_duration = duration / len(images)
    concat = ROOT / "work" / "source_images.ffconcat"
    concat.parent.mkdir(parents=True, exist_ok=True)
    lines = ["ffconcat version 1.0"]
    for image in images:
        lines.append(f"file '{ffconcat_quote(image)}'")
        lines.append(f"duration {scene_duration:.6f}")
    # Concat-demuxern behöver sista bilden upprepad för att behålla dess duration.
    lines.append(f"file '{ffconcat_quote(images[-1])}'")
    concat.write_text("\n".join(lines) + "\n", encoding="utf-8")

    target.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
        "-f", "concat", "-safe", "0", "-i", str(concat),
        "-i", str(audio),
        "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,fps=30,format=yuv420p",
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k", "-t", f"{duration:.6f}", "-movflags", "+faststart",
        str(target),
    ]
    print(f"Bygger grundvideo från {len(images)} bilder och {audio.name} …", flush=True)
    subprocess.run(command, check=True)


def main() -> None:
    if not INPUT.exists():
        raise FileNotFoundError(f"Input-mappen saknas: {INPUT}")
    folders = media_folders()
    if not folders:
        print("Ingen projektmapp med både bilder och ljud hittades. Behåller configens input_video.")
        return
    # Senast ändrade giltiga projektmapp väljs automatiskt.
    folder = max(folders, key=lambda p: p.stat().st_mtime)
    images = sorted([p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS], key=natural_key)
    audios = sorted([p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS], key=natural_key)
    if len(audios) > 1:
        print(f"Varning: flera ljudfiler hittades. Använder {audios[0].name}.")
    audio = audios[0]
    slug = slugify(folder.name)
    source_video = INPUT / f"GENERATED_{slug}.mp4"
    output_video = ROOT / "output" / "documentary.mp4"

    newest_source = max([audio.stat().st_mtime, *(p.stat().st_mtime for p in images)])
    if not source_video.exists() or source_video.stat().st_mtime < newest_source:
        build_video(folder, images, audio, source_video)
    else:
        print(f"Återanvänder aktuell grundvideo: {source_video.name}")

    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    cfg["input_video"] = str(source_video.relative_to(ROOT))
    cfg["project_dir"] = str(folder.relative_to(ROOT))
    cfg["project_audio"] = str(audio.relative_to(ROOT))
    cfg["output_video"] = str(output_video.relative_to(ROOT))
    cfg["captions_json"] = "output/captions.json"
    cfg["captions_srt"] = "output/captions.srt"
    cfg["reuse_existing_captions"] = False
    caption_director = cfg.setdefault("caption_director", {})
    caption_director.setdefault("enabled", True)
    caption_director.setdefault("plan_json", "output/caption_director_plan.json")
    caption_director.setdefault("default_position", "bottom")
    caption_director.setdefault("bottom_vertical_anchor", 0.72)
    caption_director.setdefault("top_vertical_anchor", 0.18)
    caption_director.setdefault("safe_margin", 0.08)
    caption_director.setdefault("max_width", 0.82)
    caption_director.setdefault("max_lines", 2)
    caption_director.setdefault("max_characters_per_line", 24)
    caption_director.setdefault("max_highlight_words", 2)
    caption_director.setdefault("highlight_color", "#FFD54A")
    caption_director.setdefault("allow_scene_reposition", True)
    semantic = cfg.setdefault("semantic_edit_engine", {})
    semantic["enabled"] = True
    semantic["manifest"] = "image_manifest.json"
    semantic["plan_json"] = "output/semantic_edit_plan.json"
    semantic["image_intelligence_json"] = "output/image_intelligence_plan.json"
    semantic.setdefault("min_beat_duration", 4.5)
    semantic.setdefault("preferred_beat_duration", 7.5)
    semantic.setdefault("max_beat_duration", 11.5)
    semantic["visual_lead_seconds"] = 0.0
    motion = cfg.setdefault("motion_engine", {})
    motion["reuse_analysis"] = False
    motion["analysis_json"] = "output/motion_analysis.json"
    motion["plan_json"] = "output/motion_plan.json"
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Projekt valt: {folder.name}")
    print(f"Grundvideo: {source_video.relative_to(ROOT)}")
    print(f"Slutvideo: {output_video.relative_to(ROOT)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nFEL VID FÖRBEREDELSE: {exc}", file=sys.stderr)
        raise
