"""Internal Step 5E1 read-only recovery traversal foundations.

This module deliberately performs no cache-content validation or lifecycle
classification.  It derives and discovers only contract-scoped paths.
"""

from __future__ import annotations

import errno
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable

from .cache_keys import CacheKey
from .cache_lookup import (
    BoundedFileRead,
    CacheArtifactExpectation,
    CacheLookupFilesystemError,
    CacheLookupIOError,
    CacheLookupExpectation,
    CacheLookupPermissionError,
    CacheLookupReason,
    CacheLookupRequest,
    CacheLookupStatus,
    CacheLookupVerificationPolicy,
    FileIdentity,
    FilesystemObjectType,
    LocalReadOnlyCacheFilesystem,
    LockObservationPolicy,
    LockObservationClock,
    ProducerPayloadExpectation,
    SYSTEM_LOCK_OBSERVATION_CLOCK,
    SymlinkRejectedError,
    UnstableFilesystemObjectError,
    UnsupportedFilesystemObjectError,
    ValidatedCacheRoot,
    _CacheDocumentClassification,
    _CacheDocumentName,
    _FinalEntryStructureClassification,
    _LockObservationClassification,
    _PayloadValidationClassification,
    _StableSnapshotClassification,
    _capture_entry_snapshot,
    _inspect_final_entry_structure,
    _observe_final_cache_entry,
    _observe_matching_lock,
    _read_and_parse_cache_document,
    _read_and_parse_final_entry_documents,
    _validate_final_entry_document_integrity,
    _validate_final_entry_payload,
    _validate_stable_entry_snapshot,
)
from .persistent_cache import (
    CacheNamespace,
    derive_entry_digest,
    derive_final_entry_path,
    derive_lock_path,
    derive_staging_entry_path,
)


class CacheRecoveryStatus(str, Enum):
    RECOVERY_UNSAFE = "recovery_unsafe"
    RECOVERY_UNSUPPORTED = "recovery_unsupported"
    RECOVERY_UNSTABLE = "recovery_unstable"
    RECOVERY_INVALID = "recovery_invalid"
    EMPTY = "empty"
    UNPUBLISHED_COMPLETE_STAGING = "unpublished_complete_staging"
    INCOMPLETE_STAGING = "incomplete_staging"
    COMPLETE_STAGING_WITH_ACTIVE_LOCK = "complete_staging_with_active_lock"
    COMPLETE_STAGING_WITH_STALE_LOCK = "complete_staging_with_stale_lock"
    INCOMPLETE_STAGING_WITH_ACTIVE_LOCK = "incomplete_staging_with_active_lock"
    INCOMPLETE_STAGING_WITH_STALE_LOCK = "incomplete_staging_with_stale_lock"
    FINAL_PUBLISHED = "final_published"
    FINAL_PUBLISHED_LOCK_RETAINED = "final_published_lock_retained"
    FINAL_PUBLISHED_WITH_SUPERSEDED_STAGING = "final_published_with_superseded_staging"
    FINAL_PUBLISHED_WITH_SUPERSEDED_STAGING_AND_LOCK = (
        "final_published_with_superseded_staging_and_lock"
    )
    ACTIVE_LOCK_WITHOUT_ENTRY = "active_lock_without_entry"
    STALE_LOCK_WITHOUT_ENTRY = "stale_lock_without_entry"


class CacheRecoveryReason(str, Enum):
    UNSAFE_ROOT = "unsafe_root"
    UNSAFE_STAGING_PATH = "unsafe_staging_path"
    UNSAFE_FINAL_PATH = "unsafe_final_path"
    UNSAFE_LOCK_PATH = "unsafe_lock_path"
    UNSUPPORTED_STAGING = "unsupported_staging"
    UNSUPPORTED_FINAL = "unsupported_final"
    UNSUPPORTED_LOCK = "unsupported_lock"
    UNSTABLE_ROOT = "unstable_root"
    UNSTABLE_STAGING = "unstable_staging"
    UNSTABLE_FINAL = "unstable_final"
    UNSTABLE_LOCK = "unstable_lock"
    INVALID_STAGING = "invalid_staging"
    INVALID_FINAL = "invalid_final"
    INVALID_LOCK = "invalid_lock"
    TRAVERSAL_LIMIT_EXCEEDED = "traversal_limit_exceeded"


class RecoverySubject(str, Enum):
    ROOT = "root"
    STAGING = "staging"
    FINAL = "final"
    LOCK = "lock"


