import errno
import os
import shutil
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from engine.storage.cache_keys import CacheKey
from engine.storage.cache_lookup import (
    CacheArtifactExpectation,
    CacheLookupRequest,
    CacheLookupStatus,
    CacheLookupVerificationPolicy,
    FileIdentity,
    FilesystemObjectType,
    LockObservationPolicy,
    LocalReadOnlyCacheFilesystem,
    ProducerPayloadExpectation,
    ValidatedCacheRoot,
    lookup_cache_entry,
)
from engine.storage.cache_promotion import (
    CachePromotionRequest,
    CachePromotionStatus,
    LocalCachePromotionFilesystem,
    LockRefreshStatus,
    LockReleaseStatus,
    OwnedWriterLock,
    WriterLockDocument,
    WriterOwnerMetadata,
    acquire_writer_lock,
    promote_cache_entry,
    refresh_owned_lock,
    release_owned_writer_lock,
    verify_owned_writer_lock,
)
from engine.storage.cache_writer import (
    CacheStagingWriteRequest,
    StagedCacheEntryReference,
    StagingPayloadSource,
    write_cache_staging_entry,
)
from engine.storage.persistent_cache import (
    CacheArtifactMetadata,
    CacheLookupExpectation,
    CacheNamespace,
    CacheProducerMetadata,
    CacheRuntimeFingerprint,
    derive_final_entry_path,
    derive_lock_path,
)


@dataclass(frozen=True)
class FixedClock:
    instant: datetime = datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc)

    def now_utc(self):
        return self.instant


@dataclass(frozen=True)
class FixedToken:
    token: str = "0123456789abcdef0123456789abcdef"

    def fresh_token(self):
        return self.token


class InstrumentedFilesystem(LocalCachePromotionFilesystem):
    def __init__(self):
        self.events = []
        self.create_error = None
        self.raise_after_create = False
        self.rename_error = None
        self.rename_supported = True
        self.unlink_supported = True
        self.unlink_error = None
        self.replace_supported = False
        self.replace_error = None
        self.flush_error_for = None
        self.device_override = {}
        self.after_rename_inspect = None
        self.replacement_before_unlink = None
        self.renamed = False
        self.payload_hashes_after_rename = 0

    def inspect(self, path):
        path = Path(path)
        self.events.append("inspect:" + path.name)
        if self.renamed and self.after_rename_inspect == path.name:
            raise OSError(errno.EIO, "injected post-check failure")
        identity = super().inspect(path)
        override = self.device_override.get(path)
        return replace(identity, device_id=override) if path in self.device_override else identity

    def list_directory(self, path):
        self.events.append("list:" + Path(path).name)
        return super().list_directory(path)

    def read_regular_file_bounded(self, path, *, max_bytes):
        self.events.append("read:" + Path(path).name)
        return super().read_regular_file_bounded(path, max_bytes=max_bytes)

    def stream_regular_file_sha256(self, path, *, declared_size, chunk_size):
        self.events.append("hash:" + Path(path).name)
        if self.renamed:
            self.payload_hashes_after_rename += 1
        return super().stream_regular_file_sha256(path, declared_size=declared_size, chunk_size=chunk_size)

    def create_lock_exclusive(self, path, data):
        self.events.append("exclusive-create:" + Path(path).name)
        if self.create_error is not None:
            raise self.create_error
        identity = super().create_lock_exclusive(path, data)
        if self.raise_after_create:
            raise OSError(errno.EIO, "injected post-create failure")
        return identity

    def flush_directory(self, path):
        self.events.append("fsync-dir:" + Path(path).name)
        if self.flush_error_for == Path(path):
            raise OSError(errno.EIO, "injected directory fsync failure")
        return super().flush_directory(path)

    def supports_atomic_noreplace_rename(self):
        self.events.append("rename-capability")
        return self.rename_supported

    def rename_directory_noreplace(self, source, destination):
        self.events.append("rename")
        if self.rename_error is not None:
            raise self.rename_error
        if Path(destination).exists():
            raise FileExistsError(errno.EEXIST, "occupied", str(destination))
        os.rename(source, destination)
        self.renamed = True

    def supports_identity_conditional_unlink(self):
        self.events.append("unlink-capability")
        return self.unlink_supported

    def unlink_if_same_identity(self, path, expected):
        self.events.append("conditional-unlink")
        if self.unlink_error is not None:
            raise self.unlink_error
        if self.replacement_before_unlink is not None:
            Path(path).unlink()
            Path(path).write_bytes(self.replacement_before_unlink)
        current = self.inspect(path)
        if not expected.same_stable_object(current):
            return False
        Path(path).unlink()
        return True

    def supports_identity_conditional_replace(self):
        self.events.append("replace-capability")
        return self.replace_supported

    def replace_if_same_identity(self, original, replacement_path, expected):
        self.events.append("conditional-replace")
        if self.replace_error is not None:
            raise self.replace_error
        current = self.inspect(original)
        if not expected.same_stable_object(current):
            return None
        os.replace(replacement_path, original)
        return self.inspect(original)


