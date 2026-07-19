"""Read-only command-line presentation for point-in-time storage inventory.

The CLI performs no cleanup or mutation. Potentially reclaimable bytes are an
estimate, never a deletion decision, and any future mutation must repeat safety
validation immediately beforehand. JSON is stable machine-readable output;
the text format is a fixed-order human-readable summary.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Sequence, TextIO

from .inventory import InventoryError, InventoryReport, InventoryRequest, inspect_inventory
from .safety import GitTrackingStatus, PathSafetyStatus, SymlinkStatus


EXIT_SUCCESS = 0
EXIT_USAGE = 2
EXIT_INVENTORY_ERROR = 3
EXIT_INTERNAL_ERROR = 4


def _path_argument(value: str) -> Path:
    """Expand an explicit ``~`` while preserving relative caller paths."""

    try:
        return Path(value).expanduser()
    except RuntimeError as exc:
        raise argparse.ArgumentTypeError("unknown user in path") from exc


def _non_negative_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m engine.storage",
        description="Read-only Documentary Engine storage inspection.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subcommands.add_parser(
        "inspect",
        help="inspect an explicit root without modifying it",
        description=(
            "Observe and classify an explicit root. No cleanup or file mutation is performed."
        ),
    )
    inspect_parser.add_argument("--root", required=True, type=_path_argument)
    inspect_parser.add_argument("--project-root", type=_path_argument)
    inspect_parser.add_argument("--run-root", type=_path_argument)
    inspect_parser.add_argument("--storage-root", type=_path_argument)
    inspect_parser.add_argument("--validation-root", type=_path_argument)
    inspect_parser.add_argument("--archive-root", type=_path_argument)
    inspect_parser.add_argument("--repository-root", type=_path_argument)
    inspect_parser.add_argument("--format", choices=("text", "json"), default="text")
    inspect_parser.add_argument("--max-depth", type=_non_negative_integer, default=12)
    hidden = inspect_parser.add_mutually_exclusive_group()
    hidden.add_argument(
        "--include-hidden", dest="include_hidden", action="store_true",
        help="include hidden entries (default)",
    )
    hidden.add_argument(
        "--exclude-hidden", dest="include_hidden", action="store_false",
        help="exclude hidden entries except the always-protected .git boundary",
    )
    inspect_parser.set_defaults(include_hidden=True)
    return parser


def _json_payload(report: InventoryReport) -> dict[str, object]:
    return {
        "command": "inspect",
        "mutation_performed": False,
        "read_only": True,
        "reclaimable_bytes_are_estimate": True,
        "report": report.to_dict(),
        "report_contract_version": report.contract_version,
        "requires_revalidation_before_mutation": True,
    }


def _render_inventory_json(report: InventoryReport) -> str:
    return json.dumps(
        _json_payload(report),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"


def _passes_safety_gate(record) -> bool:
    return (
        record.path_status is PathSafetyStatus.SAFE
        and record.symlink_status is SymlinkStatus.NOT_SYMLINK
        and record.git_status in {
            GitTrackingStatus.UNTRACKED,
            GitTrackingStatus.NOT_APPLICABLE,
        }
    )


def _render_inventory_text(report: InventoryReport) -> str:
    lines = [
        "Storage inventory",
        "Root: caller-supplied explicit root",
        "Read-only inspection. No files were modified.",
        "Potentially reclaimable bytes are an estimate, not a deletion decision.",
        f"Records: {len(report.records)}",
        f"Total observed bytes: {report.total_size_bytes}",
        f"Regular-file bytes: {report.total_regular_file_bytes}",
        f"Potentially reclaimable bytes: {report.potentially_reclaimable_bytes}",
        f"Truncated: {'yes' if report.truncated else 'no'}",
        f"Warnings: {len(report.warnings)}",
    ]
    for warning in report.warnings:
        lines.append(f"  warning: {warning}")
    lines.append(f"Artifact groups: {len(report.artifact_groups)}")
    for group in report.artifact_groups:
        present = ", ".join(item.value for item in group.present_members) or "none"
        missing = ", ".join(item.value for item in group.missing_required_members) or "none"
        lines.extend([
            f"  group: {group.group_id}",
            f"    type: {group.group_type.value}",
            f"    status: {group.status.value}",
            f"    present-members: {present}",
            f"    missing-required-members: {missing}",
            f"    reusable: {'yes' if group.reusable else 'no'}",
        ])
    lines.append("Observed records:")
    for record in report.records:
        label = "potentially-reclaimable" if record.cleanup_candidate else "protected-observation"
        lines.extend([
            f"[{label}] {record.relative_path}",
            f"  type: {record.artifact_type.value}",
            f"  category: {record.primary_category.value}",
            f"  protection: {record.protection.value}",
            f"  observation: {record.observation_status.value}",
            f"  git: {record.git_status.value}",
            f"  symlink: {record.symlink_status.value}",
            f"  safety-gate: {'pass' if _passes_safety_gate(record) else 'fail-closed'}",
            f"  reclaimable-bytes: {record.potentially_reclaimable_bytes}",
            f"  rule: {record.rule_id}",
        ])
    lines.extend([
        "This report is a point-in-time observation.",
        "Any future mutation requires a new safety revalidation.",
    ])
    return "\n".join(lines) + "\n"


InventoryInspector = Callable[[InventoryRequest], InventoryReport]


def main(
    argv: Sequence[str] | None = None,
    *,
    inspector: InventoryInspector = inspect_inventory,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the read-only CLI and return its documented exit code.

    ``0`` means a report was produced, ``2`` is argparse usage failure, ``3``
    means inspection could not start, and ``4`` is an unexpected internal error.
    ``KeyboardInterrupt`` and ``BrokenPipeError`` are intentionally allowed to
    propagate according to normal command-line semantics.
    """

    parser = _build_parser()
    arguments = parser.parse_args(None if argv is None else list(argv))
    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    request = InventoryRequest(
        inventory_root=arguments.root,
        project_root=arguments.project_root,
        run_root=arguments.run_root,
        storage_root=arguments.storage_root,
        validation_root=arguments.validation_root,
        archive_root=arguments.archive_root,
        repository_root=arguments.repository_root,
        include_hidden=arguments.include_hidden,
        max_depth=arguments.max_depth,
    )
    try:
        report = inspector(request)
    except InventoryError:
        errors.write("Storage inventory error: inspection could not start from the supplied arguments.\n")
        return EXIT_INVENTORY_ERROR
    except Exception:
        errors.write("Storage inventory error: unexpected internal inspection failure.\n")
        return EXIT_INTERNAL_ERROR
    rendered = (
        _render_inventory_json(report)
        if arguments.format == "json"
        else _render_inventory_text(report)
    )
    output.write(rendered)
    return EXIT_SUCCESS
