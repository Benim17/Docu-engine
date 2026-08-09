# Step 5E — Read-Only Recovery Inspection Contract

**Status: DESIGN LOCKED — APPROVED FOR IMPLEMENTATION**

This document is the normative Step 5E inspection and classification contract. It
refines the Step 5 parent contract without weakening the Step 5B read-only lookup
contracts or the Step 5D mutation contract. Step 5E observes one cache identity and
its matching persisted states. It never chooses or performs a recovery action.

## 1. Purpose and hard boundary

Step 5E supplies stable, deterministic descriptions of final, staging, and lock
state left by ordinary operation or interruption. It may answer what exists, whether
the observed objects validate, whether a matching lock is active or stale, and
whether persisted evidence matches a retained-lock publication state.

Step 5E never writes, creates, unlinks, deletes, renames, chmods, truncates, repairs,
promotes, rolls back, quarantines, acquires, releases, refreshes, breaks, or steals a
lock. It performs no retry, cleanup, eviction, indexing, quota enforcement, owner
liveness probing, or housekeeping policy decision. No result grants mutation
authority.

## 2. Authority and vocabulary

The following remain authoritative:

- `STEP_5_VERSIONED_PERSISTENT_CACHE_ENTRY_CONTRACT.md` for cache identity and
  document models;
- `STEP_5B_READ_ONLY_CACHE_LOOKUP_CONTRACT.md` for final-entry validation;
- `STEP_5B_LOCK_LIFECYCLE_OBSERVATION_CONTRACT.md` for lock parsing and freshness;
  and
- `STEP_5D_LOCK_MUTATION_AND_PROMOTION_CONTRACT.md` for possible promotion outcomes.

“Recovery” in this document means read-only observation for a future reviewed
policy. It does not mean repair or mutation.

## 3. Recovery unit and derived paths

One inspection request concerns exactly one validated physical cache identity:

```text
ValidatedCacheRoot + CacheNamespace + CacheKey
```

The trusted entry digest is `derive_entry_digest(cache_key)`. The inspector derives,
and never accepts from callers:

- the final path through `derive_final_entry_path(...)`;
- the lock path through `derive_lock_path(...)`; and
- the staging namespace directory
  `staging/v1/<domain>/<producer_id>/<producer_schema_version>`.

The inspector discovers matching staging candidates only within that one staging
namespace directory. A candidate name is exactly:

```text
<expected-entry-digest>.<validated-writer-token>
```

The suffix must pass the existing Step 5A writer-token grammar, must contain no
`..`, and reconstruction through `derive_staging_entry_path(...)` must equal the
observed path. The request accepts no arbitrary final, lock, staging, or diagnostic
path. `logical_id` does not participate in physical identity.

An optional `known_writer_token` may restrict inspection to exactly its derived
staging path. With a known token, the staging directory is not listed. Without one,
the bounded discovery rule in section 12 applies. In both modes the result concerns
only the expected entry digest.

## 4. Staging lifecycle has no time predicate

Step 5C persists no staging-activity timestamp. `CacheEntryMetadata.created_at_utc`
describes entry creation and is not a staging heartbeat. Filesystem mtime, ctime,
birth time, access time, directory activity, and process-local time are not
contractual lifecycle evidence.

Therefore Step 5E defines no `ACTIVE_STAGING`, `STALE_STAGING`, `STALLED_STAGING`, or
age threshold. It injects no staging clock and never derives staging freshness from
metadata or filesystem timestamps. The parent contract's earlier “active” and
“stale” staging vocabulary is narrowed for contract v1 to the content/lifecycle
states below. Future time-based staging states require a separately locked persisted
timestamp or heartbeat design.

## 5. Writer token and lock ownership are independent

The Step 5C `writer_token` is a validated staging-path disambiguator. The Step 5D
`owner_token` is a cryptographically fresh lock-ownership nonce. No contract equates,
derives, or correlates them.

Step 5E must not compare them or expose either token. It cannot classify staging as
owned, foreign, abandoned, live, or dead from a lock document. `host_id` and
`process_id` are likewise not liveness or staging-ownership evidence.

## 6. Immutable observation model

The implementation uses immutable equivalents of:

```text
CacheRecoveryInspectionRequest
RecoveryInspectionPolicy
StagingRecoveryObservation
FinalRecoveryObservation
LockRecoveryObservation
CacheRecoveryDiagnostic
CacheRecoveryObservation
CacheRecoveryStatus
CacheRecoveryReason
RecoverySubject
```

