"""Deterministic read-only artifact inventory.

Inventory observes metadata and classifies conservatively. It never performs
cleanup, writes reports, hashes payloads, or verifies content integrity. Normal
files remain ``unverified`` and reclaimable bytes are estimates only. Unknown or
unsafe observations are protected fail-closed.
"""

from __future__ import annotations

import stat
import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

from .models import (
    ARTIFACT_CONTRACT_VERSION,
    ArtifactCategory,
    ArtifactDescriptor,
    ArtifactGroupMembership,
    ArtifactGroupType,
    ArtifactType,
    GroupMemberRequirement,
    IntegrityStatus,
    PresenceStatus,
    ProducerIdentity,
    ProtectionClass,
    RuntimeLocation,
)
from .safety import (
    GitIndexSnapshot,
    GitTrackingStatus,
    PathSafetyStatus,
    SafetyAssessment,
    SafetyError,
    SymlinkStatus,
    assess_path_safety,
    inspect_git_index,
)


class InventoryError(ValueError):
    """Raised for invalid or inaccessible inventory requests."""


class DiscoveredFileType(str, Enum):
    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"
    OTHER = "other"
    MISSING = "missing"


class ObservationStatus(str, Enum):
    KNOWN = "known"
    PARTIAL = "partial"
    UNKNOWN = "unknown"
    AMBIGUOUS = "ambiguous"
    SUSPICIOUS = "suspicious"


class ArtifactGroupStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"


@dataclass(frozen=True)
class ArtifactGroupInventory:
    """Structural observation only; foundation groups are never reusable yet."""

    group_type: ArtifactGroupType
    group_id: str
    context_id: str
    status: ArtifactGroupStatus
    present_members: tuple[ArtifactType, ...]
    missing_required_members: tuple[ArtifactType, ...]
    reusable: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "context_id": self.context_id,
            "group_id": self.group_id,
            "group_type": self.group_type.value,
            "missing_required_members": [item.value for item in self.missing_required_members],
            "present_members": [item.value for item in self.present_members],
            "reusable": self.reusable,
            "status": self.status.value,
        }


@dataclass(frozen=True)
class InventoryRecord:
    relative_path: str
    discovered_type: DiscoveredFileType
    descriptor: ArtifactDescriptor
    size_bytes: int
    potentially_reclaimable_bytes: int
    git_status: GitTrackingStatus
    symlink_status: SymlinkStatus
    symlink_target: str | None
    path_status: PathSafetyStatus
    observation_status: ObservationStatus
    rule_id: str
    reason: str
    safety_reason: str

    @property
    def artifact_type(self) -> ArtifactType:
        return self.descriptor.artifact_type

    @property
    def primary_category(self) -> ArtifactCategory:
        return self.descriptor.primary_category

    @property
    def protection(self) -> ProtectionClass:
        return self.descriptor.protection

    @property
    def cleanup_candidate(self) -> bool:
        return (
            self.descriptor.cleanup_candidate
            and self.discovered_type is DiscoveredFileType.FILE
            and self.observation_status is ObservationStatus.KNOWN
            and self.path_status is PathSafetyStatus.SAFE
            and self.symlink_status is SymlinkStatus.NOT_SYMLINK
            and self.git_status in {
                GitTrackingStatus.UNTRACKED,
                GitTrackingStatus.NOT_APPLICABLE,
            }
        )

    def to_dict(self) -> dict[str, object]:
        """Return deterministic logical data, excluding runtime paths and symlink targets."""

        return {
            "artifact": self.descriptor.identity_dict(),
            "cleanup_candidate": self.cleanup_candidate,
            "discovered_type": self.discovered_type.value,
            "git_status": self.git_status.value,
            "observation_status": self.observation_status.value,
            "path_status": self.path_status.value,
            "potentially_reclaimable_bytes": self.potentially_reclaimable_bytes,
            "reason": self.reason,
            "relative_path": self.relative_path,
            "rule_id": self.rule_id,
            "safety_reason": self.safety_reason,
            "size_bytes": self.size_bytes,
            "symlink_status": self.symlink_status.value,
            "symlink_target_observed": self.symlink_target is not None,
        }


