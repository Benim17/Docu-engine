# SI-01 — Source Identity Contract

> **Documentary Engine**  
> **Contract:** SI-01 — Source Identity Contract  
> **Version:** 1.0.0  
> **Status:** DESIGN LOCKED — IMPLEMENTATION NOT YET APPROVED  
> **Scope:** Pure source/request identity only  
> **Implementation authority:** NOT YET APPROVED  
> **Repository authority:** Existing locked repository contracts remain authoritative until this contract is approved.

## 1. Purpose

SI-01 defines the immutable public identity language used by Source Ingestion before acquisition begins.

This contract owns only source-kind vocabulary, caller source references, canonical source identities, source observation identity value structure, equality/hash semantics, canonical serialization, and deterministic YouTube identity-only canonicalization.

SI-01 does not own concrete observation-scheme semantics, network acquisition, filesystem access, transcripts, normalization, operation envelopes, temporary workspaces, storage publication, cache keys, H1/H2 behavior, H3–H7 behavior, or production YouTube acquisition.

## 2. Dependency and adoption

- **SI01-DEP-001:** SI-01 adopts the approved Core Architecture prospectively.
- **SI01-DEP-002:** Existing Step 5A–5E, H1, H2, Storage, Director, pipeline, and renderer contracts remain unchanged.
- **SI01-DEP-003:** SI-01 MUST NOT modify or extend existing storage `ArtifactDescriptor`, `ArtifactType`, `CacheNamespace`, package exports, or persistence authority.

## 3. Primitive conventions

| Type | Definition |
|---|---|
| `SchemaVersion` | positive integer |
| `Utf8Text[N]` | NFC-normalized Unicode whose UTF-8 encoding is 1..N bytes |
| `AsciiText[N]` | ASCII string of 1..N bytes |
| `UInt` | integer >= 0; booleans invalid |
| `PositiveInt` | integer >= 1; booleans invalid |
| `Optional[T]` | field absent or exactly one T; JSON null is not used |

All public Unicode strings MUST be NFC before size validation, equality, hashing, and serialization.

Public parsers MUST reject malformed UTF-8, BOM, duplicate keys, unknown fields, unsupported schema versions, floats, booleans-as-integers, values outside bounds, and JSON null for optional absence.

Absent optional fields MUST be omitted from canonical JSON.

## 4. SourceKind

Closed v1 vocabulary:

- `YOUTUBE_VIDEO`
- `WEB_PAGE`
- `PDF_DOCUMENT`
- `TEXT`
- `AUDIO_FILE`
- `VIDEO_FILE`

Unknown values MUST be rejected. Shorts remain `YOUTUBE_VIDEO`.

## 5. SourceReference

Exact fields:

| Field | Type | Required |
|---|---|---:|
| `schema_version` | `SchemaVersion` | yes, MUST equal 1 |
| `source_kind` | `SourceKind` | yes |
| `reference_value` | `Utf8Text[4096]` | yes |
| `display_label` | `Optional[Utf8Text[512]]` | no |

Maximum canonical serialized size: **5,120 bytes**.

Exactly 5,120 bytes is accepted. 5,121 bytes or more MUST be rejected before full parsing.

Object equality/hash use all canonical public fields. `display_label` participates in object equality/hash but MUST NOT participate in canonical source identity, observation identity, Artifact identity, cache identity, or acquisition authority.

## 6. CanonicalSourceIdentity

Exact fields:

| Field | Type | Required |
|---|---|---:|
| `schema_version` | `SchemaVersion` | yes, MUST equal 1 |
| `source_kind` | `SourceKind` | yes |
| `identity_scheme` | `AsciiText[64]` | yes |
| `identity_scheme_version` | `PositiveInt` | yes |
| `canonical_value` | `AsciiText[512]` | yes |

Maximum canonical serialized size: **1,024 bytes**.

Exactly 1,024 bytes is accepted. 1,025 bytes or more MUST be rejected before full parsing.

Equality/hash use every public canonical field.

Canonical source identity MUST exclude credentials, cookies, query secrets, signed URLs, wall-clock timestamps, mutable display labels, temporary locations, machine-local/runtime hostnames, and process identifiers. This does not prohibit a source-origin hostname when a future source identity scheme such as `WEB_PAGE` requires it.

## 7. SourceObservationIdentity

SI-01 defines only the public value structure and **no concrete observation schemes**.

Exact fields:

