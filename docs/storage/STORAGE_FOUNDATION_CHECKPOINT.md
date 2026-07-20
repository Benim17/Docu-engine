# Step 5 — Versioned Persistent Cache Entry Contract

Status: **Design proposal for review**  
Branch: `feature/cache-storage-foundation`  
Scope: **Design only — no production implementation in this step**

## 1. Purpose

This specification defines the first persistent cache-entry contract for Documentary Engine.

The design must provide:

- deterministic mapping from a validated `CacheKey` to a physical cache location;
- immutable, versioned cache entries;
- integrity-checked payloads;
- safe staging and atomic promotion;
- concurrent producer protection;
- crash recovery semantics;
- read-only lookup behavior;
- strict separation from inventory and future cleanup execution.

This specification does **not** define deletion, eviction, cleanup execution, quota enforcement, or automatic cache pruning.

---

## 2. Design principles

1. A cache entry is immutable after successful promotion.
2. Only a parsed and validated `CacheKey` may influence physical cache paths.
3. Human-readable identifiers such as `logical_id` remain metadata only.
4. Staging and final entries are physically separate.
5. An entry is readable only after all payloads and metadata pass validation.
6. Promotion must be atomic and occur on the same filesystem.
7. Inventory remains an observational subsystem and never becomes a mutation plan.
8. The future persistent cache index remains separate from inventory.
9. Runtime fingerprints are declared by producer adapters, never inferred from arbitrary machine state.
10. Every future mutation must revalidate safety immediately before mutation.

---

## 3. Contract versions

Separate version domains are required.

### 3.1 Cache entry contract version

```text
cache_entry_contract_version = 1
```

Controls:

- entry directory layout;
- metadata file schema;
- payload manifest schema;
- completeness semantics;
- lookup validation rules.

### 3.2 Artifact contract version

Owned by the artifact descriptor/model layer. It must not implicitly control cache-entry persistence format.

### 3.3 Inventory report version

Owned independently by inventory. It must not follow artifact or cache-entry contract versions.

### 3.4 Producer schema version

Each producer declares its own schema version, for example:

```text
producer_id = "audio.transcription.whisper"
producer_schema_version = 3
```

A producer schema version changes when the producer's semantic output contract changes.

---

## 4. Cache namespace

Every persistent entry belongs to a validated cache namespace.

Recommended namespace contract:

```text
<domain>/<producer_id>/<producer_schema_version>
```

Example:

```text
audio/transcription.whisper/3
```

The namespace is metadata and path material only after validation.

### 4.1 Namespace validation

Each segment must:

- be non-empty;
- contain only lowercase ASCII letters, digits, `.`, `_`, or `-`;
- begin and end with an ASCII letter or digit;
- contain no path separators;
- contain no `..` segment;
- have a bounded length;
- be normalized before use.

Suggested limits:

- segment length: 1–80 characters;
- complete namespace length: at most 240 characters.

---

## 5. Validated CacheKey-to-path mapping

A physical cache path must be derived only from the canonical serialized form or digest of a validated `CacheKey`.

### 5.1 Entry digest

Recommended:

```text
entry_digest = sha256(CacheKey.canonical_bytes()).hexdigest()
```

Properties:

- lowercase hexadecimal;
- exactly 64 characters;
- independent of machine paths and runtime locations;
- independent of `logical_id` formatting.

### 5.2 Sharding

Recommended two-level sharding:

```text
<digest[0:2]>/<digest[2:4]>/<digest>
```

Example:

```text
ab/cd/abcdef...64chars
```

This avoids very large single directories while preserving deterministic lookup.

### 5.3 Final path layout

```text
<cache_root>/entries/v1/<namespace>/<shard_1>/<shard_2>/<entry_digest>/
```

Example:

```text
.cache/documentary-engine/entries/v1/audio/transcription.whisper/3/ab/cd/abcdef.../
```

### 5.4 Staging path layout

```text
<cache_root>/staging/v1/<namespace>/<entry_digest>.<writer_token>/
```

