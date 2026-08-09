# H1 — Persistent Cache Catalog / Index Contract

Status: **DESIGN LOCKED — APPROVED FOR H1 IMPLEMENTATION**

Branch: `feature/cache-storage-foundation`

Scope: **Rebuildable catalog models, canonical per-record storage, bounded lookup and iteration, and catalog-owned mutation only**

## 1. Purpose and authority

H1 defines a disposable performance and discovery layer over the authoritative
persistent cache. It provides fast, bounded answers such as:

- which previously validated cache identities are cataloged;
- which namespaces have cataloged final-entry observations;
- which validated logical payload sizes and creation metadata were last recorded;
  and
- which Step 5E recovery summary was last recorded for an identity.

H1 does not authoritatively answer whether a final entry currently exists, whether
it is currently valid, whether a lock currently exists, or whether any object is
safe to delete. The filesystem, validated through Step 5B or Step 5E as applicable,
remains authoritative.

This contract refines `HOUSEKEEPING_FOUNDATION_CONTRACT.md` and does not weaken the
locked Step 5A–5E contracts. H1 approves only catalog implementation. It does not
approve H2 reconciliation, retention, cleanup planning or execution, quotas,
pruning, or Source Ingestion.

The authority equations are:

```text
Catalog hit    != Step 5B HIT
Catalog miss   != cache MISS
Catalog stale  != filesystem corruption
Catalog record != mutation authority
```

A caller that wants to reuse an entry must still perform Step 5B validation. A
catalog record may narrow discovery to a canonical identity but never bypasses that
validation. A catalog failure ordinarily reduces performance, not correctness.

## 2. Catalog identity

### 2.1 Unique key

The unique catalog record key is:

```text
(CacheNamespace, entry_digest)
```

This matches physical cache identity: final paths are namespaced and then sharded by
the trusted entry digest. `entry_digest` is the 64-lowercase-hex digest derived from
the canonical `CacheKey`. The same digest in a different namespace is a different
catalog key.

### 2.2 Redundant integrity fields

Every live record also contains the strict Step 5A `CacheKeyReference`. Parsing must
reconstruct its `CacheKey`, derive `entry_digest`, and require exact equality with
the record key and filename. Namespace is both part of the key and canonical record
content. These redundancies detect misplaced, renamed, or internally inconsistent
records.

`logical_id` is not catalog identity and is not stored in H1. Callers may not supply
catalog paths or substitute human-readable identifiers for canonical identity.

### 2.3 Deterministic comparison

Identity ordering is ordinal by:

1. namespace `domain`;
2. namespace `producer_id`;
3. namespace `producer_schema_version` as an integer; and
4. `entry_digest` as lowercase ASCII.

No locale, filesystem case folding, Unicode normalization, creation time, or
discovery order participates.

## 3. Independent schema versions

H1 uses two positive-integer version domains:

```text
CACHE_CATALOG_LAYOUT_VERSION = 1
CACHE_CATALOG_RECORD_VERSION = 1
```

The layout version is encoded by the dedicated `catalog/v1` root and controls path
layout, synchronization, atomic publication, tombstones, and enumeration. The record
version is a required canonical field and controls live-record/tombstone JSON
schemas. It is not the Step 5A cache-entry contract version.

Strict canonical parsing rejects duplicate keys, unknown fields, missing fields,
noncanonical JSON, booleans where integers are required, invalid Unicode, and
trailing data. Version zero or a non-integer is malformed. A recognizable positive
future record version is `CATALOG_UNSUPPORTED`, not corrupt. A future layout lives
under its own version root and is not scanned as v1.

Unsupported or malformed catalog state may later be replaced during explicitly
approved rebuild/reconciliation, but H1 never mutates cache entries in response.

## 4. Canonical record model

Each exact record path contains one immutable canonical JSON document representing
either a live record or a tombstone. A live record has exactly:

```text
catalog_record_version: 1
record_state: "live"
record_revision: integer from 1 through 9,223,372,036,854,775,807
entry_digest: 64 lowercase hex
namespace: strict CacheNamespace
cache_key_reference: strict CacheKeyReference
last_validated_final: FinalCatalogSummary | null
last_recovery_observation: RecoveryCatalogSummary | null
```

At least one summary must be non-null. A tombstone has exactly:

```text
catalog_record_version: 1
record_state: "tombstone"
record_revision: integer from 1 through 9,223,372,036,854,775,807
entry_digest: 64 lowercase hex
namespace: strict CacheNamespace
cache_key_reference: strict CacheKeyReference
```

