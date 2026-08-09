import hashlib
import os
import socket
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from engine.storage.cache_keys import CacheKey
from engine.storage.cache_lookup import CacheLookupVerificationPolicy, ValidatedCacheRoot
from engine.storage.cache_writer import (
    CacheStagingWriteError,
    CacheStagingWriteRequest,
    LocalCacheStagingFilesystem,
    StagingPayloadSource,
    write_cache_staging_entry,
)
from engine.storage.persistent_cache import (
    CacheArtifactMetadata,
    CacheNamespace,
    CacheProducerMetadata,
    CacheRuntimeFingerprint,
    PayloadManifest,
    derive_final_entry_path,
    derive_lock_path,
    derive_staging_entry_path,
)


def _request(tmp_path, specs):
    root_path = tmp_path / "cache"
    root_path.mkdir(parents=True)
    namespace = CacheNamespace("audio", "transcription.whisper", 3)
    sources = []
    for index, (relative, content) in enumerate(specs):
        source = tmp_path / f"source-{index}"
        source.write_bytes(content)
        sources.append(StagingPayloadSource(source.resolve(), relative))
    return CacheStagingWriteRequest(
        ValidatedCacheRoot.from_path(root_path), namespace, CacheKey("a" * 64),
        "writer-1", CacheArtifactMetadata("transcript", "logical-1", 1),
        CacheProducerMetadata("transcription.whisper", "1.0", 3),
        CacheRuntimeFingerprint(1, {"model": "large-v3"}),
        "2026-07-20T09:00:00Z", tuple(sources), CacheLookupVerificationPolicy(),
    )


@pytest.mark.parametrize("specs", [
    (("one.bin", b"one"),),
    (("a.bin", b"a"), ("nested/b.bin", b"bb")),
    (("empty.bin", b""),),
])
def test_step5c_writes_verified_complete_staging_entry_only(tmp_path, specs):
    request = _request(tmp_path, specs)
    result = write_cache_staging_entry(request)
    assert result.staging_path.exists()
    assert (result.staging_path / "COMPLETE").is_file()
    assert PayloadManifest.from_json(
        (result.staging_path / "manifest.json").read_bytes()
    ) == result.manifest
    manifest_bytes = (result.staging_path / "manifest.json").read_bytes()
    metadata_bytes = (result.staging_path / "metadata.json").read_bytes()
    assert result.metadata.payload_manifest_digest == (
        "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()
    )
    assert result.marker.manifest_digest == result.metadata.payload_manifest_digest
    assert result.marker.metadata_digest == (
        "sha256:" + hashlib.sha256(metadata_bytes).hexdigest()
    )
    for record, (_, content) in zip(result.manifest.files, specs):
        assert record.size_bytes == len(content)
        assert record.digest == "sha256:" + hashlib.sha256(content).hexdigest()
    assert not derive_final_entry_path(
        request.cache_root.resolved_path, request.namespace, request.cache_key
    ).exists()
    assert not derive_lock_path(
        request.cache_root.resolved_path, request.namespace, request.cache_key
    ).exists()


def test_step5c_rejects_existing_staging_without_overwrite(tmp_path):
    request = _request(tmp_path, (("one.bin", b"one"),))
    first = write_cache_staging_entry(request)
    before = tuple(sorted(path.relative_to(first.staging_path) for path in first.staging_path.rglob("*")))
    with pytest.raises(CacheStagingWriteError, match="already exists"):
        write_cache_staging_entry(request)
    assert tuple(sorted(path.relative_to(first.staging_path) for path in first.staging_path.rglob("*"))) == before


def test_step5c_rejects_symlink_and_directory_sources(tmp_path):
    request = _request(tmp_path, (("one.bin", b"one"),))
    target = request.payload_sources[0].source_path
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(CacheStagingWriteError, match="regular file"):
        write_cache_staging_entry(
            CacheStagingWriteRequest(
                request.cache_root, request.namespace, request.cache_key, "writer-link",
                request.artifact, request.producer, request.runtime_fingerprint,
                request.created_at_utc, (StagingPayloadSource(link.absolute(), "one.bin"),),
                request.policy,
            )
        )


