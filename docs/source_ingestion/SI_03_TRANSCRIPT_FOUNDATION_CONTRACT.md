# SI-03 — Transcript Foundation Contract

> **Documentary Engine**
> **Contract:** SI-03 — Transcript Foundation Contract
> **Version:** 1.0.0
> **Status:** DESIGN LOCKED — IMPLEMENTATION NOT YET APPROVED
> **Scope:** Immutable transcript-domain representation only
> **Implementation authority:** NOT YET APPROVED
> **Repository authority:** Existing locked repository contracts, the approved prospective Core Architecture, locked SI-01, and locked SI-02 remain authoritative.

## 1. Purpose

SI-03 defines the immutable, provider-neutral transcript-domain Artifact that sits after validated SI-02 Evidence and before normalization into any later `NormalizedSourceDocument`.

SI-03 owns only:

- transcript-origin and completeness vocabularies;
- transcript-language representation and status;
- immutable transcript segments;
- exact transcript-to-SI-02 provenance linkage;
- the aggregate `TranscriptArtifact`;
- transcript identity, equality, hashing, canonical serialization, bounded diagnostics, resource limits, and cross-model invariants.

SI-03 defines representations, not transcript production behavior.

SI-03 does **not** own acquisition, provider clients, transcript selection policy, provider transcript parsing behavior, local transcription behavior, model inference, `LocalTranscriptProvider`, `SpeechToTextBackend`, operation envelopes, normalization, workspaces, files, cache lookup, storage publication, or orchestration.

## 2. Dependencies and precedence

- **SI03-DEP-001:** SI-03 adopts the approved Core Architecture prospectively.
- **SI03-DEP-002:** SI-03 imports `CanonicalSourceIdentity` and `SourceObservationIdentity` exactly from locked SI-01.
- **SI03-DEP-003:** SI-03 imports `EvidenceLanguageTag`, `ProviderTranscriptKind`, `ProviderTranscriptCandidate`, `ValidatedAudioEvidence`, and `AcquiredSourceEvidence` semantics exactly from locked SI-02.
- **SI03-DEP-004:** SI-03 MUST NOT reinterpret or recanonicalize SI-01 identities or SI-02 Evidence descriptors.
- **SI03-DEP-005:** Existing Step 5A–5E, H1, H2, Storage, Director, pipeline, renderer, and roadmap contracts remain unchanged.
- **SI03-DEP-006:** SI-03 MUST NOT modify storage `ArtifactDescriptor`, `ArtifactType`, `CacheNamespace`, storage exports, or persistence authority. A `TranscriptArtifact` is an architectural Artifact, not a storage descriptor.
- **SI03-DEP-007:** If SI-03 conflicts with a locked dependency, the locked contract prevails and SI-03 remains blocked pending revision.

## 3. Primitive conventions

SI-03 reuses SI-01 `SchemaVersion`, `Utf8Text[N]`, `AsciiText[N]`, `UInt`, `PositiveInt`, and `Optional[T]`, and SI-02 `Boolean`, `Millis`, `DigestSha256`, `LogicalComponentId`, `ComponentVersion`, `Tuple[T,N]`, and `NonEmptyTuple[T,N]` without modification.

Additional SI-03 primitive:

| Type | Definition |
|---|---|
| `SegmentText[N]` | NFC Unicode text whose UTF-8 encoding is 1..`N` bytes after the exact normalization in section 7 |

All Unicode MUST be NFC before validation, byte accounting, equality, hashing, ordering, identity derivation, and serialization. Booleans are never integers. Floats and JSON `null` are prohibited in all SI-03 public models.

## 4. Closed vocabularies

### 4.1 `TranscriptOrigin`

- `PROVIDER_SUPPLIED`
- `LOCAL_STT`

`PROVIDER_SUPPLIED` means the chosen transcript was derived from exactly one SI-02 `ProviderTranscriptCandidate`. `LOCAL_STT` means the chosen transcript was derived from exactly one SI-02 `ValidatedAudioEvidence` observation through a separately authorized future local-transcript capability.

