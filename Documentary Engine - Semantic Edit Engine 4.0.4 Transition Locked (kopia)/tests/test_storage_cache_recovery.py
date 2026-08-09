import hashlib
import os
from dataclasses import dataclass, fields, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from engine.storage.cache_keys import CacheKey
from engine.storage.cache_lookup import (
    CacheLookupExpectation,
    CacheLookupReason,
    CacheLookupIOError,
    CacheLookupVerificationPolicy,
    LockObservationPolicy,
    FileIdentity,
    ProducerPayloadExpectation,
    ValidatedCacheRoot,
)
from engine.storage.cache_recovery import (
    CacheRecoveryDiagnostic,
    CacheRecoveryInspectionRequest,
    CacheRecoveryObservation,
    CacheRecoveryReason,
    CacheRecoveryStatus,
    LocalRecoveryReadOnlyFilesystem,
    RecoveryInspectionPolicy,
    RecoveryReadOnlyFilesystem,
    RecoverySubject,
    RecoveryTraversalError,
    RecoveryTraversalLimitError,
    FinalRecoveryObservation,
    FinalRecoveryState,
    LockRecoveryObservation,
    LockRecoveryState,
    StagingRecoveryObservation,
    StagingRecoveryState,
    _compose_recovery_observation,
    _recovery_diagnostics,
    inspect_cache_recovery_state,
    _observe_recovery_components,
    _prepare_recovery_inspection,
)
from engine.storage.persistent_cache import (
    CacheArtifactMetadata,
    CacheNamespace,
    CacheProducerMetadata,
    CacheRuntimeFingerprint,
    canonical_json_bytes,
    derive_entry_digest,
    derive_final_entry_path,
    derive_lock_path,
    derive_staging_entry_path,
)
from engine.storage.cache_writer import (
    CacheStagingWriteRequest,
    StagingPayloadSource,
    write_cache_staging_entry,
)
from engine.storage.cache_promotion import WriterLockDocument


def _request(tmp_path, *, known_writer_token=None):
    root = tmp_path / "cache"
    root.mkdir()
    namespace = CacheNamespace("audio", "transcription.whisper", 3)
    key = CacheKey("a" * 64)
    expectation = CacheLookupExpectation(
        namespace,
        namespace.producer_id,
        namespace.producer_schema_version,
        CacheRuntimeFingerprint(1, {"model": "large-v3"}),
    )
    return CacheRecoveryInspectionRequest(
        ValidatedCacheRoot.from_path(root),
        namespace,
        key,
        expectation,
        None,
        ProducerPayloadExpectation(),
        CacheLookupVerificationPolicy(),
        LockObservationPolicy(60),
        RecoveryInspectionPolicy(),
        known_writer_token,
    )


def _staging_namespace(request):
    return (
        request.cache_root.resolved_path
        / "staging"
        / "v1"
        / request.namespace.domain
        / request.namespace.producer_id
        / str(request.namespace.producer_schema_version)
    )


def test_step5e1_derives_only_contract_paths(tmp_path):
    request = _request(tmp_path, known_writer_token="writer-1")
    traversal = _prepare_recovery_inspection(request)
    root = request.cache_root.resolved_path
    assert traversal.final_path == derive_final_entry_path(
        root, request.namespace, request.cache_key
    )
    assert traversal.lock_path == derive_lock_path(
        root, request.namespace, request.cache_key
    )
    assert traversal.staging_namespace_path == _staging_namespace(request)
    assert traversal.staging_candidate_paths == (
        derive_staging_entry_path(
            root, request.namespace, request.cache_key, "writer-1"
        ),
    )
    for path in (
        traversal.final_path,
        traversal.lock_path,
        traversal.staging_namespace_path,
        *traversal.staging_candidate_paths,
    ):
        path.relative_to(root)


def test_step5e1_known_writer_mode_never_lists_staging_directory(tmp_path):
    request = _request(tmp_path, known_writer_token="writer-1")
    class NoListingFilesystem(LocalRecoveryReadOnlyFilesystem):
        def list_directory_bounded(self, path, *, max_entries):
            raise AssertionError("known-writer mode must not enumerate")
    traversal = _prepare_recovery_inspection(request, filesystem=NoListingFilesystem())
    assert len(traversal.staging_candidate_paths) == 1


def test_step5e1_discovery_is_ordinal_and_filters_other_identities(tmp_path):
    request = _request(tmp_path)
    directory = _staging_namespace(request)
    directory.mkdir(parents=True)
    digest = derive_entry_digest(request.cache_key)
    names = [f"{digest}.writer-z", f"{digest}.writer-a", "b" * 64 + ".writer-x"]
    for name in names:
        (directory / name).mkdir()
    traversal = _prepare_recovery_inspection(
        replace(request, cache_root=ValidatedCacheRoot.from_path(request.cache_root.resolved_path))
    )
    assert tuple(path.name for path in traversal.staging_candidate_paths) == (
        f"{digest}.writer-a",
        f"{digest}.writer-z",
    )


def test_step5e1_does_not_visit_unrelated_namespace(tmp_path):
    request = _request(tmp_path)
    expected = _staging_namespace(request)
    expected.mkdir(parents=True)
    unrelated = request.cache_root.resolved_path / "staging" / "v1" / "video" / "other" / "1"
    unrelated.mkdir(parents=True)
    visited = []
    class RecordingFilesystem(LocalRecoveryReadOnlyFilesystem):
        def inspect(self, path):
            visited.append(Path(path))
            return super().inspect(path)
        def list_directory_bounded(self, path, *, max_entries):
            visited.append(Path(path))
            return super().list_directory_bounded(path, max_entries=max_entries)
    request = replace(request, cache_root=ValidatedCacheRoot.from_path(request.cache_root.resolved_path))
    _prepare_recovery_inspection(request, filesystem=RecordingFilesystem())
    assert unrelated not in visited
    assert all(unrelated not in path.parents for path in visited)


