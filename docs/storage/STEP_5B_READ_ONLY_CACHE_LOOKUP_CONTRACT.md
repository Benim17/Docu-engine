# Step 5B — Read-Only Persistent Cache Lookup Contract

Status: **DESIGN LOCKED — approved for phased Step 5B implementation**
Branch: `feature/cache-storage-foundation`
Baseline: `0c6974a7cf64c761b55f9d944ce4b1de2b9bf755`
Scope: **Read-only validation of one expected final cache entry; no production implementation in this document**

Authority: `STEP_5_VERSIONED_PERSISTENT_CACHE_ENTRY_CONTRACT.md` is the
higher-level locked normative contract. This document refines it without
overriding its observable behavior. Step 5B1 is implemented at `fb5c72a`.
The read-only lock dependency is locked by
`STEP_5B_LOCK_LIFECYCLE_OBSERVATION_CONTRACT.md`. Step 5B2 is the next approved
implementation unit. Step 5B2 and Step 5B3 remain internal and must not expose
contract-complete `MISS` results.

## 1. Purpose and scope

Step 5B defines a fail-closed, read-only lookup subsystem for persistent cache-entry contract v1.

A lookup:

1. accepts an explicitly supplied, validated cache root;
2. accepts a validated Step 5A `CacheNamespace`;
3. accepts a validated `CacheKey`;
4. accepts explicit producer, runtime, and optional artifact expectations;
5. derives exactly one expected final-entry path with Step 5A `derive_final_entry_path()`;
6. inspects only that expected entry and the descendants required by contract v1;
7. fully validates structure, canonical documents, identity, sizes, and SHA-256 payload digests;
8. returns a structured `CacheLookupResult` for expected cache states; and
9. never mutates filesystem or process state.

Step 5B never scans sibling digests, shards, namespaces, staging areas, unrelated
locks, or unrelated roots. When the expected final entry is absent, it may inspect
only the matching lock derived from the validated namespace and cache key.
`logical_id` is informational metadata and never participates in path selection.

## 2. Explicit non-goals

Step 5B does not implement or authorize:

- cache writes or directory creation;
- staging or promotion;
- lock acquisition, lock refresh, lock-file creation, lock release, lock breaking,
  lock deletion, or lock recovery;
- repair, deletion, cleanup, eviction, or quota enforcement;
- quarantine or migration;
- a persistent cache index;
- inventory mutation or use of inventory as lookup truth;
- automatic cache-root, namespace, key, or producer discovery;
- producer execution or rendering integration;
- fallback search across other namespaces, keys, roots, or legacy layouts;
- content compatibility inference from machine state;
- access-time updates or diagnostic files inside the cache.

## 3. Public API contract

The intended public entry point is:

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

The filesystem and lock-clock dependencies are injectable for deterministic tests.
`SYSTEM_LOCK_OBSERVATION_CLOCK` supplies production UTC wall time but does not choose
freshness policy. The filesystem interface exposes bounded, no-follow reads and
metadata inspection only. It exposes no write, create, rename, delete, chmod, or lock
mutation operation.

### 3.1 `ValidatedCacheRoot`

`ValidatedCacheRoot` is an immutable request value established before lookup. It contains:

- `lexical_path: Path` — caller-supplied absolute path with no `.` or `..` components;
- `resolved_path: Path` — root resolved once without accepting a symlink as the root;
- `identity: FileIdentity` — initial directory identity where supported.

Construction requires an absolute existing directory, rejects a symlink root, and confirms that lexical and resolved identity describe the same directory. This construction is read-only. Lookup revalidates the root identity before returning.

Step 5A accepts lexical `str | Path` values because it is pure. Step 5B must not treat an arbitrary Step 5A path argument as an approved root without this validation.

### 3.2 `CacheLookupRequest`

The immutable request has exactly:

```text
cache_root: ValidatedCacheRoot
namespace: CacheNamespace
cache_key: CacheKey
expectation: CacheLookupExpectation
artifact_expectation: CacheArtifactExpectation | None
payload_expectation: ProducerPayloadExpectation
policy: CacheLookupVerificationPolicy
lock_observation_policy: LockObservationPolicy
```

Rules:

- `namespace` must equal `expectation.namespace`.
- `expectation.producer_id` and `producer_schema_version` already match the namespace under Step 5A rules.
- `expectation.runtime_fingerprint` is compared by exact Step 5A model equality.
- `artifact_expectation`, when supplied, compares `artifact_kind` and `artifact_contract_version` by default.
- `CacheArtifactExpectation.expected_logical_id` defaults to `None`. Only a separately supplied non-`None` value requests exact logical-ID comparison.
- `logical_id` is otherwise informational and never causes a compatibility mismatch, affects path derivation, or contributes to cache identity.
- Absence of `artifact_expectation` means artifact metadata is still validated structurally but is not constrained by the caller.
- `payload_expectation` is a trusted producer-derived contract described below; ordinary UI or configuration input cannot construct or weaken it.
- `lock_observation_policy` is mandatory immutable request policy. Its
  `active_freshness_seconds` is explicit; neither the public entry point nor the
  production clock supplies an implicit freshness default.