A tombstone is a catalog-record absence marker. It does not claim filesystem absence
and never deletes a cache entry.

### 4.1 FinalCatalogSummary

`last_validated_final` has exactly:

```text
provenance: "step5b_hit" | "step5d_promotion"
cache_entry_contract_version: positive supported integer
producer_id: validated canonical producer ID
producer_version: validated canonical producer version
producer_schema_version: positive integer
artifact_kind: validated canonical artifact kind
artifact_contract_version: positive integer
runtime_fingerprint_digest: "sha256:<64 lowercase hex>"
created_at_utc: canonical Step 5A UTC timestamp
payload_manifest_digest: "sha256:<64 lowercase hex>"
payload_file_count: nonnegative integer
payload_total_bytes: nonnegative integer
verification_level: "full_payload_sha256"
```

The runtime fingerprint digest is SHA-256 over its strict canonical Step 5A bytes.
It permits deterministic comparison without copying potentially sensitive or large
fingerprint values into catalog records. The summary describes the last trusted
validation or promotion evidence; it is never a claim of current presence.

### 4.2 RecoveryCatalogSummary

`last_recovery_observation` has exactly:

```text
provenance: "step5e_observation"
status: supported CacheRecoveryStatus value
reason: supported CacheRecoveryReason or CacheLookupReason value | null
staging_candidate_count: integer from 0 through 64
final_state: supported FinalRecoveryState value
lock_state: supported LockRecoveryState value
```

For lifecycle statuses, `reason` is null. Failure statuses retain the precise trusted
Step 5E reason. Staging paths, diagnostics, tokens, and individual candidate details
are not persisted in H1. This summary is descriptive, may become stale immediately,
and is not cleanup eligibility or mutation authority.

### 4.3 Field decision table

| Candidate field | H1 decision | Rationale |
|---|---|---|
| Layout and record versions | **REQUIRED** | Independent strict schema evolution |
| Record state and revision | **REQUIRED** | Atomic tombstone semantics and optimistic concurrency |
| Namespace and entry digest | **REQUIRED** | Unique physical catalog identity |
| CacheKeyReference | **REQUIRED** | Reconstructable redundant digest integrity |
| Final validated summary | **OPTIONAL** | Present only with Step 5B/5D trusted evidence |
| Recovery summary | **OPTIONAL** | Present only with Step 5E trusted evidence |
| Cache-entry contract version | **REQUIRED in final summary** | Identifies strict source model version |
| Producer ID/version/schema | **REQUIRED in final summary** | Supported by validated metadata |
| Artifact kind/contract version | **REQUIRED in final summary** | Useful generic metadata without logical ID |
| Runtime fingerprint digest | **REQUIRED in final summary** | Stable compact comparison evidence |
| Created-at UTC | **REQUIRED in final summary** | Canonical entry metadata; not policy |
| Manifest digest, file count, payload bytes | **REQUIRED in final summary** | Validated logical payload summary |
| Verification level | **REQUIRED in final summary** | Prevents ambiguous provenance strength |
| Full runtime-fingerprint values | **NOT ALLOWED** | Unnecessary duplication and privacy surface |
| `logical_id` | **NOT ALLOWED** | Informational, not physical identity |
| Absolute or arbitrary paths | **NOT ALLOWED** | Paths are deterministically derived |
| Last-use/access time or count | **NOT ALLOWED** | No authoritative source exists |
| Filesystem atime/mtime/ctime/birth time | **NOT ALLOWED** | Non-contractual freshness evidence |
| Allocated/reclaimable disk bytes | **NOT ALLOWED** | Not equivalent to validated logical bytes |
| Observation wall-clock timestamp | **NOT ALLOWED** | Age does not establish catalog freshness |
| Lock/writer/host/process identifiers | **NOT ALLOWED** | Unneeded sensitive implementation data |
| Raw diagnostics, JSON, exceptions, or content | **NOT ALLOWED** | Privacy, stability, and boundedness |

## 5. Provenance and freshness semantics

Only typed trusted evidence may produce summaries:

- a Step 5B `HIT` with its strict validated entry reference;
- a successful Step 5D promotion with its strict promoted entry reference; or
- a completed Step 5E `CacheRecoveryObservation`.

Generic callers cannot construct an arbitrary persisted record. Public update APIs
accept those trusted models and derive canonical record fields internally.