def test_step5e1_directory_entry_limit_is_bounded(tmp_path):
    request = _request(tmp_path)
    directory = _staging_namespace(request)
    directory.mkdir(parents=True)
    class OverLimitFilesystem(LocalRecoveryReadOnlyFilesystem):
        def list_directory_bounded(self, path, *, max_entries):
            return super().list_directory_bounded(path, max_entries=1)
    (directory / "unrelated-a").mkdir(); (directory / "unrelated-b").mkdir()
    request = replace(request, cache_root=ValidatedCacheRoot.from_path(request.cache_root.resolved_path))
    with pytest.raises(RecoveryTraversalLimitError, match="entry limit"):
        _prepare_recovery_inspection(request, filesystem=OverLimitFilesystem())


def test_step5e1_matching_candidate_limit_is_enforced(tmp_path):
    request = _request(tmp_path)
    directory = _staging_namespace(request)
    directory.mkdir(parents=True)
    digest = derive_entry_digest(request.cache_key)
    class TooManyMatchingFilesystem(LocalRecoveryReadOnlyFilesystem):
        def list_directory_bounded(self, path, *, max_entries):
            observed = super().list_directory_bounded(path, max_entries=max_entries)
            object.__setattr__(observed, "names", tuple(
                f"{digest}.writer-{index}" for index in range(65)
            ))
            return observed
    request = replace(request, cache_root=ValidatedCacheRoot.from_path(request.cache_root.resolved_path))
    with pytest.raises(RecoveryTraversalLimitError, match="candidates"):
        _prepare_recovery_inspection(request, filesystem=TooManyMatchingFilesystem())


def test_step5e1_locked_policy_limits_cannot_be_changed():
    for field in fields(RecoveryInspectionPolicy):
        current = getattr(RecoveryInspectionPolicy(), field.name)
        with pytest.raises(ValueError, match="locked"):
            RecoveryInspectionPolicy(**{field.name: current + 1})


@pytest.mark.parametrize("token", ["../escape", "a..b", "", "/absolute", "bad/token"])
def test_step5e1_known_writer_token_cannot_escape_root(tmp_path, token):
    with pytest.raises((TypeError, ValueError)):
        _request(tmp_path, known_writer_token=token)


def test_step5e1_rejects_noncanonical_matching_candidate(tmp_path):
    request = _request(tmp_path)
    directory = _staging_namespace(request)
    directory.mkdir(parents=True)
    digest = derive_entry_digest(request.cache_key)
    (directory / f"{digest}.bad..token").mkdir()
    request = replace(request, cache_root=ValidatedCacheRoot.from_path(request.cache_root.resolved_path))
    with pytest.raises(RecoveryTraversalError, match="not canonical"):
        _prepare_recovery_inspection(request)


def test_step5e1_observations_are_unclassified_and_sanitized(tmp_path):
    request = _request(tmp_path, known_writer_token="secret-writer-token")
    traversal = _prepare_recovery_inspection(request)
    observation = traversal.observation
    assert isinstance(observation, CacheRecoveryObservation)
    assert observation.status is None and observation.reason is None
    assert observation.staging[0].classification is None
    assert "secret-writer-token" not in observation.staging[0].relative_contract_path
    assert not Path(observation.final.relative_contract_path).is_absolute()
    assert not Path(observation.lock.relative_contract_path).is_absolute()


def test_step5e1_filesystem_surface_is_read_only():
    public = {name for name in dir(RecoveryReadOnlyFilesystem) if not name.startswith("_")}
    assert public == {
        "inspect",
        "list_directory",
        "resolve",
        "read_regular_file_bounded",
        "stream_regular_file_sha256",
        "list_directory_bounded",
    }
    forbidden = {"write", "unlink", "rename", "mkdir", "chmod", "promote", "cleanup"}
    assert not public & forbidden


@dataclass(frozen=True)
class _FixedClock:
    instant: datetime = datetime(2026, 7, 20, 9, 1, tzinfo=timezone.utc)

    def now_utc(self):
        return self.instant


def _write_staging(request, token="writer-1"):
    source = request.cache_root.resolved_path.parent / f"source-{token}"
    source.write_bytes(b"payload")
    staged = write_cache_staging_entry(CacheStagingWriteRequest(
        request.cache_root,
        request.namespace,
        request.cache_key,
        token,
        CacheArtifactMetadata("transcript", "logical-1", 1),
        CacheProducerMetadata("transcription.whisper", "1.0", 3),
        request.expectation.runtime_fingerprint,
        "2026-07-20T09:00:00Z",
        (StagingPayloadSource(source.resolve(), "one.bin"),),
        request.lookup_policy,
    ))
    return staged


def _refresh_root(request):
    return replace(
        request,
        cache_root=ValidatedCacheRoot.from_path(request.cache_root.resolved_path),
    )


