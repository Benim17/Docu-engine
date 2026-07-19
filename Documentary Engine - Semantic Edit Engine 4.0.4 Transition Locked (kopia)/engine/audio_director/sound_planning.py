from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from .director import ProjectEnergyMusicAnalysis, SceneEnergyMusicAnalysis, _canonical_strings, _clamp
from .models import AmbiencePlan, AudioTransition, DuckingPlan, MusicPlan, SilencePlan


AMBIENCE_SIGNALS = {
    "urban": ("city", "street", "traffic", "stad", "gata", "trafik"),
    "crowd": ("crowd", "audience", "protesters", "folkmassa", "publik", "demonstranter"),
    "nature": ("wilderness", "landscape", "wildlife", "vildmark", "landskap", "djurliv"),
    "forest": ("forest", "trees", "woodland", "skog", "träd", "skogsmark"),
    "ocean": ("ocean", "sea", "waves", "coast", "hav", "sjö", "vågor", "kust"),
    "wind": ("wind", "gusts", "windswept", "vind", "vindbyar", "blåsigt"),
    "rain": ("rain", "rainfall", "raindrops", "regn", "regnfall", "regndroppar"),
    "machinery": ("machinery", "factory", "engines", "maskiner", "fabrik", "motorer"),
    "transport": ("train", "station", "vehicle", "airport", "tåg", "station", "fordon", "flygplats"),
    "battlefield": ("battlefield", "combat", "artillery", "front line", "slagfält", "strid", "artilleri", "frontlinje"),
    "archival_noise": ("archive footage", "old recording", "historical recording", "arkivfilm", "gammal inspelning", "historisk inspelning"),
    "room_tone": ("interior room", "indoor interview", "quiet room", "inomhusintervju", "tyst rum"),
}
ENVIRONMENT_KEYS = ("ambience_type", "environment_type", "environment", "location_type")
TOKEN_RE = re.compile(r"[a-zåäö0-9]+", re.IGNORECASE)


@dataclass(frozen=True)
class SceneSoundAnalysis:
    energy_music: SceneEnergyMusicAnalysis
    music: MusicPlan
    ambience: AmbiencePlan
    silence: SilencePlan
    ducking: DuckingPlan
    transition_in: AudioTransition
    transition_out: AudioTransition
    narration_present: bool
    narration_density: float
    transition_requests: tuple[str, ...]
    source_signals: tuple[str, ...]
    warnings: tuple[str, ...]
    rationale: str


@dataclass(frozen=True)
class SoundProjectDiagnostics:
    ambience_scene_count: int
    intentional_silence_count: int
    unsupported_aggressive_transition_count: int
    transition_pair_conflict_count: int
    narration_missing_count: int
    warnings: tuple[str, ...]
    deterministic: bool = True


@dataclass(frozen=True)
class ProjectSoundAnalysis:
    energy_plan: ProjectEnergyMusicAnalysis
    semantic_scene_ids: tuple[str, ...]
    scenes: tuple[SceneSoundAnalysis, ...]
    diagnostics: SoundProjectDiagnostics


def plan_sound_layers(
    energy_plan: ProjectEnergyMusicAnalysis,
    semantic_plan: Mapping[str, Any],
    *,
    allow_aggressive_transitions: bool = True,
) -> ProjectSoundAnalysis:
    """Plan metadata only, in order: ambience, silence, ducking, transitions, diagnostics."""
    semantic_scenes = semantic_plan.get("scenes", [])
    base = []
    unsupported_transitions = 0
    for planned, semantic_scene in zip(energy_plan.scenes, semantic_scenes):
        narration, density = _narration(semantic_scene, planned.scene.duration)
        ambience, ambience_warnings, ambience_signals = _ambience(
            planned, semantic_scene, narration,
        )
        silence, silence_signals = _silence(planned, semantic_scene)
        music = planned.music
        if silence.intentional_silence:
            music = MusicPlan(False, "none", 0.0, "Intentional silence suppresses music metadata.")
            ambience = AmbiencePlan(
                False, "none", 0.0, ambience.confidence, ambience.source_basis,
                "Intentional silence suppresses ambience metadata.",
            )
            ambience_warnings = (*ambience_warnings, "ambience_suppressed_by_intentional_silence")
        ducking, ducking_warnings = _ducking(music, ambience, silence, narration, density, planned)
        base.append({
            "energy_music": planned,
            "music": music,
            "ambience": ambience,
            "silence": silence,
            "ducking": ducking,
            "narration_present": narration,
            "narration_density": density,
            "transition_requests": _canonical_strings(
                (str(semantic_scene.get("audio_transition_intent")),)
                if semantic_scene.get("audio_transition_intent") in {"hard_cut"} else ()
            ),
            "source_signals": _canonical_strings((*ambience_signals, *silence_signals)),
            "warnings": _canonical_strings((*ambience_warnings, *ducking_warnings)),
        })

    pairs, transition_warnings = _transition_pairs(base, allow_aggressive_transitions)
    scenes = []
    for index, values in enumerate(base):
        warnings = list(values["warnings"])
        warnings.extend(transition_warnings[index])
        unsupported_transitions += sum(
            "downgraded" in value or "disabled_by_config" in value
            for value in transition_warnings[index]
        )
        transition_in, transition_out = pairs[index]
        final_values = dict(values)
        final_values["warnings"] = _canonical_strings(warnings)
        scenes.append(SceneSoundAnalysis(
            **final_values,
            transition_in=transition_in,
            transition_out=transition_out,
            rationale=(
                f"Ambience {values['ambience'].type}; silence "
                f"{str(values['silence'].intentional_silence).lower()}; ducking "
                f"{str(values['ducking'].ducking_enabled).lower()}; transitions "
                f"{transition_in.type}/{transition_out.type}."
            ),
        ))
    for scene in scenes:
        scene.music.validate()
        scene.ambience.validate()
        scene.silence.validate()
        scene.ducking.validate()
        scene.transition_in.validate()
        scene.transition_out.validate()
    project_warnings = []
    if not any(scene.ambience.enabled for scene in scenes):
        project_warnings.append("ambience_not_supported_for_any_scene")
    if any(not scene.narration_present for scene in scenes):
        project_warnings.append("narration_missing_for_some_scenes")
    return ProjectSoundAnalysis(
        energy_plan,
        tuple(str(scene.get("scene_id") or f"scene_{index + 1:03d}") for index, scene in enumerate(semantic_scenes)),
        tuple(scenes),
        SoundProjectDiagnostics(
            ambience_scene_count=sum(scene.ambience.enabled for scene in scenes),
            intentional_silence_count=sum(scene.silence.intentional_silence for scene in scenes),
            unsupported_aggressive_transition_count=unsupported_transitions,
            transition_pair_conflict_count=0,
            narration_missing_count=sum(not scene.narration_present for scene in scenes),
            warnings=_canonical_strings(project_warnings),
        ),
    )