`writer_token` must be opaque, unique, and safe as a path segment. Recommended source:

```text
uuid4().hex
```

Staging must be on the same filesystem as final entries so that promotion can use an atomic rename.

### 5.5 Lock path layout

```text
<cache_root>/locks/v1/<namespace>/<shard_1>/<shard_2>/<entry_digest>.lock
```

Lock files are not cache entries and must never be reported as payload artifacts.

---

## 6. Final cache-entry directory

A complete entry contains:

```text
<entry>/
├── metadata.json
├── manifest.json
├── payload/
│   └── ... producer-defined files ...
└── COMPLETE
```

No other top-level files are required by contract v1.

Unknown top-level files should cause strict readers to reject the entry unless explicitly allowed by a future contract version.

---

## 7. `metadata.json` contract

Recommended schema:

```json
{
  "cache_entry_contract_version": 1,
  "entry_digest": "<64 lowercase hex>",
  "cache_key": {
    "canonical_version": 1,
    "canonical_value": "<canonical serialized representation>"
  },
  "namespace": {
    "domain": "audio",
    "producer_id": "transcription.whisper",
    "producer_schema_version": 3
  },
  "artifact": {
    "artifact_kind": "transcript",
    "logical_id": "episode-001-voiceover",
    "artifact_contract_version": 1
  },
  "producer": {
    "producer_id": "transcription.whisper",
    "producer_version": "4.2.0",
    "producer_schema_version": 3
  },
  "runtime_fingerprint": {
    "schema_version": 1,
    "values": {
      "model": "whisper-large-v3",
      "language": "en"
    }
  },
  "created_at_utc": "2026-07-20T09:00:00Z",
  "payload_manifest_digest": "sha256:<hex>",
  "payload_file_count": 2,
  "payload_total_bytes": 123456
}
```

### 7.1 Required properties

- `cache_entry_contract_version`
- `entry_digest`
- canonical cache-key representation
- namespace identity
- producer identity and producer schema version
- runtime fingerprint, including its own schema version
- creation timestamp in UTC
- manifest digest
- payload count and total bytes

### 7.2 Metadata invariants

- `entry_digest` must equal the digest derived from the validated `CacheKey`.
- producer identity in metadata must match namespace identity.
- producer schema version must match the namespace segment.
- timestamps are observational and never part of cache identity.
- `logical_id` is informational only and must never be used for lookup or path construction.
- physical paths must not appear in identity-bearing fields.
- metadata must be UTF-8 JSON with deterministic serialization for digest verification where applicable.

---

## 8. `manifest.json` contract

Recommended schema:

```json
{
  "manifest_version": 1,
  "files": [
    {
      "relative_path": "transcript.json",
      "size_bytes": 120000,
      "digest": "sha256:<hex>",
      "media_type": "application/json",
      "role": "primary"
    },
    {
      "relative_path": "segments.json",
      "size_bytes": 3456,
      "digest": "sha256:<hex>",
      "media_type": "application/json",
      "role": "auxiliary"
    }
  ]
}
```

### 8.1 Manifest invariants

Every file record must have:

- a normalized relative path;
- no absolute path;
- no empty path segment;
- no `.` or `..` segment;
- no symlink target;
- exact byte size;
- SHA-256 digest;
- declared media type;
- declared semantic role.

The manifest itself must:

- list every regular file under `payload/` exactly once;
- contain no duplicate paths;
- be sorted by normalized relative path;
- exclude `metadata.json`, `manifest.json`, and `COMPLETE`;
- reject directories, devices, sockets, FIFOs, symlinks, and hardlink ambiguity where detectable.

### 8.2 Digest syntax

Use an algorithm-qualified digest:

```text
sha256:<64 lowercase hex>
```

Contract v1 accepts SHA-256 only.

---

## 9. Completeness marker

The `COMPLETE` marker is the final file created inside staging before promotion.

Recommended content:

```json
{
  "cache_entry_contract_version": 1,
  "entry_digest": "<64 lowercase hex>",
  "metadata_digest": "sha256:<hex>",
  "manifest_digest": "sha256:<hex>"
}
```

