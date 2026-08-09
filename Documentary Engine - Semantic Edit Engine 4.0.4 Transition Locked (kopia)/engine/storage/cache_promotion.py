"""Step 5D ownership-safe locking and atomic staging promotion."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import stat
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Protocol, runtime_checkable

from .cache_keys import CacheKey
from .cache_lookup import (
    CacheLookupFilesystemError,
    CacheLookupVerificationPolicy,
    FileIdentity,
    FilesystemObjectType,
    LocalReadOnlyCacheFilesystem,
    SymlinkRejectedError,
    UnstableFilesystemObjectError,
    UnsupportedFilesystemObjectError,
    ValidatedCacheRoot,
)
from .cache_writer import StagedCacheEntryReference
from .persistent_cache import (
    CacheEntryMetadata,
    CacheKeyReference,
    CacheNamespace,
    CompletenessMarker,
    PayloadManifest,
    canonical_json_bytes,
    derive_entry_digest,
    derive_final_entry_path,
    derive_lock_path,
    derive_staging_entry_path,
    parse_canonical_json,
)

MAX_LOCK_DOCUMENT_BYTES = 16 * 1024


class CachePromotionStatus(str, Enum):
    LOCK_ACQUIRED = "lock_acquired"
    PROMOTED_AND_RELEASED = "promoted_and_released"
    PROMOTED_LOCK_RETAINED = "promoted_lock_retained"
    LOCK_ALREADY_EXISTS = "lock_already_exists"
    UNSAFE_LOCK_PATH = "unsafe_lock_path"
    UNSTABLE_LOCK_PATH = "unstable_lock_path"
    LOCK_IO_FAILURE = "lock_io_failure"
    OWNERSHIP_LOST = "ownership_lost"
    STAGING_INVALID = "staging_invalid"
    UNSAFE_STAGING_PATH = "unsafe_staging_path"
    UNSTABLE_STAGING_PATH = "unstable_staging_path"
    FINAL_PATH_OCCUPIED = "final_path_occupied"
    FINAL_PATH_OCCUPIED_RACE = "final_path_occupied_race"
    UNSAFE_FINAL_PATH = "unsafe_final_path"
    UNSTABLE_FINAL_PATH = "unstable_final_path"
    SAME_FILESYSTEM_CAPABILITY_UNAVAILABLE = "same_filesystem_capability_unavailable"
    CROSS_FILESYSTEM = "cross_filesystem"
    CROSS_FILESYSTEM_RACE = "cross_filesystem_race"
    PROMOTION_CAPABILITY_UNAVAILABLE = "promotion_capability_unavailable"
    PROMOTION_IO_FAILURE = "promotion_io_failure"
    PROMOTED_OUTCOME_UNCERTAIN = "promoted_outcome_uncertain"


class LockReleaseStatus(str, Enum):
    RELEASED = "released"
    ALREADY_ABSENT = "already_absent"
    OWNERSHIP_LOST = "ownership_lost"
    UNSAFE_LOCK_PATH = "unsafe_lock_path"
    UNSTABLE_LOCK_PATH = "unstable_lock_path"
    RELEASE_IO_FAILURE = "release_io_failure"
    RELEASE_CAPABILITY_UNAVAILABLE = "release_capability_unavailable"


class LockRefreshStatus(str, Enum):
    REFRESHED = "refreshed"
    OWNERSHIP_LOST = "ownership_lost"
    INVALID_CLOCK = "invalid_clock"
    REFRESH_CAPABILITY_UNAVAILABLE = "refresh_capability_unavailable"
    REFRESH_IO_FAILURE = "refresh_io_failure"


@runtime_checkable
class WriterLockClock(Protocol):
    def now_utc(self) -> datetime: ...


class _SystemWriterLockClock:
    def now_utc(self) -> datetime:
        return datetime.now(timezone.utc).replace(microsecond=0)


SYSTEM_WRITER_LOCK_CLOCK = _SystemWriterLockClock()


@runtime_checkable
class WriterOwnerTokenSource(Protocol):
    def fresh_token(self) -> str: ...


class _UUID4OwnerTokenSource:
    def fresh_token(self) -> str:
        return uuid.uuid4().hex


UUID4_OWNER_TOKEN_SOURCE = _UUID4OwnerTokenSource()


@dataclass(frozen=True)
class WriterOwnerMetadata:
    host_id: str
    process_id: int

    def __post_init__(self) -> None:
        _validate_token(self.host_id, "host_id")
        if isinstance(self.process_id, bool) or not isinstance(self.process_id, int) or not 1 <= self.process_id <= 2**63 - 1:
            raise ValueError("process_id must be in 1..2^63-1.")


@dataclass(frozen=True)
class CachePromotionRequest:
    cache_root: ValidatedCacheRoot
    namespace: CacheNamespace
    cache_key: CacheKey
    writer_token: str
    staged_entry: StagedCacheEntryReference
    owner: WriterOwnerMetadata
    policy: CacheLookupVerificationPolicy = CacheLookupVerificationPolicy()

    def __post_init__(self) -> None:
        if not isinstance(self.cache_root, ValidatedCacheRoot):
            raise TypeError("cache_root must be a ValidatedCacheRoot.")
        if not isinstance(self.namespace, CacheNamespace) or not isinstance(self.cache_key, CacheKey):
            raise TypeError("namespace and cache_key must be validated models.")
        if not isinstance(self.staged_entry, StagedCacheEntryReference):
            raise TypeError("staged_entry must be a StagedCacheEntryReference.")
        if not isinstance(self.owner, WriterOwnerMetadata):
            raise TypeError("owner must be WriterOwnerMetadata.")
        derive_staging_entry_path(self.cache_root.resolved_path, self.namespace, self.cache_key, self.writer_token)


@dataclass(frozen=True)
class WriterLockDocument:
    entry_digest: str
    acquired_at_utc: str
    heartbeat_at_utc: str
    owner_token: str
    host_id: str
    process_id: int
    lock_version: int = 1

    def __post_init__(self) -> None:
        if self.lock_version != 1 or isinstance(self.lock_version, bool):
            raise ValueError("lock_version must be 1.")
        if len(self.entry_digest) != 64 or any(c not in "0123456789abcdef" for c in self.entry_digest):
            raise ValueError("entry_digest must be 64 lowercase hexadecimal characters.")
        _validate_token(self.owner_token, "owner_token")
        _validate_token(self.host_id, "host_id")
        if isinstance(self.process_id, bool) or not isinstance(self.process_id, int) or not 1 <= self.process_id <= 2**63 - 1:
            raise ValueError("process_id is out of range.")
        acquired = _parse_timestamp(self.acquired_at_utc)
        heartbeat = _parse_timestamp(self.heartbeat_at_utc)
        if acquired > heartbeat:
            raise ValueError("acquired_at_utc must not follow heartbeat_at_utc.")

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes({
            "acquired_at_utc": self.acquired_at_utc, "entry_digest": self.entry_digest,
            "heartbeat_at_utc": self.heartbeat_at_utc, "host_id": self.host_id,
            "lock_version": 1, "owner_token": self.owner_token,
            "process_id": self.process_id,
        })

    @classmethod
    def from_json(cls, data: bytes) -> "WriterLockDocument":
        value = parse_canonical_json(data)
        if type(value) is not dict or set(value) != {"acquired_at_utc", "entry_digest", "heartbeat_at_utc", "host_id", "lock_version", "owner_token", "process_id"}:
            raise ValueError("lock document has incorrect fields.")
        return cls(value["entry_digest"], value["acquired_at_utc"], value["heartbeat_at_utc"], value["owner_token"], value["host_id"], value["process_id"], value["lock_version"])


@dataclass(frozen=True)
class OwnedWriterLock:
    cache_root: ValidatedCacheRoot
    path: Path
    entry_digest: str
    owner_token: str
    document: WriterLockDocument
    identity: FileIdentity
    ancestor_identities: tuple[FileIdentity, ...]


@dataclass(frozen=True)
class PromotedCacheEntryReference:
    final_path: Path
    entry_digest: str
    namespace: CacheNamespace
    cache_key_reference: CacheKeyReference
    metadata: CacheEntryMetadata
    manifest: PayloadManifest
    marker: CompletenessMarker
    payload_file_count: int
    payload_total_bytes: int


@dataclass(frozen=True)
class CachePromotionResult:
    status: CachePromotionStatus
    promoted_entry: PromotedCacheEntryReference | None = None
    owned_lock: OwnedWriterLock | None = None
    release_status: LockReleaseStatus | None = None


@runtime_checkable
class CachePromotionFilesystem(Protocol):
    def inspect(self, path: Path) -> FileIdentity: ...
    def list_directory(self, path: Path) -> tuple[str, ...]: ...
    def read_regular_file_bounded(self, path: Path, *, max_bytes: int): ...
    def stream_regular_file_sha256(self, path: Path, *, declared_size: int, chunk_size: int): ...
    def create_lock_exclusive(self, path: Path, data: bytes) -> FileIdentity: ...
    def flush_directory(self, path: Path) -> None: ...
    def supports_atomic_noreplace_rename(self) -> bool: ...
    def supports_identity_conditional_unlink(self) -> bool: ...
    def supports_identity_conditional_replace(self) -> bool: ...
    def rename_directory_noreplace(self, source: Path, destination: Path) -> None: ...
    def unlink_if_same_identity(self, path: Path, expected: FileIdentity) -> bool | None: ...
    def replace_if_same_identity(self, original: Path, replacement: Path, expected: FileIdentity) -> FileIdentity | None: ...


class LocalCachePromotionFilesystem(LocalReadOnlyCacheFilesystem):
    """Local mandatory Step 5D capabilities; optional conditional mutation fails closed."""

    def create_lock_exclusive(self, path: Path, data: bytes) -> FileIdentity:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        try:
            view = memoryview(data)
            while view:
                count = os.write(fd, view)
                if count <= 0:
                    raise OSError(errno.EIO, "lock write made no progress")
                view = view[count:]
            os.fsync(fd)
            identity = FileIdentity.from_stat(os.fstat(fd))
        finally:
            os.close(fd)
        self.flush_directory(path.parent)
        return identity

    def flush_directory(self, path: Path) -> None:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def supports_atomic_noreplace_rename(self) -> bool:
        return sys.platform == "darwin"

    def supports_identity_conditional_unlink(self) -> bool:
        return False

    def supports_identity_conditional_replace(self) -> bool:
        return False

    def rename_directory_noreplace(self, source: Path, destination: Path) -> None:
        if sys.platform != "darwin":
            raise NotImplementedError("atomic no-replace directory rename unavailable")
        libc = ctypes.CDLL(None, use_errno=True)
        renameatx_np = libc.renameatx_np
        renameatx_np.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        renameatx_np.restype = ctypes.c_int
        if renameatx_np(-2, os.fsencode(source), -2, os.fsencode(destination), 0x00000004) != 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), str(destination))

    def unlink_if_same_identity(self, path: Path, expected: FileIdentity) -> bool | None:
        return None

    def replace_if_same_identity(self, original: Path, replacement: Path, expected: FileIdentity) -> FileIdentity | None:
        return None


DEFAULT_CACHE_PROMOTION_FILESYSTEM = LocalCachePromotionFilesystem()


def _validate_token(value: object, field: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 128 or not value.isascii() or not value[0].isalnum() or not value[-1].isalnum() or any(not (c.isalnum() or c in "._-") for c in value):
        raise ValueError(f"{field} must be a canonical opaque token.")
    return value


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or len(value) != 20 or value[4] != "-" or value[7] != "-" or value[10] != "T" or value[13] != ":" or value[16] != ":" or value[19] != "Z":
        raise ValueError("timestamp must use YYYY-MM-DDTHH:MM:SSZ.")
    parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    if parsed.year < 1970:
        raise ValueError("timestamp is before contract range.")
    return parsed


def _clock_text(clock: WriterLockClock) -> str:
    if not isinstance(clock, WriterLockClock):
        raise TypeError("clock must implement WriterLockClock.")
    value = clock.now_utc()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value) or value.microsecond != 0 or not 1970 <= value.year <= 9999:
        raise ValueError("clock must return a whole-second UTC datetime.")
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_directory_chain(filesystem: CachePromotionFilesystem, root: Path, directory: Path) -> tuple[FileIdentity, ...]:
    relative = directory.relative_to(root)
    identities = []
    current = root
    for part in relative.parts:
        current /= part
        identity = filesystem.inspect(current)
        if identity.object_type is not FilesystemObjectType.DIRECTORY:
            raise ValueError("unsafe directory chain")
        identities.append(identity)
    return tuple(identities)


def _same_creation_identity(first: FileIdentity, second: FileIdentity) -> bool:
    """Compare the stable object IDs that survive a same-filesystem rename."""
    return (
        first.object_type is second.object_type
        and first.device_id is not None
        and first.file_id is not None
        and (first.device_id, first.file_id) == (second.device_id, second.file_id)
    )


def _root_is_stable(request: CachePromotionRequest, filesystem: CachePromotionFilesystem) -> bool:
    try:
        return request.cache_root.identity.same_stable_object(
            filesystem.inspect(request.cache_root.resolved_path)
        )
    except (FileNotFoundError, OSError, CacheLookupFilesystemError):
        return False


def acquire_writer_lock(request: CachePromotionRequest, *, filesystem: CachePromotionFilesystem = DEFAULT_CACHE_PROMOTION_FILESYSTEM, clock: WriterLockClock = SYSTEM_WRITER_LOCK_CLOCK, token_source: WriterOwnerTokenSource = UUID4_OWNER_TOKEN_SOURCE) -> tuple[CachePromotionStatus, OwnedWriterLock | None]:
    if not isinstance(request, CachePromotionRequest):
        raise TypeError("request must be CachePromotionRequest.")
    if not isinstance(filesystem, CachePromotionFilesystem) or not isinstance(token_source, WriterOwnerTokenSource):
        raise TypeError("invalid Step 5D dependency.")
    token = _validate_token(token_source.fresh_token(), "owner_token")
    timestamp = _clock_text(clock)
    digest = derive_entry_digest(request.cache_key)
    document = WriterLockDocument(digest, timestamp, timestamp, token, request.owner.host_id, request.owner.process_id)
    data = document.canonical_bytes()
    if len(data) > MAX_LOCK_DOCUMENT_BYTES:
        raise ValueError("canonical lock exceeds 16 KiB.")
    path = derive_lock_path(request.cache_root.resolved_path, request.namespace, request.cache_key)
    if not _root_is_stable(request, filesystem):
        return CachePromotionStatus.UNSTABLE_LOCK_PATH, None
    try:
        ancestors_before = _safe_directory_chain(
            filesystem, request.cache_root.resolved_path, path.parent
        )
    except FileNotFoundError:
        return CachePromotionStatus.LOCK_IO_FAILURE, None
    except ValueError:
        return CachePromotionStatus.UNSAFE_LOCK_PATH, None
    except (OSError, CacheLookupFilesystemError):
        return CachePromotionStatus.UNSTABLE_LOCK_PATH, None
    try:
        identity = filesystem.create_lock_exclusive(path, data)
    except FileExistsError:
        try:
            existing = filesystem.inspect(path)
        except (FileNotFoundError, OSError, CacheLookupFilesystemError):
            return CachePromotionStatus.UNSTABLE_LOCK_PATH, None
        return (
            CachePromotionStatus.LOCK_ALREADY_EXISTS
            if existing.object_type is FilesystemObjectType.REGULAR_FILE
            else CachePromotionStatus.UNSAFE_LOCK_PATH
        ), None
    except (IsADirectoryError, NotADirectoryError):
        return CachePromotionStatus.UNSAFE_LOCK_PATH, None
    except OSError:
        return CachePromotionStatus.LOCK_IO_FAILURE, None
    if identity.object_type is not FilesystemObjectType.REGULAR_FILE:
        return CachePromotionStatus.UNSTABLE_LOCK_PATH, None
    try:
        ancestors_after = _safe_directory_chain(
            filesystem, request.cache_root.resolved_path, path.parent
        )
    except (FileNotFoundError, ValueError, OSError, CacheLookupFilesystemError):
        return CachePromotionStatus.UNSTABLE_LOCK_PATH, None
    if len(ancestors_before) != len(ancestors_after) or any(
        not _same_creation_identity(before, after)
        for before, after in zip(ancestors_before, ancestors_after)
    ):
        return CachePromotionStatus.UNSTABLE_LOCK_PATH, None
    owned = OwnedWriterLock(
        request.cache_root, path, digest, token, document, identity,
        ancestors_after,
    )
    return (CachePromotionStatus.OWNERSHIP_LOST, None) if not verify_owned_writer_lock(owned, filesystem=filesystem) else (CachePromotionStatus.LOCK_ACQUIRED, owned)


def verify_owned_writer_lock(lock: OwnedWriterLock, *, filesystem: CachePromotionFilesystem = DEFAULT_CACHE_PROMOTION_FILESYSTEM) -> bool:
    try:
        if not lock.cache_root.identity.same_stable_object(filesystem.inspect(lock.cache_root.resolved_path)):
            return False
        ancestors = _safe_directory_chain(filesystem, lock.cache_root.resolved_path, lock.path.parent)
        if len(ancestors) != len(lock.ancestor_identities) or any(
            not _same_creation_identity(before, after)
            for before, after in zip(lock.ancestor_identities, ancestors)
        ):
            return False
        read = filesystem.read_regular_file_bounded(lock.path, max_bytes=MAX_LOCK_DOCUMENT_BYTES)
        if read.limit_exceeded or not read.stable_read or read.data is None or not lock.identity.same_stable_object(read.post_read_identity):
            return False
        document = WriterLockDocument.from_json(read.data)
        return document == lock.document and document.entry_digest == lock.entry_digest and document.owner_token == lock.owner_token
    except (OSError, ValueError, CacheLookupFilesystemError):
        return False


def release_owned_writer_lock(lock: OwnedWriterLock, *, filesystem: CachePromotionFilesystem = DEFAULT_CACHE_PROMOTION_FILESYSTEM) -> LockReleaseStatus:
    try:
        if not lock.cache_root.identity.same_stable_object(filesystem.inspect(lock.cache_root.resolved_path)):
            return LockReleaseStatus.UNSTABLE_LOCK_PATH
        ancestors = _safe_directory_chain(filesystem, lock.cache_root.resolved_path, lock.path.parent)
        if len(ancestors) != len(lock.ancestor_identities) or any(
            not _same_creation_identity(before, after)
            for before, after in zip(lock.ancestor_identities, ancestors)
        ):
            return LockReleaseStatus.UNSTABLE_LOCK_PATH
        identity = filesystem.inspect(lock.path)
    except FileNotFoundError:
        return LockReleaseStatus.ALREADY_ABSENT
    except ValueError:
        return LockReleaseStatus.UNSAFE_LOCK_PATH
    except (OSError, CacheLookupFilesystemError):
        return LockReleaseStatus.RELEASE_IO_FAILURE
    if identity.object_type is not FilesystemObjectType.REGULAR_FILE:
        return LockReleaseStatus.UNSAFE_LOCK_PATH
    if not verify_owned_writer_lock(lock, filesystem=filesystem):
        return LockReleaseStatus.OWNERSHIP_LOST
    if not filesystem.supports_identity_conditional_unlink():
        return LockReleaseStatus.RELEASE_CAPABILITY_UNAVAILABLE
    try:
        removed = filesystem.unlink_if_same_identity(lock.path, lock.identity)
        if removed is None:
            return LockReleaseStatus.RELEASE_CAPABILITY_UNAVAILABLE
        if not removed:
            return LockReleaseStatus.OWNERSHIP_LOST
        filesystem.flush_directory(lock.path.parent)
        return LockReleaseStatus.RELEASED
    except (OSError, CacheLookupFilesystemError):
        return LockReleaseStatus.RELEASE_IO_FAILURE


def refresh_owned_lock(lock: OwnedWriterLock, *, filesystem: CachePromotionFilesystem = DEFAULT_CACHE_PROMOTION_FILESYSTEM, clock: WriterLockClock = SYSTEM_WRITER_LOCK_CLOCK, nonce_source: WriterOwnerTokenSource = UUID4_OWNER_TOKEN_SOURCE) -> tuple[LockRefreshStatus, OwnedWriterLock | None]:
    if not verify_owned_writer_lock(lock, filesystem=filesystem):
        return LockRefreshStatus.OWNERSHIP_LOST, None
    if not filesystem.supports_identity_conditional_replace():
        return LockRefreshStatus.REFRESH_CAPABILITY_UNAVAILABLE, None
    timestamp = _clock_text(clock)
    if _parse_timestamp(timestamp) < _parse_timestamp(lock.document.heartbeat_at_utc):
        return LockRefreshStatus.INVALID_CLOCK, None
    nonce = _validate_token(nonce_source.fresh_token(), "replacement nonce")
    replacement = lock.path.with_name(f".{lock.owner_token}.{nonce}.refresh")
    document = WriterLockDocument(lock.entry_digest, lock.document.acquired_at_utc, timestamp, lock.owner_token, lock.document.host_id, lock.document.process_id)
    try:
        replacement_identity = filesystem.create_lock_exclusive(replacement, document.canonical_bytes())
        new_identity = filesystem.replace_if_same_identity(lock.path, replacement, lock.identity)
        if new_identity is None:
            return LockRefreshStatus.OWNERSHIP_LOST, None
        filesystem.flush_directory(lock.path.parent)
        refreshed = OwnedWriterLock(
            lock.cache_root, lock.path, lock.entry_digest, lock.owner_token,
            document, new_identity, lock.ancestor_identities,
        )
        return (LockRefreshStatus.REFRESHED, refreshed) if verify_owned_writer_lock(refreshed, filesystem=filesystem) else (LockRefreshStatus.OWNERSHIP_LOST, None)
    except (OSError, CacheLookupFilesystemError):
        return LockRefreshStatus.REFRESH_IO_FAILURE, None


def _revalidate_staging(request: CachePromotionRequest, filesystem: CachePromotionFilesystem) -> tuple[FileIdentity, FileIdentity]:
    reference = request.staged_entry
    digest = derive_entry_digest(request.cache_key)
    expected_path = derive_staging_entry_path(request.cache_root.resolved_path, request.namespace, request.cache_key, request.writer_token)
    if reference.staging_path != expected_path or reference.entry_digest != digest or reference.namespace != request.namespace or reference.cache_key_reference != CacheKeyReference.from_cache_key(request.cache_key):
        raise ValueError("staging reference identity mismatch")
    if reference.metadata.entry_digest != digest or reference.metadata.namespace != request.namespace or reference.metadata.cache_key != reference.cache_key_reference or reference.metadata != reference.metadata or reference.manifest.files != tuple(sorted(reference.manifest.files, key=lambda item: item.relative_path)):
        raise ValueError("staging reference model mismatch")
    staging_identity = filesystem.inspect(expected_path)
    if staging_identity.object_type is not FilesystemObjectType.DIRECTORY:
        raise ValueError("unsafe staging path")
    _safe_directory_chain(filesystem, request.cache_root.resolved_path, expected_path)
    if filesystem.list_directory(expected_path) != ("COMPLETE", "manifest.json", "metadata.json", "payload"):
        raise ValueError("staging structure mismatch")
    payload_root = expected_path / "payload"
    if filesystem.inspect(payload_root).object_type is not FilesystemObjectType.DIRECTORY:
        raise ValueError("payload is not a directory")
    documents = (("manifest.json", request.policy.max_manifest_bytes, reference.manifest.canonical_bytes()), ("metadata.json", request.policy.max_metadata_bytes, reference.metadata.canonical_bytes()), ("COMPLETE", request.policy.max_complete_bytes, reference.marker.canonical_bytes()))
    marker_identity: FileIdentity | None = None
    for name, limit, expected in documents:
        observed = filesystem.read_regular_file_bounded(expected_path / name, max_bytes=limit)
        if observed.limit_exceeded or not observed.stable_read or observed.data != expected:
            raise ValueError("staging document mismatch")
        if name == "COMPLETE":
            marker_identity = observed.post_read_identity
    found: list[str] = []
    def walk(directory: Path, prefix: PurePosixPath) -> None:
        for name in filesystem.list_directory(directory):
            candidate = directory / name
            identity = filesystem.inspect(candidate)
            relative = prefix / name
            if identity.object_type is FilesystemObjectType.DIRECTORY:
                walk(candidate, relative)
            elif identity.object_type is FilesystemObjectType.REGULAR_FILE:
                if identity.link_count is not None and identity.link_count > 1:
                    raise ValueError("hardlinked payload")
                found.append(relative.as_posix())
            else:
                raise ValueError("unsafe payload object")
    walk(payload_root, PurePosixPath())
    if tuple(sorted(found)) != tuple(record.relative_path for record in reference.manifest.files):
        raise ValueError("payload set mismatch")
    total = 0
    for record in reference.manifest.files:
        observed = filesystem.stream_regular_file_sha256(payload_root.joinpath(*PurePosixPath(record.relative_path).parts), declared_size=record.size_bytes, chunk_size=request.policy.read_chunk_size)
        if not observed.stable_read or observed.bytes_read != record.size_bytes or observed.has_additional_byte or observed.digest != record.digest:
            raise ValueError("payload integrity mismatch")
        total += record.size_bytes
    if len(reference.manifest.files) != reference.payload_file_count or total != reference.payload_total_bytes or reference.metadata.payload_file_count != reference.payload_file_count or reference.metadata.payload_total_bytes != reference.payload_total_bytes:
        raise ValueError("payload summary mismatch")
    if reference.metadata.payload_manifest_digest != "sha256:" + hashlib.sha256(reference.manifest.canonical_bytes()).hexdigest() or reference.marker.metadata_digest != "sha256:" + hashlib.sha256(reference.metadata.canonical_bytes()).hexdigest() or reference.marker.manifest_digest != reference.metadata.payload_manifest_digest or reference.marker.entry_digest != digest:
        raise ValueError("document digest mismatch")
    after = filesystem.inspect(expected_path)
    if not staging_identity.same_stable_object(after) or marker_identity is None or not _root_is_stable(request, filesystem):
        raise ValueError("unstable staging path")
    return after, marker_identity


def promote_cache_entry(request: CachePromotionRequest, *, filesystem: CachePromotionFilesystem = DEFAULT_CACHE_PROMOTION_FILESYSTEM, clock: WriterLockClock = SYSTEM_WRITER_LOCK_CLOCK, token_source: WriterOwnerTokenSource = UUID4_OWNER_TOKEN_SOURCE) -> CachePromotionResult:
    status, owned = acquire_writer_lock(request, filesystem=filesystem, clock=clock, token_source=token_source)
    if owned is None:
        return CachePromotionResult(status)
    def fail(value: CachePromotionStatus) -> CachePromotionResult:
        release = release_owned_writer_lock(owned, filesystem=filesystem)
        return CachePromotionResult(value, owned_lock=owned if release is not LockReleaseStatus.RELEASED else None, release_status=release)
    if not verify_owned_writer_lock(owned, filesystem=filesystem):
        return CachePromotionResult(CachePromotionStatus.OWNERSHIP_LOST, owned_lock=owned)
    try:
        staging_identity, marker_identity = _revalidate_staging(request, filesystem)
    except (FileNotFoundError, ValueError, OSError, CacheLookupFilesystemError):
        return fail(CachePromotionStatus.STAGING_INVALID)
    final = derive_final_entry_path(request.cache_root.resolved_path, request.namespace, request.cache_key)
    try:
        final_ancestors = _safe_directory_chain(
            filesystem, request.cache_root.resolved_path, final.parent
        )
        try:
            filesystem.inspect(final)
        except FileNotFoundError:
            pass
        else:
            return fail(CachePromotionStatus.FINAL_PATH_OCCUPIED)
        parent_identity = filesystem.inspect(final.parent)
        if final_ancestors and (
            final_ancestors[-1].device_id is None
            or final_ancestors[-1].file_id is None
            or parent_identity.device_id is None
            or parent_identity.file_id is None
        ):
            return fail(CachePromotionStatus.SAME_FILESYSTEM_CAPABILITY_UNAVAILABLE)
        if not final_ancestors or not _same_creation_identity(
            final_ancestors[-1], parent_identity
        ):
            return fail(CachePromotionStatus.UNSTABLE_FINAL_PATH)
    except ValueError:
        return fail(CachePromotionStatus.UNSAFE_FINAL_PATH)
    except (FileNotFoundError, OSError, CacheLookupFilesystemError):
        return fail(CachePromotionStatus.UNSTABLE_FINAL_PATH)
    if staging_identity.device_id is None or parent_identity.device_id is None:
        return fail(CachePromotionStatus.SAME_FILESYSTEM_CAPABILITY_UNAVAILABLE)
    if staging_identity.device_id != parent_identity.device_id:
        return fail(CachePromotionStatus.CROSS_FILESYSTEM)
    if not filesystem.supports_atomic_noreplace_rename():
        return fail(CachePromotionStatus.PROMOTION_CAPABILITY_UNAVAILABLE)
    if not verify_owned_writer_lock(owned, filesystem=filesystem):
        return CachePromotionResult(CachePromotionStatus.OWNERSHIP_LOST, owned_lock=owned)
    try:
        staging_gate = filesystem.inspect(request.staged_entry.staging_path)
        parent_gate = filesystem.inspect(final.parent)
        final_ancestors_gate = _safe_directory_chain(
            filesystem, request.cache_root.resolved_path, final.parent
        )
        if not _root_is_stable(request, filesystem) or not staging_identity.same_stable_object(staging_gate):
            return fail(CachePromotionStatus.UNSTABLE_STAGING_PATH)
        if (
            not parent_identity.same_stable_object(parent_gate)
            or len(final_ancestors) != len(final_ancestors_gate)
            or any(not _same_creation_identity(before, after) for before, after in zip(final_ancestors, final_ancestors_gate))
        ):
            return fail(CachePromotionStatus.UNSTABLE_FINAL_PATH)
        try:
            filesystem.inspect(final)
        except FileNotFoundError:
            pass
        else:
            return fail(CachePromotionStatus.FINAL_PATH_OCCUPIED)
        filesystem.rename_directory_noreplace(request.staged_entry.staging_path, final)
    except OSError as exc:
        if exc.errno == errno.EXDEV:
            return fail(CachePromotionStatus.CROSS_FILESYSTEM_RACE)
        if exc.errno in (errno.EEXIST, errno.ENOTEMPTY):
            return fail(CachePromotionStatus.FINAL_PATH_OCCUPIED_RACE)
        return fail(CachePromotionStatus.PROMOTION_IO_FAILURE)
    try:
        filesystem.flush_directory(final.parent)
    except (OSError, CacheLookupFilesystemError):
        return fail(CachePromotionStatus.PROMOTED_OUTCOME_UNCERTAIN)
    try:
        try:
            filesystem.inspect(request.staged_entry.staging_path)
        except FileNotFoundError:
            pass
        else:
            return fail(CachePromotionStatus.PROMOTED_OUTCOME_UNCERTAIN)
        final_identity = filesystem.inspect(final)
        marker = filesystem.read_regular_file_bounded(final / "COMPLETE", max_bytes=request.policy.max_complete_bytes)
        if final_identity.object_type is not FilesystemObjectType.DIRECTORY or not _same_creation_identity(staging_identity, final_identity) or marker.limit_exceeded or not marker.stable_read or marker.data != request.staged_entry.marker.canonical_bytes() or not _same_creation_identity(marker_identity, marker.post_read_identity) or not _root_is_stable(request, filesystem):
            return fail(CachePromotionStatus.PROMOTED_OUTCOME_UNCERTAIN)
    except (OSError, ValueError, CacheLookupFilesystemError):
        return fail(CachePromotionStatus.PROMOTED_OUTCOME_UNCERTAIN)
    reference = request.staged_entry
    promoted = PromotedCacheEntryReference(final, reference.entry_digest, reference.namespace, reference.cache_key_reference, reference.metadata, reference.manifest, reference.marker, reference.payload_file_count, reference.payload_total_bytes)
    release = release_owned_writer_lock(owned, filesystem=filesystem)
    if release is LockReleaseStatus.RELEASED:
        return CachePromotionResult(CachePromotionStatus.PROMOTED_AND_RELEASED, promoted, release_status=release)
    return CachePromotionResult(CachePromotionStatus.PROMOTED_LOCK_RETAINED, promoted, owned, release)
