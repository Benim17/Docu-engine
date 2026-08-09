# Step 5B — Read-Only Lock-Lifecycle Observation Contract

Status: **DESIGN LOCKED — approved for Step 5B implementation**
Branch: `feature/cache-storage-foundation`
Parent: `STEP_5_VERSIONED_PERSISTENT_CACHE_ENTRY_CONTRACT.md`
Scope: **Read-only observation of one matching lock when the expected final entry is absent**

## 1. Authority and purpose

The locked Step 5 parent contract is authoritative. This contract supplies the
missing deterministic observation rules required by parent section 23.8:

```text
absent final entry + active matching lock -> LOCKED_OR_IN_PROGRESS
absent final entry + no active matching lock -> MISS
```

It authorizes no lock or filesystem mutation. It does not define acquisition,
heartbeat writing, release, breaking, deletion, recovery, or lock stealing.

## 2. Observation boundary and path identity

The observer accepts only validated request values and injected dependencies required
by Step 5B:

- `ValidatedCacheRoot`;
- `CacheNamespace`;
- `CacheKey`;
- `CacheLookupVerificationPolicy`;
- `CacheLookupRequest.lock_observation_policy: LockObservationPolicy`; and
- the keyword-only `lookup_cache_entry(..., lock_clock=...)` dependency implementing
  `LockObservationClock`.

The public entry point supplies the production clock adapter by dependency default:

```python
def lookup_cache_entry(
    request: CacheLookupRequest,
    *,
    filesystem: ReadOnlyCacheFilesystem = DEFAULT_READ_ONLY_FILESYSTEM,
    lock_clock: LockObservationClock = SYSTEM_LOCK_OBSERVATION_CLOCK,
    observer: CacheLookupObserver | None = None,
) -> CacheLookupResult:
    ...
```

`CacheLookupRequest` contains the mandatory immutable
`lock_observation_policy: LockObservationPolicy`. The system clock default chooses
only the source of UTC `now`; it never chooses or supplies freshness. Tests inject a
fixed clock through the keyword-only dependency. There is no implicit
`active_freshness_seconds` default anywhere in the public API.

The single candidate path is exactly:

```python
derive_lock_path(cache_root.resolved_path, namespace, cache_key)
```

The existing Step 5A function is the sole lock-path derivation rule. Namespace and
the entry digest derived from `CacheKey.canonical_bytes()` are sufficient physical
identity. `logical_id`, artifact metadata, producer release version, runtime
fingerprint values, owner metadata, and timestamps never participate in lock-path
selection. Producer ID and schema participate only through the validated namespace.

The observer must prove that the lexical relative path is exactly
`locks/v1/<namespace>/<shard_1>/<shard_2>/<entry_digest>.lock`, remains beneath the
validated root, and contains no caller-supplied path component. Every component below
the validated root is inspected without following symlinks. An unsafe ancestor or
candidate object maps to `UNSAFE_PATH` / `UNSAFE_OBJECT`. The observer inspects no
lock directory listing, sibling lock, fallback location, staging path, or unrelated
namespace.

The matching lock is observed only after the expected final-entry path is proven
absent. A present final entry is validated without consulting the lock.

## 3. Contract-v1 lock document

The supported document has exactly these fields:

```json
{
  "acquired_at_utc": "2026-07-20T09:00:00Z",
  "entry_digest": "<64 lowercase hex>",
  "heartbeat_at_utc": "2026-07-20T09:00:10Z",
  "host_id": "<opaque installation or adapter identifier>",
  "lock_version": 1,
  "owner_token": "<opaque writer identifier>",
  "process_id": 12345
}
```

Field roles:

- Identity-bearing: `lock_version`, `entry_digest`.
- Lifecycle/freshness: `acquired_at_utc`, `heartbeat_at_utc`.
- Informational future ownership/recovery compatibility: `owner_token`, `host_id`,
  `process_id`.

`owner_token` and `host_id` are opaque canonical tokens. They are not usernames,
hostnames, paths, or activity evidence. Each is 1–128 ASCII characters, begins and
ends with an ASCII letter or digit, and otherwise contains only ASCII letters,
digits, `.`, `_`, or `-`. `process_id` is a positive JSON integer no greater than
`2^63 - 1`; it is never probed by read-only lookup. `entry_digest` is exactly 64
lowercase hexadecimal characters and must equal the expected derived entry digest.
`lock_version` is the JSON integer `1`.

