import json
from pathlib import Path

import pytest

from engine.storage import (
    ArtifactCategory,
    ArtifactGroupStatus,
    ArtifactType,
    GitIndexSnapshot,
    GitTrackingStatus,
    InventoryError,
    InventoryRequest,
    ObservationStatus,
    ProtectionClass,
    SymlinkStatus,
    inspect_inventory,
)


def touch(path, content=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def records(report):
    return {record.relative_path: record for record in report.records}


def request(root, **changes):
    values = {"inventory_root": root}
    values.update(changes)
    return InventoryRequest(**values)


def test_empty_root_has_empty_deterministic_report(tmp_path):
    first = inspect_inventory(request(tmp_path))
    second = inspect_inventory(request(tmp_path))
    assert first == second
    assert first.records == () and first.artifact_groups == ()
    assert first.total_size_bytes == first.potentially_reclaimable_bytes == 0
    assert first.total_regular_file_bytes == 0


def test_known_source_artifacts_are_classified_from_project_context(tmp_path):
    project = tmp_path / "project"
    touch(project / "bild 1.jpg", b"image")
    touch(project / "voiceover.wav", b"audio")
    touch(project / "image_manifest.json", b"{}")
    result = records(inspect_inventory(request(tmp_path, project_root=project)))
    assert result["project/bild 1.jpg"].artifact_type is ArtifactType.SOURCE_IMAGE
    assert result["project/voiceover.wav"].artifact_type is ArtifactType.SOURCE_VOICEOVER
    assert result["project/image_manifest.json"].artifact_type is ArtifactType.IMAGE_MANIFEST
    assert all(item.primary_category is ArtifactCategory.SOURCE for item in result.values())
    assert all(item.protection is ProtectionClass.IMMUTABLE_SOURCE for item in result.values())


def test_source_music_and_ambience_are_not_misclassified_as_voiceover(tmp_path):
    project = tmp_path / "project"
    touch(project / "voiceover final.wav")
    touch(project / "music.wav")
    touch(project / "forest ambience.mp3")
    result = records(inspect_inventory(request(tmp_path, project_root=project)))
    assert result["project/voiceover final.wav"].artifact_type is ArtifactType.SOURCE_VOICEOVER
    for name in ("project/music.wav", "project/forest ambience.mp3"):
        assert result[name].artifact_type is ArtifactType.UNKNOWN
        assert result[name].protection is ProtectionClass.PROTECTED_UNKNOWN
        assert not result[name].cleanup_candidate


def test_repository_and_effective_run_configs_are_contextual(tmp_path):
    repository = tmp_path / "repository"
    run = tmp_path / "run"
    touch(repository / "config.json", b"{}")
    touch(repository / "styles" / "tiktok.json", b"{}")
    touch(run / "config.json", b"{}")
    result = records(inspect_inventory(request(
        tmp_path, repository_root=repository, run_root=run,
    ), git_inspector=lambda _: GitIndexSnapshot(
        str(repository), frozenset(), GitTrackingStatus.TRACKED, "Test index"
    )))
    assert result["repository/config.json"].artifact_type is ArtifactType.REPOSITORY_CONFIG
    assert result["repository/styles/tiktok.json"].artifact_type is ArtifactType.STYLE_CONFIG
    assert result["run/config.json"].artifact_type is ArtifactType.EFFECTIVE_RUN_CONFIG
    assert all(item.primary_category is ArtifactCategory.CONFIG for item in result.values())


@pytest.mark.parametrize(("name", "expected"), [
    ("caption_audio_16khz.wav", ArtifactType.CAPTION_AUDIO_NORMALIZED),
    ("source_images.ffconcat", ArtifactType.SOURCE_IMAGES_CONCAT),
    ("semantic_timeline.ffconcat", ArtifactType.SEMANTIC_TIMELINE_CONCAT),
    ("base_video_intermediate.mp4", ArtifactType.BASE_VIDEO_INTERMEDIATE),
    ("semantic_video_intermediate.mp4", ArtifactType.SEMANTIC_VIDEO_INTERMEDIATE),
])
def test_known_work_artifacts_are_separate_types(tmp_path, name, expected):
    run = tmp_path / "run"
    touch(run / "work" / name)
    item = inspect_inventory(request(tmp_path, run_root=run)).records[0]
    assert item.artifact_type is expected
    assert item.primary_category is ArtifactCategory.WORK
    assert item.potentially_reclaimable_bytes == 1


@pytest.mark.parametrize(("name", "expected"), [
    ("captions.json", ArtifactType.CAPTIONS_JSON),
    ("image_intelligence_plan.json", ArtifactType.IMAGE_INTELLIGENCE_PLAN),
    ("semantic_edit_plan.json", ArtifactType.SEMANTIC_EDIT_PLAN),
    ("caption_director_plan.json", ArtifactType.CAPTION_DIRECTOR_PLAN),
    ("story_director_plan.json", ArtifactType.STORY_DIRECTOR_PLAN),
    ("audio_plan.json", ArtifactType.AUDIO_PLAN),
    ("audio_diagnostics.json", ArtifactType.AUDIO_DIAGNOSTICS),
    ("motion_analysis.json", ArtifactType.MOTION_ANALYSIS),
    ("motion_plan.json", ArtifactType.MOTION_PLAN),
])
def test_known_cache_artifacts_require_run_output_context(tmp_path, name, expected):
    run = tmp_path / "run"
    touch(run / "output" / name, b"cache")
    item = inspect_inventory(request(tmp_path, run_root=run)).records[0]
    assert item.artifact_type is expected
    assert item.primary_category is ArtifactCategory.CACHE
    assert item.potentially_reclaimable_bytes == 5


def test_caption_and_documentary_outputs_are_protected(tmp_path):
    run = tmp_path / "run"
    touch(run / "output" / "captions.srt", b"subtitle")
    touch(run / "output" / "documentary.mp4", b"video")
    result = records(inspect_inventory(request(tmp_path, run_root=run)))
    assert result["run/output/captions.srt"].artifact_type is ArtifactType.CAPTIONS_SRT
    assert result["run/output/documentary.mp4"].artifact_type is ArtifactType.FINAL_DOCUMENTARY
    assert all(item.primary_category is ArtifactCategory.OUTPUT for item in result.values())
    assert all(item.potentially_reclaimable_bytes == 0 for item in result.values())


def test_known_run_log_is_protected(tmp_path):
    run = tmp_path / "run"
    touch(run / "logs" / "render.log", b"log")
    item = inspect_inventory(request(tmp_path, run_root=run)).records[0]
    assert item.artifact_type is ArtifactType.RUN_LOG
    assert item.primary_category is ArtifactCategory.LOGS
    assert not item.cleanup_candidate


def test_arbitrary_mp4_and_root_captions_are_unknown_fail_closed(tmp_path):
    touch(tmp_path / "random.mp4")
    touch(tmp_path / "captions.json")
    touch(tmp_path / "random.zip")
    result = inspect_inventory(request(tmp_path))
    assert all(item.artifact_type is ArtifactType.UNKNOWN for item in result.records)
    assert all(item.protection is ProtectionClass.PROTECTED_UNKNOWN for item in result.records)
    assert all(not item.cleanup_candidate for item in result.records)


def test_validation_and_archive_contexts_are_protected(tmp_path):
    validation = tmp_path / "validation"
    archive = tmp_path / "archive"
    touch(validation / "korea_validation_report.md")
    touch(validation / "hash_inventory.sha256")
    touch(validation / "Korea_validation.zip")
    touch(archive / "release.zip")
    result = records(inspect_inventory(request(
        tmp_path, validation_root=validation, archive_root=archive,
    )))
    assert result["validation/korea_validation_report.md"].artifact_type is ArtifactType.VALIDATION_REPORT
    assert result["validation/hash_inventory.sha256"].artifact_type is ArtifactType.VALIDATION_HASH_INVENTORY
    assert result["validation/Korea_validation.zip"].artifact_type is ArtifactType.VALIDATION_PACKAGE
    assert result["archive/release.zip"].artifact_type is ArtifactType.RELEASE_ARCHIVE
    assert all(item.potentially_reclaimable_bytes == 0 for item in result.values())


def test_rules_include_stable_reason_and_rule_id(tmp_path):
    project = tmp_path / "project"
    touch(project / "bild.jpg")
    item = inspect_inventory(request(tmp_path, project_root=project)).records[0]
    assert item.rule_id == "source.image"
    assert item.reason and item.safety_reason


def test_sorting_unicode_spaces_and_hidden_policy_are_deterministic(tmp_path):
    touch(tmp_path / "ö fil.txt")
    touch(tmp_path / "A file.txt")
    touch(tmp_path / ".hidden")
    first = inspect_inventory(request(tmp_path))
    second = inspect_inventory(request(tmp_path))
    assert first == second
    assert [item.relative_path for item in first.records] == [
        ".hidden", "A file.txt", "ö fil.txt",
    ]
    hidden_excluded = inspect_inventory(request(tmp_path, include_hidden=False))
    assert ".hidden" not in {item.relative_path for item in hidden_excluded.records}


def test_dot_git_not_traversed_and_python_caches_remain_unknown(tmp_path):
    touch(tmp_path / ".git" / "objects" / "secret")
    touch(tmp_path / ".pytest_cache" / "state")
    touch(tmp_path / "__pycache__" / "module.pyc")
    result = records(inspect_inventory(request(tmp_path)))
    assert not any(path.startswith(".git/") for path in result)
    assert result[".pytest_cache/state"].artifact_type is ArtifactType.UNKNOWN
    assert result["__pycache__/module.pyc"].artifact_type is ArtifactType.UNKNOWN


def test_unverified_git_boundary_at_inventory_root_protects_known_cache(tmp_path):
    touch(tmp_path / ".git", b"gitdir: elsewhere")
    touch(tmp_path / "output" / "captions.json", b"cache")
    report = inspect_inventory(request(tmp_path, run_root=tmp_path))
    assert len(report.records) == 1
    assert report.records[0].git_status is GitTrackingStatus.UNKNOWN
    assert not report.records[0].cleanup_candidate
    assert report.potentially_reclaimable_bytes == 0


def test_legacy_intermediate_video_is_ambiguous_not_guessed(tmp_path):
    run = tmp_path / "run"
    touch(run / "work" / "GENERATED_project.mp4", b"video")
    item = inspect_inventory(request(tmp_path, run_root=run)).records[0]
    assert item.artifact_type is ArtifactType.UNKNOWN
    assert item.observation_status is ObservationStatus.AMBIGUOUS
    assert item.rule_id == "work.legacy_video_ambiguous"
    assert item.potentially_reclaimable_bytes == 0


def test_unknown_generated_video_is_not_final_output(tmp_path):
    run = tmp_path / "run"
    touch(run / "work" / "other_generated_video.mp4")
    item = inspect_inventory(request(tmp_path, run_root=run)).records[0]
    assert item.artifact_type is ArtifactType.UNKNOWN
    assert item.primary_category is not ArtifactCategory.OUTPUT
    assert not item.cleanup_candidate


def test_sizes_and_reclaimable_estimates_are_conservative(tmp_path):
    project = tmp_path / "project"
    run = tmp_path / "run"
    touch(project / "source.jpg", b"123")
    touch(run / "work" / "caption_audio_16khz.wav", b"12345")
    touch(run / "output" / "documentary.mp4", b"1234567")
    report = inspect_inventory(request(tmp_path, project_root=project, run_root=run))
    assert report.total_size_bytes == 15
    assert report.potentially_reclaimable_bytes == 5


def test_zero_byte_eligible_artifact_is_candidate_with_zero_reclaimable_bytes(tmp_path):
    run = tmp_path / "run"
    touch(run / "output" / "captions.json", b"")
    item = inspect_inventory(request(tmp_path, run_root=run)).records[0]
    assert item.cleanup_candidate
    assert item.potentially_reclaimable_bytes == 0


def test_git_tracked_cache_is_protected_and_not_reclaimable(tmp_path):
    run = tmp_path / "run"
    cache = touch(run / "output" / "captions.json", b"cache")
    git = GitIndexSnapshot(
        str(tmp_path), frozenset({cache.relative_to(tmp_path).as_posix()}),
        GitTrackingStatus.TRACKED, "Test index",
    )
    report = inspect_inventory(
        request(tmp_path, run_root=run, repository_root=tmp_path),
        git_inspector=lambda _: git,
    )
    item = report.records[0]
    assert item.protection is ProtectionClass.PROTECTED_GIT_TRACKED
    assert item.potentially_reclaimable_bytes == 0


def test_git_unknown_eligible_cache_and_work_are_not_reclaimable(tmp_path):
    run = tmp_path / "run"
    touch(run / "output" / "captions.json", b"cache")
    touch(run / "work" / "caption_audio_16khz.wav", b"work")
    unknown_git = GitIndexSnapshot(
        str(tmp_path), frozenset(), GitTrackingStatus.UNKNOWN, "Unavailable"
    )
    report = inspect_inventory(
        request(tmp_path, run_root=run, repository_root=tmp_path),
        git_inspector=lambda _: unknown_git,
    )
    assert all(not item.cleanup_candidate for item in report.records)
    assert report.potentially_reclaimable_bytes == 0


def test_audio_bundle_complete_and_partial_groups(tmp_path):
    run = tmp_path / "run"
    touch(run / "output" / "audio_plan.json")
    first = inspect_inventory(request(tmp_path, run_root=run))
    assert first.artifact_groups[0].status is ArtifactGroupStatus.PARTIAL
    assert first.artifact_groups[0].missing_required_members == (ArtifactType.AUDIO_DIAGNOSTICS,)
    touch(run / "output" / "audio_diagnostics.json")
    second = inspect_inventory(request(tmp_path, run_root=run))
    assert second.artifact_groups[0].status is ArtifactGroupStatus.COMPLETE
    assert second.artifact_groups[0].missing_required_members == ()
    assert all(item.primary_category is ArtifactCategory.CACHE for item in second.records)


def test_audio_diagnostics_alone_is_partial_and_other_context_is_not_grouped(tmp_path):
    run = tmp_path / "run"
    touch(run / "output" / "audio_diagnostics.json")
    touch(tmp_path / "other" / "audio_plan.json")
    report = inspect_inventory(request(tmp_path, run_root=run))
    assert len(report.artifact_groups) == 1
    assert report.artifact_groups[0].present_members == (ArtifactType.AUDIO_DIAGNOSTICS,)
    assert records(report)["other/audio_plan.json"].descriptor.group_membership is None


def test_symlink_audio_member_does_not_complete_group(tmp_path):
    run = tmp_path / "run"
    target = touch(tmp_path / "local-plan.json")
    (run / "output").mkdir(parents=True)
    (run / "output" / "audio_plan.json").symlink_to(target)
    touch(run / "output" / "audio_diagnostics.json")
    report = inspect_inventory(request(tmp_path, run_root=run))
    assert report.artifact_groups[0].status is ArtifactGroupStatus.PARTIAL
    assert report.artifact_groups[0].present_members == (ArtifactType.AUDIO_DIAGNOSTICS,)
    assert report.artifact_groups[0].reusable is False
    assert report.potentially_reclaimable_bytes == 1


def test_symlink_directory_is_reported_and_not_followed(tmp_path):
    outside = tmp_path.parent / "outside-inventory"
    outside.mkdir(exist_ok=True)
    touch(outside / "documentary.mp4")
    link = tmp_path / "linked-directory"
    link.symlink_to(outside, target_is_directory=True)
    report = inspect_inventory(request(tmp_path))
    assert len(report.records) == 1
    assert report.records[0].relative_path == "linked-directory"
    assert report.records[0].symlink_status is SymlinkStatus.EXTERNAL
    assert report.records[0].symlink_target == str(outside)
    assert report.records[0].potentially_reclaimable_bytes == 0


def test_inventory_does_not_mutate_inputs_files_or_mtimes(tmp_path):
    run = tmp_path / "run"
    file = touch(run / "output" / "captions.json", b"stable")
    roots = {"run_root": run}
    roots_before = dict(roots)
    before = (file.read_bytes(), file.stat().st_mtime_ns, tuple(run.rglob("*")))
    inspect_inventory(request(tmp_path, **roots))
    after = (file.read_bytes(), file.stat().st_mtime_ns, tuple(run.rglob("*")))
    assert roots == roots_before
    assert after == before


def test_deterministic_projection_excludes_absolute_runtime_paths(tmp_path):
    run = tmp_path / "run"
    touch(run / "output" / "captions.json")
    first = inspect_inventory(request(tmp_path, run_root=run)).to_dict()
    second = inspect_inventory(request(tmp_path, run_root=run)).to_dict()
    first_bytes = json.dumps(first, ensure_ascii=False, sort_keys=True).encode("utf-8")
    second_bytes = json.dumps(second, ensure_ascii=False, sort_keys=True).encode("utf-8")
    assert first_bytes == second_bytes
    assert str(tmp_path).encode("utf-8") not in first_bytes
    assert b"runtime_location" not in first_bytes
    assert "symlink_target" not in first["records"][0]


def test_reclaimable_estimate_never_exceeds_regular_file_bytes(tmp_path):
    run = tmp_path / "run"
    touch(run / "output" / "captions.json", b"123")
    touch(run / "work" / "caption_audio_16khz.wav", b"12345")
    touch(run / "output" / "documentary.mp4", b"1234567")
    report = inspect_inventory(request(tmp_path, run_root=run))
    assert report.potentially_reclaimable_bytes <= report.total_regular_file_bytes
    assert len({record.relative_path for record in report.records}) == len(report.records)


def test_invalid_roots_and_depth_fail_closed(tmp_path):
    with pytest.raises(InventoryError):
        inspect_inventory(request(tmp_path / "missing"))
    with pytest.raises(InventoryError):
        inspect_inventory(request(tmp_path, project_root="../escape"))
    with pytest.raises(InventoryError):
        InventoryRequest(tmp_path, max_depth=True)


def test_max_depth_limits_traversal(tmp_path):
    touch(tmp_path / "one" / "two" / "deep.txt")
    shallow = inspect_inventory(request(tmp_path, max_depth=0))
    assert shallow.truncated
    assert len(shallow.records) == 1
    assert shallow.records[0].observation_status is ObservationStatus.PARTIAL
    assert not shallow.records[0].cleanup_candidate
    deep = inspect_inventory(request(tmp_path, max_depth=2))
    assert not deep.truncated
    assert len(deep.records) == 1


def test_nested_repository_boundary_is_observed_and_not_traversed(tmp_path):
    nested = tmp_path / "nested"
    touch(nested / ".git", b"gitdir: elsewhere")
    touch(nested / "output" / "captions.json", b"cache")
    report = inspect_inventory(request(tmp_path, run_root=nested))
    assert len(report.records) == 1
    assert report.records[0].relative_path == "nested"
    assert report.records[0].rule_id == "safety.nested_repository"
    assert not report.records[0].cleanup_candidate


def test_inventory_root_symlink_is_rejected(tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    link = tmp_path / "root-link"
    link.symlink_to(actual, target_is_directory=True)
    with pytest.raises(InventoryError, match="symlink"):
        inspect_inventory(request(link))


def test_nested_listing_permission_error_becomes_protected_observation(tmp_path, monkeypatch):
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    original = Path.iterdir

    def guarded_iterdir(path):
        if path == blocked:
            raise PermissionError("blocked")
        return original(path)

    monkeypatch.setattr(Path, "iterdir", guarded_iterdir)
    report = inspect_inventory(request(tmp_path))
    assert len(report.records) == 1
    assert report.records[0].rule_id == "observation.listing_error"
    assert not report.records[0].cleanup_candidate
    assert report.warnings


def test_file_disappearing_during_lstat_becomes_protected_observation(tmp_path, monkeypatch):
    vanishing = touch(tmp_path / "vanishing.json")
    original = Path.lstat

    def guarded_lstat(path):
        if path == vanishing:
            raise FileNotFoundError(path)
        return original(path)

    monkeypatch.setattr(Path, "lstat", guarded_lstat)
    report = inspect_inventory(request(tmp_path))
    assert report.records[0].descriptor.presence.value == "missing"
    assert report.records[0].rule_id == "observation.stat_error"
    assert not report.records[0].cleanup_candidate
