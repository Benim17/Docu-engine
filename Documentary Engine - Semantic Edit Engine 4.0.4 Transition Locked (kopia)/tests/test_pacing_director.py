from copy import deepcopy

import pytest

from engine.motion.analyzer import SceneAnalysis
from engine.motion.planner import build_motion_plan, motion_state
from engine.pacing import PacingDirector, pacing_progress
from engine.visual_director import SceneVisualPlan, build_motion_guidance


def analysis(start=0.0, end=5.0):
    return SceneAnalysis(start, end, 0.65, 0.4, 0.8, 0, 0.0, "right_weighted", "saliency")


def paced_plan(intent="development", start=0.0, end=5.0):
    guidance = build_motion_guidance([SceneVisualPlan(
        scene_index=0, visual_intent="map", confidence=0.8,
        narrative_intent=intent, narrative_confidence=0.7,
    )])
    plan = build_motion_plan([analysis(start, end)], {"seed": 300}, guidance)
    return PacingDirector().apply(plan)[0]


def test_pacing_is_deterministic():
    director = PacingDirector()
    first = director.apply([paced_plan("context")])
    second = director.apply([paced_plan("context")])
    assert first == second
    assert first[0].pacing_profile == "context_calm"


@pytest.mark.parametrize("intent,profile", [
    ("context", "context_calm"),
    ("introduction", "introduction_smooth"),
    ("development", "development_dynamic"),
    ("conclusion", "conclusion_settled"),
])
def test_narrative_profiles_are_fixed(intent, profile):
    plan = paced_plan(intent)
    assert plan.pacing_profile == profile
    assert sum((plan.hold_fraction, plan.ease_in_fraction, plan.peak_fraction,
                plan.ease_out_fraction, plan.settle_fraction)) == pytest.approx(1.0)


def test_pacing_changes_no_timing_or_camera_destination():
    guidance = build_motion_guidance([SceneVisualPlan(
        scene_index=0, visual_intent="portrait", narrative_intent="conclusion",
    )])
    original = build_motion_plan([analysis(1.25, 8.75)], {"seed": 300}, guidance)[0]
    paced = PacingDirector().apply([original])[0]
    protected = (
        "start", "end", "start_zoom", "end_zoom", "start_x", "start_y",
        "end_x", "end_y", "preset", "confidence", "focus_x", "focus_y", "visual_intent",
        "narrative_intent", "guidance_reason",
    )
    assert {name: getattr(paced, name) for name in protected} == {
        name: getattr(original, name) for name in protected
    }
    assert paced.end - paced.start == original.end - original.start


def test_semantic_and_caption_payloads_are_not_modified():
    semantic = [{"start": 0.0, "end": 5.0, "image": "a.jpg", "caption": "semantic"}]
    captions = [{"start": 0.1, "end": 1.2, "text": "UNCHANGED"}]
    semantic_snapshot, caption_snapshot = deepcopy(semantic), deepcopy(captions)
    PacingDirector().apply([paced_plan("introduction")])
    assert semantic == semantic_snapshot
    assert captions == caption_snapshot


def test_interpolation_is_reproducible_and_settles_at_destination():
    plan = paced_plan("conclusion")
    samples_a = [motion_state([plan], i / 30.0, 0)[:3] for i in range(151)]
    samples_b = [motion_state([plan], i / 30.0, 0)[:3] for i in range(151)]
    assert samples_a == samples_b
    assert samples_a[0] == pytest.approx((plan.start_zoom, plan.start_x, plan.start_y))
    assert samples_a[-1] == pytest.approx((plan.end_zoom, plan.end_x, plan.end_y))
    assert samples_a[-20] == pytest.approx(samples_a[-1])


def test_pacing_progress_has_gentle_endpoints_and_monotonic_motion():
    plan = paced_plan("development")
    values = [pacing_progress(i / 1000.0, plan) for i in range(1001)]
    assert values == sorted(values)
    assert values[0] == 0.0 and values[-1] == 1.0
    assert values[1] - values[0] == pytest.approx(0.0, abs=1e-8)
    assert values[-1] - values[-2] == pytest.approx(0.0, abs=1e-8)