The marker must be:

- a regular file;
- immutable after promotion;
- written only after payload, manifest, and metadata have been fully flushed and validated;
- verified during lookup.

Presence alone is not sufficient. Its values must match the entry contents.

---

## 10. Write protocol

A future writer must use this sequence.

### Phase A — Validate intent

1. Parse and validate `CacheKey`.
2. Derive canonical bytes and `entry_digest`.
3. Validate namespace.
4. Derive final, staging, and lock paths.
5. Verify all resolved paths remain beneath approved roots.
6. Verify target filesystem supports atomic rename semantics required by the implementation.

### Phase B — Acquire producer lock

1. Atomically create or acquire the entry lock.
2. Record lock ownership metadata.
3. Revalidate containment, file type, symlink state, Git status, approved roots, and entry identity.
4. Check whether a valid final entry already exists.
5. If it exists and validates, return a cache hit and do not overwrite.

### Phase C — Build staging entry

1. Create a unique staging directory.
2. Create `payload/`.
3. Write payload files.
4. Flush each payload file.
5. Build deterministic manifest.
6. Hash every payload file.
7. Write and flush `manifest.json`.
8. Write and flush `metadata.json`.
9. Validate staging entry as though it were final.
10. Write and flush `COMPLETE` last.
11. Flush the staging directory and relevant parent directory where supported.

### Phase D — Immediate pre-promotion safety gate

Immediately before mutation of the final namespace, revalidate:

- cache root identity;
- final path containment;
- staging path containment;
- file types;
- symlink absence;
- Git tracking status;
- approved mutation roots;
- validated `CacheKey` identity;
- lock ownership and freshness;
- absence or validity of the final entry;
- staging and final paths being on the same filesystem.

`GitTrackingStatus.NOT_APPLICABLE` is neutral only. It never independently proves safety.

### Phase E — Atomic promotion

1. If no final entry exists, atomically rename the complete staging directory to the final path.
2. Never merge files into an existing entry.
3. Never overwrite a valid final entry.
4. If another producer won the race, validate the winner and discard the local staging entry.
5. Flush the final parent directory where supported.

### Phase F — Release

1. Release the lock.
2. Remove only the writer's own abandoned staging directory, if still present.
3. Return the final validated entry reference.

---

## 11. Lock ownership contract

Recommended lock metadata:

```json
{
  "lock_version": 1,
  "entry_digest": "<64 lowercase hex>",
  "owner_token": "<uuid4 hex>",
  "process_id": 12345,
  "host_id": "<declared adapter value or opaque installation id>",
  "acquired_at_utc": "2026-07-20T09:00:00Z",
  "heartbeat_at_utc": "2026-07-20T09:00:10Z"
}
```

### 11.1 Requirements

- lock acquisition must be atomic;
- ownership must be verified before release;
- a producer may remove only a lock it demonstrably owns or a stale lock accepted by a separate recovery policy;
- process ID alone is insufficient for ownership;
- stale-lock handling must be conservative;
- lock timestamps do not participate in cache identity.

### 11.2 Stale lock policy

Contract v1 should define stale-lock detection but not automatically break locks without explicit recovery logic.

A lock may be considered *eligible for recovery review* when:

- heartbeat age exceeds a configured threshold;
- owner process cannot be verified locally, where meaningful;
- no active staging mutation is detected;
- all safety checks pass.

Eligibility is not permission to mutate.

---

## 12. Interrupted-write recovery

Recovery distinguishes final entries, staging entries, and locks.

### 12.1 Final entries

A final entry is either:

- **valid** — all contract checks pass;
- **invalid/incomplete** — never returned as a cache hit;
- **unsupported** — contract version is unknown;
- **conflicted** — path identity does not match entry metadata.

Contract v1 readers must not repair or delete invalid final entries automatically.

### 12.2 Staging entries

Staging entries are never visible as hits.

They may be classified as:

- active;
- complete but not promoted;
- incomplete;
- stale;
- malformed;
- ownership unknown.