def _write_lock(request, *, heartbeat="2026-07-20T09:00:00Z", digest=None, version=1):
    path = derive_lock_path(
        request.cache_root.resolved_path, request.namespace, request.cache_key
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    if version == 1:
        data = WriterLockDocument(
            digest or derive_entry_digest(request.cache_key),
            "2026-07-20T09:00:00Z",
            heartbeat,
            "owner-token",
            "host-1",
            123,
        ).canonical_bytes()
    else:
        data = canonical_json_bytes({"lock_version": version})
    path.write_bytes(data)
    return path


def test_step5e2_component_observation_absent_states(tmp_path):
    request = _request(tmp_path, known_writer_token="writer-1")
    observed = _observe_recovery_components(request, lock_clock=_FixedClock())
    assert observed.staging[0].classification is StagingRecoveryState.STAGING_ABSENT
    assert observed.final.classification is FinalRecoveryState.FINAL_ABSENT
    assert observed.lock.classification is LockRecoveryState.LOCK_ABSENT
    assert observed.status is CacheRecoveryStatus.EMPTY
    assert observed.reason is None


def test_step5e2_valid_staging_and_final_reuse_full_lookup_semantics(tmp_path):
    request = _request(tmp_path, known_writer_token="writer-1")
    staged = _write_staging(request)
    request = _refresh_root(request)
    observed = _observe_recovery_components(request, lock_clock=_FixedClock())
    assert observed.staging[0].classification is StagingRecoveryState.STAGING_COMPLETE_VALID
    assert observed.final.classification is FinalRecoveryState.FINAL_ABSENT
    final = derive_final_entry_path(request.cache_root.resolved_path, request.namespace, request.cache_key)
    final.parent.mkdir(parents=True); os.rename(staged.staging_path, final)
    request = _refresh_root(request)
    observed = _observe_recovery_components(request, lock_clock=_FixedClock())
    assert observed.final.classification is FinalRecoveryState.FINAL_VALID


@pytest.mark.parametrize("missing", ["COMPLETE", "manifest.json"])
def test_step5e2_staging_incomplete_only_for_safe_missing_objects(tmp_path, missing):
    request = _request(tmp_path, known_writer_token="writer-1")
    staged = _write_staging(request)
    (staged.staging_path / "COMPLETE").unlink()
    if missing != "COMPLETE":
        (staged.staging_path / missing).unlink()
    request = _refresh_root(request)
    observed = _observe_recovery_components(request, lock_clock=_FixedClock())
    assert observed.staging[0].classification is StagingRecoveryState.STAGING_INCOMPLETE


@pytest.mark.parametrize("document", ["metadata.json", "manifest.json", "COMPLETE"])
def test_step5e2_malformed_staging_documents_are_invalid(tmp_path, document):
    request = _request(tmp_path, known_writer_token="writer-1")
    staged = _write_staging(request)
    (staged.staging_path / document).write_bytes(b"{")
    request = _refresh_root(request)
    observed = _observe_recovery_components(request, lock_clock=_FixedClock())
    assert observed.staging[0].classification is StagingRecoveryState.STAGING_INVALID


def test_step5e2_unsupported_staging_document(tmp_path):
    request = _request(tmp_path, known_writer_token="writer-1")
    staged = _write_staging(request)
    (staged.staging_path / "COMPLETE").write_bytes(canonical_json_bytes({
        "cache_entry_contract_version": 2,
        "entry_digest": derive_entry_digest(request.cache_key),
        "manifest_digest": "sha256:" + "0" * 64,
        "metadata_digest": "sha256:" + "0" * 64,
    }))
    request = _refresh_root(request)
    assert _observe_recovery_components(
        request, lock_clock=_FixedClock()
    ).staging[0].classification is StagingRecoveryState.STAGING_UNSUPPORTED


@pytest.mark.parametrize("mutation", ["payload-missing", "payload-digest", "unexpected", "hardlink", "unsafe-descendant"])
def test_step5e2_invalid_and_unsafe_staging_content(tmp_path, mutation):
    request = _request(tmp_path, known_writer_token="writer-1")
    staged = _write_staging(request)
    payload = staged.staging_path / "payload" / "one.bin"
    if mutation == "payload-missing":
        payload.unlink()
    elif mutation == "payload-digest":
        payload.write_bytes(b"changed")
    elif mutation == "unexpected":
        (staged.staging_path / "unexpected").write_bytes(b"x")
    elif mutation == "hardlink":
        os.link(payload, tmp_path / "hardlink")
    else:
        payload.unlink(); payload.symlink_to(tmp_path / "outside")
    request = _refresh_root(request)
    state = _observe_recovery_components(
        request, lock_clock=_FixedClock()
    ).staging[0].classification
    expected = StagingRecoveryState.STAGING_UNSAFE if mutation == "unsafe-descendant" else StagingRecoveryState.STAGING_INVALID
    assert state is expected


def test_step5e2_unsafe_staging_candidate(tmp_path):
    request = _request(tmp_path, known_writer_token="writer-1")
    path = derive_staging_entry_path(
        request.cache_root.resolved_path, request.namespace, request.cache_key, "writer-1"
    )
    path.parent.mkdir(parents=True); path.symlink_to(tmp_path / "outside")
    request = _refresh_root(request)
    assert _observe_recovery_components(
        request, lock_clock=_FixedClock()
    ).staging[0].classification is StagingRecoveryState.STAGING_UNSAFE


def test_step5e2_discovered_candidate_disappearance_is_unstable(tmp_path):
    request = _request(tmp_path)
    staged = _write_staging(request)
    request = _refresh_root(request)
    class DisappearingFilesystem(LocalRecoveryReadOnlyFilesystem):
        removed = False
        def inspect(self, path):
            if Path(path) == staged.staging_path and not self.removed:
                self.removed = True
                os.rename(path, Path(path).with_name(Path(path).name + "-gone"))
            return super().inspect(path)
    state = _observe_recovery_components(
        request, filesystem=DisappearingFilesystem(), lock_clock=_FixedClock()
    ).staging[0].classification
    assert state is StagingRecoveryState.STAGING_UNSTABLE


def test_step5e2_reduced_staging_identity_is_unstable(tmp_path):
    request = _request(tmp_path, known_writer_token="writer-1")
    staged = _write_staging(request); request = _refresh_root(request)
    class ReducedIdentityFilesystem(LocalRecoveryReadOnlyFilesystem):
        def inspect(self, path):
            identity = super().inspect(path)
            if Path(path) == staged.staging_path:
                return replace(identity, device_id=None)
            return identity
    state = _observe_recovery_components(
        request, filesystem=ReducedIdentityFilesystem(), lock_clock=_FixedClock()
    ).staging[0].classification
    assert state is StagingRecoveryState.STAGING_UNSTABLE


def test_step5e2_multiple_candidates_are_independently_ordered_and_classified(tmp_path):
    request = _request(tmp_path)
    complete = _write_staging(request, "writer-z")
    incomplete = _write_staging(_refresh_root(request), "writer-a")
    (incomplete.staging_path / "COMPLETE").unlink()
    request = _refresh_root(request)
    observed = _observe_recovery_components(request, lock_clock=_FixedClock())
    assert tuple(item.classification for item in observed.staging) == (
        StagingRecoveryState.STAGING_INCOMPLETE,
        StagingRecoveryState.STAGING_COMPLETE_VALID,
    )
    assert complete.staging_path.exists()


@pytest.mark.parametrize("state", ["invalid", "unsupported", "unsafe"])
def test_step5e2_final_rejection_mapping(tmp_path, state):
    request = _request(tmp_path, known_writer_token="writer-1")
    staged = _write_staging(request)
    final = derive_final_entry_path(request.cache_root.resolved_path, request.namespace, request.cache_key)
    final.parent.mkdir(parents=True)
    if state == "unsafe":
        final.symlink_to(staged.staging_path, target_is_directory=True)
    else:
        os.rename(staged.staging_path, final)
        if state == "invalid":
            (final / "COMPLETE").unlink()
        else:
            (final / "COMPLETE").write_bytes(canonical_json_bytes({
                "cache_entry_contract_version": 2,
                "entry_digest": derive_entry_digest(request.cache_key),
                "manifest_digest": "sha256:" + "0" * 64,
                "metadata_digest": "sha256:" + "0" * 64,
            }))
    request = _refresh_root(request)
    observed = _observe_recovery_components(request, lock_clock=_FixedClock())
    expected = {
        "invalid": FinalRecoveryState.FINAL_INVALID,
        "unsupported": FinalRecoveryState.FINAL_UNSUPPORTED,
        "unsafe": FinalRecoveryState.FINAL_UNSAFE,
    }[state]
    assert observed.final.classification is expected


def test_step5e2_final_observation_does_not_read_lock_when_final_absent(tmp_path):
    request = _request(tmp_path, known_writer_token="writer-1")
    lock_path = derive_lock_path(request.cache_root.resolved_path, request.namespace, request.cache_key)
    class NoLockFilesystem(LocalRecoveryReadOnlyFilesystem):
        def inspect(self, path):
            if Path(path) == lock_path:
                raise AssertionError("final-only observation inspected lock")
            return super().inspect(path)
    from engine.storage.cache_recovery import _observe_final_component
    final = _observe_final_component(request, filesystem=NoLockFilesystem())
    assert final.classification is FinalRecoveryState.FINAL_ABSENT


def test_step5e2_final_unstable_uses_step5b_snapshot_semantics(tmp_path):
    request = _request(tmp_path, known_writer_token="writer-1")
    staged = _write_staging(request)
    final_path = derive_final_entry_path(
        request.cache_root.resolved_path, request.namespace, request.cache_key
    )
    final_path.parent.mkdir(parents=True); os.rename(staged.staging_path, final_path)
    request = _refresh_root(request)
    class UnstableFinalFilesystem(LocalRecoveryReadOnlyFilesystem):
        def read_regular_file_bounded(self, path, *, max_bytes):
            read = super().read_regular_file_bounded(path, max_bytes=max_bytes)
            if Path(path).parent == final_path and Path(path).name == "COMPLETE":
                return replace(read, stable_read=False)
            return read
    observed = _observe_recovery_components(
        request, filesystem=UnstableFinalFilesystem(), lock_clock=_FixedClock()
    )
    assert observed.final.classification is FinalRecoveryState.FINAL_UNSTABLE


def test_step5e2_staging_io_failure_is_not_invalid_or_incomplete(tmp_path):
    request = _request(tmp_path, known_writer_token="writer-1")
    staged = _write_staging(request); request = _refresh_root(request)
    class FailingStagingFilesystem(LocalRecoveryReadOnlyFilesystem):
        def inspect(self, path):
            if Path(path) == staged.staging_path:
                raise CacheLookupIOError("injected")
            return super().inspect(path)
    observed = _observe_recovery_components(
        request, filesystem=FailingStagingFilesystem(), lock_clock=_FixedClock()
    )
    assert observed.staging[0].classification is StagingRecoveryState.STAGING_IO_FAILURE


@pytest.mark.parametrize("age,state", [(60, LockRecoveryState.LOCK_ACTIVE), (61, LockRecoveryState.LOCK_STALE)])
def test_step5e2_lock_active_stale_exact_boundary(tmp_path, age, state):
    request = _request(tmp_path, known_writer_token="writer-1")
    _write_lock(request)
    request = _refresh_root(request)
    clock = _FixedClock(
        datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc)
        + timedelta(seconds=age)
    )
    assert _observe_recovery_components(
        request, lock_clock=clock
    ).lock.classification is state


