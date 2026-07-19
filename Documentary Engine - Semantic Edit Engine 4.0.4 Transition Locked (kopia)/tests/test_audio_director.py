from copy import deepcopy
from dataclasses import replace

import pytest

from engine.audio_director import (
    AudioDiagnostics,
    AudioContractError,
    AudioDirector,
    AudioPlan,
    assemble_audio_artifacts,
    serialize_audio_artifact,
    serialize_energy_music_analysis,
    serialize_intent_tone_analysis,
    serialize_sound_analysis,
)


def semantic(*scenes):
    return {"scenes": list(scenes)}


def semantic_scene(index=0, text="", narrative_intent=None, **changes):
    value = {
        "scene_id": f"scene_{index + 1:03d}",
        "start": index * 5.0,
        "end": (index + 1) * 5.0,
        "duration": 5.0,
        "voiceover_text": text,
    }
    if narrative_intent is not None:
        value["narrative_intent"] = narrative_intent
    value.update(changes)
    return value


def story_for(semantic_plan, **scene_changes):
    scenes = []
    for index, source in enumerate(semantic_plan["scenes"]):
        scene = {
            "scene_id": source.get("scene_id", f"scene_{index + 1:03d}"),
            "scene_index": index,
            "story_role": "context",
            "story_phase": "middle",
            "emotional_intensity": 0.3,
            "tension": 0.2,
            "revelation_strength": 0.1,
        }
        scene.update(scene_changes)
        scenes.append(scene)
    return {
        "schema_version": "4.6.0",
        "story_director_version": "4.6.0",
        "scene_count": len(scenes),
        "scenes": scenes,
    }


def analyze(scene, story=None):
    return AudioDirector().analyze_intent_and_tone(semantic(scene), story)[0]


@pytest.mark.parametrize(("role", "phase", "expected"), [
    ("setup", "opening", "establish"),
    ("context", "middle", "support"),
    ("development", "rising_action", "build"),
    ("contrast", "complication", "tension"),
    ("revelation", "turning_point", "release"),
    ("epilogue", "closing", "reflection"),
    ("transition", "middle", "transition"),
    ("resolution", "resolution", "resolution"),
])
def test_structural_story_roles_classify_audio_intent(role, phase, expected):
    source = semantic(semantic_scene())
    result = AudioDirector().analyze_intent_and_tone(
        source, story_for(source, story_role=role, story_phase=phase)
    )[0]
    assert result.audio_intent == expected


def test_climax_requires_aligned_structural_support():
    source = semantic(semantic_scene(text="A decisive confrontation."))
    supported = story_for(
        source, story_role="climax", story_phase="climax", tension=0.8,
        emotional_intensity=0.75,
    )
    unsupported = story_for(
        source, story_role="climax", story_phase="middle", tension=0.45,
        emotional_intensity=0.4,
    )
    assert AudioDirector().analyze_intent_and_tone(source, supported)[0].audio_intent == "climax"
    assert AudioDirector().analyze_intent_and_tone(source, unsupported)[0].audio_intent != "climax"


@pytest.mark.parametrize(("text", "expected"), [
    ("Calm, quiet and steady.", "calm"),
    ("We remember the legacy in reflection.", "reflective"),
    ("A hidden secret remains an unanswered mystery.", "mysterious"),
    ("Danger, threat and fear spread through the crisis.", "tense"),
    ("Death, loss and grief followed the tragedy.", "somber"),
    ("Hope made recovery and a future possible.", "hopeful"),
    ("Joy returned; people celebrate, inspired and renewed.", "uplifting"),
    ("Död, förlust och sorg följde tragedin.", "somber"),
    ("Hopp om återhämtning gjorde framtiden möjlig.", "hopeful"),
])
def test_strong_aligned_non_aggressive_text_classifies_tone(text, expected):
    assert analyze(semantic_scene(text=text)).emotional_tone == expected


def test_dramatic_and_triumphant_require_structural_support():
    source = semantic(semantic_scene(text="Victory, success, hope and a renewed future."))
    resolution = story_for(source, story_role="resolution", story_phase="resolution")
    assert AudioDirector().analyze_intent_and_tone(source, resolution)[0].emotional_tone == "triumphant"
    assert AudioDirector().analyze_intent_and_tone(source, None)[0].emotional_tone != "triumphant"

    climax_source = semantic(semantic_scene(text="A decisive confrontation at the turning point."))
    climax = story_for(
        climax_source, story_role="climax", story_phase="climax", tension=0.8,
        emotional_intensity=0.8,
    )
    assert AudioDirector().analyze_intent_and_tone(climax_source, climax)[0].emotional_tone == "dramatic"


def test_conflicting_tones_resolve_conservatively_to_neutral():
    result = analyze(semantic_scene(text="Calm quiet steady danger threat fear."))
    assert result.emotional_tone == "neutral"
    assert result.resolved_conflicts


def test_negation_blocks_lexical_signal_in_english_and_swedish():
    english = analyze(semantic_scene(text="There was no danger, threat or fear."))
    swedish = analyze(semantic_scene(text="Det fanns ingen fara, hot eller rädsla."))
    assert english.emotional_tone == "neutral"
    assert swedish.emotional_tone == "neutral"


def test_case_punctuation_and_word_order_do_not_change_result():
    variants = [
        "HOPE! Recovery; future: possible.",
        "possible future recovery hope",
    ]
    results = [analyze(semantic_scene(text=value)) for value in variants]
    assert {(item.audio_intent, item.emotional_tone) for item in results} == {("support", "hopeful")}


def test_missing_story_uses_explicit_conservative_fallback():
    result = analyze(semantic_scene(text="Plain information is presented."))
    assert result.audio_intent == "support"
    assert result.emotional_tone == "neutral"
    assert result.fallback_used is True
    assert result.fallback_reason
    assert "story_director_plan_missing" in result.warnings


