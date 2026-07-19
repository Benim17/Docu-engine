import json
from pathlib import Path

import pytest

from engine.audio_director import (
    AudioContractError,
    AudioDirector,
    AudioDirectorIOError,
    parse_audio_director_settings,
    serialize_audio_artifact,
    write_audio_director_outputs,
)


def semantic_payload(count=1):
    return {"scenes": [{
        "scene_id": f"scene_{index + 1:03d}",
        "start": index * 5.0,
        "end": (index + 1) * 5.0,
        "duration": 5.0,
        "voiceover_text": "Useful factual narration.",
        "narrative_intent": "context",
    } for index in range(count)]}


def story_payload(semantic):
    return {
        "schema_version": "4.6.0",
        "story_director_version": "4.6.0",
        "scene_count": len(semantic["scenes"]),
        "scenes": [{
            "scene_id": scene["scene_id"],
            "scene_index": index,
            "story_role": "context",
            "story_phase": "middle",
            "tension": 0.2,
            "emotional_intensity": 0.3,
            "revelation_strength": 0.1,
        } for index, scene in enumerate(semantic["scenes"])],
    }


def config(**audio_changes):
    audio = {
        "enabled": True,
        "plan_json": "output/audio_plan.json",
        "diagnostics_json": "output/audio_diagnostics.json",
        "target_loudness_lufs": -14.0,
        "max_energy_delta": 0.30,
        "allow_aggressive_transitions": True,
    }
    audio.update(audio_changes)
    return {
        "semantic_edit_engine": {"plan_json": "output/semantic_edit_plan.json"},
        "story_director": {"plan_json": "output/story_director_plan.json"},
        "captions_json": "output/captions.json",
        "motion_engine": {"plan_json": "output/motion_plan.json"},
        "audio_director": audio,
    }


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def prepared_root(tmp_path, *, story=True, captions=True, motion=True):
    semantic = semantic_payload()
    write_json(tmp_path / "output/semantic_edit_plan.json", semantic)
    if story:
        write_json(tmp_path / "output/story_director_plan.json", story_payload(semantic))
    if captions:
        write_json(tmp_path / "output/captions.json", {"captions": []})
    if motion:
        write_json(tmp_path / "output/motion_plan.json", {"plans": [{
            "scene_id": "scene_001", "scene_index": 0,
            "start": 0.0, "end": 5.0, "duration": 5.0,
        }]})
    return semantic


def test_public_writer_creates_both_exact_model_artifacts(tmp_path):
    prepared_root(tmp_path)
    result = write_audio_director_outputs(tmp_path, config())
    assert result is not None
    assert result.plan_path.name == "audio_plan.json"
    assert result.diagnostics_path.name == "audio_diagnostics.json"
    assert result.plan_path.read_text() == serialize_audio_artifact(result.plan)
    assert result.diagnostics_path.read_text() == serialize_audio_artifact(result.diagnostics)


def test_repeated_writer_runs_are_byte_identical(tmp_path):
    prepared_root(tmp_path)
    first = write_audio_director_outputs(tmp_path, config())
    before = (first.plan_path.read_bytes(), first.diagnostics_path.read_bytes())
    second = write_audio_director_outputs(tmp_path, config())
    assert before == (second.plan_path.read_bytes(), second.diagnostics_path.read_bytes())


@pytest.mark.parametrize("semantic_state", ["missing", "invalid_json", "not_object", "bad_contract"])
def test_required_semantic_input_fails_closed_without_outputs(tmp_path, semantic_state):
    path = tmp_path / "output/semantic_edit_plan.json"
    path.parent.mkdir(parents=True)
    if semantic_state == "invalid_json":
        path.write_text("{", encoding="utf-8")
    elif semantic_state == "not_object":
        path.write_text("[]", encoding="utf-8")
    elif semantic_state == "bad_contract":
        write_json(path, {"scenes": [{"start": 5, "end": 2}]})
    with pytest.raises((AudioDirectorIOError, AudioContractError)):
        write_audio_director_outputs(tmp_path, config())
    assert not (tmp_path / "output/audio_plan.json").exists()
    assert not (tmp_path / "output/audio_diagnostics.json").exists()


