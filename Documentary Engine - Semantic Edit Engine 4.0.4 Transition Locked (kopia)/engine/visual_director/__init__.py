"""Visual Director orchestration boundary for Documentary Engine 4.1.2."""

from .director import VisualDirector, VisualDirectorContractError
from .models import SceneVisualPlan
from .narrative_intent import (
    SUPPORTED_NARRATIVE_INTENTS,
    NarrativeClassification,
    classify_narrative_intent,
)
from .shot_library import SUPPORTED_SHOT_TYPES, ShotClassification, classify_scene

__all__ = [
    "SUPPORTED_SHOT_TYPES",
    "SUPPORTED_NARRATIVE_INTENTS",
    "NarrativeClassification",
    "SceneVisualPlan",
    "ShotClassification",
    "VisualDirector",
    "VisualDirectorContractError",
    "classify_scene",
    "classify_narrative_intent",
]