@pytest.mark.parametrize("kind,expected", [
    ("malformed", LockRecoveryState.LOCK_MALFORMED),
    ("unsupported", LockRecoveryState.LOCK_UNSUPPORTED),
    ("unsafe", LockRecoveryState.LOCK_UNSAFE),
    ("identity", LockRecoveryState.LOCK_IDENTITY_CONFLICT),
    ("timestamp", LockRecoveryState.LOCK_TIMESTAMP_INVALID),
])
def test_step5e2_lock_rejection_mapping(tmp_path, kind, expected):
    request = _request(tmp_path, known_writer_token="writer-1")
    if kind == "unsupported":
        lock = _write_lock(request, version=2)
    elif kind == "identity":
        lock = _write_lock(request, digest="b" * 64)
    elif kind == "timestamp":
        lock = _write_lock(request, heartbeat="2026-07-20T09:02:00Z")
    else:
        lock = _write_lock(request)
        lock.unlink()
        if kind == "malformed":
            lock.write_bytes(b"{")
        else:
            lock.symlink_to(tmp_path / "outside")
    request = _refresh_root(request)
    assert _observe_recovery_components(
        request, lock_clock=_FixedClock()
    ).lock.classification is expected


def test_step5e2_lock_io_and_instability_mapping(tmp_path):
    request = _request(tmp_path, known_writer_token="writer-1")
    lock = _write_lock(request); request = _refresh_root(request)
    class FailingLockFilesystem(LocalRecoveryReadOnlyFilesystem):
        unstable = False
        def read_regular_file_bounded(self, path, *, max_bytes):
            if Path(path) == lock:
                if self.unstable:
                    lock.unlink(); lock.write_bytes(b"replacement")
                    return super().read_regular_file_bounded(lock, max_bytes=max_bytes)
                raise CacheLookupIOError("injected")
            return super().read_regular_file_bounded(path, max_bytes=max_bytes)
    filesystem = FailingLockFilesystem()
    assert _observe_recovery_components(
        request, filesystem=filesystem, lock_clock=_FixedClock()
    ).lock.classification is LockRecoveryState.LOCK_IO_FAILURE
    filesystem.unstable = True
    # Replacement with malformed bytes is a deterministic malformed observation;
    # inject stable-read failure directly for the instability classification.
    from engine.storage.cache_lookup import BoundedFileRead
    original = LocalRecoveryReadOnlyFilesystem().read_regular_file_bounded(lock, max_bytes=16 * 1024)
    filesystem.read_regular_file_bounded = lambda path, max_bytes: replace(original, stable_read=False)
    assert _observe_recovery_components(
        request, filesystem=filesystem, lock_clock=_FixedClock()
    ).lock.classification is LockRecoveryState.LOCK_UNSTABLE


