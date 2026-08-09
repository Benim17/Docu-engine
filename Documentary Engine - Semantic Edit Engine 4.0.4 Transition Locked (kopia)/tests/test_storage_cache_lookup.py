from dataclasses import FrozenInstanceError, fields, replace
import hashlib
import json
import os
from pathlib import Path
import socket
import tempfile
from datetime import datetime, timezone

import pytest

from engine.storage.cache_keys import CacheKey
from engine.storage.persistent_cache import (
    CacheArtifactMetadata,
    CacheEntryContractError,
    CacheEntryMetadata,
    CacheKeyReference,
    CacheLookupExpectation,
    CacheNamespace,
    CacheProducerMetadata,
    CacheRuntimeFingerprint,
    CompletenessMarker,
    PayloadManifest,
    PayloadManifestRecord,
    canonical_json_bytes,
    derive_entry_digest,
    derive_final_entry_path,
    derive_lock_path,
)

from engine.storage.cache_lookup import (
    DEFAULT_MAX_COMPLETE_BYTES,
    DEFAULT_MAX_DIAGNOSTICS,
    DEFAULT_MAX_INDIVIDUAL_PAYLOAD_BYTES,
    DEFAULT_MAX_MANIFEST_BYTES,
    DEFAULT_MAX_METADATA_BYTES,
    DEFAULT_MAX_PAYLOAD_DEPTH,
    DEFAULT_MAX_PAYLOAD_RECORDS,
    DEFAULT_MAX_RELATIVE_PATH_UTF8_BYTES,
    DEFAULT_MAX_TOTAL_PAYLOAD_BYTES,
    DEFAULT_READ_CHUNK_SIZE,
    BoundedFileRead,
    CacheLookupIOError,
    CacheLookupPermissionError,
    CacheLookupVerificationPolicy,
    FileIdentity,
    FilesystemObjectType,
    LocalReadOnlyCacheFilesystem,
    PayloadCardinalityExpectation,
    ProducerPayloadExpectation,
    ReadOnlyCacheFilesystem,
    SymlinkRejectedError,
    UnstableFilesystemObjectError,
    UnsupportedFilesystemObjectError,
    ValidatedCacheRoot,
    _FinalEntryStructureClassification,
    _CacheDocumentClassification,
    _CacheDocumentIntegrityClassification,
    _CacheDocumentName,
    _CacheArtifactExpectation,
    _CacheVerificationLevel,
    _CacheLookupDiagnostic,
    _CacheLookupDiagnosticCode,
    _CacheLookupObserverEvent,
    _CacheLookupSubject,
    _LockObservationClassification,
    _ObservedCacheEntryMetadataV1,
    _PayloadValidationClassification,
    _StreamedPayloadHash,
    _StableSnapshotClassification,
    LockObservationPolicy,
    _inspect_final_entry_structure,
    _capture_entry_snapshot,
    _finalize_diagnostics,
    _notify_observer,
    _observe_matching_lock,
    _parse_observed_cache_entry_metadata_v1,
    _payload_cardinality_is_valid,
    _read_and_parse_cache_document,
    _read_and_parse_final_entry_documents,
    _validate_final_entry_document_integrity,
    _validate_final_entry_payload,
    _validate_stable_entry_snapshot,
    _trusted_producer_payload_expectation,
)


FILESYSTEM = LocalReadOnlyCacheFilesystem()


def test_policy_exact_defaults_and_resource_only_surface():
    policy = CacheLookupVerificationPolicy()
    assert policy == CacheLookupVerificationPolicy(
        max_complete_bytes=4 * 1024,
        max_metadata_bytes=256 * 1024,
        max_manifest_bytes=8 * 1024 * 1024,
        max_payload_records=100_000,
        max_relative_path_utf8_bytes=1_024,
        max_payload_depth=64,
        max_individual_payload_bytes=1 << 40,
        max_total_payload_bytes=16 << 40,
        max_diagnostics=32,
        read_chunk_size=1 * 1024 * 1024,
    )
    assert (
        DEFAULT_MAX_COMPLETE_BYTES,
        DEFAULT_MAX_METADATA_BYTES,
        DEFAULT_MAX_MANIFEST_BYTES,
        DEFAULT_MAX_PAYLOAD_RECORDS,
        DEFAULT_MAX_RELATIVE_PATH_UTF8_BYTES,
        DEFAULT_MAX_PAYLOAD_DEPTH,
        DEFAULT_MAX_INDIVIDUAL_PAYLOAD_BYTES,
        DEFAULT_MAX_TOTAL_PAYLOAD_BYTES,
        DEFAULT_MAX_DIAGNOSTICS,
        DEFAULT_READ_CHUNK_SIZE,
    ) == (
        4 * 1024,
        256 * 1024,
        8 * 1024 * 1024,
        100_000,
        1_024,
        64,
        1 << 40,
        16 << 40,
        32,
        1 * 1024 * 1024,
    )
    names = {item.name for item in fields(policy)}
    assert all("empty" not in name and "producer" not in name for name in names)


def test_policy_is_immutable_and_accepts_lower_custom_limits():
    policy = CacheLookupVerificationPolicy(
        max_complete_bytes=1,
        max_metadata_bytes=2,
        max_manifest_bytes=3,
        max_payload_records=4,
        max_relative_path_utf8_bytes=5,
        max_payload_depth=6,
        max_individual_payload_bytes=7,
        max_total_payload_bytes=8,
        max_diagnostics=9,
        read_chunk_size=10,
    )
    assert policy.max_total_payload_bytes == 8
    with pytest.raises(FrozenInstanceError):
        policy.max_diagnostics = 20


@pytest.mark.parametrize("invalid", [0, -1, True, False, 1.5, "1", None])
def test_policy_rejects_non_positive_non_integer_and_boolean_values(invalid):
    for item in fields(CacheLookupVerificationPolicy):
        changes = {item.name: invalid}
        if item.name == "max_total_payload_bytes" and invalid == 1:
            changes["max_individual_payload_bytes"] = 1
        with pytest.raises(ValueError, match="positive integer"):
            CacheLookupVerificationPolicy(**changes)


def test_policy_rejects_individual_limit_above_total():
    with pytest.raises(ValueError, match="cannot exceed"):
        CacheLookupVerificationPolicy(
            max_individual_payload_bytes=11,
            max_total_payload_bytes=10,
        )


def test_inspect_classifies_regular_directory_and_symlink_without_following(tmp_path):
    regular = tmp_path / "regular"
    regular.write_bytes(b"abc")
    directory = tmp_path / "directory"
    directory.mkdir()
    link = tmp_path / "link"
    link.symlink_to(regular)
    regular_identity = FILESYSTEM.inspect(regular)
    directory_identity = FILESYSTEM.inspect(directory)
    link_identity = FILESYSTEM.inspect(link)
    assert regular_identity.object_type is FilesystemObjectType.REGULAR_FILE
    assert regular_identity.size == 3
    assert directory_identity.object_type is FilesystemObjectType.DIRECTORY
    assert link_identity.object_type is FilesystemObjectType.SYMLINK
    assert link_identity.file_id != regular_identity.file_id


def test_inspect_fifo_and_socket_without_opening_or_blocking(tmp_path):
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO creation is unavailable")
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    assert FILESYSTEM.inspect(fifo).object_type is FilesystemObjectType.FIFO

    with tempfile.TemporaryDirectory(prefix="s5b1-", dir="/tmp") as short_directory:
        socket_path = Path(short_directory) / "socket"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(str(socket_path))
            assert FILESYSTEM.inspect(socket_path).object_type is FilesystemObjectType.SOCKET
        finally:
            server.close()


def test_inspect_device_where_safely_available():
    device = Path("/dev/null")
    if not device.exists():
        pytest.skip("No safe device fixture")
    assert FILESYSTEM.inspect(device).object_type in {
        FilesystemObjectType.CHARACTER_DEVICE,
        FilesystemObjectType.OTHER,
    }


def test_inspect_missing_is_distinct_and_identity_is_deterministic(tmp_path):
    with pytest.raises(FileNotFoundError):
        FILESYSTEM.inspect(tmp_path / "missing")
    path = tmp_path / "stable"
    path.write_bytes(b"stable")
    first = FILESYSTEM.inspect(path)
    second = FILESYSTEM.inspect(path)
    assert first == second
    assert first.same_stable_object(second)
    assert first.device_id is None or isinstance(first.device_id, int)
    assert first.file_id is None or isinstance(first.file_id, int)


def test_inspect_translates_permission_and_general_io_without_raw_details(monkeypatch):
    def denied(_):
        raise PermissionError("private platform detail")

    monkeypatch.setattr(os, "lstat", denied)
    with pytest.raises(CacheLookupPermissionError) as caught:
        FILESYSTEM.inspect("ignored")
    assert "private platform detail" not in str(caught.value)

    def failed(_):
        raise OSError("private platform detail")

    monkeypatch.setattr(os, "lstat", failed)
    with pytest.raises(CacheLookupIOError) as caught:
        FILESYSTEM.inspect("ignored")
    assert "private platform detail" not in str(caught.value)


def test_file_identity_does_not_claim_stability_without_device_and_file_ids(tmp_path):
    identity = FILESYSTEM.inspect(tmp_path)
    unavailable = replace(identity, device_id=None, file_id=None)
    assert not unavailable.same_stable_object(unavailable)
    assert not identity.same_stable_object(replace(identity, size=identity.size + 1))


def test_inspection_does_not_mutate_file(tmp_path):
    path = tmp_path / "stable"
    path.write_bytes(b"content")
    before = (path.read_bytes(), path.stat().st_mtime_ns, tuple(tmp_path.iterdir()))
    FILESYSTEM.inspect(path)
    after = (path.read_bytes(), path.stat().st_mtime_ns, tuple(tmp_path.iterdir()))
    assert after == before


def test_validated_cache_root_accepts_absolute_existing_directory(tmp_path):
    root = ValidatedCacheRoot.from_path(str(tmp_path), filesystem=FILESYSTEM)
    assert root.lexical_path == tmp_path
    assert root.resolved_path == tmp_path.resolve()
    assert root.identity.object_type is FilesystemObjectType.DIRECTORY


@pytest.mark.parametrize("path", ["relative/cache", "./cache", "one/../cache"])
def test_validated_cache_root_rejects_relative_paths(path):
    with pytest.raises(ValueError, match="absolute"):
        ValidatedCacheRoot.from_path(path, filesystem=FILESYSTEM)


