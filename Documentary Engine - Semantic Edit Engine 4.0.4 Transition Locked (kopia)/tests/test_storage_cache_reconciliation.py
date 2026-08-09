from dataclasses import FrozenInstanceError, replace
import hashlib
from pathlib import Path

import pytest

from engine.storage.cache_catalog import (
    CacheCatalogIdentity,
    CacheCatalogFinalProvenance,
    CacheCatalogLookupResult,
    CacheCatalogLookupStatus,
    CacheCatalogWriteResult,
    CacheCatalogWriteStatus,
    CacheCatalogLiveRecord,
    CacheCatalogRecoverySummary,
    CacheCatalogTombstone,
    CacheCatalogRecoveryProvenance,
    LocalCacheCatalogBackend,
    LocalCacheCatalogReadOnlyBackend,
    derive_catalog_record_relative_path,
    lookup_catalog_record,
)
from engine.storage.cache_keys import CacheKey
from engine.storage.cache_lookup import FilesystemObjectType
from engine.storage.cache_lookup import (
    CacheLookupReason,
    CacheLookupVerificationPolicy,
    CacheVerificationLevel,
    LockObservationPolicy,
    ProducerPayloadExpectation,
    ReadOnlyCacheLookupResult,
    ValidatedCacheEntryReference,
)
from engine.storage.cache_reconciliation import (
    MAX_RECONCILIATION_DIRECTORY_ENTRIES,
    CacheCatalogReconciliationCursor,
    CacheCatalogReconciliationMode,
    CacheCatalogReconciliationActionKind,
    CacheCatalogReconciliationActionReason,
    CacheCatalogReconciliationObservation,
    CacheCatalogReconciliationObservationScope,
    CatalogSlotClassification,
    DiscoveredCacheIdentity,
    LocalReconciliationReadOnlyFilesystem,
    ReconciliationDiscoveryError,
    ReconciliationDiscoveryLimitError,
    ReconciliationDiscoveryPage,
    ReconciliationDiscoveryPolicy,
    ReconciliationSourceFlags,
    ReconciliationResolvedExpectations,
    ReconciliationActionExecutionStatus,
    _DiscoveryBudget,
    _merged_identities,
    _validate_relative,
    discover_catalog_slots_page,
    discover_reconciliation_identities,
    iter_catalog_slots,
    iter_final_discovered_identities,
    iter_staging_discovered_identities,
    compare_reconciliation_observation,
    compare_reconciliation_observations,
    observe_and_compare_reconciliation_identity,
    execute_reconciliation_action,
)
from engine.storage.cache_recovery import (
    CacheRecoveryInspectionRequest,
    CacheRecoveryObservation,
    CacheRecoveryReason,
    CacheRecoveryStatus,
    FinalRecoveryObservation,
    FinalRecoveryState,
    LockRecoveryObservation,
    LockRecoveryState,
    StagingRecoveryObservation,
    StagingRecoveryState,
)
from engine.storage.persistent_cache import (
    CacheArtifactMetadata,
    CacheEntryMetadata,
    CacheKeyReference,
    CacheNamespace,
    CacheProducerMetadata,
    CacheRuntimeFingerprint,
    CacheLookupExpectation,
    CacheLookupStatus,
    CompletenessMarker,
    PayloadManifest,
    PayloadManifestRecord,
    CACHE_ENTRY_CONTRACT_VERSION,
    canonical_json_bytes,
    derive_entry_digest,
    derive_final_entry_path,
    derive_staging_entry_path,
)


def _metadata(key, namespace):
    return CacheEntryMetadata(
        derive_entry_digest(key),
        CacheKeyReference.from_cache_key(key),
        namespace,
        CacheArtifactMetadata("transcript", "private-id", 1),
        CacheProducerMetadata(namespace.producer_id, "1.0.0", namespace.producer_schema_version),
        CacheRuntimeFingerprint(1, {"model": "test"}),
        "2026-08-10T00:00:00Z",
        "sha256:" + "b" * 64,
        0,
        0,
    )


def _root(tmp_path):
    root = tmp_path / "cache"
    root.mkdir(parents=True)
    for relative in ("entries/v1", "staging/v1", "catalog/v1/records"):
        (root / relative).mkdir(parents=True)
    filesystem = LocalReconciliationReadOnlyFilesystem.from_root(root)
    backend = LocalCacheCatalogReadOnlyBackend.from_root(root)
    return root, filesystem, backend


def _final(root, namespace, key, *, metadata=None):
    path = derive_final_entry_path(root, namespace, key)
    path.mkdir(parents=True)
    path.joinpath("metadata.json").write_bytes((metadata or _metadata(key, namespace)).canonical_bytes())
    return path


def _staging(root, namespace, key, token, *, metadata=None):
    path = derive_staging_entry_path(root, namespace, key, token)
    path.mkdir(parents=True)
    path.joinpath("metadata.json").write_bytes((metadata or _metadata(key, namespace)).canonical_bytes())
    return path


def _catalog_record(root, record):
    relative = derive_catalog_record_relative_path(record.identity)
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(record.canonical_bytes())
    return path


def _identity(namespace, key):
    return CacheCatalogIdentity(
        namespace,
        derive_entry_digest(key),
        CacheKeyReference.from_cache_key(key),
    )


def _live(identity):
    summary = CacheCatalogRecoverySummary(
        CacheRecoveryStatus.EMPTY,
        None,
        0,
        FinalRecoveryState.FINAL_ABSENT,
        LockRecoveryState.LOCK_ABSENT,
        CacheCatalogRecoveryProvenance.STEP5E_OBSERVATION,
    )
    return CacheCatalogLiveRecord(
        identity=identity,
        record_revision=1,
        last_validated_final=None,
        last_recovery_observation=summary,
    )