- Producer ID and schema are mandatory expectations. Producer version is observed and validated as metadata but is not an additional lookup constraint because Step 5A `CacheLookupExpectation` does not contain it.

### 3.3 `CacheArtifactExpectation`

The immutable artifact expectation contains exactly:

```text
artifact_kind: str
artifact_contract_version: int
expected_logical_id: str | None = None
```

Kind and contract version are compatibility-bearing. `expected_logical_id` is an opt-in informational assertion for workflows that require it; it is not a default compatibility constraint and never participates in physical identity.

### 3.4 `ProducerPayloadExpectation`

Payload cardinality is producer semantics, not a resource-limit setting. The immutable trusted expectation contains:

```text
cardinality: PayloadCardinalityExpectation
```

`PayloadCardinalityExpectation` has exactly:

- `NON_EMPTY_REQUIRED` — default; a zero-record manifest is invalid;
- `EMPTY_ALLOWED` — zero records are permitted when metadata count and byte totals are also zero.

`EMPTY_ALLOWED` may be issued only by a registered producer contract or trusted producer adapter. Ordinary UI, user configuration, serialized cache metadata, and generic lookup callers cannot authorize it. The request builder obtains this value from the producer registry and verifies that it belongs to the expected producer ID and schema. The default producer expectation is `NON_EMPTY_REQUIRED`.

### 3.5 `CacheLookupVerificationPolicy`

The immutable policy contains only resource limits. It cannot change producer payload semantics or disable integrity checks required for `HIT`.

### 3.6 Exceptions

Exceptions are reserved for programmer errors:

- wrong request or dependency types;
- an internally inconsistent `CacheLookupRequest`;
- invalid policy limits;
- a filesystem adapter that violates its declared interface; or
- an impossible internal invariant indicating a bug.

Expected cache absence, malformed data, unsupported versions, permission failures, I/O failures, unsafe objects, and concurrent change are returned as statuses and diagnostics. They do not escape as uncontrolled exceptions.

`KeyboardInterrupt`, `SystemExit`, and process-fatal exceptions are not converted to cache statuses.

## 4. Lookup result contract

Step 5A's minimal result is expanded for Step 5B. The immutable `CacheLookupResult` has exactly:

```text
status: CacheLookupStatus
reason: CacheLookupReason | None
expected_entry_path: Path
validated_entry: ValidatedCacheEntryReference | None
entry_digest: str
namespace: CacheNamespace
cache_key_reference: CacheKeyReference
diagnostics: tuple[CacheLookupDiagnostic, ...]
verification_level: CacheVerificationLevel
observed_contract_version: int | None
payload_bytes_fully_hashed: bool
metadata: CacheEntryMetadata | None
manifest: PayloadManifest | None
marker: CompletenessMarker | None
```

### 4.1 Trusted versus observed values

Trusted expected values are derived only from validated request models:

- `expected_entry_path`;
- `entry_digest`;
- `namespace`; and
- `cache_key_reference`.

Observed models are included only after their complete stored document has passed bounded reading, canonical JSON parsing, strict unknown-field rejection, and Step 5A model construction. A parsed model is not proof that the entry is valid; its presence in a non-`HIT` result remains observational.

No raw unparsed JSON, arbitrary exception text, untrusted absolute path, device name, inode number, or symlink target appears in the public result.

`status` always uses the existing locked Step 5A `CacheLookupStatus` enum without adding, removing, or changing members. `reason` supplies the precise Step 5B classification. It is `None` for a straightforward `HIT` or `MISS`; every rejected or unsupported result has one primary reason. Diagnostics may contain additional lower-precedence findings.

### 4.2 `ValidatedCacheEntryReference`

This reference exists only for `HIT` and contains:

```text
entry_path: Path
entry_digest: str
namespace: CacheNamespace
cache_key_reference: CacheKeyReference
metadata: CacheEntryMetadata
manifest: PayloadManifest
marker: CompletenessMarker
verification_level: FULL_PAYLOAD_SHA256
```

It is an immutable point-in-time validation result, not a durable file handle and not a guarantee against changes after lookup returns. Consumers that later open payloads must either use a future validated read-handle API or revalidate.

### 4.3 Verification progress

`CacheVerificationLevel` records the highest completed level:

1. `NONE`
2. `STRUCTURE`
3. `CANONICAL_DOCUMENTS`
4. `DOCUMENT_INTEGRITY`
5. `FULL_PAYLOAD_SHA256`

Only `FULL_PAYLOAD_SHA256` can accompany `HIT`. `payload_bytes_fully_hashed` is true exactly when every declared payload byte was successfully read and hashed and all stable-snapshot checks passed.

## 5. Broad status and detailed reason taxonomy

Step 5B preserves the locked Step 5A status enum exactly. Each `CacheLookupReason` maps to exactly one existing `CacheLookupStatus` family. When multiple failures are safely observable, the primary status is the earliest locked family and the primary reason is the earliest reason within that family. Diagnostics may report additional lower-precedence findings already observed; lookup does not perform unsafe extra work merely to collect diagnostics.