`CacheRecoveryInspectionRequest` contains exactly:

```text
cache_root: ValidatedCacheRoot
namespace: CacheNamespace
cache_key: CacheKey
expectation: CacheLookupExpectation
artifact_expectation: CacheArtifactExpectation | None
payload_expectation: ProducerPayloadExpectation
lookup_policy: CacheLookupVerificationPolicy
lock_observation_policy: LockObservationPolicy
recovery_policy: RecoveryInspectionPolicy
known_writer_token: str | None = None
```

Final validation receives the same trusted expectations as Step 5B. Lock freshness
uses the request's mandatory `LockObservationPolicy` and the injected
`LockObservationClock`; there is no implicit freshness value.

`CacheRecoveryObservation` contains trusted identity values, root-relative contract
paths, a tuple of staging observations in ordinal path order, one final observation,
one lock observation, the combined status and primary reason, and bounded
diagnostics. It exposes no mutable reference or mutation recommendation.

## 7. Staging observation states

Each matching candidate has exactly one `StagingRecoveryState`:

- `STAGING_COMPLETE_VALID` — the directory has the exact Step 5C four-object
  structure; all canonical documents, expected identity relations, document
  digests, payload set, sizes, hardlink policy, and every payload SHA-256 pass the
  Step 5D staging revalidation rules; `COMPLETE` is valid.
- `STAGING_INCOMPLETE` — the candidate is a safe stable directory, `COMPLETE` is
  absent, at least one required Step 5C object is absent, no unexpected top-level
  object exists, and every present document or payload object inspected before the
  missing condition is canonical, supported, safe, and not conclusively invalid.
- `STAGING_INVALID` — a supported candidate is malformed, structurally impossible,
  identity-conflicted, digest-invalid, hardlinked, has an unexpected object, has a
  present `COMPLETE` without a fully valid entry, or otherwise fails Step 5C/5D
  content validation.
- `STAGING_UNSUPPORTED` — a safely recognizable positive future version occurs in
  `COMPLETE`, metadata, manifest, cache-key reference, or runtime fingerprint under
  the Step 5B unsupported-version rules.
- `STAGING_UNSAFE` — the candidate, an ancestor, or required descendant is a
  symlink or unsupported filesystem object, escapes containment, or violates the
  no-follow path contract.
- `STAGING_UNSTABLE` — stable identity evidence is unavailable or any relevant
  object, ancestor, or enumerated directory is replaced, disappears, or changes
  during inspection.
- `STAGING_IO_FAILURE` — a deterministic permission or I/O failure prevents a
  stable classification and is not more precisely unsafe or unstable.

Absence is an aggregate condition, not a candidate state:
`STAGING_ABSENT` means the known derived staging path is absent, or bounded discovery
finds no matching candidate after a stable enumeration. Multiple matching candidates
are permitted observations because independent Step 5C writers have distinct path
tokens. Each is validated independently; their count alone is not corruption.

## 8. Final-entry states

Final observation reuses Step 5B validation and status/reason semantics rather than
implementing a second validator. It maps to exactly:

- `FINAL_ABSENT` — the exact derived final path is absent;
- `FINAL_VALID` — Step 5B returns `HIT` after full payload verification;
- `FINAL_UNSUPPORTED` — Step 5B returns `UNSUPPORTED_VERSION`;
- `FINAL_UNSAFE` — Step 5B returns `UNSAFE_PATH`;
- `FINAL_UNSTABLE` — Step 5B reports `UNSTABLE_SNAPSHOT`; or
- `FINAL_INVALID` — any other present, supported, stable rejected final entry,
  including incomplete, malformed, conflicted, integrity, expectation, or I/O
  rejection.

For recovery composition, final absence must be observed independently of lock
classification. A final-only helper may be extracted from Step 5B, but its semantics
must remain identical. A present final is never hidden by lock state. Step 5E does
not return a staging entry as a hit.

## 9. Lock states and terminology

Lock observation reuses the exact Step 5B parser, 16 KiB bound, clock validation,
freshness threshold, no-skew rule, path derivation, and stable-read policy. It maps
to exactly:

- `LOCK_ABSENT`;
- `LOCK_ACTIVE`;
- `LOCK_STALE`;
- `LOCK_MALFORMED`;
- `LOCK_UNSUPPORTED`;
- `LOCK_UNSAFE`;
- `LOCK_UNSTABLE`;
- `LOCK_IO_FAILURE`;
- `LOCK_IDENTITY_CONFLICT`; or
- `LOCK_TIMESTAMP_INVALID`.

