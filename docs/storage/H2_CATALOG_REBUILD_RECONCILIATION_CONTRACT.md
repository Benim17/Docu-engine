# H2 — Catalog Rebuild / Reconciliation Contract

Status: **DESIGN LOCKED — APPROVED FOR H2 IMPLEMENTATION**

Branch: `feature/cache-storage-foundation`

Scope: **Bounded authoritative identity discovery, read-only Step 5B/5E observation,
deterministic reconciliation planning, and conditional mutation of H1-owned state**

## 1. Purpose and authority

H2 restores correspondence between authoritative persistent-cache state and the
disposable H1 catalog. It supports bounded incremental reconciliation and bounded,
resumable full in-place rebuild.

The authority equations are:

```text
Filesystem observed through Step 5B/5E = authoritative cache state
H1 catalog                              = rebuildable derivative
Catalog disagreement                    != storage corruption
Reconciliation action                   = catalog-only authority
```

H2 may discover authoritative cache identities, validate or inspect them through
Step 5B and Step 5E, compare observations with H1, create missing catalog records,
conditionally update stale supported records, and publish an H1 tombstone when exact
Step 5E `EMPTY` evidence permits it.

H2 never deletes or changes a final entry, staging entry, or lock. It never repairs,
promotes, retries promotion, breaks or refreshes a lock, chooses retention, plans
cleanup, enforces quota, evicts, or prunes. H2 mutates only H1/H2-owned catalog state.

This contract refines `HOUSEKEEPING_FOUNDATION_CONTRACT.md` and
`H1_PERSISTENT_CACHE_CATALOG_CONTRACT.md`. It does not weaken Step 5A–5E or approve
H3–H7 or Source Ingestion.

## 2. Modes and convergence

H2 v1 supports exactly two modes:

1. `INCREMENTAL_IDENTITIES` reconciles an immutable bounded tuple of exact
   `CacheCatalogIdentity` values supplied by a trusted caller. It performs no broad
   authoritative-root traversal.
2. `FULL_IN_PLACE` streams the deterministic union of identities discovered from
   `entries/v1`, `staging/v1`, and H1 catalog iteration. It initializes a missing H1
   v1 catalog and reconciles individual records in place.

Full rebuild does **not** build or activate a separate catalog instance. H1 v1 has no
generation pointer or activation protocol, so H2 v1 must not invent one. “Full” means
all discoverable identities are eventually visited across bounded resumable runs; it
does not mean a globally atomic snapshot or catalog-root replacement.

Repeated runs converge. Because storage and catalog writes may occur concurrently, a
single full pass is not proof of global correspondence. A completed rerun against
unchanged storage and catalog produces only deterministic `NOOP` or report outcomes
and performs no record mutation.

## 3. Validated root and dependency boundary

Every request contains one previously validated `ValidatedCacheRoot`. No public H2
API accepts a cache-root string, arbitrary filesystem path, catalog record path,
staging writer token, or lock path.

Authoritative discovery uses a new narrow `ReconciliationReadOnlyFilesystem`
protocol. It may only:

- inspect a validated root or derived contract-relative object without following
  symlinks;
- list a derived contract directory with a one-over bound;
- bounded-read the identity-bearing canonical metadata document needed for a
  candidate probe; and
- return stable before/after filesystem identity evidence internally.

The protocol has no create, write, mkdir, unlink, rename, replace, chmod, fsync,
lock, promotion, repair, or cleanup method. Physical filesystem identities remain
private adapter evidence and never appear in public H2 models or diagnostics.

Step 5B, Step 5E, and H1 backends are injected separately. Catalog mutation is
reachable only after an immutable H2 action has passed its preconditions.

## 4. Authoritative discovery roots

H2 `FULL_IN_PLACE` traverses only:

```text
entries/v1/
staging/v1/
```

It also uses the H1 catalog backend, layout grammar, limits, and cursor order for
catalog-side discovery. It never enumerates the cache root generally and never enters `locks/v1`,
`catalog/v1` through the authoritative-discovery adapter, legacy roots, unrelated
directories, or recognizable future layout roots.

### 4.1 Why `entries/v1` is required

Final entries are the primary authoritative source for identities that may produce a
Step 5B `HIT` and an H1 final summary.

### 4.2 Why `staging/v1` is required

Staging discovery is required to recover identities that have staging state but no
final entry and no catalog record. A strictly parsed identity-bearing metadata
document can supply the `CacheKeyReference` needed to construct
`CacheCatalogIdentity`; Step 5E then authoritatively classifies current staging,
final, and lock components.