def test_step5c_complete_is_absent_after_injected_document_failure(tmp_path):
    request = _request(tmp_path, (("one.bin", b"one"),))

    class FailingFilesystem(LocalCacheStagingFilesystem):
        def write_new_file(self, path, data):
            if path.name == "metadata.json":
                raise CacheStagingWriteError("injected metadata failure")
            super().write_new_file(path, data)

    with pytest.raises(CacheStagingWriteError, match="injected"):
        write_cache_staging_entry(request, filesystem=FailingFilesystem())
    staging = request.cache_root.resolved_path / "staging"
    assert not any(path.name == "COMPLETE" for path in staging.rglob("*"))


def _staging_path(request):
    return derive_staging_entry_path(
        request.cache_root.resolved_path, request.namespace, request.cache_key,
        request.writer_token,
    )


def _assert_no_complete_or_final(request):
    assert not (_staging_path(request) / "COMPLETE").exists()
    assert not derive_final_entry_path(
        request.cache_root.resolved_path, request.namespace, request.cache_key
    ).exists()


@pytest.mark.parametrize("kind", ["symlink", "file", "directory", "fifo"])
def test_step5c_rejects_preexisting_staging_objects(tmp_path, kind):
    request = _request(tmp_path, (("one.bin", b"one"),))
    staging = _staging_path(request)
    staging.parent.mkdir(parents=True)
    if kind == "symlink":
        target = tmp_path / "target"
        target.mkdir()
        staging.symlink_to(target, target_is_directory=True)
    elif kind == "file":
        staging.write_bytes(b"existing")
    elif kind == "directory":
        staging.mkdir()
    else:
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO unavailable")
        os.mkfifo(staging)
    request = replace(
        request,
        cache_root=ValidatedCacheRoot.from_path(request.cache_root.resolved_path),
    )
    with pytest.raises(CacheStagingWriteError):
        write_cache_staging_entry(request)
    _assert_no_complete_or_final(request)


def test_step5c_rejects_unsafe_existing_staging_ancestor(tmp_path):
    request = _request(tmp_path, (("one.bin", b"one"),))
    staging_root = request.cache_root.resolved_path / "staging"
    target = tmp_path / "outside"
    target.mkdir()
    staging_root.symlink_to(target, target_is_directory=True)
    request = replace(request, cache_root=ValidatedCacheRoot.from_path(request.cache_root.resolved_path))
    with pytest.raises(CacheStagingWriteError, match="unsafe"):
        write_cache_staging_entry(request)
    _assert_no_complete_or_final(request)


class _ReplacingFilesystem(LocalCacheStagingFilesystem):
    def __init__(self, target):
        self.target = target
        self.replaced = False

    def validate_directory_chain(self, root, directory):
        matches = {
            "staging_parent": directory.name == "3",
            "entry": len(directory.name) > 64 and ".writer-1" in directory.name,
            "payload": directory.name == "payload",
            "nested": directory.name == "nested",
        }
        if not self.replaced and matches[self.target]:
            moved = directory.with_name(directory.name + "-moved")
            directory.rename(moved)
            directory.symlink_to(moved, target_is_directory=True)
            self.replaced = True
        super().validate_directory_chain(root, directory)


@pytest.mark.parametrize("target", ["staging_parent", "entry", "payload", "nested"])
def test_step5c_fails_closed_on_destination_ancestor_replacement(tmp_path, target):
    specs = (("nested/one.bin", b"one"),) if target == "nested" else (("one.bin", b"one"),)
    request = _request(tmp_path, specs)
    with pytest.raises(CacheStagingWriteError, match="unsafe"):
        write_cache_staging_entry(request, filesystem=_ReplacingFilesystem(target))
    _assert_no_complete_or_final(request)


@pytest.mark.parametrize("stage", ["payload", "manifest", "metadata", "before_complete", "complete"])
def test_step5c_fault_injection_never_publishes_valid_complete(tmp_path, stage):
    request = _request(tmp_path, (("one.bin", b"one"),))

    class FaultFilesystem(LocalCacheStagingFilesystem):
        def copy_regular_file(self, source, destination, *, chunk_size):
            if stage == "payload":
                raise CacheStagingWriteError("injected payload failure")
            return super().copy_regular_file(source, destination, chunk_size=chunk_size)

        def before_complete(self, path):
            if stage == "before_complete":
                raise CacheStagingWriteError("injected pre-COMPLETE failure")

        def write_new_file(self, path, data):
            if path.name == stage + ".json":
                raise CacheStagingWriteError("injected document failure")
            if stage == "complete" and path.name == "COMPLETE":
                super().write_new_file(path, b"{")
                raise CacheStagingWriteError("injected partial COMPLETE failure")
            super().write_new_file(path, data)

    with pytest.raises(CacheStagingWriteError, match="injected"):
        write_cache_staging_entry(request, filesystem=FaultFilesystem())
    complete = _staging_path(request) / "COMPLETE"
    if stage == "complete":
        assert complete.read_bytes() == b"{"
        with pytest.raises(Exception):
            from engine.storage.persistent_cache import CompletenessMarker
            CompletenessMarker.from_json(complete.read_bytes())
    else:
        assert not complete.exists()
    assert not derive_final_entry_path(
        request.cache_root.resolved_path, request.namespace, request.cache_key
    ).exists()


