import hashlib
import os
import threading
import time
from pathlib import Path
from dataclasses import FrozenInstanceError, replace

import pytest

from engine.storage.cache_catalog import (
    CACHE_CATALOG_LAYOUT_VERSION,
    CACHE_CATALOG_RECORD_VERSION,
    MAX_CATALOG_DIRECTORY_ENTRIES,
    MAX_CATALOG_OPERATION_DIAGNOSTICS,
    MAX_CATALOG_PAGE_RECORDS,
    MAX_CATALOG_RECORD_BYTES,
    MAX_CATALOG_RECORD_REVISION,
    MAX_CATALOG_RELATIVE_PATH_UTF8_BYTES,
    MAX_CATALOG_TRAVERSAL_DEPTH,
    CacheCatalogContractError,
    CacheCatalogFinalProvenance,
    CacheCatalogFinalState,
    CacheCatalogFinalSummary,
    CacheCatalogIdentity,
    CacheCatalogCursor,
    CacheCatalogCursorScope,
    CacheCatalogDirectoryListing,
    CacheCatalogLookupResult,
    CacheCatalogLookupStatus,
    CacheCatalogBackend,
    CacheCatalogLiveRecord,
    CacheCatalogLockState,
    CacheCatalogPage,
    CacheCatalogReadOnlyBackend,
    CacheCatalogRecord,
    CacheCatalogRecordState,
    CacheCatalogRecoveryProvenance,
    CacheCatalogRecoverySummary,
    CacheCatalogTombstone,
    CacheCatalogUnsupportedVersionError,
    CacheCatalogVerificationLevel,
    CacheCatalogWriteRequest,
    CacheCatalogWriteStatus,
    LocalCacheCatalogBackend,
    LocalCacheCatalogReadOnlyBackend,
    catalog_identity_sort_key,
    derive_catalog_record_relative_path,
    enumerate_catalog_namespace,
    iterate_catalog_records,
    lookup_catalog_record,
    parse_cache_catalog_record,
    serialize_cache_catalog_record,
    tombstone_catalog_empty,
    upsert_catalog_from_lookup,
    upsert_catalog_from_promotion,
    upsert_catalog_from_recovery,
    _write_catalog_record,
)
from engine.storage.cache_keys import CacheKey
from engine.storage.cache_lookup import (
    CacheArtifactExpectation,
    CacheLookupPermissionError,
    CacheLookupReason,
    CacheLookupRequest,
    CacheLookupStatus,
    CacheLookupVerificationPolicy,
    CacheVerificationLevel,
    LockObservationPolicy,
    ProducerPayloadExpectation,
    ReadOnlyCacheLookupResult,
    ValidatedCacheEntryReference,
    ValidatedCacheRoot,
)
from engine.storage.cache_recovery import (
    CacheRecoveryInspectionRequest,
    CacheRecoveryObservation,
    CacheRecoveryReason,
    CacheRecoveryStatus,
    FinalRecoveryState,
    LockRecoveryState,
    FinalRecoveryObservation,
    LockRecoveryObservation,
    RecoveryInspectionPolicy,
    StagingRecoveryObservation,
    StagingRecoveryState,
)
from engine.storage.cache_promotion import (
    CachePromotionResult,
    CachePromotionStatus,
    PromotedCacheEntryReference,
)
from engine.storage.persistent_cache import (
    CACHE_ENTRY_CONTRACT_VERSION,
    CacheKeyReference,
    CacheLookupExpectation,
    CacheEntryMetadata,
    CacheArtifactMetadata,
    CacheProducerMetadata,
    CacheRuntimeFingerprint,
    CompletenessMarker,
    PayloadManifest,
    PayloadManifestRecord,
    CacheNamespace,
    canonical_json_bytes,
    derive_entry_digest,
)


def identity(*, domain="audio", producer_id="transcription.whisper", schema=3, key_text=None):
    key = CacheKey(key_text or "a" * 64)
    return CacheCatalogIdentity(
        CacheNamespace(domain, producer_id, schema),
        derive_entry_digest(key),
        CacheKeyReference.from_cache_key(key),
    )


def final_summary(**changes):
    values = {
        "provenance": CacheCatalogFinalProvenance.STEP5B_HIT,
        "cache_entry_contract_version": CACHE_ENTRY_CONTRACT_VERSION,
        "producer_id": "transcription.whisper",
        "producer_version": "4.2.0",
        "producer_schema_version": 3,
        "artifact_kind": "transcript",
        "artifact_contract_version": 1,
        "runtime_fingerprint_digest": "sha256:" + "b" * 64,
        "created_at_utc": "2026-07-20T09:00:00Z",
        "payload_manifest_digest": "sha256:" + "c" * 64,
        "payload_file_count": 2,
        "payload_total_bytes": 123,
        "verification_level": CacheCatalogVerificationLevel.FULL_PAYLOAD_SHA256,
    }
    values.update(changes)
    return CacheCatalogFinalSummary(**values)


def recovery_summary(**changes):
    values = {
        "status": CacheRecoveryStatus.FINAL_PUBLISHED,
        "reason": None,
        "staging_candidate_count": 0,
        "final_state": FinalRecoveryState.FINAL_VALID,
        "lock_state": LockRecoveryState.LOCK_ABSENT,
        "provenance": CacheCatalogRecoveryProvenance.STEP5E_OBSERVATION,
    }
    values.update(changes)
    return CacheCatalogRecoverySummary(**values)


def live_record(**changes):
    values = {
        "identity": identity(),
        "record_revision": 1,
        "last_validated_final": final_summary(),
        "last_recovery_observation": None,
    }
    values.update(changes)
    return CacheCatalogLiveRecord(**values)


def tombstone(**changes):
    values = {"identity": identity(), "record_revision": 2}
    values.update(changes)
    return CacheCatalogTombstone(**values)


def test_h1a_locked_versions_and_limits_are_exact():
    assert (
        CACHE_CATALOG_LAYOUT_VERSION,
        CACHE_CATALOG_RECORD_VERSION,
        MAX_CATALOG_RECORD_BYTES,
        MAX_CATALOG_PAGE_RECORDS,
        MAX_CATALOG_DIRECTORY_ENTRIES,
        MAX_CATALOG_RELATIVE_PATH_UTF8_BYTES,
        MAX_CATALOG_TRAVERSAL_DEPTH,
        MAX_CATALOG_OPERATION_DIAGNOSTICS,
        MAX_CATALOG_RECORD_REVISION,
    ) == (1, 1, 65_536, 256, 4_096, 1_024, 64, 32, 9_223_372_036_854_775_807)


def test_h1a_identity_is_reconstructable_hashable_and_deterministically_ordered():
    first = identity(domain="audio")
    same = CacheCatalogIdentity.from_values(
        first.namespace.to_dict(),
        first.entry_digest,
        first.cache_key_reference.to_dict(),
    )
    later = identity(domain="video")
    assert first == same and hash(first) == hash(same)
    assert first < later
    assert catalog_identity_sort_key(first) == (
        "audio",
        "transcription.whisper",
        3,
        first.entry_digest,
    )
    assert sorted((later, first)) == [first, later]


@pytest.mark.parametrize("digest", ["a" * 63, "A" * 64, "g" * 64, 1, None])
def test_h1a_identity_rejects_invalid_entry_digest(digest):
    valid = identity()
    with pytest.raises(CacheCatalogContractError, match="entry_digest"):
        CacheCatalogIdentity(valid.namespace, digest, valid.cache_key_reference)


def test_h1a_identity_rejects_digest_key_reference_conflict():
    valid = identity()
    other = CacheKeyReference.from_cache_key(CacheKey("d" * 64))
    with pytest.raises(CacheCatalogContractError, match="does not match"):
        CacheCatalogIdentity(valid.namespace, valid.entry_digest, other)


@pytest.mark.parametrize(
    "namespace_value",
    [
        None,
        {},
        {"domain": "Audio", "producer_id": "p", "producer_schema_version": 1},
        {"domain": "audio", "producer_id": "bad/name", "producer_schema_version": 1},
        {"domain": "audio", "producer_id": "p", "producer_schema_version": 0},
    ],
)
def test_h1a_identity_rejects_invalid_namespace_dictionaries(namespace_value):
    valid = identity()
    with pytest.raises(CacheCatalogContractError):
        CacheCatalogIdentity.from_values(
            namespace_value,
            valid.entry_digest,
            valid.cache_key_reference.to_dict(),
        )


@pytest.mark.parametrize(
    "reference",
    [
        None,
        {},
        {"canonical_version": 0, "canonical_value": "a" * 64},
        {"canonical_version": 1, "canonical_value": "not-a-key"},
        {"canonical_version": 1, "canonical_value": "a" * 64, "extra": 1},
    ],
)
def test_h1a_identity_rejects_invalid_cache_key_reference(reference):
    valid = identity()
    with pytest.raises(CacheCatalogContractError):
        CacheCatalogIdentity.from_values(
            valid.namespace.to_dict(), valid.entry_digest, reference
        )


def test_h1a_live_record_canonical_round_trip_is_exact_and_deterministic():
    value = live_record(last_recovery_observation=recovery_summary())
    serialized = value.canonical_bytes()
    assert serialized == serialize_cache_catalog_record(value)
    assert serialized == canonical_json_bytes(value.to_dict())
    assert parse_cache_catalog_record(serialized) == value
    assert CacheCatalogRecord.from_json(serialized) == value
    assert not serialized.endswith(b"\n")
    assert value.record_state is CacheCatalogRecordState.LIVE