def test_empty_scene_list_is_valid_and_deterministic():
    first = AudioDirector().analyze_intent_and_tone(semantic())
    second = AudioDirector().analyze_intent_and_tone(semantic())
    assert first == second == ()
    assert serialize_intent_tone_analysis(first) == serialize_intent_tone_analysis(second)


@pytest.mark.parametrize("mutation", [
    lambda plan: plan.update(scene_count=2),
    lambda plan: plan["scenes"][0].update(scene_id="wrong"),
    lambda plan: plan["scenes"][0].update(scene_index=9),
    lambda plan: plan["scenes"][0].update(start=99),
    lambda plan: plan.pop("story_director_version"),
])
def test_story_plan_must_match_count_identity_timing_and_version(mutation):
    source = semantic(semantic_scene(text="Danger threat fear crisis."))
    story = story_for(source, story_role="contrast", tension=0.9)
    mutation(story)
    result = AudioDirector().analyze_intent_and_tone(source, story)[0]
    assert result.fallback_used is True
    assert any(value.startswith("story_director_") for value in result.warnings)


def test_unknown_future_story_enums_are_ignored_not_crashed():
    source = semantic(semantic_scene(text="Hope recovery future possible."))
    story = story_for(source, story_role="future_role", story_phase="future_phase")
    result = AudioDirector().analyze_intent_and_tone(source, story)[0]
    assert result.audio_intent == "support"
    assert result.emotional_tone == "hopeful"
    assert "unknown_story_role:future_role" in result.warnings
    assert "unknown_story_phase:future_phase" in result.warnings


@pytest.mark.parametrize(("schema_version", "planner_version"), [
    ("4.7.0", "4.7.0"),
    ("4.6.0", "5.0.0"),
    ("3.0.0", "4.6.0"),
])
def test_incompatible_story_director_version_is_optional_and_diagnostic(
    schema_version, planner_version,
):
    source = semantic(semantic_scene(text="Hope recovery future possible."))
    story = story_for(source, story_role="climax", story_phase="climax", tension=1.0)
    story["schema_version"] = schema_version
    story["story_director_version"] = planner_version
    result = AudioDirector().analyze_intent_and_tone(source, story)[0]
    assert result.scene_id == "scene_001"
    assert result.audio_intent == "support"
    assert result.emotional_tone == "hopeful"
    assert result.fallback_used is True
    assert any(value.startswith("story_director_version_incompatible:") for value in result.warnings)


def test_partially_corrupt_optional_story_values_are_ignored():
    source = semantic(semantic_scene())
    story = story_for(
        source, story_role="context", tension="broken", emotional_intensity=4,
        revelation_strength=None,
    )
    result = AudioDirector().analyze_intent_and_tone(source, story)[0]
    assert result.audio_intent == "support"
    assert "story_tension" in result.ignored_signals


def test_output_is_identical_byte_stable_and_inputs_are_not_mutated():
    source = semantic(
        semantic_scene(0, "A hidden secret remains an unanswered mystery."),
        semantic_scene(1, "Hope recovery future possible."),
    )
    story = story_for(source, story_role="context", story_phase="middle")
    before = deepcopy((source, story))
    first = AudioDirector().analyze_intent_and_tone(source, story)
    second = AudioDirector().analyze_intent_and_tone(source, story)
    assert first == second
    assert serialize_intent_tone_analysis(first).encode() == serialize_intent_tone_analysis(second).encode()
    assert (source, story) == before


@pytest.mark.parametrize("bad_plan", [None, {}, {"scenes": "bad"}])
def test_invalid_required_semantic_input_fails_closed(bad_plan):
    with pytest.raises(AudioContractError):
        AudioDirector().analyze_intent_and_tone(bad_plan)


def test_scene_order_ids_and_timing_are_preserved_exactly():
    source = semantic(
        semantic_scene(0, scene_id="alpha", start=2.0, end=7.5, duration=5.5),
        semantic_scene(1, scene_id="beta", start=7.5, end=9.0, duration=1.5),
    )
    results = AudioDirector().analyze_intent_and_tone(source)
    assert [(item.scene_id, item.scene_index, item.start, item.end, item.duration) for item in results] == [
        ("alpha", 0, 2.0, 7.5, 5.5),
        ("beta", 1, 7.5, 9.0, 1.5),
    ]


@pytest.mark.parametrize(("intent", "expected"), [
    (None, 0.4),
    ("context", 0.4),
    ("introduction", 0.36),
    ("reflection", 0.28),
    ("development", 0.52),
    ("escalation", 0.62),
    ("reveal", 0.32),
    ("conclusion", 0.42),
])
def test_raw_energy_has_locked_base_and_intent_adjustments(intent, expected):
    source = semantic(semantic_scene(narrative_intent=intent))
    result = AudioDirector().plan_energy_and_music(source).scenes[0]
    assert result.raw_energy == expected
    assert result.energy == expected


@pytest.mark.parametrize(("text", "adjustment"), [
    ("Calm quiet steady.", -0.08),
    ("We remember the legacy in reflection.", -0.07),
    ("A hidden secret unanswered mystery.", 0.04),
    ("Danger threat fear crisis.", 0.10),
    ("Death loss grief tragedy.", -0.05),
    ("Hope recovery future possible.", 0.03),
    ("Joy celebrate inspired renewed.", 0.08),
])
def test_tone_adjustments_modify_but_do_not_dominate_energy(text, adjustment):
    result = AudioDirector().plan_energy_and_music(
        semantic(semantic_scene(text=text, narrative_intent="context"))
    ).scenes[0]
    assert result.raw_energy == pytest.approx(0.4 + adjustment)


