from io import StringIO
import importlib
import json
from pathlib import Path

import pytest

from engine.storage import (
    InventoryError,
    InventoryReport,
    inspect_inventory,
)
from engine.storage.cli import (
    EXIT_INTERNAL_ERROR,
    EXIT_INVENTORY_ERROR,
    EXIT_SUCCESS,
    main,
)


def touch(path, content=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def run_cli(arguments, *, inspector=inspect_inventory):
    stdout = StringIO()
    stderr = StringIO()
    exit_code = main(arguments, inspector=inspector, stdout=stdout, stderr=stderr)
    return exit_code, stdout.getvalue(), stderr.getvalue()


def empty_report():
    return InventoryReport((), (), 0, 0, 0)


def test_top_level_and_inspect_help_do_not_start_inventory(capsys):
    def forbidden(_):
        pytest.fail("help must not inspect")

    with pytest.raises(SystemExit) as top_level:
        main(["--help"], inspector=forbidden)
    assert top_level.value.code == 0
    assert "inspect" in capsys.readouterr().out

    with pytest.raises(SystemExit) as inspect_help:
        main(["inspect", "--help"], inspector=forbidden)
    assert inspect_help.value.code == 0
    help_output = capsys.readouterr().out
    assert "--root" in help_output and "--exclude-hidden" in help_output


@pytest.mark.parametrize("arguments", [
    [],
    ["inspect"],
    ["inspect", "--root", ".", "--unknown"],
    ["inspect", "--root", ".", "--format", "yaml"],
    ["inspect", "--root", ".", "--max-depth", "-1"],
    ["inspect", "--root", ".", "--max-depth", "many"],
])
def test_invalid_usage_exits_two_without_inventory(arguments, capsys):
    def forbidden(_):
        pytest.fail("parser errors must not inspect")

    with pytest.raises(SystemExit) as caught:
        main(arguments, inspector=forbidden)
    assert caught.value.code == 2
    assert "usage:" in capsys.readouterr().err


def test_argument_mapping_is_exact_and_unspecified_roots_remain_none(tmp_path):
    captured = []

    def inspector(request):
        captured.append(request)
        return empty_report()

    arguments = [
        "inspect", "--root", "relative-root",
        "--project-root", "project",
        "--run-root", "run",
        "--storage-root", "storage",
        "--validation-root", "validation",
        "--archive-root", "archive",
        "--repository-root", "repository",
        "--format", "json", "--max-depth", "7", "--exclude-hidden",
    ]
    original = list(arguments)
    code, _, error = run_cli(arguments, inspector=inspector)
    assert code == EXIT_SUCCESS and error == ""
    assert arguments == original
    request = captured[0]
    assert request.inventory_root == Path("relative-root")
    assert request.project_root == Path("project")
    assert request.run_root == Path("run")
    assert request.storage_root == Path("storage")
    assert request.validation_root == Path("validation")
    assert request.archive_root == Path("archive")
    assert request.repository_root == Path("repository")
    assert request.include_hidden is False and request.max_depth == 7

    captured.clear()
    run_cli(["inspect", "--root", str(tmp_path)], inspector=inspector)
    request = captured[0]
    assert request.inventory_root == tmp_path
    assert all(getattr(request, field) is None for field in (
        "project_root", "run_root", "storage_root", "validation_root",
        "archive_root", "repository_root",
    ))
    assert request.include_hidden is True and request.max_depth == 12


def test_relative_root_uses_caller_cwd_without_changing_it(tmp_path, monkeypatch):
    root = tmp_path / "relative"
    root.mkdir()
    monkeypatch.chdir(tmp_path)
    before = Path.cwd()
    code, _, error = run_cli(["inspect", "--root", "relative"])
    assert code == EXIT_SUCCESS and error == ""
    assert Path.cwd() == before


def test_tilde_is_expanded_but_environment_variables_are_not(tmp_path, monkeypatch):
    captured = []

    def inspector(request):
        captured.append(request)
        return empty_report()

    monkeypatch.setenv("HOME", str(tmp_path))
    run_cli(["inspect", "--root", "~/project"], inspector=inspector)
    assert captured[-1].inventory_root == tmp_path / "project"
    run_cli(["inspect", "--root", "$HOME/project"], inspector=inspector)
    assert captured[-1].inventory_root == Path("$HOME/project")


def test_unknown_tilde_user_is_usage_error_without_traceback(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["inspect", "--root", "~documentary_engine_unknown_user_947/fixture"])
    assert caught.value.code == 2
    error = capsys.readouterr().err
    assert "unknown user in path" in error
    assert "traceback" not in error.casefold()


def test_tilde_expanded_symlink_root_is_rejected_by_inventory(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.mkdir()
    (tmp_path / "fixture").symlink_to(target, target_is_directory=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    code, output, error = run_cli(["inspect", "--root", "~/fixture"])
    assert code == EXIT_INVENTORY_ERROR
    assert output == ""
    assert "inspection could not start" in error


def test_hidden_flags_are_mutually_exclusive_without_inventory(capsys):
    def forbidden(_):
        pytest.fail("conflicting flags must not inspect")

    with pytest.raises(SystemExit) as caught:
        main(
            ["inspect", "--root", ".", "--include-hidden", "--exclude-hidden"],
            inspector=forbidden,
        )
    assert caught.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


def test_successful_text_and_json_inspections_use_separate_streams(tmp_path):
    text_code, text, text_error = run_cli(["inspect", "--root", str(tmp_path)])
    json_code, payload, json_error = run_cli([
        "inspect", "--root", str(tmp_path), "--format", "json",
    ])
    assert text_code == json_code == EXIT_SUCCESS
    assert text and payload
    assert text_error == json_error == ""


def test_json_is_valid_unicode_stable_sorted_and_newline_terminated(tmp_path):
    run = tmp_path / "run"
    touch(run / "output" / "captions.json", "räv".encode("utf-8"))
    arguments = ["inspect", "--root", str(tmp_path), "--run-root", str(run), "--format", "json"]
    first = run_cli(arguments)
    second = run_cli(arguments)
    assert first == second
    code, output, error = first
    assert code == 0 and error == "" and output.endswith("\n")
    payload = json.loads(output)
    assert payload["read_only"] is True
    assert payload["mutation_performed"] is False
    assert payload["reclaimable_bytes_are_estimate"] is True
    assert payload["requires_revalidation_before_mutation"] is True
    assert payload["report_contract_version"] == payload["report"]["contract_version"]
    assert payload["report"]["records"][0]["relative_path"] == "run/output/captions.json"
    assert "räv" not in output  # file content is never read
    assert output == json.dumps(
        payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
    ) + "\n"


def test_json_contains_no_runtime_paths_timestamps_or_debug_text(tmp_path):
    run = tmp_path / "run"
    touch(run / "output" / "captions.json")
    _, output, error = run_cli([
        "inspect", "--root", str(tmp_path), "--run-root", str(run), "--format", "json",
    ])
    assert error == ""
    for forbidden in (str(tmp_path), "runtime_location", "timestamp", "traceback", "stderr"):
        assert forbidden not in output.casefold()


def test_json_preserves_record_group_warning_and_integer_contracts(tmp_path):
    run = tmp_path / "run"
    touch(run / "output" / "audio_plan.json", b"123")
    touch(run / "output" / "z unknown.txt", b"12")
    touch(run / "output" / "a unknown.txt", b"1")
    code, output, _ = run_cli([
        "inspect", "--root", str(tmp_path), "--run-root", str(run),
        "--max-depth", "12", "--format", "json",
    ])
    payload = json.loads(output)["report"]
    assert code == 0
    assert [item["relative_path"] for item in payload["records"]] == sorted(
        item["relative_path"] for item in payload["records"]
    )
    assert payload["artifact_groups"][0]["status"] == "partial"
    assert payload["artifact_groups"][0]["reusable"] is False
    assert isinstance(payload["total_regular_file_bytes"], int)
    assert payload["warnings"] == sorted(payload["warnings"])


def test_json_unknown_unsafe_symlink_and_truncated_reports_still_exit_zero(tmp_path):
    outside = tmp_path.parent / "cli-outside"
    outside.mkdir(exist_ok=True)
    touch(outside / "file")
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    touch(tmp_path / "deep" / "child" / "unknown.txt")
    code, output, error = run_cli([
        "inspect", "--root", str(tmp_path), "--max-depth", "0", "--format", "json",
    ])
    payload = json.loads(output)["report"]
    assert code == 0 and error == ""
    assert payload["truncated"] is True
    assert payload["warnings"]
    assert any(item["artifact"]["artifact_type"] == "unknown" for item in payload["records"])


def test_text_format_has_stable_read_only_summary_and_no_ansi(tmp_path):
    project = tmp_path / "project"
    run = tmp_path / "run"
    touch(project / "image.jpg", b"source")
    touch(run / "output" / "captions.json", b"cache")
    touch(tmp_path / "unknown.bin", b"unknown")
    code, output, error = run_cli([
        "inspect", "--root", str(tmp_path), "--project-root", str(project),
        "--run-root", str(run),
    ])
    assert code == 0 and error == "" and output.endswith("\n")
    assert output.startswith("Storage inventory\n")
    assert "Read-only inspection. No files were modified." in output
    assert "estimate, not a deletion decision" in output
    assert "Total observed bytes:" in output and "Regular-file bytes:" in output
    assert "Potentially reclaimable bytes:" in output
    assert "type: source_image" in output and "type: captions_json" in output
    assert "type: unknown" in output
    assert "safety-gate:" in output and "rule:" in output
    assert "\x1b[" not in output
    assert "safe to delete" not in output.casefold()
    assert str(tmp_path) not in output


def test_outputs_never_use_mutation_authorization_phrases(tmp_path):
    touch(tmp_path / "unknown.bin")
    text_output = run_cli(["inspect", "--root", str(tmp_path)])[1]
    json_output = run_cli([
        "inspect", "--root", str(tmp_path), "--format", "json",
    ])[1]
    for output in (text_output, json_output):
        normalized = output.casefold()
        for forbidden in (
            "safe to delete", "can be deleted", "will be removed", "cleanup approved",
        ):
            assert forbidden not in normalized


def test_text_renders_ambiguous_symlink_git_unknown_and_groups(tmp_path):
    run = tmp_path / "run"
    touch(run / "work" / "GENERATED_project.mp4")
    touch(run / "output" / "audio_plan.json")
    target = touch(tmp_path / "target")
    (tmp_path / "link").symlink_to(target)
    touch(tmp_path / ".git", b"gitdir: unknown")
    code, output, error = run_cli([
        "inspect", "--root", str(tmp_path), "--run-root", str(run),
    ])
    assert code == 0 and error == ""
    assert "observation: ambiguous" in output
    assert "symlink:" in output
    assert "git: unknown" in output
    assert "status: partial" in output
    assert "reusable: no" in output


def test_text_complete_audio_group_and_record_order_are_stable(tmp_path):
    run = tmp_path / "run"
    touch(run / "output" / "audio_plan.json")
    touch(run / "output" / "audio_diagnostics.json")
    arguments = ["inspect", "--root", str(tmp_path), "--run-root", str(run)]
    first = run_cli(arguments)
    second = run_cli(arguments)
    assert first == second
    assert "status: complete" in first[1]
    assert first[1].index("audio_diagnostics.json") < first[1].index("audio_plan.json")


def test_hidden_default_and_explicit_exclusion(tmp_path):
    touch(tmp_path / ".hidden")
    included = json.loads(run_cli([
        "inspect", "--root", str(tmp_path), "--format", "json",
    ])[1])
    excluded = json.loads(run_cli([
        "inspect", "--root", str(tmp_path), "--exclude-hidden", "--format", "json",
    ])[1])
    assert len(included["report"]["records"]) == 1
    assert excluded["report"]["records"] == []


@pytest.mark.parametrize("root_kind", ["missing", "file", "symlink"])
def test_inventory_start_errors_use_exit_three_and_stderr_only(tmp_path, root_kind):
    if root_kind == "missing":
        root = tmp_path / "missing"
    elif root_kind == "file":
        root = touch(tmp_path / "file")
    else:
        target = tmp_path / "target"
        target.mkdir()
        root = tmp_path / "link"
        root.symlink_to(target, target_is_directory=True)
    code, output, error = run_cli(["inspect", "--root", str(root)])
    assert code == EXIT_INVENTORY_ERROR
    assert output == ""
    assert error.startswith("Storage inventory error:")
    assert str(tmp_path) not in error


def test_unknown_warning_and_truncated_reports_are_success_not_validation_failures(tmp_path):
    touch(tmp_path / "unknown")
    touch(tmp_path / "deep" / "child")
    code, output, error = run_cli([
        "inspect", "--root", str(tmp_path), "--max-depth", "0",
    ])
    assert code == 0 and output and error == ""
    assert "Truncated: yes" in output and "Warnings:" in output


def test_unexpected_error_uses_exit_four_without_traceback():
    def broken(_):
        raise RuntimeError("private detail")

    code, output, error = run_cli(["inspect", "--root", "."], inspector=broken)
    assert code == EXIT_INTERNAL_ERROR
    assert output == ""
    assert "unexpected internal" in error
    assert "private detail" not in error and "traceback" not in error.casefold()


def test_keyboard_interrupt_propagates():
    def interrupted(_):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_cli(["inspect", "--root", "."], inspector=interrupted)


def test_broken_pipe_propagates_after_complete_render():
    class ClosedConsumer(StringIO):
        def write(self, value):
            raise BrokenPipeError

    with pytest.raises(BrokenPipeError):
        main(
            ["inspect", "--root", "."],
            inspector=lambda _: empty_report(),
            stdout=ClosedConsumer(),
            stderr=StringIO(),
        )


def test_cli_import_has_no_observable_side_effects(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    before = tuple(tmp_path.iterdir())
    module = importlib.import_module("engine.storage.cli")
    importlib.reload(module)
    assert tuple(tmp_path.iterdir()) == before
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_cli_is_read_only_and_creates_no_report_log_or_temp_files(tmp_path):
    touch(tmp_path / "source.jpg", b"stable")
    before = {
        path.relative_to(tmp_path).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in tmp_path.rglob("*") if path.is_file()
    }
    code, output, error = run_cli([
        "inspect", "--root", str(tmp_path), "--project-root", str(tmp_path),
        "--format", "json",
    ])
    after = {
        path.relative_to(tmp_path).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in tmp_path.rglob("*") if path.is_file()
    }
    assert code == 0 and output and error == ""
    assert after == before
    assert not (tmp_path / "inventory.json").exists()


def test_inventory_error_from_inspector_maps_to_exit_three():
    def invalid(_):
        raise InventoryError("local absolute detail")

    code, output, error = run_cli(["inspect", "--root", "."], inspector=invalid)
    assert code == EXIT_INVENTORY_ERROR
    assert output == ""
    assert "local absolute detail" not in error
