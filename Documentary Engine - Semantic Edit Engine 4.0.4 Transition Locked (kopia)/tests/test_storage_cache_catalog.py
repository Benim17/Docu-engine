import hashlib
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
    CacheCatalogLiveRecord,
    CacheCatalogRecord,
    CacheCatalogRecordState,
    CacheCatalogRecoveryProvenance,
    CacheCatalogRecoverySummary,
    CacheCatalogTombstone,
    CacheCatalogUnsupportedVersionError,
    CacheCatalogVerificationLevel,
    catalog_identity_sort_key,
    parse_cache_catalog_record,
    serialize_cache_catalog_record,
)
from engine.storage.cache_keys import CacheKey
from engine.storage.cache_lookup import CacheLookupReason
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


def test_h1a_module_has_no_filesystem_or_backend_surface():
    import engine.storage.cache_catalog as catalog

    public = {name for name in dir(catalog) if not name.startswith("_")}
    forbidden = {
        "Path",
        "open",
        "lookup_catalog_record",
        "enumerate_catalog_namespace",
        "write_lock",
        "publish",
        "replace",
        "unlink",
        "mkdir",
        "fsync",
    }
    assert forbidden.isdisjoint(public)