def _request(tmp_path, *, token="writer-1"):
    root = tmp_path / "cache"
    root.mkdir()
    namespace = CacheNamespace("audio", "transcription.whisper", 3)
    key = CacheKey("a" * 64)
    source = tmp_path / "source"
    source.write_bytes(b"payload")
    staging_request = CacheStagingWriteRequest(
        ValidatedCacheRoot.from_path(root), namespace, key, token,
        CacheArtifactMetadata("transcript", "logical-1", 1),
        CacheProducerMetadata("transcription.whisper", "1.0", 3),
        CacheRuntimeFingerprint(1, {"model": "large-v3"}),
        "2026-07-20T09:00:00Z",
        (StagingPayloadSource(source.resolve(), "one.bin"),),
        CacheLookupVerificationPolicy(),
    )
    staged = write_cache_staging_entry(staging_request)
    lock = derive_lock_path(root, namespace, key)
    lock.parent.mkdir(parents=True)
    final = derive_final_entry_path(root, namespace, key)
    final.parent.mkdir(parents=True)
    return CachePromotionRequest(
        ValidatedCacheRoot.from_path(root), namespace, key, token, staged,
        WriterOwnerMetadata("host-1", 123),
    )


def _paths(request):
    return (
        request.staged_entry.staging_path,
        derive_final_entry_path(request.cache_root.resolved_path, request.namespace, request.cache_key),
        derive_lock_path(request.cache_root.resolved_path, request.namespace, request.cache_key),
    )


def _acquire(request, filesystem=None):
    filesystem = filesystem or InstrumentedFilesystem()
    status, owned = acquire_writer_lock(
        request, filesystem=filesystem, clock=FixedClock(), token_source=FixedToken()
    )
    return filesystem, status, owned


def _promote(request, filesystem=None):
    filesystem = filesystem or InstrumentedFilesystem()
    return filesystem, promote_cache_entry(
        request, filesystem=filesystem, clock=FixedClock(), token_source=FixedToken()
    )


def test_exclusive_acquisition_is_canonical_durable_and_does_not_scan(tmp_path):
    request = _request(tmp_path)
    fs, status, owned = _acquire(request)
    assert status is CachePromotionStatus.LOCK_ACQUIRED
    assert verify_owned_writer_lock(owned, filesystem=fs)
    assert WriterLockDocument.from_json(owned.path.read_bytes()) == owned.document
    assert owned.document.acquired_at_utc == owned.document.heartbeat_at_utc
    assert "fsync-dir:" + owned.path.parent.name in fs.events
    assert not any(event.startswith("list:") for event in fs.events)
    assert fs.events.count("exclusive-create:" + owned.path.name) == 1


