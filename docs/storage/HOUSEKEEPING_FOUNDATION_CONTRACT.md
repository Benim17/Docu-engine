# Post-Step-5E Housekeeping Foundation Contract

Status: **DESIGN FOUNDATION LOCKED — NO HOUSEKEEPING IMPLEMENTATION APPROVED**

Branch: `feature/cache-storage-foundation`

Scope: **Architecture, authority, safety boundaries, and future design order only**

## 1. Purpose and authority

This document defines the architectural boundary for Documentary Engine's
post-Step-5E housekeeping work. It does not approve implementation of a catalog,
retention policy, cleanup planner, cleanup executor, quota policy, pruning
orchestrator, scheduler, or background worker.

The following locked contracts remain authoritative and are not weakened here:

- `STEP_5_VERSIONED_PERSISTENT_CACHE_ENTRY_CONTRACT.md` for canonical cache identity,
  immutable entry models, paths, and the index boundary;
- `STEP_5B_READ_ONLY_CACHE_LOOKUP_CONTRACT.md` for validation of one final entry;
- `STEP_5B_LOCK_LIFECYCLE_OBSERVATION_CONTRACT.md` for read-only lock semantics;
- `STEP_5D_LOCK_MUTATION_AND_PROMOTION_CONTRACT.md` for ownership-safe writer
  mutation and atomic promotion; and
- `STEP_5E_READ_ONLY_RECOVERY_INSPECTION_CONTRACT.md` for descriptive recovery
  observation without mutation authority.

Where this foundation is less specific, those contracts control. Later
housekeeping slices require their own locked contracts and explicit implementation
approval.

## 2. Locked layered architecture

Housekeeping is a sequence of separate layers:

1. **Persistent Cache Catalog / Index** — records validated, rebuildable summaries
   for efficient discovery.
2. **Retention & Cleanup Policy** — purely classifies trusted observations and
   catalog data.
3. **Cleanup Plan** — freezes a policy decision and its validated preconditions into
   an immutable proposal.
4. **Safe Cleanup Executor** — narrowly performs only separately approved mutation
   after immediate revalidation.
5. **Quota / Storage Budget Policy** — expresses storage pressure and requests
   policy candidate selection; it never deletes.
6. **Automatic Housekeeping / Pruning Orchestrator** — coordinates approved layers
   with explicit configuration and bounds.

The required conceptual flow is:

```text
Storage state / Step 5B and Step 5E observations
                    |
                    v
             Catalog / Index
                    |
                    v
        Retention + Budget Policy
                    |
                    v
          Immutable Cleanup Plan
                    |
                    v
          Safe Cleanup Executor
                    |
                    v
        Post-mutation Verification
```

Observation, policy, planning, mutation, and verification must not be collapsed.
No earlier layer may gain the authority reserved to a later layer.

## 3. Core safety principle

No filesystem object may be deleted merely because it is old, large, associated
with a stale lock, part of incomplete staging, described as suspicious by recovery
inspection, or present while a budget is exceeded.

Deletion requires all of:

1. deterministic observation;
2. explicit policy classification;
3. an immutable cleanup plan;
4. immediate pre-mutation revalidation;
5. mutation authority from a separately approved executor contract; and
6. post-mutation verification.

Step 5E recovery observations are evidence, not deletion authority. A policy result
is a proposal, not mutation authority. A cleanup plan is not durable authority.

## 4. Sources of truth and trust

Housekeeping layers may consume trusted values produced by existing contracts:

- canonical `CacheKey`, `CacheNamespace`, entry digest, and derived identity;
- strict Step 5A cache-entry models;
- a fully validated Step 5B cache hit and its validated entry reference;
- a structured Step 5E recovery observation;
- a validated cache root and root-relative contract paths; and
- future catalog records generated from validated observations under an independently
  versioned catalog schema.

They must not trust arbitrary directory names, caller-supplied cache-object paths,
raw JSON, symlink targets, unvalidated filesystem metadata, `InventoryReport` as
mutation truth, or a stale catalog record without revalidation.