### 4.3 Why `locks/v1` is not a discovery root

A lock path and lock document contain namespace and entry digest but not the strict
`CacheKeyReference`. Therefore a lock alone cannot reconstruct
`CacheCatalogIdentity` and cannot provide a Step 5B/Step 5E request. Enumerating lock
siblings would add cost and sensitive token-adjacent surface without creating valid
H1 identity authority.

Lock state is still observed through Step 5E for identities discovered from final
entries, staging entries, or existing H1 records. An uncataloged lock-only identity
with no final or staging metadata is intentionally not catalog-reconstructable. H2
reports no invented record for it.

## 5. Canonical identity discovery

Authoritative traversal is ordinal and grammar-directed. It validates each namespace
component with `CacheNamespace`, requires the exact v1 layout, validates lowercase
digest shards and names, and rejects or reports unrelated names without treating
them as identities.

Final candidates have exactly the shape:

```text
entries/v1/<domain>/<producer_id>/<schema>/<d0d1>/<d2d3>/<digest>/
```

Staging candidates have exactly the shape:

```text
staging/v1/<domain>/<producer_id>/<schema>/<digest>.<writer_token>/
```

Writer tokens are validated only as private path grammar and are never persisted in
H2 progress, actions, results, or diagnostics.

Directory names are candidate evidence only. For both roots, an internal bounded
identity probe must:

1. prove no-follow containment and stable candidate-directory identity;
2. bounded-read and strictly parse the canonical identity-bearing metadata using the
   existing Step 5A/Step 5B parsing boundary;
3. reconstruct `CacheKeyReference` and its `CacheKey`;
4. derive `entry_digest` from that key;
5. require exact agreement among namespace path, digest path/name, shards, metadata
   namespace, metadata key reference, and derived digest; and
6. re-check candidate and ancestor stability.

The identity probe creates no final-validation authority. A malformed, unsupported,
unsafe, unstable, missing, or inconsistent probe is reported and cannot produce a
`CacheCatalogIdentity`. Directory presence alone never becomes a catalog record.

Identity expectations are supplied by a trusted
`CacheCatalogReconciliationExpectationResolver`, backed by registered producer
contracts. Given a probed canonical identity and supported metadata observation, it
must provide the Step 5B/5E `CacheLookupExpectation`, optional artifact expectation,
trusted payload expectation, lookup policy, and explicit lock-observation policy.
It must not blindly approve stored values. If the producer contract or required
expectation is unavailable, the identity is `DEFER`red; catalog history is not used
as substitute expectation authority.

## 6. Streaming union and discovery cursors

Final discovery, staging discovery, and catalog slot discovery each yield candidates
in the H1 canonical sort order. H2 performs a three-way streaming merge and emits
each reconstructed identity once with source flags `FINAL`, `STAGING`, and/or
`CATALOG`. Memory use is bounded to one next item per source, one work unit, bounded
diagnostics, and one result page.

H1's public `iterate_catalog_records()` remains the normal supported-record pager,
but its fail-closed page semantics intentionally return no partial page when one
record is corrupt. H2 therefore defines a private structural catalog-slot pager for
full reconciliation. It reuses the H1 backend and exact v1 namespace/shard/filename
grammar, no-follow stability checks, directory bounds, canonical ordering, and
exclusive cursor semantics. Each slot contains only path-derived namespace/digest
candidate data, its supported parsed live/tombstone record when available, or its H1
failure classification.

A supported live record or tombstone supplies the strict `CacheKeyReference` and can
produce `CacheCatalogIdentity`. A corrupt/unsupported slot does not. H2 reports that
slot and advances its structural cursor by the validated namespace/digest filename;
it never trusts corrupt content or turns the path-derived pair into mutation
authority. Unsafe or unstable directory/object evidence still fails closed and
cannot be skipped. This pager is H2-internal and is not a general raw-path or record
enumeration API.

An authoritative discovery cursor contains only:

- H2 cursor version;
- phase/source;
- last completed canonical namespace and entry digest for each source;
- run mode and immutable policy digest; and
- completion flags.

Cursors are exclusive lower bounds. They contain no path, token, filesystem ID,
metadata, or exception text. Concurrent insertion before a cursor may be missed by
that pass and is found by a later full rerun. H2 never labels a pass a filesystem
snapshot.

Malformed names do not enter the merge. A safely bounded malformed child is a
per-item diagnostic; unsafe or unstable ancestors fail the affected subtree or run
as defined in section 20.

## 7. Authoritative observation strategy

