from dataclasses import replace
import builtins
import hashlib
import json
import os
from pathlib import Path

import pytest

from engine.storage import (
    CACHE_ENTRY_CONTRACT_VERSION,
    CACHE_LOOKUP_STATUS_PRECEDENCE,
    CacheArtifactMetadata,
    CacheEntryContract,
    CacheEntryContractError,
    CacheEntryMetadata,
    CacheKey,
    CacheKeyReference,
    CacheLookupExpectation,
    CacheLookupResult,
    CacheLookupStatus,
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
    derive_staging_entry_path,
    digest_shards,
    parse_canonical_json,
)


SHA_A = "a" * 64
SHA_B = "b" * 64


def key(digest=SHA_A):
    return CacheKey(digest)


def namespace(**changes):
    values = {"domain": "audio", "producer_id": "transcription.whisper", "producer_schema_version": 3}
    values.update(changes)
    return CacheNamespace(**values)


def record(path="segments.json", size=7):
    return PayloadManifestRecord(path, size, f"sha256:{SHA_A}", "application/json", "primary")


def manifest(*records):
    return PayloadManifest(tuple(records or (record(),)))


def metadata(manifest_value=None, **changes):
    manifest_value = manifest_value or manifest()
    cache_key = key()
    values = {
        "entry_digest": derive_entry_digest(cache_key),
        "cache_key": CacheKeyReference.from_cache_key(cache_key),
        "namespace": namespace(),
        "artifact": CacheArtifactMetadata("transcript", "episode-001-voiceover", 1),
        "producer": CacheProducerMetadata("transcription.whisper", "4.2.0", 3),
        "runtime_fingerprint": CacheRuntimeFingerprint(1, {"language": "sv", "model": "large-v3"}),
        "created_at_utc": "2026-07-20T09:00:00Z",
        "payload_manifest_digest": "sha256:" + hashlib.sha256(manifest_value.canonical_bytes()).hexdigest(),
        "payload_file_count": len(manifest_value.files),
        "payload_total_bytes": sum(item.size_bytes for item in manifest_value.files),
    }
    values.update(changes)
    return CacheEntryMetadata(**values)


def test_valid_namespace_has_deterministic_canonical_form_and_dict_round_trip():
    value = namespace()
    assert value.canonical == "audio/transcription.whisper/3"
    assert CacheNamespace.from_dict(value.to_dict()) == value
    assert canonical_json_bytes(value.to_dict()) == b'{"domain":"audio","producer_id":"transcription.whisper","producer_schema_version":3}'


@pytest.mark.parametrize(("field", "value"), [
    ("domain", ""), ("domain", "Audio"), ("domain", "åudio"), ("domain", "audio/cache"),
    ("domain", ".audio"), ("domain", "audio."), ("domain", "audio..cache"),
    ("domain", "a" * 81), ("producer_id", "producer\\name"),
    ("producer_schema_version", 0), ("producer_schema_version", -1),
    ("producer_schema_version", True), ("producer_schema_version", "03"),
])
def test_invalid_namespace_categories_are_rejected(field, value):
    with pytest.raises(CacheEntryContractError):
        namespace(**{field: value})


def test_namespace_total_length_is_bounded():
    with pytest.raises(CacheEntryContractError, match="240"):
        CacheNamespace("a" * 80, "b" * 80, int("9" * 80))


def test_entry_digest_uses_only_validated_cache_key_canonical_bytes():
    expected = hashlib.sha256(key().canonical_bytes()).hexdigest()
    assert derive_entry_digest(key()) == expected
    assert derive_entry_digest(key()) == derive_entry_digest(CacheKey.parse(str(key())))
    with pytest.raises(CacheEntryContractError, match="validated CacheKey"):
        derive_entry_digest(str(key()))


def test_sharding_and_final_staging_lock_paths_are_exact_and_pure():
    digest = derive_entry_digest(key())
    assert digest_shards(digest) == (digest[:2], digest[2:4])
    root = Path("cache-root")
    expected_namespace = Path("audio/transcription.whisper/3")
    assert derive_final_entry_path(root, namespace(), key()) == root / "entries/v1" / expected_namespace / digest[:2] / digest[2:4] / digest
    assert derive_staging_entry_path(root, namespace(), key(), "writer_01") == root / "staging/v1" / expected_namespace / f"{digest}.writer_01"
    assert derive_lock_path(root, namespace(), key()) == root / "locks/v1" / expected_namespace / digest[:2] / digest[2:4] / f"{digest}.lock"


