from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SceneVisualPlan:
    """Future-facing visual intent for one semantic scene.

    These values are kept separate from semantic scene dictionaries and are
    not consumed by motion, transitions, captions, or rendering.
    """

    scene_index: int
    shot_type: str = "neutral"
    emotion: str = "neutral"
    pace: str = "neutral"
    importance: float = 0.0
    cinematic_intent: str = "identity"
    visual_intent: str = "medium"
    confidence: float = 0.50
    reason: str = "Default fallback."