Only canonical supported matching locks can be `LOCK_ACTIVE` or `LOCK_STALE`.

A **retained lock** is the derived combined state:

```text
FINAL_VALID + STAGING_ABSENT + (LOCK_ACTIVE or LOCK_STALE)
```

This is persisted evidence consistent with Step 5D `PROMOTED_LOCK_RETAINED`. It does
not prove which historical process produced the state and does not authorize lock
removal. If staging also exists, the state is superseded staging with a published
final, not the narrow retained-lock label.

Step 5E does not use **orphan lock** as a normative status. A valid lock with no
observed final or staging is `ACTIVE_LOCK_WITHOUT_ENTRY` or
`STALE_LOCK_WITHOUT_ENTRY`. Absence cannot prove owner death, interruption, or
abandonment. “Orphan” remains informal future-policy vocabulary.

## 10. Combined recovery statuses

After component classification and precedence, a lifecycle-valid observation has
exactly one `CacheRecoveryStatus`:

| Staging aggregate | Final | Lock | Combined status |
|---|---|---|---|
| absent | absent | absent | `EMPTY` |
| one or more complete-valid; no incomplete | absent | absent | `UNPUBLISHED_COMPLETE_STAGING` |
| any incomplete; no higher-precedence component | absent | absent | `INCOMPLETE_STAGING` |
| one or more complete-valid | absent | active | `COMPLETE_STAGING_WITH_ACTIVE_LOCK` |
| one or more complete-valid | absent | stale | `COMPLETE_STAGING_WITH_STALE_LOCK` |
| any incomplete | absent | active | `INCOMPLETE_STAGING_WITH_ACTIVE_LOCK` |
| any incomplete | absent | stale | `INCOMPLETE_STAGING_WITH_STALE_LOCK` |
| absent | valid | absent | `FINAL_PUBLISHED` |
| absent | valid | active or stale | `FINAL_PUBLISHED_LOCK_RETAINED` |
| any valid or incomplete staging | valid | absent | `FINAL_PUBLISHED_WITH_SUPERSEDED_STAGING` |
| any valid or incomplete staging | valid | active or stale | `FINAL_PUBLISHED_WITH_SUPERSEDED_STAGING_AND_LOCK` |
| absent | absent | active | `ACTIVE_LOCK_WITHOUT_ENTRY` |
| absent | absent | stale | `STALE_LOCK_WITHOUT_ENTRY` |

For multiple staging candidates, “complete-valid” means at least one candidate is
complete-valid and none is incomplete; “incomplete” means at least one candidate is
incomplete. A mixture of complete-valid and incomplete candidates uses the
incomplete row because it describes the unresolved persisted state without implying
which writer should prevail.

The table does not claim that promotion should be attempted, retried, or avoided.
An observed state after a prior `PROMOTED_OUTCOME_UNCERTAIN` is classified solely by
current persisted evidence; historical in-memory results are never inputs.

## 11. Recovery precedence and reasons

Component failures prevent a lifecycle convenience label. Precedence is globally:

1. `RECOVERY_UNSAFE`;
2. `RECOVERY_UNSUPPORTED`;
3. `RECOVERY_UNSTABLE`;
4. `RECOVERY_INVALID`;
5. the lifecycle statuses in section 10.

Within one precedence family, primary-reason subject order is final, staging, lock;
within staging it is ordinal candidate path; within a subject it follows the
existing Step 5B reason order. All additional findings may appear as bounded
diagnostics.

Mappings are:

- any `FINAL_UNSAFE`, `STAGING_UNSAFE`, or `LOCK_UNSAFE` -> `RECOVERY_UNSAFE`;
- otherwise any unsupported component -> `RECOVERY_UNSUPPORTED`;
- otherwise any unstable component -> `RECOVERY_UNSTABLE`;
- otherwise any `FINAL_INVALID`, `STAGING_INVALID`, `STAGING_IO_FAILURE`,
  `LOCK_MALFORMED`, `LOCK_IO_FAILURE`, `LOCK_IDENTITY_CONFLICT`, or
  `LOCK_TIMESTAMP_INVALID` -> `RECOVERY_INVALID`.

`CacheRecoveryReason` preserves the precise component reason, including existing
Step 5B reasons where applicable. It is `None` only for section 10 lifecycle states.
Safety and integrity always dominate retained, waiting, or published convenience
labels.

## 12. Deterministic traversal scope

