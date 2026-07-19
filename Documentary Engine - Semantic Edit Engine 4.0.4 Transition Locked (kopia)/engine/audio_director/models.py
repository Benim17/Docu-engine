from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from typing import Any, Sequence


AUDIO_INTENTS = frozenset({
    "establish", "support", "build", "tension", "release", "reflection",
    "transition", "climax", "resolution", "neutral",
})
EMOTIONAL_TONES = frozenset({
    "neutral", "calm", "reflective", "mysterious", "tense", "somber",
    "hopeful", "dramatic", "uplifting", "triumphant",
})
MUSIC_STYLES = frozenset({
    "none", "documentary", "ambient", "cinematic", "minimal", "emotional_piano",
    "orchestral", "hybrid", "electronic", "historical", "nature", "suspense",
})
AMBIENCE_TYPES = frozenset({
    "none", "room_tone", "urban", "crowd", "nature", "forest", "ocean", "wind",
    "rain", "machinery", "transport", "battlefield", "archival_noise",
    "generic_environment",
})
TRANSITION_TYPES = frozenset({
    "none", "crossfade", "fade_in", "fade_out", "hard_cut", "silence",
    "ambient_bridge", "riser", "impact", "tonal_shift",
})
PLAN_STATUSES = frozenset({"planned", "fallback"})

SCHEMA_VERSION = "1.0"
PLANNER_VERSION = "4.7.0"


class AudioContractError(ValueError):
    """Raised when Audio Director metadata violates the 4.7.0 schema contract."""


def _number(value: Any, name: str, low: float, high: float) -> float:
    if isinstance(value, bool):
        raise AudioContractError(f"{name} must be numeric, not boolean.")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise AudioContractError(f"{name} must be numeric.") from exc
    if not math.isfinite(result) or not low <= result <= high:
        raise AudioContractError(f"{name} must be between {low} and {high}.")
    return result