def test_step5c_complete_is_last_mutation_after_verification(tmp_path):
    request = _request(tmp_path, (("nested/one.bin", b"one"),))
    operations = []

    class LoggingFilesystem(LocalCacheStagingFilesystem):
        def make_directory(self, path):
            operations.append("mkdir:" + path.name)
            super().make_directory(path)
        def copy_regular_file(self, source, destination, *, chunk_size):
            operations.append("payload:" + destination.name)
            return super().copy_regular_file(source, destination, chunk_size=chunk_size)
        def write_new_file(self, path, data):
            operations.append("write:" + path.name)
            super().write_new_file(path, data)
        def verification_completed(self, path):
            operations.append("verified")

    write_cache_staging_entry(request, filesystem=LoggingFilesystem())
    assert operations[-4:] == ["write:manifest.json", "write:metadata.json", "verified", "write:COMPLETE"]


@pytest.mark.parametrize("change", ["disappear_before_open", "replace_before_open", "during", "after"])
def test_step5c_source_change_is_deterministic_failure(tmp_path, change):
    request = _request(tmp_path, (("one.bin", b"one"),))

    class ChangingFilesystem(LocalCacheStagingFilesystem):
        def _source_inspected(self, source):
            if change == "disappear_before_open":
                source.unlink()
            elif change == "replace_before_open":
                source.unlink(); source.write_bytes(b"replacement")
        def _source_opened(self, source):
            if change == "during":
                source.write_bytes(b"changed")
        def _source_streamed(self, source):
            if change == "after":
                source.write_bytes(b"changed")

    with pytest.raises(CacheStagingWriteError):
        write_cache_staging_entry(request, filesystem=ChangingFilesystem())
    _assert_no_complete_or_final(request)


@pytest.mark.parametrize("relative", ["../escape", "a/../../escape", "/absolute", "", ".", "a//b", "a/"])
def test_step5c_reuses_manifest_path_rejection(tmp_path, relative):
    source = tmp_path / "source"
    source.write_bytes(b"x")
    with pytest.raises(Exception):
        StagingPayloadSource(source.resolve(), relative)


def test_step5c_rejects_duplicate_case_and_file_directory_collisions(tmp_path):
    request = _request(tmp_path, (("a", b"a"),))
    source = request.payload_sources[0]
    for paths in (("a", "a"), ("A.txt", "a.txt"), ("a", "a/b.txt")):
        items = tuple(replace(source, relative_path=path) for path in paths)
        with pytest.raises(ValueError, match="collide|unique"):
            replace(request, payload_sources=items)


def test_step5c_rejects_directory_fifo_socket_and_hardlink_sources(tmp_path):
    request = _request(tmp_path, (("one.bin", b"one"),))
    objects = []
    directory = tmp_path / "directory"; directory.mkdir(); objects.append(directory)
    if hasattr(os, "mkfifo"):
        fifo = tmp_path / "fifo"; os.mkfifo(fifo); objects.append(fifo)
    hardlink = tmp_path / "hardlink"; os.link(request.payload_sources[0].source_path, hardlink); objects.append(hardlink)
    with tempfile.TemporaryDirectory(prefix="s5c-", dir="/tmp") as short:
        socket_path = Path(short) / "socket"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(str(socket_path)); objects.append(socket_path)
            for index, source in enumerate(objects):
                candidate = replace(
                    request, writer_token=f"unsafe-{index}",
                    payload_sources=(StagingPayloadSource(source.absolute(), "one.bin"),),
                )
                with pytest.raises(CacheStagingWriteError, match="regular file"):
                    write_cache_staging_entry(candidate)
                _assert_no_complete_or_final(candidate)
        finally:
            server.close()
