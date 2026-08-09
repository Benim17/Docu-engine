"""Read-only filesystem primitives and internal cache-entry structure validation.

This module does not interpret cache documents or expose public lookup
orchestration.  Its local adapter exposes metadata inspection, immediate
directory listing, path resolution, and bounded regular-file reads only.
"""

from __future__ import annotations

import os
import errno
import hashlib
import json
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Protocol, runtime_checkable

from .persistent_cache import (
    CACHE_ENTRY_CONTRACT_VERSION,
    CACHE_KEY_CANONICAL_VERSION,
    PAYLOAD_MANIFEST_VERSION,
    RUNTIME_FINGERPRINT_SCHEMA_VERSION,
    CacheEntryContractError,
    CacheArtifactMetadata,
    CacheEntryMetadata,
    CacheKeyReference,
    CacheLookupExpectation,
    CacheNamespace,
    CacheProducerMetadata,
    CacheRuntimeFingerprint,
    CompletenessMarker,
    PayloadManifest,
    _digest,
    _fields,
    _nonnegative_int,
    _object,
    _timestamp,
    _validate_payload_summary,
    canonical_json_bytes,
    derive_entry_digest,
    derive_final_entry_path,
    parse_canonical_json,
)
from .cache_keys import CacheKey


DEFAULT_MAX_COMPLETE_BYTES = 4 * 1024
DEFAULT_MAX_METADATA_BYTES = 256 * 1024
DEFAULT_MAX_MANIFEST_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_PAYLOAD_RECORDS = 100_000
DEFAULT_MAX_RELATIVE_PATH_UTF8_BYTES = 1_024
DEFAULT_MAX_PAYLOAD_DEPTH = 64
DEFAULT_MAX_INDIVIDUAL_PAYLOAD_BYTES = 1 << 40
DEFAULT_MAX_TOTAL_PAYLOAD_BYTES = 16 << 40
DEFAULT_MAX_DIAGNOSTICS = 32
DEFAULT_READ_CHUNK_SIZE = 1 * 1024 * 1024


class CacheLookupFilesystemError(RuntimeError):
    """Base for deterministic read-only filesystem adapter failures."""


class CacheLookupPermissionError(CacheLookupFilesystemError):
    """Raised when a read-only filesystem operation is not permitted."""


class CacheLookupIOError(CacheLookupFilesystemError):
    """Raised when a read-only filesystem operation fails unexpectedly."""


class UnsupportedFilesystemObjectError(CacheLookupFilesystemError):
    """Raised when an operation requires a different filesystem object type."""


class SymlinkRejectedError(UnsupportedFilesystemObjectError):
    """Raised when a no-follow operation encounters a symlink."""


class UnstableFilesystemObjectError(CacheLookupFilesystemError):
    """Raised when an operation cannot establish one stable object observation."""


class FilesystemObjectType(str, Enum):
    REGULAR_FILE = "regular_file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"
    FIFO = "fifo"
    SOCKET = "socket"
    BLOCK_DEVICE = "block_device"
    CHARACTER_DEVICE = "character_device"
    OTHER = "other"


def _object_type(mode: int) -> FilesystemObjectType:
    if stat.S_ISREG(mode):
        return FilesystemObjectType.REGULAR_FILE
    if stat.S_ISDIR(mode):
        return FilesystemObjectType.DIRECTORY
    if stat.S_ISLNK(mode):
        return FilesystemObjectType.SYMLINK
    if stat.S_ISFIFO(mode):
        return FilesystemObjectType.FIFO
    if stat.S_ISSOCK(mode):
        return FilesystemObjectType.SOCKET
    if stat.S_ISBLK(mode):
        return FilesystemObjectType.BLOCK_DEVICE
    if stat.S_ISCHR(mode):
        return FilesystemObjectType.CHARACTER_DEVICE
    return FilesystemObjectType.OTHER


def _optional_stat_integer(observation: os.stat_result, field: str) -> int | None:
    value = getattr(observation, field, None)
    return value if isinstance(value, int) else None


@dataclass(frozen=True)
class FileIdentity:
    object_type: FilesystemObjectType
    device_id: int | None
    file_id: int | None
    size: int
    modification_time_ns: int | None
    change_time_ns: int | None
    link_count: int | None

    @classmethod
    def from_stat(cls, observation: os.stat_result) -> "FileIdentity":
        """Build an identity from one no-follow or handle-level stat result."""

        if not isinstance(observation, os.stat_result):
            raise TypeError("observation must be an os.stat_result.")
        return cls(
            object_type=_object_type(observation.st_mode),
            device_id=_optional_stat_integer(observation, "st_dev"),
            file_id=_optional_stat_integer(observation, "st_ino"),
            size=observation.st_size,
            modification_time_ns=_optional_stat_integer(observation, "st_mtime_ns"),
            change_time_ns=_optional_stat_integer(observation, "st_ctime_ns"),
            link_count=_optional_stat_integer(observation, "st_nlink"),
        )

    def same_stable_object(self, other: "FileIdentity") -> bool:
        """Return true only when available evidence establishes stable identity.

        Device and file IDs are the minimum identity evidence.  All metadata
        fields available in both observations must also remain equal.  A
        platform that does not expose device/file identity receives ``False``
        rather than a stronger claim based only on names, sizes, or timestamps.
        """

        if not isinstance(other, FileIdentity):
            return False
        if self.object_type is not other.object_type:
            return False
        if None in (self.device_id, self.file_id, other.device_id, other.file_id):
            return False
        if (self.device_id, self.file_id) != (other.device_id, other.file_id):
            return False
        for field in (
            "size",
            "modification_time_ns",
            "change_time_ns",
            "link_count",
        ):
            first = getattr(self, field)
            second = getattr(other, field)
            if first is not None and second is not None and first != second:
                return False
        return True


def _positive_integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a strict positive integer.")
    return value


@dataclass(frozen=True)
class CacheLookupVerificationPolicy:
    max_complete_bytes: int = DEFAULT_MAX_COMPLETE_BYTES
    max_metadata_bytes: int = DEFAULT_MAX_METADATA_BYTES
    max_manifest_bytes: int = DEFAULT_MAX_MANIFEST_BYTES
    max_payload_records: int = DEFAULT_MAX_PAYLOAD_RECORDS
    max_relative_path_utf8_bytes: int = DEFAULT_MAX_RELATIVE_PATH_UTF8_BYTES
    max_payload_depth: int = DEFAULT_MAX_PAYLOAD_DEPTH
    max_individual_payload_bytes: int = DEFAULT_MAX_INDIVIDUAL_PAYLOAD_BYTES
    max_total_payload_bytes: int = DEFAULT_MAX_TOTAL_PAYLOAD_BYTES
    max_diagnostics: int = DEFAULT_MAX_DIAGNOSTICS
    read_chunk_size: int = DEFAULT_READ_CHUNK_SIZE

    def __post_init__(self) -> None:
        for field in (
            "max_complete_bytes",
            "max_metadata_bytes",
            "max_manifest_bytes",
            "max_payload_records",
            "max_relative_path_utf8_bytes",
            "max_payload_depth",
            "max_individual_payload_bytes",
            "max_total_payload_bytes",
            "max_diagnostics",
            "read_chunk_size",
        ):
            _positive_integer(getattr(self, field), field)
        if self.max_individual_payload_bytes > self.max_total_payload_bytes:
            raise ValueError(
                "max_individual_payload_bytes cannot exceed max_total_payload_bytes."
            )


