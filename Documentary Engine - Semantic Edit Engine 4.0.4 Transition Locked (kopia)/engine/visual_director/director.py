from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from .models import SceneVisualPlan
from .motion_guidance import MotionGuidance, build_motion_guidance
from .narrative_intent import classify_narrative_intent
from .shot_library import classify_scene


class VisualDirectorContractError(RuntimeError):
    """Raised when the identity-only Visual Director changes semantic data."""


class VisualDirector:
    """Documentary Engine 4.2.0 identity-only orchestration boundary."""

    version = "4.2.0"

    def build_visual_plan(
        self,
        semantic_scenes: Sequence[Mapping[str, Any]],
        context: Mapping[str, Any] | None = None,
    ) -> list[SceneVisualPlan]:
        """Classify separate visual intent without mutating semantic scene data."""
        del context
        plans = []
        total_scene_count = len(semantic_scenes)
        for index, scene in enumerate(semantic_scenes):
            classification = classify_scene(scene)
            narrative = classify_narrative_intent(scene, index, total_scene_count)
            plans.append(SceneVisualPlan(
                scene_index=index,
                visual_intent=classification.visual_intent,
                confidence=classification.confidence,
                reason=classification.reason,
                narrative_intent=narrative.narrative_intent,
                narrative_confidence=narrative.narrative_confidence,
                narrative_reason=narrative.narrative_reason,
            ))
        return plans

    def build_motion_guidance(
        self,
        semantic_scenes: Sequence[Mapping[str, Any]],
        context: Mapping[str, Any] | None = None,
    ) -> list[MotionGuidance]:
        """Translate separate visual plans into deterministic Motion Engine hints."""
        return build_motion_guidance(self.build_visual_plan(semantic_scenes, context))

    def direct(
        self,
        semantic_scenes: Sequence[Mapping[str, Any]],
        context: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Return an equivalent deep copy of the completed semantic scene list."""
        original = deepcopy(list(semantic_scenes))
        directed = deepcopy(original)

        visual_plan = self.build_visual_plan(original, context)
        if len(visual_plan) != len(original):
            raise VisualDirectorContractError(
                "Visual Director contract violation: visual plan count changed."
            )

        self.validate_preservation(original, directed)
        return directed

    @staticmethod
    def validate_preservation(
        original: Sequence[Mapping[str, Any]],
        directed: Sequence[Mapping[str, Any]],
    ) -> None:
        """Fail loudly unless every semantic scene and all metadata are preserved."""
        if len(original) != len(directed):
            raise VisualDirectorContractError(
                "Visual Director contract violation: scene count changed "
                f"from {len(original)} to {len(directed)}."
            )

        required_fields = ("image", "start", "end", "duration")
        missing = object()
        for index, (source, result) in enumerate(zip(original, directed)):
            if not isinstance(source, Mapping) or not isinstance(result, Mapping):
                raise VisualDirectorContractError(
                    f"Visual Director contract violation: scene {index} is not a mapping."
                )

            for field in required_fields:
                before = source.get(field, missing)
                after = result.get(field, missing)
                if before != after:
                    raise VisualDirectorContractError(
                        "Visual Director contract violation: "
                        f"scene {index} field '{field}' changed from {before!r} to {after!r}."
                    )

            if source != result:
                source_keys = set(source)
                result_keys = set(result)
                if source_keys != result_keys:
                    detail = "metadata keys changed"
                else:
                    changed = sorted(key for key in source_keys if source[key] != result[key])
                    detail = "metadata changed: " + ", ".join(changed)
                raise VisualDirectorContractError(
                    f"Visual Director contract violation in scene {index}: {detail}."
                )