def _integer(value: Any, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AudioContractError(f"{name} must be an integer.")
    if not low <= value <= high:
        raise AudioContractError(f"{name} must be between {low} and {high}.")
    return value


def _enum(value: str, name: str, allowed: frozenset[str]) -> None:
    if value not in allowed:
        raise AudioContractError(f"Unsupported {name}: {value!r}.")


def _text(value: str, name: str, allow_empty: bool = False) -> None:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise AudioContractError(f"{name} must be a non-empty string.")


def _stable_strings(values: Sequence[str], name: str) -> None:
    if not isinstance(values, tuple) or any(not isinstance(value, str) or not value.strip() for value in values):
        raise AudioContractError(f"{name} must be a tuple of non-empty strings.")
    canonical = tuple(sorted(set(values), key=lambda value: (value.casefold(), value)))
    if values != canonical:
        raise AudioContractError(f"{name} must be unique and canonically sorted.")


@dataclass(frozen=True)
class MusicPlan:
    enabled: bool
    style: str
    intensity: float
    rationale: str

    def validate(self) -> None:
        if not isinstance(self.enabled, bool):
            raise AudioContractError("music.enabled must be boolean.")
        _enum(self.style, "music style", MUSIC_STYLES)
        _number(self.intensity, "music.intensity", 0.0, 1.0)
        _text(self.rationale, "music.rationale")
        if not self.enabled and (self.style != "none" or self.intensity != 0.0):
            raise AudioContractError("Disabled music must use style 'none' and intensity 0.0.")
        if self.enabled and self.style == "none":
            raise AudioContractError("Enabled music cannot use style 'none'.")


@dataclass(frozen=True)
class AmbiencePlan:
    enabled: bool
    type: str
    intensity: float
    confidence: float
    source_basis: tuple[str, ...]
    fallback_reason: str | None

    def validate(self) -> None:
        if not isinstance(self.enabled, bool):
            raise AudioContractError("ambience.enabled must be boolean.")
        _enum(self.type, "ambience type", AMBIENCE_TYPES)
        _number(self.intensity, "ambience.intensity", 0.0, 1.0)
        _number(self.confidence, "ambience.confidence", 0.0, 1.0)
        _stable_strings(self.source_basis, "ambience.source_basis")
        if self.fallback_reason is not None:
            _text(self.fallback_reason, "ambience.fallback_reason")
        if not self.enabled and (self.type != "none" or self.intensity != 0.0):
            raise AudioContractError("Disabled ambience must use type 'none' and intensity 0.0.")
        if self.enabled and self.type == "none":
            raise AudioContractError("Enabled ambience cannot use type 'none'.")


@dataclass(frozen=True)
class DuckingPlan:
    ducking_enabled: bool
    music_reduction_db: float
    ambience_reduction_db: float
    attack_ms: int
    release_ms: int
    rationale: str

    def validate(self) -> None:
        if not isinstance(self.ducking_enabled, bool):
            raise AudioContractError("ducking.ducking_enabled must be boolean.")
        _number(self.music_reduction_db, "ducking.music_reduction_db", -60.0, 0.0)
        _number(self.ambience_reduction_db, "ducking.ambience_reduction_db", -60.0, 0.0)
        _integer(self.attack_ms, "ducking.attack_ms", 0, 5000)
        _integer(self.release_ms, "ducking.release_ms", 0, 10000)
        _text(self.rationale, "ducking.rationale")
        if not self.ducking_enabled and (self.music_reduction_db != 0.0 or self.ambience_reduction_db != 0.0):
            raise AudioContractError("Disabled ducking must use zero reduction.")


@dataclass(frozen=True)
class AudioTransition:
    type: str
    duration_ms: int
    rationale: str

    def validate(self) -> None:
        _enum(self.type, "audio transition", TRANSITION_TYPES)
        _integer(self.duration_ms, "audio transition duration_ms", 0, 10000)
        _text(self.rationale, "audio transition rationale")
        if self.type in {"none", "hard_cut", "impact"} and self.duration_ms != 0:
            raise AudioContractError(f"Transition {self.type!r} must have zero duration.")


@dataclass(frozen=True)
class SilencePlan:
    pre_scene_silence_ms: int
    post_scene_silence_ms: int
    music_suppressed: bool
    ambience_suppressed: bool
    intentional_silence: bool
    rationale: str

    def validate(self) -> None:
        _integer(self.pre_scene_silence_ms, "silence.pre_scene_silence_ms", 0, 10000)
        _integer(self.post_scene_silence_ms, "silence.post_scene_silence_ms", 0, 10000)
        if not all(isinstance(value, bool) for value in (
            self.music_suppressed, self.ambience_suppressed, self.intentional_silence,
        )):
            raise AudioContractError("Silence suppression and intent fields must be boolean.")
        _text(self.rationale, "silence.rationale")
        if not self.intentional_silence and (
            self.pre_scene_silence_ms or self.post_scene_silence_ms
            or self.music_suppressed or self.ambience_suppressed
        ):
            raise AudioContractError("Non-intentional silence cannot suppress audio or add silence duration.")


@dataclass(frozen=True)
class SceneAudioDiagnostics:
    confidence: float
    warnings: tuple[str, ...]
    fallback_used: bool
    fallback_reason: str | None
    missing_inputs: tuple[str, ...]
    source_signals: tuple[str, ...]
    resolved_conflicts: tuple[str, ...]
    deterministic: bool = True
    planner_version: str = PLANNER_VERSION

    def validate(self) -> None:
        _number(self.confidence, "diagnostics.confidence", 0.0, 1.0)
        for values, name in (
            (self.warnings, "diagnostics.warnings"),
            (self.missing_inputs, "diagnostics.missing_inputs"),
            (self.source_signals, "diagnostics.source_signals"),
            (self.resolved_conflicts, "diagnostics.resolved_conflicts"),
        ):
            _stable_strings(values, name)
        if not isinstance(self.fallback_used, bool) or not isinstance(self.deterministic, bool):
            raise AudioContractError("Diagnostic fallback and deterministic fields must be boolean.")
        if self.fallback_reason is not None:
            _text(self.fallback_reason, "diagnostics.fallback_reason")
        if self.fallback_used and self.fallback_reason is None:
            raise AudioContractError("Fallback diagnostics require fallback_reason.")
        if not self.deterministic or self.planner_version != PLANNER_VERSION:
            raise AudioContractError("Scene diagnostics must identify deterministic planner 4.7.0.")


@dataclass(frozen=True)
class SceneAudioPlan:
    scene_id: str
    scene_index: int
    start: float
    end: float
    duration: float
    audio_intent: str
    emotional_tone: str
    energy: float
    music: MusicPlan
    ambience: AmbiencePlan
    ducking: DuckingPlan
    transition_in: AudioTransition
    transition_out: AudioTransition
    silence: SilencePlan
    diagnostics: SceneAudioDiagnostics

    def validate(self) -> None:
        _text(self.scene_id, "scene_id")
        _integer(self.scene_index, "scene_index", 0, 1_000_000)
        start = _number(self.start, "scene.start", 0.0, 1_000_000.0)
        end = _number(self.end, "scene.end", 0.0, 1_000_000.0)
        duration = _number(self.duration, "scene.duration", 0.0001, 1_000_000.0)
        if end <= start or abs((end - start) - duration) > 0.001:
            raise AudioContractError("Scene timing fields are inconsistent.")
        _enum(self.audio_intent, "audio intent", AUDIO_INTENTS)
        _enum(self.emotional_tone, "emotional tone", EMOTIONAL_TONES)
        _number(self.energy, "scene.energy", 0.0, 1.0)
        self.music.validate()
        self.ambience.validate()
        self.ducking.validate()
        self.transition_in.validate()
        self.transition_out.validate()
        self.silence.validate()
        self.diagnostics.validate()


@dataclass(frozen=True)
class ProjectAudioSummary:
    dominant_tone: str
    default_music_style: str
    energy_curve: tuple[float, ...]
    scene_count: int
    target_loudness_lufs: float

    def validate(self) -> None:
        _enum(self.dominant_tone, "dominant emotional tone", EMOTIONAL_TONES)
        _enum(self.default_music_style, "default music style", MUSIC_STYLES)
        _integer(self.scene_count, "project_summary.scene_count", 0, 1_000_000)
        if not isinstance(self.energy_curve, tuple) or len(self.energy_curve) != self.scene_count:
            raise AudioContractError("Energy curve must contain exactly one value per scene.")
        for value in self.energy_curve:
            _number(value, "project_summary.energy_curve value", 0.0, 1.0)
        _number(self.target_loudness_lufs, "project_summary.target_loudness_lufs", -40.0, -5.0)


@dataclass(frozen=True)
class ProjectAudioDiagnostics:
    confidence: float
    warnings: tuple[str, ...]
    fallback_count: int
    missing_inputs: tuple[str, ...]
    resolved_conflicts: tuple[str, ...]
    flat_energy_curve: bool
    extreme_energy_count: int
    music_style_change_count: int
    unsupported_aggressive_transition_count: int
    ambience_scene_count: int
    scene_without_usable_input_count: int
    fallback_dominant: bool
    deterministic: bool = True
    planner_version: str = PLANNER_VERSION

    def validate(self, scene_count: int) -> None:
        _number(self.confidence, "project_diagnostics.confidence", 0.0, 1.0)
        for values, name in (
            (self.warnings, "project_diagnostics.warnings"),
            (self.missing_inputs, "project_diagnostics.missing_inputs"),
            (self.resolved_conflicts, "project_diagnostics.resolved_conflicts"),
        ):
            _stable_strings(values, name)
        for value, name in (
            (self.fallback_count, "fallback_count"),
            (self.extreme_energy_count, "extreme_energy_count"),
            (self.music_style_change_count, "music_style_change_count"),
            (self.unsupported_aggressive_transition_count, "unsupported_aggressive_transition_count"),
            (self.ambience_scene_count, "ambience_scene_count"),
            (self.scene_without_usable_input_count, "scene_without_usable_input_count"),
        ):
            _integer(value, f"project_diagnostics.{name}", 0, scene_count)
        if not all(isinstance(value, bool) for value in (
            self.flat_energy_curve, self.fallback_dominant, self.deterministic,
        )):
            raise AudioContractError("Project diagnostic flags must be boolean.")
        if not self.deterministic or self.planner_version != PLANNER_VERSION:
            raise AudioContractError("Project diagnostics must identify deterministic planner 4.7.0.")


@dataclass(frozen=True)
class AudioPlan:
    project_summary: ProjectAudioSummary
    scenes: tuple[SceneAudioPlan, ...]
    schema_version: str = SCHEMA_VERSION
    planner_version: str = PLANNER_VERSION
    deterministic: bool = True
    status: str = "planned"

    def validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION or self.planner_version != PLANNER_VERSION:
            raise AudioContractError("Audio plan version is invalid.")
        if not self.deterministic or self.status not in PLAN_STATUSES:
            raise AudioContractError("Audio plan status or determinism is invalid.")
        if not isinstance(self.scenes, tuple):
            raise AudioContractError("Audio plan scenes must be an ordered tuple.")
        self.project_summary.validate()
        if len(self.scenes) != self.project_summary.scene_count:
            raise AudioContractError("Audio plan scene count does not match project summary.")
        seen_ids: set[str] = set()
        for expected_index, scene in enumerate(self.scenes):
            scene.validate()
            if scene.scene_index != expected_index:
                raise AudioContractError("Audio plan scene order is not contiguous.")
            if scene.scene_id in seen_ids:
                raise AudioContractError("Audio plan scene IDs must be unique.")
            seen_ids.add(scene.scene_id)
            if scene.energy != self.project_summary.energy_curve[expected_index]:
                raise AudioContractError("Energy curve must equal ordered scene energy values.")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "planner_version": self.planner_version,
            "deterministic": self.deterministic,
            "status": self.status,
            "project_summary": asdict(self.project_summary),
            "scene_count": len(self.scenes),
            "scenes": [asdict(scene) for scene in self.scenes],
        }