def test_structural_values_have_fixed_bounded_energy_weights():
    source = semantic(semantic_scene())
    story = story_for(
        source, story_role="context", tension=0.8, emotional_intensity=0.7,
        revelation_strength=0.5,
    )
    result = AudioDirector().plan_energy_and_music(source, story).scenes[0]
    assert result.raw_energy == 0.508


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -1, 2, "invalid"])
def test_invalid_optional_structural_numbers_are_ignored(invalid):
    source = semantic(semantic_scene())
    story = story_for(
        source, story_role="context", tension=invalid,
        emotional_intensity=invalid, revelation_strength=invalid,
    )
    result = AudioDirector().plan_energy_and_music(source, story).scenes[0]
    assert result.raw_energy == 0.4
    assert "story_tension" in result.scene.ignored_signals


def test_supported_climax_is_high_but_clamped_and_rounded():
    source = semantic(semantic_scene(text="A decisive confrontation."))
    story = story_for(
        source, story_role="climax", story_phase="climax", tension=1.0,
        emotional_intensity=1.0, revelation_strength=1.0,
    )
    result = AudioDirector().plan_energy_and_music(source, story).scenes[0]
    assert result.scene.audio_intent == "climax"
    assert result.raw_energy == 1.0
    assert result.energy == 1.0


def test_empty_and_single_scene_energy_curves():
    empty = AudioDirector().plan_energy_and_music(semantic())
    assert empty.project_summary.energy_curve == ()
    assert empty.project_summary.dominant_tone == "neutral"
    assert empty.diagnostics.flat_energy_curve is True
    single = AudioDirector().plan_energy_and_music(semantic(semantic_scene()))
    assert single.project_summary.energy_curve == (0.4,)


def test_normal_large_neighbor_delta_is_limited_in_one_forward_pass():
    source = semantic(
        semantic_scene(0, "Calm quiet steady.", narrative_intent="reflection"),
        semantic_scene(1, "Danger threat fear crisis.", narrative_intent="escalation"),
    )
    result = AudioDirector().plan_energy_and_music(source)
    assert result.scenes[0].energy == 0.2
    assert result.scenes[1].raw_energy == 0.72
    assert result.scenes[1].energy == 0.5
    assert result.scenes[1].energy_adjustment == "limited_up"
    assert "energy_curve_contains_limited_deltas" in result.diagnostics.warnings


def test_supported_climax_can_preserve_bounded_contrast_exception():
    source = semantic(semantic_scene(0), semantic_scene(1, "A decisive confrontation."))
    story = story_for(source)
    story["scenes"][1].update(
        story_role="climax", story_phase="climax", tension=1.0,
        emotional_intensity=1.0, revelation_strength=1.0,
    )
    result = AudioDirector().plan_energy_and_music(source, story)
    delta = result.scenes[1].energy - result.scenes[0].energy
    assert 0.30 < delta <= 0.45
    assert result.scenes[1].supported_contrast_preserved is True


def test_unsupported_contrast_is_limited_and_never_becomes_climax():
    source = semantic(semantic_scene(0), semantic_scene(1, narrative_intent="escalation"))
    story = story_for(source)
    story["scenes"][1].update(tension=1.0, emotional_intensity=1.0, revelation_strength=1.0)
    result = AudioDirector().plan_energy_and_music(source, story)
    assert result.scenes[1].scene.audio_intent != "climax"
    assert result.scenes[1].energy - result.scenes[0].energy == pytest.approx(0.30)
    assert result.scenes[1].supported_contrast_preserved is False


def test_dramatic_arc_keeps_climax_peak_release_drop_and_resolution_stability():
    source = semantic(*(semantic_scene(index) for index in range(5)))
    story = story_for(source)
    roles = [
        ("development", "rising_action", 0.45, 0.45),
        ("contrast", "complication", 0.75, 0.65),
        ("climax", "climax", 0.95, 0.9),
        ("revelation", "falling_action", 0.35, 0.45),
        ("resolution", "resolution", 0.2, 0.35),
    ]
    for item, (role, phase, tension, emotion) in zip(story["scenes"], roles):
        item.update(story_role=role, story_phase=phase, tension=tension, emotional_intensity=emotion)
    result = AudioDirector().plan_energy_and_music(source, story)
    curve = result.project_summary.energy_curve
    assert tuple(item.raw_energy for item in result.scenes) == (0.511, 0.789, 1.0, 0.333, 0.354)
    assert curve == (0.511, 0.789, 1.0, 0.7, 0.4)
    assert tuple(item.energy_adjustment for item in result.scenes) == (
        "unchanged", "unchanged", "unchanged", "limited_down", "limited_down",
    )
    assert not any(item.supported_contrast_preserved for item in result.scenes)
    assert curve[0] < curve[1] < curve[2]
    assert curve[2] > curve[3]
    assert curve[4] <= curve[3]
    assert all(abs(right - left) <= 0.45 for left, right in zip(curve, curve[1:]))
    assert result.scenes[3].music.style == "documentary"
    assert result.scenes[3].music.intensity == 0.504


def test_extreme_downward_release_is_limited_for_audio_continuity():
    analyses = AudioDirector().analyze_intent_and_tone(
        semantic(semantic_scene(0), semantic_scene(1, narrative_intent="reveal"))
    )
    final, adjustments, preserved = AudioDirector()._smooth_energy(analyses, (0.9, 0.3))
    assert final == (0.9, 0.6)
    assert adjustments == ("unchanged", "limited_down")
    assert preserved == (False, False)


@pytest.mark.parametrize(("text", "intent", "expected"), [
    ("Plain information.", "context", "documentary"),
    ("Calm quiet steady.", None, "ambient"),
    ("We remember the legacy in reflection.", "reflection", "minimal"),
    ("history", "context", "documentary"),
    ("nature", "context", "documentary"),
    ("electronic", "context", "documentary"),
])
def test_conservative_music_style_rules(text, intent, expected):
    result = AudioDirector().plan_energy_and_music(
        semantic(semantic_scene(text=text, narrative_intent=intent))
    )
    assert result.scenes[0].music.style == expected