class StagingRecoveryState(str, Enum):
    STAGING_ABSENT = "staging_absent"
    STAGING_COMPLETE_VALID = "staging_complete_valid"
    STAGING_INCOMPLETE = "staging_incomplete"
    STAGING_INVALID = "staging_invalid"
    STAGING_UNSUPPORTED = "staging_unsupported"
    STAGING_UNSAFE = "staging_unsafe"
    STAGING_UNSTABLE = "staging_unstable"
    STAGING_IO_FAILURE = "staging_io_failure"


class FinalRecoveryState(str, Enum):
    FINAL_ABSENT = "final_absent"
    FINAL_VALID = "final_valid"
    FINAL_INVALID = "final_invalid"
    FINAL_UNSUPPORTED = "final_unsupported"
    FINAL_UNSAFE = "final_unsafe"
    FINAL_UNSTABLE = "final_unstable"


class LockRecoveryState(str, Enum):
    LOCK_ABSENT = "lock_absent"
    LOCK_ACTIVE = "lock_active"
    LOCK_STALE = "lock_stale"
    LOCK_MALFORMED = "lock_malformed"
    LOCK_UNSUPPORTED = "lock_unsupported"
    LOCK_UNSAFE = "lock_unsafe"
    LOCK_UNSTABLE = "lock_unstable"
    LOCK_IO_FAILURE = "lock_io_failure"
    LOCK_IDENTITY_CONFLICT = "lock_identity_conflict"
    LOCK_TIMESTAMP_INVALID = "lock_timestamp_invalid"


@dataclass(frozen=True)
class RecoveryInspectionPolicy:
    max_staging_candidates_per_identity: int = 64
    max_staging_directory_entries: int = 4096
    max_contract_relative_path_utf8_bytes: int = 1024
    max_traversal_depth: int = 64
    max_diagnostics: int = 32

    def __post_init__(self) -> None:
        expected = (64, 4096, 1024, 64, 32)
        actual = (
            self.max_staging_candidates_per_identity,
            self.max_staging_directory_entries,
            self.max_contract_relative_path_utf8_bytes,
            self.max_traversal_depth,
            self.max_diagnostics,
        )
        if actual != expected or any(isinstance(value, bool) for value in actual):
            raise ValueError("Step 5E contract-v1 recovery limits are locked.")


@dataclass(frozen=True)
class CacheRecoveryInspectionRequest:
    cache_root: ValidatedCacheRoot
    namespace: CacheNamespace
    cache_key: CacheKey
    expectation: CacheLookupExpectation
    artifact_expectation: CacheArtifactExpectation | None
    payload_expectation: ProducerPayloadExpectation
    lookup_policy: CacheLookupVerificationPolicy
    lock_observation_policy: LockObservationPolicy
    recovery_policy: RecoveryInspectionPolicy = RecoveryInspectionPolicy()
    known_writer_token: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.cache_root, ValidatedCacheRoot):
            raise TypeError("cache_root must be a ValidatedCacheRoot.")
        if not isinstance(self.namespace, CacheNamespace):
            raise TypeError("namespace must be a CacheNamespace.")
        if not isinstance(self.cache_key, CacheKey):
            raise TypeError("cache_key must be a CacheKey.")
        if not isinstance(self.expectation, CacheLookupExpectation):
            raise TypeError("expectation must be a CacheLookupExpectation.")
        if self.expectation.namespace != self.namespace:
            raise ValueError("expectation namespace must equal request namespace.")
        if self.artifact_expectation is not None and not isinstance(
            self.artifact_expectation, CacheArtifactExpectation
        ):
            raise TypeError("artifact_expectation must be validated or None.")
        if not isinstance(self.payload_expectation, ProducerPayloadExpectation):
            raise TypeError("payload_expectation must be trusted producer semantics.")
        if not isinstance(self.lookup_policy, CacheLookupVerificationPolicy):
            raise TypeError("lookup_policy must be CacheLookupVerificationPolicy.")
        if not isinstance(self.lock_observation_policy, LockObservationPolicy):
            raise TypeError("lock_observation_policy must be LockObservationPolicy.")
        if not isinstance(self.recovery_policy, RecoveryInspectionPolicy):
            raise TypeError("recovery_policy must be RecoveryInspectionPolicy.")
        if self.known_writer_token is not None:
            derive_staging_entry_path(
                self.cache_root.resolved_path,
                self.namespace,
                self.cache_key,
                self.known_writer_token,
            )


