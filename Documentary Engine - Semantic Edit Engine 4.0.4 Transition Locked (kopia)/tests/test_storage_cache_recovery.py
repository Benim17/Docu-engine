from dataclasses import fields, replace
from pathlib import Path

import pytest

from engine.storage.cache_keys import CacheKey
from engine.storage.cache_lookup import (
    CacheLookupExpectation,
    CacheLookupVerificationPolicy,
    LockObservationPolicy,
    ProducerPayloadExpectation,
    ValidatedCacheRoot,
)
from engine.storage.cache_recovery import (
    CacheRecoveryInspectionRequest,
    CacheRecoveryObservation,
    LocalRecoveryReadOnlyFilesystem,
    RecoveryInspectionPolicy,
    RecoveryReadOnlyFilesystem,
    RecoveryTraversalError,
    RecoveryTraversalLimitError,
    _prepare_recovery_inspection,
)
from engine.storage.persistent_cache import (
    CacheNamespace,
    CacheRuntimeFingerprint,
    derive_entry_digest,
    derive_final_entry_path,
    derive_lock_path,
    derive_staging_entry_path,
)


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
        "resolve",
        "read_regular_file_bounded",
        "stream_regular_file_sha256",
        "list_directory_bounded",
    }
    forbidden = {"write", "unlink", "rename", "mkdir", "chmod", "promote", "cleanup"}
    assert not public & forbidden