def test_suspense_and_cinematic_require_structural_support():
    tension_source = semantic(semantic_scene(narrative_intent="escalation"))
    assert AudioDirector().plan_energy_and_music(tension_source).scenes[0].music.style == "documentary"
    tension_story = story_for(tension_source, story_role="contrast", story_phase="complication", tension=0.8)
    assert AudioDirector().plan_energy_and_music(tension_source, tension_story).scenes[0].music.style == "suspense"

    climax_source = semantic(semantic_scene(text="A decisive confrontation."))
    climax_story = story_for(
        climax_source, story_role="climax", story_phase="climax", tension=0.9,
        emotional_intensity=0.8,
    )
    assert AudioDirector().plan_energy_and_music(climax_source, climax_story).scenes[0].music.style == "cinematic"


def test_music_intensity_correlates_with_energy_and_support_is_restrained():
    support = AudioDirector().plan_energy_and_music(semantic(semantic_scene(narrative_intent="context"))).scenes[0]
    build = AudioDirector().plan_energy_and_music(semantic(semantic_scene(narrative_intent="development"))).scenes[0]
    assert 0.0 <= support.music.intensity < build.music.intensity <= 1.0
    assert support.music.intensity == 0.208


def test_project_summary_curve_tie_break_and_style_change_diagnostics_are_stable():
    source = semantic(
        semantic_scene(0, "Calm quiet steady."),
        semantic_scene(1, "Hope recovery future possible."),
    )
    result = AudioDirector().plan_energy_and_music(source)
    assert result.project_summary.energy_curve == tuple(scene.energy for scene in result.scenes)
    assert result.project_summary.dominant_tone == "calm"
    assert result.diagnostics.music_style_change_count == 1


def test_step_3_is_byte_deterministic_order_independent_and_non_mutating():
    first_scene = semantic_scene(text="Hope recovery future possible.", narrative_intent="context")
    reordered = {key: first_scene[key] for key in reversed(tuple(first_scene))}
    first_input, second_input = semantic(first_scene), semantic(reordered)
    before = deepcopy((first_input, second_input))
    first = AudioDirector().plan_energy_and_music(first_input)
    second = AudioDirector().plan_energy_and_music(second_input)
    assert serialize_energy_music_analysis(first).encode() == serialize_energy_music_analysis(second).encode()
    assert (first_input, second_input) == before


def test_multiple_supported_climaxes_remain_bounded_by_absolute_safety_delta():
    source = semantic(semantic_scene(0), semantic_scene(1), semantic_scene(2))
    story = story_for(source)
    for item in story["scenes"][1:]:
        item.update(
            story_role="climax", story_phase="climax", tension=1.0,
            emotional_intensity=1.0, revelation_strength=1.0,
        )
    curve = AudioDirector().plan_energy_and_music(source, story).project_summary.energy_curve
    assert all(abs(right - left) <= 0.45 for left, right in zip(curve, curve[1:]))
    assert curve[1] <= curve[2] <= 1.0


def test_release_does_not_gain_energy_after_tension():
    source = semantic(
        semantic_scene(0, narrative_intent="escalation"),
        semantic_scene(1, narrative_intent="reveal"),
    )
    curve = AudioDirector().plan_energy_and_music(source).project_summary.energy_curve
    assert curve[1] < curve[0]


def test_flat_extreme_and_fallback_project_diagnostics_are_deterministic():
    flat = AudioDirector().plan_energy_and_music(
        semantic(semantic_scene(0), semantic_scene(1))
    )
    assert flat.diagnostics.flat_energy_curve is True
    assert flat.diagnostics.extreme_energy_count == 0
    assert flat.diagnostics.fallback_count == 2
    assert flat.diagnostics.fallback_dominant is True
    assert "fallback_dominant_energy_planning" in flat.diagnostics.warnings


def test_aggressive_music_without_support_is_downgraded_with_diagnostics():
    result = AudioDirector().plan_energy_and_music(
        semantic(semantic_scene(narrative_intent="escalation"))
    ).scenes[0]
    assert result.music.style == "documentary"
    assert "aggressive_music_style_downgraded" in result.warnings


def test_incompatible_optional_story_metadata_keeps_music_conservative():
    source = semantic(semantic_scene(text="A decisive confrontation."))
    story = story_for(
        source, story_role="climax", story_phase="climax", tension=1.0,
        emotional_intensity=1.0,
    )
    story["story_director_version"] = "9.0.0"
    result = AudioDirector().plan_energy_and_music(source, story).scenes[0]
    assert result.scene.audio_intent != "climax"
    assert result.music.style == "documentary"
    assert any(value.startswith("story_director_version_incompatible:") for value in result.warnings)


def test_no_environment_signal_disables_ambience_without_generic_fallback():
    scene = AudioDirector().plan_sound_layers(semantic(semantic_scene())).scenes[0]
    assert scene.ambience.enabled is False
    assert scene.ambience.type == "none"
    assert scene.ambience.intensity == 0.0


@pytest.mark.parametrize(("kind", "description"), [
    ("urban", "A city street with traffic."),
    ("ocean", "The ocean coast and waves."),
    ("forest", "A forest of trees and woodland."),
    ("rain", "Rain, rainfall and raindrops."),
    ("transport", "A train enters the station beside a vehicle."),
])
def test_explicit_supported_environment_enables_ambience(kind, description):
    source = semantic(semantic_scene(environment_type=kind, scene_description=description))
    result = AudioDirector().plan_sound_layers(source).scenes[0]
    assert result.ambience.enabled is True
    assert result.ambience.type == kind
    assert 0.0 < result.ambience.intensity < result.energy_music.energy
    result.ambience.validate()


def test_single_environment_keyword_is_insufficient_but_three_aligned_signals_work():
    single = AudioDirector().plan_sound_layers(
        semantic(semantic_scene(scene_description="rain"))
    ).scenes[0]
    multiple = AudioDirector().plan_sound_layers(
        semantic(semantic_scene(scene_description="rain rainfall raindrops"))
    ).scenes[0]
    assert single.ambience.type == "none"
    assert "ambience_evidence_insufficient" in single.warnings
    assert multiple.ambience.type == "rain"