### 5.1 Exact reason-to-status mapping

| Precedence | `CacheLookupReason` | `CacheLookupStatus` | Meaning |
|---:|---|---|---|
| 1 | `UNSAFE_PATH` | `UNSAFE_PATH` | Root or expected path containment cannot be proven. |
| 2 | `UNSAFE_OBJECT` | `UNSAFE_PATH` | Symlink, FIFO, socket, device, or unsupported special object is present. |
| 3 | `UNSUPPORTED_ENTRY_VERSION` | `UNSUPPORTED_VERSION` | Recognizable entry version is not supported. |
| 4 | `UNSUPPORTED_MANIFEST_VERSION` | `UNSUPPORTED_VERSION` | Recognizable manifest version is not supported. |
| 5 | `UNSUPPORTED_CACHE_KEY_VERSION` | `UNSUPPORTED_VERSION` | Cache-key canonical version is recognizable but unsupported. |
| 6 | `UNSUPPORTED_RUNTIME_FINGERPRINT_VERSION` | `UNSUPPORTED_VERSION` | Runtime fingerprint schema is recognizable but unsupported. |
| 7 | `UNSUPPORTED_LOCK_VERSION` | `UNSUPPORTED_VERSION` | The matching lock has a recognizable unsupported version. |
| 8 | `INCOMPLETE_ENTRY` | `INVALID_ENTRY` | One or more required top-level objects are absent. |
| 9 | `MALFORMED_COMPLETE` | `INVALID_ENTRY` | `COMPLETE` is invalid, noncanonical, oversized, or has unknown fields. |
| 10 | `MALFORMED_METADATA` | `INVALID_ENTRY` | Metadata is invalid, noncanonical, oversized, or has unknown fields. |
| 11 | `MALFORMED_MANIFEST` | `INVALID_ENTRY` | Manifest is invalid, noncanonical, oversized, or has unknown fields. |
| 12 | `MALFORMED_LOCK` | `INVALID_ENTRY` | The matching lock is invalid, noncanonical, oversized, or violates its supported schema. |
| 13 | `ENTRY_IDENTITY_CONFLICT` | `INVALID_ENTRY` | Path, expected digest, metadata digest identity, or marker entry digest disagree. |
| 14 | `CACHE_KEY_CONFLICT` | `INVALID_ENTRY` | Expected key/reference and metadata cache-key reference disagree. |
| 15 | `LOCK_IDENTITY_CONFLICT` | `INVALID_ENTRY` | The matching lock entry digest disagrees with the expected entry identity. |
| 16 | `LOCK_TIMESTAMP_INVALID` | `INVALID_ENTRY` | Matching-lock timestamps are inconsistent or in the future. |
| 17 | `UNEXPECTED_TOP_LEVEL_OBJECT` | `INVALID_ENTRY` | A regular unknown file or directory exists at entry top level. |
| 18 | `UNEXPECTED_PAYLOAD_OBJECT` | `INVALID_ENTRY` | Payload contains an undeclared regular file or unneeded directory. |
| 19 | `UNSTABLE_SNAPSHOT` | `INVALID_ENTRY` | Entry or matching-lock observation changed or stability could not be established. |
| 20 | `IO_FAILURE` | `INVALID_ENTRY` | Permission or I/O failure prevented deterministic validation. |
| 21 | `MANIFEST_DIGEST_MISMATCH` | `INTEGRITY_FAILURE` | Metadata or marker manifest digest does not match stored canonical manifest bytes. |
| 22 | `METADATA_DIGEST_MISMATCH` | `INTEGRITY_FAILURE` | Marker metadata digest does not match stored canonical metadata bytes. |
| 23 | `PAYLOAD_MISSING` | `INTEGRITY_FAILURE` | A manifest-declared payload path is absent. |
| 24 | `PAYLOAD_SIZE_MISMATCH` | `INTEGRITY_FAILURE` | Observed size differs from the manifest. |
| 25 | `PAYLOAD_DIGEST_MISMATCH` | `INTEGRITY_FAILURE` | SHA-256 differs from the manifest. |
| 26 | `PAYLOAD_HARDLINK_DETECTED` | `INTEGRITY_FAILURE` | A payload regular file has a detectable link count greater than one. |
| 27 | `NAMESPACE_PRODUCER_CONFLICT` | `PRODUCER_MISMATCH` | Metadata namespace and producer identity disagree. |
| 28 | `PRODUCER_MISMATCH` | `PRODUCER_MISMATCH` | Observed producer ID differs from the expected producer. |
| 29 | `SCHEMA_MISMATCH` | `SCHEMA_MISMATCH` | Observed producer schema differs from the expected schema. |
| 30 | `ARTIFACT_MISMATCH` | `SCHEMA_MISMATCH` | Expected artifact kind/version, or explicitly expected logical ID, differs. |
| 31 | `RUNTIME_FINGERPRINT_MISMATCH` | `RUNTIME_FINGERPRINT_MISMATCH` | Exact runtime fingerprint equality fails. |