For every emitted identity, H2 obtains trusted expectations, then invokes Step 5B on
the exact derived final path. H2 never duplicates final validation.

- A fully verified Step 5B `HIT` is authoritative final evidence and can produce
  `UPSERT_FINAL`.
- Any non-`HIT` remains the exact Step 5B result; H2 does not reinterpret it as
  absence, corruption, or cleanup eligibility.

H2 invokes Step 5E when any of these exact conditions holds:

1. Step 5B is not `HIT` and the identity came from staging or catalog evidence;
2. staging discovery also emitted the identity, even if Step 5B is `HIT`, because
   superseded or concurrent staging state is relevant; or
3. an incremental request uses observation scope `FINAL_AND_RECOVERY` for that
   identity.

H2 v1 does not invoke Step 5E merely because an old recovery summary exists or has
wall-clock age. A Step 5B `HIT` with no staging source already supplies sufficient
fresh H1 final provenance; historical recovery data may remain preserved.

Step 5E receives the same trusted expectations and explicit lock policy. Every
completed lifecycle or failure observation is representable through H1's typed
recovery upsert. Null component observations retain H1's exact unobserved sentinels;
H2 never substitutes absence.

If Step 5B or Step 5E reports unsafe, unstable, unsupported, invalid, or I/O evidence,
H2 preserves that domain and chooses `DEFER` or `REPORT_ONLY`; it never performs
cache mutation or guesses a stronger state.

## 8. Catalog observation and comparison

H2 reads the exact H1 record and captures its supported revision immediately before
planning. Comparison uses immutable typed values, never raw JSON or timestamps.

The deterministic cases are:

| Authoritative evidence | Catalog state | Action |
|---|---|---|
| Step 5B `HIT`; no supported record | absent/uninitialized | `UPSERT_FINAL` create |
| Step 5B `HIT`; equal final summary | supported live | `NOOP` |
| Step 5B `HIT`; different/missing final summary | supported live/tombstone | `UPSERT_FINAL` conditionally |
| Completed Step 5E; equal recovery summary | supported live | `NOOP` |
| Completed Step 5E; different/missing recovery summary | supported live/tombstone | `UPSERT_RECOVERY` conditionally |
| Exact Step 5E `EMPTY`; supported live | supported live | `TOMBSTONE_EMPTY` conditionally |
| Exact Step 5E `EMPTY`; tombstone | supported tombstone | `NOOP` |
| Exact Step 5E `EMPTY`; exact record path absent | initialized catalog, no record | `NOOP` |
| Exact Step 5E `EMPTY`; catalog uninitialized/unavailable | `CATALOG_UNAVAILABLE` | `NOOP` |
| Catalog-only identity; Step 5E not exact `EMPTY` | any supported state | preserve or `UPSERT_RECOVERY`; never infer absence |
| Authoritative observation incomplete/unsafe/unstable | any | `DEFER` or `REPORT_ONLY` |
| Expected revision changed | supported record/tombstone | conflict and `DEFER` |

When both fresh final and recovery evidence exist, H2 applies at most one record
mutation per work unit. Selection order is exact:

1. exact `EMPTY` selects `TOMBSTONE_EMPTY` only when no Step 5B `HIT` exists and
   the catalog observation is a supported live record with an exact positive
   revision; exact-record absence, a supported tombstone, and an
   uninitialized/unavailable catalog instead select `NOOP`;
2. otherwise a differing/missing fresh final summary selects `UPSERT_FINAL`;
3. otherwise a differing/missing fresh recovery summary selects `UPSERT_RECOVERY`;
4. otherwise select `NOOP`.

Each typed H1 operation preserves the other supported historical summary. If both
summaries differ, recovery reconciliation is explicitly deferred to a later work
unit/pass after the final upsert; H2 does not use the private generic H1 record writer
or perform a hidden second mutation.

`TOMBSTONE_EMPTY` takes precedence over recovery upsert only when Step 5E status is
exactly `EMPTY`, there is no Step 5B `HIT`, and the catalog observation is a
supported live record. Non-`EMPTY` observations and catalog states without a
supported live revision cannot authorize tombstoning. Corrupt, unsupported, unsafe,
unstable, and I/O-failed catalog observations retain the safeguards in section 12.

## 9. Immutable reconciliation actions

H2 defines these action kinds:

- `NOOP` — authoritative and supported catalog summaries already agree;
- `UPSERT_FINAL` — call the typed H1 Step 5B integration;
- `UPSERT_RECOVERY` — call the typed H1 Step 5E integration;
- `TOMBSTONE_EMPTY` — call H1's typed exact-`EMPTY` tombstone operation;
- `DEFER` — no mutation; a later bounded run may reobserve;
- `REPORT_ONLY` — no mutation; the evidence is outside H2 mutation authority.

An immutable action contains only:

- `CacheCatalogIdentity`;
- action kind and stable reason code;
- authoritative evidence kind and typed observation reference internal to the run;
- expected supported catalog revision or explicit `ABSENT` precondition;
- intended typed H1 operation; and
- deterministic source flags.

`TOMBSTONE_EMPTY` has an additional locked invariant: its expected catalog revision
is a positive integer captured from the supported live record being logically
removed. It never carries the `ABSENT` precondition. H2 does not plan a tombstone for
an exact missing record, a supported tombstone, or an uninitialized/unavailable
catalog.

Public action projections omit Step 5B/5E paths, raw metadata, full runtime
fingerprints, diagnostics, filesystem identities, and opaque mutation preconditions.
An action is a catalog-only proposal and grants no cache mutation authority.

## 10. Preconditioned mutation

Immediately before a supported-record mutation, H1 rechecks the exact expected
revision under its writer lock. `ABSENT` means no supported live record or tombstone
revision existed when planned and uses H1 create-only semantics for typed live-record
upserts. `ABSENT` is never a tombstone precondition. H2C may execute
`TOMBSTONE_EMPTY` directly through H1's typed `tombstone_catalog_empty()` because the
action necessarily retains exact Step 5E `EMPTY` evidence, no Step 5B `HIT`, the
supported live catalog observation, and its exact positive revision; no reread or
replanning is permitted.

A conflict produces `CATALOG_REVISION_CONFLICT`, performs no write, and defers the
identity. The default and maximum automatic retry count is zero. A later run must
reobserve authoritative and catalog state; H2 may not reuse the stale action.

Checkpoint mutation never changes this rule. H2 may never overwrite a newer H1
record blindly, even when its authoritative observation appears newer or more
complete.

## 11. Staleness

H2 calls a supported catalog record stale only when one of these holds:

- a fresh Step 5B `HIT`-derived final summary differs from
  `last_validated_final`;
- a fresh completed Step 5E-derived recovery summary differs from
  `last_recovery_observation`; or
- the record revision differs from the exact revision captured for the action.

Catalog record age, file timestamps, entry creation time, discovery order, and wall
clock time never establish staleness. A stale record is not corrupt storage and is
not cleanup eligibility.

## 12. Corrupt, unsupported, unsafe, unstable, and failed catalog state

H2 handles exact H1 states as follows:

- `CATALOG_UNSUPPORTED`: `REPORT_ONLY`; never downgrade, reinterpret, or overwrite.
- `CATALOG_UNSAFE`: fail the affected identity/subtree or run; never overwrite.
- `CATALOG_UNSTABLE`: `DEFER`; never overwrite.
- `CATALOG_IO_FAILURE`: `DEFER`; never overwrite.
- `CATALOG_CORRUPT`: `REPORT_ONLY` and defer; never overwrite in place.

H2 v1 deliberately permits no corrupt-record replacement. A corrupt record has no
trusted supported revision. Resetting it to revision 1 could create an ABA match for
an older H1 writer that still holds expected revision 1; choosing another guessed
revision cannot prove monotonicity. Physical-object identity alone does not solve
that stale-revision authority problem.

Safe corrupt-state replacement therefore requires a separately locked catalog
instance/epoch and activation design understood by all H1 readers and writers. H1 v1
has no such mechanism, and H2 v1 must not invent or partially implement one. Until
then, corruption is isolated to that exact record, unrelated identities continue,
and a separately authorized administrator may remove the disposable catalog before
a missing-catalog rebuild. H2 itself performs no such removal.

## 13. Future versions and layout isolation

H2 v1 targets only H1 layout `catalog/v1` and record version 1. It ignores and never
traverses, activates, deletes, or writes future layout roots. A recognizable future
record at an exact v1 path is `CATALOG_UNSUPPORTED` and cannot be replaced by v1.

If `catalog/v1` is absent, H2 may initialize it using H1 without inspecting or
changing sibling future layouts. Coexisting version roots have no implicit active
pointer; callers select the supported H1 backend. H2 v1 defines no downgrade or
activation operation.

## 14. Missing catalog and historical summaries

A missing H1 catalog can be reconstructed from authoritative final and staging
storage plus registered producer expectations. No H1 field needed for current final
or recovery summary construction exists only in the old catalog.