H1 defines no filesystem-time freshness predicate. `record_revision` is a positive
integer local to one identity. Under the catalog write lock, creation starts at 1
and every applied live update or tombstone increments the prior supported revision
by exactly one. Revision expresses catalog update order and supports optimistic
conflict detection; it does not prove current filesystem truth.

Revision exhaustion fails closed with `CATALOG_WRITE_UNSUPPORTED`; it does not wrap,
reset in place, or overwrite another revision. A later H2 rebuild may establish a
new catalog instance under separately locked activation semantics.

A record is called stale only when:

- a caller's expected revision differs from the current supported revision; or
- a new authoritative Step 5B/5E observation disagrees with the stored summary.

Wall-clock age, file timestamps, and record age alone never establish staleness.
H1 requires no global generation, revision counter, or snapshot identifier. The
per-record revision is the smallest deterministic primitive H2 needs to compare and
conditionally reconcile individual records without claiming a globally atomic
filesystem snapshot.

## 6. Catalog-owned storage layout

The only H1 layout is beneath an explicitly validated cache root:

```text
catalog/
  v1/
    write.lock
    records/
      <domain>/
        <producer_id>/
          <producer_schema_version>/
            <digest[0:2]>/
              <digest[2:4]>/
                <entry_digest>.json
```

The record path is derived solely from strict `CacheNamespace` and `entry_digest`.
It is never accepted from callers. Catalog content is never placed in `entries/v1`,
`staging/v1`, or `locks/v1`.

Every component is created/opened relative to a validated directory descriptor,
without following symlinks. The implementation re-proves root containment, expected
directory type, and stable parent identity. Unsafe, replaced, missing-required, or
reduced identity evidence fails closed for the catalog operation and never causes a
cache mutation.

Temporary publication files use exclusive unpredictable catalog-owned names in the
same leaf directory. Exact readers and enumerators ignore every name except canonical
`<entry_digest>.json` records reconstructed through the locked grammar. H1 never
scans cache-entry, staging, or lock namespaces.

## 7. Storage technology decision

H1 selects **sharded canonical JSON record files behind a narrow catalog backend**.
The canonical model and filesystem semantics in this contract are normative; the
backend does not expose arbitrary files, paths, JSON dictionaries, or queries.

### 7.1 Why canonical JSON per record

- It reuses strict deterministic serialization principles already established by
  Step 5A.
- Exact identity lookup reads one bounded record.
- Sharding avoids a monolithic whole-catalog rewrite.
- One corrupt record need not make unrelated records unreadable.
- Atomic same-directory replacement can publish either the complete old or complete
  new record.
- Catalog loss remains visibly disposable and technology-independent to cache
  entries.
- Python and macOS require no external dependency.

### 7.2 Alternatives not selected

SQLite provides transactions and indexing, but H1 does not select it. Its single
database and sidecar files enlarge the corruption and safe-open surface, its normal
path opening does not directly express the repository's directory-descriptor/no-
follow contract, and it is unnecessary for exact sharded lookup and bounded ordinal
iteration.

A monolithic JSON catalog is rejected because every upsert rewrites the whole file,
writer contention scales with total catalog size, and one damaged document affects
all records. Other binary or third-party database formats add dependencies without
required H1 benefit.

## 8. Backend and read semantics

The backend exposes only validated-root, typed-identity catalog operations. It has no
cache-entry, staging, lock, promotion, cleanup, SQL, arbitrary query, or arbitrary
path method.

Readers are lock-free and use a bounded stable-read protocol:

1. derive and validate the exact record path;
2. inspect parent components no-follow;
3. inspect the record as a regular file no-follow;
4. open it relative to the verified leaf directory;
5. read at most 65,537 bytes to enforce the 65,536-byte record ceiling;
6. compare pre-open, open-handle, after-read, and post-close identity evidence;
7. strictly parse canonical JSON and validate record/path identity; and
8. return only an immutable structured result.

Atomic replacement means a stable reader observes either one complete prior record
or one complete replacement. Replacement, disappearance, changed identity, reduced
evidence, or an unstable directory maps to `CATALOG_UNSTABLE`; there is no automatic
retry.

## 9. Lookup result contract

Exact lookup returns an immutable `CacheCatalogLookupResult` with one status:

- `RECORD_FOUND` — one supported live record passed canonical and identity checks;
- `RECORD_ABSENT` — the initialized v1 catalog has no exact record or has a valid
  exact tombstone;
