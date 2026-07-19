from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


HIGHLIGHT_STOP_WORDS = {
    "about", "after", "again", "because", "before", "could", "every", "first", "from",
    "have", "into", "more", "other", "over", "said", "some", "than", "that", "their",
    "them", "then", "there", "these", "they", "this", "through", "under", "very", "were",
    "what", "when", "where", "which", "while", "with", "would", "your",
}
POSITIONS = frozenset({"top", "center", "bottom"})
HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


class CaptionDirectorError(ValueError):
    """Raised when Caption Director input violates its pure-layout contract."""


@dataclass(frozen=True)
class CaptionWrap:
    caption_id: int
    line_word_indices: tuple[tuple[int, ...], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "caption_id": self.caption_id,
            "line_word_indices": [list(line) for line in self.line_word_indices],
        }


@dataclass(frozen=True)
class SceneCaptionPlan:
    scene: int
    position: str
    vertical_anchor: float
    max_width: float
    max_lines: int
    safe_margin: float
    highlight_words: tuple[str, ...]
    highlight_color: str
    readability: Mapping[str, Any]
    caption_layouts: tuple[CaptionWrap, ...]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["highlight_words"] = list(self.highlight_words)
        payload["readability"] = dict(self.readability)
        payload["caption_layouts"] = [layout.to_dict() for layout in self.caption_layouts]
        return payload