Origin records provenance; it does not authorize parsing, selection, inference, acquisition, or replay acquisition.

### 4.2 `TranscriptLanguageStatus`

- `DECLARED`
- `DETECTED`
- `UNKNOWN`

`DECLARED` preserves a language assertion supplied by the selected Evidence or an authorized caller. `DETECTED` preserves the output of a separately authorized language detector or transcript producer. `UNKNOWN` means no supported language assertion is available. SI-03 performs no detection, translation, negotiation, or fallback.

### 4.3 `TranscriptCompleteness`

- `COMPLETE`
- `PARTIAL`
- `UNKNOWN`

Completeness is a bounded producer assertion about the represented transcript, not source availability and not a universal claim. `COMPLETE` MUST NOT be inferred solely from successful parsing, the existence of segments, an ending timestamp, or the absence of errors. `UNKNOWN` MUST be used when completeness was not established.

### 4.4 Diagnostic vocabularies

`TranscriptDiagnosticSubject`:

- `TRANSCRIPT`
- `LANGUAGE`
- `SEGMENT`

`TranscriptDiagnosticSeverity`:

- `NON_FATAL`
- `INFORMATIONAL`

`TranscriptDiagnosticCode`:

- `TRANSCRIPT_PARTIAL`
- `TRANSCRIPT_COMPLETENESS_UNKNOWN`
- `LANGUAGE_UNKNOWN`
- `TIMESTAMPS_PARTIAL`
- `TIMESTAMPS_ABSENT`
- `SPEAKERS_PARTIAL`
- `SPEAKERS_ABSENT`

Unknown enum values MUST be rejected.

## 5. `TranscriptLanguage`

Exact fields:

| Field | Type | Required |
|---|---|---:|
| `schema_version` | `SchemaVersion` | yes, MUST equal 1 |
| `status` | `TranscriptLanguageStatus` | yes |
| `language_tag` | SI-02 `EvidenceLanguageTag` | yes |

Invariants:

- `UNKNOWN` requires `language_tag == "und"`.
- `DECLARED` and `DETECTED` require a tag other than `und`.
- Provider candidate language hints are not silently promoted from `und` to a known language.
- A local producer may report `DETECTED` only when a separately authorized capability supplied the assertion.

Maximum canonical serialized size gate: **256 bytes**. The exact limit is a pre-parse gate; subsequent validation still applies and no valid fixture is required to reach the exact limit.

## 6. `TranscriptSegment`

Exact fields:

| Field | Type | Required |
|---|---|---:|
| `schema_version` | `SchemaVersion` | yes, MUST equal 1 |
| `ordinal` | `UInt` | yes, 0..19,999 |
| `text` | `SegmentText[16384]` | yes |
| `start_ms` | `Optional[Millis]` | no, if present 0..7,200,000 |
| `end_ms` | `Optional[Millis]` | no, if present 0..7,200,000 |
| `speaker_label` | `Optional[Utf8Text[256]]` | no |

Timestamp invariants:

- `start_ms` and `end_ms` MUST either both be present or both be absent.
- When present, `end_ms >= start_ms`; zero-duration segments are representable only when explicitly observed or produced.
- Missing timestamps remain absent. They MUST NOT be estimated from text length, neighboring segments, media duration, frame rate, or wall-clock state.
- Milliseconds are non-negative integers. Float, decimal-string, frame, sample, and wall-clock timestamp representations are invalid.

Speaker invariants:

- `speaker_label` is an opaque displayed label, not a person identity.
- Empty labels are invalid.
- Producers MUST NOT introduce credentials, contact data, biometric identity, or inferred real-world identity into `speaker_label`. The SI-03 parser validates only the declared text, normalization, control-character, emptiness, and byte-bound rules and MUST NOT perform heuristic secret, contact-data, biometric, or identity detection. Explicitly supplied opaque labels may be preserved without interpreting them as real-world identity.
- Missing speakers remain absent. SI-03 performs no diarization, speaker merging, speaker renaming, or identity inference.