The filesystem state validated under the applicable storage contract remains
authoritative over the catalog. A catalog disagreement never makes an unsafe or
invalid filesystem object safe, never turns absence into presence, and never grants
mutation authority.

## 5. Persistent Cache Catalog / Index boundary

### 5.1 Purpose

A future persistent catalog may provide bounded, fast discovery and avoid repeating
whole-cache validation for every policy query. It may summarize identities and
retain safe metadata useful to later policy evaluation.

Conceptual record data may include, when supported by validated current evidence:

- canonical cache identity and entry digest;
- namespace;
- observed final-entry presence;
- validated payload or entry size;
- validated creation timestamp;
- verification state and observation generation;
- Step 5E recovery-state summary; and
- an independent catalog schema version.

These are candidate design inputs, not required fields locked by this foundation.
Last-use data is specifically excluded until section 15's prerequisite is designed.
Physical paths, if represented at all, must be derived or validated root-relative
contract paths and must never become caller-controlled mutation targets.

### 5.2 Non-authority

The catalog must not:

- replace Step 5B final-entry validation or Step 5E recovery inspection;
- declare a path safe;
- authorize deletion, lock breaking, repair, or promotion;
- infer truth from a missing record;
- override a newer filesystem observation; or
- become a substitute for immediate pre-mutation identity proof.

Catalog loss or corruption must be recoverable by deterministic rebuild from
authoritative storage inspection. Loss of the catalog may reduce performance but
must not corrupt valid cache entries.

### 5.3 Independent mutation boundary

A future catalog writer may add, update, or remove only catalog-owned records. It
may not create, alter, promote, repair, or delete cache entries; acquire, refresh,
break, or remove locks; or mutate staging and final namespaces.

Catalog updates and cache-entry mutations are separate operations. Catalog update
failure after a valid cache operation must leave the cache entry valid and
untouched. A later reconciliation may repair the catalog, not the cache entry.

No database or file technology is selected here.

## 6. Retention and cleanup policy boundary

Retention is pure policy. Given immutable trusted inputs, it may eventually produce
classifications equivalent to:

- `RETAIN`;
- `ELIGIBLE_FOR_CLEANUP`;
- `PROTECTED`; or
- `INSUFFICIENT_EVIDENCE`.

The exact public model and vocabulary belong to the H3 contract. No classification
in this document authorizes deletion.

Future policy designs may evaluate regenerability, age, validated size, explicit
user or project protection, trustworthy recency of use, source reproducibility,
namespace or artifact type, recovery state, and storage pressure. This foundation
chooses no time threshold, score, weight, priority, or default.

Policy must be deterministic for the same immutable inputs. Missing, contradictory,
unsupported, or stale evidence fails closed to protection or insufficient evidence,
not cleanup eligibility.

## 7. Protected and insufficient-evidence states

Automatic cleanup may never delete the following without a separately locked policy
contract that supplies adequate evidence and an approved executor target category:

- an active lock;
- an unstable snapshot or changed identity;
- an unsafe object, path, or ancestor;
- unknown ownership or an ownership conflict;
- a currently referenced or active-project artifact;
- insufficient verification or missing required identity evidence;
- unsupported future-version data;
- any object whose root, containment, type, identity, or absence cannot be re-proven;
- any replacement object encountered after planning; and
- any state for which policy inputs are incomplete or contradictory.

Malformed or invalid data is not automatically safe to delete. A stale lock does
not prove owner death and is not deletion authority. Protection is fail-closed.

Explicit pinning, active-project dependency, and user-protected cache-item concepts
are legitimate future policy dependencies. Current cache-entry metadata does not
represent them. This foundation therefore does not retrofit those fields into Step
5A models or invent a user interface.

## 8. Step 5E interaction

Step 5E statuses are descriptive policy inputs. They do not imply actions. This
includes:

- `FINAL_PUBLISHED` and `FINAL_PUBLISHED_LOCK_RETAINED`;
- `FINAL_PUBLISHED_WITH_SUPERSEDED_STAGING` and its lock-bearing form;
- `UNPUBLISHED_COMPLETE_STAGING` and `INCOMPLETE_STAGING`;
- complete or incomplete staging with active or stale locks;
- `ACTIVE_LOCK_WITHOUT_ENTRY` and `STALE_LOCK_WITHOUT_ENTRY`;
- `RECOVERY_UNSAFE`, `RECOVERY_UNSUPPORTED`, `RECOVERY_UNSTABLE`, and
  `RECOVERY_INVALID`; and
- `EMPTY`.

The locked relation is:

```text
Step 5E classification != cleanup eligibility
```

For example, a published final may be valuable, a complete staging entry may still
belong to an interrupted operation, a stale lock does not prove abandonment, and an
invalid object may be unsafe to traverse. Later policy must interpret observations
without changing their meanings.

## 9. Immutable CleanupPlan concept

A future `CleanupPlan` is an immutable proposal generated by approved policy and
planning layers. A plan may identify:

- exact canonical target identity;
- an expected validated root-relative contract path;
- an approved target kind;
- observation basis and generation;
- required preconditions;
- the stable policy decision or reason; and
- expected reclaimable bytes only when safely validated.

A plan must contain no arbitrary path string and no mutation-capable callback. The
executor must independently derive and check paths from trusted identity. A plan may
not broaden its target during execution, substitute sibling objects, or infer a
target from a catalog-only name.

This foundation does not decide which target kinds may eventually be deleted.

## 10. Plan staleness and immediate revalidation

A cleanup plan is not durable mutation authority. Immediately before any mutation,
the future executor must revalidate at least that:

- the validated root remains the same stable directory;
- the exact target still exists when presence is required;
- containment, path derivation, object type, and target identity still match;
- the current target state still satisfies every policy precondition;
- no active lock, unsafe state, unsupported state, or instability has appeared;
- required protection and reference evidence remains current; and
- the platform supplies the exact reviewed mutation capability.

Any mismatch, replacement, disappearance, reduced identity evidence, unavailable
capability, or stale observation fails closed. There is no “best effort delete
whatever is at the pathname,” target widening, or automatic retry against a new
object.

## 11. Safe Cleanup Executor boundary

The future executor is a narrow mutation layer. It may eventually delete only target
categories explicitly approved by its own locked contract and only through a valid
plan whose preconditions have just been re-proven.

It must not:

- recursively remove an arbitrary path;
- follow symlinks or traverse outside the exact target;
- scan for additional deletion candidates;
- delete a replacement object after an identity race;
- infer cleanup eligibility;
- promote, repair, migrate, quarantine, or rewrite content;
- break an active lock;
- treat malformed or stale state as implicit authority; or
- accept a general-purpose filesystem mutation surface.

The executor's dependency must expose only reviewed target-specific operations.
Post-mutation verification must confirm the exact authorized outcome and must not
turn an uncertain outcome into a second deletion attempt.

## 12. Delete-race safety blocker

Cleanup has the same pathname replacement hazard identified by Step 5D. The
following sequence is forbidden:

```text
stat target
compare identity
recursively or by pathname delete whatever now occupies target
```

Another object may replace the target between comparison and deletion. Safe cleanup
therefore requires a separately reviewed identity-safe mutation strategy for each
approved target type and supported platform. The strategy must bind authorization
to the same object that is removed, fail closed on replacement, and define handling
for partial or uncertain outcomes.

This foundation makes no claim that current macOS APIs already provide every needed
conditional-delete primitive. Exact syscalls, native adapters, recursive-directory
safety, capability fallbacks, and platform support are unresolved H5 design work.
Cleanup mutation implementation cannot be approved until this blocker is resolved.

## 13. Quota and storage-budget policy

Quota and budget are policy inputs, not deletion mechanisms. Future designs may
consider global cache, namespace, or project budgets and soft or hard pressure
thresholds. This foundation selects no numeric default, unit allocation rule, or
enforcement priority.

