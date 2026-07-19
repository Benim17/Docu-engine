from __future__ import annotations

import json
import re
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Sequence


STORY_SHAPES = frozenset({
    "linear_explanation", "investigation", "rise_and_fall", "problem_solution",
    "chronological_history", "contrast", "mystery_reveal", "contested_resolution", "unknown",
})
RESOLUTION_TYPES = frozenset({
    "closed", "open_question", "ambiguous", "reflective", "call_to_action", "none", "unknown",
})
STORY_ROLES = frozenset({
    "hook", "setup", "context", "development", "evidence", "complication", "contrast",
    "counterargument", "revelation", "turning_point", "climax", "resolution", "epilogue",
    "transition", "unknown",
})
STORY_PHASES = frozenset({
    "opening", "setup", "rising_action", "middle", "complication", "turning_point",
    "climax", "falling_action", "resolution", "closing", "unknown",
})
CONTINUITY_RELATIONS = frozenset({
    "introduces_topic", "continues_previous", "adds_evidence", "deepens_context",
    "contrasts_previous", "challenges_previous", "reframes_previous",
    "reveals_new_information", "escalates_tension", "resolves_previous",
    "summarizes_story", "opens_question", "unknown",
})

STOP_WORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "for", "from", "had",
    "has", "have", "he", "her", "his", "in", "is", "it", "its", "of", "on", "or", "she",
    "that", "the", "their", "them", "they", "this", "to", "was", "were", "with", "would",
    "att", "av", "de", "den", "det", "en", "ett", "för", "från", "han", "har", "hon", "i",
    "med", "och", "om", "på", "som", "till", "var", "är",
})

SIGNALS = {
    "contrast": ("but", "however", "yet", "despite", "instead", "although", "men", "dock", "däremot", "trots", "istället"),
    "challenge": ("claimed", "alleged", "challenged", "disputed", "denied", "questioned", "påstod", "ifrågasatte", "bestred"),
    "cause": ("because", "therefore", "as a result", "the result", "därför", "eftersom", "resultatet"),
    "reveal": ("the truth", "revealed", "discovered", "uncovered", "came to light", "avslöjade", "upptäckte", "sanningen"),
    "evidence": ("evidence", "according to", "records", "documents", "data", "report", "bevis", "enligt", "journaler", "dokument", "rapport"),
    "escalation": ("crisis", "danger", "risk", "worse", "urgent", "death", "killed", "kris", "fara", "risk", "värre", "död"),
    "conclusion": ("finally", "ultimately", "in the end", "therefore", "conclusion", "slutligen", "till slut", "sammanfattningsvis"),
    "reflection": ("looking back", "legacy", "remembered", "years later", "i efterhand", "arv", "år senare"),
    "chronology": ("years later", "months later", "in 19", "in 20", "then", "later", "år senare", "månader senare", "sedan", "senare"),
    "call": ("must act", "we need", "take action", "you can", "agera", "vi måste", "du kan"),
}


class StoryDirectorError(ValueError):
    """Raised when Story Director cannot honor its metadata-only contract."""


def _clamp(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 4)


def _tokens(text: str) -> list[str]:
    return [
        token for token in re.findall(r"[a-zåäö0-9]+(?:-[a-zåäö0-9]+)?", text.casefold())
        if len(token) > 1 and token not in STOP_WORDS
    ]


def _has(text: str, signal: str) -> bool:
    folded = text.casefold()
    return any(phrase in folded for phrase in SIGNALS[signal])


@dataclass(frozen=True)
class StoryScene:
    scene_id: str
    scene_index: int
    story_role: str
    story_phase: str
    emotional_intensity: float
    tension: float
    information_density: float
    revelation_strength: float
    continuity_relation: str
    story_beats: tuple[str, ...]
    reason: str
    confidence: float
    fallback_used: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["story_beats"] = list(self.story_beats)
        return payload