def test_h1a_tombstone_canonical_round_trip_has_only_locked_fields():
    value = tombstone()
    serialized = value.canonical_bytes()
    assert parse_cache_catalog_record(serialized) == value
    assert set(value.to_dict()) == {
        "cache_key_reference",
        "catalog_record_version",
        "entry_digest",
        "namespace",
        "record_revision",
        "record_state",
    }
    assert value.record_state is CacheCatalogRecordState.TOMBSTONE


def test_h1a_live_record_allows_either_or_both_trusted_summaries():
    final_only = live_record()
    recovery_only = live_record(
        last_validated_final=None,
        last_recovery_observation=recovery_summary(),
    )
    both = live_record(last_recovery_observation=recovery_summary())
    assert final_only.last_validated_final is not None
    assert recovery_only.last_recovery_observation is not None
    assert both.last_validated_final is not None and both.last_recovery_observation is not None
    with pytest.raises(CacheCatalogContractError, match="at least one"):
        live_record(last_validated_final=None, last_recovery_observation=None)


def test_h1a_final_summary_must_match_record_namespace():
    with pytest.raises(CacheCatalogContractError, match="producer identity"):
        live_record(last_validated_final=final_summary(producer_id="other"))
    with pytest.raises(CacheCatalogContractError, match="producer identity"):
        live_record(last_validated_final=final_summary(producer_schema_version=4))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provenance", "step5b_hit"),
        ("cache_entry_contract_version", 2),
        ("producer_id", "Bad"),
        ("producer_version", "/absolute"),
        ("producer_schema_version", 0),
        ("artifact_kind", ""),
        ("artifact_contract_version", True),
        ("runtime_fingerprint_digest", "b" * 64),
        ("runtime_fingerprint_digest", "sha256:" + "B" * 64),
        ("created_at_utc", "2026-02-30T00:00:00Z"),
        ("payload_manifest_digest", "sha256:" + "x" * 64),
        ("payload_file_count", -1),
        ("payload_total_bytes", True),
        ("verification_level", "full_payload_sha256"),
    ],
)
def test_h1a_final_summary_constructor_rejects_invalid_fields(field, value):
    with pytest.raises(CacheCatalogContractError):
        final_summary(**{field: value})


def test_h1a_final_summary_parser_accepts_only_exact_supported_enums():
    value = final_summary()
    parsed = CacheCatalogFinalSummary.from_dict(value.to_dict())
    assert parsed == value
    payload = value.to_dict()
    payload["provenance"] = "future"
    with pytest.raises(CacheCatalogContractError, match="Unsupported"):
        CacheCatalogFinalSummary.from_dict(payload)


@pytest.mark.parametrize(
    "reason",
    [CacheRecoveryReason.INVALID_FINAL, CacheLookupReason.MALFORMED_METADATA],
)
def test_h1a_recovery_failure_summary_preserves_supported_reason_domains(reason):
    value = recovery_summary(
        status=CacheRecoveryStatus.RECOVERY_INVALID,
        reason=reason,
        final_state=FinalRecoveryState.FINAL_INVALID,
    )
    assert CacheCatalogRecoverySummary.from_dict(value.to_dict()) == value


def test_h1a_recovery_status_reason_rules_are_strict():
    with pytest.raises(CacheCatalogContractError, match="requires a reason"):
        recovery_summary(status=CacheRecoveryStatus.RECOVERY_INVALID, reason=None)
    with pytest.raises(CacheCatalogContractError, match="requires null"):
        recovery_summary(reason=CacheRecoveryReason.INVALID_FINAL)
    with pytest.raises(CacheCatalogContractError, match="maximum"):
        recovery_summary(staging_candidate_count=65)
    with pytest.raises(CacheCatalogContractError):
        recovery_summary(staging_candidate_count=True)


def test_h1a_recovery_summary_parser_rejects_unknown_states_and_provenance():
    payload = recovery_summary().to_dict()
    for field, bad in (
        ("status", "future"),
        ("final_state", "future"),
        ("lock_state", "future"),
        ("provenance", "future"),
    ):
        changed = dict(payload)
        changed[field] = bad
        with pytest.raises(CacheCatalogContractError):
            CacheCatalogRecoverySummary.from_dict(changed)


@pytest.mark.parametrize(
    "serialized",
    [
        b"{",
        b"null",
        b' {"record_state":"live"}',
        b'{"record_state":"live"}\n',
        b'{"record_state":"live","record_state":"tombstone"}',
        b'\xef\xbb\xbf{"record_state":"live"}',
        b'{"record_state":"live","value":1.5}',
        b'\xff',
    ],
)
def test_h1a_parser_rejects_malformed_duplicate_and_noncanonical_json(serialized):
    with pytest.raises(CacheCatalogContractError):
        parse_cache_catalog_record(serialized)


def test_h1a_unknown_missing_and_forbidden_record_fields_are_rejected():
    live_payload = live_record().to_dict()
    live_payload["last_use_utc"] = "2026-07-20T09:00:00Z"
    with pytest.raises(CacheCatalogContractError, match="Unknown"):
        CacheCatalogRecord.from_dict(live_payload)

    missing = live_record().to_dict()
    del missing["namespace"]
    with pytest.raises(CacheCatalogContractError, match="Missing"):
        CacheCatalogRecord.from_dict(missing)

    tombstone_payload = tombstone().to_dict()
    tombstone_payload["last_validated_final"] = final_summary().to_dict()
    with pytest.raises(CacheCatalogContractError, match="Unknown"):
        CacheCatalogRecord.from_dict(tombstone_payload)


@pytest.mark.parametrize("version", [0, -1, True, "1", None])
def test_h1a_invalid_record_versions_are_malformed(version):
    payload = live_record().to_dict()
    payload["catalog_record_version"] = version
    with pytest.raises(CacheCatalogContractError) as observed:
        CacheCatalogRecord.from_dict(payload)
    assert not isinstance(observed.value, CacheCatalogUnsupportedVersionError)


@pytest.mark.parametrize("version", [2, 99])
def test_h1a_positive_future_record_versions_are_distinctly_unsupported(version):
    payload = live_record().to_dict()
    payload["catalog_record_version"] = version
    payload["future_field"] = "allowed-by-future-schema"
    with pytest.raises(CacheCatalogUnsupportedVersionError):
        CacheCatalogRecord.from_dict(payload)


@pytest.mark.parametrize("revision", [0, -1, True, "1", MAX_CATALOG_RECORD_REVISION + 1])
def test_h1a_record_revision_bounds_are_strict(revision):
    with pytest.raises(CacheCatalogContractError, match="record_revision"):
        live_record(record_revision=revision)
    assert live_record(record_revision=MAX_CATALOG_RECORD_REVISION).record_revision == MAX_CATALOG_RECORD_REVISION


def test_h1a_record_size_limit_is_enforced_at_exact_byte_boundary():
    base = live_record()
    base_size = len(base.canonical_bytes())
    current_length = len(base.last_validated_final.producer_version)
    exact_length = current_length + (MAX_CATALOG_RECORD_BYTES - base_size)
    exact = live_record(
        last_validated_final=final_summary(producer_version="v" * exact_length)
    )
    assert len(exact.canonical_bytes()) == MAX_CATALOG_RECORD_BYTES
    oversized = live_record(
        last_validated_final=final_summary(producer_version="v" * (exact_length + 1))
    )
    with pytest.raises(CacheCatalogContractError, match="exceeds"):
        oversized.canonical_bytes()
    with pytest.raises(CacheCatalogContractError, match="exceeds"):
        parse_cache_catalog_record(exact.canonical_bytes() + b" ")


def test_h1a_models_are_frozen_and_equality_hash_use_all_immutable_fields():
    value = live_record()
    same = parse_cache_catalog_record(value.canonical_bytes())
    changed = replace(value, record_revision=2)
    assert value == same and hash(value) == hash(same)
    assert value != changed and hash(value) != hash(changed)
    with pytest.raises(FrozenInstanceError):
        value.record_revision = 3
    with pytest.raises(FrozenInstanceError):
        value.identity.entry_digest = "f" * 64


def test_h1a_base_record_and_wrong_serializer_types_are_rejected():
    with pytest.raises(CacheCatalogContractError):
        CacheCatalogRecord(identity=identity(), record_revision=1)
    with pytest.raises(CacheCatalogContractError):
        serialize_cache_catalog_record(object())
    with pytest.raises(CacheCatalogContractError):
        catalog_identity_sort_key(object())


def test_h1b_module_has_only_the_approved_read_only_backend_surface():
    import engine.storage.cache_catalog as catalog

    public = {name for name in dir(catalog) if not name.startswith("_")}
    forbidden = {
        "Path",
        "open",
        "write_lock",
        "publish",
        "replace",
        "unlink",
        "mkdir",
        "fsync",
    }
    assert forbidden.isdisjoint(public)
    assert {
        "lookup_catalog_record",
        "enumerate_catalog_namespace",
        "iterate_catalog_records",
        "CacheCatalogReadOnlyBackend",
    } <= public