def test_step5e3_component_pipeline_composes_and_remains_read_only(tmp_path):
    request = _request(tmp_path, known_writer_token="writer-1")
    observed = _observe_recovery_components(request, lock_clock=_FixedClock())
    assert observed.status is CacheRecoveryStatus.EMPTY
    assert observed.reason is None
    public = {name for name in dir(RecoveryReadOnlyFilesystem) if not name.startswith("_")}
    assert not public & {"write", "create", "mkdir", "fsync", "rename", "replace", "unlink", "chmod", "promote", "cleanup"}


def _staging_observation(state, index=0, reason=None):
    return StagingRecoveryObservation(index, f"staging/candidate-{index}", state, reason)


def _final_observation(state, reason=None):
    return FinalRecoveryObservation("entries/final", state, reason)


def _lock_observation(state, reason=None):
    return LockRecoveryObservation("locks/entry.lock", state, reason)


@pytest.mark.parametrize(
    ("staging_state", "final_state", "lock_state", "expected"),
    [
        (StagingRecoveryState.STAGING_ABSENT, FinalRecoveryState.FINAL_ABSENT, LockRecoveryState.LOCK_ABSENT, CacheRecoveryStatus.EMPTY),
        (StagingRecoveryState.STAGING_COMPLETE_VALID, FinalRecoveryState.FINAL_ABSENT, LockRecoveryState.LOCK_ABSENT, CacheRecoveryStatus.UNPUBLISHED_COMPLETE_STAGING),
        (StagingRecoveryState.STAGING_INCOMPLETE, FinalRecoveryState.FINAL_ABSENT, LockRecoveryState.LOCK_ABSENT, CacheRecoveryStatus.INCOMPLETE_STAGING),
        (StagingRecoveryState.STAGING_COMPLETE_VALID, FinalRecoveryState.FINAL_ABSENT, LockRecoveryState.LOCK_ACTIVE, CacheRecoveryStatus.COMPLETE_STAGING_WITH_ACTIVE_LOCK),
        (StagingRecoveryState.STAGING_COMPLETE_VALID, FinalRecoveryState.FINAL_ABSENT, LockRecoveryState.LOCK_STALE, CacheRecoveryStatus.COMPLETE_STAGING_WITH_STALE_LOCK),
        (StagingRecoveryState.STAGING_INCOMPLETE, FinalRecoveryState.FINAL_ABSENT, LockRecoveryState.LOCK_ACTIVE, CacheRecoveryStatus.INCOMPLETE_STAGING_WITH_ACTIVE_LOCK),
        (StagingRecoveryState.STAGING_INCOMPLETE, FinalRecoveryState.FINAL_ABSENT, LockRecoveryState.LOCK_STALE, CacheRecoveryStatus.INCOMPLETE_STAGING_WITH_STALE_LOCK),
        (StagingRecoveryState.STAGING_ABSENT, FinalRecoveryState.FINAL_VALID, LockRecoveryState.LOCK_ABSENT, CacheRecoveryStatus.FINAL_PUBLISHED),
        (StagingRecoveryState.STAGING_ABSENT, FinalRecoveryState.FINAL_VALID, LockRecoveryState.LOCK_ACTIVE, CacheRecoveryStatus.FINAL_PUBLISHED_LOCK_RETAINED),
        (StagingRecoveryState.STAGING_COMPLETE_VALID, FinalRecoveryState.FINAL_VALID, LockRecoveryState.LOCK_ABSENT, CacheRecoveryStatus.FINAL_PUBLISHED_WITH_SUPERSEDED_STAGING),
        (StagingRecoveryState.STAGING_INCOMPLETE, FinalRecoveryState.FINAL_VALID, LockRecoveryState.LOCK_STALE, CacheRecoveryStatus.FINAL_PUBLISHED_WITH_SUPERSEDED_STAGING_AND_LOCK),
        (StagingRecoveryState.STAGING_ABSENT, FinalRecoveryState.FINAL_ABSENT, LockRecoveryState.LOCK_ACTIVE, CacheRecoveryStatus.ACTIVE_LOCK_WITHOUT_ENTRY),
        (StagingRecoveryState.STAGING_ABSENT, FinalRecoveryState.FINAL_ABSENT, LockRecoveryState.LOCK_STALE, CacheRecoveryStatus.STALE_LOCK_WITHOUT_ENTRY),
    ],
)
def test_step5e3_exact_lifecycle_table(staging_state, final_state, lock_state, expected):
    status, reason = _compose_recovery_observation(
        (_staging_observation(staging_state),),
        _final_observation(final_state),
        _lock_observation(lock_state),
    )
    assert status is expected
    assert reason is None