@pytest.mark.parametrize(("name", "content", "warning"), [
    ("story_director_plan.json", "{", "story_director_optional_input_unreadable"),
    ("captions.json", "{", "captions_optional_input_unreadable"),
    ("motion_plan.json", "{", "motion_plan_optional_input_unreadable"),
])
def test_corrupt_optional_input_is_ignored_with_reason(tmp_path, name, content, warning):
    prepared_root(tmp_path)
    (tmp_path / "output" / name).write_text(content, encoding="utf-8")
    result = write_audio_director_outputs(tmp_path, config())
    assert warning in result.diagnostics.project.warnings


def test_missing_optional_inputs_are_diagnostic_not_fatal(tmp_path):
    prepared_root(tmp_path, story=False, captions=False, motion=False)
    result = write_audio_director_outputs(tmp_path, config())
    warnings = result.diagnostics.project.warnings
    assert "story_director_optional_input_missing" in warnings
    assert "captions_optional_input_missing" in warnings
    assert "motion_plan_optional_input_missing" in warnings


def test_incompatible_story_and_stale_motion_are_ignored(tmp_path):
    semantic = prepared_root(tmp_path)
    story = story_payload(semantic)
    story["story_director_version"] = "9.0.0"
    write_json(tmp_path / "output/story_director_plan.json", story)
    write_json(tmp_path / "output/motion_plan.json", {"plans": []})
    result = write_audio_director_outputs(tmp_path, config())
    warnings = result.diagnostics.project.warnings
    assert any(item.startswith("story_director_version_incompatible:") for item in warnings)
    assert "motion_plan_scene_count_mismatch" in warnings


def test_compatible_motion_is_read_only_and_has_no_warning(tmp_path):
    prepared_root(tmp_path)
    motion_path = tmp_path / "output/motion_plan.json"
    before = motion_path.read_bytes()
    result = write_audio_director_outputs(tmp_path, config())
    assert not any(item.startswith("motion_plan_") for item in result.input_warnings)
    assert motion_path.read_bytes() == before


@pytest.mark.parametrize(("changes", "valid"), [
    ({}, True),
    ({"enabled": False}, True),
    ({"enabled": "true"}, False),
    ({"allow_aggressive_transitions": "false"}, False),
    ({"target_loudness_lufs": -40.0}, True),
    ({"target_loudness_lufs": -4.9}, False),
    ({"target_loudness_lufs": "-14"}, False),
    ({"max_energy_delta": 0.01}, True),
    ({"max_energy_delta": 0.46}, False),
    ({"max_energy_delta": 1.01}, False),
])
def test_config_validation(changes, valid):
    cfg = config(**changes)
    if valid:
        parse_audio_director_settings(cfg)
    else:
        with pytest.raises(AudioDirectorIOError):
            parse_audio_director_settings(cfg)


def test_missing_audio_config_uses_backward_compatible_defaults():
    settings = parse_audio_director_settings({})
    assert settings.enabled is True
    assert settings.target_loudness_lufs == -14.0
    assert settings.max_energy_delta == 0.30
    assert settings.allow_aggressive_transitions is True


def test_disabled_writer_is_noop_and_preserves_old_outputs(tmp_path):
    plan = tmp_path / "output/audio_plan.json"
    diagnostics = tmp_path / "output/audio_diagnostics.json"
    plan.parent.mkdir(parents=True)
    plan.write_bytes(b"old-plan")
    diagnostics.write_bytes(b"old-diagnostics")
    assert write_audio_director_outputs(tmp_path, config(enabled=False)) is None
    assert plan.read_bytes() == b"old-plan"
    assert diagnostics.read_bytes() == b"old-diagnostics"