def test_another_writer_wins_create_race_without_overwrite_or_retry(tmp_path):
    request = _request(tmp_path)
    lock = _paths(request)[2]
    original = b"foreign writer"
    lock.write_bytes(original)
    fs, status, owned = _acquire(request)
    assert (status, owned, lock.read_bytes()) == (CachePromotionStatus.LOCK_ALREADY_EXISTS, None, original)
    assert sum(event.startswith("exclusive-create:") for event in fs.events) == 1


@pytest.mark.parametrize("kind", ["symlink", "directory", "fifo"])
def test_preexisting_unsafe_lock_object_is_rejected_untouched(tmp_path, kind):
    request = _request(tmp_path)
    lock = _paths(request)[2]
    target = tmp_path / "target"
    server = None
    if kind == "symlink":
        target.write_bytes(b"target"); lock.symlink_to(target)
    elif kind == "directory":
        lock.mkdir()
    elif kind == "fifo":
        os.mkfifo(lock)
    try:
        _, status, owned = _acquire(request)
        assert status is CachePromotionStatus.UNSAFE_LOCK_PATH and owned is None
        assert os.path.lexists(lock)
    finally:
        if server is not None:
            server.close()


def test_injected_preexisting_socket_lock_is_unsafe_without_create_retry(tmp_path):
    request = _request(tmp_path)
    lock = _paths(request)[2]
    fs = InstrumentedFilesystem(); fs.create_error = FileExistsError(errno.EEXIST, "socket")
    original_inspect = fs.inspect
    def inspect(path):
        if Path(path) == lock:
            return FileIdentity(FilesystemObjectType.SOCKET, 1, 2, 0, 0, 0, 1)
        return original_inspect(path)
    fs.inspect = inspect
    _, status, owned = _acquire(request, fs)
    assert status is CachePromotionStatus.UNSAFE_LOCK_PATH and owned is None
    assert sum(event.startswith("exclusive-create:") for event in fs.events) == 1


def test_unsafe_lock_ancestor_blocks_create(tmp_path):
    request = _request(tmp_path)
    lock = _paths(request)[2]
    ancestor = request.cache_root.resolved_path / "locks"
    moved = ancestor.with_name("locks-real")
    ancestor.rename(moved); ancestor.symlink_to(moved, target_is_directory=True)
    request = replace(request, cache_root=ValidatedCacheRoot.from_path(request.cache_root.resolved_path))
    _, status, owned = _acquire(request)
    assert status is CachePromotionStatus.UNSAFE_LOCK_PATH and owned is None
    assert not lock.exists()


@pytest.mark.parametrize("after_create", [False, True])
def test_acquisition_io_failure_never_unconditionally_cleans_lock(tmp_path, after_create):
    request = _request(tmp_path)
    fs = InstrumentedFilesystem()
    if after_create:
        fs.raise_after_create = True
    else:
        fs.create_error = OSError(errno.EIO, "injected")
    _, status, owned = _acquire(request, fs)
    lock = _paths(request)[2]
    assert status is CachePromotionStatus.LOCK_IO_FAILURE and owned is None
    assert lock.exists() is after_create
    assert "conditional-unlink" not in fs.events


def test_lock_parent_fsync_failure_leaves_indeterminate_lock(tmp_path):
    request = _request(tmp_path)
    fs = InstrumentedFilesystem(); fs.flush_error_for = _paths(request)[2].parent
    _, status, _ = _acquire(request, fs)
    assert status is CachePromotionStatus.LOCK_IO_FAILURE
    assert _paths(request)[2].is_file()
    assert "conditional-unlink" not in fs.events


def test_lock_ancestor_replacement_during_acquisition_fails_without_retry(tmp_path):
    request = _request(tmp_path)
    lock = _paths(request)[2]
    class ReplacingAncestorFilesystem(InstrumentedFilesystem):
        def create_lock_exclusive(self, path, data):
            parent = Path(path).parent
            moved = parent.with_name(parent.name + "-replaced")
            parent.rename(moved); parent.mkdir()
            return super().create_lock_exclusive(path, data)
    fs, status, owned = _acquire(request, ReplacingAncestorFilesystem())
    assert status is CachePromotionStatus.UNSTABLE_LOCK_PATH and owned is None
    assert fs.events.count("exclusive-create:" + lock.name) == 1