@pytest.mark.parametrize(
    ("states", "expected"),
    [
        ((StagingRecoveryState.STAGING_COMPLETE_VALID,) * 2, CacheRecoveryStatus.UNPUBLISHED_COMPLETE_STAGING),
        ((StagingRecoveryState.STAGING_INCOMPLETE,) * 2, CacheRecoveryStatus.INCOMPLETE_STAGING),
        ((StagingRecoveryState.STAGING_COMPLETE_VALID, StagingRecoveryState.STAGING_INCOMPLETE), CacheRecoveryStatus.INCOMPLETE_STAGING),
    ],
)
def test_step5e3_staging_aggregation(states, expected):
    staging = tuple(_staging_observation(state, index) for index, state in enumerate(states))
    assert _compose_recovery_observation(
        staging,
        _final_observation(FinalRecoveryState.FINAL_ABSENT),
        _lock_observation(LockRecoveryState.LOCK_ABSENT),
    ) == (expected, None)


@pytest.mark.parametrize("lock_state", [LockRecoveryState.LOCK_ACTIVE, LockRecoveryState.LOCK_STALE])
def test_step5e3_retained_lock_is_narrow_to_published_final_without_staging(lock_state):
    assert _compose_recovery_observation(
        (_staging_observation(StagingRecoveryState.STAGING_ABSENT),),
        _final_observation(FinalRecoveryState.FINAL_VALID),
        _lock_observation(lock_state),
    ) == (CacheRecoveryStatus.FINAL_PUBLISHED_LOCK_RETAINED, None)
    assert _compose_recovery_observation(
        (_staging_observation(StagingRecoveryState.STAGING_COMPLETE_VALID),),
        _final_observation(FinalRecoveryState.FINAL_VALID),
        _lock_observation(lock_state),
    ) == (CacheRecoveryStatus.FINAL_PUBLISHED_WITH_SUPERSEDED_STAGING_AND_LOCK, None)


@pytest.mark.parametrize(
    ("staging", "final", "lock", "expected_status", "expected_reason"),
    [
        (StagingRecoveryState.STAGING_UNSAFE, FinalRecoveryState.FINAL_UNSUPPORTED, LockRecoveryState.LOCK_UNSTABLE, CacheRecoveryStatus.RECOVERY_UNSAFE, CacheRecoveryReason.UNSAFE_STAGING_PATH),
        (StagingRecoveryState.STAGING_UNSTABLE, FinalRecoveryState.FINAL_INVALID, LockRecoveryState.LOCK_UNSUPPORTED, CacheRecoveryStatus.RECOVERY_UNSUPPORTED, CacheLookupReason.UNSUPPORTED_LOCK_VERSION),
        (StagingRecoveryState.STAGING_INVALID, FinalRecoveryState.FINAL_INVALID, LockRecoveryState.LOCK_UNSTABLE, CacheRecoveryStatus.RECOVERY_UNSTABLE, CacheLookupReason.UNSTABLE_SNAPSHOT),
        (StagingRecoveryState.STAGING_INVALID, FinalRecoveryState.FINAL_ABSENT, LockRecoveryState.LOCK_ABSENT, CacheRecoveryStatus.RECOVERY_INVALID, CacheRecoveryReason.INVALID_STAGING),
    ],
)
def test_step5e3_failure_family_precedence(staging, final, lock, expected_status, expected_reason):
    reasons = {
        StagingRecoveryState.STAGING_UNSAFE: CacheRecoveryReason.UNSAFE_STAGING_PATH,
        StagingRecoveryState.STAGING_UNSTABLE: CacheRecoveryReason.UNSTABLE_STAGING,
        StagingRecoveryState.STAGING_INVALID: CacheRecoveryReason.INVALID_STAGING,
        FinalRecoveryState.FINAL_UNSUPPORTED: CacheLookupReason.UNSUPPORTED_ENTRY_VERSION,
        FinalRecoveryState.FINAL_INVALID: CacheLookupReason.MALFORMED_METADATA,
        LockRecoveryState.LOCK_UNSUPPORTED: CacheLookupReason.UNSUPPORTED_LOCK_VERSION,
        LockRecoveryState.LOCK_UNSTABLE: CacheLookupReason.UNSTABLE_SNAPSHOT,
    }
    status, reason = _compose_recovery_observation(
        (_staging_observation(staging, reason=reasons.get(staging)),),
        _final_observation(final, reasons.get(final)),
        _lock_observation(lock, reasons.get(lock)),
    )
    assert (status, reason) == (expected_status, expected_reason)
    assert reason is not None


