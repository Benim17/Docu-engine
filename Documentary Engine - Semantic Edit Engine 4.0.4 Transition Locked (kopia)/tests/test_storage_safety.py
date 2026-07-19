from pathlib import Path
import os
import subprocess

import pytest

from engine.storage import (
    GitIndexSnapshot,
    GitTrackingStatus,
    PathSafetyStatus,
    SafetyError,
    SymlinkStatus,
    assess_path_safety,
    inspect_git_index,
)


def snapshot(root, *tracked):
    return GitIndexSnapshot(
        str(root.resolve()),
        frozenset(tracked),
        GitTrackingStatus.TRACKED,
        "Test index.",
    )


def test_inventory_repository_and_storage_roots_are_protected(tmp_path):
    for keyword in ({}, {"repository_root": tmp_path}, {"storage_root": tmp_path}):
        result = assess_path_safety(tmp_path, inventory_root=tmp_path, **keyword)
        assert result.path_status is PathSafetyStatus.PROTECTED_ROOT
        assert result.protected and not result.passes_safety_gate


def test_dot_git_and_git_tracked_paths_are_protected(tmp_path):
    dot_git = tmp_path / ".git"
    dot_git.mkdir()
    index_file = dot_git / "index"
    index_file.write_bytes(b"index")
    result = assess_path_safety(index_file, inventory_root=tmp_path)
    assert result.protected and not result.passes_safety_gate

    tracked = tmp_path / "output" / "motion_plan.json"
    tracked.parent.mkdir()
    tracked.write_text("{}", encoding="utf-8")
    result = assess_path_safety(
        tracked,
        inventory_root=tmp_path,
        repository_root=tmp_path,
        git_index=snapshot(tmp_path, "output/motion_plan.json"),
    )
    assert result.git_status is GitTrackingStatus.TRACKED
    assert result.protected and not result.passes_safety_gate


def test_git_untracked_contained_path_can_pass_safety_contract(tmp_path):
    cache = tmp_path / "output" / "captions.json"
    cache.parent.mkdir()
    cache.write_text("{}", encoding="utf-8")
    result = assess_path_safety(
        cache,
        inventory_root=tmp_path,
        repository_root=tmp_path,
        git_index=snapshot(tmp_path),
    )
    assert result.git_status is GitTrackingStatus.UNTRACKED
    assert result.passes_safety_gate and not result.protected


def test_git_unavailable_and_timeout_are_unknown_fail_closed(tmp_path):
    def unavailable(*args, **kwargs):
        raise FileNotFoundError("git")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 0.01)

    for runner in (unavailable, timeout):
        result = inspect_git_index(tmp_path, runner=runner)
        assert result.status is GitTrackingStatus.UNKNOWN
        path = tmp_path / "cache.json"
        path.write_text("{}", encoding="utf-8")
        assessment = assess_path_safety(
            path, inventory_root=tmp_path, repository_root=tmp_path, git_index=result
        )
        assert assessment.protected and not assessment.passes_safety_gate


def test_git_index_is_loaded_once_and_handles_spaces_and_unicode(tmp_path):
    calls = []
    tracked_name = "output/plan med å\nrad.json"

    def runner(command, **kwargs):
        calls.append(command)
        if "rev-parse" in command:
            return subprocess.CompletedProcess(command, 0, str(tmp_path).encode() + b"\n", b"")
        return subprocess.CompletedProcess(command, 0, tracked_name.encode() + b"\0", b"")

    result = inspect_git_index(tmp_path, runner=runner)
    assert len(calls) == 2
    tracked = tmp_path / "output" / "plan med å\nrad.json"
    tracked.parent.mkdir()
    tracked.write_text("{}", encoding="utf-8")
    assert result.status_for(tracked) is GitTrackingStatus.TRACKED


def test_git_non_repository_is_unknown_not_an_exception(tmp_path):
    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 128, b"", b"not a repository")

    result = inspect_git_index(tmp_path, runner=runner)
    assert result.status is GitTrackingStatus.UNKNOWN


def test_unexpected_git_output_and_stderr_fail_closed(tmp_path):
    def empty_root(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, b"", b"")

    def diagnostic(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, str(tmp_path).encode() + b"\n", b"warning")

    assert inspect_git_index(tmp_path, runner=empty_root).status is GitTrackingStatus.UNKNOWN
    assert inspect_git_index(tmp_path, runner=diagnostic).status is GitTrackingStatus.UNKNOWN