- `CATALOG_UNAVAILABLE` — the v1 catalog has not been initialized;
- `CATALOG_CORRUPT` — the exact record is malformed, oversized, misplaced, duplicate,
  or internally inconsistent;
- `CATALOG_UNSUPPORTED` — the exact record has a recognizable future version;
- `CATALOG_UNSAFE` — an unsafe path, symlink, or unsupported object is observed;
- `CATALOG_UNSTABLE` — stable identity cannot be established; or
- `CATALOG_IO_FAILURE` — deterministic permission or I/O failure not more precisely
  classified above.

Only `RECORD_FOUND` carries a live `CacheCatalogRecord`. A valid tombstone may carry
its revision as trusted catalog concurrency evidence but is still
`RECORD_ABSENT`. Results include at most bounded stable diagnostics and no raw
exception or native details.

`RECORD_ABSENT` is never named or mapped to cache `MISS`. For all statuses, callers
may perform an authoritative exact Step 5B or Step 5E operation when they possess the
required canonical identity. Catalog corruption, unsupported state, or unavailability
does not block direct Step 5B validation of a valid cache entry.

## 10. Catalog-owned synchronization and concurrency

H1 permits concurrent readers and serializes catalog writers through the persistent
catalog-owned `catalog/v1/write.lock` file using a process-scoped advisory exclusive
lock held on an open regular-file descriptor. The file is not a Step 5B/5D writer
lock and contains no owner token, PID, host ID, heartbeat, or cache identity.

The synchronization protocol is:

1. safely open/create `write.lock` relative to the validated `catalog/v1` directory
   with no-follow and restrictive permissions;
2. acquire the platform advisory exclusive lock for one bounded write operation;
3. re-prove catalog root and target parent identity;
4. read and validate the exact current record, if present;
5. check the caller's expected revision;
6. atomically publish one complete new live record or tombstone;
7. durably flush the publication boundary; and
8. release the advisory lock by closing/unlocking the descriptor.

Process termination releases the advisory lock; H1 has no stale-lock breaking
policy. Multiple H1 writers cooperate through this lock. Noncooperating external
filesystem mutation is detected conservatively by identity and stable-read checks;
it never authorizes a cache mutation.

An update request includes `expected_revision: int | None`. `None` means create only
when no record path exists. A matching positive revision permits replacement.
Mismatch returns `CATALOG_WRITE_CONFLICT` without writing. A valid tombstone is a
record revision, so resurrection must name that expected revision. This optimistic
check prevents lost updates among cooperating callers and gives H2 a conditional
reconciliation primitive.

H1 does not reuse cache-entry writer locks. Promotion and catalog update are separate
operations with separate failure domains.

## 11. Atomic create, upsert, and tombstone protocol

### 11.1 Initialization

Initialization safely derives and creates only `catalog/v1`, `records`, and required
catalog-owned descendants beneath a validated cache root. Directory creation is
component-wise, no-follow, and followed by parent-directory durability checks. An
existing unsafe or wrong-type object fails closed. Initialization never scans or
changes cache entries.

### 11.2 Publication

Under the catalog write lock, create and upsert:

1. derive the target and same-directory temporary path;
2. exclusively create the temporary regular file no-follow with restrictive mode;
3. write the complete canonical document with bounded write accounting;
4. fsync the temporary file;
5. revalidate the target parent and expected current revision/absence;
6. atomically replace the exact catalog record name using source and destination
   directory descriptors anchored to the same verified leaf directory; and
7. fsync the leaf directory before returning `CATALOG_WRITE_APPLIED`.

Plain check-then-write, in-place truncation, partial append, cross-filesystem move,
copy/delete fallback, and unanchored arbitrary pathname replacement are forbidden.
If atomic same-filesystem descriptor-anchored replacement or meaningful file and
directory fsync is unavailable, H1 mutation is unsupported and fails before
publication. A reader never interprets the temporary name as a record.

The operation returns an immutable write result such as:

- `CATALOG_WRITE_APPLIED` with the new revision;
- `CATALOG_WRITE_CONFLICT`;
- `CATALOG_WRITE_UNAVAILABLE`;
- `CATALOG_WRITE_CORRUPT`;
- `CATALOG_WRITE_UNSUPPORTED`;
- `CATALOG_WRITE_UNSAFE`;
- `CATALOG_WRITE_UNSTABLE`; or
- `CATALOG_WRITE_IO_FAILURE`.

