import json
import re
from pathlib import Path

from engine.audio_director import (
    AudioDirector,
    serialize_audio_artifact,
    write_audio_director_outputs,
)
from engine.audio_director.models import PLANNER_VERSION, SCHEMA_VERSION


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "AUDIO_DIRECTOR.md"


def documented_semantic_fixture():
    return {"scenes": [
        {
            "scene_id": "scene_001", "start": 0.0, "end": 5.0, "duration": 5.0,
            "voiceover_text": "Calm quiet steady context.", "narrative_intent": "context",
        },
        {
            "scene_id": "scene_002", "start": 5.0, "end": 10.0, "duration": 5.0,
            "voiceover_text": "Hope recovery future possible.", "narrative_intent": "conclusion",
        },
    ]}


def extract_example(marker):
    text = DOC.read_text(encoding="utf-8")
    match = re.search(
        rf"<!-- {re.escape(marker)} -->\s*```json\s*(.*?)\s*```",
        text,
        flags=re.DOTALL,
    )
    assert match is not None
    return json.loads(match.group(1))


def test_documented_json_examples_equal_current_validated_models():
    artifacts = AudioDirector().build_audio_artifacts(documented_semantic_fixture())
    artifacts.plan.validate()
    artifacts.diagnostics.validate()
    assert extract_example("audio-plan-example") == json.loads(serialize_audio_artifact(artifacts.plan))
    assert extract_example("audio-diagnostics-example") == json.loads(serialize_audio_artifact(artifacts.diagnostics))
    serialize_audio_artifact(artifacts.plan)
    serialize_audio_artifact(artifacts.diagnostics)


def test_documentation_and_public_models_identify_release_versions():
    text = DOC.read_text(encoding="utf-8")
    assert PLANNER_VERSION == "4.7.0" and SCHEMA_VERSION == "1.0"
    assert "Audio Director 4.7.0" in text
    assert '"schema_version": "1.0"' in text
    assert '"planner_version": "4.7.0"' in text


def test_documentation_states_metadata_only_boundaries_and_path_policy():
    text = DOC.read_text(encoding="utf-8").casefold()
    for phrase in (
        "metadata-only", "does not affect rendered", "rollback-protected",
        "relative file path within project root", "does not download or generate music",
    ):
        assert phrase in text


def test_isolated_writer_smoke_is_deterministic_and_creates_no_media(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    semantic = documented_semantic_fixture()
    story = {
        "schema_version": "4.6.0", "story_director_version": "4.6.0",
        "scene_count": 2,
        "scenes": [
            {
                "scene_id": scene["scene_id"], "scene_index": index,
                "story_role": "context", "story_phase": "middle",
                "tension": 0.2, "emotional_intensity": 0.3,
                "revelation_strength": 0.1,
            }
            for index, scene in enumerate(semantic["scenes"])
        ],
    }
    (output / "semantic_edit_plan.json").write_text(json.dumps(semantic), encoding="utf-8")
    (output / "story_director_plan.json").write_text(json.dumps(story), encoding="utf-8")
    config = {
        "semantic_edit_engine": {"plan_json": "output/semantic_edit_plan.json"},
        "story_director": {"plan_json": "output/story_director_plan.json"},
        "audio_director": {"enabled": True},
    }
    first = write_audio_director_outputs(tmp_path, config)
    before = (first.plan_path.read_bytes(), first.diagnostics_path.read_bytes())
    second = write_audio_director_outputs(tmp_path, config)
    assert before == (second.plan_path.read_bytes(), second.diagnostics_path.read_bytes())
    assert json.loads(second.plan_path.read_text())["planner_version"] == "4.7.0"
    assert json.loads(second.diagnostics_path.read_text())["schema_version"] == "1.0"
    media_suffixes = {".mp4", ".mov", ".wav", ".mp3", ".aac", ".m4a"}
    assert not any(path.suffix.casefold() in media_suffixes for path in tmp_path.rglob("*"))


def test_roadmap_marks_audio_complete_and_cache_storage_next():
    roadmap = (ROOT / "ROADMAP.md").read_text(encoding="utf-8")
    completed = roadmap.split("## Completed", 1)[1].split("## Current", 1)[0]
    current = roadmap.split("## Current", 1)[1].split("## Planned", 1)[0]
    assert "Audio Director 4.7.0" in completed
    assert "Cache and Storage Manager" in current