def test_validated_cache_root_rejects_lexical_dot_and_dot_dot(tmp_path):
    with pytest.raises(ValueError, match="dot components"):
        ValidatedCacheRoot.from_path(str(tmp_path) + "/./cache", filesystem=FILESYSTEM)
    with pytest.raises(ValueError, match="dot components"):
        ValidatedCacheRoot.from_path(str(tmp_path) + "/one/../cache", filesystem=FILESYSTEM)


def test_validated_cache_root_rejects_missing_file_and_symlink(tmp_path):
    with pytest.raises(FileNotFoundError):
        ValidatedCacheRoot.from_path(tmp_path / "missing", filesystem=FILESYSTEM)
    regular = tmp_path / "file"
    regular.write_bytes(b"x")
    with pytest.raises(UnsupportedFilesystemObjectError, match="directory"):
        ValidatedCacheRoot.from_path(regular, filesystem=FILESYSTEM)
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(SymlinkRejectedError):
        ValidatedCacheRoot.from_path(link, filesystem=FILESYSTEM)


def test_validated_cache_root_requires_lexical_resolved_identity_consistency(tmp_path):
    class ChangedResolution(LocalReadOnlyCacheFilesystem):
        def __init__(self):
            self.calls = 0

        def inspect(self, path):
            value = super().inspect(path)
            self.calls += 1
            return replace(value, file_id=value.file_id + 1) if self.calls == 2 else value

    with pytest.raises(UnstableFilesystemObjectError, match="stable identity"):
        ValidatedCacheRoot.from_path(tmp_path, filesystem=ChangedResolution())


def test_root_validation_creates_nothing_and_changes_nothing(tmp_path):
    before = (tmp_path.stat().st_mtime_ns, tuple(tmp_path.iterdir()))
    ValidatedCacheRoot.from_path(tmp_path, filesystem=FILESYSTEM)
    after = (tmp_path.stat().st_mtime_ns, tuple(tmp_path.iterdir()))
    assert after == before


def test_directory_listing_is_ordinal_immediate_and_includes_symlink_name(tmp_path):
    (tmp_path / "z").write_bytes(b"")
    (tmp_path / "A").write_bytes(b"")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "not-returned").write_bytes(b"")
    (tmp_path / "link").symlink_to(nested, target_is_directory=True)
    assert FILESYSTEM.list_directory(tmp_path) == ("A", "link", "nested", "z")


def test_directory_listing_rejects_non_directory_symlink_and_missing(tmp_path):
    regular = tmp_path / "file"
    regular.write_bytes(b"")
    with pytest.raises(UnsupportedFilesystemObjectError):
        FILESYSTEM.list_directory(regular)
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(SymlinkRejectedError):
        FILESYSTEM.list_directory(link)
    with pytest.raises(FileNotFoundError):
        FILESYSTEM.list_directory(tmp_path / "missing")


@pytest.mark.parametrize(("content", "limit", "exceeded"), [
    (b"", 1, False),
    (b"abcd", 4, False),
    (b"abcde", 4, True),
    (b"x" * 100_000, 7, True),
])
def test_bounded_regular_file_read_limits_allocation_and_preserves_raw_bytes(
    tmp_path, content, limit, exceeded
):
    path = tmp_path / "payload"
    path.write_bytes(content)
    result = FILESYSTEM.read_regular_file_bounded(path, max_bytes=limit)
    assert isinstance(result, BoundedFileRead)
    assert result.limit_exceeded is exceeded
    assert result.data is None if exceeded else result.data == content
    assert result.pre_read_identity.object_type is FilesystemObjectType.REGULAR_FILE
    assert result.handle_identity.object_type is FilesystemObjectType.REGULAR_FILE
    assert result.post_read_identity.object_type is FilesystemObjectType.REGULAR_FILE
    assert result.stable_read


def test_bounded_read_rejects_symlink_directory_fifo_socket_and_missing(tmp_path):
    regular = tmp_path / "regular"
    regular.write_bytes(b"x")
    link = tmp_path / "link"
    link.symlink_to(regular)
    with pytest.raises(SymlinkRejectedError):
        FILESYSTEM.read_regular_file_bounded(link, max_bytes=1)
    with pytest.raises(UnsupportedFilesystemObjectError):
        FILESYSTEM.read_regular_file_bounded(tmp_path, max_bytes=1)
    with pytest.raises(FileNotFoundError):
        FILESYSTEM.read_regular_file_bounded(tmp_path / "missing", max_bytes=1)

    if hasattr(os, "mkfifo"):
        fifo = tmp_path / "fifo"
        os.mkfifo(fifo)
        with pytest.raises(UnsupportedFilesystemObjectError):
            FILESYSTEM.read_regular_file_bounded(fifo, max_bytes=1)
    with tempfile.TemporaryDirectory(prefix="s5b1-", dir="/tmp") as short_directory:
        socket_path = Path(short_directory) / "socket"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(str(socket_path))
            with pytest.raises(UnsupportedFilesystemObjectError):
                FILESYSTEM.read_regular_file_bounded(socket_path, max_bytes=1)
        finally:
            server.close()


@pytest.mark.parametrize("invalid", [0, -1, True, 1.5, "1", None])
def test_bounded_read_rejects_invalid_limits(tmp_path, invalid):
    path = tmp_path / "file"
    path.write_bytes(b"")
    with pytest.raises(ValueError, match="positive integer"):
        FILESYSTEM.read_regular_file_bounded(path, max_bytes=invalid)


def test_bounded_read_detects_changed_post_read_identity_without_retry(tmp_path):
    path = tmp_path / "file"
    path.write_bytes(b"content")

    class ChangedPostRead(LocalReadOnlyCacheFilesystem):
        def __init__(self):
            self.inspect_calls = 0

        def inspect(self, inspected):
            value = super().inspect(inspected)
            self.inspect_calls += 1
            if self.inspect_calls == 2:
                return replace(value, modification_time_ns=value.modification_time_ns + 1)
            return value

    adapter = ChangedPostRead()
    result = adapter.read_regular_file_bounded(path, max_bytes=100)
    assert not result.stable_read
    assert adapter.inspect_calls == 2


def test_adapter_protocol_and_surface_have_no_mutation_methods():
    assert isinstance(FILESYSTEM, ReadOnlyCacheFilesystem)
    public = {name for name in dir(ReadOnlyCacheFilesystem) if not name.startswith("_")}
    assert public == {
        "inspect",
        "list_directory",
        "read_regular_file_bounded",
        "resolve",
    }
    forbidden = {
        "write", "create", "mkdir", "rename", "replace", "delete", "unlink",
        "chmod", "truncate", "lock", "repair", "cleanup", "promotion",
    }
    assert forbidden.isdisjoint(public)


def test_adapter_operations_do_not_mutate_filesystem(tmp_path):
    path = tmp_path / "file"
    path.write_bytes(b"raw\xff")
    before = (
        path.read_bytes(),
        path.stat().st_mtime_ns,
        tmp_path.stat().st_mtime_ns,
        tuple(tmp_path.iterdir()),
    )
    FILESYSTEM.inspect(path)
    FILESYSTEM.list_directory(tmp_path)
    result = FILESYSTEM.read_regular_file_bounded(path, max_bytes=100)
    after = (
        path.read_bytes(),
        path.stat().st_mtime_ns,
        tmp_path.stat().st_mtime_ns,
        tuple(tmp_path.iterdir()),
    )
    assert result.data == b"raw\xff"
    assert after == before


def test_bounded_read_open_is_nonblocking_and_rejects_raced_fifo_before_read(
    tmp_path, monkeypatch
):
    if not hasattr(os, "mkfifo") or not hasattr(os, "O_NONBLOCK"):
        pytest.skip("FIFO or O_NONBLOCK is unavailable")

    path = tmp_path / "payload"
    path.write_bytes(b"regular-before-race")
    fifo = tmp_path / "raced-fifo"
    os.mkfifo(fifo)
    fifo_stat = os.lstat(fifo)
    captured: dict[str, int] = {}
    calls = {"open": 0, "read": 0}

    def fake_open(opened_path, flags):
        assert Path(opened_path) == path
        captured["flags"] = flags
        calls["open"] += 1
        return 12345

    def fake_fstat(descriptor):
        assert descriptor == 12345
        return fifo_stat

    def forbidden_read(descriptor, size):
        calls["read"] += 1
        raise AssertionError("os.read must not be called for a raced FIFO")

    monkeypatch.setattr(os, "open", fake_open)
    monkeypatch.setattr(os, "fstat", fake_fstat)
    monkeypatch.setattr(os, "read", forbidden_read)
    monkeypatch.setattr(os, "close", lambda descriptor: None)

    with pytest.raises(UnstableFilesystemObjectError, match="non-regular"):
        FILESYSTEM.read_regular_file_bounded(path, max_bytes=64)

    assert captured["flags"] & os.O_NONBLOCK
    assert calls == {"open": 1, "read": 0}


def test_directory_disappearance_after_listing_is_unstable_without_retry(tmp_path):
    class DisappearingAfterListing(LocalReadOnlyCacheFilesystem):
        def __init__(self):
            self.inspect_calls = 0

        def inspect(self, path):
            self.inspect_calls += 1
            if self.inspect_calls == 2:
                raise FileNotFoundError(path)
            return super().inspect(path)

    adapter = DisappearingAfterListing()
    with pytest.raises(UnstableFilesystemObjectError, match="disappeared"):
        adapter.list_directory(tmp_path)
    assert adapter.inspect_calls == 2


def test_initial_directory_absence_remains_file_not_found(tmp_path):
    class CountingFilesystem(LocalReadOnlyCacheFilesystem):
        def __init__(self):
            self.inspect_calls = 0

        def inspect(self, path):
            self.inspect_calls += 1
            return super().inspect(path)

    adapter = CountingFilesystem()
    with pytest.raises(FileNotFoundError):
        adapter.list_directory(tmp_path / "missing")
    assert adapter.inspect_calls == 1


def _make_complete_entry(path):
    path.mkdir()
    (path / "metadata.json").write_bytes(b"not-read")
    (path / "manifest.json").write_bytes(b"not-read")
    (path / "COMPLETE").write_bytes(b"not-read")
    (path / "payload").mkdir()
    return path


