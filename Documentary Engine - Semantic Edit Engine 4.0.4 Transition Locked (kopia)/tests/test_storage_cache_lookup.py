from dataclasses import FrozenInstanceError, fields, replace
import os
from pathlib import Path
import socket
import tempfile

import pytest

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
    ReadOnlyCacheFilesystem,
    SymlinkRejectedError,
    UnstableFilesystemObjectError,
    UnsupportedFilesystemObjectError,
    ValidatedCacheRoot,
    _FinalEntryStructureClassification,
    _inspect_final_entry_structure,
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
