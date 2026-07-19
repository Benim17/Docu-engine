from copy import deepcopy

from engine.visual_director import SUPPORTED_SHOT_TYPES, VisualDirector, classify_scene


def scene(narration="", description="", terms=None):
    return {
        "start": 0.0,
        "end": 5.0,
        "duration": 5.0,
        "image": "scene.jpg",
        "voiceover_text": narration,
        "image_description": description,
        "match_terms": terms or [],
        "semantic_score": 1.0,
    }


def test_portrait_classification():
    result = classify_scene(scene(
        "The historical leader addressed the country.",
        "A formal portrait of the president.",
    ))
    assert result.visual_intent == "portrait"
    assert result.confidence > 0.50
    assert "portrait" in result.reason


def test_map_classification():
    result = classify_scene(scene(
        "The route crossed three national borders.",
        "A geographic map of the territory.",
    ))
    assert result.visual_intent == "map"
    assert result.confidence > 0.50


def test_document_classification():
    result = classify_scene(scene(
        "The report was entered into the official record.",
        "A document and handwritten letter on a desk.",
    ))
    assert result.visual_intent == "document"
    assert result.confidence > 0.50


def test_establishing_classification():
    result = classify_scene(scene(
        "The investigation began at this location.",
        "An exterior skyline view of the hospital building.",
    ))
    assert result.visual_intent == "establishing"
    assert result.confidence > 0.50


def test_fallback_classification():
    result = classify_scene(scene("The account continued.", "A neutral composition."))
    assert result.visual_intent == "medium"
    assert result.confidence == 0.50
    assert result.reason == "Default fallback."


def test_classification_is_deterministic():
    semantic_scene = scene(
        "The route appeared in the report.",
        "A document beside a geographic map.",
        {"territory", "record", "atlas"},
    )
    results = [classify_scene(semantic_scene) for _ in range(10)]
    assert all(result == results[0] for result in results)


def test_supported_shot_types_are_complete():
    assert SUPPORTED_SHOT_TYPES == (
        "establishing", "wide", "medium", "portrait",
        "detail", "document", "map", "archive",
    )


def test_visual_plans_do_not_change_semantic_scenes_or_schema():
    original = [scene(
        "A historical person gave evidence.",
        "A portrait of a woman.",
        ["person", "portrait"],
    )]
    snapshot = deepcopy(original)
    director = VisualDirector()

    visual_plans = director.build_visual_plan(original)
    directed = director.direct(original)

    assert visual_plans[0].visual_intent == "portrait"
    assert directed == snapshot
    assert original == snapshot
    assert directed is not original
    assert set(directed[0]) == set(snapshot[0])
    assert "visual_intent" not in directed[0]
    assert "confidence" not in directed[0]
    assert "reason" not in directed[0]
