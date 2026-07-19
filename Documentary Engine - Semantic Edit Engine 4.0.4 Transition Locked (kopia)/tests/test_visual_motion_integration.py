from copy import deepcopy

from engine.motion.analyzer import SceneAnalysis
from engine.motion.planner import build_motion_plan
from engine.visual_director import SceneVisualPlan, VisualDirector, build_motion_guidance


def semantic_scene(narration, description):
    return {
        "start": 0.0, "end": 5.0, "duration": 5.0, "image": "scene.jpg",
        "voiceover_text": narration, "image_description": description,
        "match_terms": [], "semantic_score": 1.0,
    }


def analysis(start=0.0, end=5.0):
    return SceneAnalysis(start, end, 0.65, 0.4, 0.8, 0, 0.0, "right_weighted", "saliency")


def test_guidance_is_deterministic_and_separate_from_semantic_data():
    scenes = [semantic_scene("The hidden evidence was revealed.", "A detailed document.")]
    snapshot = deepcopy(scenes)
    director = VisualDirector()
    first = director.build_motion_guidance(scenes)
    second = director.build_motion_guidance(scenes)
    assert first == second
    assert scenes == snapshot
    assert first[0].preferred_preset == "focus_reveal"
    assert first[0].narrative_intent == "reveal"


def test_story_moments_take_priority_over_composition():
    plans = [SceneVisualPlan(
        scene_index=0, visual_intent="wide", confidence=0.9,
        narrative_intent="climax", narrative_confidence=0.9,
    )]
    guidance = build_motion_guidance(plans)[0]
    assert guidance.preferred_preset == "focus_reveal"
    assert guidance.reason == "Narrative intent selected focus_reveal."


def test_reflection_adds_a_settle_hold():
    guidance = build_motion_guidance([SceneVisualPlan(
        scene_index=0, visual_intent="medium", narrative_intent="reflection",
    )])[0]
    assert guidance.preferred_preset == "slow_pull_out"
    assert guidance.hold_fraction == 0.12


def test_motion_planner_consumes_guidance_but_preserves_timeline_and_focus():
    guidance = build_motion_guidance([SceneVisualPlan(
        scene_index=0, visual_intent="map", confidence=0.8,
        narrative_intent="development", narrative_confidence=0.7,
    )])
    plan = build_motion_plan([analysis()], {"seed": 300}, guidance)[0]
    assert (plan.start, plan.end) == (0.0, 5.0)
    assert (plan.focus_x, plan.focus_y) == (0.65, 0.4)
    assert plan.preset == "focus_reveal"
    assert plan.visual_intent == "map"
    assert plan.narrative_intent == "development"


def test_count_and_order_drift_fail_closed():
    try:
        build_motion_plan([analysis()], {}, [])
    except ValueError as exc:
        assert "counts differ" in str(exc)
    else:
        raise AssertionError("Count drift must fail closed")

    try:
        build_motion_guidance([SceneVisualPlan(scene_index=1)])
    except ValueError as exc:
        assert "indices" in str(exc)
    else:
        raise AssertionError("Index drift must fail closed")


def test_legacy_motion_planning_remains_available_without_guidance():
    plan = build_motion_plan([analysis()], {"seed": 300})[0]
    assert plan.guidance_reason == "Motion Engine fallback."
    assert plan.start == 0.0 and plan.end == 5.0