| Field | Type | Required |
|---|---|---:|
| `schema_version` | `SchemaVersion` | yes, MUST equal 1 |
| `source_identity` | `CanonicalSourceIdentity` | yes |
| `observation_scheme` | `AsciiText[64]` | yes |
| `observation_scheme_version` | `PositiveInt` | yes |
| `observation_value` | `AsciiText[256]` | yes |

Maximum canonical serialized size: **1,536 bytes**.

Exactly 1,536 bytes is accepted. 1,537 bytes or more MUST be rejected before full parsing.

Equality/hash use every canonical public field.

Concrete observation-scheme semantics, including digest-backed schemes, MUST be defined by a later scheme-owning contract. SI-01 performs no scheme-specific `observation_value` validation.

Wall-clock acquisition time MUST NOT become observation identity authority merely because it is representable as text.

## 8. Equality/hash laws

Every SI-01 immutable public model is structurally equal using all canonical public fields unless explicitly stated otherwise. No exception exists in SI-01 v1.

Hashing MUST use the same canonical fields as equality.

Two equal values MUST serialize to byte-identical canonical JSON.

## 9. Canonical serialization

SI-01 adopts the canonical JSON encoding rules of the locked Step 5 contract exactly, except where SI-01 explicitly imposes a stricter rule.

Canonical JSON MUST:

- use strict UTF-8;
- contain no BOM;
- emit valid non-ASCII text directly as UTF-8 (`ensure_ascii=false` semantics);
- sort object keys lexicographically;
- use compact separators `,` and `:`;
- contain no insignificant whitespace;
- contain no trailing newline or trailing bytes;
- omit absent optional fields;
- reject JSON `null` for optional absence;
- reject duplicate keys and unknown fields;
- distinguish unsupported schema versions from malformed data;
- normalize Unicode to NFC before byte-limit enforcement;
- prohibit floats;
- require serialized input to equal canonical reserialization byte-for-byte when serialized public input is accepted.

Per-model serialized byte limits MUST be enforced before full parsing.

## 10. YouTube identity-only scheme

Identity scheme:

- `source_kind = YOUTUBE_VIDEO`
- `identity_scheme = youtube-video-id`
- `identity_scheme_version = 1`
- `canonical_value = validated video ID`

Video ID grammar exactly:

`[A-Za-z0-9_-]{11}`

Canonicalization performs exactly zero network operations.

### 10.1 URI scheme, authority, host, port

Only ASCII case-insensitive `https` is accepted.

User info MUST be absent.

Port acceptance is lexical: absent or exact raw port text `443`. `0443` and every other explicit port are rejected.

Host normalization:

1. ASCII only;
2. ASCII lowercase before comparison;
3. trailing DNS dot rejected;
4. IDNA/punycode alternatives rejected.

Accepted hosts exactly:

- `youtube.com`
- `www.youtube.com`
- `youtu.be`

### 10.2 Raw query tokenization

The canonicalizer MUST NOT rely on form-urlencoded parsing.

Algorithm:

1. Split raw query only on literal ASCII `&`.
2. If raw query is non-empty, every member MUST be non-empty.
3. Every member MUST contain exactly one literal ASCII `=`.
4. Split at that literal `=` into raw key and raw value.
5. Raw key MUST be non-empty.
6. Before decoding, reject any percent escape representing `&`, `=`, `%`, `#`, `?`, `/`, or backslash.
7. Percent-decode raw key and raw value exactly once.
8. Malformed percent escapes are rejected.
9. `+` is literal plus and is never converted to space.
10. Decoded key MUST be non-empty.
11. Duplicate detection happens after decoding.
12. Closed key/value rules are then applied.

Empty query members, keys without `=`, values with extra raw `=`, and empty keys are rejected.

### 10.3 Percent-encoded unreserved characters

- Query keys: percent-encoded unreserved characters are rejected. `%76` is not accepted as `v`.
- Video-ID values: percent-encoded video-ID characters are rejected.
- Ignored query values: percent-encoded unreserved characters MAY be decoded exactly once if the final decoded value satisfies that key's grammar.

### 10.4 Paths

For `youtube.com` / `www.youtube.com`:

- `/watch`
- `/shorts/<ID>`

For `youtu.be`:

- `/<ID>`

No trailing slash after ID. Repeated slashes, empty extra segments, and extra path segments are rejected.

`/channel`, `/playlist`, `/embed`, `/live`, and unrelated paths are rejected.

Percent-encoded structural delimiters in path segments are rejected. Percent-encoded video-ID characters are rejected.

