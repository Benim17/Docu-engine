"""Conservative, read-only path and Git safety assessment.

The helpers never grant deletion permission. Unknown state, unsafe containment,
Git failures, and every symlink are protected fail-closed. Path containment uses
the host filesystem's resolution and case semantics; no attempt is made to infer
case-insensitive or Unicode-alias behavior beyond normalized logical reporting.
"""

from __future__ import annotations

import math
import os
import stat
import subprocess
import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable


class SafetyError(ValueError):
    """Raised for invalid roots or caller-supplied path escape attempts."""


class GitTrackingStatus(str, Enum):
    TRACKED = "tracked"
    UNTRACKED = "untracked"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"


class PathSafetyStatus(str, Enum):
    SAFE = "safe"
    PROTECTED_ROOT = "protected_root"
    OUTSIDE_ROOT = "outside_root"
    UNSAFE = "unsafe"
    UNKNOWN = "unknown"


class SymlinkStatus(str, Enum):
    NOT_SYMLINK = "not_symlink"
    INTERNAL = "internal"
    EXTERNAL = "external"
    BROKEN = "broken"
    LOOP = "loop"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class GitIndexSnapshot:
    """One read-only snapshot of normalized paths tracked by a Git index."""

    repository_root: str | None
    tracked_paths: frozenset[str]
    status: GitTrackingStatus
    reason: str

    def status_for(self, path: Path) -> GitTrackingStatus:
        if self.repository_root is None:
            return self.status
        repository_root = Path(self.repository_root)
        try:
            relative = path.resolve(strict=False).relative_to(repository_root).as_posix()
        except ValueError:
            return GitTrackingStatus.NOT_APPLICABLE
        except (OSError, RuntimeError):
            return GitTrackingStatus.UNKNOWN
        if self.status is not GitTrackingStatus.TRACKED:
            return self.status
        normalized = unicodedata.normalize("NFC", relative)
        if normalized in self.tracked_paths:
            return GitTrackingStatus.TRACKED
        prefix = normalized.rstrip("/") + "/"
        if any(item.startswith(prefix) for item in self.tracked_paths):
            return GitTrackingStatus.TRACKED
        return GitTrackingStatus.UNTRACKED


@dataclass(frozen=True)
class SafetyAssessment:
    """Conservative path result; the safety gate alone never makes an artifact removable."""

    path_status: PathSafetyStatus
    git_status: GitTrackingStatus
    symlink_status: SymlinkStatus
    protected: bool
    passes_safety_gate: bool
    reason: str
    symlink_target: str | None = None


GitRunner = Callable[..., subprocess.CompletedProcess[bytes]]


def inspect_git_index(
    repository_root: str | Path | None,
    *,
    timeout_seconds: float = 5.0,
    runner: GitRunner = subprocess.run,
) -> GitIndexSnapshot:
    """Read a repository index once without changing Git state or configuration."""

    if repository_root is None:
        return GitIndexSnapshot(
            None, frozenset(), GitTrackingStatus.NOT_APPLICABLE, "No repository root supplied."
        )
    root = Path(repository_root).expanduser()
    if not root.is_absolute():
        raise SafetyError("repository_root must be absolute.")
    try:
        root = root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        return GitIndexSnapshot(
            None, frozenset(), GitTrackingStatus.UNKNOWN, f"Repository root unavailable: {exc}."
        )
    if not root.is_dir():
        raise SafetyError("repository_root must identify a directory.")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise SafetyError("timeout_seconds must be positive.")

    command_options = {
        "capture_output": True,
        "check": False,
        "timeout": timeout_seconds,
    }
    try:
        top_level = runner(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            **command_options,
        )
        if top_level.returncode != 0:
            return GitIndexSnapshot(
                str(root), frozenset(), GitTrackingStatus.UNKNOWN,
                "Git repository could not be identified.",
            )
        if top_level.stderr:
            return GitIndexSnapshot(
                str(root), frozenset(), GitTrackingStatus.UNKNOWN,
                "Git repository discovery produced unexpected diagnostics.",
            )
        top_level_value = top_level.stdout.decode("utf-8").rstrip("\r\n")
        if not top_level_value:
            return GitIndexSnapshot(
                str(root), frozenset(), GitTrackingStatus.UNKNOWN,
                "Git repository discovery returned an empty root.",
            )
        discovered_root = Path(
            top_level_value
        ).resolve(strict=True)
        try:
            root.relative_to(discovered_root)
        except ValueError:
            return GitIndexSnapshot(
                str(root), frozenset(), GitTrackingStatus.UNKNOWN,
                "Discovered Git root does not contain the supplied repository root.",
            )
        tracked = runner(
            ["git", "-C", str(discovered_root), "ls-files", "-z"],
            **command_options,
        )
        if tracked.returncode != 0:
            return GitIndexSnapshot(
                str(discovered_root), frozenset(), GitTrackingStatus.UNKNOWN,
                "Git index could not be read.",
            )
        if tracked.stderr:
            return GitIndexSnapshot(
                str(discovered_root), frozenset(), GitTrackingStatus.UNKNOWN,
                "Git index query produced unexpected diagnostics.",
            )
        decoded = tracked.stdout.decode("utf-8")
        paths = frozenset(
            unicodedata.normalize("NFC", item.replace("\\", "/"))
            for item in decoded.split("\0")
            if item
        )
        return GitIndexSnapshot(
            str(discovered_root), paths, GitTrackingStatus.TRACKED,
            "Git index loaded read-only.",
        )
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired, UnicodeError, OSError) as exc:
        return GitIndexSnapshot(
            str(root), frozenset(), GitTrackingStatus.UNKNOWN,
            f"Git index unavailable: {type(exc).__name__}.",
        )