Step 5E is not a cache-wide crawler. It may inspect only:

1. the validated root and exact ancestors of derived paths;
2. the one derived final entry and required descendants;
3. the one derived lock;
4. either one known derived staging path, or the one staging namespace directory;
5. matching staging candidates and their required descendants.

Without `known_writer_token`, the staging namespace directory is listed once using
the Step 5B stable no-follow directory operation. Names are sorted by Unicode code
point/ordinal order before filtering. The inspector does not recurse into unrelated
names, sibling namespaces, other entry digests, locks, final entries, cache roots,
or legacy layouts. It never lists a lock or final shard directory.

Every selected component is reconstructed from validated models and checked for
root containment. Unsafe namespace ancestors fail closed. An absent staging
namespace directory means `STAGING_ABSENT` only after root stability is re-proven.

## 13. Recovery inspection limits

`RecoveryInspectionPolicy` is immutable and contains exactly:

```text
max_staging_candidates_per_identity: int = 64
max_staging_directory_entries: int = 4096
max_contract_relative_path_utf8_bytes: int = 1024
max_traversal_depth: int = 64
max_diagnostics: int = 32
```

These are contract-v1 ceilings, not adaptive defaults. Ordinary configuration may
not raise or lower them. A future change requires contract review. Directory listing
reads at most `max_staging_directory_entries + 1` names and exposes no partial list
as complete. More total names or more matching candidates returns
`RECOVERY_INVALID` with a resource-limit reason; it does not silently truncate
candidates. Candidate and payload paths use the existing 1024-byte and depth-64
limits. Documents, payload counts, payload sizes, read chunks, and lock size reuse
the supplied locked Step 5B policies; no check may be disabled.

## 14. Snapshot stability

The inspector performs one attempt with zero automatic retries. It:

1. retains and re-proves the validated root identity;
2. inspects every selected ancestor no-follow;
3. stably lists the staging namespace directory when discovery is used;
4. retains identities for selected candidates and required descendants;
5. uses bounded stable reads and payload hashing under Step 5B rules;
6. re-proves the staging directory and enumeration identity after each candidate;
7. applies Step 5B final snapshot validation;
8. applies Step 5B stable lock observation; and
9. re-proves the root and all component identities needed for the combined result.

Replacement, disappearance, mutation, reduced identity evidence, or inability to
confirm absence maps to the relevant unstable state. A candidate discovered and then
removed is unstable, not absent. A path absent in the initial stable observation and
still absent at the final gate remains absent. No retry converts a race into a more
convenient state.

## 15. Diagnostics and privacy

`CacheRecoveryDiagnostic` contains only a stable code, `RecoverySubject`, and an
optional validated root-relative contract path. Subjects are `ROOT`, `STAGING`,
`FINAL`, and `LOCK`.

Diagnostics are deduplicated by `(code, subject, relative_path)`, ordered first by
the precedence in section 11, then subject order, then ordinal relative path, and
bounded to 32. When more findings exist, the last diagnostic is one stable
`DIAGNOSTICS_TRUNCATED` marker. Diagnostics never cause extra unsafe reads.

Results never expose absolute paths, raw JSON, arbitrary file content, raw exception
text, symlink targets, owner tokens, writer tokens, host IDs, process IDs, device or
inode values, environment values, usernames, or native adapter details.

## 16. Read-only dependency and entry point

The conceptual public entry point is:

```python
def inspect_cache_recovery_state(
    request: CacheRecoveryInspectionRequest,
    *,
    filesystem: RecoveryReadOnlyFilesystem = DEFAULT_RECOVERY_READ_ONLY_FILESYSTEM,
    lock_clock: LockObservationClock = SYSTEM_LOCK_OBSERVATION_CLOCK,
) -> CacheRecoveryObservation:
    ...
```

`RecoveryReadOnlyFilesystem` may be the complete Step 5B read-only filesystem
surface plus bounded stable directory enumeration required by section 12. It exposes
only inspection, resolution, stable sorted listing, bounded regular-file reading,
and bounded payload hashing. It has no creation, write, mkdir, rename, replace,
unlink, chmod, fsync, lock mutation, promotion, cleanup, or recovery-action method.

`CachePromotionFilesystem`, `OwnedWriterLock`, and all Step 5D mutation functions are
not accepted, imported as dependencies, or reachable through callbacks. Expected
filesystem states return observations; wrong dependency types or internally
inconsistent requests are programmer errors.

## 17. Mapping persisted Step 5D outcomes

