# Step 5D — Lock Mutation and Atomic Promotion Contract

**Status: DESIGN LOCKED — APPROVED FOR IMPLEMENTATION**

This document is the normative writer-side mutation protocol for Step 5D. It
complements, and does not weaken, the read-only Step 5B lock-observation contract.
Step 5D arbitrates and publishes an already verified Step 5C staging entry. It does
not construct payloads, break locks, recover interrupted work, or perform cleanup.

## 1. Scope and invariants

Step 5D operates on exactly one validated cache root, namespace, cache key, verified
`StagedCacheEntryReference`, derived lock path, and derived final path. It may:

1. exclusively create the exact matching lock;
2. revalidate its own lock and staging entry;
3. atomically rename that staging directory to the absent final path; and
4. release only the exact lock it still demonstrably owns when the platform supplies
   an identity-conditional unlink capability; otherwise retain the lock safely.

It never scans siblings and never accepts caller-supplied lock or final paths.
`logical_id` does not participate in physical identity.

## 2. Public concepts and dependencies

The implementation uses immutable equivalents of:

- `CachePromotionRequest`, containing a validated cache root, namespace, cache key,
  the validated Step 5C `writer_token`, verified `StagedCacheEntryReference`, owner
  metadata, and explicit dependencies;
- `WriterLockClock`, returning UTC whole-second instants under the Step 5B clock model;
- `WriterOwnerTokenSource`, returning a fresh opaque canonical token;
- `OwnedWriterLock`, retaining the exact path, entry digest, owner token, canonical
  parsed document, and stable creation identity;
- `PromotedCacheEntryReference`, containing the final path and the strict trusted
  identity/models carried by the verified staging reference; and
- a narrow `CachePromotionFilesystem`, separate from
  `ReadOnlyCacheFilesystem`.

Wrong dependency types, invalid clock/token output, inconsistent request identity,
or an unavailable required mutation capability are programmer/dependency errors.
Expected contention, unsafe objects, I/O failures, collisions, and instability are
deterministic promotion outcomes.

## 3. Lock format and path

Writer locks use exactly canonical lock v1:

```json
{
  "acquired_at_utc": "2026-07-20T09:00:00Z",
  "entry_digest": "<64 lowercase hex>",
  "heartbeat_at_utc": "2026-07-20T09:00:00Z",
  "host_id": "<opaque canonical token>",
  "lock_version": 1,
  "owner_token": "<opaque canonical token>",
  "process_id": 12345
}
```

Field syntax, process-ID bounds, canonical JSON, and the 16 KiB maximum are exactly
those in the Step 5B lock-observation contract. No writer extension fields exist.
The path is only:

```python
derive_lock_path(cache_root.resolved_path, namespace, cache_key)
```

Every component is contained beneath the validated root and inspected without
following symlinks. Acquisition never lists the lock directory.

## 4. Owner-token trust model

`owner_token` is the authoritative ownership nonce. A token source is injected and
called once per acquisition attempt. It must produce 1–128 ASCII characters under
the existing canonical token grammar and at least 128 bits of cryptographic entropy
in production. A UUID4 hex value satisfies the minimum. Tokens are opaque and are
never intentionally reused across independent attempts.

The token must not be derived solely from PID, host ID, time, cache identity, or
entry digest. Test sources may return fixed tokens. `host_id` and `process_id` are
validated informational fields and never prove ownership.

## 5. Writer clock and initial document

`WriterLockClock.now_utc()` uses `YYYY-MM-DDTHH:MM:SSZ`, UTC, whole seconds, and the
1970–9999 range. It is injected, has no local-time fallback, and persists no
monotonic value.

For acquisition:

```text
acquired_at_utc == heartbeat_at_utc == writer_clock.now_utc()
```

The clock is read exactly once for the initial document.

## 6. Atomic acquisition

The filesystem creates the exact lock with one exclusive-create operation
semantically equivalent to `O_CREAT|O_EXCL`, with no-follow protection and mode that
does not grant unintended write access. It writes only the canonical complete v1
bytes through the newly created handle, fsyncs the file, obtains its stable identity,
and fsyncs the lock parent before returning success. Ownership is not established
until all those operations succeed.