@dataclass(frozen=True)
class BoundedFileRead:
    data: bytes | None
    limit_exceeded: bool
    pre_read_identity: FileIdentity
    handle_identity: FileIdentity
    post_read_identity: FileIdentity
    stable_read: bool

    def __post_init__(self) -> None:
        if self.limit_exceeded and self.data is not None:
            raise ValueError("Oversized reads must not expose partial bytes as complete data.")
        if not self.limit_exceeded and not isinstance(self.data, bytes):
            raise ValueError("A within-limit read must contain exact bytes.")


@dataclass(frozen=True)
class _StreamedPayloadHash:
    digest: str
    bytes_read: int
    has_additional_byte: bool
    pre_read_identity: FileIdentity
    handle_identity: FileIdentity


@runtime_checkable
class ReadOnlyCacheFilesystem(Protocol):
    """Narrow filesystem surface with no mutation-capable methods."""

    def inspect(self, path: str | Path) -> FileIdentity:
        ...

    def resolve(self, path: str | Path) -> Path:
        ...

    def list_directory(self, path: str | Path) -> tuple[str, ...]:
        ...

    def read_regular_file_bounded(
        self, path: str | Path, *, max_bytes: int
    ) -> BoundedFileRead:
        ...


@runtime_checkable
class _PayloadReadOnlyCacheFilesystem(ReadOnlyCacheFilesystem, Protocol):
    def stream_regular_file_sha256(
        self,
        path: str | Path,
        *,
        declared_size: int,
        chunk_size: int,
    ) -> _StreamedPayloadHash:
        ...


def _translated_os_error(exc: OSError, operation: str) -> CacheLookupFilesystemError:
    if isinstance(exc, PermissionError):
        return CacheLookupPermissionError(f"{operation} was not permitted.")
    return CacheLookupIOError(f"{operation} failed.")