No failure changes the Step 5B/5D/5E result that supplied evidence.

### 11.3 Logical removal

H1 record removal is atomic replacement with a strict tombstone at the same derived
record path, incrementing the expected revision. H1 performs no record-file unlink.
This avoids treating a pathname unlink as identity-safe deletion and preserves
optimistic conflict evidence. Exact lookup and enumeration treat a supported
tombstone as record absence.

Tombstoning requires typed authoritative evidence from a Step 5E observation whose
combined status is `EMPTY` for the same identity, plus the expected catalog revision.
Other removal reasons belong to H2 reconciliation design. Tombstoning does not claim
durable cache absence, does not remove an entry, and may be superseded by a later
trusted observation.

Physical tombstone or abandoned-temp compaction is not approved by H1. H2 or a later
catalog-maintenance contract must define identity-safe catalog-only compaction.

## 12. Trusted record creation and update paths

H1 exposes three typed builders/update operations:

1. **Step 5B HIT upsert** — accepts the exact request identity and successful
   `ReadOnlyCacheLookupResult`/validated entry reference; derives a final summary.
2. **Step 5D promotion upsert** — accepts the exact request identity and successful
   `PromotedCacheEntryReference`; derives the equivalent full-verification summary.
3. **Step 5E observation upsert** — accepts the exact request identity and completed
   immutable `CacheRecoveryObservation`; derives the recovery summary.

Each validates cross-model identity before acquiring mutation authority. An update
preserves the other existing supported summary unless trusted new evidence explicitly
supersedes it. Step 5E evidence that says final is absent does not erase historical
`last_validated_final`; the concurrently stored recovery summary makes the later
absence explicit. This avoids pretending history is current truth.

No generic `CacheCatalogRecord` persistence API accepts arbitrary caller-created
fields. Pure record constructors may be public for parsing/results, but backend
mutation accepts only typed requests produced from approved evidence.

## 13. Step 5B integration

The intended optional fast path is:

```text
canonical cache identity
          |
          v
catalog exact lookup
          |
   RECORD_FOUND
          |
          v
Step 5B validates exact derived final entry
          |
          v
HIT or authoritative failure
```

If the catalog record is absent, unavailable, corrupt, unsupported, unsafe, unstable,
or unreadable, a caller with canonical identity may perform Step 5B anyway. Catalog
lookup never supplies a Step 5B validated entry reference and never changes Step 5B
status/reason precedence.

A successful Step 5B `HIT` may best-effort upsert its trusted final summary after the
lookup returns. Catalog update failure must not change, mask, or invalidate the hit.
Step 5B itself remains correct and usable without H1.

## 14. Step 5D integration

After Step 5D returns successful promotion, the caller may synchronously attempt a
best-effort H1 upsert using the strict `PromotedCacheEntryReference`. This is chosen
over reserving all population for H2 because it cheaply improves catalog coverage on
the normal write path without scanning.

Catalog work occurs only after Step 5D has finalized and returned its authoritative
promotion outcome. Promotion success never depends on catalog initialization,
availability, or update success. Catalog failure cannot roll back the final entry,
remove or refresh a lock, retry promotion, alter the promotion result, or corrupt the
entry. H2 may later reconcile a missing record.

No H1 dependency is added inside the Step 5D mutation filesystem interface.

## 15. Step 5E interaction

H1 may best-effort persist the minimal recovery summary defined in section 4.2 from
a completed Step 5E observation. It does not persist paths, diagnostics, raw lock
fields, or candidate contents. The summary remains explicitly historical and
descriptive.

H1 never maps recovery status to retention, deletion, repair, lock breaking,
promotion retry, or cleanup planning. In particular:

```text
cataloged Step 5E summary != current recovery truth
cataloged Step 5E summary != cleanup eligibility
```

A consumer needing current recovery state must invoke Step 5E again.

## 16. Enumeration, resource limits, and ordering

H1 v1 locks these immutable limits:

```text
max_catalog_record_bytes = 65_536
max_catalog_page_records = 256
max_catalog_directory_entries = 4_096
max_catalog_relative_path_utf8_bytes = 1_024
max_catalog_traversal_depth = 64
max_catalog_operation_diagnostics = 32
```

Reads probe at most `max_catalog_record_bytes + 1` bytes. Writes reject canonical
bytes above the limit before creating a temporary file. Limits do not derive from
free disk space.