Maximum canonical serialized size gate: **20 KiB**. One byte over is rejected before full parsing.

## 7. Transcript text normalization

Model construction normalizes supplied segment text in exactly this order:

1. validate input as Unicode text;
2. replace each CRLF sequence with LF;
3. replace every remaining CR with LF;
4. normalize Unicode to NFC;
5. reject U+0000 and ASCII control characters U+0001..U+0008, U+000B, U+000C, and U+000E..U+001F;
6. preserve TAB and LF exactly;
7. validate the 1..16,384 UTF-8-byte bound.

SI-03 MUST NOT trim leading or trailing whitespace, collapse whitespace, rewrite punctuation, alter casing, insert punctuation, remove disfluencies, censor text, translate, spell-correct, join words, split words, or fabricate inaudible content.

Canonical serialized input MUST already contain LF-only, NFC text and is rejected rather than rewritten when non-canonical. Transcript-wide plain text, when a consumer explicitly needs it, is deterministically projected by joining segment `text` values with one LF in ordinal order. This is a lossy convenience projection with respect to segment-boundary recoverability; the canonical `TranscriptArtifact` retains the exact segment boundaries and ordinal structure. The projection is not an additional public field and does not authorize normalization into a later document model.

## 8. `TranscriptProvenanceLink`

One immutable link from a `TranscriptArtifact` to the exact locked SI-02 Evidence Artifact and component descriptor used to produce it.

Exact fields:

| Field | Type | Required |
|---|---|---:|
| `schema_version` | `SchemaVersion` | yes, MUST equal 1 |
| `origin` | `TranscriptOrigin` | yes |
| `source_evidence_artifact_identity` | `DigestSha256` | yes |
| `source_evidence_aggregate_provenance_ref` | `DigestSha256` | yes |
| `provider_candidate_evidence_digest` | `Optional[DigestSha256]` | origin-specific |
| `provider_candidate_provenance_ref` | `Optional[DigestSha256]` | origin-specific |
| `audio_content_digest` | `Optional[DigestSha256]` | origin-specific |
| `audio_provenance_ref` | `Optional[DigestSha256]` | origin-specific |
| `local_runtime_fingerprint` | `Optional[DigestSha256]` | origin-specific |

For `PROVIDER_SUPPLIED`:

- both provider-candidate fields are required;
- all audio and local-runtime fields are absent;
- `source_evidence_artifact_identity` MUST equal the supplied SI-02 Artifact identity;
- the candidate digest MUST resolve to exactly one candidate in that Artifact;
- the candidate provenance reference MUST equal that candidate's `provenance_ref` and resolve under SI-02 rules.

For `LOCAL_STT`:

- `audio_content_digest`, `audio_provenance_ref`, and `local_runtime_fingerprint` are required;
- both provider-candidate fields are absent;
- `source_evidence_artifact_identity` MUST equal the supplied SI-02 Artifact identity;
- the audio digest and provenance reference MUST equal those of the Artifact's `ValidatedAudioEvidence` and resolve under SI-02 rules.

For both origins, `source_evidence_aggregate_provenance_ref` MUST equal the referenced SI-02 Artifact's `aggregate_provenance_ref`.

Originating construction or later explicit association with a supplied SI-02 Artifact MUST perform every cross-Artifact resolution above. Standalone canonical parsing of an already-preserved `TranscriptArtifact` validates the link's intrinsic field grammar, origin-specific presence matrix, and inclusion in Artifact identity, but does not require the referenced SI-02 Artifact to be loaded. It MUST NOT perform lookup or reacquisition to resolve the link. A caller claiming a live association with separately supplied SI-02 Evidence MUST revalidate the association before using that Evidence.

`local_runtime_fingerprint` is only an opaque digest slot in SI-03. Its exact preimage, backend/model/runtime fields, privacy rules, and production requirements belong to a future Local Transcript implementation contract. SI-03 neither constructs nor interprets it.