def test_h2a_policy_limits_and_immutability():
    policy = ReconciliationDiscoveryPolicy()
    assert (
        policy.max_identities_per_run,
        policy.max_directory_listings,
        policy.max_entries_per_directory,
        policy.page_size,
        policy.max_relative_path_utf8_bytes,
        policy.max_traversal_depth,
    ) == (1024, 4096, 4096, 256, 1024, 64)
    with pytest.raises(FrozenInstanceError):
        policy.page_size = 1
    for changes in (
        {"page_size": 0},
        {"page_size": 257},
        {"page_size": True},
        {"max_identities_per_run": 1025},
        {"max_directory_listings": 4097},
        {"max_entries_per_directory": 4097},
        {"max_relative_path_utf8_bytes": 1025},
        {"max_traversal_depth": 65},
    ):
        with pytest.raises(ValueError):
            ReconciliationDiscoveryPolicy(**changes)


def test_h2a_cursor_is_path_and_token_free():
    namespace = CacheNamespace("audio", "producer", 2)
    policy = ReconciliationDiscoveryPolicy(page_size=1)
    cursor = CacheCatalogReconciliationCursor(
        CacheCatalogReconciliationMode.FULL_IN_PLACE,
        namespace,
        "a" * 64,
        policy.digest,
    )
    assert set(cursor.__dataclass_fields__) == {
        "mode", "last_namespace", "last_entry_digest", "policy_digest",
        "final_complete", "staging_complete", "catalog_complete", "cursor_version",
    }
    assert not any(term in repr(cursor).lower() for term in ("writer", "/users/", "inode"))


def test_h2a_discovers_one_strict_final_identity(tmp_path):
    root, filesystem, _ = _root(tmp_path)
    namespace = CacheNamespace("audio", "producer", 2)
    key = CacheKey("a" * 64)
    _final(root, namespace, key)
    items = tuple(iter_final_discovered_identities(filesystem))
    assert items == (
        DiscoveredCacheIdentity(_identity(namespace, key), ReconciliationSourceFlags.FINAL),
    )


def test_h2a_final_discovery_orders_namespace_and_numeric_schema(tmp_path):
    root, filesystem, _ = _root(tmp_path)
    cases = [
        (CacheNamespace("video", "p", 1), CacheKey("4" * 64)),
        (CacheNamespace("audio", "q", 1), CacheKey("3" * 64)),
        (CacheNamespace("audio", "p", 10), CacheKey("2" * 64)),
        (CacheNamespace("audio", "p", 2), CacheKey("1" * 64)),
    ]
    for namespace, key in cases:
        _final(root, namespace, key)
    actual = [item.identity for item in iter_final_discovered_identities(filesystem)]
    assert actual == sorted((_identity(ns, key) for ns, key in cases), key=lambda item: item.sort_key)


def test_h2a_final_probe_rejects_namespace_digest_shard_and_metadata_failures(tmp_path):
    root, filesystem, _ = _root(tmp_path)
    namespace = CacheNamespace("audio", "producer", 1)
    other = CacheNamespace("video", "producer", 1)
    key = CacheKey("a" * 64)
    _final(root, namespace, key, metadata=_metadata(key, other))
    malformed = derive_final_entry_path(root, namespace, CacheKey("b" * 64))
    malformed.mkdir(parents=True)
    malformed.joinpath("metadata.json").write_bytes(b"{}")
    missing = derive_final_entry_path(root, namespace, CacheKey("c" * 64))
    missing.mkdir(parents=True)
    wrong_shard = root / "entries/v1/audio/producer/1/ff/ff" / ("d" * 64)
    wrong_shard.mkdir(parents=True)
    wrong_shard.joinpath("metadata.json").write_bytes(
        _metadata(CacheKey("d" * 64), namespace).canonical_bytes()
    )
    assert tuple(iter_final_discovered_identities(filesystem)) == ()


def test_h2a_staging_coalesces_tokens_and_never_exposes_them(tmp_path):
    root, filesystem, _ = _root(tmp_path)
    namespace = CacheNamespace("audio", "producer", 1)
    key = CacheKey("a" * 64)
    _staging(root, namespace, key, "writer-one")
    _staging(root, namespace, key, "writer-two")
    items = tuple(iter_staging_discovered_identities(filesystem))
    assert len(items) == 1
    assert items[0].identity == _identity(namespace, key)
    assert items[0].sources is ReconciliationSourceFlags.STAGING
    assert "writer" not in repr(items[0]).lower()


def test_h2a_staging_rejects_bad_token_digest_metadata_and_missing_metadata(tmp_path):
    root, filesystem, _ = _root(tmp_path)
    namespace = CacheNamespace("audio", "producer", 1)
    key = CacheKey("a" * 64)
    schema = root / "staging/v1/audio/producer/1"
    schema.mkdir(parents=True)
    (schema / ("a" * 64 + ".bad..token")).mkdir()
    mismatch = schema / ("b" * 64 + ".token")
    mismatch.mkdir()
    mismatch.joinpath("metadata.json").write_bytes(_metadata(key, namespace).canonical_bytes())
    (schema / ("c" * 64 + ".token")).mkdir()
    assert tuple(iter_staging_discovered_identities(filesystem)) == ()


