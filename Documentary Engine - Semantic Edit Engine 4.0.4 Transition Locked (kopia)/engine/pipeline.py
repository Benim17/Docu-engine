#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import re
import random
import shutil
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from PIL import Image, ImageColor, ImageDraw, ImageFont
from engine.motion import analyze_video, build_motion_plan, motion_state as intelligent_motion_state
from engine.motion.analyzer import SceneAnalysis
from engine.semantic import build_semantic_edit
from engine.transition import build_transition_boundaries, smooth_alpha, validate_transition_contract
from engine.visual_director import VisualDirector

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.json"
WORK_DIR = ROOT / "work"


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    fps: float
    duration: float


@dataclass(frozen=True)
class MotionKeyframe:
    time: float
    zoom: float
    pan_x: float
    pan_y: float
    preset: str


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def require(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"Programmet '{name}' saknas. Installera FFmpeg med Homebrew: brew install ffmpeg")


def run(cmd: list[str]) -> None:
    print("\n$", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def extract_words(result: dict[str, Any]) -> list[Word]:
    words: list[Word] = []
    for segment in result.get("segments", []):
        seg_start = float(segment.get("start", 0.0))
        seg_end = float(segment.get("end", seg_start + 0.25))
        raw_words = segment.get("words") or []
        if raw_words:
            for item in raw_words:
                text = clean_text(str(item.get("word", "")))
                if not text:
                    continue
                start = float(item.get("start", seg_start))
                end = float(item.get("end", start + 0.18))
                if end <= start:
                    end = start + 0.12
                words.append(Word(text=text, start=start, end=end))
        else:
            tokens = clean_text(str(segment.get("text", ""))).split()
            duration = max(0.2, seg_end - seg_start)
            for i, token in enumerate(tokens):
                start = seg_start + duration * i / max(1, len(tokens))
                end = seg_start + duration * (i + 1) / max(1, len(tokens))
                words.append(Word(text=token, start=start, end=end))
    words.sort(key=lambda x: (x.start, x.end))
    # Remove Whisper tail hallucinations: overlapping micro-words clustered after
    # the last stable sentence are not real speech and can create broken captions.
    cleaned: list[Word] = []
    for word in words:
        if cleaned and word.start < cleaned[-1].start:
            continue
        if word.end - word.start < 0.075 and cleaned and word.start >= cleaned[-1].start:
            continue
        cleaned.append(word)
    # Enforce monotonic word timing without discarding legitimate words.
    monotonic: list[Word] = []
    last_end = 0.0
    for word in cleaned:
        start = max(word.start, last_end - 0.015)
        end = max(word.end, start + 0.08)
        monotonic.append(Word(word.text, start, end))
        last_end = end
    return normalize_transcript_words(monotonic)


def normalize_transcript_words(words: list[Word]) -> list[Word]:
    """Repair common Whisper punctuation splits while preserving timing coverage."""
    out: list[Word] = []
    for word in words:
        text = clean_text(word.text)
        if out and text.startswith("-") and len(text) > 1:
            prev = out.pop()
            out.append(Word(prev.text.rstrip() + text, prev.start, word.end))
            continue
        # Whisper occasionally emits a separated hyphen token.
        if text == "-" and out:
            prev = out.pop()
            out.append(Word(prev.text.rstrip() + "-", prev.start, word.end))
            continue
        out.append(Word(text, word.start, word.end))
    return out


def transcription_quality_issues(words: list[Word]) -> list[str]:
    """Detect common Whisper hallucinations without deleting legitimate speech."""
    issues: list[str] = []
    if not words:
        return ["empty transcript"]

    normalized = [re.sub(r"[^a-z0-9']+", "", w.text.lower()) for w in words]

    # Whisper repetition loops often repeat the same 2–4-word phrase immediately.
    for size in (2, 3, 4):
        for i in range(0, len(normalized) - size * 2 + 1):
            a = normalized[i:i + size]
            b = normalized[i + size:i + size * 2]
            if a == b and all(a):
                issues.append(f"repeated {size}-word phrase near {words[i].start:.2f}s")
                break

    # Hallucinated bursts have implausibly dense, near-identical timestamps.
    for i in range(len(words)):
        j = i
        while j < len(words) and words[j].end - words[i].start <= 0.8:
            j += 1
        count = j - i
        if count >= 8:
            issues.append(f"implausible word burst ({count} words/0.8s) near {words[i].start:.2f}s")
            break

    micro_run = 0
    for w in words:
        if (w.end - w.start) <= 0.085:
            micro_run += 1
            if micro_run >= 6:
                issues.append(f"micro-timestamp run near {w.start:.2f}s")
                break
        else:
            micro_run = 0

    return issues


def transcribe_with_guard(audio: Path, cfg: dict[str, Any]) -> tuple[dict[str, Any], list[Word]]:
    """Transcribe once, then automatically retry if Whisper enters a repetition loop."""
    import mlx_whisper

    model = str(cfg.get("model", "mlx-community/whisper-large-v3-turbo"))
    attempts = [
        {"condition_on_previous_text": False, "temperature": 0.0},
        {"condition_on_previous_text": False, "temperature": 0.2},
        {"condition_on_previous_text": True, "temperature": 0.0},
    ]
    best: tuple[dict[str, Any], list[Word], list[str]] | None = None

    for attempt_no, options in enumerate(attempts, 1):
        result = mlx_whisper.transcribe(
            str(audio),
            path_or_hf_repo=model,
            task="translate",
            word_timestamps=True,
            verbose=False,
            **options,
        )
        words = extract_words(result)
        issues = transcription_quality_issues(words)
        if not issues:
            if attempt_no > 1:
                print(f"Whisper-retry {attempt_no} gav en stabil transkribering.")
            return result, words
        print(f"Whisper-retry {attempt_no}: upptäckte " + "; ".join(issues))
        if best is None or len(issues) < len(best[2]):
            best = (result, words, issues)

    assert best is not None
    raise RuntimeError(
        "Caption Quality Guard stoppade renderingen eftersom Whisper fortfarande "
        "skapade en misstänkt upprepningsloop efter tre försök: " + "; ".join(best[2])
    )

def should_break(current: list[Word], incoming: Word, cfg: dict[str, Any]) -> bool:
    if not current:
        return False
    previous = current[-1]
    duration = incoming.end - current[0].start
    gap = incoming.start - previous.end
    return (
        len(current) >= int(cfg.get("max_words_per_caption", 5))
        or duration > float(cfg.get("max_caption_duration", 2.4))
        or gap > float(cfg.get("max_gap_inside_caption", 0.55))
        or (re.search(r"[.!?]$", previous.text) is not None and len(current) >= 2)
    )


def build_phrases(words: list[Word], cfg: dict[str, Any]) -> list[dict[str, Any]]:
    max_words = int(cfg.get("max_words_per_caption", 5))
    min_words = int(cfg.get("min_words_per_caption", 2))
    max_duration = float(cfg.get("max_caption_duration", 2.4))
    uppercase = bool(cfg.get("uppercase", True))

    groups: list[list[Word]] = []
    current: list[Word] = []
    for word in words:
        if should_break(current, word, cfg):
            groups.append(current)
            current = []
        current.append(word)
    if current:
        groups.append(current)

    merged: list[list[Word]] = []
    for group in groups:
        if merged and len(group) < min_words and len(merged[-1]) + len(group) <= max_words + 1 and group[-1].end - merged[-1][0].start <= max_duration + 0.35:
            merged[-1].extend(group)
        else:
            merged.append(group)

    phrases: list[dict[str, Any]] = []
    for idx, group in enumerate(merged, 1):
        words_payload = []
        for w in group:
            txt = w.text.upper() if uppercase else w.text
            words_payload.append({"text": txt, "start": round(w.start, 3), "end": round(w.end, 3)})
        phrases.append({
            "id": idx,
            "start": round(group[0].start, 3),
            "end": round(group[-1].end, 3),
            "duration": round(group[-1].end - group[0].start, 3),
            "text": " ".join(x["text"] for x in words_payload),
            "words": words_payload,
        })
    return phrases


def srt_timestamp(seconds: float) -> str:
    ms = max(0, round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(captions: list[dict[str, Any]], path: Path) -> None:
    lines: list[str] = []
    for caption in captions:
        lines += [str(caption["id"]), f"{srt_timestamp(caption['start'])} --> {srt_timestamp(caption['end'])}", caption["text"], ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def build_caption_core(cfg: dict[str, Any], video: Path, json_path: Path, srt_path: Path) -> None:
    if bool(cfg.get("reuse_existing_captions", True)) and json_path.exists() and srt_path.exists():
        print(f"Återanvänder befintlig caption-data: {json_path}")
        return

    WORK_DIR.mkdir(exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    audio = WORK_DIR / "caption_audio_16khz.wav"
    source_audio = ROOT / str(cfg.get("project_audio", ""))
    audio_input = source_audio if source_audio.exists() else video
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning", "-i", str(audio_input), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(audio)])

    print("\nTranskriberar och översätter alltid till engelska …", flush=True)
    result, words = transcribe_with_guard(audio, cfg)
    if not words:
        raise RuntimeError("Whisper returnerade inga ord.")
    captions = build_phrases(words, cfg)
    caption_word_count = sum(len(c.get("words", [])) for c in captions)
    if caption_word_count != len(words):
        raise RuntimeError(f"Caption-täckningsfel: {caption_word_count}/{len(words)} ord inkluderades.")
    payload = {
        "schema_version": "2.0",
        "source_video": video.name,
        "language": "en",
        "model": cfg.get("model"),
        "full_text": clean_text(str(result.get("text", ""))),
        "word_count": len(words),
        "caption_count": len(captions),
        "captions": captions,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_srt(captions, srt_path)
    print(f"Caption Core klar: {len(captions)} caption-grupper.")


def probe(path: Path) -> VideoInfo:
    data = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,avg_frame_rate:format=duration",
        "-of", "json", str(path)
    ], text=True))
    stream = data["streams"][0]
    num, den = stream.get("avg_frame_rate", "30/1").split("/")
    fps = float(num) / max(float(den), 1.0)
    return VideoInfo(int(stream["width"]), int(stream["height"]), fps if fps > 0 else 30.0, float(data["format"].get("duration", 0.0)))


def find_font(family: str) -> Path:
    candidates = [
        Path("/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
        Path("/Library/Fonts/Arial Rounded Bold.ttf"),
        Path("/System/Library/Fonts/HelveticaNeue.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ]
    family_tokens = [t for t in re.split(r"\s+", family.lower()) if t]
    for root in [Path.home() / "Library/Fonts", Path("/Library/Fonts"), Path("/System/Library/Fonts")]:
        if root.exists():
            for p in root.rglob("*"):
                if p.suffix.lower() in {".ttf", ".otf", ".ttc"} and all(t in p.stem.lower() for t in family_tokens):
                    return p
    for p in candidates:
        if p.exists():
            return p
    raise FileNotFoundError("Inget lämpligt typsnitt hittades.")


def rgba(color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    r, g, b = ImageColor.getrgb(color)
    return r, g, b, alpha


def ease_out_back(x: float) -> float:
    x = max(0.0, min(1.0, x))
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


def active_caption(captions: list[dict[str, Any]], t: float, hint: int) -> tuple[dict[str, Any] | None, int]:
    n = len(captions)
    while hint < n and float(captions[hint]["end"]) < t:
        hint += 1
    if hint < n and float(captions[hint]["start"]) <= t <= float(captions[hint]["end"]):
        return captions[hint], hint
    return None, hint


def text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, stroke: int = 0) -> int:
    b = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
    return b[2] - b[0]


def line_width(words: list[str], draw: ImageDraw.ImageDraw, font: ImageFont.FreeTypeFont, stroke: int = 0) -> int:
    if not words:
        return 0
    space = text_width(draw, " ", font, stroke)
    return sum(text_width(draw, w, font, stroke) for w in words) + space * (len(words) - 1)


def split_lines(words: list[str], draw: ImageDraw.ImageDraw, font: ImageFont.FreeTypeFont, max_width: int, max_lines: int, stroke: int = 0) -> list[list[str]] | None:
    """Balanced, pixel-measured line breaking with hard safe-width guarantees."""
    n = len(words)
    if n == 0:
        return []
    widths = [text_width(draw, w, font, stroke) for w in words]
    space = text_width(draw, " ", font, stroke)
    if any(w > max_width for w in widths):
        return None
    prefix = [0]
    for w in widths:
        prefix.append(prefix[-1] + w)

    def width(i: int, j: int) -> int:
        return prefix[j] - prefix[i] + space * max(0, j - i - 1)

    inf = 10**30
    dp = [[inf] * (n + 1) for _ in range(max_lines + 1)]
    prev = [[-1] * (n + 1) for _ in range(max_lines + 1)]
    dp[0][0] = 0
    for lines in range(1, max_lines + 1):
        for j in range(1, n + 1):
            for i in range(j):
                if dp[lines - 1][i] >= inf:
                    continue
                w = width(i, j)
                if w > max_width:
                    continue
                ragged = max_width - w
                cost = dp[lines - 1][i] + ragged * ragged
                # Strongly discourage a one-word orphan line.
                if j - i == 1 and n > 2:
                    cost += max_width * max_width * 0.35
                # Prefer line lengths that are visually similar.
                if lines > 1 and i > 0:
                    prev_i = prev[lines - 1][i]
                    if prev_i >= 0:
                        cost += abs(width(prev_i, i) - w) * max_width * 0.18
                if cost < dp[lines][j]:
                    dp[lines][j] = cost
                    prev[lines][j] = i
    possible = [k for k in range(1, max_lines + 1) if dp[k][n] < inf]
    if not possible:
        return None
    k = min(possible, key=lambda x: dp[x][n])
    out: list[list[str]] = []
    j = n
    while k > 0:
        i = prev[k][j]
        if i < 0:
            return None
        out.append(words[i:j])
        j = i
        k -= 1
    return list(reversed(out))


def fit_page(words: list[str], draw: ImageDraw.ImageDraw, font_path: Path, target_size: int, min_size: int, max_width: int, max_lines: int, stroke: int) -> tuple[ImageFont.FreeTypeFont, list[list[str]]] | None:
    for size in range(target_size, min_size - 1, -2):
        font = ImageFont.truetype(str(font_path), size)
        lines = split_lines(words, draw, font, max_width, max_lines, stroke)
        if lines is not None:
            return font, lines
    return None


def paginate_words(words: list[str], draw: ImageDraw.ImageDraw, font_path: Path, target_size: int, min_size: int, max_width: int, max_lines: int, stroke: int) -> list[tuple[int, int, ImageFont.FreeTypeFont, list[list[str]]]]:
    """Split overlong captions into timed display pages instead of leaking off-screen."""
    pages: list[tuple[int, int, ImageFont.FreeTypeFont, list[list[str]]]] = []
    start = 0
    while start < len(words):
        best = None
        # Largest contiguous prefix that can fit in max_lines at or above min_size.
        for end in range(len(words), start, -1):
            fitted = fit_page(words[start:end], draw, font_path, target_size, min_size, max_width, max_lines, stroke)
            if fitted is not None:
                font, lines = fitted
                best = (start, end, font, lines)
                break
        if best is None:
            # Pathological single token: shrink until it fits. This still guarantees safe width.
            token = words[start]
            size = min_size
            while size > 10:
                font = ImageFont.truetype(str(font_path), size)
                if text_width(draw, token, font, stroke) <= max_width:
                    break
                size -= 1
            best = (start, start + 1, font, [[token]])
        pages.append(best)
        start = best[1]
    return pages


def active_word_index(words_data: list[dict[str, Any]], t: float) -> int:
    for i, word in enumerate(words_data):
        if float(word.get("start", 0)) <= t <= float(word.get("end", 0)) + 0.04:
            return i
    if not words_data:
        return -1
    # During tiny timestamp gaps, hold the nearest preceding word.
    preceding = [i for i, w in enumerate(words_data) if float(w.get("start", 0)) <= t]
    return preceding[-1] if preceding else 0


def render_caption_overlay(size: tuple[int, int], caption: dict[str, Any], t: float, style: dict[str, Any], font_path: Path) -> Image.Image:
    width, height = size
    short_edge_scale = min(width, height) / 1080.0
    base_size = max(28, round(float(style["base_font_size"]) * short_edge_scale))

    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    words_data = caption.get("words", [])
    words = [str(w.get("text", "")).strip() for w in words_data if str(w.get("text", "")).strip()] or str(caption.get("text", "")).split()

    side_margin = max(int(style.get("minimum_side_margin_px", 36) * short_edge_scale), int(width * float(style.get("side_margin_ratio", 0.10))))
    max_width = max(100, width - 2 * side_margin)
    min_size = max(20, round(float(style.get("min_font_size", 32)) * short_edge_scale))
    max_lines = int(style.get("max_lines", 2))
    outline = max(2, round(float(style.get("outline_width", 7)) * short_edge_scale))

    pages = paginate_words(words, draw, font_path, base_size, min_size, max_width, max_lines, outline)
    active_global = active_word_index(words_data, t)
    page = pages[0]
    for candidate in pages:
        if candidate[0] <= active_global < candidate[1]:
            page = candidate
            break
    page_start, page_end, font, lines = page

    # Stable line wrapping: animate only a restrained scale, never the layout itself.
    progress = (t - float(caption["start"])) / max(float(style.get("pop_duration", 0.16)), 0.01)
    start_scale = float(style.get("pop_start_scale", 0.92))
    visual_scale = start_scale + (1.0 - start_scale) * min(1.0, max(0.0, ease_out_back(progress)))

    shadow_offset = max(2, round(float(style.get("shadow_offset", 5)) * short_edge_scale))
    font_size = int(getattr(font, "size", base_size))
    line_gap = round(font_size * float(style.get("line_gap_ratio", 0.12)))
    glyph = draw.textbbox((0, 0), "Ag", font=font, stroke_width=outline)
    line_height = glyph[3] - glyph[1] + line_gap
    total_h = len(lines) * line_height - line_gap
    v_margin = max(round(float(style.get("minimum_vertical_margin_px", 40)) * short_edge_scale), int(height * float(style.get("vertical_safe_margin_ratio", 0.07))))
    center_y = int(height * float(style.get("position_y", 0.72)))
    y = max(v_margin, min(center_y - total_h // 2, height - v_margin - total_h))

    # Render on a local block, then apply a tiny stable pop transform.
    block_h = total_h + 2 * (outline + shadow_offset + 8)
    block_w = max_width
    block = Image.new("RGBA", (block_w, block_h), (0, 0, 0, 0))
    bdraw = ImageDraw.Draw(block)
    by = outline + 4
    local_global = page_start
    for line in lines:
        total_w = line_width(line, bdraw, font, outline)
        bx = max(outline, (block_w - total_w) // 2)
        for word in line:
            word_w = text_width(bdraw, word, font, outline)
            active = bool(style.get("word_highlight", True)) and local_global == active_global
            fill = rgba(style.get("highlight_color", "#FFD400") if active else style.get("text_color", "#FFFFFF"))
            shadow_alpha = int(255 * float(style.get("shadow_opacity", 0.55)))
            bdraw.text((bx + shadow_offset, by + shadow_offset), word, font=font, fill=rgba(style.get("shadow_color", "#000000"), shadow_alpha), stroke_width=outline, stroke_fill=rgba(style.get("shadow_color", "#000000"), shadow_alpha))
            bdraw.text((bx, by), word, font=font, fill=fill, stroke_width=outline, stroke_fill=rgba(style.get("outline_color", "#000000")))
            bx += word_w + text_width(bdraw, " ", font, outline)
            local_global += 1
        by += line_height

    if visual_scale < 0.999:
        sw = max(1, round(block.width * visual_scale))
        sh = max(1, round(block.height * visual_scale))
        block = block.resize((sw, sh), Image.Resampling.LANCZOS)
    paste_x = (width - block.width) // 2
    paste_y = y - (block.height - block_h) // 2
    # Final hard clamp is the last safety net.
    paste_x = max(side_margin, min(paste_x, width - side_margin - block.width))
    paste_y = max(v_margin, min(paste_y, height - v_margin - block.height))
    overlay.alpha_composite(block, (paste_x, paste_y))
    return overlay



def smootherstep(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x * x * x * (x * (x * 6.0 - 15.0) + 10.0)


def build_motion_keyframes(duration: float, motion: dict[str, Any]) -> list[MotionKeyframe]:
    """Create a deterministic, continuous camera path across the full video."""
    shot_duration = max(1.0, float(motion.get("shot_duration", 5.5)))
    min_zoom = max(1.0, float(motion.get("min_zoom", 1.00)))
    max_zoom = max(min_zoom, float(motion.get("max_zoom", 1.10)))
    pan_strength = max(0.0, min(1.0, float(motion.get("pan_strength", 0.78))))
    seed = int(motion.get("seed", 251))
    presets = [str(x) for x in motion.get("presets", [
        "push_in", "pull_out", "pan_left", "pan_right", "pan_up", "pan_down", "diagonal"
    ])] or ["push_in"]
    rng = random.Random(seed)

    count = max(1, math.ceil(duration / shot_duration))
    frames: list[MotionKeyframe] = []
    previous = (0.0, 0.0)
    for i in range(count + 1):
        t = min(duration, i * shot_duration)
        preset = presets[i % len(presets)]
        low, high = min_zoom, max_zoom
        if preset == "push_in":
            zoom = low if i % 2 == 0 else high
            x, y = previous
        elif preset == "pull_out":
            zoom = high if i % 2 == 0 else low
            x, y = previous
        else:
            zoom = rng.uniform(low + (high-low)*0.25, high)
            targets = {
                "pan_left": (-pan_strength, rng.uniform(-0.25, 0.25) * pan_strength),
                "pan_right": (pan_strength, rng.uniform(-0.25, 0.25) * pan_strength),
                "pan_up": (rng.uniform(-0.25, 0.25) * pan_strength, -pan_strength),
                "pan_down": (rng.uniform(-0.25, 0.25) * pan_strength, pan_strength),
                "diagonal": (rng.choice([-1.0, 1.0]) * pan_strength, rng.choice([-1.0, 1.0]) * pan_strength),
                "drift": (rng.uniform(-pan_strength, pan_strength), rng.uniform(-pan_strength, pan_strength)),
            }
            x, y = targets.get(preset, targets["drift"])
            previous = (x, y)
        frames.append(MotionKeyframe(t, zoom, x, y, preset))

    if len(frames) == 1:
        frames.append(MotionKeyframe(duration, min_zoom, 0.0, 0.0, "hold"))
    elif frames[-1].time < duration:
        last = frames[-1]
        frames.append(MotionKeyframe(duration, last.zoom, last.pan_x, last.pan_y, last.preset))
    return frames


def motion_state(keyframes: list[MotionKeyframe], t: float, hint: int) -> tuple[float, float, float, int]:
    while hint + 1 < len(keyframes) and keyframes[hint + 1].time < t:
        hint += 1
    if hint + 1 >= len(keyframes):
        k = keyframes[-1]
        return k.zoom, k.pan_x, k.pan_y, hint
    a, b = keyframes[hint], keyframes[hint + 1]
    span = max(0.001, b.time - a.time)
    u = smootherstep((t - a.time) / span)
    zoom = a.zoom + (b.zoom - a.zoom) * u
    pan_x = a.pan_x + (b.pan_x - a.pan_x) * u
    pan_y = a.pan_y + (b.pan_y - a.pan_y) * u
    return zoom, pan_x, pan_y, hint


def apply_camera_motion(frame: Image.Image, zoom: float, pan_x: float, pan_y: float, quality: str = "bicubic") -> Image.Image:
    """Apply border-safe Ken Burns motion. Pan values are normalized to [-1, 1]."""
    if zoom <= 1.0001:
        return frame
    width, height = frame.size
    crop_w = width / zoom
    crop_h = height / zoom
    max_dx = max(0.0, (width - crop_w) / 2.0)
    max_dy = max(0.0, (height - crop_h) / 2.0)
    cx = width / 2.0 + max(-1.0, min(1.0, pan_x)) * max_dx
    cy = height / 2.0 + max(-1.0, min(1.0, pan_y)) * max_dy
    left = max(0.0, min(width - crop_w, cx - crop_w / 2.0))
    top = max(0.0, min(height - crop_h, cy - crop_h / 2.0))
    box = (left, top, left + crop_w, top + crop_h)
    resample = Image.Resampling.LANCZOS if quality.lower() == "lanczos" else Image.Resampling.BICUBIC
    return frame.resize((width, height), resample=resample, box=box)

def render_video(cfg: dict[str, Any], video: Path, captions_path: Path, output: Path) -> None:
    style = load_json(ROOT / str(cfg["style"]))
    data = load_json(captions_path)
    captions = data.get("captions", [])
    if not captions:
        raise RuntimeError("captions.json innehåller inga captions.")
    info = probe(video)
    font_path = find_font(str(style.get("font_family", "Arial Rounded MT Bold")))
    print(f"\nVideo: {info.width}x{info.height}, {info.fps:.3f} fps, {info.duration:.1f} s")
    print(f"Typsnitt: {font_path}")
    print(f"Captions: {len(captions)}")
    output.parent.mkdir(parents=True, exist_ok=True)

    motion = cfg.get("motion_engine", {})
    motion_enabled = bool(motion.get("enabled", True))
    motion_mode = str(motion.get("mode", "subject_aware")).lower()
    motion_hint = 0
    motion_plans = []
    motion_keyframes = []
    if motion_enabled and motion_mode == "subject_aware":
        analysis_path = ROOT / str(motion.get("analysis_json", "output/motion_analysis_v300.json"))
        scenes = analyze_video(video, motion, analysis_path)
        semantic_plan_path = ROOT / str(cfg.get("semantic_edit_engine", {}).get("plan_json", ""))
        semantic_scenes = []
        if semantic_plan_path.exists():
            semantic_scenes = load_json(semantic_plan_path).get("scenes", [])
        if semantic_scenes:
            # The semantic timeline is authoritative. Cut detection can merge adjacent
            # identical images or quantize boundaries to the sampling interval, which
            # previously made dissolves begin after the real cut. Map each semantic
            # scene to the visual analysis covering its midpoint and preserve the exact
            # semantic start/end times.
            mapped = []
            for semantic_scene in semantic_scenes:
                start = float(semantic_scene["start"])
                end = float(semantic_scene["end"])
                midpoint = (start + end) * 0.5
                source = next((x for x in scenes if x.start <= midpoint < x.end), None)
                if source is None and scenes:
                    source = min(scenes, key=lambda x: abs(((x.start+x.end)*0.5)-midpoint))
                if source is not None:
                    mapped.append(replace(source, start=start, end=end))
            if mapped:
                scenes = mapped
                analysis_path.write_text(json.dumps({"schema_version":"3.2","video":video.name,"timeline_source":"semantic_edit_plan","scenes":[x.to_dict() for x in scenes]}, indent=2), encoding="utf-8")
        visual_guidance = None
        if semantic_scenes:
            visual_guidance = VisualDirector().build_motion_guidance(semantic_scenes)
        motion_plans = build_motion_plan(scenes, motion, visual_guidance)
        plan_path = ROOT / str(motion.get("plan_json", "output/motion_plan_v302.json"))
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(json.dumps({"schema_version":"4.2","visual_director_version":"4.2.0","plans":[p.to_dict() for p in motion_plans]}, indent=2), encoding="utf-8")
        print(f"Motion Engine / Visual Director 4.2.0: intent-guided camera + cinematic cross-dissolve ({len(scenes)} scener, {len(motion_plans)} planer)")
        for i, plan in enumerate(motion_plans[:8], 1):
            print(f"  {i:02d}. {plan.start:5.1f}-{plan.end:5.1f}s  {plan.preset:20s} fokus=({plan.focus_x:.2f},{plan.focus_y:.2f}) conf={plan.confidence:.2f}")
    elif motion_enabled:
        motion_keyframes = build_motion_keyframes(info.duration, motion)
        print(f"Motion Engine 3.0: legacy 2.5.1 ({len(motion_keyframes)} keyframes)")
    else:
        print("Motion Engine 3.0: avstängd")

    decoder = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video), "-f", "rawvideo", "-pix_fmt", "rgb24", "-vsync", "0", "-"], stdout=subprocess.PIPE)
    encoder = subprocess.Popen([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{info.width}x{info.height}",
        "-r", f"{info.fps:.8f}", "-i", "-", "-i", str(video),
        "-map", "0:v:0", "-map", "1:a?", "-c:v", str(cfg.get("video_codec", "libx264")),
        "-preset", str(cfg.get("preset", "medium")), "-crf", str(cfg.get("crf", 19)),
        "-pix_fmt", "yuv420p", "-c:a", str(cfg.get("audio_codec", "aac")),
        "-b:a", str(cfg.get("audio_bitrate", "192k")), "-movflags", "+faststart", "-shortest", str(output)
    ], stdin=subprocess.PIPE)
    assert decoder.stdout is not None and encoder.stdin is not None

    frame_bytes = info.width * info.height * 3
    frame_index = 0
    hint = 0

    # Cinematic cut smoothing. Camera continuity alone cannot soften a hard edit:
    # the complete image still changes on a single frame. For each analyzed scene
    # boundary, hold the final camera-processed frame from the outgoing scene and
    # dissolve it into the incoming scene over a short configurable interval.
    transition_enabled = bool(motion.get("smooth_transitions", True)) and motion_mode == "subject_aware" and bool(motion_plans)
    transition_duration = max(0.0, min(1.0, float(motion.get("transition_duration", 0.65))))
    transition_boundaries = []
    if transition_enabled:
        validate_transition_contract(semantic_scenes, motion_plans, info.fps)
        transition_boundaries = build_transition_boundaries(
            semantic_scenes, info.fps, transition_duration,
            float(motion.get("transition_max_scene_fraction", 0.20)),
        )
    cut_index = 0
    transition_from: Image.Image | None = None
    transition_start = -1.0
    previous_camera_frame: Image.Image | None = None

    try:
        while True:
            raw = decoder.stdout.read(frame_bytes)
            if len(raw) != frame_bytes:
                break
            t = frame_index / info.fps
            frame = Image.frombytes("RGB", (info.width, info.height), raw)
            if motion_enabled:
                if motion_mode == "subject_aware" and motion_plans:
                    zoom, pan_x, pan_y, motion_hint = intelligent_motion_state(
                        motion_plans, t, motion_hint, transition_duration if transition_enabled else 0.0
                    )
                else:
                    zoom, pan_x, pan_y, motion_hint = motion_state(motion_keyframes, t, motion_hint)
                frame = apply_camera_motion(frame, zoom, pan_x, pan_y, str(motion.get("resample", "bicubic")))

            # Start a dissolve exactly when the next analyzed scene begins. Use the
            # outgoing scene's final transformed frame as the source, so neither
            # zoom nor pan jumps are exposed during the visual hand-off.
            if transition_enabled and cut_index < len(transition_boundaries) and frame_index >= transition_boundaries[cut_index].frame:
                boundary = transition_boundaries[cut_index]
                if previous_camera_frame is not None:
                    transition_from = previous_camera_frame.copy()
                    transition_start = boundary.time
                    transition_duration = boundary.duration
                cut_index += 1

            incoming_camera_frame = frame.copy()
            if transition_from is not None and transition_duration > 0.0:
                progress = (t - transition_start) / transition_duration
                if progress < 1.0:
                    alpha = smooth_alpha(progress)
                    frame = Image.blend(transition_from, frame, alpha)
                else:
                    transition_from = None

            # Save the camera image before captions. Captions are rendered after the
            # dissolve and therefore remain crisp instead of ghosting between cuts.
            # Keep the genuine incoming camera frame for the next boundary. Storing
            # the blended frame here recursively contaminated later dissolves.
            previous_camera_frame = incoming_camera_frame
            caption, hint = active_caption(captions, t, hint)
            if caption is not None:
                overlay = render_caption_overlay((info.width, info.height), caption, t, style, font_path)
                frame = Image.alpha_composite(frame.convert("RGBA"), overlay).convert("RGB")
            encoder.stdin.write(frame.tobytes())
            frame_index += 1
            if frame_index % max(1, round(info.fps * 5)) == 0:
                pct = min(100.0, 100.0 * t / max(info.duration, 0.1))
                print(f"Renderar: {t:6.1f}s / {info.duration:.1f}s ({pct:5.1f}%)", flush=True)
    finally:
        decoder.stdout.close()
        encoder.stdin.close()
    dec = decoder.wait()
    enc = encoder.wait()
    if dec != 0 or enc != 0:
        raise RuntimeError(f"FFmpeg misslyckades (decoder={dec}, encoder={enc}).")
    print(f"\nKLART: {output}")


def main() -> None:
    require("ffmpeg")
    require("ffprobe")
    cfg = load_json(CONFIG_PATH)
    video = ROOT / str(cfg["input_video"])
    captions_json = ROOT / str(cfg["captions_json"])
    captions_srt = ROOT / str(cfg["captions_srt"])
    output = ROOT / str(cfg["output_video"])
    if not video.exists():
        raise FileNotFoundError(f"Videon saknas: {video}")
    build_caption_core(cfg, video, captions_json, captions_srt)
    video = build_semantic_edit(cfg, captions_json, video, ROOT)
    render_video(cfg, video, captions_json, output)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nFEL: {exc}", file=sys.stderr)
        raise