def _contains(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _caller_path(path: str | Path, root: Path) -> Path:
    supplied = Path(path).expanduser()
    if not supplied.is_absolute():
        if ".." in supplied.parts:
            raise SafetyError("Relative path escape is not allowed.")
        supplied = root / supplied
    lexical = Path(os.path.abspath(supplied))
    if not _contains(root, lexical):
        raise SafetyError("Path is outside the explicit inventory root.")
    return lexical


def assess_path_safety(
    path: str | Path,
    *,
    inventory_root: str | Path,
    repository_root: str | Path | None = None,
    storage_root: str | Path | None = None,
    git_index: GitIndexSnapshot | None = None,
) -> SafetyAssessment:
    """Assess one path read-only and fail closed for every uncertain condition."""

    try:
        root_supplied = Path(inventory_root).expanduser()
        if root_supplied.is_symlink():
            raise SafetyError("inventory_root cannot itself be a symlink.")
        root = root_supplied.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SafetyError(f"Inventory root is inaccessible: {exc}.") from exc
    if not root.is_dir():
        raise SafetyError("inventory_root must identify a directory.")
    candidate = _caller_path(path, root)

    repo = Path(repository_root).expanduser().resolve(strict=False) if repository_root else None
    storage = Path(storage_root).expanduser().resolve(strict=False) if storage_root else None
    snapshot = git_index or GitIndexSnapshot(
        None, frozenset(), GitTrackingStatus.NOT_APPLICABLE, "Git was not requested."
    )
    git_status = snapshot.status_for(candidate)

    if candidate == root or (repo is not None and candidate == repo) or (
        storage is not None and candidate == storage
    ):
        return SafetyAssessment(
            PathSafetyStatus.PROTECTED_ROOT, git_status, SymlinkStatus.NOT_SYMLINK,
            True, False, "Inventory, repository, and storage roots are protected containers.",
        )
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise SafetyError("Path is outside the explicit inventory root.") from exc
    if ".git" in relative.parts:
        return SafetyAssessment(
            PathSafetyStatus.UNSAFE, GitTrackingStatus.TRACKED, SymlinkStatus.NOT_SYMLINK,
            True, False, ".git and all of its contents are always protected.",
        )

    try:
        metadata = candidate.lstat()
    except FileNotFoundError:
        return SafetyAssessment(
            PathSafetyStatus.UNKNOWN, git_status, SymlinkStatus.UNKNOWN,
            True, False, "Path disappeared or is missing; protected fail-closed.",
        )
    except OSError:
        return SafetyAssessment(
            PathSafetyStatus.UNKNOWN, git_status, SymlinkStatus.UNKNOWN,
            True, False, "Path metadata is inaccessible; protected fail-closed.",
        )

    if stat.S_ISLNK(metadata.st_mode):
        try:
            raw_target = os.readlink(candidate)
        except OSError:
            return SafetyAssessment(
                PathSafetyStatus.UNKNOWN, git_status, SymlinkStatus.UNKNOWN,
                True, False, "Symlink target could not be read.",
            )
        try:
            resolved_target = candidate.resolve(strict=True)
            if not _contains(root, resolved_target):
                symlink_status = SymlinkStatus.EXTERNAL
                path_status = PathSafetyStatus.UNSAFE
                reason = "Symlink resolves outside the explicit inventory root."
            else:
                symlink_status = SymlinkStatus.INTERNAL
                path_status = PathSafetyStatus.SAFE
                reason = "Internal symlink is observed but always protected."
        except FileNotFoundError:
            symlink_status = SymlinkStatus.BROKEN
            path_status = PathSafetyStatus.UNSAFE
            reason = "Broken symlink is protected fail-closed."
        except RuntimeError:
            symlink_status = SymlinkStatus.LOOP
            path_status = PathSafetyStatus.UNSAFE
            reason = "Symlink loop is protected fail-closed."
        except OSError:
            symlink_status = SymlinkStatus.UNKNOWN
            path_status = PathSafetyStatus.UNKNOWN
            reason = "Symlink target could not be assessed."
        return SafetyAssessment(
            path_status, git_status, symlink_status, True, False, reason, raw_target
        )

    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        return SafetyAssessment(
            PathSafetyStatus.UNKNOWN, git_status, SymlinkStatus.NOT_SYMLINK,
            True, False, "Resolved path could not be assessed.",
        )
    if not _contains(root, resolved):
        return SafetyAssessment(
            PathSafetyStatus.OUTSIDE_ROOT, git_status, SymlinkStatus.NOT_SYMLINK,
            True, False, "Resolved path escapes the explicit inventory root.",
        )
    if git_status is GitTrackingStatus.TRACKED:
        return SafetyAssessment(
            PathSafetyStatus.SAFE, git_status, SymlinkStatus.NOT_SYMLINK,
            True, False, "Git-tracked paths are always protected.",
        )
    if git_status is GitTrackingStatus.UNKNOWN:
        return SafetyAssessment(
            PathSafetyStatus.UNKNOWN, git_status, SymlinkStatus.NOT_SYMLINK,
            True, False, "Git status is unknown; protected fail-closed.",
        )
    return SafetyAssessment(
        PathSafetyStatus.SAFE, git_status, SymlinkStatus.NOT_SYMLINK,
        False, True, "Contained non-symlink path passed read-only safety checks.",
    )