@dataclass(frozen=True)
class StagingRecoveryObservation:
    candidate_index: int
    relative_contract_path: str
    classification: StagingRecoveryState | None = None

    def __post_init__(self) -> None:
        if isinstance(self.candidate_index, bool) or self.candidate_index < 0:
            raise ValueError("candidate_index must be non-negative.")
        if not self.relative_contract_path or Path(self.relative_contract_path).is_absolute():
            raise ValueError("staging observation path must be root-relative.")
        if self.classification is not None and not isinstance(
            self.classification, StagingRecoveryState
        ):
            raise TypeError("classification must be a StagingRecoveryState or None.")


@dataclass(frozen=True)
class FinalRecoveryObservation:
    relative_contract_path: str
    classification: FinalRecoveryState | None = None

    def __post_init__(self) -> None:
        if not self.relative_contract_path or Path(self.relative_contract_path).is_absolute():
            raise ValueError("final observation path must be root-relative.")
        if self.classification is not None and not isinstance(
            self.classification, FinalRecoveryState
        ):
            raise TypeError("classification must be a FinalRecoveryState or None.")


@dataclass(frozen=True)
class LockRecoveryObservation:
    relative_contract_path: str
    classification: LockRecoveryState | None = None

    def __post_init__(self) -> None:
        if not self.relative_contract_path or Path(self.relative_contract_path).is_absolute():
            raise ValueError("lock observation path must be root-relative.")
        if self.classification is not None and not isinstance(
            self.classification, LockRecoveryState
        ):
            raise TypeError("classification must be a LockRecoveryState or None.")


@dataclass(frozen=True)
class CacheRecoveryObservation:
    entry_digest: str
    staging: tuple[StagingRecoveryObservation, ...]
    final: FinalRecoveryObservation
    lock: LockRecoveryObservation
    status: CacheRecoveryStatus | None = None
    reason: CacheRecoveryReason | None = None

    def __post_init__(self) -> None:
        if self.status is not None or self.reason is not None:
            raise ValueError("Step 5E1 does not compose recovery classifications.")
        if tuple(item.candidate_index for item in self.staging) != tuple(
            range(len(self.staging))
        ):
            raise ValueError("staging observations must use deterministic indexes.")


@dataclass(frozen=True)
class _BoundedDirectoryListing:
    names: tuple[str, ...] | None
    limit_exceeded: bool
    identity: FileIdentity


@runtime_checkable
class RecoveryReadOnlyFilesystem(Protocol):
    def inspect(self, path: str | Path) -> FileIdentity: ...
    def resolve(self, path: str | Path) -> Path: ...
    def list_directory(self, path: str | Path) -> tuple[str, ...]: ...
    def read_regular_file_bounded(
        self, path: str | Path, *, max_bytes: int
    ) -> BoundedFileRead: ...
    def stream_regular_file_sha256(
        self, path: str | Path, *, declared_size: int, chunk_size: int
    ): ...
    def list_directory_bounded(
        self, path: str | Path, *, max_entries: int
    ) -> _BoundedDirectoryListing: ...


class LocalRecoveryReadOnlyFilesystem(LocalReadOnlyCacheFilesystem):
    """Read-only local adapter with stable, bounded directory enumeration."""

    def list_directory_bounded(
        self, path: str | Path, *, max_entries: int
    ) -> _BoundedDirectoryListing:
        if (
            isinstance(max_entries, bool)
            or not isinstance(max_entries, int)
            or max_entries <= 0
        ):
            raise ValueError("max_entries must be a positive integer.")
        pre = self.inspect(path)
        if pre.object_type is FilesystemObjectType.SYMLINK:
            raise SymlinkRejectedError("Recovery enumeration rejects symlinks.")
        if pre.object_type is not FilesystemObjectType.DIRECTORY:
            raise UnsupportedFilesystemObjectError(
                "Recovery enumeration requires a directory."
            )
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor: int | None = None
        try:
            descriptor = os.open(path, flags)
            opened = FileIdentity.from_stat(os.fstat(descriptor))
            if not pre.same_stable_object(opened):
                raise UnstableFilesystemObjectError(
                    "Recovery directory changed before enumeration."
                )
            names: list[str] = []
            with os.scandir(descriptor) as entries:
                for entry in entries:
                    names.append(entry.name)
                    if len(names) > max_entries:
                        break
            handle_after = FileIdentity.from_stat(os.fstat(descriptor))
        except FileNotFoundError:
            raise
        except (SymlinkRejectedError, UnstableFilesystemObjectError):
            raise
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise SymlinkRejectedError(
                    "Recovery enumeration rejects symlinks."
                ) from exc
            raise OSError("Recovery directory enumeration failed.") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
        try:
            post = self.inspect(path)
        except FileNotFoundError as exc:
            raise UnstableFilesystemObjectError(
                "Recovery directory disappeared during enumeration."
            ) from exc
        if not (
            pre.same_stable_object(opened)
            and opened.same_stable_object(handle_after)
            and handle_after.same_stable_object(post)
        ):
            raise UnstableFilesystemObjectError(
                "Recovery directory changed during enumeration."
            )
        exceeded = len(names) > max_entries
        return _BoundedDirectoryListing(
            None if exceeded else tuple(sorted(names)), exceeded, post
        )