Fresh Step 5B evidence creates the current final summary. Fresh Step 5E evidence
creates the current recovery summary. An in-place update preserves the other
supported historical summary under H1 merge semantics.

A rebuild from scratch may not reproduce an old recovery summary when no current
identity or reason triggers Step 5E, and it cannot reproduce prior final history for
an identity no longer derivable from storage. That loss is acceptable. H1 is
rebuildable performance metadata, not an audit log. H2 never fabricates history from
absence of observation.

## 15. Restart and durable progress

Mutating `FULL_IN_PLACE` and bounded incremental runs are restartable without relying
on in-memory-only state. H2 v1 maintains at most one canonical checkpoint document
under the fixed H2-owned path:

```text
catalog/v1/reconciliation/checkpoint.json
```

The path is not caller-selectable. The checkpoint has its own positive schema
version and contains only an opaque system-generated run ID, mode, policy digest,
source cursors, last completed work identity, counters, dry-run=false, and state
`ACTIVE` or `COMPLETE`. It contains no path, token, metadata, diagnostics, runtime
fingerprint, or filesystem identity.

Checkpoint publication is serialized by the H1 writer lock and uses bounded
canonical bytes, same-directory temporary creation, file fsync, atomic replacement,
directory fsync, and expected checkpoint revision. It never unlinks the prior
checkpoint. Only one active run exists per catalog. A new mutating run conflicts
with an `ACTIVE` checkpoint unless it supplies the exact run ID and policy digest to
resume. A `COMPLETE` checkpoint may be conditionally replaced to start a new run.

H2 checkpoints only after a work unit's catalog action has completed or its no-write
outcome is final for that pass. A crash after catalog mutation but before checkpoint
publication may repeat that identity. Reobservation plus H1 expected revisions makes
the repeat safe and idempotent; H2 claims at-least-once examination, not exactly-once
mutation.

An unsupported, corrupt, unsafe, or unstable checkpoint is fatal to resume and is
never overwritten or downgraded automatically. Restart then requires separately
authorized catalog administration; H2 performs no cleanup or removal.

## 16. Dry-run

`dry_run=True` is approved and means:

- perform the same bounded discovery, trusted expectation resolution, Step 5B/5E
  observation, catalog read, comparison, and action derivation;
- return the same public planned action projections and diagnostics;
- perform zero catalog initialization, record write, corrupt replacement, tombstone,
  checkpoint write, directory creation, lock acquisition, or other mutation.

Dry-run returns an opaque continuation cursor for caller-managed continuation during
that invocation chain, but H2 does not persist it because persistence would violate
zero mutation. If the cursor is lost, dry-run restarts from the beginning safely.
Dry-run never reserves revisions and does not predict that a later apply will avoid
conflict; apply mode must reobserve and replan.

## 17. Work budgets

`CacheCatalogReconciliationPolicy` is immutable. H2 v1 locks these maxima:

| Budget | Maximum/default |
|---|---:|
| Identities completed per run | 1,024 |
| Step 5B validations per run | 1,024 |
| Step 5E inspections per run | 512 |
| Catalog record mutations per run | 256 |
| Directory listings per run | 4,096 |
| Entries read from one directory | 4,096 plus one-over probe |
| Public result actions per page | 256 |
| Catalog page size | 256 |
| Public H2 diagnostics per run | 256 |
| Contract-relative path UTF-8 bytes | 1,024 |
| Authoritative traversal depth | 64 |

Checkpoint writes are separately bounded to at most one initial write, one write per
completed identity, and one completion write: at most 1,026 for a maximum-size run.
Step 5B and Step 5E retain all their own lower per-operation limits.

Callers may lower run counts and page size but may not raise v1 maxima. Limits are
not inferred from free disk space, memory, environment variables, catalog size, or
machine type. The first exhausted budget stops before starting the next operation,
publishes progress when mutation mode permits, and returns `BUDGET_EXHAUSTED` with a
resume point. No partial identity action is applied.

## 18. Pagination and result streaming

Catalog-side traversal reuses H1's bounded backend, layout grammar, limits, canonical
order, and cursor rules through the private structural slot pager in section 6;
supported-only callers continue to use public H1 pagination. Authoritative discovery
uses separate source cursors. Public actions are emitted in pages of at most 256 in
canonical identity order. H2 never materializes the whole catalog, authoritative
namespace, action set, or diagnostic set.

The result does not claim snapshot semantics. A continuation cursor is valid only
for the same mode, validated root identity, policy digest, and run ID where
applicable. Mismatch is a programmer/configuration error and performs no work.