@pytest.mark.parametrize("mutation", ["token", "digest", "malformed", "disappear", "replace", "same-content-replace", "symlink", "fifo", "reduced"])
def test_ownership_proof_fails_closed_for_changed_or_reduced_lock(tmp_path, mutation):
    request = _request(tmp_path)
    fs, _, owned = _acquire(request)
    lock = owned.path
    if mutation in {"token", "digest"}:
        data = owned.document
        changed = replace(data, **({"owner_token": "other-owner"} if mutation == "token" else {"entry_digest": "b" * 64}))
        lock.write_bytes(changed.canonical_bytes())
    elif mutation == "malformed":
        lock.write_bytes(b"{")
    elif mutation == "disappear":
        lock.unlink()
    elif mutation in {"replace", "same-content-replace"}:
        content = owned.document.canonical_bytes() if mutation == "same-content-replace" else b"replacement"
        lock.unlink(); lock.write_bytes(content)
    elif mutation == "symlink":
        lock.unlink(); target = tmp_path / "other"; target.write_bytes(b"x"); lock.symlink_to(target)
    elif mutation == "fifo":
        lock.unlink(); os.mkfifo(lock)
    else:
        owned = replace(owned, identity=replace(owned.identity, device_id=None))
    assert not verify_owned_writer_lock(owned, filesystem=fs)
    assert "conditional-unlink" not in fs.events


def test_owned_release_removes_exact_lock_and_flushes_parent(tmp_path):
    request = _request(tmp_path)
    fs, _, owned = _acquire(request)
    fs.events.clear()
    assert release_owned_writer_lock(owned, filesystem=fs) is LockReleaseStatus.RELEASED
    assert not owned.path.exists()
    assert "conditional-unlink" in fs.events
    assert "fsync-dir:" + owned.path.parent.name in fs.events


def test_release_capability_unavailable_never_calls_unlink(tmp_path):
    request = _request(tmp_path)
    fs, _, owned = _acquire(request)
    fs.unlink_supported = False; fs.events.clear()
    assert release_owned_writer_lock(owned, filesystem=fs) is LockReleaseStatus.RELEASE_CAPABILITY_UNAVAILABLE
    assert owned.path.is_file() and "conditional-unlink" not in fs.events


def test_conditional_release_replacement_race_never_removes_replacement(tmp_path):
    request = _request(tmp_path)
    fs, _, owned = _acquire(request)
    replacement = b"replacement writer"
    fs.replacement_before_unlink = replacement
    assert release_owned_writer_lock(owned, filesystem=fs) is LockReleaseStatus.OWNERSHIP_LOST
    assert owned.path.read_bytes() == replacement


def test_explicit_refresh_supported_is_atomic_canonical_and_preserves_acquisition(tmp_path):
    request = _request(tmp_path)
    fs, _, owned = _acquire(request)
    fs.replace_supported = True; fs.events.clear()
    later = FixedClock(datetime(2026, 7, 20, 9, 1, tzinfo=timezone.utc))
    status, refreshed = refresh_owned_lock(owned, filesystem=fs, clock=later, nonce_source=FixedToken("refresh-nonce"))
    assert status is LockRefreshStatus.REFRESHED
    assert refreshed.document.acquired_at_utc == owned.document.acquired_at_utc
    assert refreshed.document.heartbeat_at_utc == "2026-07-20T09:01:00Z"
    assert WriterLockDocument.from_json(owned.path.read_bytes()) == refreshed.document
    assert refreshed.identity.same_stable_object(fs.inspect(owned.path))
    assert "conditional-replace" in fs.events
    assert "fsync-dir:" + owned.path.parent.name in fs.events


