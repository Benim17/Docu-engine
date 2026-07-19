from copy import deepcopy

from PIL import Image, ImageDraw

from engine.image_intelligence import ImageIntelligence
from engine.semantic.editor import Beat, assign_images
from engine.visual_director import VisualDirector


def make_image(path, color=(90, 110, 130), subject=True):
    image = Image.new("RGB", (720, 1280), color)
    if subject:
        draw = ImageDraw.Draw(image)
        draw.ellipse((270, 360, 450, 700), fill=(220, 205, 185))
        draw.rectangle((230, 700, 490, 1100), fill=(50, 65, 80))
    image.save(path, quality=92)
    return path


def candidates(tmp_path):
    first = make_image(tmp_path / "alpha.jpg")
    second = make_image(tmp_path / "beta.jpg", (55, 65, 75))
    return [
        (first, {"file": first.name, "description": "Real hospital evidence photograph", "tags": ["hospital", "evidence"]}),
        (second, {"file": second.name, "description": "Real courtroom archive photograph", "tags": ["courtroom", "archive"]}),
    ]


def test_ranking_scoring_reasoning_and_output_are_deterministic(tmp_path):
    engine = ImageIntelligence()
    items = candidates(tmp_path)
    first = engine.rank_scene(0, 1, "Hospital evidence was reviewed.", items)
    second = engine.rank_scene(0, 1, "Hospital evidence was reviewed.", items)

    assert first == second
    assert first.to_dict() == second.to_dict()
    assert [item.rank for item in first.candidate_ranking] == [1, 2]
    assert all(item.reasoning for item in first.candidate_ranking)
    assert first.selection_reasoning.startswith("Selected ")


def test_candidate_input_order_does_not_change_ranking(tmp_path):
    engine = ImageIntelligence()
    items = candidates(tmp_path)
    forward = engine.rank_scene(0, 1, "Hospital evidence", items)
    reverse = engine.rank_scene(0, 1, "Hospital evidence", list(reversed(items)))
    assert forward == reverse


def test_ties_use_filename_not_input_order(tmp_path):
    first = make_image(tmp_path / "a.jpg", subject=False)
    second = make_image(tmp_path / "b.jpg", subject=False)
    profile_a = {"file": "a.jpg", "description": "same"}
    profile_b = {"file": "b.jpg", "description": "same"}
    engine = ImageIntelligence()

    decision = engine.rank_scene(0, 1, "unmatched", [(second, profile_b), (first, profile_a)])
    assert decision.selected_image == "a.jpg"
    assert [item.image for item in decision.candidate_ranking] == ["a.jpg", "b.jpg"]


def test_embedded_text_lowers_caption_compatibility(tmp_path):
    first = make_image(tmp_path / "clean.jpg")
    second = make_image(tmp_path / "title.jpg")
    common = {"description": "Real documentary evidence"}
    decision = ImageIntelligence().rank_scene(0, 1, "evidence", [
        (first, {"file": "clean.jpg", **common}),
        (second, {"file": "title.jpg", **common, "ocr": ["LARGE VISIBLE TITLE"]}),
    ])
    values = {item.image: item.caption_compatibility for item in decision.candidate_ranking}
    assert values["clean.jpg"] > values["title.jpg"]


def test_assign_images_changes_only_image_owned_fields_and_not_semantics(tmp_path):
    items = candidates(tmp_path)
    images = [path for path, _ in items]
    profiles = {path.name: deepcopy(profile) for path, profile in items}
    beats = [Beat(0.0, 4.5, "Hospital evidence."), Beat(4.5, 9.0, "Courtroom archive.")]
    snapshot = deepcopy(beats)

    scenes, diagnostics = assign_images(beats, images, profiles)

    assert beats == snapshot
    assert [(scene["start"], scene["end"], scene["duration"]) for scene in scenes] == [
        (0.0, 4.5, 4.5), (4.5, 9.0, 4.5),
    ]
    assert [scene["voiceover_text"] for scene in scenes] == [beat.text for beat in beats]
    assert len(diagnostics) == len(scenes)

    for scene, decision in zip(scenes, diagnostics):
        reference = deepcopy(scene)
        reference["image"] = decision["semantic_reference_image"]
        assert {key: value for key, value in scene.items() if key != "image"} == {
            key: value for key, value in reference.items() if key != "image"
        }
        assert VisualDirector().build_motion_guidance([scene]) == VisualDirector().build_motion_guidance([reference])


def test_module_has_no_downstream_dependencies():
    import engine.image_intelligence.intelligence as module

    source = module.__loader__.get_source(module.__name__)
    forbidden = ("visual_director", "motion", "pacing", "transition")
    assert source is not None
    assert all(f"engine.{name}" not in source for name in forbidden)
