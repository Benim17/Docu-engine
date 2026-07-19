import json
from dataclasses import replace

import pytest

from engine.audio_director import (
    AMBIENCE_TYPES,
    AUDIO_INTENTS,
    EMOTIONAL_TONES,
    MUSIC_STYLES,
    TRANSITION_TYPES,
    AmbiencePlan,
    AudioContractError,
    AudioDiagnostics,
    AudioPlan,
    AudioTransition,
    DuckingPlan,
    MusicPlan,
    ProjectAudioDiagnostics,
    ProjectAudioSummary,
    SceneAudioDiagnostics,
    SceneAudioPlan,
    SilencePlan,
    serialize_audio_artifact,
)


def scene_diagnostics(**changes):
    values = {
        "confidence": 0.72,
        "warnings": (),
        "fallback_used": False,
        "fallback_reason": None,
        "missing_inputs": (),
        "source_signals": ("semantic_scene", "story_role"),
        "resolved_conflicts": (),
    }
    values.update(changes)
    return SceneAudioDiagnostics(**values)


def scene(index=0, scene_id="scene_001", energy=0.42, **changes):
    values = {
        "scene_id": scene_id,
        "scene_index": index,
        "start": index * 5.0,
        "end": (index + 1) * 5.0,
        "duration": 5.0,
        "audio_intent": "support",
        "emotional_tone": "neutral",
        "energy": energy,
        "music": MusicPlan(True, "documentary", 0.35, "Conservative documentary support."),
        "ambience": AmbiencePlan(False, "none", 0.0, 0.25, (), "No environment signal."),
        "ducking": DuckingPlan(True, -12.0, -6.0, 120, 500, "Narration remains primary."),
        "transition_in": AudioTransition("crossfade", 800, "Conservative scene entry."),
        "transition_out": AudioTransition("crossfade", 800, "Conservative scene exit."),
        "silence": SilencePlan(0, 0, False, False, False, "No supported silence intent."),
        "diagnostics": scene_diagnostics(),
    }
    values.update(changes)
    return SceneAudioPlan(**values)


def plan(*scenes):
    if not scenes:
        scenes = (scene(),)
    energies = tuple(item.energy for item in scenes)
    return AudioPlan(
        ProjectAudioSummary("neutral", "documentary", energies, len(scenes), -14.0),
        tuple(scenes),
    )


def project_diagnostics(scene_count=1, **changes):
    values = {
        "confidence": 0.68,
        "warnings": (),
        "fallback_count": 0,
        "missing_inputs": (),
        "resolved_conflicts": (),
        "flat_energy_curve": scene_count <= 1,
        "extreme_energy_count": 0,
        "music_style_change_count": 0,
        "unsupported_aggressive_transition_count": 0,
        "ambience_scene_count": 0,
        "scene_without_usable_input_count": 0,
        "fallback_dominant": False,
    }
    values.update(changes)
    return ProjectAudioDiagnostics(**values)


def diagnostics(*scenes):
    if not scenes:
        scenes = (scene_diagnostics(),)
    return AudioDiagnostics(project_diagnostics(len(scenes)), tuple(scenes))


def test_contract_exposes_only_documented_4_7_enums():
    assert AUDIO_INTENTS == {
        "establish", "support", "build", "tension", "release", "reflection",
        "transition", "climax", "resolution", "neutral",
    }
    assert "triumphant" in EMOTIONAL_TONES
    assert "battlefield" in AMBIENCE_TYPES
    assert {"impact", "riser", "hard_cut"} <= TRANSITION_TYPES
    assert "documentary" in MUSIC_STYLES and "none" in MUSIC_STYLES


def test_valid_audio_plan_schema_and_versions():
    payload = plan().to_dict()
    assert list(payload) == [
        "schema_version", "planner_version", "deterministic", "status",
        "project_summary", "scene_count", "scenes",
    ]
    assert payload["schema_version"] == "1.0"
    assert payload["planner_version"] == "4.7.0"
    assert payload["deterministic"] is True
    assert payload["scene_count"] == 1


def test_valid_audio_diagnostics_schema_and_versions():
    payload = diagnostics().to_dict()
    assert list(payload) == [
        "schema_version", "planner_version", "deterministic", "scene_count", "project", "scenes",
    ]
    assert payload["planner_version"] == "4.7.0"
    assert payload["scene_count"] == 1


def test_serialization_is_byte_stable_sorted_and_has_terminal_newline():
    first = serialize_audio_artifact(plan()).encode("utf-8")
    second = serialize_audio_artifact(plan()).encode("utf-8")
    assert first == second
    assert first.endswith(b"\n")
    assert b"timestamp" not in first and b"uuid" not in first
    assert list(json.loads(first)) == sorted(json.loads(first))


def test_serialization_rounds_floats_to_four_decimals_and_normalizes_negative_zero():
    audio_plan = plan(replace(scene(), energy=0.333333))
    audio_plan = replace(
        audio_plan,
        project_summary=replace(audio_plan.project_summary, energy_curve=(0.333333,)),
    )
    payload = json.loads(serialize_audio_artifact(audio_plan))
    assert payload["scenes"][0]["energy"] == 0.3333
    assert payload["project_summary"]["energy_curve"] == [0.3333]

    zero_plan = plan(replace(scene(), start=-0.0))
    assert '"start": 0.0' in serialize_audio_artifact(zero_plan)