`HIT`, `MISS`, and `LOCKED_OR_IN_PROGRESS` use `reason=None`.
`LOCKED_OR_IN_PROGRESS` is emitted only when the expected final entry is absent
and the single matching lock is active under the approved lock-lifecycle contract.
The family order preserves locked Step 5 section 23.9: unsafe, unsupported,
invalid, integrity, producer, schema, runtime, lock, miss, hit.

### 5.2 Absence is narrow

`MISS` means the expected final-entry path is absent and the single matching lock
is not active under the approved lock-lifecycle contract. Missing descendants are
`INCOMPLETE_ENTRY`; unreadable, malformed, incompatible, or corrupt entries never
become a miss. Until read-only lock observation is implemented in Step 5B4 and
composed by Step 5B5, no partial implementation may expose this contract-complete
public `MISS` behavior.

## 6. Filesystem object policy

A valid final entry contains exactly:

```text
<entry>/
├── metadata.json   regular file
├── manifest.json   regular file
├── payload/        directory
│   └── manifest-declared regular files only
└── COMPLETE        regular file
```

Policy:

- Absent expected entry: inspect only the matching lock read-only; return
  `LOCKED_OR_IN_PROGRESS` when it is active, otherwise `MISS`.
- Entry path is a regular file or other non-directory: `UNSAFE_OBJECT`.
- Symlink at entry, any ancestor below the validated root, any contract document, `payload/`, or any payload path: `UNSAFE_OBJECT`; never follow it.
- Metadata, manifest, or marker that is a directory or special object: `UNSAFE_OBJECT`.
- FIFO, socket, block device, character device, or unknown file type anywhere inspected: `UNSAFE_OBJECT`; never open it.
- Nested payload directories: `UNEXPECTED_PAYLOAD_OBJECT`. Contract v1 represents slash-containing manifest paths but the locked top-level text also says nested payloads may exist. Step 5B resolves this by permitting intermediate directories only when they are required prefixes of declared manifest paths, contain no undeclared descendants, are not symlinks, and pass stability checks. All other directories are rejected.
- Detectable link count greater than one for a payload regular file:
  `status=INTEGRITY_FAILURE`, `reason=PAYLOAD_HARDLINK_DETECTED`.
- Inability to determine a payload link count is a deterministic diagnostic and
  platform-capability limitation; it is not automatic corruption.
- Contract documents are not subject to a separate hardlink classification in
  contract v1; their type, bounded-read, digest, and stability rules still apply.
- Unknown top-level regular file or directory: `status=INVALID_ENTRY`, `reason=UNEXPECTED_TOP_LEVEL_OBJECT`.
- Unknown top-level symlink, FIFO, socket, device, or other unsafe object: `status=UNSAFE_PATH`, `reason=UNSAFE_OBJECT`. Unsafe-object precedence wins even though diagnostics also include `entry.unexpected_top_level_object`.
- Regular payload file not declared by the manifest: `UNEXPECTED_PAYLOAD_OBJECT`.
- Manifest path absent from disk: `PAYLOAD_MISSING`.
- Empty payload: valid only when manifest records, metadata count, and metadata total bytes are all zero and trusted `ProducerPayloadExpectation.cardinality` is `EMPTY_ALLOWED`.
- Zero-byte declared regular files are valid and must hash to the declared SHA-256 value.
- Any race or identity change during traversal: `UNSTABLE_SNAPSHOT`.

## 7. Root and containment safety

Lookup applies both lexical and observed containment:

1. Derive the expected path only with Step 5A `derive_final_entry_path(cache_root.resolved_path, namespace, cache_key)`.
2. Confirm its lexical relative path exactly equals `entries/v1/<namespace>/<shards>/<digest>`.
3. Inspect each component from the validated root to the entry with no-follow metadata operations.
4. Reject every symlink component.
5. Confirm each opened descendant is beneath the expected entry by its validated relative path, not by string prefix.
6. Construct payload candidates only from validated `PayloadManifestRecord.relative_path` values.
7. Confirm every payload candidate remains lexically beneath `payload/` before inspection.
8. Use descriptor-relative or no-follow platform operations where available to reduce substitution risk.

The existing `assess_path_safety()` is inventory-oriented: it resolves paths, incorporates Git state, and classifies cleanup protection. It must not be reused as the complete lookup validator. Small pure containment concepts may be extracted or a dedicated read-only lookup adapter may be built without changing inventory behavior.

`GitTrackingStatus` is irrelevant to cache integrity. Tracked, untracked, unknown, and not-applicable states neither validate nor invalidate entry content.

Mutation-oriented checks—approved mutation roots, pre-mutation Git revalidation, same-filesystem promotion checks, and lock ownership—do not belong in Step 5B.

## 8. Deterministic read order

The required order is:

1. Validate request and policy types/invariants.
2. Revalidate cache-root directory identity.
3. Derive the single expected entry path and digest.
4. Inspect root-to-entry components without following symlinks.
5. If the entry is absent, derive and inspect only the matching lock read-only;
   return `LOCKED_OR_IN_PROGRESS` if it is active under the approved lock-lifecycle
   contract, otherwise return `MISS`.
6. If the entry exists, require a directory; do not inspect a lock or allow lock
   state to hide or override any valid or invalid entry result.