def test_battlefield_requires_explicit_and_multiple_strong_signals():
    weak = AudioDirector().plan_sound_layers(
        semantic(semantic_scene(environment_type="battlefield", scene_description="battlefield"))
    ).scenes[0]
    strong = AudioDirector().plan_sound_layers(
        semantic(semantic_scene(
            environment_type="battlefield",
            scene_description="Battlefield combat and artillery at the front line.",
        ))
    ).scenes[0]
    assert weak.ambience.type == "none"
    assert "aggressive_ambience_downgraded" in weak.warnings
    assert strong.ambience.type == "battlefield"


def test_narration_reduces_ambience_intensity():
    silent_source = semantic(semantic_scene(
        environment_type="urban", scene_description="city street traffic",
    ))
    narrated_source = deepcopy(silent_source)
    narrated_source["scenes"][0]["voiceover_text"] = "Narration explains the city street and traffic."
    silent = AudioDirector().plan_sound_layers(silent_source).scenes[0]
    narrated = AudioDirector().plan_sound_layers(narrated_source).scenes[0]
    assert narrated.ambience.intensity < silent.ambience.intensity


def test_ducking_uses_conservative_defaults_and_final_enabled_layers():
    narrated = AudioDirector().plan_sound_layers(
        semantic(semantic_scene(text="Short narration."))
    ).scenes[0]
    assert narrated.ducking.ducking_enabled is True
    assert narrated.ducking.music_reduction_db == -12.0
    assert narrated.ducking.ambience_reduction_db == 0.0
    assert (narrated.ducking.attack_ms, narrated.ducking.release_ms) == (120, 500)
    narrated.ducking.validate()


def test_dense_support_narration_uses_stronger_music_ducking():
    text = "A detailed narrated explanation provides facts and context " * 8
    scene = AudioDirector().plan_sound_layers(
        semantic(semantic_scene(text=text, narrative_intent="context"))
    ).scenes[0]
    assert scene.narration_density >= 0.65
    assert scene.ducking.music_reduction_db == -14.0


def test_missing_narration_disables_ducking_with_diagnostics():
    scene = AudioDirector().plan_sound_layers(
        semantic(semantic_scene(scene_description="city street traffic", environment_type="urban"))
    ).scenes[0]
    assert scene.ducking.ducking_enabled is False
    assert scene.ducking.music_reduction_db == 0.0
    assert scene.ducking.ambience_reduction_db == 0.0
    assert "narration_missing_ducking_disabled" in scene.warnings


def test_transition_pairs_are_authoritative_and_consistent():
    result = AudioDirector().plan_sound_layers(
        semantic(semantic_scene(0), semantic_scene(1), semantic_scene(2))
    )
    assert result.scenes[0].transition_in.type == "fade_in"
    assert result.scenes[-1].transition_out.type == "fade_out"
    for left, right in zip(result.scenes, result.scenes[1:]):
        assert left.transition_out == right.transition_in
        assert left.transition_out.type == "crossfade"


def test_single_scene_has_fade_in_and_fade_out():
    scene = AudioDirector().plan_sound_layers(semantic(semantic_scene())).scenes[0]
    assert scene.transition_in.type == "fade_in"
    assert scene.transition_out.type == "fade_out"


def test_supported_impact_and_riser_require_structural_climax():
    source = semantic(
        semantic_scene(0, "Calm quiet steady.", narrative_intent="reflection"),
        semantic_scene(1, "A decisive confrontation."),
    )
    story = story_for(source)
    story["scenes"][1].update(
        story_role="climax", story_phase="climax", tension=1.0,
        emotional_intensity=1.0, revelation_strength=1.0,
    )
    impact = AudioDirector().plan_sound_layers(source, story)
    assert impact.scenes[0].transition_out.type == "impact"
    assert impact.scenes[0].transition_out == impact.scenes[1].transition_in

    rising_source = semantic(semantic_scene(0), semantic_scene(1, "A decisive confrontation."))
    rising_story = story_for(rising_source)
    rising_story["scenes"][0].update(
        story_role="contrast", story_phase="complication", tension=0.8,
        emotional_intensity=0.7,
    )
    rising_story["scenes"][1].update(
        story_role="climax", story_phase="climax", tension=0.9,
        emotional_intensity=0.8, revelation_strength=0.8,
    )
    riser = AudioDirector().plan_sound_layers(rising_source, rising_story)
    assert riser.scenes[0].transition_out.type == "riser"


def test_unsupported_aggressive_transition_is_downgraded():
    result = AudioDirector().plan_sound_layers(
        semantic(semantic_scene(0), semantic_scene(1, narrative_intent="escalation"))
    )
    assert result.scenes[0].transition_out.type == "crossfade"
    assert "aggressive_transition_downgraded" in result.scenes[1].warnings
    assert result.diagnostics.unsupported_aggressive_transition_count == 1


def test_supported_hard_cut_requires_strong_structural_contrast():
    source = semantic(semantic_scene(0), semantic_scene(1))
    story = story_for(source)
    story["scenes"][1].update(story_role="contrast", story_phase="complication", tension=0.9)
    result = AudioDirector().plan_sound_layers(source, story)
    assert result.scenes[0].transition_out.type == "hard_cut"


def test_ambient_bridge_requires_matching_enabled_ambience():
    source = semantic(
        semantic_scene(0, environment_type="urban", scene_description="city street traffic"),
        semantic_scene(1, environment_type="urban", scene_description="city street traffic"),
    )
    bridge = AudioDirector().plan_sound_layers(source)
    assert bridge.scenes[0].transition_out.type == "ambient_bridge"
    source["scenes"][1]["environment_type"] = "ocean"
    source["scenes"][1]["scene_description"] = "ocean coast waves"
    no_bridge = AudioDirector().plan_sound_layers(source)
    assert no_bridge.scenes[0].transition_out.type == "crossfade"
    assert "ambient_bridge_not_supported" in no_bridge.scenes[1].warnings