def test_serializer_rejects_unvalidated_free_form_payloads():
    with pytest.raises(AudioContractError):
        serialize_audio_artifact({"energy": 0.5})


@pytest.mark.parametrize("field,value", [
    ("energy", -0.01),
    ("energy", 1.01),
    ("energy", float("nan")),
])
def test_scene_energy_rejects_out_of_range_and_non_finite_values(field, value):
    with pytest.raises(AudioContractError):
        plan(replace(scene(), **{field: value})).validate()


@pytest.mark.parametrize("field,value", [
    ("audio_intent", "explode"),
    ("emotional_tone", "conflicted_future_tone"),
])
def test_unknown_scene_enums_are_rejected(field, value):
    with pytest.raises(AudioContractError):
        plan(replace(scene(), **{field: value})).validate()


def test_scene_order_and_ids_are_stable_and_unique():
    first = scene(0, "source-scene-a", 0.35)
    second = scene(1, "source-scene-b", 0.45)
    plan(first, second).validate()
    with pytest.raises(AudioContractError):
        plan(first, replace(second, scene_index=2)).validate()
    with pytest.raises(AudioContractError):
        plan(first, replace(second, scene_id="source-scene-a")).validate()


def test_scene_timing_contract_requires_consistent_duration():
    with pytest.raises(AudioContractError):
        plan(replace(scene(), duration=4.0)).validate()
    with pytest.raises(AudioContractError):
        plan(replace(scene(), end=0.0)).validate()


def test_energy_curve_must_equal_ordered_scene_values():
    audio_plan = plan(scene(0, "a", 0.3), scene(1, "b", 0.6))
    audio_plan.validate()
    broken = replace(
        audio_plan,
        project_summary=replace(audio_plan.project_summary, energy_curve=(0.6, 0.3)),
    )
    with pytest.raises(AudioContractError):
        broken.validate()


def test_disabled_music_and_ambience_require_conservative_none_contract():
    MusicPlan(False, "none", 0.0, "Music is not supported.").validate()
    AmbiencePlan(False, "none", 0.0, 0.2, (), "No environment signal.").validate()
    with pytest.raises(AudioContractError):
        MusicPlan(False, "cinematic", 0.0, "Invalid disabled style.").validate()
    with pytest.raises(AudioContractError):
        AmbiencePlan(False, "urban", 0.0, 0.2, ("city",), None).validate()


def test_ducking_ranges_and_disabled_contract_are_validated():
    DuckingPlan(False, 0.0, 0.0, 0, 0, "No narration ducking required.").validate()
    with pytest.raises(AudioContractError):
        DuckingPlan(False, -12.0, 0.0, 0, 0, "Contradictory disabled ducking.").validate()
    with pytest.raises(AudioContractError):
        DuckingPlan(True, -61.0, -6.0, 120, 500, "Out of range.").validate()


def test_silence_contract_distinguishes_intentional_silence_from_missing_data():
    SilencePlan(250, 300, True, True, True, "Supported reflective pause.").validate()
    SilencePlan(0, 0, False, False, False, "No silence evidence.").validate()
    with pytest.raises(AudioContractError):
        SilencePlan(250, 0, False, False, False, "Unintentional duration is invalid.").validate()


@pytest.mark.parametrize("transition,duration", [
    ("none", 0), ("hard_cut", 0), ("impact", 0), ("crossfade", 800), ("riser", 1200),
])
def test_transition_enum_and_duration_contract(transition, duration):
    AudioTransition(transition, duration, "Validated abstract transition metadata.").validate()


def test_diagnostic_string_collections_require_canonical_stable_sorting():
    scene_diagnostics(source_signals=("semantic_scene", "story_role")).validate()
    with pytest.raises(AudioContractError):
        scene_diagnostics(source_signals=("story_role", "semantic_scene")).validate()
    with pytest.raises(AudioContractError):
        scene_diagnostics(warnings=("duplicate", "duplicate")).validate()


def test_fallback_diagnostics_require_explicit_reason():
    with pytest.raises(AudioContractError):
        scene_diagnostics(fallback_used=True, fallback_reason=None).validate()
    scene_diagnostics(
        confidence=0.25,
        fallback_used=True,
        fallback_reason="Optional metadata was unavailable.",
        missing_inputs=("story_director_plan",),
    ).validate()


def test_project_diagnostic_counts_cannot_exceed_scene_count():
    diagnostics().validate()
    broken = AudioDiagnostics(
        project_diagnostics(1, fallback_count=2),
        (scene_diagnostics(),),
    )
    with pytest.raises(AudioContractError):
        broken.validate()


def test_target_loudness_is_metadata_but_still_bounded():
    plan().validate()
    broken = replace(
        plan(),
        project_summary=replace(plan().project_summary, target_loudness_lufs=-70.0),
    )
    with pytest.raises(AudioContractError):
        broken.validate()


def test_empty_project_contract_is_valid():
    empty = AudioPlan(
        ProjectAudioSummary("neutral", "documentary", (), 0, -14.0),
        (),
        status="fallback",
    )
    empty.validate()
    empty_diagnostics = AudioDiagnostics(project_diagnostics(0), ())
    empty_diagnostics.validate()