def test_h2a_catalog_slots_support_live_tombstone_corrupt_and_unsupported(tmp_path):
    root, _, backend = _root(tmp_path)
    namespace = CacheNamespace("audio", "producer", 1)
    identities = [_identity(namespace, CacheKey(f"{value:064x}")) for value in range(1, 5)]
    _catalog_record(root, _live(identities[0]))
    _catalog_record(root, CacheCatalogTombstone(identity=identities[1], record_revision=2))
    corrupt = root / derive_catalog_record_relative_path(identities[2])
    corrupt.parent.mkdir(parents=True, exist_ok=True)
    corrupt.write_bytes(b"{}")
    future = CacheCatalogTombstone(identity=identities[3], record_revision=1).to_dict()
    future["catalog_record_version"] = 2
    unsupported = root / derive_catalog_record_relative_path(identities[3])
    unsupported.parent.mkdir(parents=True, exist_ok=True)
    unsupported.write_bytes(canonical_json_bytes(future))
    slots = tuple(iter_catalog_slots(backend))
    by_digest = {slot.entry_digest: slot for slot in slots}
    assert by_digest[identities[0].entry_digest].classification is CatalogSlotClassification.SUPPORTED_LIVE
    assert by_digest[identities[1].entry_digest].classification is CatalogSlotClassification.SUPPORTED_TOMBSTONE
    assert by_digest[identities[2].entry_digest].classification is CatalogSlotClassification.CORRUPT
    assert by_digest[identities[3].entry_digest].classification is CatalogSlotClassification.UNSUPPORTED
    assert by_digest[identities[0].entry_digest].trusted_identity == identities[0]
    assert by_digest[identities[1].entry_digest].trusted_identity == identities[1]
    assert by_digest[identities[2].entry_digest].trusted_identity is None
    assert by_digest[identities[3].entry_digest].trusted_identity is None


def test_h2a_catalog_cursor_advances_past_corrupt_slot(tmp_path):
    root, _, backend = _root(tmp_path)
    namespace = CacheNamespace("audio", "producer", 1)
    identities = [_identity(namespace, CacheKey(f"{value:064x}")) for value in range(1, 4)]
    _catalog_record(root, CacheCatalogTombstone(identity=identities[0], record_revision=1))
    corrupt = root / derive_catalog_record_relative_path(identities[1])
    corrupt.parent.mkdir(parents=True, exist_ok=True)
    corrupt.write_bytes(b"bad")
    _catalog_record(root, CacheCatalogTombstone(identity=identities[2], record_revision=1))
    policy = ReconciliationDiscoveryPolicy(page_size=1)
    pages = []
    cursor = None
    while True:
        page = discover_catalog_slots_page(backend, policy=policy, cursor=cursor)
        pages.extend(page.catalog_slots)
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
    assert [slot.sort_key for slot in pages] == sorted(slot.sort_key for slot in pages)
    corrupt_index = next(
        index for index, slot in enumerate(pages)
        if slot.classification is CatalogSlotClassification.CORRUPT
    )
    assert corrupt_index < len(pages) - 1 or corrupt_index > 0
    assert {slot.trusted_identity for slot in pages if slot.trusted_identity} == {
        identities[0], identities[2]
    }


def test_h2a_three_way_union_coalesces_source_flags(tmp_path):
    root, filesystem, backend = _root(tmp_path)
    namespace = CacheNamespace("audio", "producer", 1)
    keys = [CacheKey(f"{value:064x}") for value in range(1, 8)]
    _final(root, namespace, keys[0])
    _staging(root, namespace, keys[1], "one")
    _catalog_record(root, CacheCatalogTombstone(identity=_identity(namespace, keys[2]), record_revision=1))
    _final(root, namespace, keys[3]); _staging(root, namespace, keys[3], "both")
    _final(root, namespace, keys[4]); _catalog_record(root, CacheCatalogTombstone(identity=_identity(namespace, keys[4]), record_revision=1))
    _staging(root, namespace, keys[5], "both"); _catalog_record(root, CacheCatalogTombstone(identity=_identity(namespace, keys[5]), record_revision=1))
    _final(root, namespace, keys[6]); _staging(root, namespace, keys[6], "all"); _catalog_record(root, CacheCatalogTombstone(identity=_identity(namespace, keys[6]), record_revision=1))
    page = discover_reconciliation_identities(filesystem, catalog_backend=backend)
    expected = {
        _identity(namespace, keys[0]): ReconciliationSourceFlags.FINAL,
        _identity(namespace, keys[1]): ReconciliationSourceFlags.STAGING,
        _identity(namespace, keys[2]): ReconciliationSourceFlags.CATALOG,
        _identity(namespace, keys[3]): ReconciliationSourceFlags.FINAL | ReconciliationSourceFlags.STAGING,
        _identity(namespace, keys[4]): ReconciliationSourceFlags.FINAL | ReconciliationSourceFlags.CATALOG,
        _identity(namespace, keys[5]): ReconciliationSourceFlags.STAGING | ReconciliationSourceFlags.CATALOG,
        _identity(namespace, keys[6]): ReconciliationSourceFlags.FINAL | ReconciliationSourceFlags.STAGING | ReconciliationSourceFlags.CATALOG,
    }
    assert [item.identity for item in page.identities] == sorted(expected, key=lambda item: item.sort_key)
    assert {item.identity: item.sources for item in page.identities} == expected