Budget pressure may request candidate classification from retention policy and may
lead to an immutable cleanup plan. It must not bypass protected states, evidence
requirements, plan construction, immediate revalidation, executor capability checks,
or post-mutation verification. Being over budget never grants direct deletion
authority.

## 14. Automatic housekeeping and pruning boundary

Automatic housekeeping is the final orchestration layer, not a hidden deletion
path. Its conceptual sequence is:

```text
inspect authoritative state
refresh or rebuild catalog if required
evaluate retention and budget policy
build immutable plan
revalidate immediately
execute only approved target-specific mutation
verify outcome
update or reconcile catalog
```

Future automation must be explicitly configurable, bounded in work and effects,
observable, interruptible at safe boundaries, and disabled unless its later contract
and product policy approve otherwise. There is no scheduling, background worker,
automatic deletion default, or pruning implementation in this foundation.

## 15. Cache reuse and access tracking

Housekeeping must preserve the primary cache goal: safely reuse expensive valid
artifacts. A Step 5B `HIT` is normally valuable cached state, not garbage merely
because it exists. Future approved retention or budget policy may select a valid
entry only after explicit policy classification and immediate revalidation.

Current contracts persist `created_at_utc`, which describes entry creation and is
not last use. They contain no trustworthy persisted last-use or access timestamp.
Step 5B lookup is read-only and deliberately writes no access time or diagnostic
file. Filesystem atime, mtime, ctime, birth time, directory activity, and process
memory are not authoritative last-use evidence.

Therefore no authoritative LRU policy is currently implementable. Housekeeping must
not use filesystem atime or reinterpret creation time as last use. A future access
tracking mechanism requires its own design covering write authority, durability,
privacy, concurrency, amplification, failure isolation, catalog reconciliation, and
the guarantee that lookup semantics remain correct when tracking fails.

## 16. Catalog rebuild and reconciliation

The catalog may be missing, deleted, corrupt, unsupported, or stale. A future rebuild
must reconstruct it from authoritative, validated storage observation. Rebuild must
be bounded, deterministic, resumable only under explicitly designed semantics, and
safe to repeat.

Catalog/filesystem disagreement produces reconciliation evidence, not cleanup
authority. Rebuild does not delete, repair, promote, break locks, or classify an
unvalidated path as a cache entry. This foundation does not design a whole-cache
scanner, persistence technology, transaction format, or checkpoint scheme; those
belong to H1 and H2.

## 17. Diagnostic-domain separation and privacy

Future diagnostics remain separated by layer:

- **Observation diagnostics** describe what exists and what validation established.
- **Policy diagnostics** explain why trusted evidence produced retention,
  protection, eligibility, or insufficient-evidence classification.
- **Executor diagnostics** describe which authorized mutation was attempted and
  what verified outcome occurred.

One domain must not masquerade as another. In particular, an observation diagnostic
is not a policy decision and a policy diagnostic is not execution proof.

Public diagnostics must use bounded stable codes, trusted subjects, and validated
relative identifiers. They expose no arbitrary or absolute paths, raw JSON, content,
tokens, host or process identifiers, filesystem IDs, symlink targets, exception
text, database internals, or native adapter details.

## 18. Proposed future design and implementation decomposition

The following order is a planning proposal. Every slice remains unapproved for
implementation until its own design is locked and explicit approval is given.

