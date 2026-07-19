from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from .models import (
    AUDIO_INTENTS, EMOTIONAL_TONES, PLANNER_VERSION, AudioContractError,
    MusicPlan, ProjectAudioDiagnostics, ProjectAudioSummary,
)


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

# Step 3 energy model. Intent is deliberately the largest categorical factor;
# tone modifies it, while compatible Story Director values provide bounded
# continuous support. These constants are internal 4.7.0 defaults until config
# integration is introduced in a later step.
NEUTRAL_BASE_ENERGY = 0.40
MAX_NORMAL_ENERGY_DELTA = 0.30
MAX_SUPPORTED_CONTRAST_DELTA = 0.45
SUPPORTED_CONTRAST_CONFIDENCE = 0.75
INTENT_ENERGY_ADJUSTMENTS = {
    "neutral": 0.00, "support": 0.00, "establish": -0.04,
    "reflection": -0.12, "release": -0.08, "transition": -0.02,
    "build": 0.12, "tension": 0.22, "resolution": 0.02, "climax": 0.38,
}
TONE_ENERGY_ADJUSTMENTS = {
    "neutral": 0.00, "calm": -0.08, "reflective": -0.07,
    "mysterious": 0.04, "somber": -0.05, "hopeful": 0.03,
    "uplifting": 0.08, "tense": 0.10, "dramatic": 0.10,
    "triumphant": 0.12,
}
TONE_TIE_BREAK_ORDER = (
    "neutral", "calm", "reflective", "mysterious", "tense", "somber",
    "hopeful", "dramatic", "uplifting", "triumphant",
)


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
    story_role: str | None = None
    story_phase: str | None = None
    story_tension: float | None = None
    story_emotional_intensity: float | None = None
    story_revelation_strength: float | None = None

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


@dataclass(frozen=True)
class SceneEnergyMusicAnalysis:
    scene: SceneAudioAnalysis
    raw_energy: float
    energy: float
    energy_adjustment: str
    supported_contrast_preserved: bool
    music: MusicPlan
    warnings: tuple[str, ...]
    rationale: str