7. Capture entry-directory pre-read identity and enumerate its top-level names once.
8. Inspect every enumerated top-level name without following symlinks, including unknown names, so unsafe-object precedence is established before generic unexpected-name classification.
9. Enforce exactly `COMPLETE`, `metadata.json`, `manifest.json`, and `payload`, and enforce their required object types.
10. Read bounded `COMPLETE`, then metadata, then manifest using no-follow regular-file handles.
11. Parse recognizable version fields cautiously, then parse all three with strict Step 5A canonical parsers.
12. Validate expected identity, cross-document identity, namespace/producer consistency, runtime expectation, and optional artifact expectation.
13. Hash the exact stored metadata and manifest bytes and validate every document digest.
14. Validate metadata count/byte totals against the manifest through Step 5A `CacheEntryContract`.
15. Safely enumerate the payload tree in deterministic ordinal relative-path order.
16. Compare the exact observed regular-file set with the manifest path set.
17. Inspect types, detectable link counts, and sizes.
18. Hash every payload file in manifest order.
19. Recheck each file identity after hashing.
20. Re-enumerate payload and top-level names and recheck directory/root identities.
21. Return the highest-precedence deterministic result.

Reading `COMPLETE` first cheaply establishes intended completeness and version while conveying no trust. Documents precede payload traversal so hostile or oversized manifests cannot trigger arbitrary enumeration. Stored document digests are checked before expensive payload hashing. Post-read checks prevent a changing snapshot from being accepted silently.

## 9. Bounded reads and resource limits

Defaults are explicit and immutable per request policy:

| Limit | Default |
|---|---:|
| `COMPLETE` bytes | 4 KiB |
| `metadata.json` bytes | 256 KiB |
| `manifest.json` bytes | 8 MiB |
| Payload records | 100,000 |
| UTF-8 bytes in one manifest relative path | 1,024 |
| Payload traversal depth below `payload/` | 64 segments |
| Individual payload size | 1 TiB |
| Declared total payload bytes | 16 TiB |
| Public diagnostics | 32 |
| Hash/read chunk size | 1 MiB |

Limits must be positive integers, and individual payload size must not exceed total payload size. The reader checks regular-file size before allocation and reads contract documents as at most `limit + 1` bytes; exceeding a limit is rejected before JSON parsing. Payloads are streamed and never allocated wholly in memory.

Callers may lower limits. Raising defaults requires an explicit reviewed policy at the integration boundary; values are never inferred from available memory, disk capacity, environment variables, or machine type.

An oversized known-version document is malformed. If a safely bounded version probe reveals a future recognizable version, unsupported-version status takes precedence.

## 10. Verification level

Step 5B exposes one successful verification mode: `FULL_PAYLOAD_SHA256`.

The default and only public lookup policy hashes every payload byte. Structure-only or document-only progress may appear in a failed result's `verification_level`, but those levels cannot produce `HIT`, a validated entry reference, or `payload_bytes_fully_hashed=True`.

This follows the locked requirement that lookup verify file existence, type, size, and digest before returning a cache hit. No configurability may weaken that guarantee.

## 11. Digest verification

All digests use SHA-256 and lowercase algorithm-qualified syntax where the model requires it.

For stored `metadata.json` and `manifest.json`:

1. bounded-read the exact stored bytes;
2. parse with the Step 5A strict parser, which rejects BOMs, whitespace differences, duplicate keys, floats, non-finite numbers, unknown fields, and noncanonical encoding;
3. reserialize the parsed model canonically;
4. require reserialized bytes to equal stored bytes exactly; and
5. hash the exact stored bytes.

Calculations:

```text
metadata_sha256 = "sha256:" + sha256(stored_metadata_bytes).hexdigest()
manifest_sha256 = "sha256:" + sha256(stored_manifest_bytes).hexdigest()
```

Required comparisons:

- `COMPLETE.metadata_digest == metadata_sha256`;
- `COMPLETE.manifest_digest == manifest_sha256`;
- `metadata.payload_manifest_digest == manifest_sha256`;
- for each record, `"sha256:" + sha256(exact_payload_bytes).hexdigest() == record.digest`.

Semantically equivalent but noncanonical JSON is invalid and is never normalized into acceptance.

## 12. Identity validation

Lookup checks all of the following:

- Expected digest equals `derive_entry_digest(cache_key)`.
- Expected path's terminal digest and shard segments equal the derived digest.
- Metadata `entry_digest` equals the expected digest.
- `COMPLETE.entry_digest` equals the expected digest.
- Metadata cache-key reference equals `CacheKeyReference.from_cache_key(cache_key)`.
- Reconstructed metadata key derives the same entry digest.
- Metadata namespace equals the expected namespace.
- Metadata namespace producer ID equals metadata producer ID.
- Metadata namespace schema equals metadata producer schema.
- Observed producer ID equals `CacheLookupExpectation.producer_id`.
- Observed producer schema equals `CacheLookupExpectation.producer_schema_version`.
- Metadata runtime fingerprint exactly equals the expected runtime fingerprint.
- Optional artifact expectation matches observed `artifact_kind` and `artifact_contract_version`.
- Observed `logical_id` is ignored for compatibility unless `CacheArtifactExpectation.expected_logical_id` is non-`None`; then exact equality is required.
- Metadata manifest digest equals stored manifest SHA-256.
- Marker manifest and metadata digests equal stored document SHA-256 values.
- Metadata count and byte totals equal manifest records.

