# Storage Foundation Checkpoint

## Project state

Branch: `feature/cache-storage-foundation`

Foundation review decision: **GO WITH CONDITIONS**

Last completed step: Step 4 – Read-only Inspect CLI

Next phase: Step 5 design only; no implementation started

## Locked commits

- `040260718665615bd659ebb268379e10a583dcf5` — `feat: add cache storage artifact contracts`
- `6ec22e820ba9be5bb2c9e4f5393f70ba7052d18d` — `feat: add deterministic cache key contracts`
- `ff946edd7e899507a2eb78733f317c69fe079f7f` — `feat: add read-only storage inventory safety`
- `124cf4b645245d9094dc533bfe344d90e9eec8e9` — `feat: add read-only storage inspect cli`

## Locked foundation modules

- `engine/storage/models.py`
- `engine/storage/cache_keys.py`
- `engine/storage/safety.py`
- `engine/storage/inventory.py`
- `engine/storage/cli.py`
- `engine/storage/__main__.py`
- `engine/storage/__init__.py`

## Test state

- Models: 74 passed
- Cache keys: 77 passed
- Inventory: 45 passed
- Safety: 21 passed
- CLI: 33 passed
- Storage suite: 250 passed
- Full regression suite: 562 passed

## Foundation rules

- Inventory and CLI are read-only.
- `InventoryReport` is a point-in-time observation, never a delete plan.
- `passes_safety_gate` is not a deletion decision.
- `potentially_reclaimable_bytes` is only an estimate.
- `logical_id` must never be used as a physical cache path.
- Only a parsed and validated `CacheKey` may later map to cache-entry paths.
- Inventory must remain separate from a future persistent cache index.
- Runtime fingerprints must be declared by producer/module adapters.
- Cache infrastructure must not autodetect arbitrary machine state.
- Physical paths and runtime locations must never affect cache identity.
- Future mutation requires immediate safety revalidation.

## Foundation Review conditions for Step 5

1. Define a separate versioned cache-entry and metadata contract.
2. Separate inventory report versioning from artifact contract versioning before reports become persistent.
3. Use only validated `CacheKey` values for physical cache layout.
4. Treat `GitTrackingStatus.NOT_APPLICABLE` as neutral, never sufficient safety.
5. Keep staging separate from final cache entries.
6. Promote entries atomically on the same filesystem.
7. Add locks or equivalent concurrent-producer protection.
8. Require payload integrity and explicit completeness.
9. Keep persistent cache index separate from read-only inventory.
10. Revalidate path, file type, symlink, containment, Git status, identity, lock status and roots immediately before every mutation.
11. Never use `ArtifactDescriptor.identity_dict()` as cache lookup material.
12. Never use `InventoryRecord` or `InventoryReport` directly as a mutation plan.

## Known non-blocking risks

- `ArtifactDescriptor.identity_dict()` is broader than pure cache identity.
- Descriptor equality includes policy and runtime observation fields.
- Logical IDs may contain traversal-like text and must remain data only.
- Inventory report version currently follows artifact contract version.
- `GitIndexSnapshot.status_for()` may scale poorly in very large repositories.
- Hardlinks can make reclaimable-byte estimates larger than actual disk recovery.
- Case and Unicode alias behavior partly depends on the host filesystem.
- TOCTOU cannot be eliminated by read-only inspection.

## Resume point

Begin by reviewing this checkpoint and designing Step 5 without writing production code.

The next design topic is **Versioned Persistent Cache Entry Contract**, covering:

- cache namespace;
- validated `CacheKey`-to-path mapping;
- sharding;
- staging entries;
- metadata schema;
- payload manifest;
- payload digests;
- completeness marker;
- producer/schema matching;
- runtime fingerprint storage;
- atomic promotion;
- lock ownership;
- interrupted-write recovery;
- read-only lookup semantics.

Do not design cleanup execution yet.

## Release tag

`audio-director-v4.7.0` points to `36df93be1701b73057a62102347b4194dd6d1d22`.

This release tag must never be moved.