Recovery may later promote a complete staging entry only after:

- lock ownership or recovery authority is established;
- the full entry validates;
- final path absence is confirmed;
- all immediate mutation safety checks pass.

Automatic staging cleanup is outside Step 5.

### 12.3 Orphan locks

Orphan-lock reporting may be read-only in Step 5. Breaking or deleting locks belongs to a later mutation/recovery implementation.

---

## 13. Read-only lookup semantics

Lookup input:

- validated `CacheKey`;
- expected namespace;
- expected producer ID;
- expected producer schema version;
- expected runtime fingerprint policy.

### 13.1 Lookup sequence

1. Validate `CacheKey`.
2. Derive `entry_digest` and final path.
3. Confirm path containment.
4. Reject symlinks or unsupported file types at every path component where required.
5. Require regular `metadata.json`, `manifest.json`, and `COMPLETE`.
6. Parse known contract versions.
7. Recompute and compare entry identity.
8. Validate producer and schema matching.
9. Validate runtime fingerprint according to producer-declared rules.
10. Validate manifest structure.
11. Verify file existence, type, size, and digest.
12. Verify completeness marker digests.
13. Return an immutable read handle/reference only after all checks pass.

### 13.2 Lookup result states

Recommended explicit states:

- `HIT`
- `MISS`
- `INVALID_ENTRY`
- `UNSUPPORTED_VERSION`
- `PRODUCER_MISMATCH`
- `SCHEMA_MISMATCH`
- `RUNTIME_FINGERPRINT_MISMATCH`
- `INTEGRITY_FAILURE`
- `UNSAFE_PATH`
- `LOCKED_OR_IN_PROGRESS`

A malformed or unsafe entry must not collapse into a normal hit.

### 13.3 Read behavior

- Readers never mutate metadata, access timestamps, manifests, or markers.
- Readers never repair entries.
- Readers never consume `InventoryRecord` or `InventoryReport` as lookup truth.
- A future persistent index may accelerate discovery but cannot override on-disk validation.

---

## 14. Runtime fingerprint contract

The producer adapter declares:

- fingerprint schema version;
- identity-bearing keys;
- normalization rules;
- compatibility policy.

Example:

```json
{
  "schema_version": 1,
  "values": {
    "model": "whisper-large-v3",
    "language": "en",
    "diarization": false
  }
}
```

Rules:

- no automatic scan of arbitrary environment variables;
- no absolute paths;
- no temporary-directory locations;
- no machine-specific values unless the producer explicitly declares them semantically necessary;
- ordering must be deterministic;
- unknown fingerprint schema versions are not assumed compatible.

---

## 15. Persistent index boundary

A future persistent cache index may store:

- entry digest;
- namespace;
- final relative path;
- validated state at a point in time;
- producer identity;
- sizes and timestamps as observations.

It must not:

- replace final-entry validation;
- be derived directly from `InventoryReport` as mutation authority;
- use `ArtifactDescriptor.identity_dict()` as cache lookup material;
- authorize deletion;
- make an unsafe path safe.

The index has its own independent schema version.

---

## 16. Security and filesystem invariants

Every implementation must defend against:

- path traversal;
- Unicode and case aliases where the host filesystem permits ambiguity;
- symlink substitution;
- hardlink-related accounting ambiguity;
- special files;
- staging/final filesystem mismatch;
- time-of-check/time-of-use races;
- lock theft;
- partial writes;
- digest mismatch;
- metadata/payload disagreement;
- stale or forged completeness markers;
- replacement of a valid immutable entry.

TOCTOU risk cannot be eliminated completely, so safety checks must be repeated immediately before each mutation.

---

## 17. Proposed model boundaries

The future implementation should use separate models for:

- `CacheNamespace`
- `CacheEntryVersion`
- `CacheEntryMetadata`
- `PayloadManifest`
- `PayloadManifestRecord`
- `CompletenessMarker`
- `CacheEntryPaths`
- `CacheLookupExpectation`
- `CacheLookupResult`
- `CacheLookupStatus`
- `CacheEntryValidationResult`
- `ProducerLockMetadata`
- `StagingEntryState`