`logical_id` is not compared merely because an artifact expectation exists. It is compared only when the separate `expected_logical_id` field is explicitly populated. It never selects or alters the root, namespace, shard, digest, or payload path and never becomes cache identity.

## 13. Payload validation

The reader builds two deterministic ordinal lists:

- declared manifest relative paths; and
- observed payload regular-file relative paths.

They must be exactly equal. Intermediate directories are allowed only as prefixes required by declared paths. The reader rejects undeclared files, missing declared files, empty unexpected directories, unsupported objects, symlinks, and extra directory branches.

For every manifest record in manifest order:

1. validate lexical containment under `payload/`;
2. capture no-follow pre-read identity;
3. require a regular file and detectable link count of one;
4. require observed size to equal `size_bytes`;
5. stream exactly that many bytes through SHA-256 in configured chunks;
6. reject early EOF or growth beyond declared size;
7. require digest equality;
8. compare handle-level and path-level post-read identity and metadata with pre-read values.

Zero-byte files are valid when declared with size zero and the correct empty-content digest. No manifest record is allowed to reference a directory.

## 14. Stable snapshot and TOCTOU policy

Step 5B cannot eliminate races and acquires no lock. It must detect instability conservatively.

`FileIdentity` records, where supported:

- object type;
- device ID;
- inode/file ID;
- size;
- nanosecond modification time;
- nanosecond change time where available; and
- detectable link count.

Strategy:

- Capture cache root and entry directory identity before validation and recheck afterward.
- Capture document and payload identity before opening, obtain handle identity immediately after open, and require them to agree.
- Open regular files with no-follow semantics where supported.
- Compare handle identity before and after reading.
- Re-inspect the path after close to detect replacement.
- Capture sorted top-level and payload directory listings before and after validation.
- Treat replacement, size/timestamp change, listing change, disappearance, or inability to establish required identity as `UNSTABLE_SNAPSHOT`.
- On platforms without stable device/inode identity, use every available type, size, timestamp, handle, and directory check; record a stable diagnostic indicating reduced platform evidence. A `HIT` is allowed only if the adapter's minimum reviewed platform guarantees are satisfied.

Default retry count is zero. Automatic retries could turn an active writer into an unpredictable latency loop and obscure instability. Callers may initiate a new independent lookup later; the result of each attempt remains deterministic.

## 15. Error and diagnostic policy

`CacheLookupDiagnostic` contains exactly:

```text
code: CacheLookupDiagnosticCode
subject: CacheLookupSubject
relative_path: str | None
```

Codes are stable machine-readable enum values such as:

- `entry.absent`;
- `entry.required_object_missing`;
- `object.symlink`;
- `object.unsupported_type`;
- `document.noncanonical`;
- `document.oversized`;
- `document.unknown_field`;
- `version.unsupported`;
- `identity.entry_digest_mismatch`;
- `identity.cache_key_mismatch`;
- `identity.namespace_mismatch`;
- `digest.metadata_mismatch`;
- `digest.manifest_mismatch`;
- `payload.missing`;
- `payload.undeclared`;
- `payload.size_mismatch`;
- `payload.digest_mismatch`;
- `snapshot.changed`;
- `io.permission_denied`;
- `io.read_failed`.

Diagnostics are deduplicated and sorted by:

1. locked broad status-family precedence;
2. detailed reason precedence within the family;
3. diagnostic code value;
4. subject value; and
5. sanitized relative path in ordinal order.

At most 32 diagnostics are public. When truncated, the last diagnostic is `diagnostics.truncated`. Relative paths are limited to validated entry-relative contract names or manifest-relative payload paths. Absolute roots, unrelated paths, raw symlink targets, usernames, hostnames, inode values, and arbitrary exception strings are never exposed.

Internal logs may retain exception classes and correlation identifiers under the application's existing privacy policy, but not through the stable result contract. Permission errors map to `status=INVALID_ENTRY`, `reason=IO_FAILURE` plus `io.permission_denied`; transient reads map to the same family and reason unless change detection proves `UNSTABLE_SNAPSHOT`, which has higher within-family reason precedence.

For an unsafe unknown top-level object, the primary result is `status=UNSAFE_PATH`, `reason=UNSAFE_OBJECT`. Diagnostics include the specific object code such as `object.symlink` and the lower-precedence contextual code `entry.unexpected_top_level_object`.

## 16. Unsupported versions

The broad `UNSUPPORTED_VERSION` status is used only when a bounded, safely parsed discriminator is recognizable without trusting the rest of the document. Its primary reason identifies the version domain.

