import json
from pathlib import Path

import pytest

import prepare_project
from engine import pipeline


def make_project(root: Path, name: str, *, images=True, audio=True) -> Path:
    project = root / name
    project.mkdir(parents=True)
    if images:
        for filename in ("frame 10.jpg", "frame 2.jpg", "frame 1.jpg"):
            (project / filename).write_bytes(b"image")
    if audio:
        (project / "voice.wav").write_bytes(b"audio")
    return project


def test_explicit_selection_and_natural_numeric_sort(tmp_path):
    project = make_project(tmp_path, "chosen")
    selected, automatic = prepare_project.resolve_project(str(project), tmp_path)
    images, _ = prepare_project.inspect_project(selected)
    assert selected == project.resolve() and automatic is False
    assert [item.name for item in images] == ["frame 1.jpg", "frame 2.jpg", "frame 10.jpg"]


def test_legacy_fallback_selects_latest_valid_project(tmp_path):
    make_project(tmp_path, "older")
    newer = make_project(tmp_path, "newer")
    selected, automatic = prepare_project.resolve_project(None, tmp_path)
    assert selected == newer.resolve() and automatic is True


def test_invalid_project_path_has_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="Projektmappen saknas"):
        prepare_project.resolve_project(str(tmp_path / "missing"), tmp_path)


@pytest.mark.parametrize(("images", "audio", "message"), [(False, True, "saknar bilder"), (True, False, "saknar ljud")])
def test_incomplete_project_has_clear_error(tmp_path, images, audio, message):
    project = make_project(tmp_path, "incomplete", images=images, audio=audio)
    with pytest.raises(ValueError, match=message):
        prepare_project.inspect_project(project)


def test_two_projects_and_runs_have_isolated_paths(tmp_path, monkeypatch):
    source = tmp_path / "source"
    korea = make_project(source, "korea")
    nurse = make_project(source, "sample_project")
    template = tmp_path / "template.json"
    template.write_text(json.dumps({"style": "styles/test.json"}), encoding="utf-8")
    monkeypatch.setattr(prepare_project, "CONFIG", template)
    monkeypatch.setattr(prepare_project, "ROOT", tmp_path)
    configs = []
    for project, run_name in ((korea, "korea_1"), (korea, "korea_2"), (nurse, "nurse_1")):
        run = tmp_path / "runs" / run_name
        config = prepare_project.build_run_config(project, project / "voice.wav", run / "work/source.mp4", run)
        configs.append((run, config))
    assert len({str(run / "config.json") for run, _ in configs}) == 3
    assert len({str(run / "work") for run, _ in configs}) == 3
    assert len({str(run / "output") for run, _ in configs}) == 3
    assert "sample_project" not in json.dumps(configs[0][1])
    assert "korea" not in json.dumps(configs[2][1])


def test_pipeline_runtime_defaults_and_legacy_config(tmp_path):
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    resolved, root = pipeline.configure_runtime(config, None)
    assert resolved == config.resolve() and root == tmp_path.resolve()
    assert pipeline.parse_args([]).config == pipeline.CONFIG_PATH


def test_pipeline_rejects_config_outside_explicit_run_dir(tmp_path):
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="måste ligga direkt"):
        pipeline.configure_runtime(config, tmp_path / "other")


def test_manifest_must_list_each_image_exactly_once(tmp_path, monkeypatch):
    project = make_project(tmp_path, "manifested")
    (project / "image_manifest.json").write_text(json.dumps({
        "schema_version": "1.0",
        "images": [{"file": "frame 1.jpg"}, {"file": "frame 1.jpg"}, {"file": "frame 10.jpg"}],
    }), encoding="utf-8")
    monkeypatch.setattr(prepare_project, "audio_duration", lambda _: 1.0)
    monkeypatch.setattr(prepare_project.Image, "open", lambda _: pytest.fail("images must not open after manifest failure"))
    images, audios = prepare_project.inspect_project(project)
    with pytest.raises(ValueError, match="exakt en gång"):
        prepare_project.validate_inputs(project, images, audios[0])


def test_missing_manifest_preserves_legacy_project_support(tmp_path, monkeypatch):
    project = make_project(tmp_path, "legacy")
    images, audios = prepare_project.inspect_project(project)
    monkeypatch.setattr(prepare_project, "audio_duration", lambda _: 1.0)
    class Opened:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def verify(self): return None
    monkeypatch.setattr(prepare_project.Image, "open", lambda _: Opened())
    assert prepare_project.validate_inputs(project, images, audios[0]) is None