Do not reuse `InventoryRecord` or `InventoryReport` as any of these models.

---

## 18. Recommended implementation phases after design approval

### Step 5A — Pure contracts

- versioned data models;
- validation rules;
- deterministic serialization;
- path derivation from validated `CacheKey`;
- no filesystem mutation.

### Step 5B — Read-only validator and lookup

- parse final entries;
- verify metadata, manifest, marker, and payload digests;
- return explicit lookup states;
- still no write path.

### Step 5C — Staging writer

- write only to staging;
- no promotion yet;
- fault-injection tests.

### Step 5D — Locking and atomic promotion

- producer lock;
- immediate pre-mutation safety revalidation;
- same-filesystem atomic rename;
- race handling.

### Step 5E — Read-only recovery inspection

- classify stale staging entries and locks;
- no cleanup execution.

---

## 19. Minimum test plan

### Contract tests

- version acceptance/rejection;
- deterministic JSON serialization;
- canonical digest derivation;
- namespace validation;
- unknown-field policy;
- metadata/namespace mismatch.

### Path tests

- valid digest sharding;
- traversal-like logical IDs have no path effect;
- Unicode and case edge cases;
- absolute and relative path rejection;
- root containment;
- same-filesystem requirement.

### Manifest tests

- duplicate path rejection;
- unsorted manifest rejection or normalization policy;
- missing file;
- extra file;
- wrong size;
- wrong digest;
- special file;
- symlink;
- nested payloads.

### Completeness tests

- missing marker;
- malformed marker;
- wrong metadata digest;
- wrong manifest digest;
- marker written before payload completion simulation.

### Lookup tests

- valid hit;
- clean miss;
- unsupported version;
- producer mismatch;
- schema mismatch;
- runtime fingerprint mismatch;
- unsafe path;
- incomplete entry;
- corrupt payload;
- in-progress lock.

### Concurrency tests

- two producers for same key;
- winner already promoted;
- stale lock classification;
- lock owner mismatch;
- staging collision resistance;
- interrupted promotion simulation.

### Regression requirements

- all existing storage tests remain green;
- all full regression tests remain green;
- inventory and CLI remain read-only;
- no cleanup execution introduced.

---

## 20. Design decisions to lock before implementation

Recommended decisions:

1. Use SHA-256 for entry and payload digests in contract v1.
2. Use two-level `2/2` hexadecimal sharding.
3. Use immutable final entry directories.
4. Use JSON metadata, manifest, and completeness marker encoded as UTF-8.
5. Use a same-filesystem atomic directory rename for promotion.
6. Treat staging as invisible to lookup.
7. Never overwrite or merge a final entry.
8. Keep producer lock state outside final entries.
9. Keep persistent index versioning separate from cache entries and inventory.
10. Defer deletion, eviction, and automatic repair.

---

## 21. Acceptance criteria for Step 5 design

The design is accepted when reviewers agree that:

- all 12 Foundation Review conditions are explicitly satisfied;
- cache identity is independent of physical paths and runtime locations;
- only validated `CacheKey` values influence final entry paths;
- staging and final entry semantics are unambiguous;
- integrity and completeness are independently verifiable;
- concurrent producers cannot silently corrupt or merge entries;
- interrupted writes never become valid hits;
- lookup is strictly read-only;
- inventory and persistent index boundaries remain intact;
- no cleanup execution has been designed or implemented.

---

## 22. Codex handoff boundary

Do not ask Codex to implement the entire storage system in one pass.

The first Codex task after approval should be limited to **Step 5A — pure contracts and tests**, with explicit constraints:

- modify only new Step 5 contract modules and their tests;
- do not change locked foundation behavior unless a failing test proves a necessary compatibility change;
- introduce no filesystem mutation;
- introduce no cleanup logic;
- run the storage suite and full regression suite;
- stop and report any ambiguity instead of inventing behavior.