def test_internal_structure_accepts_exact_v1_top_level_and_does_not_read_payload(tmp_path):
    entry = _make_complete_entry(tmp_path / "entry")
    nested = entry / "payload" / "unsafe-if-visited"
    nested.symlink_to(tmp_path / "missing")

    observation = _inspect_final_entry_structure(entry, filesystem=FILESYSTEM)

    assert observation.classification is _FinalEntryStructureClassification.VALID
    assert observation.observed_names == (
        "COMPLETE",
        "manifest.json",
        "metadata.json",
        "payload",
    )
    assert observation.missing_names == ()
    assert observation.unexpected_names == ()
    assert observation.unsafe_names == ()


def test_internal_structure_observes_entry_absence_without_public_status(tmp_path):
    observation = _inspect_final_entry_structure(
        tmp_path / "absent", filesystem=FILESYSTEM
    )
    assert observation.classification is _FinalEntryStructureClassification.ENTRY_ABSENT
    assert not hasattr(observation, "status")


@pytest.mark.parametrize(
    "missing_name", ["metadata.json", "manifest.json", "COMPLETE", "payload"]
)
def test_internal_structure_classifies_each_missing_required_object(tmp_path, missing_name):
    entry = _make_complete_entry(tmp_path / "entry")
    target = entry / missing_name
    target.rmdir() if target.is_dir() else target.unlink()

    observation = _inspect_final_entry_structure(entry, filesystem=FILESYSTEM)

    assert observation.classification is _FinalEntryStructureClassification.INCOMPLETE_ENTRY
    assert observation.missing_names == (missing_name,)


@pytest.mark.parametrize(
    ("name", "wrong_kind"),
    [
        ("metadata.json", "directory"),
        ("manifest.json", "directory"),
        ("COMPLETE", "directory"),
        ("payload", "regular_file"),
    ],
)
def test_internal_structure_classifies_wrong_required_types_as_unsafe(
    tmp_path, name, wrong_kind
):
    entry = _make_complete_entry(tmp_path / "entry")
    target = entry / name
    target.rmdir() if target.is_dir() else target.unlink()
    target.mkdir() if wrong_kind == "directory" else target.write_bytes(b"")

    observation = _inspect_final_entry_structure(entry, filesystem=FILESYSTEM)

    assert observation.classification is _FinalEntryStructureClassification.UNSAFE_OBJECT
    assert observation.unsafe_names == (name,)


@pytest.mark.parametrize("kind", ["regular_file", "directory"])
def test_internal_structure_classifies_unknown_safe_object(tmp_path, kind):
    entry = _make_complete_entry(tmp_path / "entry")
    extra = entry / "extra"
    extra.write_bytes(b"") if kind == "regular_file" else extra.mkdir()

    observation = _inspect_final_entry_structure(entry, filesystem=FILESYSTEM)

    assert (
        observation.classification
        is _FinalEntryStructureClassification.UNEXPECTED_TOP_LEVEL_OBJECT
    )
    assert observation.unexpected_names == ("extra",)


@pytest.mark.parametrize(
    "name", ["metadata.json", "manifest.json", "COMPLETE", "payload", "extra"]
)
def test_internal_structure_classifies_required_and_unknown_symlinks_as_unsafe(
    tmp_path, name
):
    entry = _make_complete_entry(tmp_path / "entry")
    target = entry / name
    if target.exists():
        target.rmdir() if target.is_dir() else target.unlink()
    target.symlink_to(tmp_path / "missing")

    observation = _inspect_final_entry_structure(entry, filesystem=FILESYSTEM)

    assert observation.classification is _FinalEntryStructureClassification.UNSAFE_OBJECT
    assert observation.unsafe_names == (name,)


def test_internal_structure_inspects_every_top_level_object_before_unexpected(tmp_path):
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO creation is unavailable")
    entry = _make_complete_entry(tmp_path / "entry")
    (entry / "ordinary-extra").write_bytes(b"")
    os.mkfifo(entry / "unsafe-extra")

    observation = _inspect_final_entry_structure(entry, filesystem=FILESYSTEM)

    assert observation.classification is _FinalEntryStructureClassification.UNSAFE_OBJECT
    assert observation.unsafe_names == ("unsafe-extra",)
    assert observation.unexpected_names == ("ordinary-extra", "unsafe-extra")


def test_internal_structure_does_not_recurse_scan_siblings_or_inspect_locks(tmp_path):
    entry = _make_complete_entry(tmp_path / "entry")
    (entry / "payload" / "nested").mkdir()
    sibling = tmp_path / "sibling-entry"
    sibling.mkdir()
    locks = tmp_path / "locks"
    locks.mkdir()

    class RecordingFilesystem:
        def __init__(self):
            self.inspected = []
            self.listed = []

        def inspect(self, path):
            self.inspected.append(Path(path))
            return FILESYSTEM.inspect(path)

        def resolve(self, path):
            return FILESYSTEM.resolve(path)

        def list_directory(self, path):
            self.listed.append(Path(path))
            return FILESYSTEM.list_directory(path)

        def read_regular_file_bounded(self, path, *, max_bytes):
            raise AssertionError("structure validation must not read files")

    filesystem = RecordingFilesystem()
    before = tuple(sorted(tmp_path.rglob("*")))
    observation = _inspect_final_entry_structure(entry, filesystem=filesystem)
    after = tuple(sorted(tmp_path.rglob("*")))

    assert observation.classification is _FinalEntryStructureClassification.VALID
    assert filesystem.listed == [entry]
    assert set(filesystem.inspected) == {
        entry,
        entry / "COMPLETE",
        entry / "manifest.json",
        entry / "metadata.json",
        entry / "payload",
    }
    assert sibling not in filesystem.inspected
    assert locks not in filesystem.inspected
    assert entry / "payload" / "nested" not in filesystem.inspected
    assert before == after


def test_internal_structure_surface_adds_no_public_lookup_or_mutation_api():
    import engine.storage.cache_lookup as cache_lookup

    assert not hasattr(cache_lookup, "lookup_cache_entry")
    assert not hasattr(cache_lookup, "LOCKED_OR_IN_PROGRESS")
    public = {name for name in dir(ReadOnlyCacheFilesystem) if not name.startswith("_")}
    assert public == {
        "inspect",
        "list_directory",
        "read_regular_file_bounded",
        "resolve",
    }


def _valid_document_models():
    cache_key = CacheKey("a" * 64)
    manifest = PayloadManifest(
        (
            PayloadManifestRecord(
                "artifact.json",
                7,
                "sha256:" + "b" * 64,
                "application/json",
                "primary",
            ),
        )
    )
    manifest_bytes = manifest.canonical_bytes()
    metadata = CacheEntryMetadata(
        derive_entry_digest(cache_key),
        CacheKeyReference.from_cache_key(cache_key),
        CacheNamespace("audio", "transcription.whisper", 3),
        CacheArtifactMetadata("transcript", "episode-001", 1),
        CacheProducerMetadata("transcription.whisper", "4.2.0", 3),
        CacheRuntimeFingerprint(1, {"model": "large-v3"}),
        "2026-07-20T09:00:00Z",
        "sha256:" + hashlib.sha256(manifest_bytes).hexdigest(),
        1,
        7,
    )
    metadata_bytes = metadata.canonical_bytes()
    marker = CompletenessMarker(
        metadata.entry_digest,
        "sha256:" + hashlib.sha256(metadata_bytes).hexdigest(),
        "sha256:" + hashlib.sha256(manifest_bytes).hexdigest(),
    )
    return marker, metadata, manifest


def _write_valid_documents(entry):
    marker, metadata, manifest = _valid_document_models()
    documents = {
        _CacheDocumentName.COMPLETE: marker.canonical_bytes(),
        _CacheDocumentName.METADATA: metadata.canonical_bytes(),
        _CacheDocumentName.MANIFEST: manifest.canonical_bytes(),
    }
    for name, content in documents.items():
        (entry / name.value).write_bytes(content)
    return documents, (marker, metadata, manifest)


def _document_dict(name):
    marker, metadata, manifest = _valid_document_models()
    return {
        _CacheDocumentName.COMPLETE: marker.to_dict(),
        _CacheDocumentName.METADATA: metadata.to_dict(),
        _CacheDocumentName.MANIFEST: manifest.to_dict(),
    }[name]


@pytest.mark.parametrize("name", tuple(_CacheDocumentName))
def test_internal_document_reader_parses_each_canonical_document_and_preserves_bytes(
    tmp_path, name
):
    entry = _make_complete_entry(tmp_path / "entry")
    documents, _ = _write_valid_documents(entry)

    observation = _read_and_parse_cache_document(
        entry, name, policy=CacheLookupVerificationPolicy(), filesystem=FILESYSTEM
    )

    assert observation.classification is _CacheDocumentClassification.VALID
    if name is _CacheDocumentName.METADATA:
        assert observation.model is None
        assert isinstance(observation.observed_metadata, _ObservedCacheEntryMetadataV1)
    else:
        assert isinstance(
            observation.model,
            CompletenessMarker
            if name is _CacheDocumentName.COMPLETE
            else PayloadManifest,
        )
        assert observation.observed_metadata is None
    assert observation.stored_bytes == documents[name]
    assert observation.stable_read


def test_internal_document_pipeline_reads_all_three_in_order_and_preserves_models(tmp_path):
    entry = _make_complete_entry(tmp_path / "entry")
    documents, models = _write_valid_documents(entry)

    observation = _read_and_parse_final_entry_documents(
        entry, policy=CacheLookupVerificationPolicy(), filesystem=FILESYSTEM
    )

    assert observation.classification is _CacheDocumentClassification.VALID
    assert observation.complete.model == models[0]
    assert observation.metadata.model is None
    assert observation.metadata.observed_metadata.to_dict() == models[1].to_dict()
    assert observation.manifest.model == models[2]
    assert observation.complete.stored_bytes == documents[_CacheDocumentName.COMPLETE]
    assert observation.metadata.stored_bytes == documents[_CacheDocumentName.METADATA]
    assert observation.manifest.stored_bytes == documents[_CacheDocumentName.MANIFEST]


def test_internal_document_pipeline_read_order_never_touches_payload_or_locks(tmp_path):
    entry = _make_complete_entry(tmp_path / "entry")
    _write_valid_documents(entry)
    (entry / "payload" / "must-not-read").write_bytes(b"payload")
    locks = tmp_path / "locks"
    locks.mkdir()

    class RecordingFilesystem:
        def __init__(self):
            self.read_paths = []

        def inspect(self, path):
            return FILESYSTEM.inspect(path)

        def resolve(self, path):
            return FILESYSTEM.resolve(path)

        def list_directory(self, path):
            raise AssertionError("document reading must not list directories")

        def read_regular_file_bounded(self, path, *, max_bytes):
            self.read_paths.append(Path(path))
            return FILESYSTEM.read_regular_file_bounded(path, max_bytes=max_bytes)

    filesystem = RecordingFilesystem()
    observation = _read_and_parse_final_entry_documents(
        entry, policy=CacheLookupVerificationPolicy(), filesystem=filesystem
    )

    assert observation.classification is _CacheDocumentClassification.VALID
    assert filesystem.read_paths == [
        entry / "COMPLETE",
        entry / "metadata.json",
        entry / "manifest.json",
    ]
    assert not any("payload" in path.parts or "locks" in path.parts for path in filesystem.read_paths)