def test_sharding_rejects_invalid_digest_boundaries():
    for value in ("a" * 63, "a" * 65, "A" * 64, "g" * 64):
        with pytest.raises(CacheEntryContractError):
            digest_shards(value)


def test_logical_id_never_influences_entry_path():
    first = metadata()
    second = replace(first, artifact=replace(first.artifact, logical_id="completely-different"))
    assert derive_final_entry_path("root", first.namespace, first.cache_key.to_cache_key()) == derive_final_entry_path("root", second.namespace, second.cache_key.to_cache_key())


def test_canonical_json_is_deterministic_utf8_compact_and_newline_free():
    first = canonical_json_bytes({"z": 1, "a": "räv"})
    second = canonical_json_bytes({"a": "räv", "z": 1})
    assert first == second == b'{"a":"r\xc3\xa4v","z":1}'
    assert not first.endswith(b"\n")
    assert parse_canonical_json(first) == {"a": "räv", "z": 1}


@pytest.mark.parametrize("serialized", [
    b'{"a":1,"a":2}', b'{"value":1.5}', b'{"value":NaN}', b'{"value":Infinity}',
    b' {"a":1}', b'{"a": 1}', b'{"a":1}\n', b'\xef\xbb\xbf{"a":1}',
])
def test_strict_json_rejects_duplicates_floats_nonfinite_and_noncanonical_bytes(serialized):
    with pytest.raises(CacheEntryContractError):
        parse_canonical_json(serialized)


@pytest.mark.parametrize("value", [1.0, float("nan"), float("inf"), {"nested": [2.0]}])
def test_canonical_json_rejects_all_floats(value):
    with pytest.raises(CacheEntryContractError, match="float"):
        canonical_json_bytes(value)


def test_metadata_manifest_complete_and_runtime_fingerprint_round_trips():
    manifest_value = manifest(record("a.json", 1), record("b.json", 2))
    metadata_value = metadata(manifest_value)
    marker = CompletenessMarker(
        metadata_value.entry_digest,
        "sha256:" + hashlib.sha256(metadata_value.canonical_bytes()).hexdigest(),
        metadata_value.payload_manifest_digest,
    )
    assert CacheEntryMetadata.from_json(metadata_value.canonical_bytes()) == metadata_value
    assert PayloadManifest.from_json(manifest_value.canonical_bytes()) == manifest_value
    assert CompletenessMarker.from_json(marker.canonical_bytes()) == marker
    assert CacheRuntimeFingerprint.from_json(metadata_value.runtime_fingerprint.canonical_bytes()) == metadata_value.runtime_fingerprint
    assert CacheEntryContract(metadata_value, manifest_value).metadata == metadata_value


@pytest.mark.parametrize("model_factory", [
    lambda: metadata(),
    lambda: manifest(),
    lambda: CompletenessMarker(derive_entry_digest(key()), f"sha256:{SHA_A}", f"sha256:{SHA_B}"),
])
def test_unknown_fields_are_rejected_at_top_level(model_factory):
    value = model_factory()
    payload = value.to_dict()
    payload["future"] = True
    serialized = canonical_json_bytes(payload)
    with pytest.raises(CacheEntryContractError, match="Unknown"):
        type(value).from_json(serialized)


def test_nested_unknown_fields_are_rejected():
    payload = metadata().to_dict()
    payload["producer"]["future"] = True
    with pytest.raises(CacheEntryContractError, match="Unknown producer"):
        CacheEntryMetadata.from_json(canonical_json_bytes(payload))


@pytest.mark.parametrize("digest", ["sha256:" + "A" * 64, "sha1:" + SHA_A, "sha256:short", SHA_A])
def test_invalid_qualified_digest_syntax_is_rejected(digest):
    with pytest.raises(CacheEntryContractError):
        record().__class__("file.json", 1, digest, "application/json", "primary")


@pytest.mark.parametrize("timestamp", [
    "2026-07-20 09:00:00Z", "2026-07-20T09:00:00+00:00", "2026-02-30T09:00:00Z",
    "2026-07-20T09:00Z", "2026-07-20T09:00:00.000Z",
])
def test_invalid_timestamps_are_rejected(timestamp):
    with pytest.raises(CacheEntryContractError):
        metadata(created_at_utc=timestamp)