class LocalReadOnlyCacheFilesystem:
    """Default local adapter using no-follow metadata and bounded streaming reads."""

    def inspect(self, path: str | Path) -> FileIdentity:
        try:
            return FileIdentity.from_stat(os.lstat(path))
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise _translated_os_error(exc, "Filesystem inspection") from exc

    def _inspect_handle(self, descriptor: int) -> FileIdentity:
        try:
            return FileIdentity.from_stat(os.fstat(descriptor))
        except OSError as exc:
            raise _translated_os_error(exc, "Handle inspection") from exc

    def resolve(self, path: str | Path) -> Path:
        try:
            return Path(path).resolve(strict=True)
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise _translated_os_error(exc, "Path resolution") from exc

    def list_directory(self, path: str | Path) -> tuple[str, ...]:
        pre_read = self.inspect(path)
        if pre_read.object_type is FilesystemObjectType.SYMLINK:
            raise SymlinkRejectedError("Directory listing rejects symlinks.")
        if pre_read.object_type is not FilesystemObjectType.DIRECTORY:
            raise UnsupportedFilesystemObjectError("Directory listing requires a directory.")
        flags = os.O_RDONLY
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor: int | None = None
        try:
            descriptor = os.open(path, flags)
            handle_identity = self._inspect_handle(descriptor)
            if not pre_read.same_stable_object(handle_identity):
                raise UnstableFilesystemObjectError("Directory changed before listing.")
            with os.scandir(descriptor) as entries:
                names = tuple(sorted(entry.name for entry in entries))
            handle_after = self._inspect_handle(descriptor)
        except FileNotFoundError:
            raise
        except UnstableFilesystemObjectError:
            raise
        except OSError as exc:
            if getattr(exc, "errno", None) == errno.ELOOP:
                raise SymlinkRejectedError("Directory listing rejects symlinks.") from exc
            raise _translated_os_error(exc, "Directory listing") from exc
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        try:
            post_read = self.inspect(path)
        except FileNotFoundError as exc:
            raise UnstableFilesystemObjectError(
                "Directory disappeared while it was listed."
            ) from exc
        if not (
            pre_read.same_stable_object(handle_identity)
            and handle_identity.same_stable_object(handle_after)
            and handle_after.same_stable_object(post_read)
        ):
            raise UnstableFilesystemObjectError("Directory changed while it was listed.")
        if any(name in {".", ".."} for name in names):
            raise CacheLookupIOError("Directory listing returned a reserved name.")
        return names

    def read_regular_file_bounded(
        self, path: str | Path, *, max_bytes: int
    ) -> BoundedFileRead:
        limit = _positive_integer(max_bytes, "max_bytes")
        pre_read = self.inspect(path)
        if pre_read.object_type is FilesystemObjectType.SYMLINK:
            raise SymlinkRejectedError("Regular-file reading rejects symlinks.")
        if pre_read.object_type is not FilesystemObjectType.REGULAR_FILE:
            raise UnsupportedFilesystemObjectError("Bounded reading requires a regular file.")

        flags = os.O_RDONLY
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        descriptor: int | None = None
        try:
            descriptor = os.open(path, flags)
            handle_identity = self._inspect_handle(descriptor)
            if handle_identity.object_type is not FilesystemObjectType.REGULAR_FILE:
                raise UnstableFilesystemObjectError(
                    "Path changed to a non-regular object before bounded reading."
                )
            if not pre_read.same_stable_object(handle_identity):
                raise UnstableFilesystemObjectError("File changed before bounded reading.")

            remaining = limit + 1
            chunks: list[bytes] = []
            while remaining > 0:
                chunk = os.read(descriptor, min(DEFAULT_READ_CHUNK_SIZE, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            observed = b"".join(chunks)
            handle_after = self._inspect_handle(descriptor)
        except FileNotFoundError:
            raise
        except (SymlinkRejectedError, UnstableFilesystemObjectError):
            raise
        except OSError as exc:
            if getattr(exc, "errno", None) == errno.ELOOP:
                raise SymlinkRejectedError("Regular-file reading rejects symlinks.") from exc
            raise _translated_os_error(exc, "Bounded file reading") from exc
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass

        try:
            post_read = self.inspect(path)
        except FileNotFoundError as exc:
            raise UnstableFilesystemObjectError("File disappeared during bounded reading.") from exc
        stable = (
            pre_read.same_stable_object(handle_identity)
            and handle_identity.same_stable_object(handle_after)
            and handle_after.same_stable_object(post_read)
        )
        exceeded = len(observed) > limit
        return BoundedFileRead(
            data=None if exceeded else observed,
            limit_exceeded=exceeded,
            pre_read_identity=pre_read,
            handle_identity=handle_identity,
            post_read_identity=post_read,
            stable_read=stable,
        )

    def stream_regular_file_sha256(
        self,
        path: str | Path,
        *,
        declared_size: int,
        chunk_size: int,
    ) -> _StreamedPayloadHash:
        """Hash at most the declared payload bytes and probe for one extra byte."""

        if (
            isinstance(declared_size, bool)
            or not isinstance(declared_size, int)
            or declared_size < 0
        ):
            raise ValueError("declared_size must be a non-negative integer.")
        chunk_limit = _positive_integer(chunk_size, "chunk_size")
        pre_read = self.inspect(path)
        if pre_read.object_type is FilesystemObjectType.SYMLINK:
            raise SymlinkRejectedError("Payload streaming rejects symlinks.")
        if pre_read.object_type is not FilesystemObjectType.REGULAR_FILE:
            raise UnsupportedFilesystemObjectError(
                "Payload streaming requires a regular file."
            )

        flags = os.O_RDONLY
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        descriptor: int | None = None
        digest = hashlib.sha256()
        bytes_read = 0
        has_additional_byte = False
        try:
            descriptor = os.open(path, flags)
            handle_identity = self._inspect_handle(descriptor)
            if handle_identity.object_type is not FilesystemObjectType.REGULAR_FILE:
                raise UnstableFilesystemObjectError(
                    "Payload path changed to a non-regular object before streaming."
                )
            if not pre_read.same_stable_object(handle_identity):
                raise UnstableFilesystemObjectError(
                    "Payload file changed before streaming."
                )
            remaining = declared_size
            while remaining:
                chunk = os.read(descriptor, min(chunk_limit, remaining))
                if not chunk:
                    break
                digest.update(chunk)
                bytes_read += len(chunk)
                remaining -= len(chunk)
            has_additional_byte = bool(os.read(descriptor, 1))
        except FileNotFoundError:
            raise
        except (SymlinkRejectedError, UnstableFilesystemObjectError):
            raise
        except OSError as exc:
            if getattr(exc, "errno", None) == errno.ELOOP:
                raise SymlinkRejectedError("Payload streaming rejects symlinks.") from exc
            raise _translated_os_error(exc, "Payload streaming") from exc
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        return _StreamedPayloadHash(
            "sha256:" + digest.hexdigest(),
            bytes_read,
            has_additional_byte,
            pre_read,
            handle_identity,
        )


DEFAULT_READ_ONLY_FILESYSTEM = LocalReadOnlyCacheFilesystem()


def _lexical_path(path: str | Path) -> Path:
    if not isinstance(path, (str, Path)):
        raise TypeError("cache root must be a string or Path.")
    raw = os.fspath(path)
    if not isinstance(raw, str) or not raw:
        raise ValueError("cache root must be non-empty text.")
    supplied = Path(raw)
    if not supplied.is_absolute():
        raise ValueError("cache root must be absolute.")
    separators = (os.sep,) if os.altsep is None else (os.sep, os.altsep)
    normalized_for_split = raw
    for separator in separators[1:]:
        normalized_for_split = normalized_for_split.replace(separator, os.sep)
    if any(component in {".", ".."} for component in normalized_for_split.split(os.sep)):
        raise ValueError("cache root must not contain lexical dot components.")
    return supplied


@dataclass(frozen=True)
class ValidatedCacheRoot:
    lexical_path: Path
    resolved_path: Path
    identity: FileIdentity

    @classmethod
    def from_path(
        cls,
        path: str | Path,
        *,
        filesystem: ReadOnlyCacheFilesystem = DEFAULT_READ_ONLY_FILESYSTEM,
    ) -> "ValidatedCacheRoot":
        if not isinstance(filesystem, ReadOnlyCacheFilesystem):
            raise TypeError("filesystem must implement ReadOnlyCacheFilesystem.")
        lexical = _lexical_path(path)
        initial = filesystem.inspect(lexical)
        if initial.object_type is FilesystemObjectType.SYMLINK:
            raise SymlinkRejectedError("Cache root must not be a symlink.")
        if initial.object_type is not FilesystemObjectType.DIRECTORY:
            raise UnsupportedFilesystemObjectError("Cache root must be a directory.")
        resolved = filesystem.resolve(lexical)
        if not resolved.is_absolute():
            raise CacheLookupIOError("Resolved cache root was not absolute.")
        resolved_identity = filesystem.inspect(resolved)
        if resolved_identity.object_type is not FilesystemObjectType.DIRECTORY:
            raise UnsupportedFilesystemObjectError("Resolved cache root must be a directory.")
        if not initial.same_stable_object(resolved_identity):
            raise UnstableFilesystemObjectError(
                "Lexical and resolved cache roots do not establish one stable identity."
            )
        return cls(lexical, resolved, initial)


_EXPECTED_FINAL_ENTRY_OBJECT_TYPES = {
    "COMPLETE": FilesystemObjectType.REGULAR_FILE,
    "manifest.json": FilesystemObjectType.REGULAR_FILE,
    "metadata.json": FilesystemObjectType.REGULAR_FILE,
    "payload": FilesystemObjectType.DIRECTORY,
}
_EXPECTED_FINAL_ENTRY_NAMES = frozenset(_EXPECTED_FINAL_ENTRY_OBJECT_TYPES)


class _FinalEntryStructureClassification(str, Enum):
    """Internal 5B2A observations; these are not public lookup statuses."""

    VALID = "valid"
    ENTRY_ABSENT = "entry_absent"
    INCOMPLETE_ENTRY = "incomplete_entry"
    UNEXPECTED_TOP_LEVEL_OBJECT = "unexpected_top_level_object"
    UNSAFE_OBJECT = "unsafe_object"


@dataclass(frozen=True)
class _FinalEntryStructureObservation:
    classification: _FinalEntryStructureClassification
    observed_names: tuple[str, ...] = ()
    missing_names: tuple[str, ...] = ()
    unexpected_names: tuple[str, ...] = ()
    unsafe_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "observed_names",
            "missing_names",
            "unexpected_names",
            "unsafe_names",
        ):
            values = getattr(self, field_name)
            if (
                not isinstance(values, tuple)
                or any(not isinstance(item, str) or not item for item in values)
                or values != tuple(sorted(set(values)))
            ):
                raise ValueError(f"{field_name} must be sorted unique non-empty names.")


def _inspect_final_entry_structure(
    expected_entry_path: str | Path,
    *,
    filesystem: ReadOnlyCacheFilesystem = DEFAULT_READ_ONLY_FILESYSTEM,
) -> _FinalEntryStructureObservation:
    """Inspect one expected entry's immediate v1 structure without reading payloads.

    Entry absence is an internal observation only.  It is deliberately not a
    public ``MISS`` because later orchestration must perform matching-lock
    observation first.
    """

    if not isinstance(expected_entry_path, (str, Path)):
        raise TypeError("expected_entry_path must be a string or Path.")
    if not isinstance(filesystem, ReadOnlyCacheFilesystem):
        raise TypeError("filesystem must implement ReadOnlyCacheFilesystem.")
    entry_path = Path(expected_entry_path)

    try:
        entry_identity = filesystem.inspect(entry_path)
    except FileNotFoundError:
        return _FinalEntryStructureObservation(
            _FinalEntryStructureClassification.ENTRY_ABSENT
        )

    if entry_identity.object_type is not FilesystemObjectType.DIRECTORY:
        return _FinalEntryStructureObservation(
            _FinalEntryStructureClassification.UNSAFE_OBJECT,
            unsafe_names=(".",),
        )

    observed_names = filesystem.list_directory(entry_path)
    observed_identities = {
        name: filesystem.inspect(entry_path / name) for name in observed_names
    }

    unsafe_names = tuple(
        sorted(
            name
            for name, identity in observed_identities.items()
            if (
                name in _EXPECTED_FINAL_ENTRY_OBJECT_TYPES
                and identity.object_type
                is not _EXPECTED_FINAL_ENTRY_OBJECT_TYPES[name]
            )
            or (
                name not in _EXPECTED_FINAL_ENTRY_NAMES
                and identity.object_type
                not in {
                    FilesystemObjectType.REGULAR_FILE,
                    FilesystemObjectType.DIRECTORY,
                }
            )
        )
    )
    missing_names = tuple(sorted(_EXPECTED_FINAL_ENTRY_NAMES - set(observed_names)))
    unexpected_names = tuple(sorted(set(observed_names) - _EXPECTED_FINAL_ENTRY_NAMES))

    if unsafe_names:
        classification = _FinalEntryStructureClassification.UNSAFE_OBJECT
    elif missing_names:
        classification = _FinalEntryStructureClassification.INCOMPLETE_ENTRY
    elif unexpected_names:
        classification = _FinalEntryStructureClassification.UNEXPECTED_TOP_LEVEL_OBJECT
    else:
        classification = _FinalEntryStructureClassification.VALID

    return _FinalEntryStructureObservation(
        classification,
        observed_names=observed_names,
        missing_names=missing_names,
        unexpected_names=unexpected_names,
        unsafe_names=unsafe_names,
    )


class _CacheDocumentName(str, Enum):
    COMPLETE = "COMPLETE"
    METADATA = "metadata.json"
    MANIFEST = "manifest.json"


class _CacheDocumentClassification(str, Enum):
    """Internal 5B2B classifications compatible with locked Step 5B reasons."""

    VALID = "valid"
    MALFORMED_COMPLETE = "malformed_complete"
    MALFORMED_METADATA = "malformed_metadata"
    MALFORMED_MANIFEST = "malformed_manifest"
    UNSUPPORTED_ENTRY_VERSION = "unsupported_entry_version"
    UNSUPPORTED_MANIFEST_VERSION = "unsupported_manifest_version"
    UNSUPPORTED_CACHE_KEY_VERSION = "unsupported_cache_key_version"
    UNSUPPORTED_RUNTIME_FINGERPRINT_VERSION = (
        "unsupported_runtime_fingerprint_version"
    )


class _CacheDocumentIntegrityClassification(str, Enum):
    """Internal Step 5B2C classifications; never public lookup results."""

    VALID = "valid"
    ENTRY_IDENTITY_CONFLICT = "entry_identity_conflict"
    CACHE_KEY_CONFLICT = "cache_key_conflict"
    MANIFEST_DIGEST_MISMATCH = "manifest_digest_mismatch"
    METADATA_DIGEST_MISMATCH = "metadata_digest_mismatch"
    METADATA_MANIFEST_SUMMARY_MISMATCH = "metadata_manifest_summary_mismatch"
    NAMESPACE_PRODUCER_CONFLICT = "namespace_producer_conflict"
    PRODUCER_MISMATCH = "producer_mismatch"
    SCHEMA_MISMATCH = "schema_mismatch"
    ARTIFACT_MISMATCH = "artifact_mismatch"
    RUNTIME_FINGERPRINT_MISMATCH = "runtime_fingerprint_mismatch"


class _PayloadValidationClassification(str, Enum):
    VALID = "valid"
    PAYLOAD_CARDINALITY_INVALID = "payload_cardinality_invalid"
    PAYLOAD_POLICY_LIMIT_EXCEEDED = "payload_policy_limit_exceeded"
    UNSAFE_OBJECT = "unsafe_object"
    UNEXPECTED_PAYLOAD_OBJECT = "unexpected_payload_object"
    PAYLOAD_MISSING = "payload_missing"
    PAYLOAD_HARDLINK_DETECTED = "payload_hardlink_detected"
    PAYLOAD_SIZE_MISMATCH = "payload_size_mismatch"
    PAYLOAD_DIGEST_MISMATCH = "payload_digest_mismatch"
    PAYLOAD_READ_UNSTABLE = "payload_read_unstable"


class _CacheVerificationLevel(str, Enum):
    NONE = "none"
    STRUCTURE = "structure"
    CANONICAL_DOCUMENTS = "canonical_documents"
    DOCUMENT_INTEGRITY = "document_integrity"
    FULL_PAYLOAD_SHA256 = "full_payload_sha256"


class PayloadCardinalityExpectation(str, Enum):
    NON_EMPTY_REQUIRED = "non_empty_required"
    EMPTY_ALLOWED = "empty_allowed"


@dataclass(frozen=True, init=False)
class ProducerPayloadExpectation:
    """Trusted Step 5B producer semantics, not serialized cache metadata."""

    cardinality: PayloadCardinalityExpectation

    def __init__(self) -> None:
        object.__setattr__(
            self,
            "cardinality",
            PayloadCardinalityExpectation.NON_EMPTY_REQUIRED,
        )


def _trusted_producer_payload_expectation(
    cardinality: PayloadCardinalityExpectation,
) -> ProducerPayloadExpectation:
    """Internal adapter/registry boundary for explicit producer semantics."""

    if not isinstance(cardinality, PayloadCardinalityExpectation):
        raise TypeError("cardinality must be a PayloadCardinalityExpectation.")
    expectation = ProducerPayloadExpectation()
    object.__setattr__(expectation, "cardinality", cardinality)
    return expectation


def _payload_cardinality_is_valid(
    expectation: ProducerPayloadExpectation,
    *,
    payload_file_count: int,
    payload_total_bytes: int,
    manifest: PayloadManifest,
) -> bool:
    """Apply trusted producer cardinality after Step 5A summary validation."""

    if not isinstance(expectation, ProducerPayloadExpectation):
        raise TypeError("expectation must be a ProducerPayloadExpectation.")
    _validate_payload_summary(
        payload_file_count,
        payload_total_bytes,
        manifest,
    )
    return bool(manifest.files) or (
        expectation.cardinality is PayloadCardinalityExpectation.EMPTY_ALLOWED
    )


@dataclass(frozen=True)
class _CacheArtifactExpectation:
    artifact_kind: str
    artifact_contract_version: int
    expected_logical_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.artifact_kind, str) or not self.artifact_kind:
            raise ValueError("artifact_kind must be non-empty text.")
        if (
            isinstance(self.artifact_contract_version, bool)
            or not isinstance(self.artifact_contract_version, int)
            or self.artifact_contract_version <= 0
        ):
            raise ValueError("artifact_contract_version must be a positive integer.")
        if self.expected_logical_id is not None and (
            not isinstance(self.expected_logical_id, str)
            or not self.expected_logical_id
        ):
            raise ValueError("expected_logical_id must be non-empty text or None.")