def _noncanonical_variant(canonical, variant):
    if variant == "trailing_newline":
        return canonical + b"\n"
    if variant == "surrounding_whitespace":
        return b" " + canonical + b" "
    if variant == "pretty_printed":
        return json.dumps(json.loads(canonical), indent=2, sort_keys=True).encode("utf-8")
    if variant == "bom":
        return b"\xef\xbb\xbf" + canonical
    raise AssertionError(variant)


@pytest.mark.parametrize("name", tuple(_CacheDocumentName))
@pytest.mark.parametrize(
    "variant", ["trailing_newline", "surrounding_whitespace", "pretty_printed", "bom"]
)
def test_internal_document_reader_rejects_noncanonical_variants(tmp_path, name, variant):
    entry = _make_complete_entry(tmp_path / "entry")
    documents, _ = _write_valid_documents(entry)
    (entry / name.value).write_bytes(_noncanonical_variant(documents[name], variant))

    observation = _read_and_parse_cache_document(
        entry, name, policy=CacheLookupVerificationPolicy(), filesystem=FILESYSTEM
    )

    assert observation.classification is {
        _CacheDocumentName.COMPLETE: _CacheDocumentClassification.MALFORMED_COMPLETE,
        _CacheDocumentName.METADATA: _CacheDocumentClassification.MALFORMED_METADATA,
        _CacheDocumentName.MANIFEST: _CacheDocumentClassification.MALFORMED_MANIFEST,
    }[name]


@pytest.mark.parametrize("name", tuple(_CacheDocumentName))
def test_internal_document_reader_rejects_duplicate_json_keys(tmp_path, name):
    entry = _make_complete_entry(tmp_path / "entry")
    documents, _ = _write_valid_documents(entry)
    duplicate_field = {
        _CacheDocumentName.COMPLETE: b'"cache_entry_contract_version":1,',
        _CacheDocumentName.METADATA: b'"cache_entry_contract_version":1,',
        _CacheDocumentName.MANIFEST: b'"manifest_version":1,',
    }[name]
    (entry / name.value).write_bytes(b"{" + duplicate_field + documents[name][1:])

    observation = _read_and_parse_cache_document(
        entry, name, policy=CacheLookupVerificationPolicy(), filesystem=FILESYSTEM
    )

    assert observation.classification.value == f"malformed_{'complete' if name is _CacheDocumentName.COMPLETE else name.value.removesuffix('.json')}"


@pytest.mark.parametrize("name", tuple(_CacheDocumentName))
def test_internal_document_reader_rejects_unknown_fields_for_supported_version(tmp_path, name):
    entry = _make_complete_entry(tmp_path / "entry")
    _write_valid_documents(entry)
    data = _document_dict(name)
    data["future"] = True
    (entry / name.value).write_bytes(canonical_json_bytes(data))

    observation = _read_and_parse_cache_document(
        entry, name, policy=CacheLookupVerificationPolicy(), filesystem=FILESYSTEM
    )

    assert observation.classification.value.startswith("malformed_")


@pytest.mark.parametrize(
    ("name", "limit_field"),
    [
        (_CacheDocumentName.COMPLETE, "max_complete_bytes"),
        (_CacheDocumentName.METADATA, "max_metadata_bytes"),
        (_CacheDocumentName.MANIFEST, "max_manifest_bytes"),
    ],
)
def test_internal_document_reader_classifies_oversized_documents_as_malformed(
    tmp_path, name, limit_field
):
    entry = _make_complete_entry(tmp_path / "entry")
    documents, _ = _write_valid_documents(entry)
    policy = replace(
        CacheLookupVerificationPolicy(), **{limit_field: len(documents[name]) - 1}
    )

    observation = _read_and_parse_cache_document(
        entry, name, policy=policy, filesystem=FILESYSTEM
    )

    assert observation.classification.value.startswith("malformed_")
    assert observation.stored_bytes is None


@pytest.mark.parametrize(
    ("name", "field"),
    [
        (_CacheDocumentName.COMPLETE, "cache_entry_contract_version"),
        (_CacheDocumentName.METADATA, "cache_entry_contract_version"),
        (_CacheDocumentName.MANIFEST, "manifest_version"),
    ],
)
def test_internal_document_reader_classifies_future_top_level_versions(tmp_path, name, field):
    entry = _make_complete_entry(tmp_path / "entry")
    _write_valid_documents(entry)
    data = _document_dict(name)
    data[field] = 2
    data["future"] = {"unknown": True}
    (entry / name.value).write_bytes(canonical_json_bytes(data))

    observation = _read_and_parse_cache_document(
        entry, name, policy=CacheLookupVerificationPolicy(), filesystem=FILESYSTEM
    )

    assert observation.classification is (
        _CacheDocumentClassification.UNSUPPORTED_MANIFEST_VERSION
        if name is _CacheDocumentName.MANIFEST
        else _CacheDocumentClassification.UNSUPPORTED_ENTRY_VERSION
    )


@pytest.mark.parametrize(
    ("nested", "field", "classification"),
    [
        ("cache_key", "canonical_version", _CacheDocumentClassification.UNSUPPORTED_CACHE_KEY_VERSION),
        (
            "runtime_fingerprint",
            "schema_version",
            _CacheDocumentClassification.UNSUPPORTED_RUNTIME_FINGERPRINT_VERSION,
        ),
    ],
)
def test_internal_metadata_reader_classifies_future_nested_versions(
    tmp_path, nested, field, classification
):
    entry = _make_complete_entry(tmp_path / "entry")
    _write_valid_documents(entry)
    data = _document_dict(_CacheDocumentName.METADATA)
    data[nested][field] = 2
    data["future"] = True
    (entry / "metadata.json").write_bytes(canonical_json_bytes(data))

    observation = _read_and_parse_cache_document(
        entry,
        _CacheDocumentName.METADATA,
        policy=CacheLookupVerificationPolicy(),
        filesystem=FILESYSTEM,
    )

    assert observation.classification is classification


@pytest.mark.parametrize("malformed", [None, True, "1", 0, -1])
@pytest.mark.parametrize(
    ("name", "field"),
    [
        (_CacheDocumentName.COMPLETE, "cache_entry_contract_version"),
        (_CacheDocumentName.METADATA, "cache_entry_contract_version"),
        (_CacheDocumentName.MANIFEST, "manifest_version"),
    ],
)
def test_internal_document_reader_keeps_malformed_discriminators_malformed(
    tmp_path, name, field, malformed
):
    entry = _make_complete_entry(tmp_path / "entry")
    _write_valid_documents(entry)
    data = _document_dict(name)
    if malformed is None:
        data.pop(field)
    else:
        data[field] = malformed
    (entry / name.value).write_bytes(canonical_json_bytes(data))

    observation = _read_and_parse_cache_document(
        entry, name, policy=CacheLookupVerificationPolicy(), filesystem=FILESYSTEM
    )

    assert observation.classification.value.startswith("malformed_")


def test_internal_document_surface_does_not_add_public_lookup_or_hash_payload():
    import engine.storage.cache_lookup as cache_lookup

    assert not hasattr(cache_lookup, "lookup_cache_entry")
    assert not hasattr(cache_lookup, "LOCKED_OR_IN_PROGRESS")
    assert not hasattr(cache_lookup, "hash_payload")


def _step5b2c_documents(
    tmp_path,
    *,
    metadata_mutator=None,
    marker_mutator=None,
    manifest_mutator=None,
):
    cache_key = CacheKey("a" * 64)
    namespace = CacheNamespace("audio", "transcription.whisper", 3)
    root = tmp_path / "cache"
    root.mkdir()
    entry = derive_final_entry_path(root, namespace, cache_key)
    entry.mkdir(parents=True)
    (entry / "payload").mkdir()

    marker, metadata, manifest = _valid_document_models()
    manifest_data = manifest.to_dict()
    if manifest_mutator is not None:
        manifest_mutator(manifest_data)
    manifest_bytes = canonical_json_bytes(manifest_data)
    metadata_data = metadata.to_dict()
    if metadata_mutator is not None:
        metadata_mutator(metadata_data)
    metadata_bytes = canonical_json_bytes(metadata_data)
    marker_data = marker.to_dict()
    marker_data["metadata_digest"] = (
        "sha256:" + hashlib.sha256(metadata_bytes).hexdigest()
    )
    marker_data["manifest_digest"] = (
        "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()
    )
    if marker_mutator is not None:
        marker_mutator(marker_data)

    (entry / "COMPLETE").write_bytes(canonical_json_bytes(marker_data))
    (entry / "metadata.json").write_bytes(metadata_bytes)
    (entry / "manifest.json").write_bytes(manifest_bytes)
    documents = _read_and_parse_final_entry_documents(
        entry,
        policy=CacheLookupVerificationPolicy(),
        filesystem=FILESYSTEM,
    )
    expectation = CacheLookupExpectation(
        namespace,
        namespace.producer_id,
        namespace.producer_schema_version,
        metadata.runtime_fingerprint,
    )
    return root, entry, cache_key, namespace, expectation, documents


def _validate_step5b2c(fixture, *, entry_path=None, expectation=None, artifact=None):
    root, entry, cache_key, namespace, default_expectation, documents = fixture
    return _validate_final_entry_document_integrity(
        root,
        entry if entry_path is None else entry_path,
        documents,
        cache_key=cache_key,
        namespace=namespace,
        expectation=default_expectation if expectation is None else expectation,
        artifact_expectation=artifact,
    )


def test_step5b2c_valid_documents_reach_document_integrity_only(tmp_path):
    fixture = _step5b2c_documents(tmp_path)
    result = _validate_step5b2c(fixture)

    assert result.classification is _CacheDocumentIntegrityClassification.VALID
    assert result.verification_level is _CacheVerificationLevel.DOCUMENT_INTEGRITY
    assert isinstance(result.metadata, CacheEntryMetadata)
    assert not result.payload_bytes_fully_hashed
    assert result.metadata == _valid_document_models()[1]


