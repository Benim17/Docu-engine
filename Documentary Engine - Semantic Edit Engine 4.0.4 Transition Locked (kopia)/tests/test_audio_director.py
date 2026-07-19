from copy import deepcopy

import pytest

from engine.audio_director import (
    AudioContractError,
    AudioDirector,
    serialize_energy_music_analysis,
    serialize_intent_tone_analysis,
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
    final, adjustments, preserved = AudioDirector._smooth_energy(analyses, (0.9, 0.3))
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