Maximum canonical serialized size gate: **2 KiB**. One byte over is rejected before full parsing.

## 9. `TranscriptDiagnostic`

Exact fields:

| Field | Type | Required |
|---|---|---:|
| `schema_version` | `SchemaVersion` | yes, MUST equal 1 |
| `subject` | `TranscriptDiagnosticSubject` | yes |
| `severity` | `TranscriptDiagnosticSeverity` | yes |
| `code` | `TranscriptDiagnosticCode` | yes |
| `ordinal` | `UInt` | yes, 0..255 |
| `segment_ordinal` | `Optional[UInt]` | no, if present 0..19,999 |

Diagnostics contain no free-form public message. Invalid construction or parsing is a typed validation failure and MUST NOT be embedded as a diagnostic.

Rules:

- Subject `SEGMENT` requires `segment_ordinal` resolving to exactly one segment; every other subject requires it absent.
- `TRANSCRIPT_PARTIAL` requires subject `TRANSCRIPT`, completeness `PARTIAL`, and absent `segment_ordinal`.
- `TRANSCRIPT_COMPLETENESS_UNKNOWN` requires subject `TRANSCRIPT`, completeness `UNKNOWN`, and absent `segment_ordinal`.
- `LANGUAGE_UNKNOWN` requires subject `LANGUAGE`, `language.status == UNKNOWN`, and absent `segment_ordinal`.
- `TIMESTAMPS_PARTIAL` requires subject `TRANSCRIPT`, at least one timed segment, at least one untimed segment, and absent `segment_ordinal`.
- `SPEAKERS_PARTIAL` requires subject `TRANSCRIPT`, at least one segment with `speaker_label`, at least one segment without `speaker_label`, and absent `segment_ordinal`.
- `TIMESTAMPS_ABSENT` permits exactly either: subject `TRANSCRIPT` when every segment is untimed and `segment_ordinal` is absent; or subject `SEGMENT` when `segment_ordinal` resolves to exactly one untimed segment.
- `SPEAKERS_ABSENT` permits exactly either: subject `TRANSCRIPT` when every segment's `speaker_label` is absent and `segment_ordinal` is absent; or subject `SEGMENT` when `segment_ordinal` resolves to exactly one segment whose `speaker_label` is absent.
- Every subject/code combination not explicitly permitted above is invalid.

SI-02 replay acquisition method remains owned and represented by SI-02 provenance. SI-03 does not duplicate that observation as a transcript diagnostic.

Diagnostic uniqueness key is `(subject, severity, code, segment_ordinal-or-absent)`. Duplicate keys are invalid. Canonical order is ordinal ascending, then subject declaration order, severity declaration order, code declaration order, then absent segment ordinal before integer segment ordinal. Serialized input in another order is non-canonical.

Maximum canonical serialized size gate per diagnostic: **512 bytes**.

## 10. `TranscriptArtifact`

Exact fields:

| Field | Type | Required |
|---|---|---:|
| `schema_version` | `SchemaVersion` | yes, MUST equal 1 |
| `artifact_identity` | `DigestSha256` | yes |
| `source_identity` | SI-01 `CanonicalSourceIdentity` | yes |
| `observation_identity` | SI-01 `SourceObservationIdentity` | yes |
| `origin` | `TranscriptOrigin` | yes |
| `language` | `TranscriptLanguage` | yes |
| `completeness` | `TranscriptCompleteness` | yes |
| `producer_id` | `LogicalComponentId` | yes |
| `producer_version` | `ComponentVersion` | yes |
| `provenance_link` | `TranscriptProvenanceLink` | yes |
| `segments` | `NonEmptyTuple[TranscriptSegment, 20000]` | yes |
| `diagnostics` | `Tuple[TranscriptDiagnostic, 256]` | yes |

The Artifact contains exactly one chosen transcript. The existence of a `TranscriptArtifact` says nothing about whether alternative candidates or local generation paths existed. Selection policy is outside SI-03.