| Slice | Purpose | Mode | Prerequisite | Key blocker or design question |
|---|---|---|---|---|
| **H1 — Persistent Cache Catalog / Index Contract** | Define independently versioned, rebuildable record models, bounded discovery inputs, and catalog authority | Read-mostly; catalog-owned record mutation only | Storage foundation through Step 5E | Record schema, catalog root/layout, atomic catalog updates, bounds, and technology-neutral failure semantics |
| **H2 — Catalog Rebuild / Reconciliation Contract** | Reconstruct and reconcile catalog summaries from authoritative storage inspection | Read-only storage; catalog-owned record mutation | Locked H1 | Bounded traversal, interruption/checkpoint semantics, duplicate observations, and stale-record handling without cleanup |
| **H3 — Retention & Cleanup Policy Contract** | Produce pure retain/protect/eligible/insufficient-evidence classifications | Pure/read-only | Trusted H1/H2 records and Step 5B/5E meanings | Regenerability, protection inputs, trustworthy access evidence, policy versioning, and thresholds |
| **H4 — Immutable Cleanup Planning Contract** | Convert approved policy decisions into exact immutable plans and preconditions | Pure/read-only | Locked H3 | Target taxonomy, observation generations, plan expiry/staleness, reclaimable-byte proof, and path-free identity representation |
| **H5 — Identity-Safe Cleanup Mutation Protocol** | Execute only approved plans and verify exact outcomes | Mutation | Locked H4 plus platform capability review | Conditional deletion and recursive-directory race safety on every supported platform |
| **H6 — Quota / Storage-Budget Policy Contract** | Express bounded storage pressure without bypassing retention safety | Pure/read-only policy | Validated size accounting and locked H3/H4 | Scope hierarchy, hard/soft semantics, accounting ambiguity, and no numeric defaults yet |
| **H7 — Automatic Housekeeping Orchestration Contract** | Coordinate inspection, catalog, policy, planning, execution, verification, and reconciliation | Orchestration; mutation only through H5 | All invoked layers separately approved | Configuration, work/effect bounds, cancellation, scheduling, observability, and safe partial failure |

The sequence may be revised only by a later planning decision that preserves the
layer and authority boundaries in this foundation.

## 19. Source Ingestion decision gate

Source Ingestion & Understanding remains the next major product direction after
Cache & Storage / Housekeeping reaches the required maturity. This foundation does
not silently decide that every H1-H7 implementation must finish first.

A future planning gate must explicitly choose the minimum housekeeping maturity for
Source Ingestion. The decision should evaluate at least:

- the completed storage foundation through Step 5E;
- reliable lookup, staging write, lock ownership, and atomic promotion;
- whether a read-only catalog and bounded rebuild are required for ingestion-scale
  discovery and observability;
- expected storage growth and whether it can initially be managed manually;
- whether protection/pinning is needed before ingestion creates expensive artifacts;
- operational consequences of deferring cleanup mutation, quotas, and automatic
  pruning; and
- whether H5's identity-safe deletion blocker is relevant to the initial ingestion
  release or may remain later because no automatic deletion is enabled.

One permissible future decision could require H1/H2 maturity while deferring cleanup
mutation if storage growth is explicitly bounded and manually managed. Another could
require more of H3-H6 first. Neither choice is approved here.

## 20. Explicit non-decisions and prohibited implementation

This foundation does not choose or authorize:

- retention days, age thresholds, LRU weights, scores, or eviction priorities;
- gigabyte or other numeric quotas;
- cleanup schedules or automatic deletion defaults;
- any target category as safe to delete;
- exact cleanup syscalls or platform adapters;
- an index database or file technology, including SQLite versus JSON;
- whole-cache scanner behavior beyond the H2 design requirement;
- access tracking or mutation during lookup;
- cleanup, deletion, lock breaking, repair, promotion retry, quarantine, migration,
  indexing, quota enforcement, pruning, or background workers; or
- Source Ingestion implementation details.

No Python, test, roadmap, or existing locked-contract change is authorized by this
document.

## 21. Approval boundary and next design action

This foundation is locked only as an architectural and safety boundary:

**DESIGN FOUNDATION LOCKED — NO HOUSEKEEPING IMPLEMENTATION APPROVED**

The exact next design action is:

**Design H1 — Persistent Cache Catalog / Index contract.**

H1 design must preserve filesystem authority, independent catalog versioning,
rebuildability, bounded behavior, failure isolation, privacy, and the prohibition on
catalog-derived mutation authority. It must not begin implementation without a
separate approval.