### 10.5 `/watch` query rules

Exactly one decoded key `v` is required.

Its raw/decoded value MUST directly satisfy the 11-character ID grammar.

Duplicate `v`, blank `v`, or extra `=` content is invalid.

Closed ignored key allowlist:

- `t`
- `start`
- `si`
- `feature`
- `list`
- `index`

Unknown decoded keys are rejected.

Duplicate ignored keys are rejected.

Blank decoded ignored values are rejected.

### 10.6 `youtu.be` and `/shorts` query rules

The same ignored-key allowlist applies.

Decoded `v` is forbidden.

Unknown, duplicate, or blank ignored values are rejected.

### 10.7 Time query grammar

Decoded `t` / `start` values must match:

`[0-9]{1,8}`

Numeric maximum: **86,400,000**.

No suffixes are accepted in query values.

Time values never affect identity.

### 10.8 Other ignored values

Decoded `si`, `feature`, `list`, and `index` values MUST be 1..512 UTF-8 bytes after decoding and NFC normalization.

They are ignored for identity and never retained in `CanonicalSourceIdentity`.

### 10.9 Fragment grammar

Fragment may be absent or, after one strict decode pass, exactly:

- `t=<decimal>`
- `t=<decimal>s`

`<decimal>` must match `[0-9]{1,8}` and numeric value <= 86,400,000.

Encoded structural delimiters are rejected before decoding.

Any other fragment is rejected.

Query time and fragment time MAY both exist and need not agree because neither affects identity.

### 10.10 Playlist context

`list` and `index` never change canonical video identity, never authorize playlist ingestion, and never survive in canonical identity.

### 10.11 Output

Every accepted YouTube reference produces exactly:

```text
CanonicalSourceIdentity(
  schema_version=1,
  source_kind=YOUTUBE_VIDEO,
  identity_scheme="youtube-video-id",
  identity_scheme_version=1,
  canonical_value=<validated video id>
)
```

## 11. Invariants

- SI01-INV-001: identity domains MUST NOT be conflated.
- SI01-INV-002: caller spelling MUST NOT become canonical identity.
- SI01-INV-003: equivalent accepted YouTube forms MUST canonicalize identically.
- SI01-INV-004: identity derivation performs zero network operations.
- SI01-INV-005: runtime machine identity and wall-clock state MUST NOT influence identity.
- SI01-INV-006: unknown query data is rejected.
- SI01-INV-007: one accepted YouTube reference identifies exactly one video.
- SI01-INV-008: playlist context never changes individual-video identity.
- SI01-INV-009: semantic identity-scheme change requires a new scheme version.
- SI01-INV-010: SI-01 defines no concrete observation-scheme semantics.

## 12. Required tests

Mandatory tests include:

- every SourceKind member and unknown rejection;
- SourceReference equality/hash and display-label semantic exclusion;
- exact 5,120/5,121 serialized SourceReference boundary;
- CanonicalSourceIdentity equality/hash and 1,024/1,025 boundary;
- SourceObservationIdentity equality/hash and 1,536/1,537 boundary;
- no digest-backed scheme test in SI-01;
- UTF-8, no BOM, sorted keys, compact separators, no trailing newline;
- direct UTF-8 non-ASCII, non-canonical escaped form rejection;
- omitted optional field versus `null`;
- duplicate/unknown key rejection;
- NFC canonicalization;
- exact raw query tokenization edge cases;
- decoded-key duplicate collisions;
- percent-encoded key rejection;
- percent-encoded ignored-value handling;
- percent-encoded ID rejection;
- `+` literal behavior;
- lexical `443` acceptance and `0443` rejection;
- exact host/path/query/fragment rules;
- exact 11-character ID acceptance and 10/12 rejection;
- time bound exact max and one-over;
- zero filesystem, network, cache/storage, STT, H3–H7, Knowledge, Narrative, or Presentation authority.

## 13. Design-lock criteria

SI-01 may become:

`DESIGN LOCKED — IMPLEMENTATION NOT YET APPROVED`

only if repository review confirms:

1. per-model canonical byte limits are acceptable;
2. canonical JSON aligns exactly with locked Step 5 authority except explicit stricter rules;
3. concrete observation-scheme semantics may be deferred;
4. raw YouTube query parsing is deterministic;
5. future source-origin hostname identity remains possible;
6. no storage/public-export change is implied;
7. no unresolved SI-01 public type references remain.

Locking SI-01 does not authorize implementation.