H1 sets no separately configured global record-count ceiling. Each digest leaf may
contain at most 4,096 directory entries of any kind; a create that would
exceed that structural capacity fails before publication. With 65,536 two-level
digest leaves per namespace, this keeps every directory operation bounded without a
small whole-catalog ceiling. Exact operations remain constant-scope and enumeration
is bounded and paginated. Namespace enumeration accepts a strict
`CacheNamespace`, an optional validated exclusive cursor containing the last emitted
`entry_digest`, and a page limit from 1 through 256. It walks only that namespace's
two fixed digest-shard levels, validates canonical names, returns live records in
entry-digest ordinal order, omits tombstones, and returns a next cursor when more
records exist.

H1 also exposes bounded full-catalog iteration for future H2. It pages in the
canonical identity order from section 2.3 and uses a validated exclusive cursor
containing the last emitted `(namespace, entry_digest)`. It never materializes the
whole catalog in memory. Concurrent writes may cause a page sequence to reflect
different catalog moments; per-record revisions permit H2 to detect conflicts. H1
does not call such iteration a snapshot.

Every directory listing reads at most 4,097 names to prove the 4,096-entry ceiling.
Unrelated files, future layout roots, malformed directory names, excessive depth,
over-capacity directories, or unsafe objects are not silently treated as records.
Enumeration returns structured failure evidence and never traverses cache storage
namespaces.

## 17. Diagnostics and privacy

H1 diagnostics are distinct from Step 5B lookup, Step 5E recovery, future H2
reconciliation, policy, and executor diagnostics. They describe only catalog
operations.

Diagnostics contain stable codes, a catalog subject (`CATALOG_ROOT`, `RECORD`,
`NAMESPACE`, or `WRITE_LOCK`), and at most a sanitized canonical catalog identity or
derived root-relative catalog path. They are deterministically ordered by primary
status precedence, subject, canonical identity, then code; deduplicated by stable
fields; and capped at 32 with a final truncation marker.

No catalog record, result, cursor, or diagnostic exposes absolute paths, usernames,
host IDs, owner or writer tokens, PIDs, raw exceptions, native error text, symlink
targets, device/inode IDs, source secrets or URLs, file content, raw database/native
details, or arbitrary metadata.

## 18. Failure isolation and rebuildability

H1 behavior is fail-closed for catalog state and fail-open for authoritative cache
use through Step 5B/5E:

- **Missing catalog:** `CATALOG_UNAVAILABLE`; direct cache validation remains usable.
- **Missing/tombstoned exact record in an initialized catalog:** `RECORD_ABSENT`;
  never cache `MISS`.
- **Malformed/oversized/duplicate/identity-conflicted record:**
  `CATALOG_CORRUPT`; unrelated records remain independently readable.
- **Future record version:** `CATALOG_UNSUPPORTED`; no downgrade interpretation.
- **Partial publication:** only a temporary ignored name or complete old/new record
  is visible; partial bytes are never accepted as current.
- **Permission/I/O failure:** structured catalog failure; no cache mutation.
- **Concurrent expected-revision mismatch:** `CATALOG_WRITE_CONFLICT`; no write.
- **Stale record:** may narrow discovery but must be authoritatively revalidated.
- **Duplicate physical identity evidence:** corruption; no arbitrary winner.

The catalog can be discarded and rebuilt without cache loss. It contains no value
required to derive, parse, validate, promote, or interpret a cache entry. Step 5A
entries remain self-describing. Catalog corruption never requires cache repair.

H1 does not itself delete or rebuild the catalog. Administrative removal and H2
rebuild must use separately approved safe catalog-root handling.

## 19. Size and time semantics

`payload_total_bytes` is the validated logical sum of manifest-declared regular-file
sizes. `payload_file_count` is the validated manifest cardinality. H1 does not call
either total entry bytes, allocated bytes, disk usage, or reclaimable bytes. Document
bytes, directory overhead, sparse allocation, compression, clones, and filesystem
block allocation are not estimated or combined with logical payload bytes.

`created_at_utc` is copied from strict canonical `CacheEntryMetadata`. It is entry
creation time, not last use, recency of access, retention eligibility, or catalog
freshness. H1 performs no policy interpretation.

H1 contains no access timestamp, access counter, LRU field, lookup mutation, or
filesystem atime. Future schema evolution may add separately approved access evidence
without changing H1 v1 semantics, but no such mechanism is designed or approved here.

## 20. H2 rebuild/reconciliation boundary

H1 supplies H2 only these primitives:

