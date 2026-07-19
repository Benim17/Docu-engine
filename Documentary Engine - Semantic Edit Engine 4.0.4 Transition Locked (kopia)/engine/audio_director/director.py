from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from .models import AUDIO_INTENTS, EMOTIONAL_TONES, PLANNER_VERSION, AudioContractError


KNOWN_STORY_ROLES = frozenset({
    "hook", "setup", "context", "development", "evidence", "complication", "contrast",
    "counterargument", "revelation", "turning_point", "climax", "resolution", "epilogue",
    "transition", "unknown",
})
KNOWN_STORY_PHASES = frozenset({
    "opening", "setup", "rising_action", "middle", "complication", "turning_point",
    "climax", "falling_action", "resolution", "closing", "unknown",
})
SUPPORTED_STORY_DIRECTOR_VERSIONS = frozenset({("4.6.0", "4.6.0")})

NEGATIONS = frozenset({"not", "no", "never", "without", "inte", "ingen", "inget", "inga", "ej", "utan"})
TOKEN_RE = re.compile(r"[a-zåäö0-9]+(?:-[a-zåäö0-9]+)?", re.IGNORECASE)

# Text is deliberately secondary. Single lexical matches never select climax,
# triumphant, or another aggressive class without matching structural metadata.
TEXT_SIGNALS = {
    "establish": ("begin", "begins", "began", "introduction", "first", "börjar", "började", "inledning", "först"),
    "build": ("growing", "increasing", "mounting", "toward", "växande", "ökande", "byggdes", "mot"),
    "tension": ("danger", "threat", "crisis", "risk", "urgent", "fara", "hot", "kris", "risk", "akut"),
    "release": ("relief", "released", "revealed", "truth", "lättnad", "släpptes", "avslöjades", "sanningen"),
    "reflection": ("remember", "looking back", "retrospect", "years later", "minns", "i efterhand", "år senare"),
    "transition": ("meanwhile", "elsewhere", "next", "later", "samtidigt", "på annat håll", "därefter", "senare"),
    "resolution": ("resolved", "solved", "ultimately", "in the end", "conclusion", "löstes", "slutligen", "till slut", "slutsats"),
    "calm": ("calm", "quiet", "steady", "gentle", "lugn", "stilla", "stadig", "mjuk"),
    "reflective": ("remember", "reflection", "looking back", "legacy", "minns", "eftertanke", "i efterhand", "arv"),
    "mysterious": ("unknown", "hidden", "secret", "mystery", "unanswered", "okänd", "dold", "hemlig", "mysterium", "obesvarad"),
    "tense": ("danger", "threat", "crisis", "risk", "fear", "fara", "hot", "kris", "risk", "rädsla"),
    "somber": ("death", "loss", "grief", "tragedy", "mourning", "död", "förlust", "sorg", "tragedi", "sörjande"),
    "hopeful": ("hope", "recovery", "possible", "future", "chance", "hopp", "återhämtning", "möjlig", "möjligt", "framtid", "möjlighet"),
    "uplifting": ("joy", "celebrate", "inspired", "renewed", "glädje", "firar", "inspirerad", "förnyad"),
    "triumphant": ("victory", "triumph", "won", "success", "seger", "triumf", "vann", "framgång"),
    "dramatic": ("decisive", "turning point", "confrontation", "avgörande", "vändpunkt", "konfrontation"),
}

SEMANTIC_INTENT_MAP = {
    "introduction": "establish",
    "context": "support",
    "explanation": "support",
    "development": "build",
    "escalation": "tension",
    "reveal": "release",
    "reflection": "reflection",
    "conclusion": "resolution",
}

STORY_ROLE_INTENT = {
    "hook": "establish",
    "setup": "establish",
    "context": "support",
    "development": "build",
    "evidence": "support",
    "complication": "build",
    "contrast": "tension",
    "counterargument": "tension",
    "revelation": "release",
    "turning_point": "build",
    "resolution": "resolution",
    "epilogue": "reflection",
    "transition": "transition",
}

CONFLICTING_TONES = frozenset({
    frozenset({"hopeful", "somber"}),
    frozenset({"calm", "tense"}),
    frozenset({"uplifting", "somber"}),
    frozenset({"triumphant", "somber"}),
})


def _canonical_strings(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values), key=lambda value: (value.casefold(), value)))


def _clamp(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 4)


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(match.group(0).casefold() for match in TOKEN_RE.finditer(text))


