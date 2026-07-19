from types import SimpleNamespace
from engine.transition import build_transition_boundaries, smooth_alpha, validate_transition_contract


def test_boundaries_are_frame_exact():
    scenes = [{"start":0.0,"end":2.0},{"start":2.0,"end":5.0},{"start":5.0,"end":5.5}]
    boundaries = build_transition_boundaries(scenes, 30.0, 0.65, 0.20)
    assert [b.frame for b in boundaries] == [60, 150]
    assert boundaries[0].duration_frames == 12
    assert boundaries[1].duration_frames == 3


def test_contract_rejects_drift():
    scenes = [{"start":0.0,"end":2.0},{"start":2.0,"end":4.0}]
    plans = [SimpleNamespace(start=0.0,end=2.0), SimpleNamespace(start=2.05,end=4.0)]
    try:
        validate_transition_contract(scenes, plans, 30.0)
    except RuntimeError:
        pass
    else:
        raise AssertionError("drift should fail closed")


def test_easing_endpoints():
    assert smooth_alpha(0.0) == 0.0
    assert smooth_alpha(1.0) == 1.0