DEFAULT_RECOVERY_READ_ONLY_FILESYSTEM = LocalRecoveryReadOnlyFilesystem()


class RecoveryTraversalError(RuntimeError):
    """Deterministic refusal while deriving the Step 5E1 traversal scope."""


class RecoveryTraversalLimitError(RecoveryTraversalError):
    """A locked Step 5E traversal ceiling was exceeded."""


@dataclass(frozen=True)
class _RecoveryTraversalFoundation:
    entry_digest: str
    final_path: Path
    lock_path: Path
    staging_namespace_path: Path
    staging_candidate_paths: tuple[Path, ...]
    observation: CacheRecoveryObservation


def _relative_path(root: Path, path: Path, policy: RecoveryInspectionPolicy) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise RecoveryTraversalError("Derived recovery path escaped cache root.") from exc
    text = relative.as_posix()
    if (
        len(text.encode("utf-8")) > policy.max_contract_relative_path_utf8_bytes
        or len(relative.parts) > policy.max_traversal_depth
    ):
        raise RecoveryTraversalLimitError("Derived recovery path exceeds contract limits.")
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise RecoveryTraversalError("Derived recovery path is not canonical.")
    return text


def _staging_namespace_path(request: CacheRecoveryInspectionRequest) -> Path:
    namespace = request.namespace
    return (
        request.cache_root.resolved_path
        / "staging"
        / "v1"
        / namespace.domain
        / namespace.producer_id
        / str(namespace.producer_schema_version)
    )


def _validate_existing_directory_chain(
    root: Path, directory: Path, filesystem: RecoveryReadOnlyFilesystem
) -> None:
    current = root
    for part in directory.relative_to(root).parts:
        current /= part
        identity = filesystem.inspect(current)
        if identity.object_type is not FilesystemObjectType.DIRECTORY:
            raise RecoveryTraversalError("Recovery traversal encountered an unsafe ancestor.")


def _sanitized_staging_relative(request: CacheRecoveryInspectionRequest) -> str:
    directory = _staging_namespace_path(request).relative_to(
        request.cache_root.resolved_path
    )
    return (directory / f"{derive_entry_digest(request.cache_key)}.<writer-token>").as_posix()


