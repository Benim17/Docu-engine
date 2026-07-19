from copy import deepcopy

from engine.visual_director import VisualDirector, VisualDirectorContractError


def sample_scenes():
    return [
        {
            "start": 0.0,
            "end": 4.5,
            "duration": 4.5,
            "image": "opening.jpg",
            "voiceover_text": "Opening scene.",
            "image_description": "An opening image",
            "match_terms": ["opening", "scene"],
            "semantic_score": 4.2,
            "metadata": {"source": "manifest", "labels": ["archive"]},
        },
        {
            "start": 4.5,
            "end": 9.0,
            "duration": 4.5,
            "image": "closing.jpg",
            "voiceover_text": "Closing scene.",
            "image_description": "A closing image",
            "match_terms": ["closing", "scene"],
            "semantic_score": 3.8,
        },
    ]


def assert_contract_rejected(original, mutated):
    try:
        VisualDirector.validate_preservation(original, mutated)
    except VisualDirectorContractError:
        pass
    else:
        raise AssertionError("Visual Director contract should reject mutated scenes")


def test_director_preserves_complete_semantic_scene_data():
    original = sample_scenes()
    directed = VisualDirector().direct(original)

    assert directed == original
    assert len(directed) == len(original)
    assert [scene["image"] for scene in directed] == ["opening.jpg", "closing.jpg"]
    assert [(scene["start"], scene["end"], scene["duration"]) for scene in directed] == [
        (0.0, 4.5, 4.5),
        (4.5, 9.0, 4.5),
    ]
    assert directed[0]["metadata"] == original[0]["metadata"]


def test_director_returns_a_deep_copy():
    original = sample_scenes()
    directed = VisualDirector().direct(original)

    assert directed is not original
    assert directed[0] is not original[0]
    assert directed[0]["match_terms"] is not original[0]["match_terms"]
    assert directed[0]["metadata"] is not original[0]["metadata"]
    assert directed[0]["metadata"]["labels"] is not original[0]["metadata"]["labels"]

    directed[0]["metadata"]["labels"].append("changed")
    assert original[0]["metadata"]["labels"] == ["archive"]


def test_contract_rejects_count_order_image_timing_duration_and_metadata_changes():
    original = sample_scenes()

    assert_contract_rejected(original, original[:-1])
    assert_contract_rejected(original, list(reversed(original)))

    for field, value in (
        ("image", "replacement.jpg"),
        ("start", 0.25),
        ("end", 4.75),
        ("duration", 4.75),
        ("semantic_score", 99.0),
    ):
        mutated = deepcopy(original)
        mutated[0][field] = value
        assert_contract_rejected(original, mutated)

    mutated = deepcopy(original)
    mutated[0]["new_visual_field"] = "must not enter semantic schema"
    assert_contract_rejected(original, mutated)


def test_empty_scene_list_is_safe():
    original = []
    directed = VisualDirector().direct(original)
    assert directed == []
    assert directed is not original
    VisualDirector.validate_preservation([], [])


def test_visual_plan_is_neutral_and_separate_from_semantic_scenes():
    original = sample_scenes()
    visual_plan = VisualDirector().build_visual_plan(original)

    assert [plan.scene_index for plan in visual_plan] == [0, 1]
    assert all(plan.shot_type == "neutral" for plan in visual_plan)
    assert all(plan.emotion == "neutral" for plan in visual_plan)
    assert all(plan.pace == "neutral" for plan in visual_plan)
    assert all(plan.importance == 0.0 for plan in visual_plan)
    assert all(plan.cinematic_intent == "identity" for plan in visual_plan)
    assert all("shot_type" not in scene for scene in original)
