from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from .models import SceneVisualPlan


class VisualDirectorContractError(RuntimeError):
    """Raised when the identity-only Visual Director changes semantic data."""


class VisualDirector:
    """Documentary Engine 4.1.0 identity-only orchestration boundary."""

    version = "4.1.0"

    def build_visual_plan(
        self,
        semantic_scenes: Sequence[Mapping[str, Any]],
        context: Mapping[str, Any] | None = None,
    ) -> list[SceneVisualPlan]:
        """Return neutral visual intent without reading or mutating scene data."""
        del context
        return [SceneVisualPlan(scene_index=index) for index, _ in enumerate(semantic_scenes)]

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