class StoryDirector:
    """Whole-story analysis that produces metadata and changes no source payload."""

    version = "4.6.0"
    schema_version = "4.6.0"

    def build_plan(
        self,
        semantic_plan: Mapping[str, Any],
        captions_payload: Mapping[str, Any] | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        inputs = (semantic_plan, captions_payload, config)
        snapshot = deepcopy(inputs)
        try:
            plan = self._build_plan(semantic_plan, captions_payload or {}, config or {})
            self.validate_schema(plan, len(semantic_plan.get("scenes", [])))
            if inputs != snapshot:
                raise StoryDirectorError("Story Director mutated an input payload.")
            return plan
        except (StoryDirectorError, KeyError, TypeError, ValueError):
            raise
        except Exception as exc:
            raise StoryDirectorError(f"Story analysis failed: {exc}") from exc

    def _build_plan(
        self,
        semantic_plan: Mapping[str, Any],
        captions_payload: Mapping[str, Any],
        config: Mapping[str, Any],
    ) -> dict[str, Any]:
        scenes = semantic_plan.get("scenes", [])
        captions = captions_payload.get("captions", [])
        if not isinstance(scenes, list) or not isinstance(captions, list):
            raise StoryDirectorError("Semantic scenes and captions must be lists.")
        settings = config.get("story_director", {})
        if settings and not isinstance(settings, Mapping):
            raise StoryDirectorError("story_director config must be an object.")

        texts = [self._scene_text(scene, captions, index) for index, scene in enumerate(scenes)]
        analyzed: list[StoryScene] = []
        previous_tokens: set[str] = set()
        for index, (scene, text) in enumerate(zip(scenes, texts)):
            if not isinstance(scene, Mapping):
                raise StoryDirectorError(f"Semantic scene {index} must be an object.")
            analyzed.append(self._analyze_scene(index, len(scenes), text, previous_tokens))
            previous_tokens = set(_tokens(text))

        analyzed, turning_index, climax_index = self._assign_structural_roles(analyzed)
        document = self._document_story(texts, analyzed, turning_index, climax_index)
        edges = self._story_edges(analyzed)
        fallback_count = sum(scene.fallback_used for scene in analyzed)
        warnings = []
        if not scenes:
            warnings.append("No semantic scenes were available for story analysis.")
        if fallback_count:
            warnings.append(f"{fallback_count} scene(s) used deterministic fallback analysis.")
        return {
            "schema_version": self.schema_version,
            "story_director_version": self.version,
            "status": "fallback" if fallback_count or not scenes else "planned",
            "scene_count": len(analyzed),
            "document_story": document,
            "story_graph": {"edges": edges},
            "scenes": [scene.to_dict() for scene in analyzed],
            "diagnostics": {
                "fallback_count": fallback_count,
                "warning_count": len(warnings),
                "warnings": warnings,
                "deterministic": True,
            },
        }

    @staticmethod
    def _scene_text(scene: Any, captions: Sequence[Any], index: int) -> str:
        if not isinstance(scene, Mapping):
            raise StoryDirectorError(f"Semantic scene {index} must be an object.")
        pieces = [
            str(scene.get("voiceover_text", "")), str(scene.get("narration", "")),
            " ".join(str(value) for value in scene.get("match_terms", [])),
        ]
        try:
            start, end = float(scene["start"]), float(scene["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise StoryDirectorError(f"Semantic scene {index} has invalid timing.") from exc
        if end <= start:
            raise StoryDirectorError(f"Semantic scene {index} has invalid timing.")
        for caption in captions:
            if not isinstance(caption, Mapping):
                continue
            try:
                midpoint = (float(caption["start"]) + float(caption["end"])) * 0.5
            except (KeyError, TypeError, ValueError):
                continue
            if start <= midpoint < end:
                pieces.append(str(caption.get("text", "")))
        return " ".join(piece.strip() for piece in pieces if piece.strip())

    def _analyze_scene(
        self, index: int, count: int, text: str, previous_tokens: set[str],
    ) -> StoryScene:
        scene_id = f"scene_{index + 1:03d}"
        if not _tokens(text):
            return self._fallback_scene(index, count)
        relative = index / max(1, count - 1)
        tokens = set(_tokens(text))
        question = "?" in text or "question" in tokens or "fråga" in tokens
        contrast = _has(text, "contrast")
        challenge = _has(text, "challenge")
        evidence = _has(text, "evidence")
        reveal = _has(text, "reveal")
        escalation = _has(text, "escalation")
        conclusion = _has(text, "conclusion")
        reflection = _has(text, "reflection")

        if count == 1:
            role = "hook" if question else "development"
        elif index == 0:
            role = "hook" if question or reveal else "setup"
        elif index == count - 1:
            role = "epilogue" if reflection else "resolution"
        elif reveal:
            role = "revelation"
        elif contrast and challenge:
            role = "counterargument"
        elif contrast:
            role = "contrast"
        elif evidence:
            role = "evidence"
        elif escalation:
            role = "complication"
        else:
            role = "development"

        base_tension = 0.25 + 0.5 * (1.0 - abs(relative - 0.72))
        tension = _clamp(base_tension + 0.13 * escalation + 0.10 * reveal + 0.06 * contrast + 0.05 * question)
        revelation = _clamp(0.08 + 0.62 * reveal + 0.18 * evidence + 0.10 * challenge)
        emotional = _clamp(0.28 + 0.36 * escalation + 0.20 * reveal + 0.10 * question + 0.08 * contrast)
        information = _clamp(0.18 + min(0.62, len(_tokens(text)) / 55.0) + 0.12 * evidence + 0.08 * _has(text, "cause"))
        continuity = self._continuity(index, text, tokens, previous_tokens, role, tension)
        beats = tuple(name for name in (
            "question" if question else "", "contrast" if contrast else "",
            "challenge" if challenge else "", "evidence" if evidence else "",
            "revelation" if reveal else "", "escalation" if escalation else "",
            "conclusion" if conclusion else "", "reflection" if reflection else "",
        ) if name)
        confidence = _clamp(0.58 + 0.045 * len(beats) + (0.08 if role not in {"development", "unknown"} else 0.0))
        reason = (
            f"Scene {index + 1}/{count}; role {role} from relative position and "
            f"signals: {', '.join(beats) if beats else 'general narrative continuation'}."
        )
        return StoryScene(
            scene_id, index, role, self._phase(index, count, role), emotional, tension,
            information, revelation, continuity, beats, reason, confidence, False,
        )

    @staticmethod
    def _phase(index: int, count: int, role: str) -> str:
        if count <= 1:
            return "opening"
        if index == 0:
            return "opening"
        if index == count - 1:
            return "closing"
        role_phase = {
            "complication": "complication", "turning_point": "turning_point",
            "climax": "climax", "resolution": "resolution", "epilogue": "closing",
        }
        if role in role_phase:
            return role_phase[role]
        relative = index / max(1, count - 1)
        if relative <= 0.22:
            return "setup"
        if relative <= 0.48:
            return "rising_action"
        if relative <= 0.68:
            return "middle"
        if relative <= 0.84:
            return "falling_action"
        return "resolution"

    @staticmethod
    def _continuity(
        index: int, text: str, tokens: set[str], previous: set[str], role: str, tension: float,
    ) -> str:
        if index == 0:
            return "opens_question" if "?" in text else "introduces_topic"
        if _has(text, "contrast") and _has(text, "challenge"):
            return "challenges_previous"
        if _has(text, "contrast"):
            return "contrasts_previous"
        if role == "revelation":
            return "reveals_new_information"
        if role == "evidence":
            return "adds_evidence"
        if role in {"resolution", "epilogue"}:
            return "opens_question" if "?" in text else "resolves_previous"
        if tension >= 0.72:
            return "escalates_tension"
        overlap = len(tokens & previous) / max(1, len(tokens | previous))
        return "continues_previous" if overlap >= 0.08 else "deepens_context"

    @staticmethod
    def _assign_structural_roles(
        scenes: Sequence[StoryScene],
    ) -> tuple[list[StoryScene], int, int]:
        if not scenes:
            return [], 0, 0
        if len(scenes) == 1:
            return list(scenes), 0, 0
        interior = list(range(1, len(scenes) - 1)) or list(range(len(scenes)))
        climax = max(interior, key=lambda i: (scenes[i].tension, scenes[i].revelation_strength, -i))
        changes = [0.0]
        for previous, current in zip(scenes, scenes[1:]):
            changes.append(abs(current.tension - previous.tension) + current.revelation_strength * 0.35)
        turning_candidates = [index for index in interior if index != climax]
        turning = max(turning_candidates, key=lambda i: (changes[i], -i)) if turning_candidates else climax
        decorated = list(scenes)
        if turning != climax:
            scene = decorated[turning]
            decorated[turning] = replace(
                scene, story_role="turning_point", story_phase="turning_point",
                reason=scene.reason + " Document-level change analysis marks this scene as the turning point.",
            )
        scene = decorated[climax]
        decorated[climax] = replace(
            scene, story_role="climax", story_phase="climax",
            reason=scene.reason + " Document-level tension analysis marks this scene as the climax.",
        )
        return decorated, turning, climax

    def _document_story(
        self, texts: Sequence[str], scenes: Sequence[StoryScene],
        turning_index: int, climax_index: int,
    ) -> dict[str, Any]:
        if not scenes:
            return {
                "story_shape": "unknown", "opening_strategy": "unknown",
                "central_question": "Unknown.", "turning_point_scene": 0, "climax_scene": 0,
                "resolution_type": "unknown", "overall_tension_curve": [],
                "story_coherence_score": 0.0,
                "reason": "Deterministic fallback because no scenes were available.",
                "confidence": 0.0, "fallback_used": True,
            }
        joined = " ".join(texts)
        question_count = sum("?" in text for text in texts)
        reveal_count = sum(_has(text, "reveal") for text in texts)
        evidence_count = sum(_has(text, "evidence") for text in texts)
        contrast_count = sum(_has(text, "contrast") for text in texts)
        challenge_count = sum(_has(text, "challenge") for text in texts)
        chronology_count = sum(_has(text, "chronology") for text in texts)
        if question_count and challenge_count:
            shape = "contested_resolution"
        elif question_count and reveal_count:
            shape = "mystery_reveal"
        elif evidence_count >= 2 or question_count:
            shape = "investigation"
        elif contrast_count >= 2:
            shape = "contrast"
        elif _has(joined, "cause") and _has(joined, "conclusion"):
            shape = "problem_solution"
        elif chronology_count >= 2:
            shape = "chronological_history"
        else:
            shape = "linear_explanation"

        first = texts[0] if texts else ""
        opening = (
            "direct_question" if "?" in first else
            "chronological_setup" if _has(first, "chronology") else
            "immediate_revelation" if _has(first, "reveal") else
            "contextual_setup"
        )
        last = texts[-1] if texts else ""
        resolution = (
            "open_question" if "?" in last else
            "call_to_action" if _has(last, "call") else
            "reflective" if _has(last, "reflection") else
            "ambiguous" if _has(last, "challenge") or _has(last, "contrast") else
            "closed" if _has(last, "conclusion") else "none"
        )
        overlap_scores = []
        token_sets = [set(_tokens(text)) for text in texts]
        for left, right in zip(token_sets, token_sets[1:]):
            overlap_scores.append(len(left & right) / max(1, len(left | right)))
        coherence = _clamp(0.55 + (sum(overlap_scores) / max(1, len(overlap_scores))) * 0.45)
        central = self._central_question(texts)
        return {
            "story_shape": shape,
            "opening_strategy": opening,
            "central_question": central,
            "turning_point_scene": turning_index,
            "climax_scene": climax_index,
            "resolution_type": resolution,
            "overall_tension_curve": [scene.tension for scene in scenes],
            "story_coherence_score": coherence,
            "reason": (
                f"{shape} selected from {question_count} question, {evidence_count} evidence, "
                f"{reveal_count} reveal, and {contrast_count} contrast signal(s)."
            ),
            "confidence": _clamp(0.56 + min(0.32, 0.04 * (question_count + evidence_count + reveal_count + contrast_count))),
            "fallback_used": any(scene.fallback_used for scene in scenes),
        }

    @staticmethod
    def _central_question(texts: Sequence[str]) -> str:
        for text in texts:
            sentences = re.split(r"(?<=[.!?])\s+", text.strip())
            for sentence in sentences:
                if "?" in sentence:
                    return sentence.strip()
        counts = Counter(token for text in texts for token in _tokens(text))
        terms = [token for token, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:3]]
        return f"What explains {' '.join(terms)}?" if terms else "Unknown."

    @staticmethod
    def _story_edges(scenes: Sequence[StoryScene]) -> list[dict[str, Any]]:
        edges = []
        for source, target in zip(scenes, scenes[1:]):
            relation = target.continuity_relation
            strength = _clamp(0.52 + 0.22 * target.confidence + 0.16 * target.tension)
            edges.append({
                "from_scene": source.scene_id,
                "to_scene": target.scene_id,
                "relation": relation,
                "strength": strength,
                "reason": f"Scene {target.scene_index + 1} {relation.replace('_', ' ')} in existing story order.",
            })
        return edges

    @classmethod
    def _fallback_scene(cls, index: int, count: int) -> StoryScene:
        phase = "opening" if index == 0 else "closing" if index == count - 1 and count > 1 else "middle"
        relation = "introduces_topic" if index == 0 else "continues_previous"
        return StoryScene(
            f"scene_{index + 1:03d}", index, "development", phase,
            0.5, 0.5, 0.5, 0.0, relation, tuple(),
            "Deterministic fallback due to insufficient story metadata.", 0.25, True,
        )

    @classmethod
    def fallback_plan(
        cls, semantic_plan: Mapping[str, Any] | None, reason: str,
    ) -> dict[str, Any]:
        raw = semantic_plan.get("scenes", []) if isinstance(semantic_plan, Mapping) else []
        scenes = raw if isinstance(raw, list) else []
        decisions = [cls._fallback_scene(index, len(scenes)) for index in range(len(scenes))]
        warnings = [f"Story Director fallback: {reason}"]
        return {
            "schema_version": cls.schema_version,
            "story_director_version": cls.version,
            "status": "fallback",
            "scene_count": len(decisions),
            "document_story": {
                "story_shape": "unknown", "opening_strategy": "unknown",
                "central_question": "Unknown.", "turning_point_scene": 0, "climax_scene": 0,
                "resolution_type": "unknown", "overall_tension_curve": [0.5 for _ in decisions],
                "story_coherence_score": 0.0,
                "reason": "Deterministic fallback due to insufficient story metadata.",
                "confidence": 0.0, "fallback_used": True,
            },
            "story_graph": {"edges": cls._story_edges(decisions)},
            "scenes": [scene.to_dict() for scene in decisions],
            "diagnostics": {
                "fallback_count": len(decisions), "warning_count": 1,
                "warnings": warnings, "deterministic": True,
            },
        }

    @classmethod
    def validate_schema(cls, plan: Mapping[str, Any], expected_scene_count: int) -> None:
        required = {
            "schema_version", "story_director_version", "status", "scene_count",
            "document_story", "story_graph", "scenes", "diagnostics",
        }
        if set(plan) != required or plan["scene_count"] != expected_scene_count:
            raise StoryDirectorError("Story Director top-level schema is invalid.")
        scenes = plan["scenes"]
        if not isinstance(scenes, list) or len(scenes) != expected_scene_count:
            raise StoryDirectorError("Story Director scene count changed.")
        for index, scene in enumerate(scenes):
            if scene.get("scene_id") != f"scene_{index + 1:03d}" or scene.get("scene_index") != index:
                raise StoryDirectorError("Story Director scene order changed.")
            if scene.get("story_role") not in STORY_ROLES or scene.get("story_phase") not in STORY_PHASES:
                raise StoryDirectorError("Story Director scene enum is invalid.")
            if scene.get("continuity_relation") not in CONTINUITY_RELATIONS:
                raise StoryDirectorError("Story Director continuity enum is invalid.")
            for key in ("emotional_intensity", "tension", "information_density", "revelation_strength", "confidence"):
                if not 0.0 <= float(scene[key]) <= 1.0:
                    raise StoryDirectorError(f"Story Director numeric field {key} is out of range.")
        document = plan["document_story"]
        if document.get("story_shape") not in STORY_SHAPES or document.get("resolution_type") not in RESOLUTION_TYPES:
            raise StoryDirectorError("Story Director document enum is invalid.")
        for key in ("story_coherence_score", "confidence"):
            if not 0.0 <= float(document[key]) <= 1.0:
                raise StoryDirectorError(f"Story Director document field {key} is out of range.")
        if any(not 0.0 <= float(value) <= 1.0 for value in document["overall_tension_curve"]):
            raise StoryDirectorError("Story Director tension curve is out of range.")


def serialize_story_plan(plan: Mapping[str, Any]) -> str:
    return json.dumps(plan, ensure_ascii=False, indent=2) + "\n"


def _read_object(path: Path, required: bool) -> dict[str, Any]:
    if not path.exists():
        if required:
            raise StoryDirectorError(f"Required input is missing: {path.name}")
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise StoryDirectorError(f"Input must be a JSON object: {path.name}")
    return payload


def write_story_director_plan(root: Path, config: Mapping[str, Any]) -> Path:
    """Read authoritative story inputs and always write deterministic metadata."""
    semantic_path = root / str(config.get("semantic_edit_engine", {}).get("plan_json", "output/semantic_edit_plan.json"))
    captions_path = root / str(config.get("captions_json", "output/captions.json"))
    output_path = root / str(config.get("story_director", {}).get("plan_json", "output/story_director_plan.json"))
    semantic: dict[str, Any] = {}
    try:
        semantic = _read_object(semantic_path, required=True)
        captions = _read_object(captions_path, required=False)
        plan = StoryDirector().build_plan(semantic, captions, config)
    except Exception as exc:
        plan = StoryDirector.fallback_plan(semantic, str(exc))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(serialize_story_plan(plan), encoding="utf-8")
    return output_path