def _normalized_text(scene: Mapping[str, Any]) -> str:
    values = [
        scene.get("voiceover_text", ""), scene.get("narration", ""), scene.get("text", ""),
        scene.get("image_description", ""),
    ]
    for key in ("match_terms", "semantic_keywords", "keywords", "tags"):
        raw = scene.get(key, ())
        if isinstance(raw, (list, tuple, set, frozenset)):
            values.extend(sorted((str(value) for value in raw), key=lambda value: (value.casefold(), value)))
    return " ".join(str(value).strip() for value in values if str(value).strip())


def _phrase_tokens(phrase: str) -> tuple[str, ...]:
    return _tokens(phrase)


def _phrase_occurs(tokens: tuple[str, ...], phrase: str) -> bool:
    wanted = _phrase_tokens(phrase)
    if not wanted or len(wanted) > len(tokens):
        return False
    return any(tokens[index:index + len(wanted)] == wanted for index in range(len(tokens) - len(wanted) + 1))


def _phrase_is_negated(tokens: tuple[str, ...], phrase: str) -> bool:
    wanted = _phrase_tokens(phrase)
    if not wanted:
        return False
    for index in range(len(tokens) - len(wanted) + 1):
        if tokens[index:index + len(wanted)] == wanted:
            if any(token in NEGATIONS for token in tokens[max(0, index - 3):index]):
                return True
    return False


def _text_evidence(text: str) -> dict[str, tuple[str, ...]]:
    tokens = _tokens(text)
    evidence: dict[str, tuple[str, ...]] = {}
    for category in sorted(TEXT_SIGNALS):
        accepted = []
        for phrase in TEXT_SIGNALS[category]:
            if _phrase_occurs(tokens, phrase) and not _phrase_is_negated(tokens, phrase):
                accepted.append(phrase)
        evidence[category] = _canonical_strings(accepted)
    return evidence


@dataclass(frozen=True)
class StorySceneMetadata:
    story_role: str
    story_phase: str
    emotional_intensity: float | None
    tension: float | None
    revelation_strength: float | None
    continuity_relation: str | None


@dataclass(frozen=True)
class SceneAudioAnalysis:
    scene_id: str
    scene_index: int
    start: float
    end: float
    duration: float
    accepted_signals: tuple[str, ...]
    ignored_signals: tuple[str, ...]
    warnings: tuple[str, ...]
    resolved_conflicts: tuple[str, ...]
    audio_intent: str
    emotional_tone: str
    confidence: float
    fallback_used: bool
    fallback_reason: str | None
    rationale: str

    def __post_init__(self) -> None:
        if self.audio_intent not in AUDIO_INTENTS or self.emotional_tone not in EMOTIONAL_TONES:
            raise AudioContractError("Internal intent/tone analysis contains an unknown enum.")
        if not 0.0 <= self.confidence <= 1.0:
            raise AudioContractError("Internal intent/tone confidence must be within 0.0–1.0.")
        for values, name in (
            (self.accepted_signals, "accepted_signals"),
            (self.ignored_signals, "ignored_signals"),
            (self.warnings, "warnings"),
            (self.resolved_conflicts, "resolved_conflicts"),
        ):
            if values != _canonical_strings(values):
                raise AudioContractError(f"Internal {name} must be canonically sorted and unique.")


