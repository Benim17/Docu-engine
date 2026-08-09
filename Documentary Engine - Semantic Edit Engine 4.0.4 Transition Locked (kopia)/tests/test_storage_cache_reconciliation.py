from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from engine.storage.cache_catalog import (
    CacheCatalogIdentity,
    CacheCatalogLiveRecord,
    CacheCatalogRecoverySummary,
    CacheCatalogTombstone,
    CacheCatalogRecoveryProvenance,
    LocalCacheCatalogBackend,
    LocalCacheCatalogReadOnlyBackend,
    derive_catalog_record_relative_path,
)
from engine.storage.cache_keys import CacheKey
from engine.storage.cache_lookup import FilesystemObjectType
from engine.storage.cache_reconciliation import (
    MAX_RECONCILIATION_DIRECTORY_ENTRIES,
    CacheCatalogReconciliationCursor,
    CacheCatalogReconciliationMode,
    CatalogSlotClassification,
    DiscoveredCacheIdentity,
    LocalReconciliationReadOnlyFilesystem,
    ReconciliationDiscoveryError,
    ReconciliationDiscoveryLimitError,
    ReconciliationDiscoveryPage,
    ReconciliationDiscoveryPolicy,
    ReconciliationSourceFlags,
    _DiscoveryBudget,
    _merged_identities,
    _validate_relative,
    discover_catalog_slots_page,
    discover_reconciliation_identities,
    iter_catalog_slots,
    iter_final_discovered_identities,
    iter_staging_discovered_identities,
)
from engine.storage.cache_recovery import (
    CacheRecoveryStatus,
    FinalRecoveryState,
    LockRecoveryState,
)
from engine.storage.persistent_cache import (
    CacheArtifactMetadata,
    CacheEntryMetadata,
    CacheKeyReference,
    CacheNamespace,
    CacheProducerMetadata,
    CacheRuntimeFingerprint,
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
