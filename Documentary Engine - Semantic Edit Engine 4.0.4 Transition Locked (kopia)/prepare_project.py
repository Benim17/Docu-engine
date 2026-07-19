#!/usr/bin/env python3
from __future__ import annotations

import json
import argparse
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path
from PIL import Image

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


def media_folders(input_root: Path = INPUT) -> list[Path]:
    found = []
    if not input_root.exists():
        return found
    for folder in sorted((p for p in input_root.iterdir() if p.is_dir()), key=lambda p: p.name.lower()):
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


def build_video(folder: Path, images: list[Path], audio: Path, target: Path, work_dir: Path = ROOT / "work") -> None:
    duration = audio_duration(audio)
    if duration <= 0:
        raise RuntimeError(f"Ljudfilen har ogiltig längd: {audio.name}")
    scene_duration = duration / len(images)
    concat = work_dir / "source_images.ffconcat"
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


def resolve_project(project: str | None, input_root: Path = INPUT) -> tuple[Path, bool]:
    if project:
        supplied = Path(project).expanduser()
        candidates = [supplied] if supplied.is_absolute() else [Path.cwd() / supplied, input_root / supplied]
        folder = next((candidate.resolve() for candidate in candidates if candidate.is_dir()), None)
        if folder is None:
            raise FileNotFoundError(f"Projektmappen saknas eller är inte en katalog: {project}")
        return folder, False
    folders = media_folders(input_root)
    if not folders:
        raise FileNotFoundError("Ingen projektmapp med både bilder och ljud hittades.")
    return max(folders, key=lambda p: p.stat().st_mtime).resolve(), True


def inspect_project(folder: Path) -> tuple[list[Path], list[Path]]:
    images = sorted([p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS], key=natural_key)
    audios = sorted([p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS], key=natural_key)
    if not images:
        raise ValueError(f"Projektet saknar bilder: {folder}")
    if not audios:
        raise ValueError(f"Projektet saknar ljud: {folder}")
    return images, audios


def validate_inputs(folder: Path, images: list[Path], audio: Path) -> Path | None:
    manifest = folder / "image_manifest.json"
    if manifest.is_file():
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        entries = payload.get("images")
        if payload.get("schema_version") != "1.0" or not isinstance(entries, list):
            raise ValueError(f"Bildmanifestet har ogiltigt schema: {manifest}")
        manifest_files = [str(item.get("file", "")) for item in entries if isinstance(item, dict)]
        expected_files = [image.name for image in images]
        if len(manifest_files) != len(set(manifest_files)) or manifest_files != expected_files:
            raise ValueError("Bildmanifestet måste innehålla varje projektbild exakt en gång i naturlig ordning.")
    for image in images:
        with Image.open(image) as opened:
            opened.verify()
    if audio_duration(audio) <= 0:
        raise ValueError(f"Voiceovern har ogiltig längd: {audio}")
    return manifest if manifest.is_file() else None


def build_run_config(folder: Path, audio: Path, source_video: Path, run_dir: Path) -> dict:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    cfg["input_video"] = str(source_video.resolve())
    cfg["project_dir"] = str(folder.resolve())
    cfg["project_audio"] = str(audio.resolve())
    cfg["style"] = str((ROOT / str(cfg["style"])).resolve())
    cfg["output_video"] = "output/documentary.mp4"
    cfg["captions_json"] = "output/captions.json"
    cfg["captions_srt"] = "output/captions.srt"
    cfg["reuse_existing_captions"] = False
    cfg.setdefault("caption_director", {}).setdefault("plan_json", "output/caption_director_plan.json")
    cfg.setdefault("story_director", {}).setdefault("plan_json", "output/story_director_plan.json")
    audio_director = cfg.setdefault("audio_director", {})
    audio_director.setdefault("enabled", True)
    audio_director.setdefault("plan_json", "output/audio_plan.json")
    audio_director.setdefault("diagnostics_json", "output/audio_diagnostics.json")
    audio_director.setdefault("target_loudness_lufs", -14.0)
    audio_director.setdefault("max_energy_delta", 0.30)
    audio_director.setdefault("allow_aggressive_transitions", True)
    semantic = cfg.setdefault("semantic_edit_engine", {})
    semantic.update({"enabled": True, "manifest": "image_manifest.json", "plan_json": "output/semantic_edit_plan.json", "image_intelligence_json": "output/image_intelligence_plan.json", "visual_lead_seconds": 0.0})
    motion = cfg.setdefault("motion_engine", {})
    motion.update({"reuse_analysis": False, "analysis_json": "output/motion_analysis.json", "plan_json": "output/motion_plan.json"})
    return cfg