class AudioDirector:
    """Step 2 classifier for scene audio intent and emotional tone only."""

    version = PLANNER_VERSION

    def analyze_intent_and_tone(
        self,
        semantic_plan: Mapping[str, Any],
        story_plan: Mapping[str, Any] | None = None,
    ) -> tuple[SceneAudioAnalysis, ...]:
        inputs = (semantic_plan, story_plan)
        snapshot = deepcopy(inputs)
        semantic_scenes = self._semantic_scenes(semantic_plan)
        matched_story, story_issue = self._match_story_plan(semantic_scenes, story_plan)
        analyses = tuple(
            self._classify_scene(
                scene, index, len(semantic_scenes),
                matched_story[index] if matched_story is not None else None,
                story_issue,
            )
            for index, scene in enumerate(semantic_scenes)
        )
        if inputs != snapshot:
            raise AudioContractError("Audio Director mutated intent/tone input metadata.")
        return analyses

    @staticmethod
    def _semantic_scenes(semantic_plan: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        if not isinstance(semantic_plan, Mapping):
            raise AudioContractError("semantic_edit_plan must be an object.")
        scenes = semantic_plan.get("scenes")
        if not isinstance(scenes, list):
            raise AudioContractError("semantic_edit_plan.scenes must be a list.")
        validated = []
        seen_ids: set[str] = set()
        for index, scene in enumerate(scenes):
            if not isinstance(scene, Mapping):
                raise AudioContractError(f"Semantic scene {index} must be an object.")
            scene_id = str(scene.get("scene_id") or f"scene_{index + 1:03d}")
            if not scene_id.strip() or scene_id in seen_ids:
                raise AudioContractError("Semantic scene IDs must be non-empty and unique.")
            try:
                start, end = float(scene["start"]), float(scene["end"])
                duration = float(scene.get("duration", end - start))
            except (KeyError, TypeError, ValueError) as exc:
                raise AudioContractError(f"Semantic scene {index} timing is invalid.") from exc
            if end <= start or abs((end - start) - duration) > 0.001:
                raise AudioContractError(f"Semantic scene {index} timing is inconsistent.")
            seen_ids.add(scene_id)
            validated.append(scene)
        return validated

    def _match_story_plan(
        self,
        semantic_scenes: Sequence[Mapping[str, Any]],
        story_plan: Mapping[str, Any] | None,
    ) -> tuple[tuple[StorySceneMetadata, ...] | None, str | None]:
        if story_plan is None:
            return None, "story_director_plan_missing"
        if not isinstance(story_plan, Mapping):
            return None, "story_director_plan_not_object"
        story_scenes = story_plan.get("scenes")
        if not isinstance(story_scenes, list):
            return None, "story_director_scenes_invalid"
        if story_plan.get("scene_count") != len(semantic_scenes) or len(story_scenes) != len(semantic_scenes):
            return None, "story_director_scene_count_mismatch"
        version = story_plan.get("story_director_version")
        schema = story_plan.get("schema_version")
        if not isinstance(version, str) or not version.strip() or not isinstance(schema, str) or not schema.strip():
            return None, "story_director_version_missing"
        if (schema, version) not in SUPPORTED_STORY_DIRECTOR_VERSIONS:
            return None, f"story_director_version_incompatible:{schema}:{version}"

        matched = []
        for index, (semantic, story) in enumerate(zip(semantic_scenes, story_scenes)):
            if not isinstance(story, Mapping):
                return None, f"story_director_scene_{index}_invalid"
            semantic_id = str(semantic.get("scene_id") or f"scene_{index + 1:03d}")
            if story.get("scene_id") != semantic_id or story.get("scene_index") != index:
                return None, f"story_director_scene_{index}_identity_mismatch"
            if not self._optional_timing_matches(semantic, story):
                return None, f"story_director_scene_{index}_timing_mismatch"
            role = str(story.get("story_role", "unknown"))
            phase = str(story.get("story_phase", "unknown"))
            matched.append(StorySceneMetadata(
                story_role=role,
                story_phase=phase,
                emotional_intensity=self._optional_unit(story.get("emotional_intensity")),
                tension=self._optional_unit(story.get("tension")),
                revelation_strength=self._optional_unit(story.get("revelation_strength")),
                continuity_relation=(
                    str(story["continuity_relation"])
                    if isinstance(story.get("continuity_relation"), str) else None
                ),
            ))
        return tuple(matched), None

    @staticmethod
    def _optional_timing_matches(semantic: Mapping[str, Any], story: Mapping[str, Any]) -> bool:
        for key in ("start", "end", "duration"):
            if key not in story:
                continue
            try:
                semantic_value = float(semantic.get(key, float(semantic["end"]) - float(semantic["start"])))
                story_value = float(story[key])
            except (KeyError, TypeError, ValueError):
                return False
            if abs(semantic_value - story_value) > 0.001:
                return False
        return True

    @staticmethod
    def _optional_unit(value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
        return result if 0.0 <= result <= 1.0 else None

    def _classify_scene(
        self,
        scene: Mapping[str, Any],
        index: int,
        scene_count: int,
        story: StorySceneMetadata | None,
        story_issue: str | None,
    ) -> SceneAudioAnalysis:
        scene_id = str(scene.get("scene_id") or f"scene_{index + 1:03d}")
        start, end = float(scene["start"]), float(scene["end"])
        duration = float(scene.get("duration", end - start))
        text = _normalized_text(scene)
        tokens = _tokens(text)
        evidence = _text_evidence(text)
        accepted: list[str] = []
        ignored: list[str] = []
        warnings: list[str] = []
        conflicts: list[str] = []
        structural_strength = 0.0

        if story_issue:
            warnings.append(story_issue)
            ignored.append("story_director_metadata")
        elif story is not None:
            if story.story_role in KNOWN_STORY_ROLES:
                accepted.append(f"story_role:{story.story_role}")
                structural_strength += 0.42 if story.story_role != "unknown" else 0.0
            else:
                warnings.append(f"unknown_story_role:{story.story_role}")
                ignored.append(f"story_role:{story.story_role}")
            if story.story_phase in KNOWN_STORY_PHASES:
                accepted.append(f"story_phase:{story.story_phase}")
                structural_strength += 0.10 if story.story_phase != "unknown" else 0.0
            else:
                warnings.append(f"unknown_story_phase:{story.story_phase}")
                ignored.append(f"story_phase:{story.story_phase}")
            for name, value in (
                ("story_tension", story.tension),
                ("story_emotional_intensity", story.emotional_intensity),
                ("story_revelation_strength", story.revelation_strength),
            ):
                if value is not None:
                    accepted.append(f"{name}:{value:.4f}")
                    structural_strength += 0.05
                else:
                    ignored.append(name)

        semantic_intent = str(scene.get("narrative_intent", ""))
        if semantic_intent in SEMANTIC_INTENT_MAP:
            accepted.append(f"semantic_intent:{semantic_intent}")
            structural_strength += 0.20
        elif semantic_intent:
            warnings.append(f"unknown_semantic_intent:{semantic_intent}")
            ignored.append(f"semantic_intent:{semantic_intent}")

        for category, matches in evidence.items():
            if matches:
                accepted.extend(f"text_{category}:{match}" for match in matches)

        audio_intent, intent_conflict = self._choose_intent(
            scene, story, semantic_intent, evidence, bool(tokens), index, scene_count,
        )
        if intent_conflict:
            conflicts.append(intent_conflict)
        tone, tone_conflicts = self._choose_tone(story, evidence)
        conflicts.extend(tone_conflicts)

        text_categories = sum(bool(matches) for matches in evidence.values())
        confidence = 0.24 + structural_strength + min(0.16, text_categories * 0.025)
        if story_issue:
            confidence -= 0.08
        confidence -= min(0.24, len(conflicts) * 0.12)
        if audio_intent in {"climax", "tension"} or tone in {"triumphant", "dramatic", "tense"}:
            confidence += 0.05
        confidence = _clamp(confidence)

        fallback_used = story is None and semantic_intent not in SEMANTIC_INTENT_MAP
        fallback_reason = None
        if fallback_used:
            fallback_reason = (
                "No compatible structural metadata; conservative semantic-text fallback used."
                if tokens else "No usable structural or text metadata; neutral fallback used."
            )
        rationale = (
            f"Intent {audio_intent} and tone {tone} selected from "
            f"{len(accepted)} accepted signal(s); {len(conflicts)} conflict(s) resolved."
        )
        return SceneAudioAnalysis(
            scene_id=scene_id,
            scene_index=index,
            start=round(start, 4),
            end=round(end, 4),
            duration=round(duration, 4),
            accepted_signals=_canonical_strings(accepted),
            ignored_signals=_canonical_strings(ignored),
            warnings=_canonical_strings(warnings),
            resolved_conflicts=_canonical_strings(conflicts),
            audio_intent=audio_intent,
            emotional_tone=tone,
            confidence=confidence,
            fallback_used=fallback_used,
            fallback_reason=fallback_reason,
            rationale=rationale,
        )

    @staticmethod
    def _choose_intent(
        scene: Mapping[str, Any],
        story: StorySceneMetadata | None,
        semantic_intent: str,
        evidence: Mapping[str, tuple[str, ...]],
        has_text: bool,
        index: int,
        scene_count: int,
    ) -> tuple[str, str | None]:
        candidates: dict[str, float] = {}

        def add(intent: str, score: float) -> None:
            candidates[intent] = round(candidates.get(intent, 0.0) + score, 4)

        if story is not None and story.story_role in STORY_ROLE_INTENT:
            add(STORY_ROLE_INTENT[story.story_role], 0.85)
        if story is not None and story.story_role == "climax":
            supported = (
                story.story_phase == "climax"
                and (story.tension or 0.0) >= 0.70
                and ((story.emotional_intensity or 0.0) >= 0.60 or (story.revelation_strength or 0.0) >= 0.65)
            )
            if supported:
                add("climax", 1.25)
        if story is not None:
            if story.story_phase in {"rising_action", "turning_point", "complication"}:
                add("build", 0.32)
            if story.story_phase == "resolution":
                add("resolution", 0.32)
            if story.story_phase == "closing" and story.story_role == "epilogue":
                add("reflection", 0.32)
            if (story.tension or 0.0) >= 0.72 and story.story_role in {"complication", "contrast", "counterargument"}:
                add("tension", 0.38)

        mapped = SEMANTIC_INTENT_MAP.get(semantic_intent)
        if mapped:
            add(mapped, 0.52)

        for intent in ("establish", "build", "tension", "release", "reflection", "transition", "resolution"):
            if evidence.get(intent):
                add(intent, min(0.34, 0.18 + 0.06 * len(evidence[intent])))

        if index == 0 and scene_count > 1 and evidence.get("establish"):
            add("establish", 0.16)

        if not candidates:
            return ("support" if has_text else "neutral"), None

        ranked = sorted(candidates.items(), key=lambda item: (-item[1], item[0]))
        winner, top = ranked[0]
        second = ranked[1] if len(ranked) > 1 else None
        conflict = None
        if second and abs(top - second[1]) <= 0.08 and {winner, second[0]} == {"build", "resolution"}:
            conflict = "build_resolution_conflict_resolved_to_support"
            winner = "support"
        if winner == "climax" and top < 1.2:
            conflict = "unsupported_climax_downgraded_to_build"
            winner = "build"
        return winner, conflict

    @staticmethod
    def _choose_tone(
        story: StorySceneMetadata | None,
        evidence: Mapping[str, tuple[str, ...]],
    ) -> tuple[str, tuple[str, ...]]:
        scores: dict[str, float] = {}

        def add(tone: str, score: float) -> None:
            scores[tone] = round(scores.get(tone, 0.0) + score, 4)

        if story is not None:
            tension = story.tension or 0.0
            emotion = story.emotional_intensity or 0.0
            role = story.story_role
            if role in {"epilogue"}:
                add("reflective", 0.78)
            if role == "climax" and tension >= 0.70 and emotion >= 0.60:
                add("dramatic", 0.82)
            if role in {"complication", "contrast", "counterargument"} and tension >= 0.65:
                add("tense", 0.72)
            if role in {"context", "setup"} and tension <= 0.35 and emotion <= 0.35:
                add("calm", 0.62)
            if role == "revelation" or (story.revelation_strength or 0.0) >= 0.65:
                add("mysterious", 0.58)

        for tone in ("calm", "reflective", "mysterious", "tense", "somber", "hopeful", "uplifting", "dramatic"):
            if evidence.get(tone):
                # One or two words remain supporting evidence only. Three or
                # more aligned, non-negated signals can establish a
                # non-aggressive tone even without Story Director metadata.
                add(tone, min(0.62, 0.20 + 0.13 * len(evidence[tone])))

        # Triumphant needs structural completion plus non-negated victory evidence
        # and an additional positive-direction signal.
        if (
            story is not None
            and story.story_role in {"resolution", "climax"}
            and evidence.get("triumphant")
            and (evidence.get("uplifting") or evidence.get("hopeful"))
        ):
            add("triumphant", 0.92)

        if not scores:
            return "neutral", tuple()
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        top_tone, top_score = ranked[0]
        conflicts = []
        for other_tone, other_score in ranked[1:]:
            pair = frozenset({top_tone, other_tone})
            if pair in CONFLICTING_TONES and abs(top_score - other_score) <= 0.18:
                conflicts.append(f"{min(pair)}_{max(pair)}_conflict_resolved_to_neutral")
        if conflicts:
            return "neutral", _canonical_strings(conflicts)
        if top_score < 0.55:
            return "neutral", tuple()
        if top_tone == "dramatic" and top_score < 0.75:
            return "neutral", ("unsupported_dramatic_downgraded_to_neutral",)
        return top_tone, tuple()


def serialize_intent_tone_analysis(analyses: Sequence[SceneAudioAnalysis]) -> str:
    """Stable Step 2 debug serialization; not a 4.7.0 output artifact."""
    payload = {
        "planner_version": PLANNER_VERSION,
        "scene_count": len(analyses),
        "scenes": [asdict(analysis) for analysis in analyses],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
