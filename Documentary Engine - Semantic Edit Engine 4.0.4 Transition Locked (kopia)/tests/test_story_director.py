import json
from copy import deepcopy

import pytest

from engine.story_director import StoryDirector, StoryDirectorError, write_story_director_plan
from engine.story_director.director import serialize_story_plan


def scenes():
    texts = [
        "What happened inside the hospital? This is where the investigation begins.",
        "According to records, the evidence showed a repeated pattern.",
        "However, experts challenged the claim because the data was incomplete.",
        "Years later, the truth was revealed in new documents.",
        "Ultimately, the result remains disputed. Was justice served?",
    ]
    return [{
        "start": index * 5.0, "end": (index + 1) * 5.0, "duration": 5.0,
        "image": f"image_{index}.jpg", "voiceover_text": text,
        "match_terms": ["hospital", "evidence"] if index < 2 else [],
    } for index, text in enumerate(texts)]


def semantic():
    return {"duration": 25.0, "scenes": scenes()}


def captions():
    return {"captions": [{
        "id": index + 1, "start": index * 5.0 + 0.1, "end": index * 5.0 + 4.8,
        "text": scene["voiceover_text"],
    } for index, scene in enumerate(scenes())]}


def config():
    return {"story_director": {"enabled": True, "plan_json": "output/story_director_plan.json"}}


def build():
    return StoryDirector().build_plan(semantic(), captions(), config())


def test_one_decision_per_scene_and_document_analysis():
    plan = build()
    assert plan["status"] == "planned"
    assert plan["scene_count"] == len(scenes())
    assert len(plan["scenes"]) == len(scenes())
    assert plan["document_story"]["story_shape"] == "contested_resolution"
    assert plan["document_story"]["opening_strategy"] == "direct_question"
    assert plan["document_story"]["central_question"].endswith("?")
    assert plan["document_story"]["resolution_type"] == "open_question"
    turning = plan["document_story"]["turning_point_scene"]
    climax = plan["document_story"]["climax_scene"]
    assert plan["scenes"][turning]["story_role"] == "turning_point"
    assert plan["scenes"][climax]["story_role"] == "climax"


def test_graph_preserves_existing_adjacent_scene_order():
    edges = build()["story_graph"]["edges"]
    assert len(edges) == len(scenes()) - 1
    assert [(edge["from_scene"], edge["to_scene"]) for edge in edges] == [
        (f"scene_{index:03d}", f"scene_{index + 1:03d}")
        for index in range(1, len(scenes()))
    ]
    assert all(0.0 <= edge["strength"] <= 1.0 for edge in edges)


def test_all_numeric_values_are_bounded_and_schema_is_valid():
    plan = build()
    StoryDirector.validate_schema(plan, len(scenes()))
    for scene in plan["scenes"]:
        for key in ("emotional_intensity", "tension", "information_density", "revelation_strength", "confidence"):
            assert 0.0 <= scene[key] <= 1.0
    document = plan["document_story"]
    assert 0.0 <= document["story_coherence_score"] <= 1.0
    assert 0.0 <= document["confidence"] <= 1.0
    assert all(0.0 <= value <= 1.0 for value in document["overall_tension_curve"])


def test_identical_inputs_produce_byte_identical_json():
    first = serialize_story_plan(build()).encode("utf-8")
    second = serialize_story_plan(build()).encode("utf-8")
    assert first == second


def test_all_existing_creative_payloads_remain_unchanged():
    semantic_payload, caption_payload, cfg = semantic(), captions(), config()
    motion = {"plans": [{"preset": "slow_pull_out", "start": 0.0, "end": 5.0}]}
    pacing = {"plans": [{"pacing_profile": "context_calm"}]}
    transitions = {"duration": 0.65, "enabled": True}
    snapshots = deepcopy((semantic_payload, caption_payload, cfg, motion, pacing, transitions))

    StoryDirector().build_plan(semantic_payload, caption_payload, cfg)

    assert (semantic_payload, caption_payload, cfg, motion, pacing, transitions) == snapshots
    assert [scene["image"] for scene in semantic_payload["scenes"]] == [f"image_{i}.jpg" for i in range(5)]
    assert [(scene["start"], scene["end"]) for scene in semantic_payload["scenes"]] == [(i * 5.0, (i + 1) * 5.0) for i in range(5)]
    assert [scene["voiceover_text"] for scene in semantic_payload["scenes"]] == [scene["voiceover_text"] for scene in scenes()]