def test_h2a_page_cursor_is_exclusive_and_policy_bound(tmp_path):
    root, filesystem, backend = _root(tmp_path)
    namespace = CacheNamespace("audio", "producer", 1)
    keys = [CacheKey(f"{value:064x}") for value in range(1, 4)]
    for key in keys:
        _final(root, namespace, key)
    policy = ReconciliationDiscoveryPolicy(page_size=1)
    first = discover_reconciliation_identities(filesystem, catalog_backend=backend, policy=policy)
    second = discover_reconciliation_identities(filesystem, catalog_backend=backend, policy=policy, cursor=first.next_cursor)
    ordered = sorted((_identity(namespace, key) for key in keys), key=lambda item: item.sort_key)
    assert first.identities[0].identity == ordered[0]
    assert second.identities[0].identity == ordered[1]
    with pytest.raises(ValueError, match="policy"):
        discover_reconciliation_identities(
            filesystem,
            catalog_backend=backend,
            policy=ReconciliationDiscoveryPolicy(page_size=2),
            cursor=first.next_cursor,
        )


def test_h2a_directory_listing_exact_limit_and_one_over(tmp_path):
    root, filesystem, _ = _root(tmp_path)
    directory = root / "entries/v1"
    for value in range(3):
        (directory / str(value)).mkdir()
    accepted = filesystem.list_relative_bounded(Path("entries/v1"), max_entries=3)
    assert len(accepted.names) == 3 and not accepted.limit_exceeded
    rejected = filesystem.list_relative_bounded(Path("entries/v1"), max_entries=2)
    assert len(rejected.names) == 3 and rejected.limit_exceeded


def test_h2a_path_and_depth_limits_exact_and_one_over():
    exact_depth = Path(*(["a"] * 64))
    _validate_relative(exact_depth, ReconciliationDiscoveryPolicy())
    with pytest.raises(ReconciliationDiscoveryLimitError):
        _validate_relative(Path(*(["a"] * 65)), ReconciliationDiscoveryPolicy())
    exact_bytes = Path("a" * 1024)
    _validate_relative(exact_bytes, ReconciliationDiscoveryPolicy())
    with pytest.raises(ReconciliationDiscoveryLimitError):
        _validate_relative(Path("a" * 1025), ReconciliationDiscoveryPolicy())


def test_h2a_listing_budget_fails_before_one_over():
    budget = _DiscoveryBudget(ReconciliationDiscoveryPolicy(max_directory_listings=1))
    budget.consume_listing()
    with pytest.raises(ReconciliationDiscoveryLimitError):
        budget.consume_listing()


def test_h2a_rejects_mutation_capable_dependencies(tmp_path):
    root, filesystem, _ = _root(tmp_path)

    class MutationSurface:
        cache_root = filesystem.cache_root
        inspect_root = filesystem.inspect_root
        inspect_relative = filesystem.inspect_relative
        list_relative_bounded = filesystem.list_relative_bounded
        read_relative_bounded = filesystem.read_relative_bounded

        def unlink(self):
            raise AssertionError("must not be called")

    with pytest.raises(TypeError, match="mutation-capable"):
        tuple(iter_final_discovered_identities(MutationSurface()))
    with pytest.raises(TypeError, match="mutation-capable"):
        tuple(iter_catalog_slots(LocalCacheCatalogBackend.from_root(root)))


def test_h2a_root_boundary_never_lists_locks_future_or_unrelated(tmp_path):
    root = tmp_path / "cache"
    root.mkdir()
    for relative in ("entries/v1", "staging/v1", "catalog/v1/records"):
        (root / relative).mkdir(parents=True)
    for relative in ("locks/v1", "entries/v2", "staging/v2", "catalog/v2", "unrelated"):
        path = root / relative
        path.mkdir(parents=True)
        path.joinpath("tripwire").write_text("untouched")
    filesystem = LocalReconciliationReadOnlyFilesystem.from_root(root)
    backend = LocalCacheCatalogReadOnlyBackend.from_root(root)
    page = discover_reconciliation_identities(filesystem, catalog_backend=backend)
    assert page.identities == ()
    for relative in ("locks/v1", "entries/v2", "staging/v2", "catalog/v2", "unrelated"):
        assert (root / relative / "tripwire").read_text() == "untouched"


def test_h2a_models_never_claim_snapshot_or_mutation_authority():
    page = ReconciliationDiscoveryPage(())
    assert page.is_snapshot is False
    names = set(page.__dataclass_fields__)
    assert not names & {"action", "write", "checkpoint", "dry_run", "cleanup"}


def _h2b_evidence(tmp_path):
    root = tmp_path / "h2b-cache"
    root.mkdir()
    key = CacheKey("9" * 64)
    namespace = CacheNamespace("audio", "producer", 1)
    manifest = PayloadManifest((PayloadManifestRecord(
        "artifact.json", 7, "sha256:" + "b" * 64, "application/json", "primary"
    ),))
    metadata = CacheEntryMetadata(
        derive_entry_digest(key), CacheKeyReference.from_cache_key(key), namespace,
        CacheArtifactMetadata("transcript", "private", 1),
        CacheProducerMetadata("producer", "1.0.0", 1),
        CacheRuntimeFingerprint(1, {"model": "test"}), "2026-08-10T00:00:00Z",
        "sha256:" + hashlib.sha256(manifest.canonical_bytes()).hexdigest(), 1, 7,
    )
    marker = CompletenessMarker(
        metadata.entry_digest,
        "sha256:" + hashlib.sha256(metadata.canonical_bytes()).hexdigest(),
        metadata.payload_manifest_digest,
    )
    reference = ValidatedCacheEntryReference(
        root / "entry", metadata.entry_digest, namespace, metadata.cache_key,
        metadata, manifest, marker, CacheVerificationLevel.FULL_PAYLOAD_SHA256,
    )
    lookup = ReadOnlyCacheLookupResult(
        CacheLookupStatus.HIT, None, reference.entry_path, reference,
        metadata.entry_digest, namespace, metadata.cache_key, (),
        CacheVerificationLevel.FULL_PAYLOAD_SHA256, CACHE_ENTRY_CONTRACT_VERSION,
        True, metadata, manifest, marker,
    )
    identity = CacheCatalogIdentity(namespace, metadata.entry_digest, metadata.cache_key)
    return root, identity, DiscoveredCacheIdentity(identity, ReconciliationSourceFlags.FINAL), lookup