def prepare(project: str | None, run_dir: Path, *, preflight: bool = False) -> dict:
    if not INPUT.exists():
        raise FileNotFoundError(f"Input-mappen saknas: {INPUT}")
    folder, automatic = resolve_project(project)
    images, audios = inspect_project(folder)
    if automatic:
        print(f"Varning: inget --project angavs; väljer automatiskt senast ändrade giltiga projekt: {folder.name}")
    if len(audios) > 1:
        print(f"Varning: flera ljudfiler hittades. Använder {audios[0].name}.")
    audio = audios[0]
    manifest = validate_inputs(folder, images, audio)
    slug = slugify(folder.name)
    run_dir = run_dir.expanduser().resolve()
    for child in ("work", "output", "logs"):
        (run_dir / child).mkdir(parents=True, exist_ok=True)
    source_video = run_dir / "work" / f"GENERATED_{slug}.mp4"

    newest_source = max([audio.stat().st_mtime, *(p.stat().st_mtime for p in images)])
    if not preflight:
        if not source_video.exists() or source_video.stat().st_mtime < newest_source:
            build_video(folder, images, audio, source_video, run_dir / "work")
        else:
            print(f"Återanvänder aktuell grundvideo: {source_video.name}")

    cfg = build_run_config(folder, audio, source_video, run_dir)
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
    story_director = cfg.setdefault("story_director", {})
    story_director.setdefault("enabled", True)
    story_director.setdefault("plan_json", "output/story_director_plan.json")
    audio_director = cfg.setdefault("audio_director", {})
    audio_director.setdefault("enabled", True)
    audio_director.setdefault("plan_json", "output/audio_plan.json")
    audio_director.setdefault("diagnostics_json", "output/audio_diagnostics.json")
    audio_director.setdefault("target_loudness_lufs", -14.0)
    audio_director.setdefault("max_energy_delta", 0.30)
    audio_director.setdefault("allow_aggressive_transitions", True)
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
    config_path = run_dir / "config.json"
    config_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Projekt valt: {folder.name}")
    print(f"Projektmapp: {folder}")
    print(f"Bilder ({len(images)}): " + ", ".join(path.name for path in images))
    print(f"Voiceover: {audio} ({audio_duration(audio):.3f} s)")
    print(f"Manifest: {manifest if manifest is not None else 'saknas (bakåtkompatibel semantisk fallback)'}")
    print(f"Config: {config_path}")
    print(f"Work: {run_dir / 'work'}")
    print(f"Output: {run_dir / 'output'}")
    print(f"Renderkommando: {sys.executable} -m engine.pipeline --config {config_path} --run-dir {run_dir}")
    if preflight:
        print("Preflight godkänd; ingen media byggdes eller renderades.")
    return {"folder": folder, "images": images, "audio": audio, "config": config_path, "run_dir": run_dir}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Välj dokumentärprojekt och skapa en isolerad run-konfiguration.")
    parser.add_argument("--project", help="Projektnamn under input/build eller explicit projektsökväg.")
    parser.add_argument("--run-dir", type=Path, help="Separat katalog för config, work, output och logs.")
    parser.add_argument("--preflight", action="store_true", help="Validera inputs och skriv config utan att bygga media.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    prepare(args.project, args.run_dir if args.run_dir is not None else ROOT, preflight=args.preflight)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Exception as exc:
        print(f"\nFEL VID FÖRBEREDELSE: {exc}", file=sys.stderr)
        raise