@dataclass(frozen=True)
class ProjectEnergyMusicAnalysis:
    project_summary: ProjectAudioSummary
    scenes: tuple[SceneEnergyMusicAnalysis, ...]
    diagnostics: ProjectAudioDiagnostics


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

    def plan_energy_and_music(
        self,
        semantic_plan: Mapping[str, Any],
        story_plan: Mapping[str, Any] | None = None,
    ) -> ProjectEnergyMusicAnalysis:
        """Plan Step 3 energy and abstract music metadata without media work."""
        inputs = (semantic_plan, story_plan)
        snapshot = deepcopy(inputs)
        analyses = self.analyze_intent_and_tone(semantic_plan, story_plan)
        raw = tuple(self._raw_energy(scene) for scene in analyses)
        final, adjustments, preserved = self._smooth_energy(analyses, raw)
        planned = []
        for index, (scene, raw_energy, energy) in enumerate(zip(analyses, raw, final)):
            music, music_warning = self._music_plan(scene, energy)
            warnings = list(scene.warnings)
            if adjustments[index] != "unchanged":
                warnings.append("normal_energy_delta_limited")
            if preserved[index]:
                warnings.append("supported_climax_contrast_preserved")
            if music_warning:
                warnings.append(music_warning)
            planned.append(SceneEnergyMusicAnalysis(
                scene=scene,
                raw_energy=raw_energy,
                energy=energy,
                energy_adjustment=adjustments[index],
                supported_contrast_preserved=preserved[index],
                music=music,
                warnings=_canonical_strings(warnings),
                rationale=(
                    f"Energy {raw_energy:.4f}->{energy:.4f} ({adjustments[index]}); "
                    f"music {music.style} at {music.intensity:.4f}."
                ),
            ))

        scenes = tuple(planned)
        curve = tuple(item.energy for item in scenes)
        dominant = self._dominant_tone(analyses)
        styles = tuple(item.music.style for item in scenes)
        style_changes = sum(left != right for left, right in zip(styles, styles[1:]))
        fallback_count = sum(scene.fallback_used for scene in analyses)
        project_warnings = []
        if any(item.energy_adjustment != "unchanged" for item in scenes):
            project_warnings.append("energy_curve_contains_limited_deltas")
        if any(item.supported_contrast_preserved for item in scenes):
            project_warnings.append("energy_curve_contains_supported_climax_contrast")
        if analyses and fallback_count * 2 > len(analyses):
            project_warnings.append("fallback_dominant_energy_planning")
        confidence = _clamp(
            sum(scene.confidence for scene in analyses) / len(analyses)
            if analyses else 0.0
        )
        summary = ProjectAudioSummary(
            dominant_tone=dominant,
            default_music_style=self._default_music_style(styles),
            energy_curve=curve,
            scene_count=len(scenes),
            target_loudness_lufs=-14.0,
        )
        diagnostics = ProjectAudioDiagnostics(
            confidence=confidence,
            warnings=_canonical_strings(project_warnings),
            fallback_count=fallback_count,
            missing_inputs=("story_director_plan",) if analyses and all(scene.story_role is None for scene in analyses) else (),
            resolved_conflicts=_canonical_strings(
                conflict for scene in analyses for conflict in scene.resolved_conflicts
            ),
            flat_energy_curve=(not curve or max(curve) - min(curve) <= 0.05),
            extreme_energy_count=sum(value <= 0.10 or value >= 0.90 for value in curve),
            music_style_change_count=style_changes,
            unsupported_aggressive_transition_count=0,
            ambience_scene_count=0,
            scene_without_usable_input_count=sum(
                not scene.accepted_signals for scene in analyses
            ),
            fallback_dominant=bool(analyses and fallback_count * 2 > len(analyses)),
        )
        summary.validate()
        diagnostics.validate(len(scenes))
        for item in scenes:
            item.music.validate()
        if inputs != snapshot:
            raise AudioContractError("Audio Director mutated energy/music input metadata.")
        return ProjectEnergyMusicAnalysis(summary, scenes, diagnostics)

    def plan_sound_layers(
        self,
        semantic_plan: Mapping[str, Any],
        story_plan: Mapping[str, Any] | None = None,
    ) -> Any:
        """Plan Step 4 ambience, silence, ducking and transition metadata."""
        from .sound_planning import plan_sound_layers

        inputs = (semantic_plan, story_plan)
        snapshot = deepcopy(inputs)
        result = plan_sound_layers(
            self.plan_energy_and_music(semantic_plan, story_plan), semantic_plan,
        )
        if inputs != snapshot:
            raise AudioContractError("Audio Director mutated sound-layer input metadata.")
        return result

    @staticmethod
    def _raw_energy(scene: SceneAudioAnalysis) -> float:
        # Fixed order: base -> intent -> tone -> bounded structural values -> clamp/round.
        value = NEUTRAL_BASE_ENERGY
        value += INTENT_ENERGY_ADJUSTMENTS[scene.audio_intent]
        value += TONE_ENERGY_ADJUSTMENTS[scene.emotional_tone]
        if scene.story_tension is not None:
            value += (scene.story_tension - 0.5) * 0.18
        if scene.story_emotional_intensity is not None:
            value += (scene.story_emotional_intensity - 0.5) * 0.12
        if scene.story_revelation_strength is not None:
            value += scene.story_revelation_strength * 0.06
        return _clamp(value)

    @staticmethod
    def _has_supported_contrast(scene: SceneAudioAnalysis) -> bool:
        return bool(
            scene.audio_intent == "climax"
            and scene.story_role == "climax"
            and scene.story_phase == "climax"
            and (scene.story_tension or 0.0) >= 0.70
            and scene.confidence >= SUPPORTED_CONTRAST_CONFIDENCE
        )

    @classmethod
    def _smooth_energy(
        cls,
        scenes: Sequence[SceneAudioAnalysis],
        raw: Sequence[float],
    ) -> tuple[tuple[float, ...], tuple[str, ...], tuple[bool, ...]]:
        # One forward pass preserves causal explainability: each scene is
        # bounded only against the already-final preceding semantic scene.
        if not raw:
            return (), (), ()
        final = [raw[0]]
        adjustments = ["unchanged"]
        preserved = [False]
        for index in range(1, len(raw)):
            previous, candidate = final[-1], raw[index]
            delta = candidate - previous
            allowed = MAX_NORMAL_ENERGY_DELTA
            contrast = cls._has_supported_contrast(scenes[index]) and delta > allowed
            if contrast:
                allowed = MAX_SUPPORTED_CONTRAST_DELTA
            bounded = min(previous + allowed, max(previous - MAX_NORMAL_ENERGY_DELTA, candidate))
            bounded = _clamp(bounded)
            final.append(bounded)
            if bounded < candidate:
                adjustments.append("limited_up")
            elif bounded > candidate:
                adjustments.append("limited_down")
            else:
                adjustments.append("unchanged")
            preserved.append(contrast and bounded - previous > MAX_NORMAL_ENERGY_DELTA)
        return tuple(final), tuple(adjustments), tuple(preserved)

    @staticmethod
    def _music_plan(scene: SceneAudioAnalysis, energy: float) -> tuple[MusicPlan, str | None]:
        supported_tension = (
            scene.story_role in {"complication", "contrast", "counterargument", "climax"}
            and (scene.story_tension or 0.0) >= 0.65
        )
        supported_climax = AudioDirector._has_supported_contrast(scene)
        warning = None
        if scene.emotional_tone in {"calm", "reflective"} or scene.audio_intent == "reflection":
            style = "ambient" if energy >= 0.25 else "minimal"
        elif scene.emotional_tone == "somber":
            style = "emotional_piano"
        elif scene.audio_intent == "tension" and supported_tension:
            style = "suspense"
        elif scene.audio_intent == "climax" and supported_climax:
            style = "orchestral" if scene.emotional_tone == "triumphant" else "cinematic"
        else:
            style = "documentary"
            if scene.audio_intent in {"tension", "climax"} or scene.emotional_tone in {"dramatic", "triumphant"}:
                warning = "aggressive_music_style_downgraded"
        intensity = energy * 0.72
        if scene.audio_intent in {"support", "neutral", "establish"}:
            intensity -= 0.08
        if supported_climax:
            intensity += 0.08
        intensity = _clamp(intensity)
        rationale = f"Style {style} from intent {scene.audio_intent}, tone {scene.emotional_tone}, energy {energy:.4f}."
        return MusicPlan(True, style, intensity, rationale), warning

    @staticmethod
    def _dominant_tone(scenes: Sequence[SceneAudioAnalysis]) -> str:
        if not scenes:
            return "neutral"
        order = {tone: index for index, tone in enumerate(TONE_TIE_BREAK_ORDER)}
        totals = {
            tone: (
                round(sum(scene.confidence for scene in scenes if scene.emotional_tone == tone), 4),
                sum(scene.emotional_tone == tone for scene in scenes),
            )
            for tone in TONE_TIE_BREAK_ORDER
        }
        return min(TONE_TIE_BREAK_ORDER, key=lambda tone: (-totals[tone][0], -totals[tone][1], order[tone]))

    @staticmethod
    def _default_music_style(styles: Sequence[str]) -> str:
        if not styles:
            return "documentary"
        order = ("documentary", "ambient", "minimal", "emotional_piano", "cinematic", "suspense", "orchestral")
        counts = {style: styles.count(style) for style in order}
        return min(order, key=lambda style: (-counts[style], order.index(style)))

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
            story_role=story.story_role if story is not None else None,
            story_phase=story.story_phase if story is not None else None,
            story_tension=story.tension if story is not None else None,
            story_emotional_intensity=story.emotional_intensity if story is not None else None,
            story_revelation_strength=story.revelation_strength if story is not None else None,
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
            if (
                role in {"context", "setup"}
                and story.tension is not None
                and story.emotional_intensity is not None
                and tension <= 0.35
                and emotion <= 0.35
            ):
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


def serialize_energy_music_analysis(analysis: ProjectEnergyMusicAnalysis) -> str:
    """Stable Step 3 debug serialization; not a 4.7.0 output artifact."""
    payload = {
        "planner_version": PLANNER_VERSION,
        "project_summary": asdict(analysis.project_summary),
        "scenes": [asdict(scene) for scene in analysis.scenes],
        "diagnostics": asdict(analysis.diagnostics),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