@dataclass(frozen=True)
class _ObservedCacheEntryMetadataV1:
    """Schema-valid metadata before Step 5A aggregate relational invariants."""

    entry_digest: str
    cache_key: CacheKeyReference
    namespace: CacheNamespace
    artifact: CacheArtifactMetadata
    producer: CacheProducerMetadata
    runtime_fingerprint: CacheRuntimeFingerprint
    created_at_utc: str
    payload_manifest_digest: str
    payload_file_count: int
    payload_total_bytes: int
    cache_entry_contract_version: int

    def to_dict(self) -> dict[str, object]:
        return {
            "artifact": self.artifact.to_dict(),
            "cache_entry_contract_version": self.cache_entry_contract_version,
            "cache_key": self.cache_key.to_dict(),
            "created_at_utc": self.created_at_utc,
            "entry_digest": self.entry_digest,
            "namespace": self.namespace.to_dict(),
            "payload_file_count": self.payload_file_count,
            "payload_manifest_digest": self.payload_manifest_digest,
            "payload_total_bytes": self.payload_total_bytes,
            "producer": self.producer.to_dict(),
            "runtime_fingerprint": self.runtime_fingerprint.to_dict(),
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    def to_strict_metadata(self) -> CacheEntryMetadata:
        return CacheEntryMetadata(
            self.entry_digest,
            self.cache_key,
            self.namespace,
            self.artifact,
            self.producer,
            self.runtime_fingerprint,
            self.created_at_utc,
            self.payload_manifest_digest,
            self.payload_file_count,
            self.payload_total_bytes,
            self.cache_entry_contract_version,
        )


def _parse_observed_cache_entry_metadata_v1(
    stored_bytes: bytes,
) -> _ObservedCacheEntryMetadataV1:
    """Reuse Step 5A schema validators without enforcing aggregate relations."""

    data = _object(parse_canonical_json(stored_bytes), "metadata")
    expected = frozenset(
        {
            "cache_entry_contract_version",
            "entry_digest",
            "cache_key",
            "namespace",
            "artifact",
            "producer",
            "runtime_fingerprint",
            "created_at_utc",
            "payload_manifest_digest",
            "payload_file_count",
            "payload_total_bytes",
        }
    )
    _fields(data, expected, "metadata")
    _digest(data["entry_digest"], "entry_digest", qualified=False)
    _timestamp(data["created_at_utc"])
    _digest(
        data["payload_manifest_digest"],
        "payload_manifest_digest",
        qualified=True,
    )
    _nonnegative_int(data["payload_file_count"], "payload_file_count")
    _nonnegative_int(data["payload_total_bytes"], "payload_total_bytes")
    return _ObservedCacheEntryMetadataV1(
        entry_digest=data["entry_digest"],
        cache_key=CacheKeyReference.from_dict(data["cache_key"]),
        namespace=CacheNamespace.from_dict(data["namespace"]),
        artifact=CacheArtifactMetadata.from_dict(data["artifact"]),
        producer=CacheProducerMetadata.from_dict(data["producer"]),
        runtime_fingerprint=CacheRuntimeFingerprint.from_dict(
            data["runtime_fingerprint"]
        ),
        created_at_utc=data["created_at_utc"],
        payload_manifest_digest=data["payload_manifest_digest"],
        payload_file_count=data["payload_file_count"],
        payload_total_bytes=data["payload_total_bytes"],
        cache_entry_contract_version=data["cache_entry_contract_version"],
    )


_CACHE_DOCUMENT_LIMIT_FIELDS = {
    _CacheDocumentName.COMPLETE: "max_complete_bytes",
    _CacheDocumentName.METADATA: "max_metadata_bytes",
    _CacheDocumentName.MANIFEST: "max_manifest_bytes",
}
_CACHE_DOCUMENT_MALFORMED_CLASSIFICATIONS = {
    _CacheDocumentName.COMPLETE: _CacheDocumentClassification.MALFORMED_COMPLETE,
    _CacheDocumentName.METADATA: _CacheDocumentClassification.MALFORMED_METADATA,
    _CacheDocumentName.MANIFEST: _CacheDocumentClassification.MALFORMED_MANIFEST,
}
_CACHE_DOCUMENT_MODEL_TYPES = {
    _CacheDocumentName.COMPLETE: CompletenessMarker,
    _CacheDocumentName.METADATA: CacheEntryMetadata,
    _CacheDocumentName.MANIFEST: PayloadManifest,
}


@dataclass(frozen=True)
class _CacheDocumentObservation:
    name: _CacheDocumentName
    classification: _CacheDocumentClassification
    stored_bytes: bytes | None = None
    model: CompletenessMarker | CacheEntryMetadata | PayloadManifest | None = None
    observed_metadata: _ObservedCacheEntryMetadataV1 | None = None
    observed_versions: tuple[tuple[str, int], ...] = ()
    stable_read: bool | None = None


@dataclass(frozen=True)
class _FinalEntryDocumentsObservation:
    classification: _CacheDocumentClassification
    complete: _CacheDocumentObservation
    metadata: _CacheDocumentObservation | None = None
    manifest: _CacheDocumentObservation | None = None


@dataclass(frozen=True)
class _FinalEntryDocumentIntegrityObservation:
    classification: _CacheDocumentIntegrityClassification
    verification_level: _CacheVerificationLevel
    documents: _FinalEntryDocumentsObservation
    metadata: CacheEntryMetadata | None = None
    payload_bytes_fully_hashed: bool = False


@dataclass(frozen=True)
class _PayloadValidationObservation:
    classification: _PayloadValidationClassification
    verification_level: _CacheVerificationLevel
    observed_regular_files: tuple[str, ...] = ()
    hash_order: tuple[str, ...] = ()
    payload_bytes_hashed: int = 0
    declared_payload_bytes_fully_verified: bool = False


class _VersionProbeError(ValueError):
    """Internal signal for malformed bounded version-discriminator input."""


def _duplicate_rejecting_json_object(
    items: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise _VersionProbeError("Duplicate JSON key in version probe.")
        result[key] = value
    return result


def _probe_json_object(stored_bytes: bytes) -> dict[str, object]:
    if stored_bytes.startswith(b"\xef\xbb\xbf"):
        raise _VersionProbeError("Version probe rejects a BOM.")
    try:
        text = stored_bytes.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_duplicate_rejecting_json_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                _VersionProbeError("Version probe rejects non-finite values.")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise _VersionProbeError("Version probe requires bounded strict JSON.") from exc
    if type(value) is not dict:
        raise _VersionProbeError("Versioned document must be a JSON object.")
    return value


def _version_discriminator(
    container: object, field_name: str, diagnostic_name: str
) -> tuple[str, int]:
    if type(container) is not dict or field_name not in container:
        raise _VersionProbeError(f"Missing {diagnostic_name} discriminator.")
    value = container[field_name]
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _VersionProbeError(f"Malformed {diagnostic_name} discriminator.")
    return diagnostic_name, value


def _probe_cache_document_versions(
    name: _CacheDocumentName, stored_bytes: bytes
) -> tuple[tuple[tuple[str, int], ...], _CacheDocumentClassification | None]:
    data = _probe_json_object(stored_bytes)

    if name is _CacheDocumentName.MANIFEST:
        observed = _version_discriminator(data, "manifest_version", "manifest")
        unsupported = (
            _CacheDocumentClassification.UNSUPPORTED_MANIFEST_VERSION
            if observed[1] != PAYLOAD_MANIFEST_VERSION
            else None
        )
        return (observed,), unsupported

    entry = _version_discriminator(
        data, "cache_entry_contract_version", "entry"
    )
    if entry[1] != CACHE_ENTRY_CONTRACT_VERSION:
        return (entry,), _CacheDocumentClassification.UNSUPPORTED_ENTRY_VERSION
    if name is _CacheDocumentName.COMPLETE:
        return (entry,), None

    cache_key = _version_discriminator(
        data.get("cache_key"), "canonical_version", "cache_key"
    )
    if cache_key[1] != CACHE_KEY_CANONICAL_VERSION:
        return (
            entry,
            cache_key,
        ), _CacheDocumentClassification.UNSUPPORTED_CACHE_KEY_VERSION

    runtime = _version_discriminator(
        data.get("runtime_fingerprint"), "schema_version", "runtime_fingerprint"
    )
    if runtime[1] != RUNTIME_FINGERPRINT_SCHEMA_VERSION:
        return (
            entry,
            cache_key,
            runtime,
        ), _CacheDocumentClassification.UNSUPPORTED_RUNTIME_FINGERPRINT_VERSION
    return (entry, cache_key, runtime), None


def _read_and_parse_cache_document(
    entry_path: str | Path,
    name: _CacheDocumentName,
    *,
    policy: CacheLookupVerificationPolicy,
    filesystem: ReadOnlyCacheFilesystem = DEFAULT_READ_ONLY_FILESYSTEM,
) -> _CacheDocumentObservation:
    """Bounded-read, probe, and strictly parse one contract document."""

    if not isinstance(name, _CacheDocumentName):
        raise TypeError("name must be a _CacheDocumentName.")
    if not isinstance(policy, CacheLookupVerificationPolicy):
        raise TypeError("policy must be a CacheLookupVerificationPolicy.")
    if not isinstance(filesystem, ReadOnlyCacheFilesystem):
        raise TypeError("filesystem must implement ReadOnlyCacheFilesystem.")

    read = filesystem.read_regular_file_bounded(
        Path(entry_path) / name.value,
        max_bytes=getattr(policy, _CACHE_DOCUMENT_LIMIT_FIELDS[name]),
    )
    malformed = _CACHE_DOCUMENT_MALFORMED_CLASSIFICATIONS[name]
    if read.limit_exceeded:
        return _CacheDocumentObservation(
            name,
            malformed,
            stable_read=read.stable_read,
        )
    assert read.data is not None

    try:
        observed_versions, unsupported = _probe_cache_document_versions(name, read.data)
    except _VersionProbeError:
        return _CacheDocumentObservation(
            name,
            malformed,
            stored_bytes=read.data,
            stable_read=read.stable_read,
        )
    if unsupported is not None:
        return _CacheDocumentObservation(
            name,
            unsupported,
            stored_bytes=read.data,
            observed_versions=observed_versions,
            stable_read=read.stable_read,
        )

    try:
        if name is _CacheDocumentName.METADATA:
            observed_metadata = _parse_observed_cache_entry_metadata_v1(read.data)
            model = None
        else:
            observed_metadata = None
            model = _CACHE_DOCUMENT_MODEL_TYPES[name].from_json(read.data)
    except CacheEntryContractError:
        return _CacheDocumentObservation(
            name,
            malformed,
            stored_bytes=read.data,
            observed_versions=observed_versions,
            stable_read=read.stable_read,
        )
    return _CacheDocumentObservation(
        name,
        _CacheDocumentClassification.VALID,
        stored_bytes=read.data,
        model=model,
        observed_metadata=observed_metadata,
        observed_versions=observed_versions,
        stable_read=read.stable_read,
    )


def _read_and_parse_final_entry_documents(
    entry_path: str | Path,
    *,
    policy: CacheLookupVerificationPolicy,
    filesystem: ReadOnlyCacheFilesystem = DEFAULT_READ_ONLY_FILESYSTEM,
) -> _FinalEntryDocumentsObservation:
    """Process COMPLETE, metadata, then manifest; stop at the first rejection."""

    complete = _read_and_parse_cache_document(
        entry_path,
        _CacheDocumentName.COMPLETE,
        policy=policy,
        filesystem=filesystem,
    )
    if complete.classification is not _CacheDocumentClassification.VALID:
        return _FinalEntryDocumentsObservation(complete.classification, complete)

    metadata = _read_and_parse_cache_document(
        entry_path,
        _CacheDocumentName.METADATA,
        policy=policy,
        filesystem=filesystem,
    )
    if metadata.classification is not _CacheDocumentClassification.VALID:
        return _FinalEntryDocumentsObservation(
            metadata.classification, complete, metadata
        )

    manifest = _read_and_parse_cache_document(
        entry_path,
        _CacheDocumentName.MANIFEST,
        policy=policy,
        filesystem=filesystem,
    )
    return _FinalEntryDocumentsObservation(
        manifest.classification,
        complete,
        metadata,
        manifest,
    )


def _validate_final_entry_document_integrity(
    cache_root: str | Path,
    entry_path: str | Path,
    documents: _FinalEntryDocumentsObservation,
    *,
    cache_key: CacheKey,
    namespace: CacheNamespace,
    expectation: CacheLookupExpectation,
    artifact_expectation: _CacheArtifactExpectation | None = None,
) -> _FinalEntryDocumentIntegrityObservation:
    """Validate Step 5B2C identity and document integrity without payload access."""

    if not isinstance(documents, _FinalEntryDocumentsObservation):
        raise TypeError("documents must be a _FinalEntryDocumentsObservation.")
    if documents.classification is not _CacheDocumentClassification.VALID:
        raise ValueError("documents must have completed Step 5B2B successfully.")
    if not isinstance(cache_key, CacheKey):
        raise TypeError("cache_key must be a CacheKey.")
    if not isinstance(namespace, CacheNamespace):
        raise TypeError("namespace must be a CacheNamespace.")
    if not isinstance(expectation, CacheLookupExpectation):
        raise TypeError("expectation must be a CacheLookupExpectation.")
    if expectation.namespace != namespace:
        raise ValueError("expectation namespace must equal the expected namespace.")
    if artifact_expectation is not None and not isinstance(
        artifact_expectation, _CacheArtifactExpectation
    ):
        raise TypeError("artifact_expectation must be private expectation data or None.")

    complete_observation = documents.complete
    metadata_observation = documents.metadata
    manifest_observation = documents.manifest
    if metadata_observation is None or manifest_observation is None:
        raise ValueError("valid documents must include metadata and manifest observations.")
    marker = complete_observation.model
    observed = metadata_observation.observed_metadata
    manifest = manifest_observation.model
    if not isinstance(marker, CompletenessMarker):
        raise ValueError("valid COMPLETE observation must contain its Step 5A model.")
    if not isinstance(observed, _ObservedCacheEntryMetadataV1):
        raise ValueError("valid metadata observation must contain private typed fields.")
    if not isinstance(manifest, PayloadManifest):
        raise ValueError("valid manifest observation must contain its Step 5A model.")
    if metadata_observation.stored_bytes is None or manifest_observation.stored_bytes is None:
        raise ValueError("valid document observations must retain exact stored bytes.")

    def result(
        classification: _CacheDocumentIntegrityClassification,
        *,
        level: _CacheVerificationLevel = _CacheVerificationLevel.CANONICAL_DOCUMENTS,
        metadata: CacheEntryMetadata | None = None,
    ) -> _FinalEntryDocumentIntegrityObservation:
        return _FinalEntryDocumentIntegrityObservation(
            classification,
            level,
            documents,
            metadata,
        )

    expected_digest = derive_entry_digest(cache_key)
    expected_path = derive_final_entry_path(cache_root, namespace, cache_key)
    reconstructed_digest = derive_entry_digest(observed.cache_key.to_cache_key())
    if (
        Path(entry_path) != expected_path
        or observed.entry_digest != reconstructed_digest
        or observed.entry_digest != expected_digest
        or observed.namespace != namespace
        or marker.entry_digest != expected_digest
    ):
        return result(_CacheDocumentIntegrityClassification.ENTRY_IDENTITY_CONFLICT)

    expected_reference = CacheKeyReference.from_cache_key(cache_key)
    if observed.cache_key != expected_reference:
        return result(_CacheDocumentIntegrityClassification.CACHE_KEY_CONFLICT)

    manifest_sha256 = (
        "sha256:"
        + hashlib.sha256(manifest_observation.stored_bytes).hexdigest()
    )
    metadata_sha256 = (
        "sha256:"
        + hashlib.sha256(metadata_observation.stored_bytes).hexdigest()
    )
    if (
        observed.payload_manifest_digest != manifest_sha256
        or marker.manifest_digest != manifest_sha256
    ):
        return result(_CacheDocumentIntegrityClassification.MANIFEST_DIGEST_MISMATCH)
    if marker.metadata_digest != metadata_sha256:
        return result(_CacheDocumentIntegrityClassification.METADATA_DIGEST_MISMATCH)
    try:
        _validate_payload_summary(
            observed.payload_file_count,
            observed.payload_total_bytes,
            manifest,
        )
    except CacheEntryContractError:
        return result(
            _CacheDocumentIntegrityClassification.METADATA_MANIFEST_SUMMARY_MISMATCH
        )

    if (
        observed.namespace.producer_id != observed.producer.producer_id
        or observed.namespace.producer_schema_version
        != observed.producer.producer_schema_version
    ):
        return result(_CacheDocumentIntegrityClassification.NAMESPACE_PRODUCER_CONFLICT)
    if observed.producer.producer_id != expectation.producer_id:
        return result(_CacheDocumentIntegrityClassification.PRODUCER_MISMATCH)
    if (
        observed.producer.producer_schema_version
        != expectation.producer_schema_version
    ):
        return result(_CacheDocumentIntegrityClassification.SCHEMA_MISMATCH)

    if artifact_expectation is not None and (
        observed.artifact.artifact_kind != artifact_expectation.artifact_kind
        or observed.artifact.artifact_contract_version
        != artifact_expectation.artifact_contract_version
        or (
            artifact_expectation.expected_logical_id is not None
            and observed.artifact.logical_id
            != artifact_expectation.expected_logical_id
        )
    ):
        return result(_CacheDocumentIntegrityClassification.ARTIFACT_MISMATCH)
    if observed.runtime_fingerprint != expectation.runtime_fingerprint:
        return result(
            _CacheDocumentIntegrityClassification.RUNTIME_FINGERPRINT_MISMATCH
        )

    strict_metadata = observed.to_strict_metadata()
    return result(
        _CacheDocumentIntegrityClassification.VALID,
        level=_CacheVerificationLevel.DOCUMENT_INTEGRITY,
        metadata=strict_metadata,
    )


def _validate_final_entry_payload(
    entry_path: str | Path,
    document_integrity: _FinalEntryDocumentIntegrityObservation,
    *,
    payload_expectation: ProducerPayloadExpectation,
    policy: CacheLookupVerificationPolicy,
    filesystem: _PayloadReadOnlyCacheFilesystem = DEFAULT_READ_ONLY_FILESYSTEM,
) -> _PayloadValidationObservation:
    """Validate one manifest-authoritative payload tree without public lookup state."""

    if not isinstance(
        document_integrity, _FinalEntryDocumentIntegrityObservation
    ) or (
        document_integrity.classification
        is not _CacheDocumentIntegrityClassification.VALID
        or document_integrity.verification_level
        is not _CacheVerificationLevel.DOCUMENT_INTEGRITY
        or not isinstance(document_integrity.metadata, CacheEntryMetadata)
    ):
        raise ValueError("Step 5B3 requires successful document-integrity validation.")
    if not isinstance(payload_expectation, ProducerPayloadExpectation):
        raise TypeError("payload_expectation must be trusted producer semantics.")
    if not isinstance(policy, CacheLookupVerificationPolicy):
        raise TypeError("policy must be a CacheLookupVerificationPolicy.")
    if not isinstance(filesystem, _PayloadReadOnlyCacheFilesystem):
        raise TypeError(
            "filesystem must implement the read-only payload streaming interface."
        )
    manifest_observation = document_integrity.documents.manifest
    if manifest_observation is None or not isinstance(
        manifest_observation.model, PayloadManifest
    ):
        raise ValueError("Document-integrity result must retain its manifest model.")
    manifest = manifest_observation.model
    metadata = document_integrity.metadata

    def result(
        classification: _PayloadValidationClassification,
        *,
        observed: tuple[str, ...] = (),
        hash_order: tuple[str, ...] = (),
        bytes_hashed: int = 0,
        fully_hashed: bool = False,
    ) -> _PayloadValidationObservation:
        return _PayloadValidationObservation(
            classification,
            _CacheVerificationLevel.DOCUMENT_INTEGRITY,
            observed,
            hash_order,
            bytes_hashed,
            fully_hashed,
        )

    if not _payload_cardinality_is_valid(
        payload_expectation,
        payload_file_count=metadata.payload_file_count,
        payload_total_bytes=metadata.payload_total_bytes,
        manifest=manifest,
    ):
        return result(_PayloadValidationClassification.PAYLOAD_CARDINALITY_INVALID)

    records = manifest.files
    declared_paths = tuple(record.relative_path for record in records)
    if len(records) > policy.max_payload_records:
        return result(_PayloadValidationClassification.PAYLOAD_POLICY_LIMIT_EXCEEDED)
    if any(
        len(path.encode("utf-8")) > policy.max_relative_path_utf8_bytes
        or len(PurePosixPath(path).parts) > policy.max_payload_depth
        or record.size_bytes > policy.max_individual_payload_bytes
        for path, record in zip(declared_paths, records)
    ) or sum(record.size_bytes for record in records) > policy.max_total_payload_bytes:
        return result(_PayloadValidationClassification.PAYLOAD_POLICY_LIMIT_EXCEEDED)

    declared_set = frozenset(declared_paths)
    required_directories = frozenset(
        "/".join(parts[:index])
        for path in declared_paths
        for parts in (PurePosixPath(path).parts,)
        for index in range(1, len(parts))
    )
    payload_root = Path(entry_path) / "payload"
    try:
        root_identity = filesystem.inspect(payload_root)
    except FileNotFoundError:
        return result(_PayloadValidationClassification.PAYLOAD_MISSING)
    if root_identity.object_type is not FilesystemObjectType.DIRECTORY:
        return result(_PayloadValidationClassification.UNSAFE_OBJECT)

    observed_identities: dict[str, FileIdentity] = {}

    def enumerate_directory(
        directory: Path, relative_directory: str | None
    ) -> _PayloadValidationClassification | None:
        try:
            names = tuple(sorted(filesystem.list_directory(directory)))
            identities = tuple(
                (name, filesystem.inspect(directory / name)) for name in names
            )
        except FileNotFoundError:
            return _PayloadValidationClassification.PAYLOAD_MISSING

        unsafe = tuple(
            name
            for name, identity in identities
            if identity.object_type
            not in {FilesystemObjectType.REGULAR_FILE, FilesystemObjectType.DIRECTORY}
        )
        if unsafe:
            return _PayloadValidationClassification.UNSAFE_OBJECT

        child_directories: list[tuple[str, Path]] = []
        for name, identity in identities:
            relative = name if relative_directory is None else f"{relative_directory}/{name}"
            if identity.object_type is FilesystemObjectType.REGULAR_FILE:
                observed_identities[relative] = identity
                if relative not in declared_set:
                    return _PayloadValidationClassification.UNEXPECTED_PAYLOAD_OBJECT
            else:
                if relative not in required_directories:
                    return _PayloadValidationClassification.UNEXPECTED_PAYLOAD_OBJECT
                child_directories.append((relative, directory / name))
        for relative, child in child_directories:
            rejected = enumerate_directory(child, relative)
            if rejected is not None:
                return rejected
        return None

    rejected = enumerate_directory(payload_root, None)
    observed_paths = tuple(sorted(observed_identities))
    if rejected is not None:
        return result(rejected, observed=observed_paths)
    if observed_paths != declared_paths:
        return result(
            _PayloadValidationClassification.PAYLOAD_MISSING,
            observed=observed_paths,
        )

    bytes_hashed = 0
    hash_order: list[str] = []
    for record in records:
        identity = observed_identities[record.relative_path]
        if identity.link_count is not None and identity.link_count > 1:
            return result(
                _PayloadValidationClassification.PAYLOAD_HARDLINK_DETECTED,
                observed=observed_paths,
                hash_order=tuple(hash_order),
                bytes_hashed=bytes_hashed,
            )
        if identity.size != record.size_bytes:
            return result(
                _PayloadValidationClassification.PAYLOAD_SIZE_MISMATCH,
                observed=observed_paths,
                hash_order=tuple(hash_order),
                bytes_hashed=bytes_hashed,
            )
        candidate = payload_root.joinpath(*PurePosixPath(record.relative_path).parts)
        try:
            streamed = filesystem.stream_regular_file_sha256(
                candidate,
                declared_size=record.size_bytes,
                chunk_size=policy.read_chunk_size,
            )
        except FileNotFoundError:
            return result(
                _PayloadValidationClassification.PAYLOAD_MISSING,
                observed=observed_paths,
                hash_order=tuple(hash_order),
                bytes_hashed=bytes_hashed,
            )
        except (SymlinkRejectedError, UnsupportedFilesystemObjectError):
            return result(
                _PayloadValidationClassification.UNSAFE_OBJECT,
                observed=observed_paths,
                hash_order=tuple(hash_order),
                bytes_hashed=bytes_hashed,
            )
        except UnstableFilesystemObjectError:
            return result(
                _PayloadValidationClassification.PAYLOAD_READ_UNSTABLE,
                observed=observed_paths,
                hash_order=tuple(hash_order),
                bytes_hashed=bytes_hashed,
            )
        if streamed.bytes_read != record.size_bytes or streamed.has_additional_byte:
            return result(
                _PayloadValidationClassification.PAYLOAD_SIZE_MISMATCH,
                observed=observed_paths,
                hash_order=tuple(hash_order),
                bytes_hashed=bytes_hashed + streamed.bytes_read,
            )
        hash_order.append(record.relative_path)
        bytes_hashed += streamed.bytes_read
        if streamed.digest != record.digest:
            return result(
                _PayloadValidationClassification.PAYLOAD_DIGEST_MISMATCH,
                observed=observed_paths,
                hash_order=tuple(hash_order),
                bytes_hashed=bytes_hashed,
            )

    return result(
        _PayloadValidationClassification.VALID,
        observed=observed_paths,
        hash_order=tuple(hash_order),
        bytes_hashed=bytes_hashed,
        fully_hashed=True,
    )