def test_step5b2b_preserves_relationally_invalid_metadata_as_private_observation(
    tmp_path,
):
    fixture = _step5b2c_documents(
        tmp_path,
        metadata_mutator=lambda data: data["producer"].update(
            producer_id="other.producer"
        ),
    )
    documents = fixture[-1]

    assert documents.classification is _CacheDocumentClassification.VALID
    assert documents.metadata.model is None
    assert isinstance(
        documents.metadata.observed_metadata, _ObservedCacheEntryMetadataV1
    )
    assert documents.metadata.observed_metadata.producer.producer_id == "other.producer"
    with pytest.raises(CacheEntryContractError, match="IDs"):
        documents.metadata.observed_metadata.to_strict_metadata()


def test_step5b2b_private_observation_round_trips_exact_canonical_metadata(tmp_path):
    fixture = _step5b2c_documents(tmp_path)
    metadata = fixture[-1].metadata
    observed = _parse_observed_cache_entry_metadata_v1(metadata.stored_bytes)

    assert observed.canonical_bytes() == metadata.stored_bytes
    assert observed.to_strict_metadata() == _valid_document_models()[1]


@pytest.mark.parametrize(
    "fixture_change",
    [
        "path",
        "shard",
        "metadata_entry_digest",
        "complete_entry_digest",
        "cache_key",
        "namespace",
    ],
)
def test_step5b2c_entry_identity_conflicts_take_locked_precedence(
    tmp_path, fixture_change
):
    metadata_mutator = None
    marker_mutator = None
    entry_path = None
    if fixture_change == "metadata_entry_digest":
        metadata_mutator = lambda data: data.update(entry_digest="b" * 64)
    elif fixture_change == "complete_entry_digest":
        marker_mutator = lambda data: data.update(entry_digest="b" * 64)
    elif fixture_change == "cache_key":
        metadata_mutator = lambda data: data["cache_key"].update(
            canonical_value=str(CacheKey("c" * 64))
        )
    elif fixture_change == "namespace":
        def metadata_mutator(data):
            data["namespace"]["domain"] = "video"

    fixture = _step5b2c_documents(
        tmp_path,
        metadata_mutator=metadata_mutator,
        marker_mutator=marker_mutator,
    )
    if fixture_change == "path":
        entry_path = fixture[1].parent / ("f" * 64)
    elif fixture_change == "shard":
        entry_path = fixture[1].parent.parent / "ff" / fixture[1].name

    result = _validate_step5b2c(fixture, entry_path=entry_path)
    assert (
        result.classification
        is _CacheDocumentIntegrityClassification.ENTRY_IDENTITY_CONFLICT
    )
    assert result.verification_level is _CacheVerificationLevel.CANONICAL_DOCUMENTS
    assert result.metadata is None


@pytest.mark.parametrize("field", ["producer_id", "producer_schema_version"])
def test_step5b2c_namespace_producer_conflicts_remain_precise(tmp_path, field):
    def change(data):
        data["producer"][field] = (
            "other.producer" if field == "producer_id" else 4
        )

    result = _validate_step5b2c(
        _step5b2c_documents(tmp_path, metadata_mutator=change)
    )
    assert (
        result.classification
        is _CacheDocumentIntegrityClassification.NAMESPACE_PRODUCER_CONFLICT
    )


def test_step5b2c_runtime_fingerprint_uses_exact_step5a_equality(tmp_path):
    fixture = _step5b2c_documents(tmp_path)
    namespace = fixture[3]
    mismatch = CacheLookupExpectation(
        namespace,
        namespace.producer_id,
        namespace.producer_schema_version,
        CacheRuntimeFingerprint(1, {"model": "small"}),
    )

    result = _validate_step5b2c(fixture, expectation=mismatch)
    assert (
        result.classification
        is _CacheDocumentIntegrityClassification.RUNTIME_FINGERPRINT_MISMATCH
    )


@pytest.mark.parametrize(
    "artifact",
    [
        _CacheArtifactExpectation("audio", 1),
        _CacheArtifactExpectation("transcript", 2),
        _CacheArtifactExpectation("transcript", 1, "other-logical-id"),
    ],
)
def test_step5b2c_rejects_explicit_artifact_expectation_mismatches(
    tmp_path, artifact
):
    result = _validate_step5b2c(
        _step5b2c_documents(tmp_path), artifact=artifact
    )
    assert result.classification is _CacheDocumentIntegrityClassification.ARTIFACT_MISMATCH


def test_step5b2c_accepts_different_logical_id_when_not_explicitly_expected(tmp_path):
    fixture = _step5b2c_documents(
        tmp_path,
        metadata_mutator=lambda data: data["artifact"].update(
            logical_id="different-logical-id"
        ),
    )
    result = _validate_step5b2c(
        fixture,
        artifact=_CacheArtifactExpectation("transcript", 1),
    )

    assert result.classification is _CacheDocumentIntegrityClassification.VALID


@pytest.mark.parametrize(
    ("target", "classification"),
    [
        ("metadata_manifest", _CacheDocumentIntegrityClassification.MANIFEST_DIGEST_MISMATCH),
        ("complete_manifest", _CacheDocumentIntegrityClassification.MANIFEST_DIGEST_MISMATCH),
        ("complete_metadata", _CacheDocumentIntegrityClassification.METADATA_DIGEST_MISMATCH),
    ],
)
def test_step5b2c_validates_exact_stored_document_digests(
    tmp_path, target, classification
):
    metadata_mutator = None
    marker_mutator = None
    if target == "metadata_manifest":
        metadata_mutator = lambda data: data.update(
            payload_manifest_digest="sha256:" + "d" * 64
        )
    elif target == "complete_manifest":
        marker_mutator = lambda data: data.update(
            manifest_digest="sha256:" + "d" * 64
        )
    else:
        marker_mutator = lambda data: data.update(
            metadata_digest="sha256:" + "d" * 64
        )

    result = _validate_step5b2c(
        _step5b2c_documents(
            tmp_path,
            metadata_mutator=metadata_mutator,
            marker_mutator=marker_mutator,
        )
    )
    assert result.classification is classification


@pytest.mark.parametrize(
    ("field", "value"),
    [("payload_file_count", 2), ("payload_total_bytes", 8)],
)
def test_step5b2c_reuses_step5a_summary_helper_for_manifest_consistency(
    tmp_path, field, value
):
    result = _validate_step5b2c(
        _step5b2c_documents(
            tmp_path,
            metadata_mutator=lambda data: data.update({field: value}),
        )
    )
    assert (
        result.classification
        is _CacheDocumentIntegrityClassification.METADATA_MANIFEST_SUMMARY_MISMATCH
    )
    assert result.metadata is None
    assert not result.payload_bytes_fully_hashed


def test_step5b2c_summary_integrity_precedes_runtime_expectation(tmp_path):
    fixture = _step5b2c_documents(
        tmp_path,
        metadata_mutator=lambda data: data.update(payload_file_count=2),
    )
    namespace = fixture[3]
    runtime_mismatch = CacheLookupExpectation(
        namespace,
        namespace.producer_id,
        namespace.producer_schema_version,
        CacheRuntimeFingerprint(1, {"model": "small"}),
    )

    result = _validate_step5b2c(fixture, expectation=runtime_mismatch)
    assert (
        result.classification
        is _CacheDocumentIntegrityClassification.METADATA_MANIFEST_SUMMARY_MISMATCH
    )


def test_step5b2c_never_accesses_payload_locks_or_public_lookup(tmp_path):
    import engine.storage.cache_lookup as cache_lookup

    fixture = _step5b2c_documents(tmp_path)
    payload = fixture[1] / "payload" / "must-not-read"
    payload.write_bytes(b"payload")
    before = (payload.read_bytes(), payload.stat().st_mtime_ns)
    result = _validate_step5b2c(fixture)
    after = (payload.read_bytes(), payload.stat().st_mtime_ns)

    assert result.classification is _CacheDocumentIntegrityClassification.VALID
    assert before == after
    assert not hasattr(cache_lookup, "lookup_cache_entry")
    assert not hasattr(cache_lookup, "HIT")
    assert not hasattr(cache_lookup, "MISS")
    assert not hasattr(cache_lookup, "LOCKED_OR_IN_PROGRESS")


def test_payload_cardinality_expectation_has_exact_locked_members():
    assert tuple(PayloadCardinalityExpectation) == (
        PayloadCardinalityExpectation.NON_EMPTY_REQUIRED,
        PayloadCardinalityExpectation.EMPTY_ALLOWED,
    )
    assert {item.name for item in fields(ProducerPayloadExpectation)} == {
        "cardinality"
    }


def test_default_producer_payload_expectation_is_immutable_non_empty_required():
    expectation = ProducerPayloadExpectation()

    assert (
        expectation.cardinality
        is PayloadCardinalityExpectation.NON_EMPTY_REQUIRED
    )
    with pytest.raises(FrozenInstanceError):
        expectation.cardinality = PayloadCardinalityExpectation.EMPTY_ALLOWED


@pytest.mark.parametrize(
    "invalid",
    [PayloadCardinalityExpectation.EMPTY_ALLOWED, "empty_allowed", None, True],
)
def test_generic_producer_payload_expectation_cannot_select_cardinality(invalid):
    with pytest.raises(TypeError):
        ProducerPayloadExpectation(invalid)


@pytest.mark.parametrize("invalid", ["empty_allowed", None, True, 1])
def test_trusted_payload_expectation_factory_rejects_wrong_cardinality_type(invalid):
    with pytest.raises(TypeError, match="PayloadCardinalityExpectation"):
        _trusted_producer_payload_expectation(invalid)


def test_private_trusted_producer_boundary_can_explicitly_allow_empty_payload():
    expectation = _trusted_producer_payload_expectation(
        PayloadCardinalityExpectation.EMPTY_ALLOWED
    )

    assert expectation == _trusted_producer_payload_expectation(
        PayloadCardinalityExpectation.EMPTY_ALLOWED
    )
    assert expectation.cardinality is PayloadCardinalityExpectation.EMPTY_ALLOWED


def test_payload_expectation_has_no_serialized_metadata_construction_path():
    assert not hasattr(ProducerPayloadExpectation, "from_dict")
    assert not hasattr(ProducerPayloadExpectation, "from_json")
    assert not hasattr(ProducerPayloadExpectation, "parse")