def _prepare_recovery_inspection(
    request: CacheRecoveryInspectionRequest,
    *,
    filesystem: RecoveryReadOnlyFilesystem = DEFAULT_RECOVERY_READ_ONLY_FILESYSTEM,
) -> _RecoveryTraversalFoundation:
    """Derive one bounded traversal plan without validating cache contents."""

    if not isinstance(request, CacheRecoveryInspectionRequest):
        raise TypeError("request must be CacheRecoveryInspectionRequest.")
    if not isinstance(filesystem, RecoveryReadOnlyFilesystem):
        raise TypeError("filesystem must implement RecoveryReadOnlyFilesystem.")
    root = request.cache_root.resolved_path
    try:
        root_now = filesystem.inspect(root)
    except OSError as exc:
        raise RecoveryTraversalError("Validated cache root cannot be observed.") from exc
    if not request.cache_root.identity.same_stable_object(root_now):
        raise RecoveryTraversalError("Validated cache root identity changed.")

    final_path = derive_final_entry_path(root, request.namespace, request.cache_key)
    lock_path = derive_lock_path(root, request.namespace, request.cache_key)
    staging_namespace = _staging_namespace_path(request)
    for path in (final_path, lock_path, staging_namespace):
        _relative_path(root, path, request.recovery_policy)

    candidates: tuple[Path, ...]
    if request.known_writer_token is not None:
        candidate = derive_staging_entry_path(
            root, request.namespace, request.cache_key, request.known_writer_token
        )
        _relative_path(root, candidate, request.recovery_policy)
        candidates = (candidate,)
    else:
        try:
            _validate_existing_directory_chain(root, staging_namespace, filesystem)
            listing = filesystem.list_directory_bounded(
                staging_namespace,
                max_entries=request.recovery_policy.max_staging_directory_entries,
            )
        except FileNotFoundError:
            listing = None
        if listing is None:
            candidates = ()
        else:
            if listing.limit_exceeded or listing.names is None:
                raise RecoveryTraversalLimitError(
                    "Staging namespace enumeration exceeds the entry limit."
                )
            prefix = derive_entry_digest(request.cache_key) + "."
            selected: list[Path] = []
            for name in listing.names:
                if not name.startswith(prefix):
                    continue
                token = name[len(prefix):]
                try:
                    candidate = derive_staging_entry_path(
                        root, request.namespace, request.cache_key, token
                    )
                except (TypeError, ValueError) as exc:
                    raise RecoveryTraversalError(
                        "Matching staging candidate name is not canonical."
                    ) from exc
                if candidate.parent != staging_namespace or candidate.name != name:
                    raise RecoveryTraversalError(
                        "Matching staging candidate failed exact reconstruction."
                    )
                _relative_path(root, candidate, request.recovery_policy)
                selected.append(candidate)
            if len(selected) > request.recovery_policy.max_staging_candidates_per_identity:
                raise RecoveryTraversalLimitError(
                    "Matching staging candidates exceed the contract limit."
                )
            candidates = tuple(selected)

    root_after = filesystem.inspect(root)
    if not request.cache_root.identity.same_stable_object(root_after):
        raise RecoveryTraversalError("Validated cache root changed during traversal.")

    staging_observations = tuple(
        StagingRecoveryObservation(index, _sanitized_staging_relative(request))
        for index, _ in enumerate(candidates)
    )
    observation = CacheRecoveryObservation(
        derive_entry_digest(request.cache_key),
        staging_observations,
        FinalRecoveryObservation(
            _relative_path(root, final_path, request.recovery_policy)
        ),
        LockRecoveryObservation(
            _relative_path(root, lock_path, request.recovery_policy)
        ),
    )
    return _RecoveryTraversalFoundation(
        observation.entry_digest,
        final_path,
        lock_path,
        staging_namespace,
        candidates,
        observation,
    )


_UNSUPPORTED_DOCUMENTS = frozenset(
    classification
    for classification in _CacheDocumentClassification
    if classification.name.startswith("UNSUPPORTED_")
)


def _lookup_request(request: CacheRecoveryInspectionRequest) -> CacheLookupRequest:
    return CacheLookupRequest(
        request.cache_root,
        request.namespace,
        request.cache_key,
        request.expectation,
        request.artifact_expectation,
        request.payload_expectation,
        request.lookup_policy,
        request.lock_observation_policy,
    )


def _observe_final_component(
    request: CacheRecoveryInspectionRequest,
    *,
    filesystem: RecoveryReadOnlyFilesystem,
) -> FinalRecoveryObservation:
    result = _observe_final_cache_entry(
        _lookup_request(request), filesystem=filesystem
    )
    if result.status is CacheLookupStatus.MISS:
        state = FinalRecoveryState.FINAL_ABSENT
    elif result.status is CacheLookupStatus.HIT:
        state = FinalRecoveryState.FINAL_VALID
    elif result.status is CacheLookupStatus.UNSUPPORTED_VERSION:
        state = FinalRecoveryState.FINAL_UNSUPPORTED
    elif result.status is CacheLookupStatus.UNSAFE_PATH:
        state = FinalRecoveryState.FINAL_UNSAFE
    elif result.reason is CacheLookupReason.UNSTABLE_SNAPSHOT:
        state = FinalRecoveryState.FINAL_UNSTABLE
    else:
        state = FinalRecoveryState.FINAL_INVALID
    relative = _relative_path(
        request.cache_root.resolved_path,
        result.expected_entry_path,
        request.recovery_policy,
    )
    return FinalRecoveryObservation(relative, state)