The document is canonical contract JSON: UTF-8 without BOM, Unicode keys sorted,
separators `,` and `:`, no insignificant whitespace or trailing newline, no floats or
non-finite numbers, duplicate keys rejected, and no unknown or missing fields under
version 1. Exact stored bytes must equal the canonical serialization. Implementations
must reuse the Step 5A canonical JSON rules rather than introduce another serializer.

The maximum lock-document size is **16 KiB**. This bound includes the complete stored
document and is part of this contract. A reader performs a bounded no-follow read of
at most the limit plus one byte and never exposes partial bytes as a valid document.

## 4. Time representation

Both timestamps use exactly `YYYY-MM-DDTHH:MM:SSZ` in UTC, with four-digit year and
second precision. Offsets, fractional seconds, leap-second text, whitespace, and
locale-dependent forms are forbidden. Values range from
`1970-01-01T00:00:00Z` through `9999-12-31T23:59:59Z`, subject to calendar validity.

`acquired_at_utc <= heartbeat_at_utc` is required. Equality is valid. Timestamps are
parsed as UTC instants using calendar arithmetic independent of the host locale and
timezone.

## 5. Clock model

Observation receives an immutable `LockObservationClock` dependency whose only
semantic operation returns the current UTC instant at whole-second precision.
Production uses a system UTC wall clock through the injected adapter. Tests inject a
fixed or explicitly advanced clock. Lookup does not read a process-global mutable
clock, timezone, environment variable, or machine performance signal.

Monotonic time may measure one in-process operation but cannot determine persisted
lock age across processes or restarts; it therefore does not participate in the
active/stale predicate.

The supplied current time must use the same valid range and whole-second UTC semantics
as stored timestamps. An invalid clock result is a programmer/dependency error, not a
cache status.

## 6. Freshness policy

`LockObservationPolicy` is immutable and contains exactly:

```text
active_freshness_seconds: int
max_lock_document_bytes: int = 16384
```

`active_freshness_seconds` is mandatory: this contract deliberately supplies no
implicit numeric freshness default. The integration boundary must choose an explicit,
reviewed value for its producer heartbeat cadence. It is a strict positive JSON/Python
integer bounded from 1 through 2,592,000 seconds (30 days). It is not inferred from
machine load, storage type, environment state, lock content, owner metadata, or prior
observations. A lock document can never select or extend its own freshness interval.

`max_lock_document_bytes` is locked to 16 KiB for contract v1. Callers may not raise
or lower it through ordinary configuration; changing it requires contract review.

## 7. Active, stale, and invalid predicates

After safe bounded reading, a matching lock is **ACTIVE** exactly when all are true:

1. the document is canonical, supported contract-v1 JSON and passes strict schema;
2. `entry_digest` equals the expected entry digest;
3. both timestamps are calendar-valid and `acquired_at_utc <= heartbeat_at_utc`;
4. neither timestamp is later than the injected current UTC second; and
5. `now_utc - heartbeat_at_utc <= active_freshness_seconds`.

The exact threshold boundary is active.

A valid matching lock is **STALE** exactly when conditions 1–4 hold and:

```text
now_utc - heartbeat_at_utc > active_freshness_seconds
```

A stale matching lock does not count as active. With the final entry absent, lookup
returns `MISS`; it does not delete, break, refresh, recover, or otherwise alter the
lock. Stale-lock cleanup and recovery belong to later writer/recovery work.

There is no clock-skew tolerance in contract v1. A future `acquired_at_utc` or
`heartbeat_at_utc`, including one second in the future, is invalid rather than active
or stale. This prevents a forged future timestamp from remaining active indefinitely.
`acquired_at_utc > heartbeat_at_utc` is also invalid. These cases map to
`INVALID_ENTRY` / `LOCK_TIMESTAMP_INVALID`.

PID existence, hostname matching, process inspection, filesystem activity, staging
presence, and owner reachability never affect active or stale classification.

## 8. Observable classification

This table applies only when the expected final entry is absent.

| Matching-lock observation | `CacheLookupStatus` | `CacheLookupReason` |
|---|---|---|
| Lock path absent | `MISS` | `None` |
| Valid matching active lock | `LOCKED_OR_IN_PROGRESS` | `None` |
| Valid matching stale lock | `MISS` | `None` |
| Recognizable unsupported positive integer `lock_version` | `UNSUPPORTED_VERSION` | `UNSUPPORTED_LOCK_VERSION` |
| Malformed, noncanonical, duplicate-key, unknown/missing-field, invalid-version-discriminator, or oversized document | `INVALID_ENTRY` | `MALFORMED_LOCK` |
| Valid document with nonmatching `entry_digest` | `INVALID_ENTRY` | `LOCK_IDENTITY_CONFLICT` |
| Invalid or future timestamps | `INVALID_ENTRY` | `LOCK_TIMESTAMP_INVALID` |
| Symlink, FIFO, socket, device, unsupported object, or unsafe ancestor | `UNSAFE_PATH` | `UNSAFE_OBJECT` |
| Permission denial or deterministic read/inspection failure | `INVALID_ENTRY` | `IO_FAILURE` |
| Replacement, disappearance after initial observation, or identity/change instability | `INVALID_ENTRY` | `UNSTABLE_SNAPSHOT` |