Where current evidence matches a Step 5D success shape:

- final valid + staging absent + lock absent -> `FINAL_PUBLISHED`, consistent with
  `PROMOTED_AND_RELEASED`;
- final valid + staging absent + active/stale valid lock ->
  `FINAL_PUBLISHED_LOCK_RETAINED`, consistent with `PROMOTED_LOCK_RETAINED`.

These are consistency statements, not historical proof. Interrupted-before-rename
states use the relevant staging/lock rows in section 10. A final and staging both
present use the superseded-staging rows. Invalid or uncertain objects use precedence,
not guessed history. Step 5E never resumes promotion.

## 18. Descriptive-only semantics

An observation may say that a valid staging entry is unpublished, a lock is stale,
a final is valid, or persisted evidence is consistent with a retained lock. It must
not say “safe to delete,” “break,” “retry,” “repair,” “evict,” “reclaim,” “promote,”
or “clean now.” It contains no deletion eligibility, priority, retention age,
storage value, or proposed action field.

## 19. Post-Step-5E boundary

Reserved for separately locked later design and implementation:

- stale-, retained-, or malformed-lock removal;
- orphan or invalid staging deletion;
- promotion retry or recovery promotion;
- repair, rollback, quarantine, or migration;
- retention and cleanup policy;
- cleanup execution;
- cache indexing and catalogs;
- quota or storage-budget enforcement;
- eviction and automatic pruning; and
- any owner-liveness or recovery-authority mechanism.

The roadmap currently identifies this only as the post-5E housekeeping design /
contract phase; it is not implementation-approved.

## 20. Required implementation tests

Implementation approval requires deterministic injected tests for:

### Staging

- absent known path and absent discovery directory;
- one and multiple complete-valid candidates;
- incomplete entry without `COMPLETE` and other safely missing objects;
- malformed metadata, manifest, or marker;
- recognizable unsupported document versions;
- missing, changed, extra, special, or hardlinked payloads;
- unsafe candidate, ancestor, and descendant objects;
- replacement, disappearance, change, and reduced identity;
- exact digest filtering and invalid matching candidate names; and
- no time-based staging classification or filesystem timestamp use.

### Final

- absent, valid, invalid/incomplete, conflicted, unsupported, unsafe, and unstable;
- exact reuse of Step 5B expectations, payload hashing, reason mapping, and present
  final precedence; and
- no repair or removal.

### Lock

- absent, active boundary, stale boundary, malformed, unsupported, unsafe, unstable,
  I/O failure, identity conflict, and timestamp invalid;
- fixed injected clock and explicit freshness policy;
- retained with valid final; and
- no PID/host liveness, breaking, deletion, refresh, or release.

### Combined states and precedence

- every row in section 10, including complete/incomplete staging with active/stale
  locks, final plus staging, and final plus staging plus lock;
- every precedence family and final/staging/lock tie order;
- multiple candidate aggregation;
- current-state classification after each observable Step 5D failure-matrix shape;
  and
- invalid components never hidden by lifecycle convenience labels.

### Traversal, stability, privacy, and read-only proof

- known-token mode performs no staging directory listing;
- discovery lists only the one namespace staging directory and uses ordinal order;
- 64-candidate, 4096-entry, 1024-byte, depth-64, and 32-diagnostic boundaries plus
  one-over failures;
- unrelated namespaces, digests, roots, locks, finals, and legacy paths are untouched;
- unsafe enumeration objects and replacement races fail closed;
- zero automatic retries;
- deterministic diagnostic ordering, deduplication, truncation, and sanitization;
- no absolute paths, tokens, IDs, raw text, or exceptions escape; and
- a spy surface proves no write, create, mkdir, unlink, rename, replace, chmod,
  fsync, acquire, refresh, release, promotion, cleanup, recovery action, or Step 5D
  mutation dependency is called or reachable.

All existing storage and full regression tests must remain green.

## 21. Acceptance criteria and next action

This contract locks:

- one-cache-identity scope and exact derived paths;
- bounded optional staging discovery;
- content-based staging lifecycle without invented timestamps;
- independence of writer and lock-owner tokens;
- retained-lock terminology and conservative non-use of “orphan”;
- exact staging, final, and lock component states;
- combined lifecycle mapping and failure precedence;
- traversal limits, snapshot stability, diagnostics, and privacy;
- a mutation-free dependency surface; and
- the post-Step-5E policy and mutation boundary.

The exact next approved action is:

**Implement Step 5E read-only recovery inspection.**
