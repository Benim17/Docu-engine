from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable
import math


STANDARD_NAME = "Smooth Transition Standard"
STANDARD_VERSION = "1.0"


@dataclass(frozen=True)
class TransitionBoundary:
    scene_index: int
    frame: int
    time: float
    duration_frames: int
    duration: float


def smooth_alpha(progress: float) -> float:
    """Cosine-eased dissolve; stable at both endpoints."""
    p = max(0.0, min(1.0, float(progress)))
    return 0.5 - 0.5 * math.cos(math.pi * p)


def build_transition_boundaries(
    semantic_scenes: list[dict[str, Any]],
    fps: float,
    requested_duration: float = 0.65,
    max_scene_fraction: float = 0.20,
) -> list[TransitionBoundary]:
    if fps <= 0:
        raise ValueError("fps must be positive")
    boundaries: list[TransitionBoundary] = []
    for index in range(1, len(semantic_scenes)):
        previous = semantic_scenes[index - 1]
        current = semantic_scenes[index]
        previous_duration = float(previous["end"]) - float(previous["start"])
        current_duration = float(current["end"]) - float(current["start"])
        duration = min(
            max(0.0, float(requested_duration)),
            max(0.0, previous_duration * max_scene_fraction),
            max(0.0, current_duration * max_scene_fraction),
        )
        frame = round(float(current["start"]) * fps)
        duration_frames = max(1, round(duration * fps)) if duration > 0 else 0
        boundaries.append(TransitionBoundary(
            scene_index=index,
            frame=frame,
            time=frame / fps,
            duration_frames=duration_frames,
            duration=duration_frames / fps if duration_frames else 0.0,
        ))
    return boundaries


def validate_transition_contract(
    semantic_scenes: list[dict[str, Any]],
    motion_plans: Iterable[Any],
    fps: float,
) -> None:
    """Fail closed if another engine drifts away from the semantic master timeline."""
    plans = list(motion_plans)
    if not semantic_scenes:
        raise RuntimeError("Transition Contract: semantic timeline is empty.")
    if len(plans) != len(semantic_scenes):
        raise RuntimeError(
            f"Transition Contract: semantic scenes ({len(semantic_scenes)}) and motion plans ({len(plans)}) differ."
        )
    tolerance = 0.5 / fps
    previous_end = None
    for index, (scene, plan) in enumerate(zip(semantic_scenes, plans)):
        start = float(scene["start"])
        end = float(scene["end"])
        if end <= start:
            raise RuntimeError(f"Transition Contract: scene {index + 1} has invalid duration.")
        if previous_end is not None and abs(start - previous_end) > tolerance:
            raise RuntimeError(
                f"Transition Contract: gap/overlap before scene {index + 1}: {start - previous_end:+.6f}s."
            )
        if abs(float(plan.start) - start) > tolerance or abs(float(plan.end) - end) > tolerance:
            raise RuntimeError(
                f"Transition Contract: motion plan {index + 1} drifted from semantic timeline."
            )
        previous_end = end
