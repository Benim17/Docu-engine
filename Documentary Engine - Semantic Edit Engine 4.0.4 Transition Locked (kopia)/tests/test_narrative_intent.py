from copy import deepcopy

from engine.visual_director import (
    SUPPORTED_NARRATIVE_INTENTS,
    SceneVisualPlan,
    VisualDirector,
    classify_narrative_intent,
    classify_scene,
)


def scene(narration="", description="", terms=None):
    return {
        "start": 0.0, "end": 5.0, "duration": 5.0, "image": "scene.jpg",
        "voiceover_text": narration, "image_description": description,
        "match_terms": terms or [], "semantic_score": 1.0,
    }


def test_introduction_classification():
    assert classify_narrative_intent(scene("Our story begins here.")).narrative_intent == "introduction"


def test_context_classification():
    assert classify_narrative_intent(scene("Years before, this history shaped the region.")).narrative_intent == "context"


def test_explanation_classification():
    assert classify_narrative_intent(scene("This happened because the process failed.")).narrative_intent == "explanation"


def test_escalation_classification():
    assert classify_narrative_intent(scene("The conflict became an urgent crisis.")).narrative_intent == "escalation"


def test_reveal_classification():
    result = classify_narrative_intent(scene("The hidden record was discovered and exposed."))
    assert result.narrative_intent == "reveal"
    assert result.narrative_reason == "Narration contains discovery and disclosure indicators."


def test_climax_classification():
    assert classify_narrative_intent(scene("The decisive turning point had arrived.")).narrative_intent == "climax"


def test_reflection_classification():
    assert classify_narrative_intent(scene("Looking back, its legacy and impact remain.")).narrative_intent == "reflection"


def test_conclusion_classification():
    assert classify_narrative_intent(scene("In the end, the story reached its conclusion.")).narrative_intent == "conclusion"


def test_exact_fallback_behavior():
    result = classify_narrative_intent(scene("The account continued."), 2, 6)
    assert (result.narrative_intent, result.narrative_confidence, result.narrative_reason) == (
        "development", 0.50, "Default fallback.",
    )


def test_first_scene_position_influence():
    result = classify_narrative_intent(scene("A neutral scene."), 0, 5)
    assert result.narrative_intent == "introduction"
    assert "First scene" in result.narrative_reason


def test_final_scene_position_influence():
    result = classify_narrative_intent(scene("A neutral scene."), 4, 5)
    assert result.narrative_intent == "conclusion"
    assert "Final scene" in result.narrative_reason


def test_early_scene_position_influence():
    assert classify_narrative_intent(scene("A neutral scene."), 1, 8).narrative_intent == "context"


def test_strong_textual_evidence_beats_position():
    result = classify_narrative_intent(scene("The evidence was discovered and revealed."), 4, 5)
    assert result.narrative_intent == "reveal"


def test_confidence_range_and_valid_intent_set():
    samples = [
        classify_narrative_intent(scene("The urgent crisis reached a decisive turning point.")),
        classify_narrative_intent(scene("Neutral."), 0, 4),
        classify_narrative_intent(scene("Neutral."), 2, 5),
    ]
    assert all(0.0 <= item.narrative_confidence <= 1.0 for item in samples)
    assert all(item.narrative_intent in SUPPORTED_NARRATIVE_INTENTS for item in samples)


def test_repeated_output_is_deterministic_with_unordered_input():
    semantic_scene = scene("The record was revealed during a crisis.", terms={"urgent", "discovered", "evidence"})
    results = [classify_narrative_intent(semantic_scene, 3, 7) for _ in range(10)]
    assert all(result == results[0] for result in results)


def test_ties_use_stable_explicit_rule_order():
    assert classify_narrative_intent(scene("The decisive evidence was revealed.")).narrative_intent == "climax"


def test_supported_intents_are_complete():
    assert SUPPORTED_NARRATIVE_INTENTS == (
        "introduction", "context", "explanation", "development", "escalation",
        "reveal", "climax", "reflection", "conclusion",
    )


def test_shot_library_metadata_is_preserved_in_visual_plan():
    semantic_scene = scene("Looking back at the leader's legacy.", "A portrait of a person.")
    shot = classify_scene(semantic_scene)
    plan = VisualDirector().build_visual_plan([semantic_scene])[0]
    assert (plan.visual_intent, plan.confidence, plan.reason) == (
        shot.visual_intent, shot.confidence, shot.reason,
    )
    assert plan.narrative_intent == "reflection"


def test_semantic_scene_dictionaries_remain_authoritative_and_unchanged():
    original = [scene("The report was revealed.", "A document.")]
    snapshot = deepcopy(original)
    director = VisualDirector()
    plan = director.build_visual_plan(original)
    directed = director.direct(original)
    assert original == snapshot and directed == snapshot and directed is not original
    assert set(directed[0]) == set(snapshot[0])
    assert plan[0].narrative_intent == "reveal"
    assert not any(key.startswith("narrative_") for key in directed[0])


def test_scene_visual_plan_defaults_are_backwards_compatible():
    plan = SceneVisualPlan(scene_index=0)
    assert (plan.visual_intent, plan.confidence, plan.reason) == ("medium", 0.50, "Default fallback.")
    assert (plan.narrative_intent, plan.narrative_confidence, plan.narrative_reason) == (
        "development", 0.50, "Default fallback.",
    )


def test_empty_pipeline_input_remains_safe():
    director = VisualDirector()
    assert director.build_visual_plan([]) == []
    assert director.direct([]) == []