def test_reflection_or_text_pause_alone_does_not_create_silence():
    source = semantic(semantic_scene(
        text="A long reflective pause.", narrative_intent="reflection", intentional_pause=True,
    ))
    scene = AudioDirector().plan_sound_layers(source).scenes[0]
    assert scene.silence.intentional_silence is False


@pytest.mark.parametrize("role", ["epilogue", "revelation"])
def test_explicit_structurally_supported_pause_suppresses_audio(role):
    source = semantic(semantic_scene(
        text="Narration before a deliberate pause.", intentional_pause=True,
        environment_type="urban", scene_description="city street traffic",
    ))
    story = story_for(
        source, story_role=role,
        story_phase="closing" if role == "epilogue" else "turning_point",
        revelation_strength=0.9, emotional_intensity=0.8,
    )
    scene = AudioDirector().plan_sound_layers(source, story).scenes[0]
    assert scene.silence.intentional_silence is True
    assert 0 <= scene.silence.pre_scene_silence_ms <= 300
    assert 0 <= scene.silence.post_scene_silence_ms <= 400
    assert scene.music.enabled is False and scene.music.style == "none"
    assert scene.ambience.enabled is False and scene.ambience.type == "none"
    assert scene.ducking.ducking_enabled is False
    scene.silence.validate()


def test_silence_boundary_uses_consistent_silence_transition_pair():
    source = semantic(semantic_scene(0), semantic_scene(1, intentional_pause=True))
    story = story_for(source)
    story["scenes"][1].update(
        story_role="revelation", story_phase="turning_point",
        revelation_strength=0.9, emotional_intensity=0.8,
    )
    result = AudioDirector().plan_sound_layers(source, story)
    assert result.scenes[0].transition_out.type == "silence"
    assert result.scenes[0].transition_out == result.scenes[1].transition_in


def test_empty_sound_project_and_debug_serialization_are_deterministic():
    first = AudioDirector().plan_sound_layers(semantic())
    second = AudioDirector().plan_sound_layers(semantic())
    assert first.scenes == ()
    assert serialize_sound_analysis(first).encode() == serialize_sound_analysis(second).encode()


def test_sound_planning_does_not_mutate_upstream_inputs():
    source = semantic(semantic_scene(
        text="Narration.", environment_type="urban", scene_description="city street traffic",
    ))
    story = story_for(source)
    before = deepcopy((source, story))
    AudioDirector().plan_sound_layers(source, story)
    assert (source, story) == before


def _urban_scene(index, **changes):
    return semantic_scene(
        index, environment_type="urban", scene_description="city street traffic", **changes,
    )


def test_transition_priority_silence_over_compatible_ambience():
    source = semantic(_urban_scene(0), _urban_scene(1, intentional_pause=True))
    story = story_for(source)
    story["scenes"][1].update(
        story_role="revelation", story_phase="turning_point",
        revelation_strength=0.9, emotional_intensity=0.8,
    )
    result = AudioDirector().plan_sound_layers(source, story)
    assert "explicit_environment:urban" in result.scenes[1].ambience.source_basis
    assert "ambience_suppressed_by_intentional_silence" in result.scenes[1].warnings
    assert result.scenes[0].transition_out.type == "silence"


def test_transition_priority_impact_over_compatible_ambience_and_external_edges():
    source = semantic(
        _urban_scene(0, text="Calm quiet steady.", narrative_intent="reflection"),
        _urban_scene(1, text="A decisive confrontation."),
    )
    story = story_for(source)
    story["scenes"][1].update(
        story_role="climax", story_phase="climax", tension=1.0,
        emotional_intensity=1.0, revelation_strength=1.0,
    )
    result = AudioDirector().plan_sound_layers(source, story)
    assert result.scenes[0].ambience.type == result.scenes[1].ambience.type == "urban"
    assert result.scenes[0].transition_in.type == "fade_in"
    assert result.scenes[0].transition_out.type == "impact"
    assert result.scenes[1].transition_in.type == "impact"
    assert result.scenes[1].transition_out.type == "fade_out"


def test_transition_priority_riser_over_compatible_ambience():
    source = semantic(_urban_scene(0), _urban_scene(1, text="A decisive confrontation."))
    story = story_for(source)
    story["scenes"][0].update(
        story_role="contrast", story_phase="complication", tension=0.8,
        emotional_intensity=0.7,
    )
    story["scenes"][1].update(
        story_role="climax", story_phase="climax", tension=0.9,
        emotional_intensity=0.8, revelation_strength=0.8,
    )
    result = AudioDirector().plan_sound_layers(source, story)
    assert result.scenes[0].transition_out.type == "riser"


def test_transition_priority_compatible_ambience_over_unsupported_aggression():
    source = semantic(_urban_scene(0), _urban_scene(1, narrative_intent="escalation"))
    result = AudioDirector().plan_sound_layers(source)
    assert result.scenes[0].transition_out.type == "ambient_bridge"


def test_transition_priority_silence_over_supported_requested_hard_cut():
    source = semantic(
        semantic_scene(0),
        semantic_scene(1, intentional_pause=True, audio_transition_intent="hard_cut"),
    )
    story = story_for(source)
    story["scenes"][1].update(
        story_role="revelation", story_phase="turning_point",
        revelation_strength=0.9, emotional_intensity=0.9,
    )
    result = AudioDirector().plan_sound_layers(source, story)
    assert result.scenes[0].transition_out.type == "silence"


def test_ambience_intensity_never_exceeds_very_low_or_zero_energy():
    from engine.audio_director.sound_planning import _ambience

    source_scene = _urban_scene(0)
    base = AudioDirector().plan_energy_and_music(semantic(source_scene)).scenes[0]
    low_plan = replace(base, energy=0.03)
    low, _, _ = _ambience(low_plan, source_scene, False)
    assert low.enabled is True
    assert 0.0 < low.intensity <= 0.03
    zero, warnings, _ = _ambience(replace(base, energy=0.0), source_scene, False)
    assert zero.enabled is False
    assert zero.type == "none" and zero.intensity == 0.0
    assert "ambience_disabled_at_zero_energy" in warnings