def test_explicit_refresh_unsupported_and_backwards_time_fail_closed(tmp_path):
    request = _request(tmp_path)
    fs, _, owned = _acquire(request)
    original = owned.path.read_bytes()
    status, refreshed = refresh_owned_lock(owned, filesystem=fs, clock=FixedClock(), nonce_source=FixedToken("nonce"))
    assert status is LockRefreshStatus.REFRESH_CAPABILITY_UNAVAILABLE and refreshed is None
    assert owned.path.read_bytes() == original
    fs.replace_supported = True
    earlier = FixedClock(datetime(2026, 7, 20, 8, 59, 59, tzinfo=timezone.utc))
    status, _ = refresh_owned_lock(owned, filesystem=fs, clock=earlier, nonce_source=FixedToken("nonce"))
    assert status is LockRefreshStatus.INVALID_CLOCK
    assert owned.path.read_bytes() == original


def test_explicit_refresh_rejects_conditional_replacement_ownership_loss(tmp_path):
    request = _request(tmp_path)
    fs, _, owned = _acquire(request)
    fs.replace_supported = True
    fs.replace_if_same_identity = lambda original, replacement_path, expected: None
    status, refreshed = refresh_owned_lock(
        owned, filesystem=fs,
        clock=FixedClock(datetime(2026, 7, 20, 9, 1, tzinfo=timezone.utc)),
        nonce_source=FixedToken("replacement-nonce"),
    )
    assert status is LockRefreshStatus.OWNERSHIP_LOST and refreshed is None
    assert owned.path.read_bytes() == owned.document.canonical_bytes()


@pytest.mark.parametrize("mutation", ["arbitrary-path", "token", "namespace", "keyref", "digest", "metadata", "complete-missing", "complete-malformed", "document", "payload-change", "payload-missing", "hardlink", "staging-symlink", "staging-fifo"])
def test_staging_revalidation_failure_never_promotes(tmp_path, mutation):
    request = _request(tmp_path)
    staging, final, _ = _paths(request)
    if mutation == "arbitrary-path":
        request = replace(request, staged_entry=replace(request.staged_entry, staging_path=tmp_path / "arbitrary"))
    elif mutation == "token":
        request = replace(request, writer_token="other-writer")
    elif mutation == "namespace":
        request = replace(request, namespace=CacheNamespace("video", "transcription.whisper", 3))
        derive_lock_path(request.cache_root.resolved_path, request.namespace, request.cache_key).parent.mkdir(parents=True)
    elif mutation == "keyref":
        request = replace(request, staged_entry=replace(request.staged_entry, cache_key_reference=replace(request.staged_entry.cache_key_reference, canonical_value=str(CacheKey("b" * 64)))))
    elif mutation == "digest":
        request = replace(request, staged_entry=replace(request.staged_entry, entry_digest="b" * 64))
    elif mutation == "metadata":
        changed = replace(request.staged_entry.metadata, artifact=CacheArtifactMetadata("other", "logical-1", 1))
        request = replace(request, staged_entry=replace(request.staged_entry, metadata=changed))
    elif mutation == "complete-missing":
        (staging / "COMPLETE").unlink()
    elif mutation == "complete-malformed":
        (staging / "COMPLETE").write_bytes(b"{")
    elif mutation == "document":
        (staging / "metadata.json").write_bytes(b"{}");
    elif mutation == "payload-change":
        (staging / "payload" / "one.bin").write_bytes(b"changed")
    elif mutation == "payload-missing":
        (staging / "payload" / "one.bin").unlink()
    elif mutation == "hardlink":
        payload = staging / "payload" / "one.bin"; other = tmp_path / "hardlink"; os.link(payload, other)
    elif mutation == "staging-symlink":
        moved = staging.with_name(staging.name + ".moved"); staging.rename(moved); staging.symlink_to(moved, target_is_directory=True)
    else:
        shutil.rmtree(staging); os.mkfifo(staging)
    fs, result = _promote(request)
    assert result.status is CachePromotionStatus.STAGING_INVALID
    assert not final.exists()
    assert "rename" not in fs.events