@dataclass(frozen=True)
class AudioDiagnostics:
    project: ProjectAudioDiagnostics
    scenes: tuple[SceneAudioDiagnostics, ...]
    schema_version: str = SCHEMA_VERSION
    planner_version: str = PLANNER_VERSION
    deterministic: bool = True

    def validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION or self.planner_version != PLANNER_VERSION:
            raise AudioContractError("Audio diagnostics version is invalid.")
        if not self.deterministic or not isinstance(self.scenes, tuple):
            raise AudioContractError("Audio diagnostics must be deterministic and ordered.")
        self.project.validate(len(self.scenes))
        for scene in self.scenes:
            scene.validate()

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "planner_version": self.planner_version,
            "deterministic": self.deterministic,
            "scene_count": len(self.scenes),
            "project": asdict(self.project),
            "scenes": [asdict(scene) for scene in self.scenes],
        }


def _canonical_json_value(value: Any) -> Any:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise AudioContractError("Audio artifacts cannot contain non-finite floats.")
        rounded = round(value, 4)
        return 0.0 if rounded == 0.0 else rounded
    if isinstance(value, dict):
        return {key: _canonical_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_json_value(item) for item in value]
    return value


def serialize_audio_artifact(artifact: AudioPlan | AudioDiagnostics) -> str:
    """Serialize validated audio metadata with stable keys, floats, and newline."""
    if isinstance(artifact, (AudioPlan, AudioDiagnostics)):
        payload = _canonical_json_value(artifact.to_dict())
    else:
        raise AudioContractError("Unsupported audio artifact type.")
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
