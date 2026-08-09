import hashlib
import os
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
    CacheCatalogFinalSummary,
    CacheCatalogIdentity,
    CacheCatalogLookupResult,
    CacheCatalogLookupStatus,
    CacheCatalogLiveRecord,
    CacheCatalogReadOnlyBackend,
    CacheCatalogRecord,
    CacheCatalogRecordState,
    CacheCatalogRecoveryProvenance,
    CacheCatalogRecoverySummary,
    CacheCatalogTombstone,
    CacheCatalogUnsupportedVersionError,
    CacheCatalogVerificationLevel,
    LocalCacheCatalogReadOnlyBackend,
    catalog_identity_sort_key,
    derive_catalog_record_relative_path,
    lookup_catalog_record,
    parse_cache_catalog_record,
    serialize_cache_catalog_record,
)
from engine.storage.cache_keys import CacheKey
from engine.storage.cache_lookup import CacheLookupPermissionError, CacheLookupReason
from engine.storage.cache_recovery import (
    CacheRecoveryReason,
    CacheRecoveryStatus,
    FinalRecoveryState,
    LockRecoveryState,
)
from engine.storage.persistent_cache import (
    CACHE_ENTRY_CONTRACT_VERSION,
    CacheKeyReference,
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
        "enumerate_catalog_namespace",
        "write_lock",
        "publish",
        "replace",
        "unlink",
        "mkdir",
        "fsync",
    }
    assert forbidden.isdisjoint(public)
    assert {"lookup_catalog_record", "CacheCatalogReadOnlyBackend"} <= public


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