def _scene_text(scene: Mapping[str, Any]) -> str:
    return " ".join(str(scene.get(key, "")) for key in (
        "voiceover_text", "narration", "text", "image_description", "scene_description",
    )).casefold()


def _phrase_present(text: str, phrase: str) -> bool:
    phrase_tokens = tuple(TOKEN_RE.findall(phrase.casefold()))
    tokens = tuple(TOKEN_RE.findall(text))
    return any(tokens[index:index + len(phrase_tokens)] == phrase_tokens for index in range(len(tokens) - len(phrase_tokens) + 1))


def _narration(scene: Mapping[str, Any], duration: float) -> tuple[bool, float]:
    text = " ".join(str(scene.get(key, "")).strip() for key in ("voiceover_text", "narration", "text") if str(scene.get(key, "")).strip())
    return bool(text), _clamp(len(text) / max(1.0, duration * 20.0))


def _ambience(
    planned: SceneEnergyMusicAnalysis,
    scene: Mapping[str, Any],
    narration: bool,
) -> tuple[AmbiencePlan, tuple[str, ...], tuple[str, ...]]:
    text = _scene_text(scene)
    explicit = None
    for key in ENVIRONMENT_KEYS:
        value = scene.get(key)
        if isinstance(value, str) and value in AMBIENCE_SIGNALS:
            explicit = value
            break
    matches = {
        kind: tuple(signal for signal in signals if _phrase_present(text, signal))
        for kind, signals in AMBIENCE_SIGNALS.items()
    }
    candidates = []
    for kind in sorted(AMBIENCE_SIGNALS):
        count = len(matches[kind])
        score = (0.65 if explicit == kind else 0.0) + min(0.30, count * 0.10)
        if explicit is None and count >= 3:
            score = 0.72
        if kind == "battlefield" and not (explicit == kind and count >= 2):
            score = 0.0
        candidates.append((score, kind, count))
    score, kind, count = sorted(candidates, key=lambda item: (-item[0], item[1]))[0]
    warnings = []
    signals = []
    if explicit:
        signals.append(f"explicit_environment:{explicit}")
    signals.extend(f"environment_text:{value}" for value in matches.get(kind, ()))
    if score < 0.70:
        if explicit or any(count for _, _, count in candidates):
            warnings.append("ambience_evidence_insufficient")
        if explicit == "battlefield":
            warnings.append("aggressive_ambience_downgraded")
        return (
            AmbiencePlan(False, "none", 0.0, _clamp(score), _canonical_strings(signals), "Environment evidence did not reach the conservative threshold."),
            _canonical_strings(warnings), _canonical_strings(signals),
        )
    intensity = min(planned.energy * 0.45, 0.35)
    if narration:
        intensity -= 0.08
    intensity = _clamp(min(planned.energy, max(0.0, intensity)))
    if intensity == 0.0:
        return (
            AmbiencePlan(False, "none", 0.0, _clamp(score), _canonical_strings(signals), "Scene energy leaves no usable ambience intensity."),
            ("ambience_disabled_at_zero_energy",), _canonical_strings(signals),
        )
    return (
        AmbiencePlan(True, kind, intensity, _clamp(score), _canonical_strings(signals), None),
        (), _canonical_strings(signals),
    )