@pytest.mark.parametrize("target", ["directory", "ancestor"])
def test_staging_directory_or_ancestor_replacement_blocks_promotion(tmp_path, target):
    request = _request(tmp_path)
    staging, final, _ = _paths(request)
    victim = staging if target == "directory" else staging.parent
    moved = victim.with_name(victim.name + "-old")
    victim.rename(moved); victim.mkdir()
    request = replace(request, cache_root=ValidatedCacheRoot.from_path(request.cache_root.resolved_path))
    fs, result = _promote(request)
    assert result.status is CachePromotionStatus.STAGING_INVALID
    assert not final.exists() and "rename" not in fs.events


@pytest.mark.parametrize("kind", ["valid-directory", "invalid-directory", "file", "symlink", "fifo"])
def test_every_final_object_collision_is_preserved(tmp_path, kind):
    request = _request(tmp_path)
    staging, final, _ = _paths(request)
    server = None
    if kind == "valid-directory":
        shutil.copytree(staging, final)
    elif kind == "invalid-directory":
        final.mkdir()
    elif kind == "file":
        final.write_bytes(b"winner")
    elif kind == "symlink":
        target = tmp_path / "winner"; target.mkdir(); final.symlink_to(target, target_is_directory=True)
    elif kind == "fifo":
        os.mkfifo(final)
    try:
        fs, result = _promote(request)
        assert result.status is CachePromotionStatus.FINAL_PATH_OCCUPIED
        assert os.path.lexists(final) and staging.is_dir()
        assert "rename" not in fs.events
    finally:
        if server is not None:
            server.close()


def test_injected_existing_socket_final_is_collision_not_repaired(tmp_path):
    request = _request(tmp_path)
    staging, final, _ = _paths(request)
    fs = InstrumentedFilesystem()
    original_inspect = fs.inspect
    def inspect(path):
        if Path(path) == final:
            return FileIdentity(FilesystemObjectType.SOCKET, 1, 2, 0, 0, 0, 1)
        return original_inspect(path)
    fs.inspect = inspect
    _, result = _promote(request, fs)
    assert result.status is CachePromotionStatus.FINAL_PATH_OCCUPIED
    assert staging.is_dir() and "rename" not in fs.events


def test_unsafe_final_ancestor_prevents_rename(tmp_path):
    request = _request(tmp_path)
    staging, final, _ = _paths(request)
    entries = request.cache_root.resolved_path / "entries"
    moved = entries.with_name("entries-real"); entries.rename(moved); entries.symlink_to(moved, target_is_directory=True)
    request = replace(request, cache_root=ValidatedCacheRoot.from_path(request.cache_root.resolved_path))
    fs, result = _promote(request)
    assert result.status is CachePromotionStatus.UNSAFE_FINAL_PATH
    assert staging.is_dir() and not final.exists() and "rename" not in fs.events


def test_final_ancestor_replacement_during_inspection_blocks_rename(tmp_path):
    request = _request(tmp_path)
    staging, final, _ = _paths(request)
    class ReplacingFinalAncestorFilesystem(InstrumentedFilesystem):
        def __init__(self):
            super().__init__(); self.done = False
        def inspect(self, path):
            identity = super().inspect(path)
            if Path(path) == final.parent and not self.done:
                self.done = True
                moved = final.parent.with_name(final.parent.name + "-old")
                final.parent.rename(moved); final.parent.mkdir()
            return identity
    fs, result = _promote(request, ReplacingFinalAncestorFilesystem())
    assert result.status is CachePromotionStatus.UNSTABLE_FINAL_PATH
    assert staging.is_dir() and not final.exists() and "rename" not in fs.events