def _nonhit(identity, status=CacheLookupStatus.MISS, reason=None):
    return ReadOnlyCacheLookupResult(
        status, reason, Path("expected"), None, identity.entry_digest,
        identity.namespace, identity.cache_key_reference, (), CacheVerificationLevel.NONE,
        None, False, None, None, None,
    )


def _recovery(identity, status=CacheRecoveryStatus.EMPTY, reason=None):
    return CacheRecoveryObservation(
        identity.entry_digest,
        (StagingRecoveryObservation(0, "staging/v1/item", StagingRecoveryState.STAGING_ABSENT),),
        FinalRecoveryObservation("entries/v1/item", FinalRecoveryState.FINAL_ABSENT),
        LockRecoveryObservation("locks/v1/item", LockRecoveryState.LOCK_ABSENT),
        status, reason,
    )


def _observation(
    identity, lookup, catalog, recovery=None,
    sources=ReconciliationSourceFlags.FINAL, recovery_request=None,
):
    return CacheCatalogReconciliationObservation(
        identity, sources, lookup, recovery, catalog, recovery_request
    )


def _recovery_request(root, identity):
    return CacheRecoveryInspectionRequest(
        LocalReconciliationReadOnlyFilesystem.from_root(root).cache_root,
        identity.namespace,
        identity.cache_key_reference.to_cache_key(),
        CacheLookupExpectation(
            identity.namespace, identity.namespace.producer_id,
            identity.namespace.producer_schema_version,
            CacheRuntimeFingerprint(1, {"model": "test"}),
        ),
        None,
        ProducerPayloadExpectation(),
        CacheLookupVerificationPolicy(),
        LockObservationPolicy(60),
    )


def test_h2b_absent_hit_upserts_final_and_equal_summary_noops(tmp_path):
    _, identity, _, lookup = _h2b_evidence(tmp_path)
    absent = CacheCatalogLookupResult(CacheCatalogLookupStatus.RECORD_ABSENT)
    upsert = compare_reconciliation_observation(_observation(identity, lookup, absent))
    assert upsert.kind is CacheCatalogReconciliationActionKind.UPSERT_FINAL
    record = CacheCatalogLiveRecord(
        identity=identity, record_revision=4,
        last_validated_final=upsert.final_summary, last_recovery_observation=None,
    )
    noop = compare_reconciliation_observation(_observation(
        identity, lookup, CacheCatalogLookupResult(CacheCatalogLookupStatus.RECORD_FOUND, record)
    ))
    assert (noop.kind, noop.reason, noop.expected_catalog_revision) == (
        CacheCatalogReconciliationActionKind.NOOP,
        CacheCatalogReconciliationActionReason.SUMMARIES_MATCH, 4,
    )


def test_h2b_final_precedes_recovery_and_recovery_can_upsert(tmp_path):
    _, identity, _, lookup = _h2b_evidence(tmp_path)
    recovery = _recovery(identity, CacheRecoveryStatus.FINAL_PUBLISHED)
    absent = CacheCatalogLookupResult(CacheCatalogLookupStatus.RECORD_ABSENT)
    final = compare_reconciliation_observation(_observation(identity, lookup, absent, recovery))
    assert final.kind is CacheCatalogReconciliationActionKind.UPSERT_FINAL
    recovery_action = compare_reconciliation_observation(_observation(identity, _nonhit(identity), absent, recovery))
    assert recovery_action.kind is CacheCatalogReconciliationActionKind.UPSERT_RECOVERY
    record = CacheCatalogLiveRecord(
        identity=identity, record_revision=2, last_validated_final=None,
        last_recovery_observation=recovery_action.recovery_summary,
    )
    matched = compare_reconciliation_observation(_observation(
        identity, _nonhit(identity), CacheCatalogLookupResult(CacheCatalogLookupStatus.RECORD_FOUND, record), recovery
    ))
    assert matched.kind is CacheCatalogReconciliationActionKind.NOOP


def test_h2b_exact_empty_tombstones_or_matches_tombstone(tmp_path):
    _, identity, _, _ = _h2b_evidence(tmp_path)
    empty = _recovery(identity)
    absent = CacheCatalogLookupResult(CacheCatalogLookupStatus.RECORD_ABSENT)
    action = compare_reconciliation_observation(_observation(identity, _nonhit(identity), absent, empty))
    assert action.kind is CacheCatalogReconciliationActionKind.NOOP
    live = CacheCatalogLookupResult(CacheCatalogLookupStatus.RECORD_FOUND, _live(identity))
    action = compare_reconciliation_observation(_observation(identity, _nonhit(identity), live, empty))
    assert action.kind is CacheCatalogReconciliationActionKind.TOMBSTONE_EMPTY
    assert action.expected_catalog_revision == 1
    existing = CacheCatalogLookupResult(CacheCatalogLookupStatus.RECORD_ABSENT, tombstone_revision=7)
    noop = compare_reconciliation_observation(_observation(identity, _nonhit(identity), existing, empty))
    assert noop.kind is CacheCatalogReconciliationActionKind.NOOP
    unavailable = compare_reconciliation_observation(_observation(
        identity, _nonhit(identity),
        CacheCatalogLookupResult(CacheCatalogLookupStatus.CATALOG_UNAVAILABLE), empty,
    ))
    assert unavailable.kind is CacheCatalogReconciliationActionKind.NOOP