- Unsupported entry contract version: `UNSUPPORTED_ENTRY_VERSION`.
- Unsupported manifest version: `UNSUPPORTED_MANIFEST_VERSION`.
- Unsupported cache-key canonical version: `UNSUPPORTED_CACHE_KEY_VERSION`.
- Unsupported runtime-fingerprint schema: `UNSUPPORTED_RUNTIME_FINGERPRINT_VERSION`.
- Unknown fields under a supported version: `status=INVALID_ENTRY` with the relevant malformed-document reason, because contract v1 is strict.
- A future version with future fields: unsupported version takes precedence over unknown-field corruption when the version discriminator is recognizable.
- Missing, wrong-type, duplicated, non-integer, or otherwise malformed version fields are malformed, not unsupported.

The version probe must itself be bounded and reject duplicate keys. It does not construct a Step 5A v1 model before classification.

## 17. Observability

An optional injected observer may receive read-only in-memory events:

- `lookup_attempted`;
- `lookup_absent`;
- `lookup_hit`;
- `lookup_rejected`;
- `lookup_unsupported`;
- `lookup_unstable`;
- `lookup_io_failure`;
- `payload_bytes_hashed` aggregate; and
- `verification_duration` aggregate.

Observer failures never alter the lookup result and are swallowed or isolated by the observability boundary. Events contain status, namespace, digest, counts, and durations—not payload contents or absolute paths. The observer must not write into the cache root or entry. Step 5B itself creates no logs or metric files.

## 18. Testing contract

Step 5B implementation is not approved until tests cover:

### 18.1 Valid and absent entries

- valid non-empty entry with full hashing;
- valid zero-byte payload file;
- valid empty entry only with trusted producer-derived `EMPTY_ALLOWED` cardinality;
- empty entry rejected under the default `NON_EMPTY_REQUIRED` cardinality;
- proof that ordinary UI or generic configuration cannot construct or override `EMPTY_ALLOWED`;
- absent expected entry plus an active matching lock returns `LOCKED_OR_IN_PROGRESS`;
- absent expected entry plus no lock returns `MISS`;
- absent expected entry plus a non-active or stale matching lock returns `MISS`;
- valid final entry plus any matching lock still returns `HIT`;
- invalid final entry plus any matching lock returns the entry validation failure;
- unrelated locks are never scanned or used;
- proof that siblings and unrelated namespaces are never scanned.

### 18.2 Structure and object safety

- wrong object type at entry path;
- symlink at every root-to-entry component;
- symlinked metadata, manifest, marker, payload directory, intermediate directory, and payload file;
- missing metadata, manifest, marker, or payload directory;
- unknown top-level regular file and directory classified `INVALID_ENTRY` / `UNEXPECTED_TOP_LEVEL_OBJECT`;
- unknown top-level symlink, FIFO, socket, device, and other unsafe object classified `UNSAFE_PATH` / `UNSAFE_OBJECT`, with both unsafe-object and unexpected-name diagnostics;
- undeclared payload file;
- nested required directory and unexpected nested directory;
- FIFO, socket, device, and unknown object where the platform permits;
- detectable payload hardlink classified as `INTEGRITY_FAILURE` /
  `PAYLOAD_HARDLINK_DETECTED`;
- unavailable hardlink evidence handled deterministically as a platform limitation;
- no-follow adapter behavior.

### 18.3 Documents and versions

- noncanonical JSON variants;
- duplicate JSON keys;
- unknown fields at every strict schema level;
- oversized marker, metadata, and manifest;
- excessive record count, path length, depth, individual size, and total size;
- unsupported entry, manifest, cache-key, and runtime-fingerprint versions;
- malformed version discriminators;
- future version with unknown fields classified unsupported.

### 18.4 Identity and integrity

- path shard/digest mismatch;
- metadata entry-digest mismatch;
- marker entry-digest mismatch;
- cache-key mismatch;
- namespace mismatch;
- namespace/producer mismatch;
- producer ID and schema mismatch;
- runtime fingerprint mismatch;
- artifact kind mismatch and artifact contract-version mismatch;
- differing informational `logical_id` accepted by default;
- explicit `expected_logical_id` mismatch;
- metadata manifest-digest mismatch;
- marker metadata-digest mismatch;
- marker manifest-digest mismatch;
- metadata count and byte-total mismatch;
- missing declared payload;
- payload size mismatch;
- payload digest mismatch;
- early EOF and growth during hashing.

### 18.5 Stability, diagnostics, and boundaries

- file replacement before open, during hashing, and after close;
- document mutation during read;
- directory mutation during enumeration;
- root or entry replacement;
- platform identity limitations;
- permission and transient I/O errors;
- deterministic broad-status and detailed-reason precedence for multiple faults;
- deterministic diagnostic sorting, deduplication, sanitization, and truncation;
- observer success and failure do not alter results;
- no filesystem mutation;
- no calls to write, create, rename, delete, chmod, acquire, refresh, release,
  break, repair, or cleanup APIs; read-only matching-lock inspection is allowed;
- inventory remains separate and unchanged;
- all Step 5A and previous storage tests remain passing;
- full project regression remains passing.

## 19. Step 5C and later boundary

Reserved for separately approved later work:

- writers and payload construction;
- staging directory creation;
- promotion and atomic rename;
- lock ownership, heartbeat, and release;
- interrupted-write recovery and stale-lock handling;
- repair, quarantine, migration, deletion, cleanup, and eviction;
- persistent cache index;
- producer and rendering integration.

Step 5B results never authorize any of these actions.

## 20. Proposed implementation decomposition

Step 5B1 is implemented. The remaining units are a gated implementation plan:

### Step 5B1 — Read-only adapter and limits

- `ValidatedCacheRoot`;
- no-follow object metadata;
- bounded document reads;
- immutable resource policy;
- no mutation-capable adapter methods.

### Step 5B2 — Structure and document validation

- exact top-level validation;
- canonical Step 5A parsing;
- unsupported-version probes;
- identity and cross-document validation.

### Step 5B3 — Payload validation

- safe deterministic payload enumeration;
- exact manifest/filesystem comparison;
- type, hardlink, size, and streaming SHA-256 checks.

### Step 5B4 — Stable snapshot and diagnostics

- pre/open/post identity checks;
- directory re-enumeration;
- deterministic broad statuses, detailed reasons, and sanitized diagnostics;
- observability isolation.
- internal read-only observation of only the matching lock under
  `STEP_5B_LOCK_LIFECYCLE_OBSERVATION_CONTRACT.md`.

### Step 5B5 — Public lookup orchestration and regression/integration hardening

- compose entry validation and matching-lock observation into the public lookup;
- expose `LOCKED_OR_IN_PROGRESS` and contract-complete `MISS` only here;
- adversarial filesystem tests;
- platform-specific no-follow tests;
- Step 5A/storage/full regression;
- public export review only after behavior is locked.

No unit acquires or mutates locks or introduces writing, repair, cleanup, indexing,
producer execution, or rendering integration. Before Step 5B5, lower-level helpers
remain internal and must not claim final public `MISS` semantics.

## 21. Ambiguities and conservative resolutions

### 21.1 Lock-aware absence

The locked Step 5 contract requires `LOCKED_OR_IN_PROGRESS` when the final entry is
absent and the matching lock is active. Step 5B therefore includes narrowly scoped,
read-only observation of only the lock derived by Step 5A `derive_lock_path()` for the
validated namespace and cache key. It never scans lock siblings. A present final
entry is validated without consulting or being overridden by lock state.

`STEP_5B_LOCK_LIFECYCLE_OBSERVATION_CONTRACT.md` normatively defines the strict lock
document, explicit freshness policy, injected UTC clock, active/stale predicate,
future-timestamp handling, result mapping, bounded stable read, and mutation boundary.
Step 5B4 implements that read-only observation contract. Step 5B5 composes it with
entry validation and is the first substep allowed to expose contract-complete public
`MISS` and `LOCKED_OR_IN_PROGRESS` behavior. Step 5B2 and Step 5B3 remain internal
helpers and must not treat bare entry absence as a final public result.

### 21.2 Nested payload directories

The locked contract permits normalized relative manifest paths and tests nested payloads, while its top-level wording can be read as rejecting directories. Step 5B permits only intermediate directories that are exact prefixes of declared manifest paths and rejects every other payload directory.

### 21.3 Empty payload permission

Step 5A models zero records and zero totals but does not encode whether a producer permits empty output. Step 5B introduces a trusted `ProducerPayloadExpectation` sourced only from a registered producer contract or producer adapter. Its default is `NON_EMPTY_REQUIRED`; ordinary callers cannot authorize `EMPTY_ALLOWED`. Resource policy contains no payload-semantic override.

### 21.4 Producer version expectation

Step 5A `CacheLookupExpectation` constrains producer ID, schema, and runtime fingerprint but not producer release version or artifact metadata. Step 5B does not silently invent a producer-version constraint. Artifact comparison is optional and explicit: kind and artifact contract version are compatibility-bearing, while logical ID requires a separate `expected_logical_id`. Adding producer-version compatibility requires a later reviewed expectation model.

### 21.5 Existing result model

Step 5A intentionally provides only the locked broad `status` and text diagnostics. Step 5B preserves that enum and adds a separate `CacheLookupReason` plus machine-readable diagnostics. A later implementation must evolve the result shape without changing Step 5A status semantics or exposing raw exception text.

### 21.6 Platform stability guarantees

Filesystem identity primitives vary. Step 5B requires a reviewed adapter capability floor per supported platform and fails closed with `UNSTABLE_SNAPSHOT` when that floor cannot establish a stable read.

## 22. Acceptance criteria

The Step 5B design is ready for implementation review only when reviewers agree that:

- exactly one expected final path is inspected;
- a hit always means full payload SHA-256 verification;
- canonical stored document bytes and every required digest are verified;
- the existing broad Step 5A status enum is unchanged and every detailed reason maps to exactly one family;
- expected values and observed values are distinguishable;
- containment and no-follow behavior are fail-closed;
- resource use is explicitly bounded;
- unstable reads cannot become hits;
- diagnostics are deterministic and sanitized;
- inventory, lock mutation/management, recovery, mutation, indexing, producers,
  and rendering remain outside Step 5B; read-only matching-lock observation is included; and
- this document adds no production behavior.