def _silence(
    planned: SceneEnergyMusicAnalysis,
    scene: Mapping[str, Any],
) -> tuple[SilencePlan, tuple[str, ...]]:
    explicit_pause = scene.get("intentional_pause") is True
    structurally_supported = (
        planned.scene.story_role in {"epilogue", "revelation", "resolution", "climax"}
        and (
            planned.scene.story_role == "epilogue"
            or (planned.scene.story_revelation_strength or 0.0) >= 0.75
            or (planned.scene.story_emotional_intensity or 0.0) >= 0.75
        )
        and planned.scene.confidence >= 0.70
    )
    if not (explicit_pause and structurally_supported):
        return SilencePlan(0, 0, False, False, False, "No explicit structurally supported pause."), ()
    pre = 300 if planned.scene.story_role in {"revelation", "climax"} else 200
    post = 400 if planned.scene.story_role in {"epilogue", "resolution"} else 250
    return (
        SilencePlan(pre, post, True, True, True, "Explicit pause has compatible structural support."),
        ("intentional_pause", f"silence_story_role:{planned.scene.story_role}"),
    )


def _ducking(
    music: MusicPlan,
    ambience: AmbiencePlan,
    silence: SilencePlan,
    narration: bool,
    density: float,
    planned: SceneEnergyMusicAnalysis,
) -> tuple[DuckingPlan, tuple[str, ...]]:
    if silence.intentional_silence or not narration or (not music.enabled and not ambience.enabled):
        warning = ("narration_missing_ducking_disabled",) if not narration else ()
        return DuckingPlan(False, 0.0, 0.0, 0, 0, "No enabled narrated layer requires ducking."), warning
    music_db = 0.0 if not music.enabled else (-14.0 if density >= 0.65 else -12.0)
    ambience_db = 0.0 if not ambience.enabled else (-8.0 if density >= 0.65 else -6.0)
    return DuckingPlan(True, music_db, ambience_db, 120, 500, "Metadata ducking protects narration readability."), ()


def _transition_pairs(
    base: Sequence[Mapping[str, Any]],
    allow_aggressive_transitions: bool = True,
) -> tuple[tuple[tuple[AudioTransition, AudioTransition], ...], tuple[tuple[str, ...], ...]]:
    if not base:
        return (), ()
    boundaries = []
    boundary_warnings = [[] for _ in base]
    for index in range(len(base) - 1):
        left, right = base[index], base[index + 1]
        target = right["energy_music"]
        supported_hard_cut = (
            (
                target.scene.story_role in {"contrast", "counterargument"}
                and (target.scene.story_tension or 0.0) >= 0.8
            )
            or (
                "hard_cut" in right["transition_requests"]
                and target.scene.story_role in {"revelation", "climax"}
                and (target.scene.story_emotional_intensity or 0.0) >= 0.75
                and target.scene.confidence >= 0.75
            )
        )
        supported_impact = target.scene.audio_intent == "climax" and target.supported_contrast_preserved
        supported_riser = (
            target.scene.audio_intent == "climax"
            and target.scene.story_role == "climax"
            and target.scene.confidence >= 0.75
        )
        if right["silence"].intentional_silence:
            transition = AudioTransition("silence", 300, "Intentional pause defines the scene boundary.")
        elif allow_aggressive_transitions and supported_impact:
            transition = AudioTransition("impact", 0, "Structurally supported climax contrast.")
        elif allow_aggressive_transitions and supported_hard_cut:
            transition = AudioTransition("hard_cut", 0, "Strong structural contrast supports a hard cut.")
        elif allow_aggressive_transitions and supported_riser:
            transition = AudioTransition("riser", 800, "Structurally supported climax build.")
        elif left["ambience"].enabled and right["ambience"].enabled and left["ambience"].type == right["ambience"].type:
            transition = AudioTransition("ambient_bridge", 800, "Compatible ambience bridges both scenes.")
        else:
            transition = AudioTransition("crossfade", 800, "Conservative scene boundary.")
            if left["ambience"].enabled or right["ambience"].enabled:
                boundary_warnings[index + 1].append("ambient_bridge_not_supported")
            if target.scene.audio_intent in {"climax", "tension"} and target.scene.story_role is None:
                boundary_warnings[index + 1].append("aggressive_transition_downgraded")
        if not allow_aggressive_transitions and (supported_impact or supported_hard_cut or supported_riser):
            boundary_warnings[index + 1].append("aggressive_transition_disabled_by_config")
        boundaries.append(transition)
    pairs = []
    for index in range(len(base)):
        transition_in = (
            AudioTransition("fade_in", 800, "First scene entry.")
            if index == 0 else boundaries[index - 1]
        )
        transition_out = (
            AudioTransition("fade_out", 800, "Final scene exit.")
            if index == len(base) - 1 else boundaries[index]
        )
        pairs.append((transition_in, transition_out))
    return tuple(pairs), tuple(_canonical_strings(values) for values in boundary_warnings)


def serialize_sound_analysis(analysis: ProjectSoundAnalysis) -> str:
    """Stable Step 4 metadata serialization for deterministic inspection."""
    return json.dumps(
        asdict(analysis), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False,
    ) + "\n"