@pytest.mark.parametrize("status,kind", [
    (CacheCatalogLookupStatus.CATALOG_CORRUPT, CacheCatalogReconciliationActionKind.REPORT_ONLY),
    (CacheCatalogLookupStatus.CATALOG_UNSUPPORTED, CacheCatalogReconciliationActionKind.REPORT_ONLY),
    (CacheCatalogLookupStatus.CATALOG_UNSAFE, CacheCatalogReconciliationActionKind.REPORT_ONLY),
    (CacheCatalogLookupStatus.CATALOG_UNSTABLE, CacheCatalogReconciliationActionKind.DEFER),
    (CacheCatalogLookupStatus.CATALOG_IO_FAILURE, CacheCatalogReconciliationActionKind.DEFER),
])
def test_h2b_catalog_failure_precedence_never_proposes_mutation(tmp_path, status, kind):
    _, identity, _, lookup = _h2b_evidence(tmp_path)
    action = compare_reconciliation_observation(_observation(identity, lookup, CacheCatalogLookupResult(status)))
    assert action.kind is kind
    assert action.final_summary is None and action.recovery_summary is None


@pytest.mark.parametrize("status,reason,kind", [
    (CacheLookupStatus.UNSUPPORTED_VERSION, CacheLookupReason.UNSUPPORTED_ENTRY_VERSION, CacheCatalogReconciliationActionKind.REPORT_ONLY),
    (CacheLookupStatus.UNSAFE_PATH, CacheLookupReason.UNSAFE_PATH, CacheCatalogReconciliationActionKind.REPORT_ONLY),
    (CacheLookupStatus.INVALID_ENTRY, CacheLookupReason.IO_FAILURE, CacheCatalogReconciliationActionKind.DEFER),
    (CacheLookupStatus.INVALID_ENTRY, CacheLookupReason.MALFORMED_METADATA, CacheCatalogReconciliationActionKind.REPORT_ONLY),
    (CacheLookupStatus.LOCKED_OR_IN_PROGRESS, None, CacheCatalogReconciliationActionKind.DEFER),
])
def test_h2b_authoritative_failures_are_descriptive(tmp_path, status, reason, kind):
    _, identity, _, _ = _h2b_evidence(tmp_path)
    action = compare_reconciliation_observation(_observation(
        identity, _nonhit(identity, status, reason),
        CacheCatalogLookupResult(CacheCatalogLookupStatus.RECORD_ABSENT),
        None if status is CacheLookupStatus.LOCKED_OR_IN_PROGRESS else _recovery(identity),
    ))
    assert action.kind is kind


@pytest.mark.parametrize("status,reason,kind", [
    (CacheRecoveryStatus.RECOVERY_UNSAFE, CacheRecoveryReason.UNSAFE_ROOT, CacheCatalogReconciliationActionKind.REPORT_ONLY),
    (CacheRecoveryStatus.RECOVERY_UNSUPPORTED, CacheRecoveryReason.UNSUPPORTED_FINAL, CacheCatalogReconciliationActionKind.REPORT_ONLY),
    (CacheRecoveryStatus.RECOVERY_INVALID, CacheRecoveryReason.INVALID_FINAL, CacheCatalogReconciliationActionKind.REPORT_ONLY),
    (CacheRecoveryStatus.RECOVERY_UNSTABLE, CacheRecoveryReason.UNSTABLE_ROOT, CacheCatalogReconciliationActionKind.DEFER),
])
def test_h2b_recovery_failures_never_authorize_actions(tmp_path, status, reason, kind):
    _, identity, _, _ = _h2b_evidence(tmp_path)
    action = compare_reconciliation_observation(_observation(
        identity, _nonhit(identity), CacheCatalogLookupResult(CacheCatalogLookupStatus.RECORD_ABSENT),
        _recovery(identity, status, reason),
    ))
    assert action.kind is kind
    assert action.final_summary is None and action.recovery_summary is None


def test_h2b_action_batch_is_canonical_bounded_and_unique(tmp_path):
    _, identity, _, _ = _h2b_evidence(tmp_path)
    other = _identity(CacheNamespace("video", "producer", 1), CacheKey("8" * 64))
    catalog = CacheCatalogLookupResult(CacheCatalogLookupStatus.RECORD_ABSENT)
    observations = (
        _observation(other, _nonhit(other), catalog),
        _observation(identity, _nonhit(identity), catalog),
    )
    actions = compare_reconciliation_observations(observations)
    assert [item.identity for item in actions] == sorted((identity, other), key=lambda item: item.sort_key)
    with pytest.raises(ValueError, match="unique"):
        compare_reconciliation_observations((observations[0], observations[0]))


def test_h2b_observer_schedules_each_source_once_and_skips_unneeded_recovery(tmp_path):
    root, identity, discovered, lookup = _h2b_evidence(tmp_path)
    resolved = ReconciliationResolvedExpectations(
        CacheLookupExpectation(identity.namespace, "producer", 1, CacheRuntimeFingerprint(1, {"model": "test"})),
        None, ProducerPayloadExpectation(), CacheLookupVerificationPolicy(), LockObservationPolicy(60),
    )
    class Resolver:
        def resolve(self, requested): return resolved
    calls = []
    common = dict(
        cache_root=LocalReconciliationReadOnlyFilesystem.from_root(root).cache_root,
        expectation_resolver=Resolver(), lookup_filesystem=object(), recovery_filesystem=object(), catalog_backend=object(),
        lookup_operation=lambda request, **kwargs: (calls.append("lookup") or lookup),
        recovery_operation=lambda request, **kwargs: (calls.append("recovery") or _recovery(identity)),
        catalog_operation=lambda requested, **kwargs: (calls.append("catalog") or CacheCatalogLookupResult(CacheCatalogLookupStatus.RECORD_ABSENT)),
    )
    action = observe_and_compare_reconciliation_identity(discovered, **common)
    assert calls == ["lookup", "catalog"] and action.kind is CacheCatalogReconciliationActionKind.UPSERT_FINAL
    calls.clear()
    staged = replace(discovered, sources=ReconciliationSourceFlags.FINAL | ReconciliationSourceFlags.STAGING)
    observe_and_compare_reconciliation_identity(staged, **common)
    assert calls == ["lookup", "recovery", "catalog"]