def test_file_writer_changes_only_story_output(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    semantic_path = output / "semantic_edit_plan.json"
    captions_path = output / "captions.json"
    semantic_path.write_text(json.dumps(semantic()), encoding="utf-8")
    captions_path.write_text(json.dumps(captions()), encoding="utf-8")
    cfg = config()
    cfg["semantic_edit_engine"] = {"plan_json": "output/semantic_edit_plan.json"}
    cfg["captions_json"] = "output/captions.json"
    before = (semantic_path.read_bytes(), captions_path.read_bytes())

    target = write_story_director_plan(tmp_path, cfg)

    assert target.name == "story_director_plan.json"
    assert (semantic_path.read_bytes(), captions_path.read_bytes()) == before
    assert json.loads(target.read_text())["scene_count"] == 5


def test_missing_story_metadata_uses_one_fallback_per_known_scene():
    payload = {"scenes": [
        {"start": 0.0, "end": 2.0, "image": "a.jpg"},
        {"start": 2.0, "end": 4.0, "image": "b.jpg"},
    ]}
    plan = StoryDirector().build_plan(payload, {}, {})
    assert plan["status"] == "fallback"
    assert len(plan["scenes"]) == 2
    assert all(scene["fallback_used"] for scene in plan["scenes"])
    assert all(scene["confidence"] == 0.25 for scene in plan["scenes"])


def test_invalid_required_file_writes_valid_fallback(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    (output / "semantic_edit_plan.json").write_text("not json", encoding="utf-8")
    cfg = config()
    cfg["semantic_edit_engine"] = {"plan_json": "output/semantic_edit_plan.json"}
    cfg["captions_json"] = "output/missing_captions.json"
    path = write_story_director_plan(tmp_path, cfg)
    plan = json.loads(path.read_text())
    assert plan["status"] == "fallback"
    assert plan["scene_count"] == 0
    assert plan["diagnostics"]["warning_count"] == 1


def test_empty_project_is_valid_and_single_scene_is_supported():
    empty = StoryDirector().build_plan({"scenes": []}, {}, {})
    assert empty["scene_count"] == 0
    assert empty["story_graph"]["edges"] == []
    assert empty["document_story"]["fallback_used"] is True

    one = StoryDirector().build_plan({"scenes": [{
        "start": 0.0, "end": 3.0, "image": "only.jpg",
        "voiceover_text": "Why did this happen?",
    }]}, {}, {})
    assert one["scene_count"] == 1
    assert one["scenes"][0]["story_role"] == "hook"
    assert one["story_graph"]["edges"] == []


@pytest.mark.parametrize("story,expected", [
    (["What happened?", "Evidence was discovered.", "The truth was revealed."], "mystery_reveal"),
    (["The old account said one thing.", "However, records showed another.", "Yet the claim remained disputed."], "contrast"),
    (["The process began.", "Because pressure increased, it failed.", "Therefore the problem was solved finally."], "problem_solution"),
])
def test_generic_stories_are_classified_without_project_specific_text(story, expected):
    payload = {"scenes": [{
        "start": index * 2.0, "end": (index + 1) * 2.0,
        "image": f"generic_{index}.jpg", "voiceover_text": text,
    } for index, text in enumerate(story)]}
    assert StoryDirector().build_plan(payload, {}, {})["document_story"]["story_shape"] == expected


def test_invalid_scene_timing_is_rejected_by_pure_api():
    with pytest.raises(StoryDirectorError):
        StoryDirector().build_plan({"scenes": [{"start": 3.0, "end": 2.0}]}, {}, {})


def test_broken_story_director_does_not_escape_pipeline_boundary(monkeypatch):
    import engine.pipeline as pipeline

    def broken(*args, **kwargs):
        raise OSError("simulated story output failure")

    monkeypatch.setattr(pipeline, "write_story_director_plan", broken)
    assert pipeline.run_story_director_fail_safe(__import__("pathlib").Path("."), {}) is None


def test_module_has_no_forbidden_imports_or_project_specific_rules():
    import engine.story_director.director as module

    source = module.__loader__.get_source(module.__name__)
    assert source is not None
    forbidden = (
        "engine.motion", "engine.pacing", "engine.visual_director", "engine.image_intelligence",
        "engine.caption_director", "ffmpeg", "subprocess", "requests", "urllib", "openai", "korea",
    )
    assert all(name not in source.casefold() for name in forbidden)