## 19. Concurrency

### 19.1 H1 writers

Normal H1 writes continue during H2. H2 reads a supported revision, plans against
it, and relies on H1's writer lock and exact expected revision at mutation. Conflict
causes no write and zero automatic retry. The identity is deferred to a later pass.

### 19.2 Step 5C/5D writers

H2 acquires no global storage lock. Step 5B and Step 5E stable-observation rules are
authoritative while Step 5C/5D operate. Replacement, disappearance, or incomplete
snapshot evidence becomes the existing unstable/in-progress classification and is
deferred. H2 does not wait, arbitrate ownership, or retry promotion.

### 19.3 Multiple H2 runs

Only one mutating run may own the active checkpoint. A second mutating run conflicts
before authoritative work. Dry-runs may coexist because they mutate nothing. H1's
per-record revision rules remain the final record-write arbiter.

## 20. Failure isolation

Fatal run failures are:

- validated cache-root loss, replacement, unsafe type, or containment failure;
- unsafe/unstable authoritative version-root identity;
- inability to read or safely advance an active required checkpoint;
- incompatible cursor/run/policy identity; or
- violation of an injected dependency contract.

Per-identity or per-subtree failures are:

- malformed candidate grammar or identity document;
- unsupported producer expectations;
- corrupt or unsupported authoritative entry;
- bounded permission/I/O failure below a stable root;
- catalog exact-record failure, which advances only when its structural slot was
  safely and stably identified;
- revision conflict; and
- candidate/subtree budget exhaustion.

Per-item failure does not stop unrelated identities when continuing can be proven
safe and deterministic. A root-level safety failure stops the run. No failure grants
cleanup, repair, replacement of unsafe state, or cache mutation authority.

## 21. Diagnostics and privacy

H2 diagnostics are distinct from H1, Step 5B, Step 5E, policy, and executor
diagnostics. They use a stable `CacheCatalogReconciliationDiagnosticCode` including:

```text
IDENTITY_DISCOVERED
IDENTITY_RECONCILED
IDENTITY_NOOP
IDENTITY_SKIPPED
CATALOG_REVISION_CONFLICT
AUTHORITATIVE_UNSTABLE
AUTHORITATIVE_UNSAFE
AUTHORITATIVE_CORRUPT
CATALOG_FAILURE
BUDGET_EXHAUSTED
CHECKPOINT_PUBLISHED
CHECKPOINT_RESUMED
DIAGNOSTICS_TRUNCATED
```

Diagnostics contain only a trusted subject, optional `CacheCatalogIdentity`, stable
code, source kind, and bounded ordinal sequence. They are deterministically ordered,
deduplicated, and capped at 256.

No public request, action, cursor, progress, result, or diagnostic exposes absolute
paths, usernames or home directories, logical IDs, URLs, source secrets, full runtime
fingerprints, owner/writer tokens, host IDs, PIDs, inode/device IDs, temporary names,
raw JSON, content, exception text, errno/native text, or opaque filesystem identity
preconditions.

## 22. Conceptual public API

H2 may expose immutable equivalents of:

```text
CacheCatalogReconciliationMode
CacheCatalogReconciliationObservationScope
CacheCatalogReconciliationActionKind
CacheCatalogReconciliationStatus
CacheCatalogReconciliationPolicy
CacheCatalogReconciliationRequest
CacheCatalogReconciliationAction
CacheCatalogReconciliationProgress
CacheCatalogReconciliationResult
CacheCatalogReconciliationCursor
CacheCatalogReconciliationDiagnostic
CacheCatalogReconciliationDiagnosticCode
CacheCatalogReconciliationExpectationResolver
ReconciliationReadOnlyFilesystem
```

The conceptual operation is:

```python
def reconcile_cache_catalog(
    request: CacheCatalogReconciliationRequest,
    *,
    discovery_filesystem: ReconciliationReadOnlyFilesystem,
    catalog_backend: CacheCatalogBackend,
    expectation_resolver: CacheCatalogReconciliationExpectationResolver,
    lookup_filesystem: ReadOnlyCacheFilesystem,
    recovery_filesystem: RecoveryReadOnlyFilesystem,
    lock_clock: LockObservationClock,
) -> CacheCatalogReconciliationResult:
    ...
```