def test_h2b_missing_expectations_defers_without_any_dependency_call(tmp_path):
    root, _, discovered, _ = _h2b_evidence(tmp_path)
    class Resolver:
        def resolve(self, requested): return None
    action = observe_and_compare_reconciliation_identity(
        discovered, cache_root=LocalReconciliationReadOnlyFilesystem.from_root(root).cache_root,
        expectation_resolver=Resolver(), lookup_filesystem=object(), recovery_filesystem=object(), catalog_backend=object(),
    )
    assert action.kind is CacheCatalogReconciliationActionKind.DEFER


def test_h2c_no_write_actions_preserve_reason_and_never_touch_backend(tmp_path):
    root, identity, _, lookup = _h2b_evidence(tmp_path)
    empty = _recovery(identity)
    request = _recovery_request(root, identity)
    noop = compare_reconciliation_observation(_observation(
        identity, _nonhit(identity), CacheCatalogLookupResult(CacheCatalogLookupStatus.RECORD_ABSENT),
        empty, recovery_request=request,
    ))
    deferred = compare_reconciliation_observation(
        CacheCatalogReconciliationObservation(
            identity, ReconciliationSourceFlags.FINAL, None, None, None
        )
    )
    report = compare_reconciliation_observation(_observation(
        identity, lookup, CacheCatalogLookupResult(CacheCatalogLookupStatus.CATALOG_CORRUPT)
    ))
    results = [execute_reconciliation_action(item, backend=None) for item in (noop, deferred, report)]
    assert [item.status for item in results] == [
        ReconciliationActionExecutionStatus.NO_CHANGE,
        ReconciliationActionExecutionStatus.DEFERRED,
        ReconciliationActionExecutionStatus.REPORT_ONLY,
    ]
    assert [item.action.reason for item in results] == [item.reason for item in (noop, deferred, report)]


def test_h2c_upsert_final_absent_creates_revision_one(tmp_path):
    root, identity, _, lookup = _h2b_evidence(tmp_path)
    backend = LocalCacheCatalogBackend.from_root(root)
    action = compare_reconciliation_observation(_observation(
        identity, lookup, CacheCatalogLookupResult(CacheCatalogLookupStatus.RECORD_ABSENT)
    ))
    result = execute_reconciliation_action(action, backend=backend)
    assert (result.status, result.applied_revision) == (
        ReconciliationActionExecutionStatus.APPLIED, 1
    )
    stored = lookup_catalog_record(identity, backend=backend)
    assert stored.record is not None and stored.record.last_validated_final == action.final_summary


def test_h2c_upsert_recovery_create_update_and_preserve_final(tmp_path):
    root, identity, _, lookup = _h2b_evidence(tmp_path)
    backend = LocalCacheCatalogBackend.from_root(root)
    final_action = compare_reconciliation_observation(_observation(
        identity, lookup, CacheCatalogLookupResult(CacheCatalogLookupStatus.RECORD_ABSENT)
    ))
    execute_reconciliation_action(final_action, backend=backend)
    current = lookup_catalog_record(identity, backend=backend)
    recovery = _recovery(identity, CacheRecoveryStatus.FINAL_PUBLISHED)
    request = _recovery_request(root, identity)
    recovery_action = compare_reconciliation_observation(_observation(
        identity, _nonhit(identity), current, recovery, recovery_request=request,
    ))
    result = execute_reconciliation_action(recovery_action, backend=backend)
    assert (result.status, result.applied_revision) == (
        ReconciliationActionExecutionStatus.APPLIED, 2
    )
    stored = lookup_catalog_record(identity, backend=backend)
    assert stored.record is not None
    assert stored.record.last_validated_final == final_action.final_summary
    assert stored.record.last_recovery_observation == recovery_action.recovery_summary


def test_h2c_empty_live_tombstones_with_positive_revision_only(tmp_path):
    root, identity, _, _ = _h2b_evidence(tmp_path)
    backend = LocalCacheCatalogBackend.from_root(root)
    request = _recovery_request(root, identity)
    published = _recovery(identity, CacheRecoveryStatus.FINAL_PUBLISHED)
    create = compare_reconciliation_observation(_observation(
        identity, _nonhit(identity), CacheCatalogLookupResult(CacheCatalogLookupStatus.RECORD_ABSENT),
        published, recovery_request=request,
    ))
    execute_reconciliation_action(create, backend=backend)
    current = lookup_catalog_record(identity, backend=backend)
    empty = _recovery(identity)
    tombstone = compare_reconciliation_observation(_observation(
        identity, _nonhit(identity), current, empty, recovery_request=request,
    ))
    assert tombstone.expected_catalog_revision == 1
    result = execute_reconciliation_action(tombstone, backend=backend)
    assert (result.status, result.applied_revision) == (
        ReconciliationActionExecutionStatus.APPLIED, 2
    )
    stored = lookup_catalog_record(identity, backend=backend)
    assert stored.status is CacheCatalogLookupStatus.RECORD_ABSENT
    assert stored.tombstone_revision == 2