Maximum canonical serialized size gate: **16 MiB**. Maximum aggregate canonical segment bytes: **12 MiB**. Maximum aggregate normalized segment-text bytes: **8 MiB**. Maximum aggregate canonical diagnostic bytes: **128 KiB**. These limits are independently enforced.

## 11. Cross-model invariants

### 11.1 Identity agreement

- `observation_identity.source_identity` MUST equal top-level `source_identity`.
- The SI-02 Artifact supplied for construction validation MUST contain identities equal to the top-level identities.
- Identity mismatch is invalid, not diagnostic.

### 11.2 Origin agreement

- Top-level `origin` MUST equal `provenance_link.origin`.
- Origin-specific provenance fields MUST satisfy the exact presence matrix in section 8.
- Provider and local Evidence references MUST never coexist in one link.

### 11.3 Segment ordering

- Segment tuple order is semantic and canonical.
- Ordinals MUST be contiguous, unique, and exactly `0..len(segments)-1` in tuple order.
- Among timed segments, `start_ms` MUST be nondecreasing in tuple order. Equal starts preserve ordinal order.
- Overlap and equal time spans are permitted only as explicitly represented; SI-03 does not repair them.
- Untimed segments may occur anywhere. Their position is preserved by ordinal and no timestamp is inferred.

### 11.4 No fabrication

No SI-03 constructor, parser, or canonicalizer may invent text, timestamps, language, speaker labels, completeness, source identities, Evidence references, or runtime fingerprints. Unknown or absent information remains explicitly unknown or absent under the applicable field rules.

## 12. Artifact identity

`artifact_identity` MUST equal SHA-256, algorithm-qualified as `sha256:`, over canonical JSON serialization of exactly this semantic preimage:

| Field | Exact value |
|---|---|
| `identity_schema_version` | integer `1` |
| `source_identity` | canonical top-level object |
| `observation_identity` | canonical top-level object |
| `origin` | top-level enum value |
| `language` | canonical language object |
| `completeness` | top-level enum value |
| `producer_id` | top-level value |
| `producer_version` | top-level value |
| `provenance_link` | canonical provenance-link object |
| `segments` | canonical segment tuple in ordinal order |

No other field is included. Diagnostics are excluded from Artifact identity but remain part of structural equality and hashing. The preimage contains no wall-clock time, path, machine identity, storage descriptor, cache identity, or implicit backend state.

## 13. Equality and hashing

Every SI-03 immutable public model is structurally equal over all canonical public fields. Hashing uses the same fields as equality. Nested tuples participate by canonical tuple value.

Two `TranscriptArtifact` values differing only in diagnostics are structurally unequal but intentionally share `artifact_identity` when every semantic-preimage field is equal.

## 14. Canonical serialization and parsing

SI-03 adopts locked SI-01, SI-02, and Step 5 canonical JSON rules exactly:

- strict UTF-8 without BOM;
- direct UTF-8 for non-ASCII text;
- NFC before validation and byte accounting;
- lexicographically sorted object keys;
- compact `,` and `:` separators;
- no insignificant whitespace, trailing newline, or trailing bytes;
- absent optional fields omitted and JSON `null` rejected;
- exact uppercase enum names;
- duplicate and unknown keys rejected;
- unsupported schema versions distinguished from malformed data;
- floats and non-finite values prohibited;
- booleans rejected where integers are required;
- persistent/public serialized input equal to canonical reserialization byte-for-byte.

The applicable per-model byte gate MUST be enforced before full parsing. Exact-limit input passes that gate subject to later validation. One byte over is rejected before full parsing. No valid fixture is required to reach an otherwise unreachable exact-limit size. Nested and aggregate limits are independently enforced.

## 15. Resource limits