def test_step5e3_failure_subject_and_staging_ordinal_ties_are_deterministic():
    status, reason = _compose_recovery_observation(
        (
            _staging_observation(StagingRecoveryState.STAGING_IO_FAILURE, 1, CacheRecoveryReason.STAGING_IO_FAILURE),
            _staging_observation(StagingRecoveryState.STAGING_INVALID, 0, CacheRecoveryReason.INVALID_STAGING),
        ),
        _final_observation(FinalRecoveryState.FINAL_INVALID, CacheLookupReason.MALFORMED_METADATA),
        _lock_observation(LockRecoveryState.LOCK_MALFORMED, CacheLookupReason.MALFORMED_LOCK),
    )
    assert (status, reason) == (CacheRecoveryStatus.RECOVERY_INVALID, CacheLookupReason.MALFORMED_METADATA)

    status, reason = _compose_recovery_observation(
        (
            _staging_observation(StagingRecoveryState.STAGING_IO_FAILURE, 1, CacheRecoveryReason.STAGING_IO_FAILURE),
            _staging_observation(StagingRecoveryState.STAGING_INVALID, 0, CacheRecoveryReason.INVALID_STAGING),
        ),
        _final_observation(FinalRecoveryState.FINAL_ABSENT),
        _lock_observation(LockRecoveryState.LOCK_MALFORMED, CacheLookupReason.MALFORMED_LOCK),
    )
    assert (status, reason) == (CacheRecoveryStatus.RECOVERY_INVALID, CacheRecoveryReason.INVALID_STAGING)


def test_step5e3_composition_has_no_external_observation_or_mutation_dependencies():
    forbidden = {
        "filesystem", "clock", "hash", "lookup_cache_entry", "open", "unlink",
        "rename", "replace", "write", "mkdir", "promote", "cleanup",
    }
    assert forbidden.isdisjoint(_compose_recovery_observation.__code__.co_names)


@pytest.mark.parametrize(
    ("staging_kind", "final_present", "lock_kind", "expected"),
    [
        ("absent", False, "absent", CacheRecoveryStatus.EMPTY),
        ("complete", False, "absent", CacheRecoveryStatus.UNPUBLISHED_COMPLETE_STAGING),
        ("incomplete", False, "absent", CacheRecoveryStatus.INCOMPLETE_STAGING),
        ("complete", False, "active", CacheRecoveryStatus.COMPLETE_STAGING_WITH_ACTIVE_LOCK),
        ("complete", False, "stale", CacheRecoveryStatus.COMPLETE_STAGING_WITH_STALE_LOCK),
        ("incomplete", False, "active", CacheRecoveryStatus.INCOMPLETE_STAGING_WITH_ACTIVE_LOCK),
        ("incomplete", False, "stale", CacheRecoveryStatus.INCOMPLETE_STAGING_WITH_STALE_LOCK),
        ("absent", True, "absent", CacheRecoveryStatus.FINAL_PUBLISHED),
        ("absent", True, "active", CacheRecoveryStatus.FINAL_PUBLISHED_LOCK_RETAINED),
        ("complete", True, "absent", CacheRecoveryStatus.FINAL_PUBLISHED_WITH_SUPERSEDED_STAGING),
        ("incomplete", True, "stale", CacheRecoveryStatus.FINAL_PUBLISHED_WITH_SUPERSEDED_STAGING_AND_LOCK),
        ("absent", False, "active", CacheRecoveryStatus.ACTIVE_LOCK_WITHOUT_ENTRY),
        ("absent", False, "stale", CacheRecoveryStatus.STALE_LOCK_WITHOUT_ENTRY),
    ],
)
def test_step5e_final_public_api_covers_every_lifecycle_row(
    tmp_path, staging_kind, final_present, lock_kind, expected
):
    request = _request(tmp_path, known_writer_token="writer-1")
    if final_present:
        staged = _write_staging(request)
        final = derive_final_entry_path(
            request.cache_root.resolved_path, request.namespace, request.cache_key
        )
        final.parent.mkdir(parents=True)
        os.rename(staged.staging_path, final)
    if staging_kind != "absent":
        staged = _write_staging(_refresh_root(request))
        if staging_kind == "incomplete":
            (staged.staging_path / "COMPLETE").unlink()
    if lock_kind != "absent":
        _write_lock(request)
    request = _refresh_root(request)
    clock = _FixedClock(
        datetime(2026, 7, 20, 9, 1, tzinfo=timezone.utc)
        + (timedelta(seconds=1) if lock_kind == "stale" else timedelta())
    )
    observation = inspect_cache_recovery_state(request, lock_clock=clock)
    assert observation.status is expected
    assert observation.reason is None
    assert observation.diagnostics