class CaptionDirector:
    """Deterministic, renderer-independent caption layout authority."""

    version = "4.5.0"
    schema_version = "1.0"

    def build_plan(
        self,
        semantic_plan: Mapping[str, Any],
        captions_payload: Mapping[str, Any],
        image_intelligence_plan: Mapping[str, Any] | None,
        motion_analysis: Mapping[str, Any] | None,
        config: Mapping[str, Any],
    ) -> dict[str, Any]:
        # Snapshots make mutation a contract violation, including nested payloads.
        inputs = (semantic_plan, captions_payload, image_intelligence_plan, motion_analysis, config)
        snapshots = deepcopy(inputs)
        try:
            result = self._build_plan(
                semantic_plan, captions_payload, image_intelligence_plan or {},
                motion_analysis or {}, config,
            )
            self.validate_schema(result, len(semantic_plan.get("scenes", [])))
            if inputs != snapshots:
                raise CaptionDirectorError("Caption Director mutated an input payload.")
            return result
        except (CaptionDirectorError, KeyError, TypeError, ValueError):
            raise
        except Exception as exc:
            raise CaptionDirectorError(f"Caption layout planning failed: {exc}") from exc

    def _build_plan(
        self,
        semantic_plan: Mapping[str, Any],
        captions_payload: Mapping[str, Any],
        image_plan: Mapping[str, Any],
        motion_analysis: Mapping[str, Any],
        config: Mapping[str, Any],
    ) -> dict[str, Any]:
        scenes = semantic_plan.get("scenes", [])
        captions = captions_payload.get("captions", [])
        if not isinstance(scenes, list) or not isinstance(captions, list):
            raise CaptionDirectorError("Semantic scenes and captions must be lists.")

        settings = config.get("caption_director", {})
        if not isinstance(settings, Mapping):
            raise CaptionDirectorError("caption_director config must be an object.")
        default_position = str(settings.get("default_position", "bottom")).lower()
        if default_position not in POSITIONS:
            raise CaptionDirectorError(f"Unsupported default caption position: {default_position}")
        safe_margin = self._fraction(settings.get("safe_margin", 0.08), "safe_margin", 0.02, 0.20)
        max_width = self._fraction(settings.get("max_width", 0.82), "max_width", 0.40, 1.0 - 2 * safe_margin)
        max_lines = int(settings.get("max_lines", 2))
        if not 1 <= max_lines <= 4:
            raise CaptionDirectorError("max_lines must be between 1 and 4.")
        highlight_color = str(settings.get("highlight_color", "#FFD54A")).upper()
        if not HEX_COLOR.fullmatch(highlight_color):
            raise CaptionDirectorError("highlight_color must be a six-digit hex color.")
        allow_reposition = bool(settings.get("allow_scene_reposition", True))
        max_highlights = max(0, min(3, int(settings.get("max_highlight_words", 2))))
        max_chars = max(8, min(60, int(settings.get("max_characters_per_line", 24))))

        analyses = motion_analysis.get("scenes", [])
        if not isinstance(analyses, list):
            analyses = []
        image_scene_count = len(image_plan.get("scenes", [])) if isinstance(image_plan.get("scenes", []), list) else 0

        plans: list[SceneCaptionPlan] = []
        previous_position = default_position
        for index, scene in enumerate(scenes):
            self._validate_scene(scene, index)
            scene_captions = self._captions_for_scene(captions, scene)
            analysis = self._analysis_for_scene(analyses, scene)
            required, detection_reason = self._required_position(analysis)
            if allow_reposition and required is not None:
                position = required
            else:
                position = previous_position if allow_reposition else default_position
            anchor = self._anchor(position, settings)
            highlights = self._highlight_words(scene_captions, scene, max_highlights)
            layouts = tuple(
                CaptionWrap(
                    caption_id=int(caption.get("id", caption_index + 1)),
                    line_word_indices=self._wrap_caption(caption, max_chars, max_lines),
                )
                for caption_index, caption in enumerate(scene_captions)
            )
            line_reason = (
                f"Up to {max_lines} lines with {max_width:.2f} maximum width preserve reading comfort."
            )
            highlight_reason = (
                "Conservative highlights: " + ", ".join(f'\"{word}\"' for word in highlights) + "."
                if highlights else "No conservative highlight candidate was found."
            )
            stability_reason = (
                "Placement moved only to avoid detected visual content."
                if position != previous_position else "Placement remains stable."
            )
            reason = " ".join((
                detection_reason,
                f"{position.capitalize()} placement chosen.",
                line_reason,
                highlight_reason,
                f"Safe margin {safe_margin:.2f} maintained.",
                stability_reason,
            ))
            plans.append(SceneCaptionPlan(
                scene=index + 1,
                position=position,
                vertical_anchor=anchor,
                max_width=max_width,
                max_lines=max_lines,
                safe_margin=safe_margin,
                highlight_words=tuple(highlights),
                highlight_color=highlight_color,
                readability={
                    "contrast_required": True,
                    "outline_recommended": True,
                    "caption_count": len(scene_captions),
                    "face_avoidance_applied": bool(analysis and int(analysis.get("face_count", 0)) > 0),
                    "subject_avoidance_applied": bool(analysis and analysis.get("source") in {"face", "saliency"}),
                },
                caption_layouts=layouts,
                reason=reason,
            ))
            previous_position = position

        return {
            "schema_version": self.schema_version,
            "caption_director_version": self.version,
            "status": "planned",
            "scene_count": len(plans),
            "source_scene_count": len(scenes),
            "source_caption_count": len(captions),
            "image_intelligence_scene_count": image_scene_count,
            "scenes": [plan.to_dict() for plan in plans],
        }

    @staticmethod
    def _fraction(value: Any, name: str, low: float, high: float) -> float:
        result = round(float(value), 4)
        if not low <= result <= high:
            raise CaptionDirectorError(f"{name} must be between {low:.2f} and {high:.2f}.")
        return result

    @staticmethod
    def _validate_scene(scene: Any, index: int) -> None:
        if not isinstance(scene, Mapping):
            raise CaptionDirectorError(f"Semantic scene {index} must be an object.")
        start, end = float(scene["start"]), float(scene["end"])
        if end <= start:
            raise CaptionDirectorError(f"Semantic scene {index} has invalid timing.")

    @staticmethod
    def _captions_for_scene(captions: Sequence[Any], scene: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        start, end = float(scene["start"]), float(scene["end"])
        result = []
        for caption in captions:
            if not isinstance(caption, Mapping):
                raise CaptionDirectorError("Every caption must be an object.")
            caption_start, caption_end = float(caption["start"]), float(caption["end"])
            if caption_end < caption_start:
                raise CaptionDirectorError("Caption timing is invalid.")
            midpoint = (caption_start + caption_end) * 0.5
            if start <= midpoint < end or (midpoint == end and end == start):
                result.append(caption)
        return result

    @staticmethod
    def _analysis_for_scene(analyses: Sequence[Any], scene: Mapping[str, Any]) -> Mapping[str, Any] | None:
        midpoint = (float(scene["start"]) + float(scene["end"])) * 0.5
        valid = [item for item in analyses if isinstance(item, Mapping)]
        covering = [item for item in valid if float(item.get("start", 0.0)) <= midpoint < float(item.get("end", 0.0))]
        if covering:
            return min(covering, key=lambda item: (float(item.get("start", 0.0)), float(item.get("end", 0.0))))
        return None

    @staticmethod
    def _required_position(analysis: Mapping[str, Any] | None) -> tuple[str | None, str]:
        if not analysis:
            return None, "No reliable face or subject metadata is available."
        focus_y = max(0.0, min(1.0, float(analysis.get("focus_y", 0.5))))
        faces = max(0, int(analysis.get("face_count", 0)))
        source = str(analysis.get("source", "unknown"))
        label = "Face" if faces > 0 else "Primary subject"
        if focus_y >= 0.55:
            return "top", f"{label} detected in the lower frame at y={focus_y:.2f}."
        if focus_y <= 0.45:
            return "bottom", f"{label} detected in the upper frame at y={focus_y:.2f}."
        if source in {"face", "saliency"}:
            return "bottom", f"{label} detected near center at y={focus_y:.2f}; bottom is the safer edge."
        return None, "No reliable face or subject metadata is available."

    @staticmethod
    def _anchor(position: str, settings: Mapping[str, Any]) -> float:
        defaults = {"top": 0.18, "center": 0.50, "bottom": 0.72}
        key = f"{position}_vertical_anchor"
        return CaptionDirector._fraction(settings.get(key, defaults[position]), key, 0.08, 0.92)

    @staticmethod
    def _caption_words(caption: Mapping[str, Any]) -> list[str]:
        words = [str(item.get("text", "")).strip() for item in caption.get("words", []) if isinstance(item, Mapping)]
        return [word for word in words if word] or str(caption.get("text", "")).split()

    @classmethod
    def _highlight_words(
        cls, captions: Sequence[Mapping[str, Any]], scene: Mapping[str, Any], limit: int,
    ) -> list[str]:
        if limit == 0:
            return []
        keywords = {str(term).casefold() for term in scene.get("match_terms", []) if str(term).strip()}
        chosen: list[str] = []
        seen: set[str] = set()
        for caption in captions:
            for raw in cls._caption_words(caption):
                clean = re.sub(r"^[^A-Za-z0-9]+|[^A-Za-z0-9-]+$", "", raw)
                folded = clean.casefold()
                conservative = bool(re.fullmatch(r"(?:19|20)\d{2}", clean)) or (
                    folded in keywords and len(folded) >= 5 and folded not in HIGHLIGHT_STOP_WORDS
                )
                if conservative and folded not in seen:
                    chosen.append(clean)
                    seen.add(folded)
                    if len(chosen) == limit:
                        return chosen
        return chosen

    @classmethod
    def _wrap_caption(
        cls, caption: Mapping[str, Any], max_characters: int, max_lines: int,
    ) -> tuple[tuple[int, ...], ...]:
        words = cls._caption_words(caption)
        if not words:
            return tuple()
        lines: list[list[int]] = [[]]
        current_length = 0
        for index, word in enumerate(words):
            extra = len(word) + (1 if lines[-1] else 0)
            if lines[-1] and current_length + extra > max_characters and len(lines) < max_lines:
                lines.append([])
                current_length = 0
                extra = len(word)
            lines[-1].append(index)
            current_length += extra
        return tuple(tuple(line) for line in lines)

    @classmethod
    def validate_schema(cls, plan: Mapping[str, Any], expected_scene_count: int) -> None:
        required_top = {
            "schema_version", "caption_director_version", "status", "scene_count",
            "source_scene_count", "source_caption_count", "image_intelligence_scene_count", "scenes",
        }
        if set(plan) != required_top:
            raise CaptionDirectorError("Caption Director output schema keys are invalid.")
        scenes = plan["scenes"]
        if not isinstance(scenes, list) or len(scenes) != expected_scene_count:
            raise CaptionDirectorError("Caption Director scene count changed.")
        required_scene = {
            "scene", "position", "vertical_anchor", "max_width", "max_lines", "safe_margin",
            "highlight_words", "highlight_color", "readability", "caption_layouts", "reason",
        }
        for index, scene in enumerate(scenes, 1):
            if set(scene) != required_scene or scene["scene"] != index:
                raise CaptionDirectorError(f"Caption Director scene {index} schema is invalid.")
            if scene["position"] not in POSITIONS or not scene["reason"]:
                raise CaptionDirectorError(f"Caption Director scene {index} layout is invalid.")
            if not 0.08 <= float(scene["vertical_anchor"]) <= 0.92:
                raise CaptionDirectorError(f"Caption Director scene {index} anchor is invalid.")
            if not 0.02 <= float(scene["safe_margin"]) <= 0.20:
                raise CaptionDirectorError(f"Caption Director scene {index} margin is invalid.")
            if not HEX_COLOR.fullmatch(str(scene["highlight_color"])):
                raise CaptionDirectorError(f"Caption Director scene {index} highlight color is invalid.")


def _read_optional(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise CaptionDirectorError(f"Input must contain a JSON object: {path.name}")
    return payload


def _fallback_plan(semantic_plan: Mapping[str, Any], captions_payload: Mapping[str, Any], config: Mapping[str, Any], reason: str) -> dict[str, Any]:
    settings = config.get("caption_director", {}) if isinstance(config.get("caption_director", {}), Mapping) else {}
    position = str(settings.get("default_position", "bottom"))
    if position not in POSITIONS:
        position = "bottom"
    def safe_float(value: Any, default: float, low: float, high: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        return round(parsed, 4) if low <= parsed <= high else default

    def safe_int(value: Any, default: int, low: int, high: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if low <= parsed <= high else default

    anchor = safe_float(
        settings.get(f"{position}_vertical_anchor"),
        {"top": 0.18, "center": 0.50, "bottom": 0.72}[position], 0.08, 0.92,
    )
    margin = safe_float(settings.get("safe_margin"), 0.08, 0.02, 0.20)
    width = safe_float(settings.get("max_width"), 0.82, 0.40, 1.0 - 2 * margin)
    lines = safe_int(settings.get("max_lines"), 2, 1, 4)
    color = str(settings.get("highlight_color", "#FFD54A")).upper()
    if not HEX_COLOR.fullmatch(color):
        color = "#FFD54A"
    scenes = semantic_plan.get("scenes", []) if isinstance(semantic_plan.get("scenes", []), list) else []
    plans = [{
        "scene": index + 1, "position": position, "vertical_anchor": anchor,
        "max_width": width, "max_lines": lines, "safe_margin": margin,
        "highlight_words": [], "highlight_color": color,
        "readability": {"contrast_required": True, "outline_recommended": True, "caption_count": 0,
                        "face_avoidance_applied": False, "subject_avoidance_applied": False},
        "caption_layouts": [],
        "reason": f"Fail-closed fallback preserved existing placement. {reason}",
    } for index, _ in enumerate(scenes)]
    captions = captions_payload.get("captions", []) if isinstance(captions_payload.get("captions", []), list) else []
    return {
        "schema_version": CaptionDirector.schema_version,
        "caption_director_version": CaptionDirector.version,
        "status": "fallback",
        "scene_count": len(plans),
        "source_scene_count": len(scenes),
        "source_caption_count": len(captions),
        "image_intelligence_scene_count": 0,
        "scenes": plans,
    }


def write_caption_director_plan(root: Path, config: Mapping[str, Any]) -> Path:
    """Read only approved inputs and always emit a render-safe layout plan."""
    semantic_path = root / str(config.get("semantic_edit_engine", {}).get("plan_json", "output/semantic_edit_plan.json"))
    captions_path = root / str(config.get("captions_json", "output/captions.json"))
    image_path = root / str(config.get("semantic_edit_engine", {}).get("image_intelligence_json", "output/image_intelligence_plan.json"))
    motion_path = root / str(config.get("motion_engine", {}).get("analysis_json", "output/motion_analysis.json"))
    output_path = root / str(config.get("caption_director", {}).get("plan_json", "output/caption_director_plan.json"))
    semantic: dict[str, Any] = {}
    captions: dict[str, Any] = {}
    try:
        semantic = _read_optional(semantic_path)
        captions = _read_optional(captions_path)
        plan = CaptionDirector().build_plan(
            semantic, captions, _read_optional(image_path), _read_optional(motion_path), config,
        )
    except Exception as exc:
        plan = _fallback_plan(semantic, captions, config, str(exc))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path