def test_non_empty_required_rejects_empty_and_accepts_non_empty_document_state():
    expectation = ProducerPayloadExpectation()
    empty_manifest = PayloadManifest(())
    non_empty_manifest = _valid_document_models()[2]

    assert not _payload_cardinality_is_valid(
        expectation,
        payload_file_count=0,
        payload_total_bytes=0,
        manifest=empty_manifest,
    )
    assert _payload_cardinality_is_valid(
        expectation,
        payload_file_count=1,
        payload_total_bytes=7,
        manifest=non_empty_manifest,
    )


def test_empty_allowed_accepts_only_summary_consistent_empty_document_state():
    expectation = _trusted_producer_payload_expectation(
        PayloadCardinalityExpectation.EMPTY_ALLOWED
    )
    empty_manifest = PayloadManifest(())

    assert _payload_cardinality_is_valid(
        expectation,
        payload_file_count=0,
        payload_total_bytes=0,
        manifest=empty_manifest,
    )
    with pytest.raises(CacheEntryContractError, match="byte total"):
        _payload_cardinality_is_valid(
            expectation,
            payload_file_count=0,
            payload_total_bytes=1,
            manifest=empty_manifest,
        )


def test_empty_allowed_does_not_bypass_other_document_summary_integrity():
    expectation = _trusted_producer_payload_expectation(
        PayloadCardinalityExpectation.EMPTY_ALLOWED
    )
    manifest = _valid_document_models()[2]

    with pytest.raises(CacheEntryContractError, match="count"):
        _payload_cardinality_is_valid(
            expectation,
            payload_file_count=0,
            payload_total_bytes=7,
            manifest=manifest,
        )


def test_payload_cardinality_prerequisite_adds_no_step5b3_io_or_public_lookup():
    import engine.storage.cache_lookup as cache_lookup

    assert not hasattr(cache_lookup, "lookup_cache_entry")
    assert not hasattr(cache_lookup, "hash_payload")
    assert not hasattr(cache_lookup, "observe_lock")
    assert not hasattr(cache_lookup, "HIT")
    assert not hasattr(cache_lookup, "MISS")
    assert not hasattr(cache_lookup, "LOCKED_OR_IN_PROGRESS")


def _step5b3_fixture(tmp_path, payload_specs):
    records = []
    for relative_path, content, declared_size, declared_digest in payload_specs:
        records.append(
            {
                "digest": declared_digest
                or "sha256:" + hashlib.sha256(content).hexdigest(),
                "media_type": "application/octet-stream",
                "relative_path": relative_path,
                "role": "primary",
                "size_bytes": len(content) if declared_size is None else declared_size,
            }
        )
    records.sort(key=lambda item: item["relative_path"])
    manifest_data = {"files": records, "manifest_version": 1}
    manifest_bytes = canonical_json_bytes(manifest_data)

    def change_manifest(data):
        data.clear()
        data.update(manifest_data)

    def change_metadata(data):
        data.update(
            payload_file_count=len(records),
            payload_total_bytes=sum(item["size_bytes"] for item in records),
            payload_manifest_digest=(
                "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()
            ),
        )

    fixture = _step5b2c_documents(
        tmp_path,
        metadata_mutator=change_metadata,
        manifest_mutator=change_manifest,
    )
    for relative_path, content, _, _ in payload_specs:
        destination = fixture[1] / "payload" / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    document_integrity = _validate_step5b2c(fixture)
    assert document_integrity.classification is _CacheDocumentIntegrityClassification.VALID
    return fixture[1], document_integrity


def _validate_step5b3(
    fixture,
    *,
    expectation=None,
    policy=None,
    filesystem=FILESYSTEM,
):
    entry, document_integrity = fixture
    return _validate_final_entry_payload(
        entry,
        document_integrity,
        payload_expectation=expectation or ProducerPayloadExpectation(),
        policy=policy or CacheLookupVerificationPolicy(),
        filesystem=filesystem,
    )


@pytest.mark.parametrize(
    "payload_specs",
    [
        (("one.bin", b"one", None, None),),
        (
            ("a.bin", b"a", None, None),
            ("b.bin", b"bb", None, None),
        ),
        (("nested/deeper/value.bin", b"nested", None, None),),
        (("empty.bin", b"", None, None),),
    ],
)
def test_step5b3_accepts_exact_manifest_authoritative_payloads(
    tmp_path, payload_specs
):
    result = _validate_step5b3(_step5b3_fixture(tmp_path, payload_specs))

    expected_order = tuple(sorted(spec[0] for spec in payload_specs))
    assert result.classification is _PayloadValidationClassification.VALID
    assert result.verification_level is _CacheVerificationLevel.DOCUMENT_INTEGRITY
    assert result.observed_regular_files == expected_order
    assert result.hash_order == expected_order
    assert result.payload_bytes_hashed == sum(len(spec[1]) for spec in payload_specs)
    assert result.declared_payload_bytes_fully_verified


def test_step5b3_hashes_in_manifest_order_not_native_listing_order(tmp_path):
    fixture = _step5b3_fixture(
        tmp_path,
        (
            ("z.bin", b"z", None, None),
            ("a.bin", b"a", None, None),
            ("m.bin", b"m", None, None),
        ),
    )
    observed_order = []

    class RecordingFilesystem(LocalReadOnlyCacheFilesystem):
        def stream_regular_file_sha256(self, path, *, declared_size, chunk_size):
            observed_order.append(Path(path).name)
            return super().stream_regular_file_sha256(
                path, declared_size=declared_size, chunk_size=chunk_size
            )

    result = _validate_step5b3(fixture, filesystem=RecordingFilesystem())
    assert result.classification is _PayloadValidationClassification.VALID
    assert observed_order == ["a.bin", "m.bin", "z.bin"]


def test_step5b3_applies_trusted_empty_payload_cardinality(tmp_path):
    fixture = _step5b3_fixture(tmp_path, ())

    rejected = _validate_step5b3(fixture)
    accepted = _validate_step5b3(
        fixture,
        expectation=_trusted_producer_payload_expectation(
            PayloadCardinalityExpectation.EMPTY_ALLOWED
        ),
    )
    assert (
        rejected.classification
        is _PayloadValidationClassification.PAYLOAD_CARDINALITY_INVALID
    )
    assert accepted.classification is _PayloadValidationClassification.VALID
    assert accepted.observed_regular_files == ()
    assert accepted.hash_order == ()
    assert accepted.payload_bytes_hashed == 0
    assert accepted.declared_payload_bytes_fully_verified


@pytest.mark.parametrize(
    "unexpected_kind",
    ["extra_file", "empty_directory", "directory_branch", "nested_descendant"],
)
def test_step5b3_rejects_unexpected_payload_objects(tmp_path, unexpected_kind):
    fixture = _step5b3_fixture(
        tmp_path, (("declared.bin", b"declared", None, None),)
    )
    payload = fixture[0] / "payload"
    if unexpected_kind == "extra_file":
        (payload / "extra.bin").write_bytes(b"extra")
    elif unexpected_kind == "empty_directory":
        (payload / "empty").mkdir()
    elif unexpected_kind == "directory_branch":
        (payload / "branch").mkdir()
        (payload / "branch" / "extra.bin").write_bytes(b"extra")
    else:
        (payload / "nested").mkdir()
        (payload / "nested" / "extra.bin").write_bytes(b"extra")

    result = _validate_step5b3(fixture)
    assert (
        result.classification
        is _PayloadValidationClassification.UNEXPECTED_PAYLOAD_OBJECT
    )


@pytest.mark.parametrize(
    "unexpected_path",
    ["a/bad.txt", "a/b/c.txt", "a2/file.txt"],
)
def test_step5b3_manifest_directory_prefixes_are_component_exact(
    tmp_path, unexpected_path
):
    fixture = _step5b3_fixture(
        tmp_path, (("a/b.txt", b"declared", None, None),)
    )
    unexpected = fixture[0] / "payload" / unexpected_path
    unexpected.parent.mkdir(parents=True, exist_ok=True)
    unexpected.write_bytes(b"unexpected")

    result = _validate_step5b3(fixture)
    assert (
        result.classification
        is _PayloadValidationClassification.UNEXPECTED_PAYLOAD_OBJECT
    )


def test_step5b3_rejects_missing_declared_file(tmp_path):
    fixture = _step5b3_fixture(
        tmp_path, (("missing.bin", b"expected", None, None),)
    )
    (fixture[0] / "payload" / "missing.bin").unlink()

    result = _validate_step5b3(fixture)
    assert result.classification is _PayloadValidationClassification.PAYLOAD_MISSING


@pytest.mark.parametrize("unsafe_kind", ["symlink", "nested_symlink", "fifo", "socket"])
def test_step5b3_rejects_unsafe_payload_objects(tmp_path, unsafe_kind):
    fixture = _step5b3_fixture(
        tmp_path, (("declared.bin", b"declared", None, None),)
    )
    payload = fixture[0] / "payload"
    unsafe = payload / "unsafe"
    server = None
    filesystem = FILESYSTEM
    if unsafe_kind == "symlink":
        unsafe.symlink_to(payload / "declared.bin")
    elif unsafe_kind == "nested_symlink":
        unsafe.symlink_to(payload, target_is_directory=True)
    elif unsafe_kind == "fifo":
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO creation is unavailable")
        os.mkfifo(unsafe)
    else:
        unsafe.write_bytes(b"socket-fixture")

        class SocketObservationFilesystem(LocalReadOnlyCacheFilesystem):
            def inspect(self, path):
                identity = super().inspect(path)
                return (
                    replace(identity, object_type=FilesystemObjectType.SOCKET)
                    if Path(path) == unsafe
                    else identity
                )

        filesystem = SocketObservationFilesystem()
    try:
        result = _validate_step5b3(fixture, filesystem=filesystem)
    finally:
        if server is not None:
            server.close()
    assert result.classification is _PayloadValidationClassification.UNSAFE_OBJECT


def test_step5b3_rejects_symlink_at_declared_payload_path(tmp_path):
    fixture = _step5b3_fixture(
        tmp_path, (("declared.bin", b"declared", None, None),)
    )
    declared = fixture[0] / "payload" / "declared.bin"
    target = tmp_path / "target"
    target.write_bytes(b"declared")
    declared.unlink()
    declared.symlink_to(target)

    result = _validate_step5b3(fixture)
    assert result.classification is _PayloadValidationClassification.UNSAFE_OBJECT