def _initialized_backend(tmp_path, record=None):
    relative = derive_catalog_record_relative_path(identity())
    target = tmp_path / relative
    (tmp_path / "catalog" / "v1" / "records").mkdir(parents=True)
    if record is not None:
        target.parent.mkdir(parents=True)
        target.write_bytes(
            record if isinstance(record, bytes) else serialize_cache_catalog_record(record)
        )
    backend = LocalCacheCatalogReadOnlyBackend.from_root(tmp_path)
    return backend, target, relative


def test_h1b_exact_path_derivation_is_locked_and_contained(tmp_path):
    value = identity()
    relative = derive_catalog_record_relative_path(value)
    assert relative == Path(
        "catalog/v1/records/audio/transcription.whisper/3/"
        f"{value.entry_digest[:2]}/{value.entry_digest[2:4]}/{value.entry_digest}.json"
    )
    assert not relative.is_absolute()
    assert ".." not in relative.parts
    assert len(relative.parts) == 9
    assert len(relative.as_posix().encode()) <= MAX_CATALOG_RELATIVE_PATH_UTF8_BYTES


def test_h1b_supported_live_record_is_found(tmp_path):
    expected = live_record()
    backend, _, _ = _initialized_backend(tmp_path, expected)
    result = lookup_catalog_record(expected.identity, backend=backend)
    assert result == CacheCatalogLookupResult(CacheCatalogLookupStatus.RECORD_FOUND, expected)
    assert result.record is expected or result.record == expected


def test_h1b_supported_tombstone_is_absent_with_revision(tmp_path):
    expected = tombstone(record_revision=7)
    backend, _, _ = _initialized_backend(tmp_path, expected)
    result = lookup_catalog_record(expected.identity, backend=backend)
    assert result.status is CacheCatalogLookupStatus.RECORD_ABSENT
    assert result.record is None and result.tombstone_revision == 7


def test_h1b_initialized_catalog_missing_exact_record_is_absent(tmp_path):
    backend, _, _ = _initialized_backend(tmp_path)
    result = lookup_catalog_record(identity(), backend=backend)
    assert result.status is CacheCatalogLookupStatus.RECORD_ABSENT


@pytest.mark.parametrize("initialized", [False, True])
def test_h1b_missing_catalog_or_v1_is_unavailable(tmp_path, initialized):
    if initialized:
        (tmp_path / "catalog").mkdir()
    backend = LocalCacheCatalogReadOnlyBackend.from_root(tmp_path)
    result = lookup_catalog_record(identity(), backend=backend)
    assert result.status is CacheCatalogLookupStatus.CATALOG_UNAVAILABLE


@pytest.mark.parametrize(
    "data",
    [
        b"{",
        b'{"catalog_record_version":1}',
        b'{"catalog_record_version":1,"catalog_record_version":1}',
        b'{"catalog_record_version":1, "record_state":"live"}',
    ],
)
def test_h1b_malformed_duplicate_or_noncanonical_record_is_corrupt(tmp_path, data):
    backend, _, _ = _initialized_backend(tmp_path, data)
    assert lookup_catalog_record(identity(), backend=backend).status is CacheCatalogLookupStatus.CATALOG_CORRUPT


def test_h1b_future_record_version_is_unsupported(tmp_path):
    data = live_record().canonical_bytes().replace(
        b'"catalog_record_version":1', b'"catalog_record_version":2'
    )
    backend, _, _ = _initialized_backend(tmp_path, data)
    assert lookup_catalog_record(identity(), backend=backend).status is CacheCatalogLookupStatus.CATALOG_UNSUPPORTED