- initialize validated catalog-owned storage;
- exact typed lookup;
- typed trusted live-summary upsert with expected revision;
- typed `EMPTY`-evidence tombstone with expected revision;
- bounded namespace enumeration; and
- bounded full-catalog iteration with per-record revisions.

H2 — Catalog Rebuild / Reconciliation must separately define:

- bounded discovery of authoritative cache storage;
- how Step 5B/5E observations are scheduled and composed during rebuild;
- comparison between catalog and filesystem evidence;
- creation of missing records and conditional correction of stale records;
- treatment of corrupt/unsupported record files and tombstone/temp compaction;
- removal of stale catalog-only records beyond H1's narrow `EMPTY` evidence rule;
- interruption, resumption, progress, work budgets, and reconciliation diagnostics;
- concurrency with normal H1 updates; and
- whether a new catalog can be built separately and safely activated.

H1 implements no cache-wide traversal, rebuild loop, reconciliation policy, catalog
root deletion, or record compaction. H2 implementation is not approved by this
contract.

## 21. Source Ingestion support

H1 is generic across Documentary Engine artifact producers and namespaces. Future
Source Ingestion may ask whether a catalog record is known for a deterministic cache
identity, then must perform Step 5B validation before reuse.

H1 adds no YouTube, URL, source type, ingestion provider, credential, or source-
specific field. It does not decide the Source Ingestion maturity gate or begin
ingestion implementation.

## 22. Conceptual public API

H1 implementation may expose immutable equivalents of:

```text
CacheCatalogIdentity
CacheCatalogRecord
CacheCatalogTombstone
CacheCatalogFinalSummary
CacheCatalogRecoverySummary
CacheCatalogLookupResult
CacheCatalogLookupStatus
CacheCatalogWriteRequest
CacheCatalogWriteResult
CacheCatalogWriteStatus
CacheCatalogPage
CacheCatalogCursor
CacheCatalogPolicy
CacheCatalogDiagnostic
CacheCatalogSubject
CacheCatalogReadOnlyBackend
CacheCatalogBackend
```

Approved conceptual operations are:

```python
lookup_catalog_record(identity, *, backend=...) -> CacheCatalogLookupResult

upsert_catalog_from_lookup(identity, lookup_result, *, expected_revision, backend=...)
upsert_catalog_from_promotion(identity, promotion_result, *, expected_revision, backend=...)
upsert_catalog_from_recovery(identity, recovery_observation, *, expected_revision, backend=...)

tombstone_catalog_empty(identity, recovery_observation, *, expected_revision, backend=...)

enumerate_catalog_namespace(namespace, *, cursor=None, limit=256, backend=...)
iterate_catalog_records(*, cursor=None, limit=256, backend=...)
```

Exact Python naming may be refined mechanically during H1 implementation review,
but semantics, authority, typed inputs, limits, and mutation boundaries are locked.
No arbitrary SQL, query language, caller path, dictionary persistence, raw JSON
write, generic delete, or cache mutation API is approved.

## 23. Required implementation tests

### Models and serialization

- canonical supported live record and tombstone round trips;
- deterministic bytes and field ordering;
- missing, duplicate, unknown, wrong-type, and malformed fields;
- zero/negative/non-integer and recognizable future versions;
- namespace/key-reference/digest/path conflicts;
- live record requires at least one summary;
- strict provenance, recovery reason/status, and verification-level combinations;
- record revision bounds and exact increment behavior; and
- record-size boundary at 65,536 bytes and one over.

### Lookup and authority

- found, exact absent, tombstone, uninitialized catalog, corrupt, unsupported,
  unsafe, unstable, permission, and I/O results;
- catalog hit still invokes Step 5B before reuse;
- catalog absence does not force cache `MISS`;
- corrupt/unavailable catalog does not block direct Step 5B;
- catalog record never supplies a validated cache entry reference; and
- no logical ID or caller path affects identity.

### Atomic updates and concurrency

- safe catalog initialization and unsafe ancestor rejection;
- exclusive same-directory temp creation, bounded complete write, file fsync, atomic
  descriptor-anchored replacement, and directory fsync;
- create at revision 1 and exact increment on update/tombstone;
- expected-revision success and conflict;
- multiple readers during replacement see only complete old/new records;
- multiple writers serialize without lost updates;
- process-scoped advisory lock release and I/O failure;
- interruption before write, during temporary write, before replacement, after
  replacement, and before/after directory fsync;
