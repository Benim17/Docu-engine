"""Read-only filesystem primitives for persistent cache lookup Step 5B1.

This module does not interpret cache documents or classify cache entries.  Its
local adapter exposes metadata inspection, immediate directory listing, path
resolution, and bounded regular-file reads only.
"""

from __future__ import annotations

import os
import errno
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable


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