An unsupported version is recognized only by a bounded duplicate-key-rejecting probe
whose discriminator is a positive non-boolean JSON integer. Missing, duplicated,
non-integer, boolean, zero, or negative discriminators are malformed. A safely
recognizable future version takes unsupported precedence over its future fields.

None of these rejected states collapse into `MISS`. Existing Step 5 broad-family
precedence remains unchanged: unsafe, unsupported, invalid, integrity, producer,
schema, runtime, lock, miss, hit.

## 9. Stable read and TOCTOU policy

The observer:

1. inspects every root-to-lock component without following symlinks;
2. captures candidate pre-read identity;
3. requires a regular file;
4. opens and bounded-reads it with no-follow and nonblocking safeguards;
5. compares pre-read, handle-open, handle-post-read, and path-post-read identities;
6. rejects any replacement, metadata change, disappearance, or inability to establish
   the adapter's reviewed identity floor as `INVALID_ENTRY` / `UNSTABLE_SNAPSHOT`; and
7. revalidates the cache-root identity before returning.

The lock is observed once with no automatic retry. A later caller may start a new
independent lookup.

## 10. Present-entry precedence

If the expected final entry exists, Step 5B validates it completely and does not
inspect the lock merely to classify lookup state. A lock cannot convert a valid entry
from `HIT` to `LOCKED_OR_IN_PROGRESS`, or hide an invalid entry as `MISS` or
`LOCKED_OR_IN_PROGRESS`. Lock state matters only after entry absence is established.

## 11. Diagnostics, security, and privacy

Diagnostics are stable and sanitized under the Step 5B diagnostic contract. They may
identify the subject as the matching lock and use a validated root-relative contract
path. They never expose absolute paths, usernames, hostnames, owner tokens, process
IDs, raw exception strings, symlink targets, arbitrary document text, process command
lines, device/inode values, or clock implementation details.

## 12. Mutation boundary

Step 5B lock observation never creates, acquires, refreshes, heartbeats, rewrites,
chmods, renames, unlinks, deletes, breaks, quarantines, repairs, recovers, or otherwise
mutates a lock or filesystem object. It creates no diagnostic or metric file. Its
filesystem dependency exposes inspection, path resolution, directory-independent
candidate observation, and bounded regular-file reading only; it exposes no lock
mutation method. Observer callbacks cannot write into the cache.

## 13. Future writer compatibility

A later Step 5C/5D writer may produce this exact canonical document and update its
heartbeat under a separately approved mutation protocol. This contract does not
define atomic acquisition, heartbeat replacement, ownership proof, release, stealing,
breaking, cleanup, or recovery authority. Informational ownership fields are retained
solely so that a future reviewed writer/recovery contract can use them without changing
the observation schema.

## 14. Test contract

Future implementation tests must cover:

- absent lock returns `MISS`;
- active matching lock returns `LOCKED_OR_IN_PROGRESS`;
- stale matching lock returns `MISS` without mutation;
- malformed, unsupported-version, noncanonical, duplicate-key, unknown-field, and
  oversized locks;
- symlink and every practical special-object lock;
- permission failure and sanitized diagnostics;
- replacement or change before, during, and after bounded reading;
- entry-digest identity mismatch;
- any future heartbeat or acquisition timestamp;
- `acquired_at_utc > heartbeat_at_utc`;
- fixed injected-clock determinism;
- freshness age exactly equal to, one second inside, and one second outside the
  explicit threshold;
- unrelated locks are never listed, scanned, or used;
- no mutation method is exposed or invoked;
- valid final entry ignores a matching lock and remains `HIT`; and
- invalid final entry is not hidden by a matching lock.

## 15. Acceptance criteria

This contract is ready for implementation when:

- only one lock derived by existing Step 5A identity is observed;
- active, stale, invalid, unsafe, unsupported, unstable, and I/O outcomes are
  deterministic;
- time and freshness inputs are explicit and testable;
- no machine liveness heuristic is used;
- present-entry precedence matches locked Step 5 section 23.8;
- public diagnostics preserve security and privacy; and
- the observation surface is strictly read-only.