| Resource | Maximum |
|---|---:|
| language canonical bytes | 256 bytes |
| segments per Artifact | 20,000 |
| segment text | 16 KiB each |
| segment canonical bytes | 20 KiB each |
| aggregate normalized segment text | 8 MiB |
| aggregate canonical segment bytes | 12 MiB |
| timestamp | 7,200,000 ms |
| speaker label | 256 UTF-8 bytes |
| provenance-link canonical bytes | 2 KiB |
| diagnostics | 256 |
| diagnostic canonical bytes | 512 bytes each |
| aggregate diagnostic canonical bytes | 128 KiB |
| Transcript Artifact canonical bytes | 16 MiB |

These are representation and validation limits only. They grant no authority to acquire, read, write, parse provider bytes, invoke inference, retry, publish, or delete anything.

## 16. Replay and local-STT determinism

A preserved canonical `TranscriptArtifact` MUST be parseable, validated, compared, hashed, and projected to transcript-wide plain text with exactly zero network access and zero filesystem access.

Reconstructing a provider-origin Artifact from candidate Evidence requires separately supplied immutable bytes whose digest and byte length have already been revalidated against the referenced SI-02 candidate. Missing bytes MUST NOT trigger provider reacquisition.

Reconstructing a local-origin Artifact requires separately supplied audio bytes revalidated against SI-02 audio digest and byte length plus a separately authorized local-transcript capability. Missing audio MUST NOT trigger source reacquisition.

SI-03 canonicalization and validation are deterministic. Local model inference is not required to be byte-identical across arbitrary hardware, runtime, dependency, backend, or model revisions. Output-affecting backend/model/runtime details and the exact `local_runtime_fingerprint` derivation belong to the future Local Transcript implementation contract. Once produced, the exact immutable Artifact is deterministic to replay without rerunning inference.

## 17. Diagnostic and failure boundary

SI-03 diagnostics describe valid representable transcript observations only. Malformed input, unsupported versions, invalid ordering, invalid timestamps, identity mismatch, an intrinsically invalid provenance link, origin-field mismatch, invalid digest, duplicate diagnostics, or exceeded limits MUST fail construction/parsing. Failure to resolve or agree with separately supplied SI-02 Evidence MUST fail originating construction or explicit association validation. Standalone parsing does not perform external Evidence resolution.

Diagnostics do not replace operation status, parsing errors, acquisition failures, backend failures, selection results, or native exceptions. Diagnostic collection, truncation, overflow handling, and operation-result reporting remain outside SI-03.

## 18. Explicit authority exclusions

- No network, provider, redirect, authentication, or rate-limit authority.
- No filesystem, path, workspace, temporary-file, cleanup, or recovery authority.
- No `LocalTranscriptProvider` or `SpeechToTextBackend` behavior or interface is defined.
- No provider transcript parser behavior or format support is defined.
- No transcript selection, ranking, fallback, translation, or generation policy is defined.
- No normalization into `NormalizedSourceDocument` is defined.
- No request/result envelope, retry, cancellation, scheduling, or orchestration authority.
- No cache lookup, cache key, storage publication, `ArtifactType`, `CacheNamespace`, `ArtifactDescriptor`, H1, H2, or H3–H7 authority.
- No package-level export authority.
- No production YouTube acquisition authority.
- No Knowledge, Narrative, Presentation, Director, pipeline, or renderer authority.

## 19. Required contract tests after implementation approval

### 19.1 Models and vocabularies

- exact enum membership and unknown rejection;
- exact fields, immutability, schema versions, equality, and hashing;
- unknown/duplicate keys, floats, booleans-as-integers, `null`, BOM, malformed UTF-8, and non-canonical input rejected;
- every per-model, aggregate, count, text, and numeric gate, including exact-gate passage and one-over pre-parse rejection.

### 19.2 Text, timestamps, ordering, and speakers