`CacheCatalogReconciliationRequest` contains a `ValidatedCacheRoot`, mode, immutable
policy, `dry_run`, an optional bounded tuple of identities for incremental mode,
observation scope, and an optional validated resume token/cursor. Observation scope
is exactly `FINAL_SUFFICIENT` or `FINAL_AND_RECOVERY`; full mode derives recovery need
from source evidence and requires `FINAL_SUFFICIENT`, while incremental mode may
explicitly request `FINAL_AND_RECOVERY`. The request contains no path or caller-
created record, summary, or action.

Result statuses distinguish `COMPLETE`, `BUDGET_EXHAUSTED`, `CHECKPOINT_CONFLICT`,
`ROOT_FAILURE`, and `DEPENDENCY_FAILURE`. Results contain bounded action projections,
progress counters, next cursor, and sanitized diagnostics only.

## 23. Idempotence and non-authority

For unchanged authoritative and catalog state:

```text
reconcile(reconcile(state)) = reconcile(state)
```

The second completed pass performs zero record mutations and yields deterministic
`NOOP` outcomes. A repeated work unit after interruption either observes the already-
applied summary and chooses `NOOP`, or sees a revision conflict and defers; it never
duplicates a semantic update blindly.

H2 output explicitly does not mean:

```text
catalog hit          = Step 5B HIT
catalog miss         = cache MISS
catalog record       = validated cache authority
recovery summary     = cleanup eligibility
unobserved component = absent component
```

No H2 action or diagnostic is a `CleanupPlan`, deletion authority, promotion
authority, lock authority, retry recommendation, retention decision, or quota input.

## 24. Tombstones and physical compaction

H2 may publish only H1's logical tombstone from exact same-identity Step 5E `EMPTY`
evidence when replacing an existing supported live catalog record at its exact
positive revision. It never creates a tombstone merely to encode an already absent
record and never unlinks a record file.

`NOOP` for an absent or uninitialized catalog does not prove that a durable historical
tombstone exists. It means only that no catalog mutation is needed because no visible
live catalog record exists. Step 5E `EMPTY` remains authoritative cache/recovery
evidence, but H2 does not manufacture catalog history solely to encode absence.

H2 v1 does not compact physical tombstones, abandoned catalog temporary files,
checkpoints, or catalog directories. It does not delete or swap catalog roots.
Identity-safe catalog-only compaction remains later catalog-maintenance design and
must not be combined with H3–H5 cache deletion authority.

## 25. Source Ingestion maturity gate

After all of these are complete and verified:

- Step 5A–5E;
- H1 Persistent Cache Catalog / Index; and
- H2 bounded rebuild/reconciliation;

the storage platform has the intended minimum maturity for a separate planning
decision to **consider** beginning Source Ingestion & Understanding without first
implementing H3–H7, provided:

- automatic cleanup, deletion, quota enforcement, and pruning remain disabled;
- expected cache growth is explicitly judged manually manageable; and
- no ingestion feature assumes automatic retention, eviction, or protection policy.

This contract establishes the decision gate only. It does not approve Source
Ingestion, change the roadmap, or define YouTube/source-specific behavior. YouTube
URL remains the planned first ingestion vertical slice if that later decision is
approved.

## 26. H3–H7 boundary

Reserved unchanged:

- **H3** — retention and cleanup policy classifications;
- **H4** — immutable `CleanupPlan` and its preconditions;
- **H5** — identity-safe cache deletion and post-deletion verification;
- **H6** — quota and storage-budget policy; and
- **H7** — automatic housekeeping orchestration.

H2 does not implement, expose, or partially authorize any of these layers.

## 27. Implementation decomposition

H2 implementation is divided into separately reviewable slices:

1. **H2A — Authoritative identity and catalog-slot discovery.** Read-only
   validated-root adapter, strict final/staging grammar, bounded identity probe,
   private H1-safe structural slot pager, sorted source cursors, and streaming union;
   no Step 5B/5E calls and no catalog mutation.
2. **H2B — Observation and comparison engine.** Trusted expectation resolver,
   Step 5B/5E scheduling, typed comparison, stale detection, and pure action models;
   no catalog mutation.
3. **H2C — Conditional supported-record reconciliation.** Apply `NOOP`, typed final
   and recovery upserts, exact-`EMPTY` tombstones, expected revisions, conflict
   behavior, and dry-run proof; no checkpoints.
4. **H2D — Durable progress and resume.** Canonical checkpoint model/publication,
   resume, interruption matrix, single-mutating-run arbitration, and strict
   corrupt/unsupported checkpoint refusal.
5. **H2E — Full bounded orchestration and regression hardening.** Full in-place and
   incremental modes, budgets, pagination, diagnostics, concurrency, failure
   isolation, idempotence, exports, privacy proof, and full regression.

