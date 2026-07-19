from copy import deepcopy

import pytest

from engine.audio_director import (
    AudioContractError,
    AudioDirector,
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