def test_step5b3_rejects_intermediate_directory_symlink(tmp_path):
    fixture = _step5b3_fixture(
        tmp_path, (("nested/declared.bin", b"declared", None, None),)
    )
    nested = fixture[0] / "payload" / "nested"
    target = tmp_path / "target-dir"
    target.mkdir()
    (target / "declared.bin").write_bytes(b"declared")
    (nested / "declared.bin").unlink()
    nested.rmdir()
    nested.symlink_to(target, target_is_directory=True)

    result = _validate_step5b3(fixture)
    assert result.classification is _PayloadValidationClassification.UNSAFE_OBJECT


def test_step5b3_detects_payload_hardlink_but_allows_unavailable_evidence(tmp_path):
    fixture = _step5b3_fixture(
        tmp_path, (("declared.bin", b"declared", None, None),)
    )
    declared = fixture[0] / "payload" / "declared.bin"
    outside_link = tmp_path / "outside-link"
    os.link(declared, outside_link)

    detected = _validate_step5b3(fixture)

    class NoLinkCountFilesystem(LocalReadOnlyCacheFilesystem):
        def inspect(self, path):
            identity = super().inspect(path)
            return (
                replace(identity, link_count=None)
                if identity.object_type is FilesystemObjectType.REGULAR_FILE
                else identity
            )

        def stream_regular_file_sha256(self, path, *, declared_size, chunk_size):
            streamed = super().stream_regular_file_sha256(
                path, declared_size=declared_size, chunk_size=chunk_size
            )
            return replace(
                streamed,
                pre_read_identity=replace(
                    streamed.pre_read_identity, link_count=None
                ),
                handle_identity=replace(streamed.handle_identity, link_count=None),
            )

    unavailable = _validate_step5b3(
        fixture, filesystem=NoLinkCountFilesystem()
    )
    assert (
        detected.classification
        is _PayloadValidationClassification.PAYLOAD_HARDLINK_DETECTED
    )
    assert unavailable.classification is _PayloadValidationClassification.VALID


@pytest.mark.parametrize("actual", [b"short", b"content-too-large"])
def test_step5b3_size_mismatch_precedes_digest_mismatch(tmp_path, actual):
    fixture = _step5b3_fixture(
        tmp_path,
        (("declared.bin", actual, 7, "sha256:" + "f" * 64),),
    )

    result = _validate_step5b3(fixture)
    assert result.classification is _PayloadValidationClassification.PAYLOAD_SIZE_MISMATCH
    assert result.hash_order == ()


def test_step5b3_rejects_wrong_digest_after_exact_size_stream(tmp_path):
    fixture = _step5b3_fixture(
        tmp_path,
        (("declared.bin", b"content", None, "sha256:" + "f" * 64),),
    )

    result = _validate_step5b3(fixture)
    assert result.classification is _PayloadValidationClassification.PAYLOAD_DIGEST_MISMATCH
    assert result.payload_bytes_hashed == 7


def test_step5b3_streams_in_configured_chunks(tmp_path, monkeypatch):
    fixture = _step5b3_fixture(
        tmp_path, (("declared.bin", b"abcdefgh", None, None),)
    )
    original_read = os.read
    requested_sizes = []

    def recording_read(descriptor, size):
        requested_sizes.append(size)
        return original_read(descriptor, size)

    monkeypatch.setattr(os, "read", recording_read)
    result = _validate_step5b3(
        fixture,
        policy=replace(CacheLookupVerificationPolicy(), read_chunk_size=2),
    )
    assert result.classification is _PayloadValidationClassification.VALID
    assert requested_sizes[:-1] == [2, 2, 2, 2]
    assert requested_sizes[-1] == 1


@pytest.mark.parametrize("stream_fault", ["early_eof", "growth"])
def test_step5b3_rejects_stream_length_boundary_faults(tmp_path, stream_fault):
    fixture = _step5b3_fixture(
        tmp_path, (("declared.bin", b"content", None, None),)
    )

    class FaultFilesystem(LocalReadOnlyCacheFilesystem):
        def stream_regular_file_sha256(self, path, *, declared_size, chunk_size):
            streamed = super().stream_regular_file_sha256(
                path, declared_size=declared_size, chunk_size=chunk_size
            )
            return replace(
                streamed,
                bytes_read=(declared_size - 1 if stream_fault == "early_eof" else declared_size),
                has_additional_byte=stream_fault == "growth",
            )

    result = _validate_step5b3(fixture, filesystem=FaultFilesystem())
    assert result.classification is _PayloadValidationClassification.PAYLOAD_SIZE_MISMATCH


def test_step5b3_does_not_scan_locks_siblings_or_reenumerate_payload(tmp_path):
    fixture = _step5b3_fixture(
        tmp_path, (("declared.bin", b"declared", None, None),)
    )
    listed = []

    class RecordingFilesystem(LocalReadOnlyCacheFilesystem):
        def list_directory(self, path):
            listed.append(Path(path))
            return super().list_directory(path)

    result = _validate_step5b3(fixture, filesystem=RecordingFilesystem())
    assert result.classification is _PayloadValidationClassification.VALID
    assert listed == [fixture[0] / "payload"]
    assert not any("locks" in path.parts for path in listed)


def test_step5b3_remains_internal_and_read_only(tmp_path):
    import engine.storage.cache_lookup as cache_lookup

    fixture = _step5b3_fixture(
        tmp_path, (("declared.bin", b"declared", None, None),)
    )
    before = tuple(sorted((fixture[0] / "payload").rglob("*")))
    result = _validate_step5b3(fixture)
    after = tuple(sorted((fixture[0] / "payload").rglob("*")))

    assert result.classification is _PayloadValidationClassification.VALID
    assert before == after
    assert not hasattr(cache_lookup, "lookup_cache_entry")
    assert not hasattr(cache_lookup, "HIT")
    assert not hasattr(cache_lookup, "MISS")
    assert not hasattr(cache_lookup, "LOCKED_OR_IN_PROGRESS")


def _step5b4_entry_fixture(tmp_path):
    fixture = _step5b3_fixture(
        tmp_path, (("nested/payload.bin", b"payload", None, None),)
    )
    root = ValidatedCacheRoot.from_path(tmp_path / "cache", filesystem=FILESYSTEM)
    before = _capture_entry_snapshot(root, fixture[0], filesystem=FILESYSTEM)
    payload = _validate_step5b3(fixture)
    return fixture, root, before, payload


def test_step5b4_stable_snapshot_promotes_only_internal_verification_level(tmp_path):
    fixture, root, before, payload = _step5b4_entry_fixture(tmp_path)

    result = _validate_stable_entry_snapshot(
        root,
        fixture[0],
        before,
        fixture[1].documents,
        payload,
        filesystem=FILESYSTEM,
    )
    assert result.classification is _StableSnapshotClassification.VALID
    assert result.verification_level is _CacheVerificationLevel.FULL_PAYLOAD_SHA256
    assert result.payload_bytes_fully_hashed


@pytest.mark.parametrize(
    "change",
    [
        "root_identity",
        "entry_identity",
        "top_listing",
        "payload_listing",
        "payload_content",
        "document_content",
        "disappearance",
        "type_change",
        "document_read",
    ],
)
def test_step5b4_rejects_entry_snapshot_instability(tmp_path, change):
    fixture, root, before, payload = _step5b4_entry_fixture(tmp_path)
    filesystem = FILESYSTEM
    documents = fixture[1].documents
    entry = fixture[0]
    payload_file = entry / "payload" / "nested" / "payload.bin"
    if change == "root_identity":
        before = replace(
            before,
            root_identity=replace(before.root_identity, modification_time_ns=-1),
        )
    elif change == "entry_identity":
        before = replace(
            before,
            entry_identity=replace(before.entry_identity, modification_time_ns=-1),
        )
    elif change == "top_listing":
        (entry / "extra").write_bytes(b"extra")
    elif change == "payload_listing":
        (entry / "payload" / "extra").write_bytes(b"extra")
    elif change == "payload_content":
        payload_file.write_bytes(b"changed")
    elif change == "document_content":
        metadata_path = entry / "metadata.json"
        metadata_path.write_bytes(metadata_path.read_bytes() + b" ")
    elif change == "disappearance":
        payload_file.unlink()
    elif change == "type_change":
        payload_file.unlink()
        payload_file.mkdir()
    elif change == "document_read":
        changed_metadata = replace(documents.metadata, stable_read=False)
        documents = replace(documents, metadata=changed_metadata)

    result = _validate_stable_entry_snapshot(
        root,
        entry,
        before,
        documents,
        payload,
        filesystem=filesystem,
    )
    assert result.classification is _StableSnapshotClassification.UNSTABLE_SNAPSHOT
    assert result.verification_level is _CacheVerificationLevel.DOCUMENT_INTEGRITY
    assert not result.payload_bytes_fully_hashed


def test_step5b4_fails_closed_without_device_file_identity_and_never_retries(tmp_path):
    fixture, root, before, payload = _step5b4_entry_fixture(tmp_path)

    class ReducedIdentityFilesystem(LocalReadOnlyCacheFilesystem):
        def __init__(self):
            self.entry_listings = 0

        def inspect(self, path):
            return replace(super().inspect(path), device_id=None, file_id=None)

        def list_directory(self, path):
            if Path(path) == fixture[0]:
                self.entry_listings += 1
            return super().list_directory(path)

    filesystem = ReducedIdentityFilesystem()
    result = _validate_stable_entry_snapshot(
        root,
        fixture[0],
        before,
        fixture[1].documents,
        payload,
        filesystem=filesystem,
    )
    assert result.classification is _StableSnapshotClassification.UNSTABLE_SNAPSHOT
    assert filesystem.entry_listings == 1


def test_step5b4_diagnostics_are_deduplicated_ordered_sanitized_and_truncated():
    unstable = _CacheLookupDiagnostic(
        _CacheLookupDiagnosticCode.UNSTABLE,
        _CacheLookupSubject.PAYLOAD,
        "nested/payload.bin",
    )
    unsafe = _CacheLookupDiagnostic(
        _CacheLookupDiagnosticCode.UNSAFE_OBJECT,
        _CacheLookupSubject.PAYLOAD,
        "unsafe",
    )
    unexpected = _CacheLookupDiagnostic(
        _CacheLookupDiagnosticCode.UNEXPECTED_OBJECT,
        _CacheLookupSubject.PAYLOAD,
        "extra",
    )
    io_failure = _CacheLookupDiagnostic(
        _CacheLookupDiagnosticCode.IO_FAILURE,
        _CacheLookupSubject.DOCUMENT,
        "metadata.json",
    )
    finalized = _finalize_diagnostics(
        (unstable, unexpected, unsafe, io_failure, unstable), limit=3
    )

    assert finalized[0] == unsafe
    assert finalized[1] == unexpected
    assert finalized[2].code is _CacheLookupDiagnosticCode.TRUNCATED
    with pytest.raises(ValueError, match="sanitized"):
        _CacheLookupDiagnostic(
            _CacheLookupDiagnosticCode.IO_FAILURE,
            _CacheLookupSubject.ENTRY,
            "/private/secret",
        )
    assert all("private" not in repr(item) for item in finalized)