Completion of one slice does not authorize the next without normal review. No slice
begins H3, Source Ingestion, or cache cleanup.

## 28. Required implementation tests

### Discovery

- valid final and staging identities, duplicate identity coalescing, supported live
  and tombstoned catalog-only identities;
- corrupt/unsupported catalog slots report and advance without becoming identity
  authority, while unsafe/unstable slots fail closed;
- malformed namespace, schema, shard, digest, writer-token, and metadata names;
- strict key-reference/digest/path reconstruction and mismatch rejection;
- unsafe roots/ancestors/objects, replacement races, future roots, and containment;
- deterministic ordinal traversal, exclusive cursors, bounded streaming, exact 4,096
  directory-entry and one-over limits, depth 64/one-over, and path bytes 1,024/one-
  over;
- no cache-root, lock-root, legacy-root, future-root, or unrelated traversal.

### Observation

- Step 5B `HIT`, every broad failure, unavailable producer expectations, and exact
  trusted request identity;
- Step 5E every lifecycle/failure observation, including unobserved component
  sentinels;
- Step 5E skipped for a plain Step 5B `HIT` and invoked for discovered staging;
- zero duplicated validators or weakened Step 5B/5E limits.

### Comparison and actions

- absent, equal, stale, tombstoned, corrupt, unsupported, unsafe, unstable, and I/O-
  failed catalog states;
- deterministic `NOOP`, `UPSERT_FINAL`, `UPSERT_RECOVERY`, `TOMBSTONE_EMPTY`,
  `DEFER`, and `REPORT_ONLY`;
- exact summary merge, final absence preserving historical final evidence, and no
  caller-created summary authority;
- exact `EMPTY` plus supported live record selects `TOMBSTONE_EMPTY` with a positive
  revision;
- exact `EMPTY` plus supported tombstone, missing exact record, or
  uninitialized/unavailable catalog selects `NOOP`;
- exact `EMPTY` plus corrupt, unsupported, unsafe, unstable, or I/O-failed catalog
  state retains its existing safe failure action;
- no `TOMBSTONE_EMPTY` action carries `ABSENT`/`None`, and every non-`EMPTY`
  observation is rejected as tombstone authority.

### Corruption and versions

- corrupt, unsupported, unsafe, unstable, and I/O-failed records are never
  overwritten;
- corrupt state cannot reset or guess a revision and cannot create an ABA match;
- future layouts untouched and absent v1 initialized independently;
- no unlink, root replacement, downgrade, activation, corrupt replacement, or
  compaction.

### Concurrency

- H1 revision conflict, concurrent typed H1 update, concurrent promotion, staging
  change, final replacement, and unstable Step 5B/5E observation;
- zero automatic retries and no global storage lock;
- two mutating H2 runs conflict while dry-run remains read-only.

### Restart and budgets

- interruption before/after action and before/after checkpoint publication;
- exact resume, policy/run mismatch, corrupt/unsupported/unsafe checkpoint, completed
  checkpoint replacement, and idempotent replay;
- every budget at exact limit and one over; no partial identity action;
- missing cursor restart and later convergence for concurrent insertion before a
  cursor.

### Dry-run, diagnostics, privacy, and boundaries

- dry-run produces the same planned action kinds and performs zero mutation,
  initialization, lock acquisition, or checkpoint publication;
- deterministic diagnostic order, deduplication, truncation at 256, and stable codes;
- no absolute paths, users, logical IDs, URLs, secrets, fingerprints, tokens,
  host/PID, filesystem IDs, temp names, raw JSON, exception/native text, or content;
- no cache entry/staging/lock mutation, repair, promotion/retry, retention,
  `CleanupPlan`, deletion, quota, pruning, H3–H7, Source Ingestion, or background
  worker behavior;
- H1A–H1E, Step 5B, Step 5E, all storage, and full-suite regressions remain green.

## 29. Acceptance criteria and next action

This contract resolves H2 authority, discovery roots and identity reconstruction,
Step 5B/5E scheduling, comparison and action semantics, in-place rebuild mode,
corrupt/unsupported state, durable restart, work budgets, pagination, concurrency,
staleness, historical summaries, dry-run, idempotence, diagnostics, privacy, the
Source Ingestion decision gate, and H3–H7 boundaries.

Status:

**DESIGN LOCKED — APPROVED FOR H2 IMPLEMENTATION**

Exact next action:

**Resume H2C conditional supported-record reconciliation.**