def _incomplete_staging_state(
    path: Path,
    names: tuple[str, ...],
    request: CacheRecoveryInspectionRequest,
    filesystem: RecoveryReadOnlyFilesystem,
) -> StagingRecoveryState:
    expected = {"COMPLETE", "metadata.json", "manifest.json", "payload"}
    if set(names) - expected or "COMPLETE" in names:
        return StagingRecoveryState.STAGING_INVALID
    for name in names:
        identity = filesystem.inspect(path / name)
        expected_type = (
            FilesystemObjectType.DIRECTORY
            if name == "payload"
            else FilesystemObjectType.REGULAR_FILE
        )
        if identity.object_type is not expected_type:
            return StagingRecoveryState.STAGING_UNSAFE
    for document_name in (_CacheDocumentName.METADATA, _CacheDocumentName.MANIFEST):
        if document_name.value not in names:
            continue
        observed = _read_and_parse_cache_document(
            path,
            document_name,
            policy=request.lookup_policy,
            filesystem=filesystem,
        )
        if not observed.stable_read:
            return StagingRecoveryState.STAGING_UNSTABLE
        if observed.classification in _UNSUPPORTED_DOCUMENTS:
            return StagingRecoveryState.STAGING_UNSUPPORTED
        if observed.classification is not _CacheDocumentClassification.VALID:
            return StagingRecoveryState.STAGING_INVALID
    return StagingRecoveryState.STAGING_INCOMPLETE


def _observe_staging_candidate(
    request: CacheRecoveryInspectionRequest,
    path: Path,
    *,
    candidate_index: int,
    absence_is_stable: bool,
    filesystem: RecoveryReadOnlyFilesystem,
) -> StagingRecoveryObservation:
    relative = _sanitized_staging_relative(request)
    try:
        identity = filesystem.inspect(path)
    except FileNotFoundError:
        state = (
            StagingRecoveryState.STAGING_ABSENT
            if absence_is_stable
            else StagingRecoveryState.STAGING_UNSTABLE
        )
        return StagingRecoveryObservation(candidate_index, relative, state)
    except (CacheLookupPermissionError, CacheLookupIOError, OSError):
        return StagingRecoveryObservation(
            candidate_index, relative, StagingRecoveryState.STAGING_IO_FAILURE
        )
    if identity.object_type is not FilesystemObjectType.DIRECTORY:
        return StagingRecoveryObservation(
            candidate_index, relative, StagingRecoveryState.STAGING_UNSAFE
        )
    try:
        structure = _inspect_final_entry_structure(path, filesystem=filesystem)
        if structure.classification is _FinalEntryStructureClassification.UNSAFE_OBJECT:
            state = StagingRecoveryState.STAGING_UNSAFE
        elif structure.classification is _FinalEntryStructureClassification.UNEXPECTED_TOP_LEVEL_OBJECT:
            state = StagingRecoveryState.STAGING_INVALID
        elif structure.classification is _FinalEntryStructureClassification.INCOMPLETE_ENTRY:
            state = _incomplete_staging_state(
                path, structure.observed_names, request, filesystem
            )
        elif structure.classification is _FinalEntryStructureClassification.ENTRY_ABSENT:
            state = StagingRecoveryState.STAGING_UNSTABLE
        else:
            before = _capture_entry_snapshot(
                request.cache_root, path, filesystem=filesystem
            )
            documents = _read_and_parse_final_entry_documents(
                path, policy=request.lookup_policy, filesystem=filesystem
            )
            if documents.classification in _UNSUPPORTED_DOCUMENTS:
                state = StagingRecoveryState.STAGING_UNSUPPORTED
            elif documents.classification is not _CacheDocumentClassification.VALID:
                state = StagingRecoveryState.STAGING_INVALID
            else:
                integrity = _validate_final_entry_document_integrity(
                    request.cache_root.resolved_path,
                    path,
                    documents,
                    cache_key=request.cache_key,
                    namespace=request.namespace,
                    expectation=request.expectation,
                    artifact_expectation=request.artifact_expectation,
                    expected_entry_path=path,
                )
                if integrity.classification.name != "VALID":
                    state = StagingRecoveryState.STAGING_INVALID
                else:
                    payload = _validate_final_entry_payload(
                        path,
                        integrity,
                        payload_expectation=request.payload_expectation,
                        policy=request.lookup_policy,
                        filesystem=filesystem,
                    )
                    if payload.classification is _PayloadValidationClassification.UNSAFE_OBJECT:
                        state = StagingRecoveryState.STAGING_UNSAFE
                    elif payload.classification is _PayloadValidationClassification.PAYLOAD_READ_UNSTABLE:
                        state = StagingRecoveryState.STAGING_UNSTABLE
                    elif payload.classification is not _PayloadValidationClassification.VALID:
                        state = StagingRecoveryState.STAGING_INVALID
                    else:
                        stable = _validate_stable_entry_snapshot(
                            request.cache_root,
                            path,
                            before,
                            documents,
                            payload,
                            filesystem=filesystem,
                            observer=None,
                        )
                        state = (
                            StagingRecoveryState.STAGING_COMPLETE_VALID
                            if stable.classification
                            is _StableSnapshotClassification.VALID
                            else StagingRecoveryState.STAGING_UNSTABLE
                        )
    except (SymlinkRejectedError, UnsupportedFilesystemObjectError):
        state = StagingRecoveryState.STAGING_UNSAFE
    except (FileNotFoundError, UnstableFilesystemObjectError):
        state = StagingRecoveryState.STAGING_UNSTABLE
    except (CacheLookupPermissionError, CacheLookupIOError, OSError):
        state = StagingRecoveryState.STAGING_IO_FAILURE
    return StagingRecoveryObservation(candidate_index, relative, state)