@pytest.mark.parametrize(
    ("subject", "kind", "expected_status"),
    [
        ("final", "unsafe", CacheRecoveryStatus.RECOVERY_UNSAFE),
        ("staging", "unsafe", CacheRecoveryStatus.RECOVERY_UNSAFE),
        ("lock", "unsafe", CacheRecoveryStatus.RECOVERY_UNSAFE),
        ("final", "unsupported", CacheRecoveryStatus.RECOVERY_UNSUPPORTED),
        ("staging", "unsupported", CacheRecoveryStatus.RECOVERY_UNSUPPORTED),
        ("lock", "unsupported", CacheRecoveryStatus.RECOVERY_UNSUPPORTED),
        ("final", "invalid", CacheRecoveryStatus.RECOVERY_INVALID),
        ("staging", "invalid", CacheRecoveryStatus.RECOVERY_INVALID),
        ("lock", "invalid", CacheRecoveryStatus.RECOVERY_INVALID),
    ],
)
def test_step5e_final_public_api_structures_component_failures(
    tmp_path, subject, kind, expected_status
):
    request = _request(tmp_path, known_writer_token="writer-1")
    if subject in {"final", "staging"}:
        staged = _write_staging(request)
        target = staged.staging_path
        if subject == "final":
            target = derive_final_entry_path(
                request.cache_root.resolved_path, request.namespace, request.cache_key
            )
            target.parent.mkdir(parents=True)
            os.rename(staged.staging_path, target)
        if kind == "unsafe":
            for child in sorted(target.rglob("*"), reverse=True):
                child.unlink() if child.is_file() else child.rmdir()
            target.rmdir()
            target.symlink_to(tmp_path / "outside", target_is_directory=True)
        elif kind == "unsupported":
            (target / "COMPLETE").write_bytes(canonical_json_bytes({
                "cache_entry_contract_version": 2,
                "entry_digest": derive_entry_digest(request.cache_key),
                "manifest_digest": "sha256:" + "0" * 64,
                "metadata_digest": "sha256:" + "0" * 64,
            }))
        else:
            (target / "COMPLETE").write_bytes(b"{")
    else:
        lock = _write_lock(request, version=2 if kind == "unsupported" else 1)
        if kind == "unsafe":
            lock.unlink()
            lock.symlink_to(tmp_path / "outside")
        elif kind == "invalid":
            lock.write_bytes(b"{")
    request = _refresh_root(request)
    observation = inspect_cache_recovery_state(request, lock_clock=_FixedClock())
    assert observation.status is expected_status
    assert observation.reason is not None
    assert observation.diagnostics[0].subject.value == subject


def test_step5e_final_diagnostics_are_ordered_deduplicated_bounded_and_sanitized(tmp_path):
    request = _request(tmp_path)
    staging = tuple(
        StagingRecoveryObservation(
            index,
            _staging_namespace(request).relative_to(request.cache_root.resolved_path).as_posix()
            + f"/{derive_entry_digest(request.cache_key)}.<writer-token-{index:04d}>",
            StagingRecoveryState.STAGING_INVALID,
            CacheRecoveryReason.INVALID_STAGING,
        )
        for index in range(40)
    )
    observation = CacheRecoveryObservation(
        derive_entry_digest(request.cache_key),
        staging,
        FinalRecoveryObservation("entries/final", FinalRecoveryState.FINAL_VALID),
        LockRecoveryObservation("locks/entry.lock", LockRecoveryState.LOCK_ABSENT),
        CacheRecoveryStatus.RECOVERY_INVALID,
        CacheRecoveryReason.INVALID_STAGING,
    )
    diagnostics = _recovery_diagnostics(observation, limit=32)
    assert len(diagnostics) == 32
    assert diagnostics[-1] == CacheRecoveryDiagnostic(
        "DIAGNOSTICS_TRUNCATED", RecoverySubject.ROOT
    )
    assert len(set(diagnostics)) == len(diagnostics)
    serialized = repr(diagnostics)
    assert str(tmp_path) not in serialized
    assert "owner-token" not in serialized and "host-1" not in serialized
    assert diagnostics == _recovery_diagnostics(observation, limit=32)


def test_step5e_final_public_api_converts_resource_limit_and_root_instability(tmp_path):
    request = _request(tmp_path)
    directory = _staging_namespace(request)
    directory.mkdir(parents=True)

    class LimitFilesystem(LocalRecoveryReadOnlyFilesystem):
        def list_directory_bounded(self, path, *, max_entries):
            listing = super().list_directory_bounded(path, max_entries=max_entries)
            return replace(listing, names=None, limit_exceeded=True)

    limited = inspect_cache_recovery_state(
        _refresh_root(request), filesystem=LimitFilesystem(), lock_clock=_FixedClock()
    )
    assert limited.status is CacheRecoveryStatus.RECOVERY_INVALID
    assert limited.reason is CacheRecoveryReason.TRAVERSAL_LIMIT_EXCEEDED

    class RootReplacementFilesystem(LocalRecoveryReadOnlyFilesystem):
        root_observations = 0

        def inspect(self, path):
            identity = super().inspect(path)
            if Path(path) == request.cache_root.resolved_path:
                self.root_observations += 1
                if self.root_observations >= 3:
                    return replace(identity, device_id=identity.device_id + 1)
            return identity

    unstable = inspect_cache_recovery_state(
        _refresh_root(request),
        filesystem=RootReplacementFilesystem(),
        lock_clock=_FixedClock(),
    )
    assert unstable.status is CacheRecoveryStatus.RECOVERY_UNSTABLE
    assert unstable.reason in {
        CacheRecoveryReason.UNSTABLE_ROOT,
        CacheLookupReason.UNSTABLE_SNAPSHOT,
    }
    assert any(
        diagnostic.code == CacheRecoveryReason.UNSTABLE_ROOT.value
        for diagnostic in unstable.diagnostics
    )


def test_step5e_final_package_exports_and_read_only_surface():
    import engine.storage as storage

    for name in {
        "CacheRecoveryDiagnostic",
        "CacheRecoveryInspectionRequest",
        "CacheRecoveryObservation",
        "CacheRecoveryReason",
        "CacheRecoveryStatus",
        "RecoveryInspectionPolicy",
        "RecoveryReadOnlyFilesystem",
        "RecoverySubject",
        "inspect_cache_recovery_state",
    }:
        assert name in storage.__all__
        assert getattr(storage, name) is not None
    forbidden = {
        "write", "create", "mkdir", "fsync", "rename", "replace", "unlink",
        "chmod", "acquire", "refresh", "release", "promote", "cleanup", "delete",
        "repair",
    }
    assert forbidden.isdisjoint(dir(RecoveryReadOnlyFilesystem))
    model_fields = {field.name for field in fields(CacheRecoveryObservation)}
    assert model_fields.isdisjoint({
        "safe_to_delete", "delete_candidate", "cleanup_priority", "retry_promotion",
        "break_lock", "eviction_priority", "retention_age", "reclaimable_bytes",
        "recommended_action",
    })