@pytest.mark.parametrize("mode,expected", [
    ("missing-device", CachePromotionStatus.SAME_FILESYSTEM_CAPABILITY_UNAVAILABLE),
    ("cross-device", CachePromotionStatus.CROSS_FILESYSTEM),
    ("no-rename", CachePromotionStatus.PROMOTION_CAPABILITY_UNAVAILABLE),
    ("collision-race", CachePromotionStatus.FINAL_PATH_OCCUPIED_RACE),
    ("exdev", CachePromotionStatus.CROSS_FILESYSTEM_RACE),
    ("io", CachePromotionStatus.PROMOTION_IO_FAILURE),
])
def test_same_filesystem_and_rename_failure_matrix_preserves_staging(tmp_path, mode, expected):
    request = _request(tmp_path)
    staging, final, _ = _paths(request)
    fs = InstrumentedFilesystem()
    if mode == "missing-device":
        fs.device_override[final.parent] = None
    elif mode == "cross-device":
        fs.device_override[final.parent] = 999999
    elif mode == "no-rename":
        fs.rename_supported = False
    elif mode == "collision-race":
        fs.rename_error = FileExistsError(errno.EEXIST, "race")
    elif mode == "exdev":
        fs.rename_error = OSError(errno.EXDEV, "cross-device race")
    else:
        fs.rename_error = OSError(errno.EIO, "rename failure")
    _, result = _promote(request, fs)
    assert result.status is expected
    assert staging.is_dir() and not final.exists()
    assert not any(event.startswith("copy") or event.startswith("delete") for event in fs.events)


def test_success_is_one_atomic_directory_mutation_with_no_post_rename_rehash(tmp_path):
    request = _request(tmp_path)
    staging, final, _ = _paths(request)
    before = os.lstat(staging)
    fs, result = _promote(request)
    assert result.status is CachePromotionStatus.PROMOTED_AND_RELEASED
    assert not staging.exists() and final.is_dir()
    after = os.lstat(final)
    assert (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)
    assert result.promoted_entry.marker.canonical_bytes() == (final / "COMPLETE").read_bytes()
    assert fs.events.count("rename") == 1 and fs.payload_hashes_after_rename == 0
    assert "fsync-dir:" + final.parent.name in fs.events


@pytest.mark.parametrize("failure", ["staging-check", "final-check", "complete-check", "final-parent-fsync"])
def test_post_promotion_uncertainty_never_rolls_back_or_recreates_staging(tmp_path, failure):
    request = _request(tmp_path)
    staging, final, _ = _paths(request)
    fs = InstrumentedFilesystem()
    if failure == "staging-check":
        fs.after_rename_inspect = staging.name
    elif failure == "final-check":
        fs.after_rename_inspect = final.name
    elif failure == "complete-check":
        fs.after_rename_inspect = "COMPLETE"
    else:
        fs.flush_error_for = final.parent
    _, result = _promote(request, fs)
    assert result.status is CachePromotionStatus.PROMOTED_OUTCOME_UNCERTAIN
    assert not staging.exists() and final.exists()
    assert fs.events.count("rename") == 1


@pytest.mark.parametrize("mutation", ["complete-missing", "complete-changed", "final-replaced"])
def test_post_rename_content_or_identity_change_is_uncertain_and_not_repaired(tmp_path, mutation):
    request = _request(tmp_path)
    staging, final, _ = _paths(request)
    class MutatingAfterRenameFilesystem(InstrumentedFilesystem):
        def rename_directory_noreplace(self, source, destination):
            super().rename_directory_noreplace(source, destination)
            if mutation == "complete-missing":
                (Path(destination) / "COMPLETE").unlink()
            elif mutation == "complete-changed":
                (Path(destination) / "COMPLETE").write_bytes(b"{}")
            else:
                moved = Path(destination).with_name(Path(destination).name + "-original")
                Path(destination).rename(moved); Path(destination).mkdir()
    fs, result = _promote(request, MutatingAfterRenameFilesystem())
    assert result.status is CachePromotionStatus.PROMOTED_OUTCOME_UNCERTAIN
    assert not staging.exists() and final.exists()
    assert fs.events.count("rename") == 1