def test_step5b4_observer_receives_aggregates_and_failures_are_isolated(tmp_path):
    fixture, root, before, payload = _step5b4_entry_fixture(tmp_path)
    events = []

    class Observer:
        def observe(self, event):
            events.append(event)

    successful = _validate_stable_entry_snapshot(
        root,
        fixture[0],
        before,
        fixture[1].documents,
        payload,
        observer=Observer(),
    )

    class FailingObserver:
        def observe(self, event):
            raise RuntimeError("private observer failure")

    isolated = _validate_stable_entry_snapshot(
        root,
        fixture[0],
        before,
        fixture[1].documents,
        payload,
        observer=FailingObserver(),
    )
    assert successful == isolated
    assert events == [
        _CacheLookupObserverEvent(
            "payload_bytes_hashed",
            "valid",
            item_count=1,
            byte_count=7,
        )
    ]


def _lock_document(entry_digest, **changes):
    data = {
        "acquired_at_utc": "2026-07-20T09:00:00Z",
        "entry_digest": entry_digest,
        "heartbeat_at_utc": "2026-07-20T09:00:10Z",
        "host_id": "host-1",
        "lock_version": 1,
        "owner_token": "owner-1",
        "process_id": 123,
    }
    data.update(changes)
    return canonical_json_bytes(data)


def _lock_fixture(tmp_path, *, content=None):
    root_path = tmp_path / "cache"
    root_path.mkdir(parents=True)
    namespace = CacheNamespace("audio", "transcription.whisper", 3)
    cache_key = CacheKey("a" * 64)
    lock_path = derive_lock_path(root_path.resolve(), namespace, cache_key)
    if content is not None:
        lock_path.parent.mkdir(parents=True)
        lock_path.write_bytes(content)
    root = ValidatedCacheRoot.from_path(root_path, filesystem=FILESYSTEM)
    return root, namespace, cache_key, lock_path


class _FixedLockClock:
    def __init__(self, value):
        self.value = value

    def now_utc(self):
        return self.value


def _observe_lock_fixture(fixture, *, freshness=10, clock_second=20, filesystem=FILESYSTEM):
    root, namespace, cache_key, _ = fixture
    return _observe_matching_lock(
        root,
        namespace,
        cache_key,
        policy=LockObservationPolicy(freshness),
        clock=_FixedLockClock(
            datetime(2026, 7, 20, 9, 0, clock_second, tzinfo=timezone.utc)
        ),
        filesystem=filesystem,
    )


def test_step5b4_lock_policy_is_explicit_bounded_and_fixed_size():
    assert LockObservationPolicy(1).max_lock_document_bytes == 16 * 1024
    assert LockObservationPolicy(2_592_000).active_freshness_seconds == 2_592_000
    for invalid in (0, -1, True, 2_592_001, None):
        with pytest.raises(ValueError):
            LockObservationPolicy(invalid)
    with pytest.raises(ValueError, match="16384"):
        LockObservationPolicy(10, max_lock_document_bytes=1)


def test_step5b4_matching_lock_absence_is_internal(tmp_path):
    result = _observe_lock_fixture(_lock_fixture(tmp_path))
    assert result.classification is _LockObservationClassification.ABSENT
    assert result.parsed_lock is None


@pytest.mark.parametrize(
    ("freshness", "clock_second", "classification"),
    [
        (10, 19, _LockObservationClassification.ACTIVE),
        (10, 20, _LockObservationClassification.ACTIVE),
        (10, 21, _LockObservationClassification.STALE),
    ],
)
def test_step5b4_matching_lock_active_threshold_and_stale(
    tmp_path, freshness, clock_second, classification
):
    key = CacheKey("a" * 64)
    fixture = _lock_fixture(
        tmp_path, content=_lock_document(derive_entry_digest(key))
    )
    result = _observe_lock_fixture(
        fixture, freshness=freshness, clock_second=clock_second
    )
    assert result.classification is classification
    assert result.stable_read


@pytest.mark.parametrize(
    ("content_factory", "classification"),
    [
        (lambda digest: b"not-json", _LockObservationClassification.MALFORMED_LOCK),
        (
            lambda digest: canonical_json_bytes({"lock_version": 2, "future": True}),
            _LockObservationClassification.UNSUPPORTED_LOCK_VERSION,
        ),
        (
            lambda digest: _lock_document(digest) + b"\n",
            _LockObservationClassification.MALFORMED_LOCK,
        ),
        (
            lambda digest: _lock_document(digest, future=True),
            _LockObservationClassification.MALFORMED_LOCK,
        ),
        (
            lambda digest: _lock_document("b" * 64),
            _LockObservationClassification.LOCK_IDENTITY_CONFLICT,
        ),
        (
            lambda digest: _lock_document(
                digest, acquired_at_utc="2026-07-20T09:00:21Z"
            ),
            _LockObservationClassification.LOCK_TIMESTAMP_INVALID,
        ),
        (
            lambda digest: _lock_document(
                digest, heartbeat_at_utc="2026-07-20T09:00:21Z"
            ),
            _LockObservationClassification.LOCK_TIMESTAMP_INVALID,
        ),
        (
            lambda digest: _lock_document(
                digest,
                acquired_at_utc="2026-07-20T09:00:11Z",
                heartbeat_at_utc="2026-07-20T09:00:10Z",
            ),
            _LockObservationClassification.LOCK_TIMESTAMP_INVALID,
        ),
    ],
)
def test_step5b4_lock_document_classifications(
    tmp_path, content_factory, classification
):
    key = CacheKey("a" * 64)
    fixture = _lock_fixture(
        tmp_path, content=content_factory(derive_entry_digest(key))
    )
    assert _observe_lock_fixture(fixture).classification is classification


def test_step5b4_duplicate_key_and_oversized_lock_are_malformed(tmp_path):
    key = CacheKey("a" * 64)
    valid = _lock_document(derive_entry_digest(key))
    duplicate = b'{"lock_version":1,' + valid[1:]
    duplicate_result = _observe_lock_fixture(
        _lock_fixture(tmp_path / "duplicate", content=duplicate)
    )
    oversized_fixture = _lock_fixture(
        tmp_path / "oversized", content=b"x" * (16 * 1024 + 1)
    )
    assert duplicate_result.classification is _LockObservationClassification.MALFORMED_LOCK
    assert (
        _observe_lock_fixture(oversized_fixture).classification
        is _LockObservationClassification.MALFORMED_LOCK
    )


def test_step5b4_symlink_lock_is_unsafe_and_unrelated_locks_are_never_listed(tmp_path):
    fixture = _lock_fixture(tmp_path)
    target = tmp_path / "target"
    target.write_bytes(b"target")
    fixture[3].parent.mkdir(parents=True)
    fixture[3].symlink_to(target)
    listed = []

    class RecordingFilesystem(LocalReadOnlyCacheFilesystem):
        def list_directory(self, path):
            listed.append(Path(path))
            return super().list_directory(path)

    result = _observe_lock_fixture(fixture, filesystem=RecordingFilesystem())
    assert result.classification is _LockObservationClassification.UNSAFE_OBJECT
    assert listed == []


def test_step5b4_special_object_lock_is_unsafe(tmp_path):
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO creation is unavailable")
    fixture = _lock_fixture(tmp_path)
    fixture[3].parent.mkdir(parents=True)
    os.mkfifo(fixture[3])

    assert (
        _observe_lock_fixture(fixture).classification
        is _LockObservationClassification.UNSAFE_OBJECT
    )


def test_step5b4_lock_io_and_instability_are_not_absence(tmp_path):
    key = CacheKey("a" * 64)
    fixture = _lock_fixture(
        tmp_path, content=_lock_document(derive_entry_digest(key))
    )

    class IOFilesystem(LocalReadOnlyCacheFilesystem):
        def read_regular_file_bounded(self, path, *, max_bytes):
            raise CacheLookupIOError("private")

    class PermissionFilesystem(LocalReadOnlyCacheFilesystem):
        def read_regular_file_bounded(self, path, *, max_bytes):
            raise CacheLookupPermissionError("private")

    class UnstableFilesystem(LocalReadOnlyCacheFilesystem):
        def read_regular_file_bounded(self, path, *, max_bytes):
            return replace(
                super().read_regular_file_bounded(path, max_bytes=max_bytes),
                stable_read=False,
            )

    assert (
        _observe_lock_fixture(fixture, filesystem=IOFilesystem()).classification
        is _LockObservationClassification.IO_FAILURE
    )
    assert (
        _observe_lock_fixture(
            fixture, filesystem=PermissionFilesystem()
        ).classification
        is _LockObservationClassification.IO_FAILURE
    )
    assert (
        _observe_lock_fixture(fixture, filesystem=UnstableFilesystem()).classification
        is _LockObservationClassification.UNSTABLE_SNAPSHOT
    )


def test_step5b4_invalid_clock_is_dependency_error(tmp_path):
    fixture = _lock_fixture(tmp_path)
    root, namespace, cache_key, _ = fixture
    with pytest.raises(ValueError, match="UTC whole-second"):
        _observe_matching_lock(
            root,
            namespace,
            cache_key,
            policy=LockObservationPolicy(10),
            clock=_FixedLockClock(datetime(2026, 7, 20, 9, 0, 20)),
        )


def test_step5b4_adds_no_public_lookup_or_mutation_surface():
    import engine.storage.cache_lookup as cache_lookup

    assert not hasattr(cache_lookup, "lookup_cache_entry")
    assert not hasattr(cache_lookup, "ValidatedCacheEntryReference")
    assert not hasattr(cache_lookup, "HIT")
    assert not hasattr(cache_lookup, "MISS")
    assert not hasattr(cache_lookup, "LOCKED_OR_IN_PROGRESS")
    public = {name for name in dir(ReadOnlyCacheFilesystem) if not name.startswith("_")}
    assert public == {
        "inspect",
        "list_directory",
        "read_regular_file_bounded",
        "resolve",
    }