def test_default_config_matches_pure_step_5_output(tmp_path):
    semantic = prepared_root(tmp_path)
    story = story_payload(semantic)
    expected = AudioDirector().build_audio_artifacts(semantic, story)
    actual = write_audio_director_outputs(tmp_path, config())
    assert serialize_audio_artifact(actual.plan) == serialize_audio_artifact(expected.plan)
    assert serialize_audio_artifact(actual.diagnostics) == serialize_audio_artifact(expected.diagnostics)


def test_target_loudness_and_max_delta_are_applied(tmp_path):
    semantic = semantic_payload(2)
    semantic["scenes"][0].update(narrative_intent="reflection", voiceover_text="Calm quiet steady.")
    semantic["scenes"][1].update(narrative_intent="escalation", voiceover_text="Danger threat fear crisis.")
    write_json(tmp_path / "output/semantic_edit_plan.json", semantic)
    result = write_audio_director_outputs(
        tmp_path, config(target_loudness_lufs=-16.0, max_energy_delta=0.1),
    )
    assert result.plan.project_summary.target_loudness_lufs == -16.0
    curve = result.plan.project_summary.energy_curve
    assert curve[1] - curve[0] == pytest.approx(0.1)


def test_aggressive_transitions_can_be_disabled(tmp_path):
    semantic = semantic_payload(2)
    semantic["scenes"][0].update(narrative_intent="reflection", voiceover_text="Calm quiet steady.")
    semantic["scenes"][1].update(voiceover_text="A decisive confrontation.")
    story = story_payload(semantic)
    story["scenes"][1].update(
        story_role="climax", story_phase="climax", tension=1.0,
        emotional_intensity=1.0, revelation_strength=1.0,
    )
    write_json(tmp_path / "output/semantic_edit_plan.json", semantic)
    write_json(tmp_path / "output/story_director_plan.json", story)
    result = write_audio_director_outputs(tmp_path, config(allow_aggressive_transitions=False))
    assert result.plan.scenes[0].transition_out.type == "crossfade"
    assert result.diagnostics.project.unsupported_aggressive_transition_count == 1
    assert "aggressive_transition_disabled_by_config" in result.plan.scenes[1].diagnostics.warnings


@pytest.mark.parametrize("failure_stage", ["write_1", "write_2", "replace_1", "replace_2"])
def test_publish_failures_preserve_old_pair_and_clean_temps(tmp_path, monkeypatch, failure_stage):
    import engine.audio_director.io as module

    prepared_root(tmp_path)
    plan = tmp_path / "output/audio_plan.json"
    diagnostics = tmp_path / "output/audio_diagnostics.json"
    plan.write_bytes(b"old-plan")
    diagnostics.write_bytes(b"old-diagnostics")
    original_write = module._write_temp
    original_replace = module.os.replace
    write_calls = 0
    replace_calls = 0

    def failing_write(*args):
        nonlocal write_calls
        write_calls += 1
        if failure_stage == f"write_{write_calls}":
            raise OSError("simulated temp write failure")
        return original_write(*args)

    def failing_replace(*args):
        nonlocal replace_calls
        replace_calls += 1
        if failure_stage == f"replace_{replace_calls}":
            raise OSError("simulated replace failure")
        return original_replace(*args)

    monkeypatch.setattr(module, "_write_temp", failing_write)
    monkeypatch.setattr(module.os, "replace", failing_replace)
    with pytest.raises(AudioDirectorIOError):
        write_audio_director_outputs(tmp_path, config())
    assert plan.read_bytes() == b"old-plan"
    assert diagnostics.read_bytes() == b"old-diagnostics"
    assert list((tmp_path / "output").glob("*.tmp")) == []
    assert list((tmp_path / "output").glob(".*.tmp")) == []