def test_path_outside_git_repository_is_not_applicable(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    git = snapshot(repository, "tracked.json")
    assert git.status_for(outside) is GitTrackingStatus.NOT_APPLICABLE
    unknown_git = GitIndexSnapshot(
        str(repository), frozenset(), GitTrackingStatus.UNKNOWN, "Unavailable"
    )
    assert unknown_git.status_for(outside) is GitTrackingStatus.NOT_APPLICABLE


def test_discovered_git_root_must_contain_supplied_root(tmp_path):
    supplied = tmp_path / "supplied"
    unrelated = tmp_path / "unrelated"
    supplied.mkdir()
    unrelated.mkdir()

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, str(unrelated).encode() + b"\n", b"")

    assert inspect_git_index(supplied, runner=runner).status is GitTrackingStatus.UNKNOWN


@pytest.mark.parametrize("relative", ["../escape", "inside/../../escape"])
def test_relative_dot_dot_escape_is_rejected(tmp_path, relative):
    with pytest.raises(SafetyError, match="escape"):
        assess_path_safety(relative, inventory_root=tmp_path)


def test_absolute_outside_path_and_string_prefix_bypass_are_rejected(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-other" / "file.json"
    outside.parent.mkdir(exist_ok=True)
    outside.write_text("{}", encoding="utf-8")
    with pytest.raises(SafetyError, match="outside"):
        assess_path_safety(outside, inventory_root=tmp_path)


def test_internal_symlink_is_observed_but_never_cleanup_eligible(tmp_path):
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    result = assess_path_safety(link, inventory_root=tmp_path)
    assert result.symlink_status is SymlinkStatus.INTERNAL
    assert result.protected and not result.passes_safety_gate


def test_symlink_to_inventory_root_does_not_recurse(tmp_path):
    link = tmp_path / "root-link"
    link.symlink_to(tmp_path, target_is_directory=True)
    result = assess_path_safety(link, inventory_root=tmp_path)
    assert result.symlink_status is SymlinkStatus.INTERNAL
    assert result.protected and not result.passes_safety_gate


def test_external_and_broken_symlinks_are_unsafe(tmp_path):
    external_target = tmp_path.parent / "external-target.json"
    external_target.write_text("{}", encoding="utf-8")
    external = tmp_path / "external-link.json"
    external.symlink_to(external_target)
    broken = tmp_path / "broken-link.json"
    broken.symlink_to(tmp_path / "missing.json")
    assert assess_path_safety(external, inventory_root=tmp_path).symlink_status is SymlinkStatus.EXTERNAL
    broken_result = assess_path_safety(broken, inventory_root=tmp_path)
    assert broken_result.symlink_status is SymlinkStatus.BROKEN
    assert broken_result.symlink_target == str(tmp_path / "missing.json")
    assert assess_path_safety(external, inventory_root=tmp_path).protected
    assert assess_path_safety(broken, inventory_root=tmp_path).protected


def test_symlink_loop_does_not_recurse(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.symlink_to(second)
    second.symlink_to(first)
    result = assess_path_safety(first, inventory_root=tmp_path)
    assert result.symlink_status is SymlinkStatus.LOOP
    assert result.protected and not result.passes_safety_gate


def test_missing_path_is_unknown_and_protected(tmp_path):
    result = assess_path_safety(tmp_path / "missing", inventory_root=tmp_path)
    assert result.path_status is PathSafetyStatus.UNKNOWN
    assert result.protected and not result.passes_safety_gate


def test_gitfile_is_always_protected(tmp_path):
    gitfile = tmp_path / ".git"
    gitfile.write_text("gitdir: elsewhere", encoding="utf-8")
    result = assess_path_safety(gitfile, inventory_root=tmp_path)
    assert result.protected and not result.passes_safety_gate


def test_inventory_root_symlink_is_rejected(tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    link = tmp_path / "root-link"
    link.symlink_to(actual, target_is_directory=True)
    with pytest.raises(SafetyError, match="symlink"):
        assess_path_safety(actual / "file", inventory_root=link)


def test_readlink_and_resolve_errors_fail_closed(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.write_text("x", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(target)
    monkeypatch.setattr(os, "readlink", lambda _: (_ for _ in ()).throw(PermissionError()))
    result = assess_path_safety(link, inventory_root=tmp_path)
    assert result.symlink_status is SymlinkStatus.UNKNOWN
    assert result.protected and not result.passes_safety_gate


def test_safety_helpers_do_not_mutate_files(tmp_path):
    file = tmp_path / "stable.json"
    file.write_text("{}", encoding="utf-8")
    before = (file.read_bytes(), file.stat().st_mtime_ns, tuple(tmp_path.iterdir()))
    assess_path_safety(file, inventory_root=tmp_path)
    after = (file.read_bytes(), file.stat().st_mtime_ns, tuple(tmp_path.iterdir()))
    assert after == before