No pathname existence check followed by nonexclusive creation is permitted. If any
object wins the path race, acquisition returns `LOCK_ALREADY_EXISTS`; the object is
not read for freshness, overwritten, removed, or replaced. A known symlink/special
object or unsafe ancestor returns `UNSAFE_LOCK_PATH`; deterministic filesystem
failure returns `LOCK_IO_FAILURE`; ancestor or identity change returns
`UNSTABLE_LOCK_PATH`.

Failure after exclusive creation but before durable ownership has an indeterminate
owned-lock outcome. The implementation may attempt release only through the complete
ownership-safe release protocol using the generated token and captured identity. It
must not perform unconditional cleanup.

## 7. Ownership proof

Memory of acquisition is insufficient. Before heartbeat replacement, promotion, or
release, ownership proof requires all of:

1. the exact derived lock path;
2. a no-follow, bounded, stable read under the reviewed identity floor;
3. canonical supported lock v1;
4. expected `entry_digest` equality;
5. exact `owner_token` equality; and
6. equality with the retained stable creation identity, or with the identity produced
   by the writer's last successful conditional heartbeat replacement.

Mismatch, malformed content, disappearance, replacement, unsafe type, reduced
identity below the supported capability floor, or unstable read means ownership is
lost or unknown. No mutation follows.

## 8. Heartbeat decision and refresh

The normal Step 5D orchestration does **not** refresh heartbeat. Step 5C finishes
before lock acquisition, and Step 5D performs one bounded revalidation and rename;
there is no background thread, cadence, retry loop, or autonomous scheduler.

An explicit `refresh_owned_lock()` operation is an optional stronger capability for a future caller that
must divide promotion into bounded externally controlled phases. It is never invoked
implicitly. It:

1. proves ownership;
2. reads the injected clock once;
3. rejects time earlier than `acquired_at_utc` or earlier than the stored heartbeat;
4. preserves every field except `heartbeat_at_utc`;
5. writes canonical replacement bytes to an exclusively created private name in the
   same lock directory;
6. fsyncs the replacement;
7. re-proves the original identity and ownership;
8. uses `replace_if_same_identity(original, replacement, expected_identity)`;
9. fsyncs the lock parent; and
10. stably reads the resulting lock and retains its new identity.

On platforms without `replace_if_same_identity`, refresh is unsupported and fails
closed without touching the live lock. This optional capability is not part of the
normal promotion capability floor and cannot block ordinary Step 5D promotion.

The private replacement name is derived from the owner token plus fresh injected
nonce material, is never caller supplied, and is not a second observable lock. The
filesystem primitive must atomically refuse if the original no longer has the
expected identity. Truncate-and-rewrite of the live lock is forbidden. Failure never
authorizes deletion of an unknown replacement; private-temp recovery is outside
Step 5D.

## 9. Ownership-safe release and the release race

Release first performs the full ownership proof, then calls:

```text
unlink_if_same_identity(lock_path, expected_identity)
```

This narrow primitive must atomically unlink only if the directory entry still names
the expected stable object. A pathname re-check followed by ordinary unlink is not
sufficient. Descriptor identity plus an unguarded pathname unlink is not sufficient.
Identity-conditional unlink is an optional stronger platform capability, not a
prerequisite for promotion. If unavailable, Step 5D performs no pathname unlink,
leaves the still-owned canonical lock in place, and returns successful outcome
`PROMOTED_LOCK_RETAINED`. A pathname re-check followed by unlink remains forbidden.

After successful conditional unlink, the lock parent is fsynced. Outcomes are:

- `RELEASED` — expected owned identity conditionally removed and parent flushed;
- `ALREADY_ABSENT` — no deletion performed;
- `OWNERSHIP_LOST` — content, token, digest, or identity mismatch;
- `UNSAFE_LOCK_PATH` — unsafe object or ancestor;
- `UNSTABLE_LOCK_PATH` — stable ownership could not be established;
- `RELEASE_IO_FAILURE` — deterministic failure, with presence treated as unknown; or
- `RELEASE_CAPABILITY_UNAVAILABLE` — no safe conditional-unlink primitive.

