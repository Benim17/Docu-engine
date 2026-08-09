"""Step 5C staging-only persistent cache writer."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol, runtime_checkable

from .cache_keys import CacheKey
from .cache_lookup import (
    CacheLookupVerificationPolicy,
    LocalReadOnlyCacheFilesystem,
    ValidatedCacheRoot,
)
from .persistent_cache import (
    CacheArtifactMetadata,
    CacheEntryContractError,
    CacheEntryMetadata,
    CacheKeyReference,
    CacheNamespace,
    CacheProducerMetadata,
    CacheRuntimeFingerprint,
    CompletenessMarker,
    PayloadManifest,
    PayloadManifestRecord,
    derive_entry_digest,
    derive_staging_entry_path,
)


class CacheStagingWriteError(RuntimeError):
    """Deterministic refusal or failure while constructing one staging entry."""


@dataclass(frozen=True)
class StagingPayloadSource:
    source_path: Path
    relative_path: str
    media_type: str = "application/octet-stream"
    role: str = "primary"

    def __post_init__(self) -> None:
        if not isinstance(self.source_path, Path) or not self.source_path.is_absolute():
            raise TypeError("source_path must be an absolute Path.")
        # Reuse the normative portable manifest-path validator.
        PayloadManifestRecord(self.relative_path, 0, "sha256:" + "0" * 64,
                              self.media_type, self.role)


@dataclass(frozen=True)
class CacheStagingWriteRequest:
    cache_root: ValidatedCacheRoot
    namespace: CacheNamespace
    cache_key: CacheKey
    writer_token: str
    artifact: CacheArtifactMetadata
    producer: CacheProducerMetadata
    runtime_fingerprint: CacheRuntimeFingerprint
    created_at_utc: str
    payload_sources: tuple[StagingPayloadSource, ...]
    policy: CacheLookupVerificationPolicy = CacheLookupVerificationPolicy()

    def __post_init__(self) -> None:
        if not isinstance(self.cache_root, ValidatedCacheRoot):
            raise TypeError("cache_root must be a ValidatedCacheRoot.")
        if not isinstance(self.namespace, CacheNamespace) or not isinstance(self.cache_key, CacheKey):
            raise TypeError("namespace and cache_key must be validated models.")
        if not isinstance(self.payload_sources, tuple) or any(
            not isinstance(item, StagingPayloadSource) for item in self.payload_sources
        ):
            raise TypeError("payload_sources must be an immutable tuple.")
        paths = tuple(item.relative_path for item in self.payload_sources)
        if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
            raise ValueError("payload_sources must be sorted with unique relative paths.")
        folded = tuple(
            "".join(char.lower() if "A" <= char <= "Z" else char for char in path)
            for path in paths
        )
        if len(folded) != len(set(folded)):
            raise ValueError("payload source paths collide under ASCII-case comparison.")
        for path in paths:
            if any(other.startswith(path + "/") for other in paths):
                raise ValueError("payload source file/directory paths collide.")
        if self.producer.producer_id != self.namespace.producer_id or (
            self.producer.producer_schema_version
            != self.namespace.producer_schema_version
        ):
            raise ValueError("producer identity must match namespace.")
        # Validate token and timestamp through locked Step 5A constructors.
        derive_staging_entry_path(
            self.cache_root.resolved_path, self.namespace, self.cache_key,
            self.writer_token,
        )
        CacheEntryMetadata(
            derive_entry_digest(self.cache_key), CacheKeyReference.from_cache_key(self.cache_key),
            self.namespace, self.artifact, self.producer, self.runtime_fingerprint,
            self.created_at_utc, "sha256:" + "0" * 64, 0, 0,
        )


@dataclass(frozen=True)
class StagedCacheEntryReference:
    staging_path: Path
    entry_digest: str
    namespace: CacheNamespace
    cache_key_reference: CacheKeyReference
    metadata: CacheEntryMetadata
    manifest: PayloadManifest
    marker: CompletenessMarker
    payload_file_count: int
    payload_total_bytes: int


@runtime_checkable
class CacheStagingFilesystem(Protocol):
    def make_directory(self, path: Path) -> None: ...
    def copy_regular_file(self, source: Path, destination: Path, *, chunk_size: int) -> tuple[int, str]: ...
    def write_new_file(self, path: Path, data: bytes) -> None: ...
    def validate_directory_chain(self, root: Path, directory: Path) -> None: ...
    def verification_completed(self, staging_path: Path) -> None: ...
    def before_complete(self, complete_path: Path) -> None: ...


class LocalCacheStagingFilesystem:
    """Narrow exclusive-creation adapter; it has no rename or deletion operation."""

    def validate_directory_chain(self, root: Path, directory: Path) -> None:
        try:
            relative = directory.relative_to(root)
        except ValueError as exc:
            raise CacheStagingWriteError("staging directory escaped cache root.") from exc
        current = root
        for part in relative.parts:
            current = current / part
            try:
                observation = os.lstat(current)
            except OSError as exc:
                raise CacheStagingWriteError("staging directory chain changed.") from exc
            if not stat.S_ISDIR(observation.st_mode):
                raise CacheStagingWriteError("unsafe staging directory chain.")

    def verification_completed(self, staging_path: Path) -> None:
        return None

    def before_complete(self, complete_path: Path) -> None:
        return None

    def _source_inspected(self, source: Path) -> None:
        return None

    def _source_opened(self, source: Path) -> None:
        return None

    def _source_streamed(self, source: Path) -> None:
        return None

    def make_directory(self, path: Path) -> None:
        try:
            path.mkdir()
        except FileExistsError as exc:
            raise CacheStagingWriteError("staging destination already exists.") from exc
        except OSError as exc:
            raise CacheStagingWriteError("staging directory creation failed.") from exc

    def _open_new(self, path: Path) -> int:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            return os.open(path, flags, 0o600)
        except OSError as exc:
            raise CacheStagingWriteError("exclusive staging file creation failed.") from exc

    def write_new_file(self, path: Path, data: bytes) -> None:
        descriptor = self._open_new(path)
        try:
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise CacheStagingWriteError("staging write made no progress.")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def copy_regular_file(self, source: Path, destination: Path, *, chunk_size: int) -> tuple[int, str]:
        try:
            before = os.lstat(source)
        except OSError as exc:
            raise CacheStagingWriteError("payload source inspection failed.") from exc
        if not stat.S_ISREG(before.st_mode) or before.st_nlink > 1:
            raise CacheStagingWriteError("payload source must be a non-hardlinked regular file.")
        self._source_inspected(source)
        source_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        destination_fd = self._open_new(destination)
        source_fd: int | None = None
        digest = hashlib.sha256()
        size = 0
        try:
            source_fd = os.open(source, source_flags)
            opened = os.fstat(source_fd)
            self._source_opened(source)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise CacheStagingWriteError("payload source changed before copy.")
            while True:
                chunk = os.read(source_fd, chunk_size)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
                view = memoryview(chunk)
                while view:
                    view = view[os.write(destination_fd, view):]
            after_handle = os.fstat(source_fd)
            self._source_streamed(source)
            after_path = os.lstat(source)
            identity = lambda value: (value.st_dev, value.st_ino, value.st_size,
                                      value.st_mtime_ns, value.st_ctime_ns)
            if identity(before) != identity(opened) or identity(opened) != identity(after_handle) or identity(after_handle) != identity(after_path):
                raise CacheStagingWriteError("payload source changed during copy.")
            os.fsync(destination_fd)
        except OSError as exc:
            raise CacheStagingWriteError("payload copy failed.") from exc
        finally:
            if source_fd is not None:
                os.close(source_fd)
            os.close(destination_fd)
        return size, "sha256:" + digest.hexdigest()


DEFAULT_CACHE_STAGING_FILESYSTEM = LocalCacheStagingFilesystem()


def write_cache_staging_entry(
    request: CacheStagingWriteRequest,
    *,
    filesystem: CacheStagingFilesystem = DEFAULT_CACHE_STAGING_FILESYSTEM,
) -> StagedCacheEntryReference:
    """Create and verify one complete staging entry without promotion or locking."""
    if not isinstance(request, CacheStagingWriteRequest):
        raise TypeError("request must be a CacheStagingWriteRequest.")
    if not isinstance(filesystem, CacheStagingFilesystem):
        raise TypeError("filesystem must implement CacheStagingFilesystem.")
    root = request.cache_root.resolved_path
    staging = derive_staging_entry_path(root, request.namespace, request.cache_key,
                                        request.writer_token)
    if staging != root / staging.relative_to(root):
        raise CacheStagingWriteError("derived staging path escaped cache root.")
    # Create only deterministic staging ancestors; reject any unsafe existing object.
    current = root
    for part in staging.relative_to(root).parts[:-1]:
        current = current / part
        if current.exists():
            if current.is_symlink() or not current.is_dir():
                raise CacheStagingWriteError("unsafe staging ancestor.")
        else:
            filesystem.make_directory(current)
        filesystem.validate_directory_chain(root, current)
    filesystem.make_directory(staging)
    filesystem.validate_directory_chain(root, staging)
    payload_root = staging / "payload"
    filesystem.make_directory(payload_root)
    filesystem.validate_directory_chain(root, payload_root)

    records: list[PayloadManifestRecord] = []
    if len(request.payload_sources) > request.policy.max_payload_records:
        raise CacheStagingWriteError("payload count exceeds writer policy.")
    total_bytes = 0
    for source in request.payload_sources:
        path = PurePosixPath(source.relative_path)
        if (len(source.relative_path.encode("utf-8"))
                > request.policy.max_relative_path_utf8_bytes
                or len(path.parts) > request.policy.max_payload_depth):
            raise CacheStagingWriteError("payload path exceeds writer policy.")
        destination = payload_root.joinpath(*path.parts)
        directory = payload_root
        for part in PurePosixPath(source.relative_path).parts[:-1]:
            directory = directory / part
            if not directory.exists():
                filesystem.make_directory(directory)
            elif directory.is_symlink() or not directory.is_dir():
                raise CacheStagingWriteError("unsafe payload destination ancestor.")
            filesystem.validate_directory_chain(root, directory)
        filesystem.validate_directory_chain(root, destination.parent)
        size, digest = filesystem.copy_regular_file(
            source.source_path, destination, chunk_size=request.policy.read_chunk_size
        )
        filesystem.validate_directory_chain(root, destination.parent)
        total_bytes += size
        if size > request.policy.max_individual_payload_bytes or total_bytes > request.policy.max_total_payload_bytes:
            raise CacheStagingWriteError("payload bytes exceed writer policy.")
        records.append(PayloadManifestRecord(source.relative_path, size, digest,
                                             source.media_type, source.role))

    manifest = PayloadManifest(tuple(records))
    manifest_bytes = manifest.canonical_bytes()
    if len(manifest_bytes) > request.policy.max_manifest_bytes:
        raise CacheStagingWriteError("manifest exceeds writer policy.")
    filesystem.validate_directory_chain(root, staging)
    filesystem.write_new_file(staging / "manifest.json", manifest_bytes)
    filesystem.validate_directory_chain(root, staging)
    manifest_digest = "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()
    metadata = CacheEntryMetadata(
        derive_entry_digest(request.cache_key), CacheKeyReference.from_cache_key(request.cache_key),
        request.namespace, request.artifact, request.producer, request.runtime_fingerprint,
        request.created_at_utc, manifest_digest, len(records), total_bytes,
    )
    metadata_bytes = metadata.canonical_bytes()
    if len(metadata_bytes) > request.policy.max_metadata_bytes:
        raise CacheStagingWriteError("metadata exceeds writer policy.")
    filesystem.write_new_file(staging / "metadata.json", metadata_bytes)
    filesystem.validate_directory_chain(root, staging)
    # Verify every fallible payload/document condition before creating COMPLETE.
    if PayloadManifest.from_json((staging / "manifest.json").read_bytes()) != manifest or CacheEntryMetadata.from_json((staging / "metadata.json").read_bytes()) != metadata:
        raise CacheStagingWriteError("completed staging documents failed verification.")
    reader = LocalReadOnlyCacheFilesystem()
    observed_payloads = tuple(
        sorted(
            str(path.relative_to(payload_root)).replace(os.sep, "/")
            for path in payload_root.rglob("*")
            if path.is_file()
        )
    )
    if observed_payloads != tuple(record.relative_path for record in records):
        raise CacheStagingWriteError("completed staging payload set failed verification.")
    for record in records:
        candidate = payload_root.joinpath(*PurePosixPath(record.relative_path).parts)
        observed = reader.stream_regular_file_sha256(
            candidate, declared_size=record.size_bytes,
            chunk_size=request.policy.read_chunk_size,
        )
        if (not observed.stable_read or observed.bytes_read != record.size_bytes
                or observed.has_additional_byte or observed.digest != record.digest):
            raise CacheStagingWriteError("completed staging payload failed verification.")
    marker = CompletenessMarker(
        metadata.entry_digest, "sha256:" + hashlib.sha256(metadata_bytes).hexdigest(),
        manifest_digest,
    )
    marker_bytes = marker.canonical_bytes()
    if len(marker_bytes) > request.policy.max_complete_bytes:
        raise CacheStagingWriteError("COMPLETE exceeds writer policy.")
    filesystem.verification_completed(staging)
    filesystem.validate_directory_chain(root, staging)
    filesystem.before_complete(staging / "COMPLETE")
    filesystem.validate_directory_chain(root, staging)
    filesystem.write_new_file(staging / "COMPLETE", marker_bytes)
    return StagedCacheEntryReference(staging, metadata.entry_digest, metadata.namespace,
                                     metadata.cache_key, metadata, manifest, marker,
                                     metadata.payload_file_count, metadata.payload_total_bytes)
