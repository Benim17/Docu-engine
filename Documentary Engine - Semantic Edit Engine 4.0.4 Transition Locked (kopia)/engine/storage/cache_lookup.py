"""Read-only filesystem primitives and internal cache-entry structure validation.

This module does not interpret cache documents or expose public lookup
orchestration.  Its local adapter exposes metadata inspection, immediate
directory listing, path resolution, and bounded regular-file reads only.
"""

from __future__ import annotations

import os
import errno
import json
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable

from .persistent_cache import (
    CACHE_ENTRY_CONTRACT_VERSION,
    CACHE_KEY_CANONICAL_VERSION,
    PAYLOAD_MANIFEST_VERSION,
    RUNTIME_FINGERPRINT_SCHEMA_VERSION,
    CacheEntryContractError,
    CacheEntryMetadata,
    CompletenessMarker,
    PayloadManifest,
)


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
    observed_versions: tuple[tuple[str, int], ...] = ()
    stable_read: bool | None = None


@dataclass(frozen=True)
class _FinalEntryDocumentsObservation:
    classification: _CacheDocumentClassification
    complete: _CacheDocumentObservation
    metadata: _CacheDocumentObservation | None = None
    manifest: _CacheDocumentObservation | None = None


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

    model_type = _CACHE_DOCUMENT_MODEL_TYPES[name]
    try:
        model = model_type.from_json(read.data)
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