Only `RELEASED` means release completed. `RELEASE_CAPABILITY_UNAVAILABLE`, ownership
loss, instability, or release I/O after successful promotion preserve promotion
success and map to `PROMOTED_LOCK_RETAINED`; the lock or replacement remains
present/unknown and untouched. Callers must not retry promotion because release was
retained. No force-delete exists.

## 10. Pre-existing lock policy

Any pre-existing object at the exact lock path causes acquisition failure. Step 5D
does not inspect age to steal it, delete it, retry, wait, scan, or arbitrate owner
liveness. Stale-lock breaking and orphan handling require later separately approved
recovery mutation work.

## 11. Staging-to-lock lifecycle order

The authoritative order is:

1. receive one verified Step 5C `StagedCacheEntryReference`;
2. validate request identity and derived paths;
3. acquire the exact writer lock;
4. prove ownership;
5. fully revalidate the staging entry without constructing or modifying it;
6. inspect the exact final destination and its ancestors;
7. establish same-filesystem atomic-rename capability;
8. atomically rename staging to final;
9. perform minimum post-promotion checks; and
10. conditionally release when supported, otherwise retain the lock and report the
    corresponding successful promotion outcome.

Step 5C does not retroactively acquire or hold this lock.

## 12. Staging revalidation and promotion prerequisites

The request accepts only `StagedCacheEntryReference`, never an arbitrary path. Before
rename, Step 5D requires:

- reference namespace, entry digest, cache-key reference, metadata, manifest, and
  marker equal the request-derived trusted identity;
- staging path equals `derive_staging_entry_path(...)` using the request's validated
  Step 5C writer token, matches the reference path, and remains beneath the root;
- every ancestor and the staging directory are stable, safe, and no-follow;
- the exact four-object structure remains present;
- canonical documents, their digests, payload set, sizes, hardlink policy, and every
  payload SHA-256 revalidate successfully;
- `COMPLETE` is valid; and
- root/staging identities remain stable through the immediate rename gate.

The existing Step 5B validation primitives may be adapted privately for a staging
path. No public `HIT` is produced.

## 13. Final-path collision policy

The final path is only `derive_final_entry_path(...)`. Step 5D never overwrites any
existing final-path object. If a directory, regular file, symlink, FIFO, socket,
device, or other object exists, promotion returns `FINAL_PATH_OCCUPIED`; staging and
the final object remain untouched, and owned-lock release is attempted safely.

A valid existing final entry is not treated as this promotion's success and is not
returned as a promoted reference. Higher-level code may independently perform a
read-only lookup. Invalid final content is neither repaired nor removed.

Every final ancestor is checked no-follow. Unsafe or unstable ancestors return
`UNSAFE_FINAL_PATH` or `UNSTABLE_FINAL_PATH` before rename.

## 14. Same-filesystem capability

The filesystem proves that the staging directory and the existing final parent have
equal stable device/filesystem identity and that its rename primitive supplies atomic
directory-entry rename without replacement. Missing device evidence or an adapter
without a reviewed atomic no-replace rename capability fails closed.

Cross-filesystem evidence returns `CROSS_FILESYSTEM`; actual `EXDEV` returns
`CROSS_FILESYSTEM_RACE`. There is no copy/delete, merge, or piecemeal fallback.
Staging remains when rename did not occur and final remains absent.

The current supported macOS adapter implements the semantic no-replace operation
through Darwin `renameatx_np(..., RENAME_EXCL)`. The binding mechanism is not
normative. Required behavior is: absent destination atomically moves the directory;
an existing destination fails without replacement; `EXDEV` is a cross-filesystem
failure; and no copy/delete fallback occurs.

## 15. Atomic promotion

Promotion is one operation:

```text
rename_directory_noreplace(staging_path, final_path)
```

