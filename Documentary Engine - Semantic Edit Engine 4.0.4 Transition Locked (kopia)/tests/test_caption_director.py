import json
from copy import deepcopy

import pytest

from engine.caption_director import CaptionDirector, CaptionDirectorError, write_caption_director_plan


def semantic_payload():
    return {
        "duration": 10.0,
        "scenes": [
            {"start": 0.0, "end": 5.0, "duration": 5.0, "image": "a.jpg",
             "voiceover_text": "The hospital opened in 2023.", "match_terms": ["hospital"]},
            {"start": 5.0, "end": 10.0, "duration": 5.0, "image": "b.jpg",
             "voiceover_text": "The investigation continued.", "match_terms": ["investigation"]},
        ],
    }


def captions_payload():
    return {
        "captions": [
            {"id": 1, "start": 0.2, "end": 2.0, "text": "THE HOSPITAL OPENED IN 2023",
             "words": [{"text": word, "start": 0.2 + index * 0.2, "end": 0.38 + index * 0.2}
                       for index, word in enumerate("THE HOSPITAL OPENED IN 2023".split())]},
            {"id": 2, "start": 5.2, "end": 7.0, "text": "THE INVESTIGATION CONTINUED"},
        ],
    }


def config():
    return {"caption_director": {
        "enabled": True, "default_position": "bottom", "safe_margin": 0.08,
        "max_width": 0.82, "max_lines": 2, "max_characters_per_line": 16,
        "highlight_color": "#FFD54A", "allow_scene_reposition": True,
    }}


def image_plan():
    return {"scenes": [{"scene_index": 0}, {"scene_index": 1}]}


def motion_analysis():
    return {"scenes": [
        {"start": 0.0, "end": 5.0, "focus_x": 0.75, "focus_y": 0.25,
         "face_count": 1, "source": "face"},
        {"start": 5.0, "end": 10.0, "focus_x": 0.5, "focus_y": 0.72,
         "face_count": 0, "source": "saliency"},
    ]}


def test_identical_input_produces_identical_output():
    director = CaptionDirector()
    args = (semantic_payload(), captions_payload(), image_plan(), motion_analysis(), config())
    assert director.build_plan(*args) == director.build_plan(*args)


def test_inputs_caption_text_timing_semantics_and_images_are_unchanged():
    payloads = [semantic_payload(), captions_payload(), image_plan(), motion_analysis(), config()]
    snapshots = deepcopy(payloads)
    CaptionDirector().build_plan(*payloads)
    assert payloads == snapshots
    assert payloads[1]["captions"][0]["text"] == "THE HOSPITAL OPENED IN 2023"
    assert (payloads[1]["captions"][0]["start"], payloads[1]["captions"][0]["end"]) == (0.2, 2.0)
    assert [scene["image"] for scene in payloads[0]["scenes"]] == ["a.jpg", "b.jpg"]


def test_scene_count_schema_face_subject_priority_and_highlights():
    plan = CaptionDirector().build_plan(
        semantic_payload(), captions_payload(), image_plan(), motion_analysis(), config(),
    )
    CaptionDirector.validate_schema(plan, 2)
    assert plan["scene_count"] == plan["source_scene_count"] == 2
    assert plan["source_caption_count"] == 2
    assert plan["scenes"][0]["position"] == "bottom"
    assert plan["scenes"][1]["position"] == "top"
    assert plan["scenes"][0]["highlight_words"] == ["HOSPITAL", "2023"]
    assert plan["scenes"][0]["safe_margin"] == 0.08
    assert plan["scenes"][0]["max_lines"] == 2
    assert plan["scenes"][0]["caption_layouts"][0]["line_word_indices"] == [[0, 1], [2, 3, 4]]
    assert "Face detected" in plan["scenes"][0]["reason"]
    assert "Primary subject detected" in plan["scenes"][1]["reason"]


def test_stable_default_placement_without_reliable_analysis():
    plan = CaptionDirector().build_plan(semantic_payload(), captions_payload(), image_plan(), {}, config())
    assert [scene["position"] for scene in plan["scenes"]] == ["bottom", "bottom"]
    assert all("Placement remains stable" in scene["reason"] for scene in plan["scenes"])


@pytest.mark.parametrize("field,value", [
    ("default_position", "diagonal"),
    ("safe_margin", 0.45),
    ("max_lines", 0),
    ("highlight_color", "yellow"),
])
def test_invalid_layout_config_fails_closed_at_pure_api(field, value):
    invalid = config()
    invalid["caption_director"][field] = value
    with pytest.raises(CaptionDirectorError):
        CaptionDirector().build_plan(semantic_payload(), captions_payload(), {}, {}, invalid)


def test_file_boundary_always_writes_fallback_and_preserves_source_files(tmp_path):
    semantic = semantic_payload()
    captions = captions_payload()
    (tmp_path / "output").mkdir()
    (tmp_path / "output/semantic_edit_plan.json").write_text(json.dumps(semantic), encoding="utf-8")
    (tmp_path / "output/captions.json").write_text(json.dumps(captions), encoding="utf-8")
    invalid = config()
    invalid["captions_json"] = "output/captions.json"
    invalid["semantic_edit_engine"] = {
        "plan_json": "output/semantic_edit_plan.json",
        "image_intelligence_json": "output/missing_image_plan.json",
    }
    invalid["motion_engine"] = {"analysis_json": "output/missing_motion_analysis.json"}
    invalid["caption_director"]["plan_json"] = "output/caption_director_plan.json"
    invalid["caption_director"]["highlight_color"] = "invalid"
    before_semantic = (tmp_path / "output/semantic_edit_plan.json").read_bytes()
    before_captions = (tmp_path / "output/captions.json").read_bytes()

    output = write_caption_director_plan(tmp_path, invalid)
    plan = json.loads(output.read_text(encoding="utf-8"))

    assert plan["status"] == "fallback"
    assert plan["scene_count"] == len(semantic["scenes"])
    assert all(scene["position"] == "bottom" for scene in plan["scenes"])
    assert all(scene["highlight_color"] == "#FFD54A" for scene in plan["scenes"])
    assert (tmp_path / "output/semantic_edit_plan.json").read_bytes() == before_semantic
    assert (tmp_path / "output/captions.json").read_bytes() == before_captions


def test_module_has_no_forbidden_dependencies():
    import engine.caption_director.director as module

    source = module.__loader__.get_source(module.__name__)
    assert source is not None
    forbidden = (
        "engine.motion", "engine.pacing", "engine.visual_director", "engine.semantic",
        "engine.image_intelligence", "ffmpeg", "subprocess", "cv2", "PIL",
    )
    assert all(name not in source for name in forbidden)