def test_silence_suppression_creates_new_models_without_changing_step_3_result():
    source = semantic(_urban_scene(0, intentional_pause=True))
    story = story_for(
        source, story_role="revelation", story_phase="turning_point",
        revelation_strength=0.9, emotional_intensity=0.8,
    )
    director = AudioDirector()
    before = director.plan_energy_and_music(source, story)
    final = director.plan_sound_layers(source, story)
    after = director.plan_energy_and_music(source, story)
    assert before == after
    assert before.scenes[0].music.enabled is True
    assert final.scenes[0].music.enabled is False
    assert "Intentional silence" in final.scenes[0].music.rationale
    assert "Intentional silence" in final.scenes[0].ambience.fallback_reason


def test_disabled_ducking_contract_uses_stable_zero_timing_and_reductions():
    source = semantic(semantic_scene(intentional_pause=True))
    story = story_for(
        source, story_role="revelation", story_phase="turning_point",
        revelation_strength=0.9, emotional_intensity=0.8,
    )
    ducking = AudioDirector().plan_sound_layers(source, story).scenes[0].ducking
    assert ducking.ducking_enabled is False
    assert (ducking.music_reduction_db, ducking.ambience_reduction_db) == (0.0, 0.0)
    assert (ducking.attack_ms, ducking.release_ms) == (0, 0)


def test_final_assembly_builds_valid_public_plan_and_diagnostics():
    source = semantic(_urban_scene(
        0, text="A narrated city scene.", narrative_intent="context",
    ))
    artifacts = AudioDirector().build_audio_artifacts(source)
    assert isinstance(artifacts.plan, AudioPlan)
    assert isinstance(artifacts.diagnostics, AudioDiagnostics)
    artifacts.plan.validate()
    artifacts.diagnostics.validate()
    assert artifacts.plan.status == "planned"
    assert artifacts.plan.scenes[0].ambience.type == "urban"


def test_final_multiple_scene_order_timing_curve_and_boundaries_match():
    source = semantic(
        semantic_scene(0, scene_id="alpha", start=1.0, end=4.0, duration=3.0, narrative_intent="context"),
        semantic_scene(1, scene_id="beta", start=4.0, end=9.5, duration=5.5, narrative_intent="development"),
    )
    artifacts = AudioDirector().build_audio_artifacts(source)
    assert [(scene.scene_id, scene.start, scene.end, scene.duration) for scene in artifacts.plan.scenes] == [
        ("alpha", 1.0, 4.0, 3.0), ("beta", 4.0, 9.5, 5.5),
    ]
    assert artifacts.plan.project_summary.energy_curve == tuple(scene.energy for scene in artifacts.plan.scenes)
    assert artifacts.plan.scenes[0].transition_out == artifacts.plan.scenes[1].transition_in
    assert len(artifacts.plan.scenes) == len(artifacts.diagnostics.scenes) == 2
    assert not any(signal.startswith("scene_id:") for item in artifacts.diagnostics.scenes for signal in item.source_signals)


def test_final_empty_project_is_planned_with_explicit_zero_confidence():
    artifacts = AudioDirector().build_audio_artifacts(semantic())
    assert artifacts.plan.status == "planned"
    assert artifacts.plan.scenes == ()
    assert artifacts.plan.project_summary.energy_curve == ()
    assert artifacts.diagnostics.project.confidence == 0.0
    assert artifacts.diagnostics.project.flat_energy_curve is True
    assert artifacts.diagnostics.project.fallback_dominant is False


def test_final_silence_suppression_and_ducking_use_final_state():
    source = semantic(_urban_scene(0, text="Narration.", intentional_pause=True))
    story = story_for(
        source, story_role="revelation", story_phase="turning_point",
        revelation_strength=0.9, emotional_intensity=0.8,
    )
    scene = AudioDirector().build_audio_artifacts(source, story).plan.scenes[0]
    assert scene.silence.intentional_silence is True
    assert scene.music.enabled is False and scene.music.style == "none"
    assert scene.ambience.enabled is False and scene.ambience.type == "none"
    assert scene.ducking.ducking_enabled is False


def _status_source(fallback_flags):
    scenes = []
    for index, fallback in enumerate(fallback_flags):
        scenes.append(semantic_scene(
            index,
            text="Useful narration.",
            narrative_intent=None if fallback else "context",
        ))
    return semantic(*scenes)


@pytest.mark.parametrize(("flags", "expected"), [
    ((False, False), "planned"),
    ((True, False), "planned"),
    ((True, True, False), "fallback"),
    ((True,), "fallback"),
])
def test_final_status_uses_strict_more_than_half_fallback_threshold(flags, expected):
    artifacts = AudioDirector().build_audio_artifacts(_status_source(flags))
    assert artifacts.plan.status == expected
    assert artifacts.diagnostics.project.fallback_count == sum(flags)
    assert artifacts.diagnostics.project.fallback_dominant is (expected == "fallback")


def test_missing_story_with_useful_semantic_intent_remains_planned():
    source = semantic(semantic_scene(text="Useful narration.", narrative_intent="context"))
    artifacts = AudioDirector().build_audio_artifacts(source)
    assert artifacts.plan.status == "planned"
    assert artifacts.diagnostics.project.scene_without_usable_input_count == 0
    assert "story_director_plan" in artifacts.diagnostics.project.missing_inputs


def test_final_diagnostics_use_only_final_music_and_ambience_state():
    source = semantic(
        _urban_scene(0, text="Calm quiet steady."),
        _urban_scene(1, text="Narration.", intentional_pause=True),
    )
    story = story_for(source)
    story["scenes"][1].update(
        story_role="revelation", story_phase="turning_point",
        revelation_strength=0.9, emotional_intensity=0.8,
    )
    artifacts = AudioDirector().build_audio_artifacts(source, story)
    assert [scene.music.style for scene in artifacts.plan.scenes] == ["minimal", "none"]
    assert artifacts.diagnostics.project.music_style_change_count == 1
    assert artifacts.diagnostics.project.ambience_scene_count == 1