@pytest.mark.parametrize("write_status,execution_status", [
    (CacheCatalogWriteStatus.CATALOG_WRITE_CONFLICT, ReconciliationActionExecutionStatus.REVISION_CONFLICT),
    (CacheCatalogWriteStatus.CATALOG_WRITE_UNAVAILABLE, ReconciliationActionExecutionStatus.CATALOG_FAILURE),
    (CacheCatalogWriteStatus.CATALOG_WRITE_CORRUPT, ReconciliationActionExecutionStatus.CATALOG_FAILURE),
    (CacheCatalogWriteStatus.CATALOG_WRITE_UNSUPPORTED, ReconciliationActionExecutionStatus.CATALOG_FAILURE),
    (CacheCatalogWriteStatus.CATALOG_WRITE_UNSAFE, ReconciliationActionExecutionStatus.CATALOG_FAILURE),
    (CacheCatalogWriteStatus.CATALOG_WRITE_UNSTABLE, ReconciliationActionExecutionStatus.CATALOG_FAILURE),
    (CacheCatalogWriteStatus.CATALOG_WRITE_IO_FAILURE, ReconciliationActionExecutionStatus.CATALOG_FAILURE),
])
def test_h2c_maps_h1_write_failures_once_without_retry(tmp_path, write_status, execution_status):
    _, identity, _, lookup = _h2b_evidence(tmp_path)
    action = compare_reconciliation_observation(_observation(
        identity, lookup, CacheCatalogLookupResult(CacheCatalogLookupStatus.RECORD_ABSENT)
    ))
    calls = []
    def operation(*args, **kwargs):
        calls.append((args, kwargs))
        return CacheCatalogWriteResult(write_status)
    result = execute_reconciliation_action(action, backend=object(), final_operation=operation)
    assert result.status is execution_status
    assert result.catalog_write_status is write_status
    assert len(calls) == 1


def test_h2c_dry_run_validates_but_performs_no_typed_operation(tmp_path):
    root, identity, _, lookup = _h2b_evidence(tmp_path)
    request = _recovery_request(root, identity)
    recovery = _recovery(identity, CacheRecoveryStatus.FINAL_PUBLISHED)
    final = compare_reconciliation_observation(_observation(
        identity, lookup, CacheCatalogLookupResult(CacheCatalogLookupStatus.RECORD_ABSENT)
    ))
    recovery_action = compare_reconciliation_observation(_observation(
        identity, _nonhit(identity), CacheCatalogLookupResult(CacheCatalogLookupStatus.RECORD_ABSENT),
        recovery, recovery_request=request,
    ))
    live = CacheCatalogLookupResult(CacheCatalogLookupStatus.RECORD_FOUND, _live(identity))
    tombstone = compare_reconciliation_observation(_observation(
        identity, _nonhit(identity), live, _recovery(identity), recovery_request=request,
    ))
    def forbidden(*args, **kwargs):
        raise AssertionError("typed mutation must not run during dry-run")
    for action in (final, recovery_action, tombstone):
        result = execute_reconciliation_action(
            action, backend=None, dry_run=True, final_operation=forbidden,
            recovery_operation=forbidden, tombstone_operation=forbidden,
        )
        assert result.status is ReconciliationActionExecutionStatus.WOULD_APPLY


def test_h2c_tombstone_invariants_fail_before_mutation(tmp_path):
    root, identity, _, lookup = _h2b_evidence(tmp_path)
    request = _recovery_request(root, identity)
    live = CacheCatalogLookupResult(CacheCatalogLookupStatus.RECORD_FOUND, _live(identity))
    valid = compare_reconciliation_observation(_observation(
        identity, _nonhit(identity), live, _recovery(identity), recovery_request=request,
    ))
    with pytest.raises(ValueError, match="positive supported revision"):
        replace(valid, expected_catalog_revision=None)
    contradictory = replace(valid, observation=_observation(
        identity, lookup, live, _recovery(identity), recovery_request=request,
    ))
    with pytest.raises(ValueError, match="no HIT"):
        execute_reconciliation_action(contradictory, backend=object())
    nonempty = replace(valid, observation=_observation(
        identity, _nonhit(identity), live,
        _recovery(identity, CacheRecoveryStatus.FINAL_PUBLISHED), recovery_request=request,
    ))
    with pytest.raises(ValueError, match="exact EMPTY"):
        execute_reconciliation_action(nonempty, backend=object())


def test_h2c_final_precedence_executes_exactly_one_mutation(tmp_path):
    root, identity, _, lookup = _h2b_evidence(tmp_path)
    request = _recovery_request(root, identity)
    observation = _observation(
        identity, lookup, CacheCatalogLookupResult(CacheCatalogLookupStatus.RECORD_FOUND, _live(identity)),
        _recovery(identity, CacheRecoveryStatus.FINAL_PUBLISHED), recovery_request=request,
    )
    action = compare_reconciliation_observation(observation)
    assert action.kind is CacheCatalogReconciliationActionKind.UPSERT_FINAL
    calls = []
    def final_once(*args, **kwargs):
        calls.append("final")
        return CacheCatalogWriteResult(CacheCatalogWriteStatus.CATALOG_WRITE_APPLIED, 2)
    def recovery_forbidden(*args, **kwargs):
        raise AssertionError("hidden recovery mutation")
    result = execute_reconciliation_action(
        action, backend=object(), final_operation=final_once,
        recovery_operation=recovery_forbidden,
    )
    assert result.status is ReconciliationActionExecutionStatus.APPLIED
    assert calls == ["final"]