def test_h1b_record_for_another_identity_at_exact_path_is_corrupt(tmp_path):
    expected = identity()
    other = live_record(identity=identity(key_text="d" * 64))
    backend, target, _ = _initialized_backend(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_bytes(other.canonical_bytes())
    assert lookup_catalog_record(expected, backend=backend).status is CacheCatalogLookupStatus.CATALOG_CORRUPT


@pytest.mark.parametrize("conflict", ["namespace", "entry_digest", "cache_key_reference", "unknown"])
def test_h1b_internal_identity_conflicts_and_unknown_fields_are_corrupt(tmp_path, conflict):
    data = live_record().to_dict()
    if conflict == "namespace":
        data["namespace"] = {**data["namespace"], "domain": "video"}
    elif conflict == "entry_digest":
        data["entry_digest"] = "f" * 64
    elif conflict == "cache_key_reference":
        data["cache_key_reference"] = identity(key_text="d" * 64).cache_key_reference.to_dict()
    else:
        data["unexpected"] = True
    backend, _, _ = _initialized_backend(tmp_path, canonical_json_bytes(data))
    assert lookup_catalog_record(identity(), backend=backend).status is CacheCatalogLookupStatus.CATALOG_CORRUPT


def test_h1b_oversized_record_is_corrupt(tmp_path):
    backend, _, _ = _initialized_backend(tmp_path, b"x" * (MAX_CATALOG_RECORD_BYTES + 1))
    assert lookup_catalog_record(identity(), backend=backend).status is CacheCatalogLookupStatus.CATALOG_CORRUPT


def test_h1b_exact_65536_byte_supported_record_is_accepted(tmp_path):
    base = live_record()
    initial = base.canonical_bytes()
    desired = MAX_CATALOG_RECORD_BYTES - len(initial) + len(base.last_validated_final.producer_version)
    summary = replace(base.last_validated_final, producer_version="x" * desired)
    exact = replace(base, last_validated_final=summary)
    assert len(exact.canonical_bytes()) == MAX_CATALOG_RECORD_BYTES
    backend, _, _ = _initialized_backend(tmp_path, exact)
    assert lookup_catalog_record(identity(), backend=backend).status is CacheCatalogLookupStatus.RECORD_FOUND


@pytest.mark.parametrize("component", ["record", "ancestor"])
def test_h1b_symlinks_are_unsafe(tmp_path, component):
    _, target, _ = _initialized_backend(tmp_path)
    target.parent.mkdir(parents=True)
    if component == "record":
        os.symlink(tmp_path / "elsewhere", target)
    else:
        shard = target.parent
        shard.rmdir()
        os.symlink(tmp_path / "elsewhere", shard)
    backend = LocalCacheCatalogReadOnlyBackend.from_root(tmp_path)
    assert lookup_catalog_record(identity(), backend=backend).status is CacheCatalogLookupStatus.CATALOG_UNSAFE


def test_h1b_wrong_type_layout_or_record_is_unsafe(tmp_path):
    (tmp_path / "catalog").write_bytes(b"not a directory")
    backend = LocalCacheCatalogReadOnlyBackend.from_root(tmp_path)
    assert lookup_catalog_record(identity(), backend=backend).status is CacheCatalogLookupStatus.CATALOG_UNSAFE


def test_h1b_fifo_at_record_path_is_unsafe_without_opening_it(tmp_path):
    _, target, _ = _initialized_backend(tmp_path)
    target.parent.mkdir(parents=True)
    os.mkfifo(target)
    backend = LocalCacheCatalogReadOnlyBackend.from_root(tmp_path)
    assert lookup_catalog_record(identity(), backend=backend).status is CacheCatalogLookupStatus.CATALOG_UNSAFE


class _DelegatingBackend:
    def __init__(self, delegate, *, alter_path=None, alter_on=2, read=None, failure=None):
        self.delegate = delegate
        self.alter_path = alter_path
        self.alter_on = alter_on
        self.read_override = read
        self.failure = failure
        self.counts = {}

    @property
    def cache_root(self):
        return self.delegate.cache_root

    def inspect_root(self):
        path = self.cache_root.resolved_path
        self.counts[path] = self.counts.get(path, 0) + 1
        if self.failure is not None and self.counts[path] == 1:
            raise self.failure
        observed = self.delegate.inspect_root()
        if path == self.alter_path and self.counts[path] >= self.alter_on:
            return replace(observed, file_id=None)
        return observed

    def inspect_catalog_relative(self, relative_path):
        path = self.cache_root.resolved_path / relative_path
        self.counts[path] = self.counts.get(path, 0) + 1
        if self.failure is not None and self.counts[path] == 1:
            raise self.failure
        observed = self.delegate.inspect_catalog_relative(relative_path)
        if path == self.alter_path and self.counts[path] >= self.alter_on:
            return replace(observed, file_id=None)
        return observed

    def read_record_bounded(self, identity):
        if self.read_override is not None:
            return self.read_override(self.delegate.read_record_bounded(identity))
        return self.delegate.read_record_bounded(identity)

    def read_discovered_record(self, namespace, entry_digest):
        return self.delegate.read_discovered_record(namespace, entry_digest)

    def list_catalog_relative(self, relative_path):
        return self.delegate.list_catalog_relative(relative_path)


@pytest.mark.parametrize("subject", ["root", "parent"])
def test_h1b_reduced_root_or_parent_identity_is_unstable(tmp_path, subject):
    local, target, _ = _initialized_backend(tmp_path, live_record())
    altered = tmp_path if subject == "root" else target.parent
    backend = _DelegatingBackend(local, alter_path=altered, alter_on=2)
    assert lookup_catalog_record(identity(), backend=backend).status is CacheCatalogLookupStatus.CATALOG_UNSTABLE


def test_h1b_unstable_record_read_is_unstable_without_retry(tmp_path):
    local, _, _ = _initialized_backend(tmp_path, live_record())
    calls = 0

    def unstable(read):
        nonlocal calls
        calls += 1
        return replace(read, stable_read=False)

    result = lookup_catalog_record(identity(), backend=_DelegatingBackend(local, read=unstable))
    assert result.status is CacheCatalogLookupStatus.CATALOG_UNSTABLE
    assert calls == 1


def test_h1b_replacement_before_open_is_unstable(tmp_path):
    local, _, _ = _initialized_backend(tmp_path, live_record())

    def replaced(read):
        return replace(read, pre_read_identity=replace(read.pre_read_identity, file_id=None))

    result = lookup_catalog_record(identity(), backend=_DelegatingBackend(local, read=replaced))
    assert result.status is CacheCatalogLookupStatus.CATALOG_UNSTABLE


def test_h1b_disappearance_during_read_is_unstable_without_retry(tmp_path):
    local, _, _ = _initialized_backend(tmp_path, live_record())

    class DisappearingBackend(_DelegatingBackend):
        calls = 0

        def read_record_bounded(self, identity):
            self.calls += 1
            raise FileNotFoundError

    backend = DisappearingBackend(local)
    result = lookup_catalog_record(identity(), backend=backend)
    assert result.status is CacheCatalogLookupStatus.CATALOG_UNSTABLE
    assert backend.calls == 1


def test_h1b_permission_failure_is_structured_and_sanitized(tmp_path):
    local, _, relative = _initialized_backend(tmp_path, live_record())
    backend = _DelegatingBackend(local, failure=CacheLookupPermissionError("secret /absolute/path"))
    result = lookup_catalog_record(identity(), backend=backend)
    assert result.status is CacheCatalogLookupStatus.CATALOG_IO_FAILURE
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.relative_path == relative.as_posix()
    assert "/absolute/path" not in repr(result)


def test_h1b_authority_boundary_and_read_only_protocol_are_explicit(tmp_path):
    backend, _, _ = _initialized_backend(tmp_path, live_record())
    result = lookup_catalog_record(identity(), backend=backend)
    assert result.status.value != "hit"
    assert CacheCatalogLookupStatus.RECORD_ABSENT.value != "miss"
    assert not hasattr(result, "validated_entry")
    assert isinstance(backend, CacheCatalogReadOnlyBackend)
    forbidden = {"write", "mkdir", "rename", "replace", "unlink", "fsync", "acquire_lock", "publish"}
    assert forbidden.isdisjoint(dir(backend))


def _writer_backend(tmp_path):
    return LocalCacheCatalogBackend.from_root(tmp_path)


def _write(backend, record=None, expected=None):
    return _write_catalog_record(
        CacheCatalogWriteRequest(record or live_record(), expected), backend=backend
    )


def test_h1c_initialization_creates_only_catalog_owned_layout(tmp_path):
    backend = _writer_backend(tmp_path)
    backend.initialize_catalog()
    assert (tmp_path / "catalog/v1/records").is_dir()
    assert (tmp_path / "catalog/v1/write.lock").is_file()
    assert not (tmp_path / "entries").exists()
    assert not (tmp_path / "staging").exists()
    assert not (tmp_path / "locks").exists()


def test_h1c_initialization_is_idempotent(tmp_path):
    backend = _writer_backend(tmp_path)
    backend.initialize_catalog()
    before = os.lstat(tmp_path / "catalog/v1/write.lock")
    backend.initialize_catalog()
    after = os.lstat(tmp_path / "catalog/v1/write.lock")
    assert (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)


@pytest.mark.parametrize("unsafe", ["symlink", "file"])
def test_h1c_initialization_rejects_unsafe_catalog_component(tmp_path, unsafe):
    if unsafe == "symlink":
        os.symlink(tmp_path / "elsewhere", tmp_path / "catalog")
    else:
        (tmp_path / "catalog").write_bytes(b"unsafe")
    backend = _writer_backend(tmp_path)
    result = _write(backend)
    assert result.status is CacheCatalogWriteStatus.CATALOG_WRITE_UNSAFE


def test_h1c_create_publishes_revision_one_canonically(tmp_path):
    backend = _writer_backend(tmp_path)
    result = _write(backend)
    assert result.status is CacheCatalogWriteStatus.CATALOG_WRITE_APPLIED
    assert result.record_revision == 1
    lookup = lookup_catalog_record(identity(), backend=backend)
    assert lookup.status is CacheCatalogLookupStatus.RECORD_FOUND
    assert lookup.record.record_revision == 1
    assert lookup.record.canonical_bytes() == (
        tmp_path / derive_catalog_record_relative_path(identity())
    ).read_bytes()


def test_h1c_exact_update_increments_revision_and_stale_update_conflicts(tmp_path):
    backend = _writer_backend(tmp_path)
    assert _write(backend).record_revision == 1
    updated = live_record(last_recovery_observation=recovery_summary())
    applied = _write(backend, updated, 1)
    assert applied.record_revision == 2
    stale = _write(backend, live_record(), 1)
    assert stale.status is CacheCatalogWriteStatus.CATALOG_WRITE_CONFLICT
    assert lookup_catalog_record(identity(), backend=backend).record == replace(
        updated, record_revision=2
    )


def test_h1c_second_create_only_writer_conflicts_without_overwrite(tmp_path):
    backend = _writer_backend(tmp_path)
    first = live_record()
    second = live_record(last_recovery_observation=recovery_summary())
    assert _write(backend, first).status is CacheCatalogWriteStatus.CATALOG_WRITE_APPLIED
    assert _write(backend, second).status is CacheCatalogWriteStatus.CATALOG_WRITE_CONFLICT
    assert lookup_catalog_record(identity(), backend=backend).record == replace(
        first, record_revision=1
    )


def test_h1c_tombstone_is_atomic_logical_removal_and_resurrection_requires_revision(tmp_path):
    backend = _writer_backend(tmp_path)
    cache_truth = tmp_path / "entries/v1/untouched"
    cache_truth.mkdir(parents=True)
    cache_truth.joinpath("payload").write_bytes(b"truth")
    backend = _writer_backend(tmp_path)
    assert _write(backend).record_revision == 1
    removed = _write(backend, tombstone(), 1)
    assert removed.record_revision == 2
    target = tmp_path / derive_catalog_record_relative_path(identity())
    assert target.is_file()
    absent = lookup_catalog_record(identity(), backend=backend)
    assert absent.status is CacheCatalogLookupStatus.RECORD_ABSENT
    assert absent.tombstone_revision == 2
    assert _write(backend, live_record(), 1).status is CacheCatalogWriteStatus.CATALOG_WRITE_CONFLICT
    assert _write(backend, live_record(), 2).record_revision == 3
    assert cache_truth.joinpath("payload").read_bytes() == b"truth"


def test_h1c_revision_exhaustion_is_unsupported(tmp_path):
    backend = _writer_backend(tmp_path)
    backend.initialize_catalog()
    target = tmp_path / derive_catalog_record_relative_path(identity())
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(
        replace(live_record(), record_revision=MAX_CATALOG_RECORD_REVISION).canonical_bytes()
    )
    backend = _writer_backend(tmp_path)
    result = _write(backend, live_record(), MAX_CATALOG_RECORD_REVISION)
    assert result.status is CacheCatalogWriteStatus.CATALOG_WRITE_UNSUPPORTED


@pytest.mark.parametrize("current", [b"{", b'{"catalog_record_version":2,"record_state":"live"}'])
def test_h1c_corrupt_or_future_current_record_fails_closed(tmp_path, current):
    backend, target, _ = _initialized_backend(tmp_path, current)
    writer = LocalCacheCatalogBackend(backend.cache_root, backend._filesystem)
    writer.initialize_catalog()
    result = _write(writer, live_record(), 1)
    expected = (
        CacheCatalogWriteStatus.CATALOG_WRITE_CORRUPT
        if current == b"{"
        else CacheCatalogWriteStatus.CATALOG_WRITE_UNSUPPORTED
    )
    assert result.status is expected
    assert target.read_bytes() == current


def test_h1c_writer_uses_flock_exclusive_and_releases(monkeypatch, tmp_path):
    backend = _writer_backend(tmp_path)
    backend.initialize_catalog()
    calls = []
    real = __import__("fcntl").flock

    def observed(fd, operation):
        calls.append(operation)
        return real(fd, operation)

    monkeypatch.setattr("engine.storage.cache_catalog.fcntl.flock", observed)
    with backend.acquire_writer_lock():
        assert calls == [__import__("fcntl").LOCK_EX]
    assert calls == [__import__("fcntl").LOCK_EX, __import__("fcntl").LOCK_UN]


def test_h1c_flock_serializes_cooperating_writers(tmp_path):
    first = _writer_backend(tmp_path)
    first.initialize_catalog()
    second = _writer_backend(tmp_path)
    entered = threading.Event()
    released = threading.Event()

    def waiter():
        with second.acquire_writer_lock():
            entered.set()
        released.set()

    with first.acquire_writer_lock():
        thread = threading.Thread(target=waiter)
        thread.start()
        time.sleep(0.05)
        assert not entered.is_set()
    thread.join(timeout=2)
    assert entered.is_set() and released.is_set()


def test_h1c_flock_acquisition_failure_is_io_failure(monkeypatch, tmp_path):
    backend = _writer_backend(tmp_path)
    backend.initialize_catalog()

    def fail(fd, operation):
        if operation == __import__("fcntl").LOCK_EX:
            raise OSError("flock unavailable")

    monkeypatch.setattr("engine.storage.cache_catalog.fcntl.flock", fail)
    assert _write(backend).status is CacheCatalogWriteStatus.CATALOG_WRITE_IO_FAILURE


def test_h1c_replace_failure_leaves_current_record_and_abandoned_temp(monkeypatch, tmp_path):
    backend = _writer_backend(tmp_path)
    original = live_record()
    assert _write(backend, original).record_revision == 1
    target = tmp_path / derive_catalog_record_relative_path(identity())
    original_bytes = target.read_bytes()

    def fail(*args, **kwargs):
        raise OSError(errno.EXDEV, "cross-device forbidden")

    import errno
    monkeypatch.setattr("engine.storage.cache_catalog.os.replace", fail)
    result = _write(
        backend,
        live_record(last_recovery_observation=recovery_summary()),
        1,
    )
    assert result.status is CacheCatalogWriteStatus.CATALOG_WRITE_IO_FAILURE
    assert target.read_bytes() == original_bytes
    assert any(name.startswith(".catalog-tmp-") for name in os.listdir(target.parent))
    assert lookup_catalog_record(identity(), backend=backend).record == replace(
        original, record_revision=1
    )


def test_h1c_postpublication_directory_fsync_failure_is_io_without_rollback(monkeypatch, tmp_path):
    import stat

    backend = _writer_backend(tmp_path)
    assert _write(backend).record_revision == 1
    target = tmp_path / derive_catalog_record_relative_path(identity())
    updated = live_record(last_recovery_observation=recovery_summary())
    real_fsync = os.fsync

    def fail_after_publication(fd):
        if target.exists() and stat.S_ISDIR(os.fstat(fd).st_mode):
            parsed = parse_cache_catalog_record(target.read_bytes())
            if parsed.record_revision == 2:
                raise OSError("directory durability uncertain")
        return real_fsync(fd)

    monkeypatch.setattr("engine.storage.cache_catalog.os.fsync", fail_after_publication)
    result = _write(backend, updated, 1)
    assert result.status is CacheCatalogWriteStatus.CATALOG_WRITE_IO_FAILURE
    assert lookup_catalog_record(identity(), backend=backend).record == replace(
        updated, record_revision=2
    )


def test_h1c_abandoned_temp_is_ignored_by_exact_lookup(tmp_path):
    backend = _writer_backend(tmp_path)
    assert _write(backend).record_revision == 1
    target = tmp_path / derive_catalog_record_relative_path(identity())
    target.parent.joinpath(".catalog-tmp-abandoned").write_bytes(b"not json")
    assert lookup_catalog_record(identity(), backend=backend).status is CacheCatalogLookupStatus.RECORD_FOUND


class _FailingCatalogBackend:
    def __init__(self, delegate, failing_method, failure):
        self.delegate = delegate
        self.failing_method = failing_method
        self.failure = failure

    @property
    def cache_root(self):
        return self.delegate.cache_root

    def _invoke(self, name, *args, **kwargs):
        if name == self.failing_method:
            raise self.failure
        return getattr(self.delegate, name)(*args, **kwargs)

    def inspect_root(self):
        return self._invoke("inspect_root")

    def inspect_catalog_relative(self, relative_path):
        return self._invoke("inspect_catalog_relative", relative_path)

    def read_record_bounded(self, identity):
        return self._invoke("read_record_bounded", identity)

    def read_discovered_record(self, namespace, entry_digest):
        return self._invoke("read_discovered_record", namespace, entry_digest)

    def list_catalog_relative(self, relative_path):
        return self._invoke("list_catalog_relative", relative_path)

    def initialize_catalog(self):
        return self._invoke("initialize_catalog")

    def acquire_writer_lock(self):
        return self._invoke("acquire_writer_lock")

    def ensure_record_parent(self, identity):
        return self._invoke("ensure_record_parent", identity)

    def publish_record_bytes(self, identity, data, *, create_only, expected_revision):
        return self._invoke(
            "publish_record_bytes",
            identity,
            data,
            create_only=create_only,
            expected_revision=expected_revision,
        )


@pytest.mark.parametrize(
    "method",
    ["initialize_catalog", "acquire_writer_lock", "ensure_record_parent", "publish_record_bytes"],
)
def test_h1c_prepublication_failures_are_structured_and_cache_truth_untouched(tmp_path, method):
    cache_truth = tmp_path / "entries/v1/value"
    cache_truth.mkdir(parents=True)
    cache_truth.joinpath("data").write_bytes(b"authoritative")
    local = _writer_backend(tmp_path)
    if method != "initialize_catalog":
        local.initialize_catalog()
    backend = _FailingCatalogBackend(local, method, OSError("secret native failure"))
    result = _write_catalog_record(CacheCatalogWriteRequest(live_record(), None), backend=backend)
    assert result.status is CacheCatalogWriteStatus.CATALOG_WRITE_IO_FAILURE
    assert cache_truth.joinpath("data").read_bytes() == b"authoritative"
    assert "secret" not in repr(result)


def test_h1c_writer_backend_has_no_cache_mutation_or_cleanup_surface(tmp_path):
    backend = _writer_backend(tmp_path)
    assert isinstance(backend, CacheCatalogBackend)
    forbidden = {
        "delete_cache_entry", "write_cache_entry", "promote", "recover", "cleanup",
        "prune", "unlink", "lock_cache_entry", "mutate_staging",
    }
    assert forbidden.isdisjoint(dir(backend))


def _catalog_record_for(value):
    summary = replace(
        final_summary(),
        producer_id=value.namespace.producer_id,
        producer_schema_version=value.namespace.producer_schema_version,
    )
    return live_record(identity=value, last_validated_final=summary)


def _populate(backend, values, *, tombstones=()):
    for value in values:
        record = tombstone(identity=value) if value in tombstones else _catalog_record_for(value)
        result = _write(backend, record)
        assert result.status is CacheCatalogWriteStatus.CATALOG_WRITE_APPLIED


def _keys(count, **namespace):
    return [identity(key_text=f"{number:064x}", **namespace) for number in range(1, count + 1)]


def test_h1d_cursor_and_page_models_are_strict_immutable_and_not_snapshots():
    namespace = identity().namespace
    cursor = CacheCatalogCursor(CacheCatalogCursorScope.NAMESPACE, namespace, "a" * 64)
    page = CacheCatalogPage(next_cursor=cursor)
    assert page.is_snapshot is False
    with pytest.raises(FrozenInstanceError):
        cursor.entry_digest = "b" * 64
    with pytest.raises(CacheCatalogContractError):
        CacheCatalogCursor(CacheCatalogCursorScope.NAMESPACE, namespace, "bad")
    with pytest.raises(ValueError, match="snapshot"):
        CacheCatalogPage(is_snapshot=True)


@pytest.mark.parametrize("limit", [0, -1, True, 257, 1.5])
def test_h1d_page_limit_rejects_invalid_values(tmp_path, limit):
    backend = _writer_backend(tmp_path)
    with pytest.raises(CacheCatalogContractError, match="limit"):
        enumerate_catalog_namespace(identity().namespace, limit=limit, backend=backend)


@pytest.mark.parametrize("limit", [1, 2, 256])
def test_h1d_namespace_page_limits_and_exclusive_cursors(tmp_path, limit):
    values = _keys(5)
    backend = _writer_backend(tmp_path)
    _populate(backend, values)
    expected = sorted(values)
    page = enumerate_catalog_namespace(values[0].namespace, limit=limit, backend=backend)
    assert [record.identity for record in page.records] == expected[:limit]
    if limit < len(values):
        assert page.next_cursor is not None
        second = enumerate_catalog_namespace(
            values[0].namespace, cursor=page.next_cursor, limit=256, backend=backend
        )
        assert [record.identity for record in second.records] == expected[limit:]
        assert page.next_cursor.entry_digest not in {
            record.identity.entry_digest for record in second.records
        }
    else:
        assert page.next_cursor is None


def test_h1d_empty_namespace_and_uninitialized_catalog_are_distinct(tmp_path):
    backend = _writer_backend(tmp_path)
    unavailable = enumerate_catalog_namespace(identity().namespace, backend=backend)
    assert unavailable.failure_status is CacheCatalogLookupStatus.CATALOG_UNAVAILABLE
    backend.initialize_catalog()
    empty = enumerate_catalog_namespace(identity().namespace, backend=backend)
    assert empty == CacheCatalogPage()


def test_h1d_tombstones_are_omitted_without_breaking_pagination(tmp_path):
    values = sorted(_keys(4))
    backend = _writer_backend(tmp_path)
    _populate(backend, values, tombstones=(values[1], values[3]))
    first = enumerate_catalog_namespace(values[0].namespace, limit=1, backend=backend)
    second = enumerate_catalog_namespace(
        values[0].namespace, cursor=first.next_cursor, limit=1, backend=backend
    )
    live = [values[0], values[2]]
    assert [first.records[0].identity, second.records[0].identity] == live
    assert second.next_cursor is None


def test_h1d_namespace_cursor_mismatch_is_rejected(tmp_path):
    namespace = identity().namespace
    other = CacheNamespace("video", namespace.producer_id, namespace.producer_schema_version)
    cursor = CacheCatalogCursor(CacheCatalogCursorScope.NAMESPACE, other, "a" * 64)
    with pytest.raises(CacheCatalogContractError, match="does not belong"):
        enumerate_catalog_namespace(namespace, cursor=cursor, backend=_writer_backend(tmp_path))


def test_h1d_full_catalog_order_is_namespace_then_numeric_schema_then_digest(tmp_path):
    values = [
        identity(domain="video", producer_id="p", schema=2, key_text="1" * 64),
        identity(domain="audio", producer_id="z", schema=2, key_text="2" * 64),
        identity(domain="audio", producer_id="p", schema=10, key_text="3" * 64),
        identity(domain="audio", producer_id="p", schema=2, key_text="4" * 64),
    ]
    backend = _writer_backend(tmp_path)
    _populate(backend, values)
    page = iterate_catalog_records(limit=256, backend=backend)
    assert [record.identity for record in page.records] == sorted(values)
    assert [
        item.identity.namespace.producer_schema_version for item in page.records[:2]
    ] == [2, 10]


def test_h1d_full_catalog_paginates_across_namespace_boundaries(tmp_path):
    values = [
        identity(domain="audio", producer_id="p", schema=2, key_text="1" * 64),
        identity(domain="audio", producer_id="q", schema=1, key_text="2" * 64),
        identity(domain="video", producer_id="p", schema=1, key_text="3" * 64),
    ]
    backend = _writer_backend(tmp_path)
    _populate(backend, values)
    seen = []
    cursor = None
    while True:
        page = iterate_catalog_records(cursor=cursor, limit=1, backend=backend)
        seen.extend(record.identity for record in page.records)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert seen == sorted(values)
    assert len(seen) == len(set(seen))


def test_h1d_namespace_enumeration_never_descends_into_unrelated_namespace(tmp_path):
    wanted = _keys(1)[0]
    unrelated = identity(domain="video", key_text="f" * 64)
    backend = _writer_backend(tmp_path)
    _populate(backend, (wanted, unrelated))

    class Guarded(_DelegatingBackend):
        def list_catalog_relative(self, relative_path):
            if "video" in relative_path.parts:
                raise AssertionError("unrelated namespace traversed")
            return self.delegate.list_catalog_relative(relative_path)

    page = enumerate_catalog_namespace(wanted.namespace, backend=Guarded(backend))
    assert [record.identity for record in page.records] == [wanted]


def test_h1d_approved_temp_name_is_ignored_but_malformed_name_is_corrupt(tmp_path):
    value = _keys(1)[0]
    backend = _writer_backend(tmp_path)
    _populate(backend, (value,))
    leaf = (tmp_path / derive_catalog_record_relative_path(value)).parent
    leaf.joinpath(".catalog-tmp-" + "a" * 32).write_bytes(b"partial")
    assert enumerate_catalog_namespace(value.namespace, backend=backend).failure_status is None
    leaf.joinpath("almost-a-record.json").write_bytes(b"{}")
    failed = enumerate_catalog_namespace(value.namespace, backend=backend)
    assert failed.failure_status is CacheCatalogLookupStatus.CATALOG_CORRUPT


@pytest.mark.parametrize("kind", ["malformed", "unsupported", "symlink"])
def test_h1d_broken_canonical_record_fails_the_page(tmp_path, kind):
    value = _keys(1)[0]
    backend = _writer_backend(tmp_path)
    _populate(backend, (value,))
    target = tmp_path / derive_catalog_record_relative_path(value)
    if kind == "malformed":
        target.write_bytes(b"{")
    elif kind == "unsupported":
        target.write_bytes(
            target.read_bytes().replace(
                b'"catalog_record_version":1', b'"catalog_record_version":2'
            )
        )
    else:
        target.unlink()
        os.symlink(tmp_path / "elsewhere", target)
    backend = _writer_backend(tmp_path)
    page = enumerate_catalog_namespace(value.namespace, backend=backend)
    expected = {
        "malformed": CacheCatalogLookupStatus.CATALOG_CORRUPT,
        "unsupported": CacheCatalogLookupStatus.CATALOG_UNSUPPORTED,
        "symlink": CacheCatalogLookupStatus.CATALOG_UNSAFE,
    }[kind]
    assert page.failure_status is expected
    assert page.records == ()


def test_h1d_directory_one_over_limit_is_structured_corruption(tmp_path):
    backend = _writer_backend(tmp_path)
    backend.initialize_catalog()
    root = Path("catalog/v1/records")
    observed = backend.inspect_catalog_relative(root)

    class OverCapacity(_DelegatingBackend):
        def list_catalog_relative(self, relative_path):
            if relative_path == root:
                names = tuple(f"x{number:04d}" for number in range(4097))
                return CacheCatalogDirectoryListing(names, True, observed, observed)
            return self.delegate.list_catalog_relative(relative_path)

    page = iterate_catalog_records(backend=OverCapacity(backend))
    assert page.failure_status is CacheCatalogLookupStatus.CATALOG_CORRUPT


def test_h1d_exact_4096_directory_entries_allowed_and_one_over_rejected(tmp_path):
    value = _keys(1)[0]
    backend = _writer_backend(tmp_path)
    _populate(backend, (value,))
    leaf = (tmp_path / derive_catalog_record_relative_path(value)).parent
    for number in range(MAX_CATALOG_DIRECTORY_ENTRIES - 1):
        leaf.joinpath(f".catalog-tmp-{number:032x}").touch()
    accepted = enumerate_catalog_namespace(value.namespace, backend=backend)
    assert accepted.failure_status is None
    assert [record.identity for record in accepted.records] == [value]
    leaf.joinpath(f".catalog-tmp-{MAX_CATALOG_DIRECTORY_ENTRIES:032x}").touch()
    rejected = enumerate_catalog_namespace(value.namespace, backend=backend)
    assert rejected.failure_status is CacheCatalogLookupStatus.CATALOG_CORRUPT


def test_h1d_reduced_directory_identity_evidence_is_unstable(tmp_path):
    value = _keys(1)[0]
    backend = _writer_backend(tmp_path)
    _populate(backend, (value,))

    class Reduced(_DelegatingBackend):
        def list_catalog_relative(self, relative_path):
            listing = self.delegate.list_catalog_relative(relative_path)
            if relative_path.name == str(value.namespace.producer_schema_version):
                return replace(
                    listing,
                    post_identity=replace(listing.post_identity, file_id=None),
                )
            return listing

    page = enumerate_catalog_namespace(value.namespace, backend=Reduced(backend))
    assert page.failure_status is CacheCatalogLookupStatus.CATALOG_UNSTABLE


@pytest.mark.parametrize("failure", ["unstable", "io"])
def test_h1d_record_read_failure_is_not_silently_skipped(tmp_path, failure):
    value = _keys(1)[0]
    backend = _writer_backend(tmp_path)
    _populate(backend, (value,))

    class FailedRead(_DelegatingBackend):
        calls = 0

        def read_discovered_record(self, namespace, entry_digest):
            self.calls += 1
            if failure == "io":
                raise CacheLookupPermissionError("private native detail")
            return replace(
                self.delegate.read_discovered_record(namespace, entry_digest),
                stable_read=False,
            )

    failing = FailedRead(backend)
    page = enumerate_catalog_namespace(value.namespace, backend=failing)
    expected = (
        CacheCatalogLookupStatus.CATALOG_UNSTABLE
        if failure == "unstable"
        else CacheCatalogLookupStatus.CATALOG_IO_FAILURE
    )
    assert page.failure_status is expected
    assert page.records == () and failing.calls == 1
    assert "private native detail" not in repr(page)


def test_h1d_full_iteration_stops_after_page_plus_one_live_records(tmp_path):
    values = _keys(8)
    backend = _writer_backend(tmp_path)
    _populate(backend, values)

    class Counting(_DelegatingBackend):
        reads = 0

        def read_discovered_record(self, namespace, entry_digest):
            self.reads += 1
            return self.delegate.read_discovered_record(namespace, entry_digest)

    counting = Counting(backend)
    page = iterate_catalog_records(limit=1, backend=counting)
    assert len(page.records) == 1 and page.next_cursor is not None
    assert counting.reads == 2


def test_h1d_cursor_change_does_not_rewind_and_later_insert_may_appear(tmp_path):
    values = sorted(_keys(3))
    backend = _writer_backend(tmp_path)
    _populate(backend, (values[1], values[2]))
    first = enumerate_catalog_namespace(values[0].namespace, limit=1, backend=backend)
    assert first.records[0].identity == values[1]
    _populate(backend, (values[0],))
    later = identity(key_text="f" * 64)
    _populate(backend, (later,))
    second = enumerate_catalog_namespace(
        values[0].namespace, cursor=first.next_cursor, limit=256, backend=backend
    )
    emitted = [record.identity for record in second.records]
    assert values[0] not in emitted
    assert all(item.entry_digest > first.next_cursor.entry_digest for item in emitted)


def _trusted_h1e_evidence(tmp_path):
    root = tmp_path / "cache"
    root.mkdir(parents=True)
    key = CacheKey("a" * 64)
    namespace = CacheNamespace("audio", "transcription.whisper", 3)
    manifest = PayloadManifest(
        (PayloadManifestRecord("artifact.json", 7, "sha256:" + "b" * 64,
                               "application/json", "primary"),)
    )
    metadata = CacheEntryMetadata(
        derive_entry_digest(key),
        CacheKeyReference.from_cache_key(key),
        namespace,
        CacheArtifactMetadata("transcript", "private-logical-id", 1),
        CacheProducerMetadata(namespace.producer_id, "4.2.0", 3),
        CacheRuntimeFingerprint(1, {"model": "large-v3", "secret": "not-persisted"}),
        "2026-07-20T09:00:00Z",
        "sha256:" + hashlib.sha256(manifest.canonical_bytes()).hexdigest(),
        1,
        7,
    )
    marker = CompletenessMarker(
        metadata.entry_digest,
        "sha256:" + hashlib.sha256(metadata.canonical_bytes()).hexdigest(),
        metadata.payload_manifest_digest,
    )
    validated = ValidatedCacheEntryReference(
        root / "entries/exact",
        metadata.entry_digest,
        namespace,
        metadata.cache_key,
        metadata,
        manifest,
        marker,
        CacheVerificationLevel.FULL_PAYLOAD_SHA256,
    )
    lookup = ReadOnlyCacheLookupResult(
        CacheLookupStatus.HIT,
        None,
        validated.entry_path,
        validated,
        metadata.entry_digest,
        namespace,
        metadata.cache_key,
        (),
        CacheVerificationLevel.FULL_PAYLOAD_SHA256,
        CACHE_ENTRY_CONTRACT_VERSION,
        True,
        metadata,
        manifest,
        marker,
    )
    promoted = PromotedCacheEntryReference(
        root / "entries/final",
        metadata.entry_digest,
        namespace,
        metadata.cache_key,
        metadata,
        manifest,
        marker,
        1,
        7,
    )
    promotion = CachePromotionResult(
        CachePromotionStatus.PROMOTED_AND_RELEASED, promoted
    )
    expectation = CacheLookupExpectation(
        namespace,
        namespace.producer_id,
        namespace.producer_schema_version,
        metadata.runtime_fingerprint,
    )
    recovery_request = CacheRecoveryInspectionRequest(
        ValidatedCacheRoot.from_path(root),
        namespace,
        key,
        expectation,
        None,
        ProducerPayloadExpectation(),
        CacheLookupVerificationPolicy(),
        LockObservationPolicy(60),
        RecoveryInspectionPolicy(),
    )
    catalog_identity = CacheCatalogIdentity(
        namespace, metadata.entry_digest, metadata.cache_key
    )
    backend = LocalCacheCatalogBackend.from_root(root)
    return catalog_identity, backend, lookup, promotion, recovery_request


def _recovery_observation(request, status=CacheRecoveryStatus.EMPTY, *, root_failure=False):
    reason = (
        CacheRecoveryReason.UNSAFE_ROOT
        if status in {
            CacheRecoveryStatus.RECOVERY_UNSAFE,
            CacheRecoveryStatus.RECOVERY_UNSUPPORTED,
            CacheRecoveryStatus.RECOVERY_UNSTABLE,
            CacheRecoveryStatus.RECOVERY_INVALID,
        }
        else None
    )
    return CacheRecoveryObservation(
        derive_entry_digest(request.cache_key),
        (StagingRecoveryObservation(
            0,
            "staging/v1/candidate",
            None if root_failure else StagingRecoveryState.STAGING_ABSENT,
        ),),
        FinalRecoveryObservation(
            "entries/v1/final",
            None if root_failure else FinalRecoveryState.FINAL_ABSENT,
        ),
        LockRecoveryObservation(
            "locks/v1/lock",
            None if root_failure else LockRecoveryState.LOCK_ABSENT,
        ),
        status,
        reason,
    )


def test_h1e_lookup_hit_upsert_derives_summary_and_fingerprint_digest(tmp_path):
    catalog_identity, backend, lookup, _, _ = _trusted_h1e_evidence(tmp_path)
    original_lookup = lookup
    result = upsert_catalog_from_lookup(
        catalog_identity, lookup, expected_revision=None, backend=backend
    )
    assert result.record_revision == 1 and lookup is original_lookup
    record = lookup_catalog_record(catalog_identity, backend=backend).record
    summary = record.last_validated_final
    assert summary.provenance is CacheCatalogFinalProvenance.STEP5B_HIT
    assert summary.runtime_fingerprint_digest == (
        "sha256:" + hashlib.sha256(
            lookup.metadata.runtime_fingerprint.canonical_bytes()
        ).hexdigest()
    )
    serialized = record.canonical_bytes()
    assert b"private-logical-id" not in serialized
    assert b"not-persisted" not in serialized


def test_h1e_non_hit_cannot_become_final_provenance(tmp_path):
    catalog_identity, backend, lookup, _, _ = _trusted_h1e_evidence(tmp_path)
    rejected = replace(
        lookup,
        status=CacheLookupStatus.MISS,
        validated_entry=None,
        payload_bytes_fully_hashed=False,
        metadata=None,
        manifest=None,
        marker=None,
    )
    with pytest.raises(CacheCatalogContractError, match="only a fully verified"):
        upsert_catalog_from_lookup(
            catalog_identity, rejected, expected_revision=None, backend=backend
        )
    assert not (backend.cache_root.resolved_path / "catalog").exists()


@pytest.mark.parametrize(
    "status",
    [CachePromotionStatus.PROMOTED_AND_RELEASED,
     CachePromotionStatus.PROMOTED_LOCK_RETAINED],
)
def test_h1e_successful_promotion_upsert_supports_both_final_outcomes(tmp_path, status):
    catalog_identity, backend, _, promotion, _ = _trusted_h1e_evidence(tmp_path)
    promotion = replace(promotion, status=status)
    original = promotion
    result = upsert_catalog_from_promotion(
        catalog_identity, promotion, expected_revision=None, backend=backend
    )
    assert result.record_revision == 1 and promotion is original
    summary = lookup_catalog_record(catalog_identity, backend=backend).record.last_validated_final
    assert summary.provenance is CacheCatalogFinalProvenance.STEP5D_PROMOTION


def test_h1e_failed_promotion_is_rejected_without_catalog_or_upstream_change(tmp_path):
    catalog_identity, backend, _, promotion, _ = _trusted_h1e_evidence(tmp_path)
    failed = CachePromotionResult(CachePromotionStatus.PROMOTION_IO_FAILURE)
    with pytest.raises(CacheCatalogContractError, match="only successful"):
        upsert_catalog_from_promotion(
            catalog_identity, failed, expected_revision=None, backend=backend
        )
    assert failed.status is CachePromotionStatus.PROMOTION_IO_FAILURE
    assert promotion.promoted_entry is not None
    assert not (backend.cache_root.resolved_path / "catalog").exists()


def test_h1e_root_failure_maps_to_explicit_unobserved_summary(tmp_path):
    catalog_identity, backend, _, _, request = _trusted_h1e_evidence(tmp_path)
    observation = _recovery_observation(
        request, CacheRecoveryStatus.RECOVERY_UNSAFE, root_failure=True
    )
    result = upsert_catalog_from_recovery(
        catalog_identity, request, observation, expected_revision=None, backend=backend
    )
    assert result.record_revision == 1
    summary = lookup_catalog_record(catalog_identity, backend=backend).record.last_recovery_observation
    assert summary.final_state is CacheCatalogFinalState.FINAL_UNOBSERVED
    assert summary.lock_state is CacheCatalogLockState.LOCK_UNOBSERVED
    assert summary.staging_candidate_count is None
    assert summary.status is observation.status and summary.reason is observation.reason
    assert parse_cache_catalog_record(
        lookup_catalog_record(catalog_identity, backend=backend).record.canonical_bytes()
    ).last_recovery_observation == summary


def test_h1e_observed_absence_and_zero_candidates_remain_concrete(tmp_path):
    catalog_identity, backend, _, _, request = _trusted_h1e_evidence(tmp_path)
    observation = _recovery_observation(request)
    upsert_catalog_from_recovery(
        catalog_identity, request, observation, expected_revision=None, backend=backend
    )
    summary = lookup_catalog_record(catalog_identity, backend=backend).record.last_recovery_observation
    assert summary.final_state is FinalRecoveryState.FINAL_ABSENT
    assert summary.lock_state is LockRecoveryState.LOCK_ABSENT
    assert summary.staging_candidate_count == 0
    assert summary.final_state is not CacheCatalogFinalState.FINAL_UNOBSERVED
    assert summary.lock_state is not CacheCatalogLockState.LOCK_UNOBSERVED
    assert parse_cache_catalog_record(
        lookup_catalog_record(catalog_identity, backend=backend).record.canonical_bytes()
    ).last_recovery_observation == summary


@pytest.mark.parametrize("staging", [(), (
    StagingRecoveryObservation(0, "staging/v1/one", None),
    StagingRecoveryObservation(1, "staging/v1/two", None),
)])
def test_h1e_unobserved_staging_requires_exactly_one_placeholder(tmp_path, staging):
    catalog_identity, backend, _, _, request = _trusted_h1e_evidence(tmp_path)
    observation = replace(_recovery_observation(request, root_failure=True), staging=staging)
    with pytest.raises(CacheCatalogContractError, match="staging evidence"):
        upsert_catalog_from_recovery(
            catalog_identity,
            request,
            observation,
            expected_revision=None,
            backend=backend,
        )
    assert not (backend.cache_root.resolved_path / "catalog").exists()


@pytest.mark.parametrize(
    "concrete",
    [
        StagingRecoveryState.STAGING_ABSENT,
        StagingRecoveryState.STAGING_COMPLETE_VALID,
    ],
)
def test_h1e_unobserved_staging_cannot_mix_with_concrete_evidence(
    tmp_path, concrete
):
    catalog_identity, backend, _, _, request = _trusted_h1e_evidence(tmp_path)
    staging = (
        StagingRecoveryObservation(0, "staging/v1/placeholder", None),
        StagingRecoveryObservation(1, "staging/v1/concrete", concrete),
    )
    observation = replace(_recovery_observation(request, root_failure=True), staging=staging)
    with pytest.raises(CacheCatalogContractError, match="mixes observed and unobserved"):
        upsert_catalog_from_recovery(
            catalog_identity,
            request,
            observation,
            expected_revision=None,
            backend=backend,
        )
    assert not (backend.cache_root.resolved_path / "catalog").exists()


@pytest.mark.parametrize("candidate_count", [1, 64])
def test_h1e_concrete_staging_candidate_count_preserves_exact_bounds(
    tmp_path, candidate_count
):
    catalog_identity, backend, _, _, request = _trusted_h1e_evidence(tmp_path)
    staging = tuple(
        StagingRecoveryObservation(
            index,
            f"staging/v1/candidate-{index}",
            StagingRecoveryState.STAGING_COMPLETE_VALID,
        )
        for index in range(candidate_count)
    )
    observation = replace(_recovery_observation(request), staging=staging)
    upsert_catalog_from_recovery(
        catalog_identity,
        request,
        observation,
        expected_revision=None,
        backend=backend,
    )
    summary = lookup_catalog_record(
        catalog_identity, backend=backend
    ).record.last_recovery_observation
    assert summary.staging_candidate_count == candidate_count
    assert parse_cache_catalog_record(
        lookup_catalog_record(catalog_identity, backend=backend).record.canonical_bytes()
    ).last_recovery_observation == summary


def test_h1e_every_recovery_status_is_accepted_as_completed_evidence(tmp_path):
    for index, status in enumerate(CacheRecoveryStatus):
        case = tmp_path / str(index)
        catalog_identity, backend, _, _, request = _trusted_h1e_evidence(case)
        observation = _recovery_observation(request, status)
        result = upsert_catalog_from_recovery(
            catalog_identity, request, observation, expected_revision=None, backend=backend
        )
        assert result.status is CacheCatalogWriteStatus.CATALOG_WRITE_APPLIED
        stored = lookup_catalog_record(catalog_identity, backend=backend).record.last_recovery_observation
        assert stored.status is status and stored.reason is observation.reason


def test_h1e_final_and_recovery_updates_preserve_each_other(tmp_path):
    catalog_identity, backend, lookup, _, request = _trusted_h1e_evidence(tmp_path)
    assert upsert_catalog_from_lookup(
        catalog_identity, lookup, expected_revision=None, backend=backend
    ).record_revision == 1
    observation = _recovery_observation(request)
    assert upsert_catalog_from_recovery(
        catalog_identity, request, observation, expected_revision=1, backend=backend
    ).record_revision == 2
    after_recovery = lookup_catalog_record(catalog_identity, backend=backend).record
    original_final = after_recovery.last_validated_final
    assert original_final is not None and after_recovery.last_recovery_observation is not None
    assert upsert_catalog_from_lookup(
        catalog_identity, lookup, expected_revision=2, backend=backend
    ).record_revision == 3
    merged = lookup_catalog_record(catalog_identity, backend=backend).record
    assert merged.last_recovery_observation == after_recovery.last_recovery_observation


def test_h1e_empty_tombstone_and_exact_resurrection_preserve_cache_truth(tmp_path):
    catalog_identity, backend, lookup, _, request = _trusted_h1e_evidence(tmp_path)
    truth = backend.cache_root.resolved_path / "entries/v1/truth"
    truth.mkdir(parents=True)
    truth.joinpath("payload").write_bytes(b"untouched")
    backend = LocalCacheCatalogBackend.from_root(backend.cache_root.resolved_path)
    upsert_catalog_from_lookup(
        catalog_identity, lookup, expected_revision=None, backend=backend
    )
    empty = _recovery_observation(request)
    tombstoned = tombstone_catalog_empty(
        catalog_identity, request, empty, expected_revision=1, backend=backend
    )
    assert tombstoned.record_revision == 2
    absent = lookup_catalog_record(catalog_identity, backend=backend)
    assert absent.status is CacheCatalogLookupStatus.RECORD_ABSENT
    assert absent.tombstone_revision == 2
    assert truth.joinpath("payload").read_bytes() == b"untouched"
    resurrected = upsert_catalog_from_lookup(
        catalog_identity, lookup, expected_revision=2, backend=backend
    )
    assert resurrected.record_revision == 3


def test_h1e_nonempty_or_mismatched_recovery_cannot_authorize_mutation(tmp_path):
    catalog_identity, backend, _, _, request = _trusted_h1e_evidence(tmp_path)
    nonempty = _recovery_observation(request, CacheRecoveryStatus.FINAL_PUBLISHED)
    with pytest.raises(CacheCatalogContractError, match="only Step 5E EMPTY"):
        tombstone_catalog_empty(
            catalog_identity, request, nonempty, expected_revision=1, backend=backend
        )
    mismatched = replace(catalog_identity, namespace=CacheNamespace("video", "p", 1))
    with pytest.raises(CacheCatalogContractError, match="does not match"):
        upsert_catalog_from_recovery(
            mismatched, request, nonempty, expected_revision=None, backend=backend
        )
    assert not (backend.cache_root.resolved_path / "catalog").exists()


def test_h1e_public_exports_exclude_native_and_raw_mutation_helpers():
    import engine.storage as storage

    approved = {
        "CacheCatalogIdentity", "CacheCatalogRecord", "CacheCatalogLiveRecord",
        "CacheCatalogTombstone", "CacheCatalogLookupResult", "CacheCatalogPage",
        "lookup_catalog_record", "upsert_catalog_from_lookup",
        "upsert_catalog_from_promotion", "upsert_catalog_from_recovery",
        "tombstone_catalog_empty", "enumerate_catalog_namespace",
        "iterate_catalog_records",
    }
    assert approved <= set(storage.__all__)
    forbidden = {
        "LocalCacheCatalogBackend", "LocalCacheCatalogReadOnlyBackend",
        "CacheCatalogWriteRequest", "_write_catalog_record", "renameatx_np",
    }
    assert forbidden.isdisjoint(storage.__all__)
    result_fields = set(CacheCatalogLookupResult.__dataclass_fields__)
    assert "validated_entry" not in result_fields
    assert all(term not in " ".join(storage.__all__).lower() for term in (
        "cleanup", "retention", "quota", "pruning", "rebuild"
    ))


class _FailingH1EPublicationBackend(LocalCacheCatalogBackend):
    def publish_record_bytes(self, *args, **kwargs):
        raise OSError("private native publication detail")


def test_h1e_catalog_is_only_an_optional_hint_before_authoritative_step5b(tmp_path):
    catalog_identity, backend, lookup, _, _ = _trusted_h1e_evidence(tmp_path)
    absent = lookup_catalog_record(catalog_identity, backend=backend)
    assert absent.status is CacheCatalogLookupStatus.CATALOG_UNAVAILABLE
    assert lookup.status is CacheLookupStatus.HIT
    assert lookup.validated_entry is not None

    upsert_catalog_from_lookup(
        catalog_identity, lookup, expected_revision=None, backend=backend
    )
    hint = lookup_catalog_record(catalog_identity, backend=backend)
    assert hint.status is CacheCatalogLookupStatus.RECORD_FOUND
    assert "validated_entry" not in CacheCatalogLookupResult.__dataclass_fields__
    assert lookup.status is CacheLookupStatus.HIT


def test_h1e_catalog_failure_never_changes_lookup_or_promotion_authority(tmp_path):
    catalog_identity, backend, lookup, promotion, _ = _trusted_h1e_evidence(tmp_path)
    failing = _FailingH1EPublicationBackend.from_root(backend.cache_root.resolved_path)

    lookup_result = upsert_catalog_from_lookup(
        catalog_identity, lookup, expected_revision=None, backend=failing
    )
    assert lookup_result.status is CacheCatalogWriteStatus.CATALOG_WRITE_IO_FAILURE
    assert lookup.status is CacheLookupStatus.HIT
    assert lookup.validated_entry is not None

    promotion_result = upsert_catalog_from_promotion(
        catalog_identity, promotion, expected_revision=None, backend=failing
    )
    assert promotion_result.status is CacheCatalogWriteStatus.CATALOG_WRITE_IO_FAILURE
    assert promotion.status is CachePromotionStatus.PROMOTED_AND_RELEASED
    assert promotion.promoted_entry is not None


def test_h1e_all_typed_integrations_reject_identity_mismatch_before_mutation(tmp_path):
    catalog_identity, backend, lookup, promotion, request = _trusted_h1e_evidence(tmp_path)
    mismatched = replace(catalog_identity, namespace=CacheNamespace("video", "p", 1))

    with pytest.raises(CacheCatalogContractError, match="identity"):
        upsert_catalog_from_lookup(
            mismatched, lookup, expected_revision=None, backend=backend
        )
    with pytest.raises(CacheCatalogContractError, match="identity"):
        upsert_catalog_from_promotion(
            mismatched, promotion, expected_revision=None, backend=backend
        )
    with pytest.raises(CacheCatalogContractError, match="identity"):
        upsert_catalog_from_recovery(
            mismatched,
            request,
            _recovery_observation(request),
            expected_revision=None,
            backend=backend,
        )
    assert not (backend.cache_root.resolved_path / "catalog").exists()