- CRLF/CR-to-LF and NFC construction normalization;
- canonical serialized input rejects non-canonical line endings or normalization;
- forbidden controls rejected; TAB/LF and exact whitespace preserved;
- no trimming, punctuation, casing, spelling, translation, or content fabrication;
- timestamp pair presence, integer type, zero, maximum, one-over, and end-before-start rejection;
- contiguous ordinals, exact tuple order, nondecreasing timed starts, ties, overlaps, and mixed timed/untimed segments;
- missing timestamps and speakers remain absent;
- speaker byte bounds and Producer privacy obligations, with no heuristic privacy classification by the parser;
- deterministic transcript-wide LF projection and explicit loss of segment-boundary recoverability from the convenience projection only.

### 19.3 Language, completeness, and diagnostics

- exact language-status/tag matrix;
- completeness remains distinct from availability and is never inferred by SI-03;
- every explicitly permitted diagnostic subject/code/segment-reference/state combination accepted and every unlisted combination rejected;
- diagnostic uniqueness, canonical order, count and aggregate-byte limits;
- diagnostics excluded from Artifact identity but included in equality/hash;
- invalid models cannot be legalized by diagnostics.

### 19.4 SI-02 provenance linkage

- provider origin resolves exactly one candidate by Evidence digest and exact provenance reference;
- local origin resolves exact validated audio digest and provenance reference;
- source Evidence Artifact and aggregate provenance references agree exactly;
- origin-specific presence matrix and cross-origin-field rejection;
- SI-01 source/observation identity agreement;
- standalone parsing performs intrinsic provenance-link validation only and performs no external Evidence loading;
- cross-Artifact mismatch fails originating construction or explicit association validation;
- missing supplied Evidence never triggers reacquisition;
- runtime fingerprint is required for local origin but opaque to SI-03.

### 19.5 Artifact identity, replay, and boundaries

- exact semantic preimage and digest derivation;
- canonical round trips and structural hashing laws;
- preserved Artifact replay performs zero network, filesystem, provider, parser, STT, storage, cache, Director, pipeline, or renderer calls;
- local inference nondeterminism is not misrepresented as canonicalization nondeterminism;
- Step 5A–5E, SI-01, SI-02, H1, H2, storage, Director, pipeline, renderer, and full-suite regression.

## 20. Expected Lock Review questions

SI-03 draft.2 remains a draft until review confirms:

1. whether a nonempty ordered segment tuple is sufficient for every chosen provider and local transcript without a duplicate transcript-wide text field;
2. whether `TranscriptLanguage` and its exact declared/detected/unknown matrix preserve useful assertions without granting detection authority;
3. whether `COMPLETE`, `PARTIAL`, and `UNKNOWN` are mechanically useful without leaking operation or availability semantics;
4. whether timed/untimed mixing, zero-duration spans, overlap, and nondecreasing timed-start ordering are appropriate;
5. whether opaque speaker labels are sufficient and privacy-safe without speaker identity or diarization authority;
6. whether provider linkage by SI-02 Artifact identity, candidate Evidence digest, and candidate provenance reference is exact and replay-safe;
7. whether local linkage by SI-02 audio digest, provenance reference, and opaque runtime fingerprint is mechanically complete without defining STT behavior;
8. whether the Artifact identity preimage includes every semantic field, excludes diagnostics intentionally, and creates no hidden storage/cache identity;
9. whether all byte, count, timestamp, and aggregate limits are acceptable;
10. whether SI-03 diagnostics belong at representation scope and remain mechanical;
11. whether canonicalization is deterministic while local inference variability is framed correctly;
12. whether every authority exclusion preserves SI-01, SI-02, Step 5A–5E, H1/H2, Storage, Directors, pipeline, renderer, roadmap, and future SI-03 implementation boundaries.

## 21. Design-lock criterion

SI-03 may become:

`DESIGN LOCKED — IMPLEMENTATION NOT YET APPROVED`

only after repository-aware review resolves every mandatory issue in section 20 and confirms compatibility with Core Architecture, locked SI-01, locked SI-02, Step 5A–5E, H1, H2, Storage, Directors, pipeline, renderer, and the roadmap planning gate.

Locking SI-03 would not authorize implementation.