- abandoned temporary names are never interpreted as records;
- unsupported atomic/fsync capability fails before publication; and
- zero automatic retry.

### Removal and failure isolation

- tombstone requires same-identity Step 5E `EMPTY` evidence;
- tombstone removes only catalog visibility and never unlinks a cache object or
  record file;
- cache final, staging, and lock paths remain untouched for every catalog operation;
- catalog update failure cannot alter Step 5B hit or Step 5D promotion outcomes;
- one corrupt record does not block unrelated exact lookup;
- catalog loss leaves cache entries independently valid; and
- recreated catalog records deterministically represent the same trusted evidence.

### Enumeration, limits, ordering, and privacy

- exact namespace/digest shard derivation and containment;
- namespace pages and full pages of 1 through 256 records;
- exclusive cursor behavior, next cursor, empty page, and invalid cursor;
- ordinal namespace/entry-digest ordering independent of discovery order;
- concurrent page mutation is not mislabeled a snapshot;
- 1,024-byte path, depth-64, 32-diagnostic, and bounded-read/write boundaries plus
  one-over failures;
- 4,096 directory entries and one-over structural-capacity failure;
- no total in-memory catalog materialization;
- no traversal into `entries`, `staging`, `locks`, other roots, or future layouts;
- stable diagnostic ordering, deduplication, and truncation; and
- no absolute paths, usernames, tokens, IDs, exceptions, URLs, raw content, runtime
  fingerprint values, or native details.

### Integration and prohibited behavior

- typed upsert from Step 5B HIT, Step 5D success, and Step 5E observation;
- all other lookup/promotion/recovery outcomes rejected as trusted final provenance;
- promotion succeeds even when catalog update fails;
- recovery summary remains descriptive and stale-able;
- no inventory report becomes catalog mutation authority;
- no H2 traversal/rebuild, retention, cleanup, quota, pruning, lock mutation, repair,
  Source Ingestion-specific behavior, or background worker; and
- catalog backends expose no arbitrary path, SQL, delete-cache, or general filesystem
  mutation surface.

All existing storage and full regression tests must remain green.

## 24. H1 implementation decomposition

The approved H1 implementation should proceed in these reviewable slices:

1. **H1A — Catalog identity, models, canonical serialization, and limits.** Pure
   immutable types, strict parsing, record/tombstone invariants, derivation, and
   deterministic tests; no filesystem access.
2. **H1B — Read-only catalog backend, exact lookup, and failure results.** Safe
   initialization observation, bounded stable record reads, no-follow containment,
   and catalog-only diagnostics; no writes.
3. **H1C — Serialized atomic catalog publication and tombstones.** Catalog-owned
   initialization, advisory write lock, expected revisions, atomic upsert, logical
   removal, and crash/failure matrix.
4. **H1D — Bounded namespace and full-catalog pagination.** Deterministic shard
   traversal, validated cursors, pagination, bounds, and concurrent-change semantics.
5. **H1E — Typed Step 5B/5D/5E integration helpers and regression hardening.**
   Best-effort evidence adapters, package exports, privacy/read-only/mutation-boundary
   proof, and full regression coverage.

Completing one slice does not authorize the next without the normal review and
roadmap-maintenance process. None begins H2.

## 25. Explicit non-goals

H1 does not implement or authorize:

- authoritative cache lookup, cache-entry validation, or cache `MISS` inference;
- H2 discovery, rebuild, reconciliation, or catalog compaction;
- cache-entry, staging, or lock mutation;
- promotion, repair, quarantine, migration, or recovery mutation;
- retention or cleanup eligibility;
- cache deletion or catalog-derived mutation authority;
- quota, budget, eviction, pruning, scheduling, or automatic housekeeping;
- access tracking, LRU, filesystem-time freshness, or policy thresholds;
- Source Ingestion behavior or source-specific metadata; or
- arbitrary SQL, queries, paths, or generic record writes.

## 26. Approval and next action

This contract resolves catalog authority, identity, versions, record schema,
provenance, freshness, layout, technology, exact lookup, atomic update and logical
removal, concurrency, failure isolation, resource limits, deterministic ordering,
integration, rebuildability, and the H2 boundary.

Status:

**DESIGN LOCKED — APPROVED FOR H1 IMPLEMENTATION**

Exact next action:

**Implement H1 according to the locked contract, beginning with H1A — catalog
identity, models, canonical serialization, and limits.**

H2 implementation remains unapproved.