@pytest.mark.parametrize("path", [
    "/absolute.json", "../escape.json", "dir/../escape.json", "./file.json", "dir//file.json",
    "dir\\file.json", "nul\x00file.json", "dir/trailing. ", "dir/trailing.", "dir/",
])
def test_manifest_rejects_traversal_backslashes_and_nonportable_paths(path):
    with pytest.raises(CacheEntryContractError):
        record(path)


def test_manifest_rejects_duplicates_case_collisions_and_unsorted_paths():
    for records in (
        (record("a.json"), record("a.json")),
        (record("A.json"), record("a.json")),
        (record("b.json"), record("a.json")),
    ):
        with pytest.raises(CacheEntryContractError):
            PayloadManifest(records)


def test_metadata_rejects_entry_and_producer_identity_mismatches():
    with pytest.raises(CacheEntryContractError, match="entry_digest"):
        metadata(entry_digest="b" * 64)
    with pytest.raises(CacheEntryContractError, match="IDs"):
        metadata(producer=CacheProducerMetadata("other", "1.0", 3))
    with pytest.raises(CacheEntryContractError, match="schema"):
        metadata(producer=CacheProducerMetadata("transcription.whisper", "1.0", 4))


def test_aggregate_rejects_count_byte_total_and_manifest_digest_mismatches():
    value = manifest()
    for changed, message in (
        (metadata(value, payload_file_count=2), "count"),
        (metadata(value, payload_total_bytes=8), "byte total"),
        (metadata(value, payload_manifest_digest=f"sha256:{SHA_B}"), "digest"),
    ):
        with pytest.raises(CacheEntryContractError, match=message):
            CacheEntryContract(changed, value)


def test_physical_paths_are_rejected_from_identity_bearing_fields():
    for factory in (
        lambda: CacheArtifactMetadata("transcript", "/tmp/item", 1),
        lambda: CacheArtifactMetadata("C:\\kind", "logical", 1),
        lambda: CacheProducerMetadata("producer", "/tmp/version", 1),
        lambda: CacheRuntimeFingerprint(1, {"model": "/models/local"}),
        lambda: CacheRuntimeFingerprint(1, {"nested": {"model": "C:\\models\\local"}}),
    ):
        with pytest.raises(CacheEntryContractError, match="physical path"):
            factory()


def test_lookup_models_are_pure_and_enforce_identity():
    fingerprint = CacheRuntimeFingerprint(1, {"model": "large-v3"})
    expectation = CacheLookupExpectation(namespace(), "transcription.whisper", 3, fingerprint)
    assert expectation.runtime_fingerprint == fingerprint
    assert CacheLookupResult(CacheLookupStatus.MISS).status is CacheLookupStatus.MISS
    assert CACHE_LOOKUP_STATUS_PRECEDENCE[0] is CacheLookupStatus.UNSAFE_PATH
    assert CACHE_LOOKUP_STATUS_PRECEDENCE[-1] is CacheLookupStatus.HIT
    with pytest.raises(CacheEntryContractError, match="match namespace"):
        CacheLookupExpectation(namespace(), "other", 3, fingerprint)


def test_every_step5a_public_operation_avoids_filesystem_and_environment(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("Step 5A must not access the filesystem or environment")

    for name in ("open",):
        monkeypatch.setattr(builtins, name, forbidden)
    monkeypatch.setattr(os, "getenv", forbidden)
    monkeypatch.setattr(Path, "exists", forbidden)
    monkeypatch.setattr(Path, "resolve", forbidden)
    monkeypatch.setattr(Path, "mkdir", forbidden)
    before = tuple(tmp_path.iterdir())
    manifest_value = manifest()
    metadata_value = metadata(manifest_value)
    assert derive_final_entry_path(tmp_path, namespace(), key())
    assert derive_staging_entry_path(tmp_path, namespace(), key(), "writer")
    assert derive_lock_path(tmp_path, namespace(), key())
    assert CacheEntryMetadata.from_json(metadata_value.canonical_bytes()) == metadata_value
    assert PayloadManifest.from_json(manifest_value.canonical_bytes()) == manifest_value
    assert tuple(tmp_path.iterdir()) == before == ()


def test_contract_version_is_locked():
    assert CACHE_ENTRY_CONTRACT_VERSION == 1
    with pytest.raises(CacheEntryContractError, match="Unsupported"):
        replace(metadata(), cache_entry_contract_version=2)