def test_final_flat_nonflat_extreme_and_style_metrics_have_fixed_definitions():
    source = semantic(semantic_scene(0, narrative_intent="context"), semantic_scene(1, narrative_intent="context"))
    flat = AudioDirector().build_audio_artifacts(source)
    assert flat.diagnostics.project.flat_energy_curve is True
    sound = AudioDirector().plan_sound_layers(source)
    low = replace(sound.scenes[0].energy_music, energy=0.1)
    high = replace(sound.scenes[1].energy_music, energy=0.9)
    altered_scenes = (
        replace(sound.scenes[0], energy_music=low),
        replace(sound.scenes[1], energy_music=high),
    )
    altered_energy = replace(sound.energy_plan, scenes=(low, high))
    altered_energy = replace(
        altered_energy,
        project_summary=replace(altered_energy.project_summary, energy_curve=(0.1, 0.9)),
    )
    altered = replace(sound, energy_plan=altered_energy, scenes=altered_scenes)
    final = assemble_audio_artifacts(altered)
    assert final.diagnostics.project.flat_energy_curve is False
    assert final.diagnostics.project.extreme_energy_count == 2


def test_project_confidence_is_mean_with_fallback_and_optional_rejection_penalties():
    source = _status_source((True, True, False))
    plain = AudioDirector().build_audio_artifacts(source)
    mean = sum(item.confidence for item in plain.diagnostics.scenes) / 3
    assert plain.diagnostics.project.confidence == round(max(0.0, mean - 0.10), 4)

    story = story_for(source)
    story["story_director_version"] = "9.0.0"
    rejected = AudioDirector().build_audio_artifacts(source, story)
    rejected_mean = sum(item.confidence for item in rejected.diagnostics.scenes) / 3
    assert rejected.diagnostics.project.confidence == round(max(0.0, rejected_mean - 0.15), 4)


def test_final_project_warnings_are_deduplicated_and_sorted():
    artifacts = AudioDirector().build_audio_artifacts(_status_source((True, True, True)))
    warnings = artifacts.diagnostics.project.warnings
    assert warnings == tuple(sorted(set(warnings), key=lambda value: (value.casefold(), value)))
    assert warnings.count("story_director_plan_missing") == 1


def test_final_artifacts_are_byte_stable_and_input_order_independent():
    scene = _urban_scene(0, text="Useful narration.", narrative_intent="context")
    reordered = {key: scene[key] for key in reversed(tuple(scene))}
    first = AudioDirector().build_audio_artifacts(semantic(scene))
    second = AudioDirector().build_audio_artifacts(semantic(reordered))
    assert serialize_audio_artifact(first.plan).encode() == serialize_audio_artifact(second.plan).encode()
    assert serialize_audio_artifact(first.diagnostics).encode() == serialize_audio_artifact(second.diagnostics).encode()
    payload = serialize_audio_artifact(first.plan)
    assert "timestamp" not in payload and "machine" not in payload and "path" not in payload


def test_final_assembly_does_not_mutate_inputs_and_repeated_runs_match():
    source = semantic(semantic_scene(text="Useful narration.", narrative_intent="context"))
    story = story_for(source)
    before = deepcopy((source, story))
    first = AudioDirector().build_audio_artifacts(source, story)
    second = AudioDirector().build_audio_artifacts(source, story)
    assert first == second
    assert (source, story) == before


def test_final_assembly_rejects_mismatched_internal_scene_count_and_order():
    sound = AudioDirector().plan_sound_layers(semantic(semantic_scene()))
    with pytest.raises(AudioContractError):
        assemble_audio_artifacts(replace(sound, scenes=()))
    duplicate = replace(sound, scenes=(sound.scenes[0], sound.scenes[0]))
    with pytest.raises(AudioContractError):
        assemble_audio_artifacts(duplicate)


def test_final_assembly_rejects_wrong_or_reordered_internal_scene_identity():
    source = semantic(semantic_scene(0, scene_id="alpha"), semantic_scene(1, scene_id="beta"))
    sound = AudioDirector().plan_sound_layers(source)
    with pytest.raises(AudioContractError):
        assemble_audio_artifacts(replace(sound, scenes=tuple(reversed(sound.scenes))))

    wrong_analysis = replace(sound.scenes[0].energy_music.scene, scene_id="wrong")
    wrong_energy_scene = replace(sound.scenes[0].energy_music, scene=wrong_analysis)
    wrong_sound_scene = replace(sound.scenes[0], energy_music=wrong_energy_scene)
    wrong_energy_plan = replace(
        sound.energy_plan,
        scenes=(wrong_energy_scene, sound.energy_plan.scenes[1]),
    )
    with pytest.raises(AudioContractError):
        assemble_audio_artifacts(replace(
            sound,
            energy_plan=wrong_energy_plan,
            scenes=(wrong_sound_scene, sound.scenes[1]),
        ))


def test_conservative_evidence_based_scene_is_not_fallback():
    source = semantic(semantic_scene(
        text="A clear factual explanation provides useful context.",
        narrative_intent="context",
    ))
    artifacts = AudioDirector().build_audio_artifacts(source)
    scene = artifacts.plan.scenes[0]
    assert scene.audio_intent == "support"
    assert scene.emotional_tone == "neutral"
    assert scene.music.style == "documentary"
    assert scene.diagnostics.fallback_used is False
    assert artifacts.plan.status == "planned"


@pytest.mark.parametrize("bad_plan", [None, {}, {"scenes": "invalid"}])
def test_final_core_fails_closed_for_invalid_required_semantics(bad_plan):
    with pytest.raises(AudioContractError):
        AudioDirector().build_audio_artifacts(bad_plan)
