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
    CacheLookupExpectation,
    CacheLookupVerificationPolicy,
    FileIdentity,
    FilesystemObjectType,
    LocalReadOnlyCacheFilesystem,
    LockObservationPolicy,
    ProducerPayloadExpectation,
    SymlinkRejectedError,
    UnstableFilesystemObjectError,
    UnsupportedFilesystemObjectError,
    ValidatedCacheRoot,
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
    classification: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.candidate_index, bool) or self.candidate_index < 0:
            raise ValueError("candidate_index must be non-negative.")
        if not self.relative_contract_path or Path(self.relative_contract_path).is_absolute():
            raise ValueError("staging observation path must be root-relative.")
        if self.classification is not None:
            raise ValueError("Step 5E1 does not classify staging observations.")


@dataclass(frozen=True)
class FinalRecoveryObservation:
    relative_contract_path: str
    classification: str | None = None

    def __post_init__(self) -> None:
        if not self.relative_contract_path or Path(self.relative_contract_path).is_absolute():
            raise ValueError("final observation path must be root-relative.")
        if self.classification is not None:
            raise ValueError("Step 5E1 does not classify final observations.")


@dataclass(frozen=True)
class LockRecoveryObservation:
    relative_contract_path: str
    classification: str | None = None

    def __post_init__(self) -> None:
        if not self.relative_contract_path or Path(self.relative_contract_path).is_absolute():
            raise ValueError("lock observation path must be root-relative.")
        if self.classification is not None:
            raise ValueError("Step 5E1 does not classify lock observations.")


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