The primitive must atomically move the complete directory only if the final directory
entry is absent. Plain rename with replacement semantics is forbidden. A collision
reported by the primitive maps to `FINAL_PATH_OCCUPIED_RACE`; neither winner nor
staging is deleted. No file beneath the final path is written individually.

Immediately before the call, Step 5D re-proves owned lock, root and staging identity,
final absence/ancestor safety, and same-filesystem evidence. There are zero automatic
retries.

## 16. Post-promotion verification and result

After rename, Step 5D requires only:

- staging path absent;
- final path is a directory at the exact derived path;
- final directory identity equals the pre-rename staging identity where stable IDs
  are available; and
- `COMPLETE` exists as a regular no-follow object with the retained marker identity
  and canonical model.

Full payload rehash is not repeated: the atomic same-filesystem rename preserves the
immediately revalidated staging object. Failure of a post-check returns
`PROMOTED_OUTCOME_UNCERTAIN`; it does not rename back, delete, repair, or recover.

`PromotedCacheEntryReference` is immutable and contains the final path, entry digest,
namespace, cache-key reference, strict metadata, manifest, marker, and the verified
payload summary. It is distinct from `ValidatedCacheEntryReference` and does not
claim that a later read-only lookup was performed.

Successful promotion has exactly two release outcomes:

- `PROMOTED_AND_RELEASED` — atomic promotion and post-check succeeded, ownership was
  re-proven, and identity-conditional unlink plus parent flush succeeded.
- `PROMOTED_LOCK_RETAINED` — atomic promotion and post-check succeeded, but safe
  conditional release was unavailable or could not complete without risking a
  replacement lock. The lock is retained/present/unknown and no unsafe cleanup occurs.

Both outcomes mean staging is absent and final is published. Release failure never
rolls back final state and callers must not retry promotion. Under Step 5B present-entry
precedence, the promoted valid final entry remains `HIT` even with a retained matching
lock; it never becomes `LOCKED_OR_IN_PROGRESS`. Lock observation affects public lock
state only while the final entry is absent.

## 17. Durability model

Atomic visibility and crash durability are separate:

- acquisition fsyncs the lock file and lock parent before ownership is established;
- Step 5C is responsible for payload/document file flushes and staging-directory
  durability before it returns its verified reference;
- promotion fsyncs the final parent after successful rename;
- conditional release fsyncs the lock parent after unlink; and
- explicit heartbeat replacement fsyncs replacement content and the lock parent.

The mandatory normal-path capability floor requires meaningful file fsync, directory
fsync, stable no-follow identity evidence, atomic exclusive create, lock ownership
revalidation, same-filesystem evidence, and atomic no-replace directory rename.
Identity-conditional unlink and identity-conditional replacement are optional stronger
capabilities. Their absence selects retained-lock success and unsupported explicit
heartbeat respectively; it does not block ordinary promotion. An adapter lacking a
mandatory capability fails closed before the associated mutation. The contract claims
no stronger guarantees than those capabilities provide.

## 18. Failure-state matrix

| Failure point | Staging | Final | Lock |
|---|---|---|---|
| Before/acquiring lock loses race | present | untouched | other/unknown object present |
| Acquisition I/O before durable ownership | present | untouched | absent or unknown |
| Ownership proof fails | present | untouched | present/absent unknown; never deleted |
| Staging revalidation fails | present | untouched | safe owned release attempted |
| Final inspection/collision fails | present | existing state untouched | safe owned release attempted |
| Same-filesystem/capability check fails | present | absent/untouched | safe owned release attempted |
| Rename reports no move, including EXDEV | present | absent/untouched | safe owned release attempted |
| Rename and post-check succeed; conditional release succeeds | absent | promoted directory present | absent; `PROMOTED_AND_RELEASED` |
| Rename and post-check succeed; release capability unavailable | absent | promoted directory present | retained; `PROMOTED_LOCK_RETAINED` |
| Rename and post-check succeed; ownership lost before release | absent | promoted directory present | replacement/unknown untouched; `PROMOTED_LOCK_RETAINED` |
| Rename and post-check succeed; release I/O fails | absent | promoted directory present | present/unknown; `PROMOTED_LOCK_RETAINED` |
| Post-promotion verification fails | absent or uncertain | present/uncertain, untouched | safe owned release attempted; result uncertain |
| Release ownership fails | according to prior phase | according to prior phase | replacement/unknown lock untouched |
| Conditional unlink/fsync fails | according to prior phase | according to prior phase | present or unknown |