_LOCK_STATE_MAP = {
    _LockObservationClassification.ABSENT: LockRecoveryState.LOCK_ABSENT,
    _LockObservationClassification.ACTIVE: LockRecoveryState.LOCK_ACTIVE,
    _LockObservationClassification.STALE: LockRecoveryState.LOCK_STALE,
    _LockObservationClassification.MALFORMED_LOCK: LockRecoveryState.LOCK_MALFORMED,
    _LockObservationClassification.UNSUPPORTED_LOCK_VERSION: LockRecoveryState.LOCK_UNSUPPORTED,
    _LockObservationClassification.UNSAFE_OBJECT: LockRecoveryState.LOCK_UNSAFE,
    _LockObservationClassification.UNSTABLE_SNAPSHOT: LockRecoveryState.LOCK_UNSTABLE,
    _LockObservationClassification.IO_FAILURE: LockRecoveryState.LOCK_IO_FAILURE,
    _LockObservationClassification.LOCK_IDENTITY_CONFLICT: LockRecoveryState.LOCK_IDENTITY_CONFLICT,
    _LockObservationClassification.LOCK_TIMESTAMP_INVALID: LockRecoveryState.LOCK_TIMESTAMP_INVALID,
}


def _observe_lock_component(
    request: CacheRecoveryInspectionRequest,
    *,
    filesystem: RecoveryReadOnlyFilesystem,
    lock_clock: LockObservationClock,
) -> LockRecoveryObservation:
    lock = _observe_matching_lock(
        request.cache_root,
        request.namespace,
        request.cache_key,
        policy=request.lock_observation_policy,
        clock=lock_clock,
        filesystem=filesystem,
    )
    relative = _relative_path(
        request.cache_root.resolved_path,
        lock.lock_path,
        request.recovery_policy,
    )
    return LockRecoveryObservation(relative, _LOCK_STATE_MAP[lock.classification])


def _observe_recovery_components(
    request: CacheRecoveryInspectionRequest,
    *,
    filesystem: RecoveryReadOnlyFilesystem = DEFAULT_RECOVERY_READ_ONLY_FILESYSTEM,
    lock_clock: LockObservationClock = SYSTEM_LOCK_OBSERVATION_CLOCK,
) -> CacheRecoveryObservation:
    """Privately observe components without composing lifecycle status or diagnostics."""

    traversal = _prepare_recovery_inspection(request, filesystem=filesystem)
    if traversal.staging_candidate_paths:
        staging = tuple(
            _observe_staging_candidate(
                request,
                path,
                candidate_index=index,
                absence_is_stable=request.known_writer_token is not None,
                filesystem=filesystem,
            )
            for index, path in enumerate(traversal.staging_candidate_paths)
        )
    else:
        staging = (
            StagingRecoveryObservation(
                0,
                _sanitized_staging_relative(request),
                StagingRecoveryState.STAGING_ABSENT,
            ),
        )
    final = _observe_final_component(request, filesystem=filesystem)
    lock = _observe_lock_component(
        request, filesystem=filesystem, lock_clock=lock_clock
    )
    return CacheRecoveryObservation(
        traversal.entry_digest,
        staging,
        final,
        lock,
    )