def test_release_failure_after_success_retains_published_final(tmp_path):
    request = _request(tmp_path)
    staging, final, lock = _paths(request)
    fs = InstrumentedFilesystem(); fs.unlink_error = OSError(errno.EIO, "release")
    _, result = _promote(request, fs)
    assert result.status is CachePromotionStatus.PROMOTED_LOCK_RETAINED
    assert result.release_status is LockReleaseStatus.RELEASE_IO_FAILURE
    assert not staging.exists() and final.is_dir() and lock.is_file()


def test_release_replacement_race_after_promotion_keeps_replacement_and_success(tmp_path):
    request = _request(tmp_path)
    staging, final, lock = _paths(request)
    fs = InstrumentedFilesystem(); replacement = b"new writer"; fs.replacement_before_unlink = replacement
    _, result = _promote(request, fs)
    assert result.status is CachePromotionStatus.PROMOTED_LOCK_RETAINED
    assert result.release_status is LockReleaseStatus.OWNERSHIP_LOST
    assert not staging.exists() and final.is_dir() and lock.read_bytes() == replacement


def test_normal_promotion_never_refreshes_and_unsupported_refresh_does_not_block(tmp_path):
    request = _request(tmp_path)
    fs, result = _promote(request)
    assert result.status is CachePromotionStatus.PROMOTED_AND_RELEASED
    assert "conditional-replace" not in fs.events
    assert not any("thread" in event or "heartbeat" in event for event in fs.events)


def test_retained_lock_does_not_hide_later_lookup_hit(tmp_path):
    request = _request(tmp_path)
    fs = InstrumentedFilesystem(); fs.unlink_supported = False
    _, promoted = _promote(request, fs)
    assert promoted.status is CachePromotionStatus.PROMOTED_LOCK_RETAINED
    metadata = request.staged_entry.metadata
    lookup_request = CacheLookupRequest(
        request.cache_root, request.namespace, request.cache_key,
        CacheLookupExpectation(request.namespace, metadata.producer.producer_id, metadata.producer.producer_schema_version, metadata.runtime_fingerprint),
        CacheArtifactExpectation(metadata.artifact.artifact_kind, metadata.artifact.artifact_contract_version, metadata.artifact.logical_id),
        ProducerPayloadExpectation(), CacheLookupVerificationPolicy(), LockObservationPolicy(60),
    )
    lookup = lookup_cache_entry(lookup_request, filesystem=LocalReadOnlyCacheFilesystem(), lock_clock=FixedClock())
    assert lookup.status is CacheLookupStatus.HIT


def test_operation_order_and_read_write_boundaries(tmp_path):
    request = _request(tmp_path)
    fs, result = _promote(request)
    assert result.status is CachePromotionStatus.PROMOTED_AND_RELEASED
    create = next(i for i, event in enumerate(fs.events) if event.startswith("exclusive-create:"))
    first_lock_read = next(i for i, event in enumerate(fs.events) if event == "read:" + _paths(request)[2].name)
    first_hash = next(i for i, event in enumerate(fs.events) if event.startswith("hash:"))
    capability = fs.events.index("rename-capability")
    rename_index = fs.events.index("rename")
    release = fs.events.index("conditional-unlink")
    post_check = max(i for i, event in enumerate(fs.events[:release]) if event == "read:COMPLETE")
    assert create < first_lock_read < first_hash < capability < rename_index < post_check < release
    assert fs.events.count("rename") == 1
    assert not any(event.startswith(("scan-lock", "cleanup", "recursive-delete", "break-lock", "recovery", "retry", "index", "quota")) for event in fs.events)
    readonly_methods = set(dir(LocalReadOnlyCacheFilesystem))
    assert not {"create_lock_exclusive", "rename_directory_noreplace", "unlink_if_same_identity", "replace_if_same_identity"} & readonly_methods


def test_package_does_not_export_local_or_native_testing_internals():
    import engine.storage as storage
    assert not hasattr(storage, "LocalCachePromotionFilesystem")
    assert not any(name.startswith("_Darwin") or name.startswith("_Test") for name in storage.__all__)