@dataclass(frozen=True)
class InventoryReport:
    """Point-in-time observation, never a future mutation or deletion plan.

    Estimates are path-based and do not use inode identity, so hardlink aliases
    are not deduplicated in foundation. Any future cleanup must revalidate
    identity, file type, symlink state, path containment, and Git status
    immediately before mutation.
    """

    records: tuple[InventoryRecord, ...]
    artifact_groups: tuple[ArtifactGroupInventory, ...]
    total_size_bytes: int
    total_regular_file_bytes: int
    potentially_reclaimable_bytes: int
    truncated: bool = False
    warnings: tuple[str, ...] = ()
    deterministic: bool = True
    contract_version: str = ARTIFACT_CONTRACT_VERSION

    def to_dict(self) -> dict[str, object]:
        """Return a deterministic projection without absolute runtime locations."""

        return {
            "artifact_groups": [group.to_dict() for group in self.artifact_groups],
            "contract_version": self.contract_version,
            "deterministic": self.deterministic,
            "potentially_reclaimable_bytes": self.potentially_reclaimable_bytes,
            "records": [record.to_dict() for record in self.records],
            "total_regular_file_bytes": self.total_regular_file_bytes,
            "total_size_bytes": self.total_size_bytes,
            "truncated": self.truncated,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class InventoryRequest:
    """Explicit read-only roots; no home or neighboring project discovery occurs.

    Root children are depth zero. A directory encountered at ``max_depth`` is
    reported as a protected partial observation and its children are not read.
    Hidden entries are included by default, except ``.git`` which is never
    traversed. Excluded hidden entries are absent from totals and estimates.
    """

    inventory_root: str | Path
    project_root: str | Path | None = None
    run_root: str | Path | None = None
    storage_root: str | Path | None = None
    validation_root: str | Path | None = None
    archive_root: str | Path | None = None
    repository_root: str | Path | None = None
    include_hidden: bool = True
    max_depth: int = 12

    def __post_init__(self) -> None:
        if not isinstance(self.include_hidden, bool):
            raise InventoryError("include_hidden must be boolean.")
        if isinstance(self.max_depth, bool) or not isinstance(self.max_depth, int) or self.max_depth < 0:
            raise InventoryError("max_depth must be a non-negative integer.")


@dataclass(frozen=True)
class _Classification:
    artifact_type: ArtifactType
    category: ArtifactCategory
    protection: ProtectionClass
    cacheable: bool
    rule_id: str
    reason: str
    observation: ObservationStatus = ObservationStatus.KNOWN
    group_membership: ArtifactGroupMembership | None = None


_PRODUCER = ProducerIdentity("engine.storage.inventory", "foundation-1", "1.0")
_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"})
_AUDIO_SUFFIXES = frozenset({".wav", ".mp3", ".m4a", ".aac", ".flac"})
_CACHE_OUTPUT_NAMES = {
    "captions.json": ArtifactType.CAPTIONS_JSON,
    "image_intelligence_plan.json": ArtifactType.IMAGE_INTELLIGENCE_PLAN,
    "semantic_edit_plan.json": ArtifactType.SEMANTIC_EDIT_PLAN,
    "caption_director_plan.json": ArtifactType.CAPTION_DIRECTOR_PLAN,
    "story_director_plan.json": ArtifactType.STORY_DIRECTOR_PLAN,
    "audio_plan.json": ArtifactType.AUDIO_PLAN,
    "audio_diagnostics.json": ArtifactType.AUDIO_DIAGNOSTICS,
    "motion_analysis.json": ArtifactType.MOTION_ANALYSIS,
    "motion_plan.json": ArtifactType.MOTION_PLAN,
}
_WORK_NAMES = {
    "caption_audio_16khz.wav": ArtifactType.CAPTION_AUDIO_NORMALIZED,
    "source_images.ffconcat": ArtifactType.SOURCE_IMAGES_CONCAT,
    "semantic_timeline.ffconcat": ArtifactType.SEMANTIC_TIMELINE_CONCAT,
    "base_video_intermediate.mp4": ArtifactType.BASE_VIDEO_INTERMEDIATE,
    "semantic_video_intermediate.mp4": ArtifactType.SEMANTIC_VIDEO_INTERMEDIATE,
}


def _resolve_root(value: str | Path | None, inventory_root: Path, name: str) -> Path | None:
    if value is None:
        return None
    supplied = Path(value).expanduser()
    if not supplied.is_absolute():
        if ".." in supplied.parts:
            raise InventoryError(f"{name} cannot escape inventory_root.")
        supplied = inventory_root / supplied
    try:
        return supplied.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise InventoryError(f"{name} could not be resolved: {exc}.") from exc


def _relative_to(path: Path, root: Path | None) -> Path | None:
    if root is None:
        return None
    try:
        return path.relative_to(root)
    except ValueError:
        return None


def _unknown(reason: str, *, rule_id: str = "unknown.fail_closed", observation: ObservationStatus = ObservationStatus.UNKNOWN) -> _Classification:
    return _Classification(
        ArtifactType.UNKNOWN,
        ArtifactCategory.WORK,
        ProtectionClass.PROTECTED_UNKNOWN,
        False,
        rule_id,
        reason,
        observation,
    )


def _classify(
    path: Path,
    *,
    project_root: Path | None,
    run_root: Path | None,
    validation_root: Path | None,
    archive_root: Path | None,
    repository_root: Path | None,
) -> _Classification:
    name = path.name
    lower = name.casefold()
    suffix = path.suffix.casefold()
    project_relative = _relative_to(path, project_root)
    run_relative = _relative_to(path, run_root)
    validation_relative = _relative_to(path, validation_root)
    archive_relative = _relative_to(path, archive_root)
    repository_relative = _relative_to(path, repository_root)

    if validation_relative is not None:
        if "validation_report" in lower or ("report" in lower and suffix in {".md", ".json", ".txt"}):
            return _Classification(
                ArtifactType.VALIDATION_REPORT, ArtifactCategory.VALIDATION,
                ProtectionClass.PROTECTED_VALIDATION, False,
                "validation.report", "Known report inside explicit validation root.",
            )
        if "hash" in lower or "sha256" in lower:
            return _Classification(
                ArtifactType.VALIDATION_HASH_INVENTORY, ArtifactCategory.VALIDATION,
                ProtectionClass.PROTECTED_VALIDATION, False,
                "validation.hash_inventory", "Known hash inventory inside explicit validation root.",
            )
        if suffix == ".zip" and "validation" in lower:
            return _Classification(
                ArtifactType.VALIDATION_PACKAGE, ArtifactCategory.VALIDATION,
                ProtectionClass.PROTECTED_VALIDATION, False,
                "validation.package", "Explicitly named package inside validation root.",
            )
        return _unknown("Unrecognized file inside protected validation root.")

    if archive_relative is not None:
        return _Classification(
            ArtifactType.RELEASE_ARCHIVE, ArtifactCategory.ARCHIVE,
            ProtectionClass.PROTECTED_ARCHIVE, False,
            "archive.explicit_root", "File is inside an explicit protected archive root.",
        )

    if repository_relative is not None and repository_relative.as_posix() == "config.json":
        return _Classification(
            ArtifactType.REPOSITORY_CONFIG, ArtifactCategory.CONFIG,
            ProtectionClass.PROTECTED_CONFIG, False,
            "config.repository", "config.json is at the explicit repository root.",
        )
    if repository_relative is not None and len(repository_relative.parts) >= 2 and repository_relative.parts[0] == "styles" and suffix == ".json":
        return _Classification(
            ArtifactType.STYLE_CONFIG, ArtifactCategory.CONFIG,
            ProtectionClass.PROTECTED_CONFIG, False,
            "config.style", "JSON file is inside the repository styles context.",
        )

    if run_relative is not None:
        if run_relative.as_posix() == "config.json":
            return _Classification(
                ArtifactType.EFFECTIVE_RUN_CONFIG, ArtifactCategory.CONFIG,
                ProtectionClass.PROTECTED_CONFIG, False,
                "config.effective_run", "config.json is at the explicit run root.",
            )
        if len(run_relative.parts) >= 2 and run_relative.parts[0] == "work":
            if lower in _WORK_NAMES:
                return _Classification(
                    _WORK_NAMES[lower], ArtifactCategory.WORK, ProtectionClass.ELIGIBLE_WORK,
                    False, f"work.{_WORK_NAMES[lower].value}",
                    "Known intermediate filename inside explicit run work context.",
                )
            if lower.startswith("generated_") and suffix == ".mp4":
                return _unknown(
                    "Legacy generated video path is ambiguous between base and semantic intermediates.",
                    rule_id="work.legacy_video_ambiguous",
                    observation=ObservationStatus.AMBIGUOUS,
                )
        if len(run_relative.parts) >= 2 and run_relative.parts[0] == "output":
            if lower in _CACHE_OUTPUT_NAMES:
                artifact_type = _CACHE_OUTPUT_NAMES[lower]
                membership = None
                if artifact_type in {ArtifactType.AUDIO_PLAN, ArtifactType.AUDIO_DIAGNOSTICS}:
                    membership = ArtifactGroupMembership(
                        ArtifactGroupType.AUDIO_PLAN_BUNDLE,
                        f"audio-plan-bundle:{run_relative.parent.as_posix()}",
                        GroupMemberRequirement.REQUIRED,
                    )
                return _Classification(
                    artifact_type, ArtifactCategory.CACHE, ProtectionClass.ELIGIBLE_CACHE,
                    True, f"cache.{artifact_type.value}",
                    "Known derived plan inside explicit run output context.",
                    group_membership=membership,
                )
            if lower == "captions.srt":
                return _Classification(
                    ArtifactType.CAPTIONS_SRT, ArtifactCategory.OUTPUT,
                    ProtectionClass.PROTECTED_OUTPUT, False,
                    "output.captions_srt", "Caption export inside explicit run output context.",
                )
            if lower == "documentary.mp4":
                return _Classification(
                    ArtifactType.FINAL_DOCUMENTARY, ArtifactCategory.OUTPUT,
                    ProtectionClass.PROTECTED_OUTPUT, False,
                    "output.final_documentary", "Named final documentary inside run output context.",
                )
        if len(run_relative.parts) >= 2 and run_relative.parts[0] == "logs" and suffix == ".log":
            return _Classification(
                ArtifactType.RUN_LOG, ArtifactCategory.LOGS,
                ProtectionClass.PROTECTED_UNKNOWN, False,
                "logs.run_log", "Log file inside explicit run logs context.",
            )

    if project_relative is not None:
        if lower == "image_manifest.json":
            return _Classification(
                ArtifactType.IMAGE_MANIFEST, ArtifactCategory.SOURCE,
                ProtectionClass.IMMUTABLE_SOURCE, False,
                "source.image_manifest", "Manifest inside explicit source project root.",
            )
        if suffix in _IMAGE_SUFFIXES:
            return _Classification(
                ArtifactType.SOURCE_IMAGE, ArtifactCategory.SOURCE,
                ProtectionClass.IMMUTABLE_SOURCE, False,
                "source.image", "Image inside explicit source project root.",
            )
        voiceover_tokens = ("voiceover", "voice over", "narration", "narrator")
        if suffix in _AUDIO_SUFFIXES and any(token in lower for token in voiceover_tokens):
            return _Classification(
                ArtifactType.SOURCE_VOICEOVER, ArtifactCategory.SOURCE,
                ProtectionClass.IMMUTABLE_SOURCE, False,
                "source.voiceover", "Audio inside explicit source project root.",
            )
        if suffix in _AUDIO_SUFFIXES:
            return _unknown(
                "Audio inside source context has no explicit voiceover role; protected fail-closed.",
                rule_id="source.audio_role_unknown",
            )

    return _unknown("No safe context-sensitive artifact rule matched.")


def _iter_entries(root: Path, *, include_hidden: bool, max_depth: int):
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        directory, depth = stack.pop()
        try:
            entries = sorted(
                directory.iterdir(),
                key=lambda item: (unicodedata.normalize("NFC", item.name).casefold(), item.name),
            )
        except OSError as exc:
            if directory == root:
                raise InventoryError("Inventory root cannot be listed.") from exc
            yield directory, DiscoveredFileType.DIRECTORY, 0, "listing_error"
            continue
        child_directories: list[tuple[Path, int]] = []
        for entry in entries:
            if not include_hidden and entry.name.startswith("."):
                continue
            if entry.name == ".git":
                continue
            try:
                metadata = entry.lstat()
            except OSError:
                yield entry, DiscoveredFileType.MISSING, 0, "stat_error"
                continue
            if stat.S_ISLNK(metadata.st_mode):
                yield entry, DiscoveredFileType.SYMLINK, metadata.st_size, None
            elif stat.S_ISDIR(metadata.st_mode):
                try:
                    (entry / ".git").lstat()
                    nested_repository = True
                except FileNotFoundError:
                    nested_repository = False
                except OSError:
                    yield entry, DiscoveredFileType.DIRECTORY, metadata.st_size, "nested_git_unknown"
                    continue
                if nested_repository:
                    yield entry, DiscoveredFileType.DIRECTORY, metadata.st_size, "nested_repository"
                elif depth < max_depth:
                    child_directories.append((entry, depth + 1))
                else:
                    yield entry, DiscoveredFileType.DIRECTORY, metadata.st_size, "truncated"
            elif stat.S_ISREG(metadata.st_mode):
                yield entry, DiscoveredFileType.FILE, metadata.st_size, None
            else:
                yield entry, DiscoveredFileType.OTHER, metadata.st_size, None
        stack.extend(reversed(child_directories))


def _effective_protection(
    classification: _Classification,
    safety: SafetyAssessment,
) -> ProtectionClass:
    if safety.git_status is GitTrackingStatus.TRACKED:
        return ProtectionClass.PROTECTED_GIT_TRACKED
    if safety.protected:
        return ProtectionClass.PROTECTED_UNKNOWN
    return classification.protection


def _audio_groups(records: tuple[InventoryRecord, ...]) -> tuple[ArtifactGroupInventory, ...]:
    required = {ArtifactType.AUDIO_PLAN, ArtifactType.AUDIO_DIAGNOSTICS}
    grouped: dict[str, set[ArtifactType]] = {}
    contexts: dict[str, str] = {}
    for record in records:
        membership = record.descriptor.group_membership
        if membership is None or membership.group_type is not ArtifactGroupType.AUDIO_PLAN_BUNDLE:
            continue
        grouped.setdefault(membership.group_id, set()).add(record.artifact_type)
        contexts[membership.group_id] = str(Path(record.relative_path).parent).replace("\\", "/")
    results = []
    for group_id in sorted(grouped):
        present = grouped[group_id]
        missing = required - present
        results.append(ArtifactGroupInventory(
            ArtifactGroupType.AUDIO_PLAN_BUNDLE,
            group_id,
            contexts[group_id],
            ArtifactGroupStatus.PARTIAL if missing else ArtifactGroupStatus.COMPLETE,
            tuple(sorted(present, key=lambda item: item.value)),
            tuple(sorted(missing, key=lambda item: item.value)),
        ))
    return tuple(results)


GitInspector = Callable[[str | Path | None], GitIndexSnapshot]


def inspect_inventory(
    request: InventoryRequest,
    *,
    git_inspector: GitInspector = inspect_git_index,
) -> InventoryReport:
    """Inspect only explicit roots and return a deterministic, read-only report."""

    if not isinstance(request, InventoryRequest):
        raise InventoryError("request must be an InventoryRequest.")
    root_supplied = Path(request.inventory_root).expanduser()
    if not root_supplied.is_absolute():
        if ".." in root_supplied.parts:
            raise InventoryError("inventory_root cannot contain a relative path escape.")
        root_supplied = Path.cwd() / root_supplied
    if root_supplied.is_symlink():
        raise InventoryError("inventory_root cannot itself be a symlink.")
    try:
        root = root_supplied.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise InventoryError(f"inventory_root is inaccessible: {exc}.") from exc
    if not root.is_dir():
        raise InventoryError("inventory_root must identify a directory.")

    project_root = _resolve_root(request.project_root, root, "project_root")
    run_root = _resolve_root(request.run_root, root, "run_root")
    storage_root = _resolve_root(request.storage_root, root, "storage_root")
    validation_root = _resolve_root(request.validation_root, root, "validation_root")
    archive_root = _resolve_root(request.archive_root, root, "archive_root")
    repository_root = _resolve_root(request.repository_root, root, "repository_root")
    try:
        try:
            (root / ".git").lstat()
            root_has_git_marker = True
        except FileNotFoundError:
            root_has_git_marker = False
        except OSError:
            root_has_git_marker = True
        if repository_root is None and root_has_git_marker:
            git_index = GitIndexSnapshot(
                str(root), frozenset(), GitTrackingStatus.UNKNOWN,
                "Inventory root has an unverified Git boundary.",
            )
        else:
            git_index = git_inspector(repository_root)
    except Exception as exc:
        git_index = GitIndexSnapshot(
            str(repository_root) if repository_root else None,
            frozenset(),
            GitTrackingStatus.UNKNOWN,
            f"Git inspection failed: {type(exc).__name__}.",
        )

    records_by_path: dict[str, InventoryRecord] = {}
    warnings: set[str] = set()
    for path, discovered_type, size_bytes, traversal_issue in _iter_entries(
        root, include_hidden=request.include_hidden, max_depth=request.max_depth
    ):
        relative_path = unicodedata.normalize("NFC", path.relative_to(root).as_posix())
        try:
            safety = assess_path_safety(
                path,
                inventory_root=root,
                repository_root=repository_root,
                storage_root=storage_root,
                git_index=git_index,
            )
        except SafetyError as exc:
            safety = SafetyAssessment(
                PathSafetyStatus.UNSAFE, GitTrackingStatus.UNKNOWN, SymlinkStatus.UNKNOWN,
                True, False, str(exc),
            )
        classification = _classify(
            path,
            project_root=project_root,
            run_root=run_root,
            validation_root=validation_root,
            archive_root=archive_root,
            repository_root=repository_root,
        )
        if traversal_issue == "nested_repository":
            classification = _unknown(
                "Nested repository or submodule boundary was not traversed.",
                rule_id="safety.nested_repository",
                observation=ObservationStatus.SUSPICIOUS,
            )
            warnings.add("Nested repository or submodule boundary protected fail-closed.")
        elif traversal_issue == "nested_git_unknown":
            classification = _unknown(
                "Nested Git boundary could not be assessed.",
                rule_id="safety.nested_git_unknown",
                observation=ObservationStatus.SUSPICIOUS,
            )
            warnings.add("A nested Git boundary could not be assessed.")
        elif traversal_issue == "truncated":
            classification = _unknown(
                "Directory was observed at max depth but its children were not traversed.",
                rule_id="traversal.max_depth",
                observation=ObservationStatus.PARTIAL,
            )
            warnings.add("Traversal was truncated by max_depth.")
        elif traversal_issue in {"listing_error", "stat_error"}:
            classification = _unknown(
                "Path metadata or directory listing changed or was inaccessible during inspection.",
                rule_id=f"observation.{traversal_issue}",
                observation=ObservationStatus.SUSPICIOUS,
            )
            warnings.add("One or more paths could not be fully observed.")
        if discovered_type is DiscoveredFileType.SYMLINK:
            classification = _unknown(
                f"Symlink target is never classified as a cleanup-eligible artifact; prior rule: {classification.rule_id}.",
                rule_id="safety.symlink_fail_closed",
                observation=ObservationStatus.SUSPICIOUS,
            )
        protection = _effective_protection(classification, safety)
        descriptor = ArtifactDescriptor(
            artifact_type=classification.artifact_type,
            primary_category=classification.category,
            logical_id=relative_path,
            cacheable=classification.cacheable,
            protection=protection,
            producer=_PRODUCER,
            group_membership=classification.group_membership,
            presence=(
                PresenceStatus.MISSING
                if discovered_type is DiscoveredFileType.MISSING
                else PresenceStatus.PRESENT
            ),
            integrity=(
                IntegrityStatus.UNKNOWN
                if discovered_type is DiscoveredFileType.MISSING
                else IntegrityStatus.UNVERIFIED
            ),
            runtime_location=RuntimeLocation(str(path)),
        )
        reclaimable = (
            size_bytes
            if descriptor.cleanup_candidate
            and not safety.protected
            and discovered_type is DiscoveredFileType.FILE
            and classification.observation is ObservationStatus.KNOWN
            else 0
        )
        record = InventoryRecord(
            relative_path,
            discovered_type,
            descriptor,
            size_bytes,
            reclaimable,
            safety.git_status,
            safety.symlink_status,
            safety.symlink_target,
            safety.path_status,
            classification.observation,
            classification.rule_id,
            classification.reason,
            safety.reason,
        )
        if relative_path in records_by_path:
            previous = records_by_path[relative_path]
            collision_descriptor = ArtifactDescriptor(
                ArtifactType.UNKNOWN,
                ArtifactCategory.WORK,
                relative_path,
                False,
                ProtectionClass.PROTECTED_UNKNOWN,
                _PRODUCER,
                presence=PresenceStatus.PRESENT,
                integrity=IntegrityStatus.UNKNOWN,
            )
            records_by_path[relative_path] = InventoryRecord(
                relative_path,
                (
                    DiscoveredFileType.FILE
                    if previous.discovered_type is DiscoveredFileType.FILE
                    and record.discovered_type is DiscoveredFileType.FILE
                    else DiscoveredFileType.OTHER
                ),
                collision_descriptor,
                previous.size_bytes + record.size_bytes,
                0,
                GitTrackingStatus.UNKNOWN,
                SymlinkStatus.UNKNOWN,
                None,
                PathSafetyStatus.UNKNOWN,
                ObservationStatus.SUSPICIOUS,
                "observation.logical_path_collision",
                "Multiple filesystem entries collide after logical path normalization.",
                "Logical path collision is protected fail-closed.",
            )
            warnings.add("Logical path normalization collision detected.")
        else:
            records_by_path[relative_path] = record

    ordered_records = tuple(sorted(
        records_by_path.values(),
        key=lambda item: (item.relative_path.casefold(), item.relative_path),
    ))
    groups = _audio_groups(ordered_records)
    total_regular = sum(
        record.size_bytes
        for record in ordered_records
        if record.discovered_type is DiscoveredFileType.FILE
    )
    reclaimable = sum(record.potentially_reclaimable_bytes for record in ordered_records)
    if reclaimable > total_regular:
        raise InventoryError("Reclaimable estimate exceeded observed regular-file bytes.")
    return InventoryReport(
        ordered_records,
        groups,
        sum(record.size_bytes for record in ordered_records),
        total_regular,
        reclaimable,
        any(record.rule_id == "traversal.max_depth" for record in ordered_records),
        tuple(sorted(warnings)),
    )
