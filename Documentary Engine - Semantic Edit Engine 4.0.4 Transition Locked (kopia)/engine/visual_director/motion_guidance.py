from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .models import SceneVisualPlan


@dataclass(frozen=True)
class MotionGuidance:
    """Deterministic, renderer-neutral guidance for one motion plan."""

    scene_index: int
    preferred_preset: str
    intensity: float
    hold_fraction: float
    visual_intent: str
    narrative_intent: str
    reason: str


_SHOT_PRESETS = {
    "establishing": "slow_pull_out",
    "wide": "slow_pull_out",
    "portrait": "subject_push_in",
    "detail": "focus_reveal",
    "document": "documentary_float",
    "map": "focus_reveal",
    "archive": "documentary_float",
}

_NARRATIVE_PRESETS = {
    "introduction": "safe_push_in",
    "context": "documentary_float",
    "explanation": "documentary_float",
    "development": "documentary_float",
    "escalation": "subject_push_in",
    "reveal": "focus_reveal",
    "climax": "focus_reveal",
    "reflection": "slow_pull_out",
    "conclusion": "slow_pull_out",
}


def build_motion_guidance(plans: Sequence[SceneVisualPlan]) -> list[MotionGuidance]:
    """Translate visual plans without changing scenes, timing, or image selection."""
    guidance = []
    for expected_index, plan in enumerate(plans):
        if plan.scene_index != expected_index:
            raise ValueError(
                "Visual-to-Motion contract violation: visual plan indices must be "
                "contiguous and aligned with semantic scene order."
            )

        narrative_preset = _NARRATIVE_PRESETS.get(plan.narrative_intent, "documentary_float")
        shot_preset = _SHOT_PRESETS.get(plan.visual_intent)
        # Strong story moments lead; otherwise composition determines camera behavior.
        if plan.narrative_intent in {"escalation", "reveal", "climax", "conclusion"}:
            preset = narrative_preset
            source = "narrative"
        elif shot_preset is not None:
            preset = shot_preset
            source = "visual"
        else:
            preset = narrative_preset
            source = "narrative"

        confidence = (float(plan.confidence) + float(plan.narrative_confidence)) * 0.5
        intensity = round(max(0.35, min(1.0, 0.35 + confidence * 0.65)), 3)
        hold = 0.12 if plan.narrative_intent in {"reflection", "conclusion"} else 0.0
        if plan.visual_intent in {"document", "archive"}:
            hold = max(hold, 0.08)
        guidance.append(MotionGuidance(
            scene_index=plan.scene_index,
            preferred_preset=preset,
            intensity=intensity,
            hold_fraction=hold,
            visual_intent=plan.visual_intent,
            narrative_intent=plan.narrative_intent,
            reason=f"{source.title()} intent selected {preset}.",
        ))
    return guidance