def test_planning_failure_leaves_existing_complete_pair_untouched(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    plan, diagnostics = output / "audio_plan.json", output / "audio_diagnostics.json"
    plan.write_bytes(b"old-plan")
    diagnostics.write_bytes(b"old-diagnostics")
    with pytest.raises(AudioDirectorIOError):
        write_audio_director_outputs(tmp_path, config())
    assert (plan.read_bytes(), diagnostics.read_bytes()) == (b"old-plan", b"old-diagnostics")


def test_second_replace_without_old_files_removes_published_plan(tmp_path, monkeypatch):
    import engine.audio_director.io as module

    prepared_root(tmp_path)
    original_replace = module.os.replace
    calls = 0

    def fail_second(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("diagnostics replace failed")
        return original_replace(*args)

    monkeypatch.setattr(module.os, "replace", fail_second)
    with pytest.raises(AudioDirectorIOError):
        write_audio_director_outputs(tmp_path, config())
    assert not (tmp_path / "output/audio_plan.json").exists()
    assert not (tmp_path / "output/audio_diagnostics.json").exists()
    assert not list((tmp_path / "output").glob(".*.tmp"))


def test_publish_and_rollback_failure_are_both_reported_and_recovery_is_retained(tmp_path, monkeypatch):
    import engine.audio_director.io as module

    prepared_root(tmp_path)
    plan = tmp_path / "output/audio_plan.json"
    diagnostics = tmp_path / "output/audio_diagnostics.json"
    plan.write_bytes(b"old-plan")
    diagnostics.write_bytes(b"old-diagnostics")
    original_replace = module.os.replace
    calls = 0

    def fail_publish_and_rollback(*args):
        nonlocal calls
        calls += 1
        if calls in {2, 3}:
            raise OSError("simulated failure")
        return original_replace(*args)

    monkeypatch.setattr(module.os, "replace", fail_publish_and_rollback)
    with pytest.raises(AudioDirectorIOError) as caught:
        write_audio_director_outputs(tmp_path, config())
    error = caught.value
    assert error.publish_error is not None
    assert error.rollback_error is not None
    assert "publish failed" in str(error)
    assert "rollback failed" in str(error)
    assert diagnostics.read_bytes() == b"old-diagnostics"
    assert not plan.exists()
    recovery = list((tmp_path / "output").glob(".audio_plan.json.*.tmp"))
    assert len(recovery) == 1
    assert recovery[0].read_bytes() == b"old-plan"


@pytest.mark.parametrize(("plan_path", "diagnostics_path"), [
    ("output/audio_plan.json", "output/audio_diagnostics.json"),
    ("artifacts/audio/audio_plan.json", "artifacts/audio/audio_diagnostics.json"),
])
def test_output_path_policy_accepts_relative_project_paths(tmp_path, plan_path, diagnostics_path):
    prepared_root(tmp_path)
    result = write_audio_director_outputs(
        tmp_path, config(plan_json=plan_path, diagnostics_json=diagnostics_path),
    )
    assert result.plan_path.is_relative_to(tmp_path.resolve())
    assert result.diagnostics_path.is_relative_to(tmp_path.resolve())


@pytest.mark.parametrize("invalid_path", [
    "../audio_plan.json", "../../other_project/file.json", "/tmp/audio_plan.json", "",
])
def test_output_path_policy_rejects_escape_absolute_and_empty_paths(tmp_path, invalid_path):
    prepared_root(tmp_path)
    with pytest.raises(AudioDirectorIOError):
        write_audio_director_outputs(tmp_path, config(plan_json=invalid_path))


def test_output_path_policy_rejects_normalized_duplicate_and_directory(tmp_path):
    prepared_root(tmp_path)
    with pytest.raises(AudioDirectorIOError):
        write_audio_director_outputs(tmp_path, config(
            plan_json="output/../output/audio.json",
            diagnostics_json="output/audio.json",
        ))
    (tmp_path / "artifacts").mkdir()
    with pytest.raises(AudioDirectorIOError):
        write_audio_director_outputs(tmp_path, config(plan_json="artifacts"))


def test_output_path_policy_rejects_symlink_escape(tmp_path):
    prepared_root(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / "linked-output").symlink_to(outside, target_is_directory=True)
    with pytest.raises(AudioDirectorIOError):
        write_audio_director_outputs(tmp_path, config(plan_json="linked-output/audio.json"))


def test_all_upstream_files_remain_byte_unchanged(tmp_path):
    prepared_root(tmp_path)
    paths = [
        tmp_path / "output/semantic_edit_plan.json",
        tmp_path / "output/story_director_plan.json",
        tmp_path / "output/captions.json",
        tmp_path / "output/motion_plan.json",
    ]
    before = {path: path.read_bytes() for path in paths}
    write_audio_director_outputs(tmp_path, config())
    assert {path: path.read_bytes() for path in paths} == before


def test_pipeline_fail_safe_catches_writer_error(monkeypatch, capsys):
    import engine.pipeline as pipeline

    monkeypatch.setattr(pipeline, "write_audio_director_outputs", lambda *_: (_ for _ in ()).throw(OSError("broken")))
    assert pipeline.run_audio_director_fail_safe(Path("."), {}) is None
    assert "Audio Director 4.7.0: fail-closed" in capsys.readouterr().out


def test_pipeline_warning_sanitizes_paths_narration_and_json(monkeypatch, capsys):
    import engine.pipeline as pipeline

    secret = '/Users/private/person/project {"narration":"sensitive words"}'
    monkeypatch.setattr(
        pipeline, "write_audio_director_outputs",
        lambda *_: (_ for _ in ()).throw(AudioDirectorIOError(secret)),
    )
    pipeline.run_audio_director_fail_safe(Path("."), {})
    output = capsys.readouterr().out
    assert "Audio Director 4.7.0: fail-closed" in output
    assert "/Users/" not in output
    assert "sensitive words" not in output
    assert "narration" not in output


def test_target_loudness_changes_only_project_metadata():
    semantic = semantic_payload(2)
    default = AudioDirector().build_audio_artifacts(semantic)
    changed = AudioDirector(target_loudness_lufs=-16.0).build_audio_artifacts(semantic)
    assert default.plan.scenes == changed.plan.scenes
    assert default.diagnostics == changed.diagnostics
    assert default.plan.project_summary.energy_curve == changed.plan.project_summary.energy_curve
    assert default.plan.project_summary.target_loudness_lufs == -14.0
    assert changed.plan.project_summary.target_loudness_lufs == -16.0


def test_pipeline_orders_story_audio_then_render_without_passing_audio(monkeypatch, tmp_path):
    import engine.pipeline as pipeline

    events = []
    video = tmp_path / "input.mp4"
    video.write_bytes(b"video")
    cfg = {
        "input_video": "input.mp4", "captions_json": "captions.json",
        "captions_srt": "captions.srt", "output_video": "documentary.mp4",
        "caption_director": {"enabled": False},
        "story_director": {"enabled": True}, "audio_director": {"enabled": True},
    }
    monkeypatch.setattr(pipeline, "ROOT", tmp_path)
    monkeypatch.setattr(pipeline, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(pipeline, "require", lambda *_: None)
    monkeypatch.setattr(pipeline, "load_json", lambda *_: cfg)
    monkeypatch.setattr(pipeline, "build_caption_core", lambda *_: events.append("captions"))
    monkeypatch.setattr(pipeline, "build_semantic_edit", lambda *_: video)
    monkeypatch.setattr(pipeline, "run_story_director_fail_safe", lambda *_: events.append("story"))
    monkeypatch.setattr(pipeline, "run_audio_director_fail_safe", lambda *_: events.append("audio"))
    monkeypatch.setattr(pipeline, "render_video", lambda *args: events.append(("render", len(args))))
    pipeline.main()
    assert events == ["captions", "story", "audio", ("render", 4)]


def test_audio_director_import_boundary_has_no_media_or_network_dependencies():
    import engine.audio_director.io as io_module
    import engine.audio_director.sound_planning as planning_module

    source = (io_module.__loader__.get_source(io_module.__name__) or "") + (
        planning_module.__loader__.get_source(planning_module.__name__) or ""
    )
    forbidden = (
        "import ffmpeg", "import renderer", "import requests", "from requests",
        "import urllib", "from urllib", "import openai", "from openai", "import subprocess",
    )
    assert all(value not in source.casefold() for value in forbidden)