No row authorizes staging cleanup, final rollback, lock breaking, retry, or repair.
No successfully published final entry is converted into generic promotion failure
solely because release could not complete safely.

## 19. Narrow mutation filesystem boundary

`CachePromotionFilesystem` exposes only capabilities needed here:

- safe no-follow inspection and bounded canonical lock read;
- exclusive canonical lock creation plus file/parent flush;
- optional `replace_if_same_identity` for explicit heartbeat;
- optional `unlink_if_same_identity` for clean release;
- staging revalidation reads;
- same-filesystem capability proof;
- `rename_directory_noreplace`; and
- directory flush.

It exposes no arbitrary delete, recursive cleanup, copy fallback, final-file writer,
lock scan, chmod, quarantine, repair, or index mutation. Step 5B's read-only protocol
is unchanged.

## 20. Step 5E and later boundary

Reserved for Step 5E or later separately approved work:

- orphan staging/lock inspection;
- interrupted-promotion classification;
- stale-lock breaking or stealing;
- orphan cleanup or deletion;
- rollback, repair, quarantine, or recovery promotion;
- retry arbitration and directory scanning; and
- eviction, indexing, quotas, and housekeeping.

Step 5E remains read-only: it may observe and classify retained or orphan locks but
may not delete them. Eventual retained/stale-lock removal belongs to later separately
approved recovery or cleanup mutation, not Step 5E.

Step 5D touches only the active request's exact staging, final, and lock paths.

## 21. Required implementation tests

Implementation approval requires deterministic injected tests for:

- exclusive acquisition success, contention, existing/unsafe objects, ancestor races,
  create failure, no overwrite, and no sibling scan;
- token/digest mismatch, malformed/replaced/disappeared locks, stable ownership proof,
  conditional-release success, unavailable capability producing retained-lock success,
  ownership loss leaving a replacement untouched, and release I/O not rolling back final;
- release racing with replacement, proving a replacement is never unlinked;
- explicit heartbeat preservation/update where supported, canonical atomic replacement,
  fixed clock, ownership loss, unsupported replacement capability not blocking normal
  promotion, and no torn live document or truncate fallback;
- complete staging success, incomplete/replaced/unsafe staging rejection, and exact
  identity/model revalidation;
- every final collision object type and final-ancestor replacement;
- same-filesystem success, missing capability, cross-filesystem evidence, actual
  `EXDEV`, Darwin `RENAME_EXCL` adapter behavior, and absence of copy fallback;
- exact acquire → verify staging → inspect final → rename → post-check → release order;
- failures injected at every matrix row with exact staging/final/lock state assertions;
- final content identity and absence of piecemeal writes; and
- valid final plus retained lock producing a later read-only `HIT`; and
- no lock breaking, scanning, cleanup, recovery, retry, or Step 5E behavior.

## 22. Acceptance criteria

This contract is implementation-approved because it normatively fixes:

- exact lock schema and derivation;
- atomic durable acquisition and fresh-token trust;
- stable ownership proof;
- explicit-only optional heartbeat with fail-closed unsupported capability;
- optional identity-conditional release that closes the replacement race without
  blocking retained-lock promotion success;
- conservative existing-lock and existing-final behavior;
- verified staging input and lock-before-promotion ordering;
- same-filesystem no-replace atomic rename;
- minimum post-promotion checks and distinct promoted reference;
- durability capability floor and deterministic failure states; and
- the Step 5E/recovery boundary.

Normal Step 5D is implementable on the current supported macOS environment through
exclusive creation, no heartbeat refresh, Darwin atomic no-replace rename, and
retained-lock success when conditional release is unavailable. The exact next approved
action is implementation of Step 5D against this locked contract. ROADMAP advancement
remains deferred until that implementation is reviewed.
