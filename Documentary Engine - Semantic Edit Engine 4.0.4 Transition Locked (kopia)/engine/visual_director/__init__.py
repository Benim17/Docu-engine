"""Visual Director orchestration boundary for Documentary Engine 4.1.1."""

from .director import VisualDirector, VisualDirectorContractError
from .models import SceneVisualPlan
from .shot_library import SUPPORTED_SHOT_TYPES, ShotClassification, classify_scene

__all__ = [
    "SUPPORTED_SHOT_TYPES",
    "SceneVisualPlan",
    "ShotClassification",
    "VisualDirector",
    "VisualDirectorContractError",
    "classify_scene",
]
