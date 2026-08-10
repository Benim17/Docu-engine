# SI-02 — Source Evidence Models Contract

> **Documentary Engine**
> **Contract:** SI-02 — Source Evidence Models Contract
> **Version:** 1.0.0
> **Status:** DESIGN LOCKED — IMPLEMENTATION NOT YET APPROVED
> **Scope:** Immutable acquired-source Evidence models only
> **Implementation authority:** NOT YET APPROVED
> **Repository authority:** Existing locked repository contracts, the approved prospective Core Architecture, and locked SI-01 remain authoritative.

## 1. Purpose

SI-02 defines the immutable, provider-neutral public Evidence models produced after a source has been identified under SI-01 and observed through a separately authorized acquisition boundary.

SI-02 owns only:

- component-availability vocabulary;
- bounded provider-neutral metadata projections;
- provider transcript-candidate observations;
- validated audio-evidence descriptions;
- acquisition provenance records;
- the aggregate `AcquiredSourceEvidence` Evidence Artifact;
- evidence-model equality, hashing, canonical serialization, bounded diagnostics, resource limits, and cross-model invariants.

SI-02 defines representations, not acquisition behavior.

SI-02 does **not** own:

- network requests, redirects, authentication, rate limits, or provider clients;
- source canonicalization or observation-scheme semantics owned by SI-01 or later scheme-owning contracts;
- transcript selection, parsing, generation, translation, or transcription;
- `LocalTranscriptProvider` or `SpeechToTextBackend`;
- normalization into `NormalizedSourceDocument`;
- operation request/result envelopes, scheduling, retry, cancellation, or orchestration;
- filesystem paths, temporary workspaces, cleanup, or recovery;
- cache lookup, cache keys, storage publication, `ArtifactType`, `CacheNamespace`, H1, H2, or H3–H7;
- production YouTube acquisition authority;
- Knowledge, Narrative, Presentation, rendering, Directors, or pipeline migration.

## 2. Dependencies and precedence

- **SI02-DEP-001:** SI-02 adopts the approved Core Architecture prospectively.
- **SI02-DEP-002:** SI-02 imports `SourceKind`, `CanonicalSourceIdentity`, and `SourceObservationIdentity` exactly from locked SI-01.
- **SI02-DEP-003:** SI-02 MUST NOT reinterpret, recanonicalize, or derive SI-01 identities.
- **SI02-DEP-004:** Existing Step 5A–5E, H1, H2, Storage, Director, pipeline, and renderer contracts remain unchanged.
- **SI02-DEP-005:** SI-02 MUST NOT modify or extend existing storage `ArtifactDescriptor`, `ArtifactType`, `CacheNamespace`, storage package exports, or persistence authority.
- **SI02-DEP-006:** If this draft conflicts with locked SI-01 or an existing locked repository contract, the locked contract prevails and SI-02 remains blocked pending revision.

## 3. Primitive conventions

SI-02 reuses SI-01 `SchemaVersion`, `Utf8Text[N]`, `AsciiText[N]`, `UInt`, `PositiveInt`, and `Optional[T]` without modification.

Additional SI-02 primitives:

| Type | Definition |
|---|---|
| `Boolean` | exactly JSON `true` or `false` |
| `Millis` | `UInt` milliseconds |
| `ByteCount` | `UInt` bytes |
| `DigestSha256` | ASCII `sha256:` followed by exactly 64 lowercase hexadecimal digits |
| `Tuple[T, N]` | immutable ordered tuple containing at most `N` values of `T` |
| `NonEmptyTuple[T, N]` | immutable ordered tuple containing 1..`N` values of `T` |
| `OpaqueCandidateId` | ASCII matching `[A-Za-z0-9._~-]{1,256}` |
| `LogicalComponentId` | lowercase ASCII matching `[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?` |
| `ComponentVersion` | ASCII matching `[A-Za-z0-9](?:[A-Za-z0-9._+~-]{0,62}[A-Za-z0-9])?` |
| `KiB` | exactly 1,024 bytes |
| `MiB` | exactly 1,048,576 bytes |

All Unicode text MUST be NFC before validation, byte accounting, equality, hashing, ordering, and serialization.

Booleans are never valid integers. Floats and JSON `null` are prohibited in SI-02 public models.

## 4. Closed vocabularies

### 4.1 `SourceComponentAvailability`

Closed v1 values:

- `AVAILABLE`
- `UNAVAILABLE`
- `UNKNOWN`
- `NOT_REQUESTED`

Meanings:

- `AVAILABLE`: the component was observed and its corresponding validated public value is present.
- `UNAVAILABLE`: the authorized observation established that no usable component was available.
- `UNKNOWN`: the observation could not establish availability without asserting absence.
- `NOT_REQUESTED`: the acquisition policy did not request the component.

Availability is an observation, not a statement of universal or permanent provider state.

### 4.2 `ProviderTranscriptKind`

Closed v1 values:

- `MANUAL`
- `AUTOMATIC`
- `UNKNOWN`

`UNKNOWN` MUST be used rather than inferring a transcript kind.

### 4.3 `AudioContainer`

Closed v1 values:

- `WAV`
- `FLAC`
- `MP3`
- `M4A`
- `OGG`
- `WEBM`
- `OTHER`

`OTHER` preserves an explicitly observed container outside the closed named set. It does not authorize a decoder, imply codec support, or permit the implementation to invent a container label. When the exact codec is observed independently, `codec_label` MAY preserve it; otherwise `codec_label` remains absent.

### 4.4 `SourceAcquisitionMethod`

Closed v1 values:

- `PROVIDER_API`
- `PROVIDER_PAGE`
- `USER_SUPPLIED`
- `LOCAL_FILE`
- `REPLAY`

These values describe provenance only. They grant no network or filesystem authority.

### 4.5 `ProviderTranscriptEvidenceFormat`

Closed v1 values:

- `PLAIN_TEXT`
- `WEBVTT`
- `SRT`
- `TTML`
- `JSON`
- `OTHER`

The format is an observed descriptor. It does not authorize parsing, imply support, select a transcript, or convert the evidence into a `TranscriptArtifact`. `OTHER` preserves an explicitly observed format outside the named set.

### 4.6 `SourceAcquisitionProvenanceRole`

Closed v1 values:

- `AGGREGATE`
- `METADATA`
- `PROVIDER_TRANSCRIPT_CANDIDATE`
- `AUDIO`

The role identifies the Evidence contribution described by one provenance record. It grants no acquisition or processing authority.

### 4.7 Diagnostic vocabularies

`SourceEvidenceDiagnosticSubject`:

- `EVIDENCE`
- `METADATA`
- `PROVIDER_TRANSCRIPT_CANDIDATE`
- `AUDIO_EVIDENCE`
- `PROVENANCE`

`SourceEvidenceDiagnosticSeverity`:

- `NON_FATAL`
- `INFORMATIONAL`

`SourceEvidenceDiagnosticCode`:

- `COMPONENT_AVAILABLE`
- `COMPONENT_UNAVAILABLE`
- `COMPONENT_UNKNOWN`
- `COMPONENT_NOT_REQUESTED`
- `EVIDENCE_PARTIAL`
- `METADATA_INCOMPLETE`
- `TRANSCRIPT_CANDIDATE_INCOMPLETE`
- `AUDIO_PROPERTIES_INCOMPLETE`
- `PROVENANCE_REPLAYED`

Unknown enum values MUST be rejected.

## 5. `EvidenceLanguageTag`

SI-02 needs bounded provider-declared language hints without owning transcript-language selection.

A canonical `EvidenceLanguageTag` is either `und` or:

- `language`;
- `language-Script`;
- `language-REGION`; or
- `language-Script-REGION`.

Where:

- `language` is 2..8 ASCII letters, lowercase;
- `Script` is exactly four ASCII letters, title case;
- `REGION` is exactly two ASCII letters uppercase or exactly three digits.

Maximum: **18 ASCII bytes**.

Extensions, variants, private-use subtags, aliases, empty subtags, repeated separators, and non-ASCII characters are rejected. `und` means explicitly unknown. Model construction canonicalizes otherwise-valid input to the casing above before equality and hashing. Canonical serialized input MUST already use canonical casing and is rejected rather than rewritten when its casing is non-canonical. SI-02 performs no language translation, negotiation, or fallback.

## 6. `SourceEvidenceMetadata`

Immutable bounded provider-neutral metadata projection from validated metadata Evidence.

Exact fields:

| Field | Type | Required |
|---|---|---:|
| `schema_version` | `SchemaVersion` | yes, MUST equal 1 |
| `evidence_digest` | `DigestSha256` | yes |
| `provenance_ref` | `DigestSha256` | yes |
| `title` | `Optional[Utf8Text[1024]]` | no |
| `creator_label` | `Optional[Utf8Text[512]]` | no |
| `creator_identity` | `Optional[AsciiText[512]]` | no |
| `published_at_ms` | `Optional[Millis]` | no |
| `duration_ms` | `Optional[Millis]` | no |
| `language_hint` | `Optional[EvidenceLanguageTag]` | no |
| `description_excerpt` | `Optional[Utf8Text[16384]]` | no |

`evidence_digest` identifies the exact validated metadata evidence from which the public fields were derived. At least one optional metadata value MUST be present. Empty strings are invalid. Popularity counters, recommendation scores, comments, and provider-specific opaque payloads are not fields.

Maximum canonical serialized size gate: **24 KiB**. The pre-parse gate permits exactly 24 KiB; one byte over is rejected before full parsing. Passing the size gate does not make otherwise-invalid content valid, and valid field combinations need not be able to reach every byte count below the gate.

## 7. `ProviderTranscriptCandidate`

A `ProviderTranscriptCandidate` records that a provider exposed one transcript candidate. It is not a generated `TranscriptArtifact`, does not select a candidate, and contains no transcript text.

Exact fields:

| Field | Type | Required |
|---|---|---:|
| `schema_version` | `SchemaVersion` | yes, MUST equal 1 |
| `source_identity` | SI-01 `CanonicalSourceIdentity` | yes |
| `observation_identity` | SI-01 `SourceObservationIdentity` | yes |
| `candidate_id` | `OpaqueCandidateId` | yes |
| `candidate_kind` | `ProviderTranscriptKind` | yes |
| `language_hint` | `EvidenceLanguageTag` | yes |
| `is_translatable` | `Boolean` | yes |
| `evidence_byte_length` | `PositiveInt` | yes, 1..16,777,216 |
| `evidence_format` | `ProviderTranscriptEvidenceFormat` | yes |
| `evidence_digest` | `DigestSha256` | yes |
| `provenance_ref` | `DigestSha256` | yes |

`candidate_id` is a bounded provider-scoped opaque identifier. The parser validates only the `OpaqueCandidateId` grammar. The Producer MUST NOT place credentials, cookies, authentication material, signed-URL material, or other secrets in `candidate_id`. This privacy obligation does not authorize heuristic secret detection by the parser. The identifier MUST NOT be interpreted outside the adapter contract that produced it.

`evidence_digest` and `evidence_byte_length` describe the exact preserved immutable provider transcript-candidate bytes from which this observation was derived. No locator, path, URI, storage reference, file descriptor, or native provider object is stored.

A later explicitly approved capability boundary MAY associate supplied immutable bytes with this descriptor only after revalidating both `evidence_digest` and `evidence_byte_length`. Missing bytes MUST NOT trigger implicit source or provider reacquisition. SI-02 does not define how bytes are acquired, retained, parsed, selected, or converted into transcript content.

Candidate canonical order is:

1. canonical `language_hint` ASCII bytes;
2. `candidate_kind` declaration order;
3. `candidate_id` ASCII bytes;
4. `evidence_format` declaration order;
5. `evidence_digest` ASCII bytes;
6. full canonical serialized candidate bytes as the final tie-breaker.

The final tie-breaker makes this a total deterministic order even when descriptors share all earlier ordering fields but differ in another canonical public field.

Two candidates within one `AcquiredSourceEvidence` MUST NOT share the same `(candidate_id, language_hint, candidate_kind)` tuple.

`ProviderTranscriptCandidate.evidence_digest` values MUST be unique within one `AcquiredSourceEvidence`.

Before `AcquiredSourceEvidence` construction, the pure SI-02 pre-construction normalization rule for provider transcript candidates is exactly:

1. validate every candidate descriptor independently;
2. group candidates by `evidence_digest`;
3. if candidates sharing one `evidence_digest` declare different `evidence_byte_length` values, reject the input;
4. sort all candidates by the canonical candidate order above;
5. retain the first candidate for each `evidence_digest` and discard every later candidate with that digest;
6. return the retained candidates in canonical candidate order.

This rule performs no acquisition, parsing, selection by content, filesystem access, or mutation of caller-owned values. It only canonicalizes an already-supplied immutable descriptor collection before model construction. The `AcquiredSourceEvidence` constructor accepts only the resulting already-normalized tuple and rejects any duplicate candidate `evidence_digest`. Consequently, a provider-transcript diagnostic `component_ref` resolves to exactly one candidate.

Maximum preserved candidate Evidence size: **16 MiB**. Maximum canonical descriptor size gate: **4 KiB**. The pre-parse gate permits exactly 4 KiB; one byte over is rejected before full parsing. Passing the gate does not make invalid content valid or guarantee every byte count is reachable.

## 8. `ValidatedAudioEvidence`

`ValidatedAudioEvidence` is an immutable description of validated audio content. It is neither a filesystem locator nor decoder authority.

Exact fields:

| Field | Type | Required |
|---|---|---:|
| `schema_version` | `SchemaVersion` | yes, MUST equal 1 |
| `source_identity` | SI-01 `CanonicalSourceIdentity` | yes |
| `observation_identity` | SI-01 `SourceObservationIdentity` | yes |
| `content_digest` | `DigestSha256` | yes |
| `byte_length` | `ByteCount` | yes, 1..536,870,912 |
| `duration_ms` | `Optional[Millis]` | no, if present 1..7,200,000 |
| `container` | `AudioContainer` | yes |
| `media_type` | `AsciiText[128]` | yes |
| `codec_label` | `Optional[AsciiText[128]]` | no |
| `sample_rate_hz` | `Optional[PositiveInt]` | no, if present 1..768,000 |
| `channel_count` | `Optional[PositiveInt]` | no, if present 1..64 |
| `provenance_ref` | `DigestSha256` | yes |

`media_type` MUST be lowercase ASCII and match `[a-z0-9][a-z0-9!#$&^_.+-]{0,62}/[a-z0-9][a-z0-9!#$&^_.+-]{0,62}`. Parameters are forbidden.

`content_digest` is the digest of the exact validated audio byte sequence. Duration, container, codec, sample rate, and channel count MUST be observed or validated; absent optional properties MUST NOT be fabricated.

The model contains no path, URI, file descriptor, storage reference, or native media object. A later explicitly approved capability boundary may associate the digest with supplied bytes, but MUST revalidate digest and byte length.

Maximum canonical serialized size gate: **6 KiB**. The pre-parse gate permits exactly 6 KiB; one byte over is rejected before full parsing. Passing the gate does not make invalid content valid or guarantee every byte count is reachable.

## 9. `SourceAcquisitionProvenance`

One immutable record describing the origin of one acquired Evidence contribution.

Exact fields:

| Field | Type | Required |
|---|---|---:|
| `schema_version` | `SchemaVersion` | yes, MUST equal 1 |
| `provenance_id` | `DigestSha256` | yes |
| `source_identity` | SI-01 `CanonicalSourceIdentity` | yes |
| `observation_identity` | SI-01 `SourceObservationIdentity` | yes |
| `role` | `SourceAcquisitionProvenanceRole` | yes |
| `adapter_id` | `LogicalComponentId` | yes |
| `adapter_version` | `ComponentVersion` | yes |
| `acquisition_method` | `SourceAcquisitionMethod` | yes |
| `evidence_digest` | `DigestSha256` | yes |
| `parent_refs` | `Tuple[DigestSha256, 34]` | yes |

`provenance_id` MUST equal SHA-256, algorithm-qualified as `sha256:`, over the canonical JSON object containing every field except `provenance_id`.

`parent_refs` is set-like: values MUST be unique and serialized in ascending ASCII byte order. Self-reference is invalid.

Role-specific parent maxima are exact:

- `AGGREGATE`: 0..34 `parent_refs`;
- `METADATA`: 0..16 `parent_refs`;
- `PROVIDER_TRANSCRIPT_CANDIDATE`: 0..16 `parent_refs`;
- `AUDIO`: 0..16 `parent_refs`.

An aggregate record with 35 parents is invalid. A non-aggregate component record with 17 parents is invalid. These role-specific limits apply in addition to graph depth, closure, and total-record limits.

Public provenance MUST NOT contain credentials, cookies, authorization headers, signed secret URLs, local paths, host/PID/inode data, prompts, raw provider payloads, native exception text, or wall-clock access time.

Maximum canonical serialized size gate: **8 KiB**. The pre-parse gate permits exactly 8 KiB; one byte over is rejected before full parsing. Passing the gate does not make invalid content valid or guarantee every byte count is reachable.

## 10. `SourceEvidenceDiagnostic`

Exact fields:

| Field | Type | Required |
|---|---|---:|
| `schema_version` | `SchemaVersion` | yes, MUST equal 1 |
| `subject` | `SourceEvidenceDiagnosticSubject` | yes |
| `severity` | `SourceEvidenceDiagnosticSeverity` | yes |
| `code` | `SourceEvidenceDiagnosticCode` | yes |
| `ordinal` | `UInt` | yes, 0..255 |
| `component_ref` | `Optional[DigestSha256]` | no |

Diagnostics contain no free-form public message in v1. They describe only valid, representable Evidence observations. Invalid model construction or parsing failures are raised as typed validation failures and MUST NOT be embedded as `SourceEvidenceDiagnostic` values. Diagnostics MUST NOT contain native exception text, secrets, provider bodies, URLs, paths, machine identity, prompts, or timestamps.

Diagnostic uniqueness key:

`(subject, severity, code, component_ref-or-absent)`

Duplicate uniqueness keys are invalid. SI-02 public model construction accepts only an already-canonical diagnostic tuple. Diagnostic collection, deduplication, truncation, and overflow reporting remain outside SI-02 and belong to a later operation contract. SI-02 defines no diagnostic normalization helper in v1.

Canonical order is ordinal ascending, then subject declaration order, severity declaration order, code declaration order, and component reference ASCII bytes. Serialized input in any other order is non-canonical.

Maximum canonical serialized size gate per diagnostic: **1 KiB**. Passing the gate does not make invalid content valid or guarantee every byte count is reachable.

## 11. `AcquiredSourceEvidence`

`AcquiredSourceEvidence` is the immutable provider-neutral Evidence Artifact produced by a separately authorized acquisition component.

Exact fields:

| Field | Type | Required |
|---|---|---:|
| `schema_version` | `SchemaVersion` | yes, MUST equal 1 |
| `artifact_identity` | `DigestSha256` | yes |
| `source_identity` | SI-01 `CanonicalSourceIdentity` | yes |
| `observation_identity` | SI-01 `SourceObservationIdentity` | yes |
| `producer_id` | `LogicalComponentId` | yes |
| `producer_version` | `ComponentVersion` | yes |
| `metadata_availability` | `SourceComponentAvailability` | yes |
| `transcript_availability` | `SourceComponentAvailability` | yes |
| `audio_availability` | `SourceComponentAvailability` | yes |
| `metadata` | `Optional[SourceEvidenceMetadata]` | no |
| `provider_transcript_candidates` | `Tuple[ProviderTranscriptCandidate, 32]` | yes |
| `audio_evidence` | `Optional[ValidatedAudioEvidence]` | no |
| `provenance` | `NonEmptyTuple[SourceAcquisitionProvenance, 64]` | yes |
| `aggregate_provenance_ref` | `DigestSha256` | yes |
| `diagnostics` | `Tuple[SourceEvidenceDiagnostic, 256]` | yes |

`artifact_identity` MUST equal SHA-256, algorithm-qualified as `sha256:`, over the canonical semantic preimage containing every field except `artifact_identity` and `diagnostics`. Diagnostics do not alter semantic Artifact identity.

Every `AcquiredSourceEvidence` declares exactly one Producer through `producer_id` and `producer_version`.

Maximum canonical serialized size gate: **512 KiB**. The pre-parse gate permits exactly 512 KiB; one byte over is rejected before full parsing. Passing the gate does not make invalid content valid or guarantee every byte count is reachable. The aggregate limit is independently enforced and may prevent all component-level maxima from being reached simultaneously.

## 12. Cross-model invariants

### 12.1 Identity agreement

- `observation_identity.source_identity` MUST equal top-level `source_identity`.
- Every candidate, audio observation, and provenance record MUST contain identities equal to the top-level identities.
- Identity mismatch is invalid and MUST NOT be represented merely as a non-fatal diagnostic.

### 12.2 Availability/value correspondence

Metadata:

- `AVAILABLE` requires `metadata` present.
- `UNAVAILABLE`, `UNKNOWN`, or `NOT_REQUESTED` requires `metadata` absent.

Provider transcript candidates:

- `AVAILABLE` requires 1..32 candidates.
- `UNAVAILABLE`, `UNKNOWN`, or `NOT_REQUESTED` requires an empty candidate tuple.

Audio:

- `AVAILABLE` requires `audio_evidence` present.
- `UNAVAILABLE`, `UNKNOWN`, or `NOT_REQUESTED` requires `audio_evidence` absent.

`NOT_REQUESTED` MUST NOT be inferred from missing data; it must reflect the separately supplied acquisition policy.

### 12.3 Provenance resolution

- Metadata `provenance_ref`, every candidate `provenance_ref`, and audio `provenance_ref` MUST resolve to exactly one record in `provenance` when the corresponding component is present.
- Every provenance `parent_ref` MUST resolve to exactly one record in the same tuple.
- Provenance IDs MUST be unique.
- Duplicate canonical provenance records are invalid.
- The directed child-to-parent graph MUST be acyclic.
- Maximum graph depth is **32** records, counting the starting record.
- Maximum provenance records is **64**.
- Aggregate canonical provenance bytes MUST NOT exceed **256 KiB**.
- Exactly one provenance record MUST have role `AGGREGATE`.
- `aggregate_provenance_ref` MUST resolve to that record and no other record.
- The `AGGREGATE` record is the unique graph root: it is the only record whose `provenance_id` does not appear in another record's `parent_refs`.
- Following `parent_refs` from the `AGGREGATE` root MUST reach every provenance record exactly within the bounded acyclic graph. An unrelated or unreachable record is invalid.
- The `AGGREGATE` record's `parent_refs` MUST equal the sorted unique tuple of direct component provenance references used by the Artifact: metadata `provenance_ref` when metadata is present, every candidate `provenance_ref`, and audio `provenance_ref` when audio is present.
- The `AGGREGATE` record MAY have an empty `parent_refs` tuple only when metadata, provider transcript candidates, and audio are all non-`AVAILABLE` and therefore no component values exist.
- A maximum legal Artifact containing one metadata contribution, 32 transcript-candidate contributions, and one audio contribution MAY produce exactly 34 unique direct aggregate parents. The aggregate limit accepts all 34 and rejects 35.
- That maximum direct fan-out uses 35 provenance records when each direct contribution has its own record: 34 component records plus one aggregate root. It therefore remains within the overall 64-record limit and leaves capacity only for 29 additional reachable ancestor records.
- A record directly referenced by metadata MUST have role `METADATA` and `evidence_digest` equal to metadata `evidence_digest`.
- A record directly referenced by a transcript candidate MUST have role `PROVIDER_TRANSCRIPT_CANDIDATE` and `evidence_digest` equal to that candidate's `evidence_digest`.
- A record directly referenced by audio MUST have role `AUDIO` and `evidence_digest` equal to audio `content_digest`.
- A non-aggregate parent record retains the role of the Evidence contribution it describes. Its digest relationship is governed by the contract that produced that earlier contribution.
- A non-aggregate record MUST NOT have role `AGGREGATE`; a component record MUST NOT be directly referenced by a mismatched component kind.

### 12.4 Aggregate component-evidence summary

The `AGGREGATE` provenance record's `evidence_digest` MUST equal SHA-256, algorithm-qualified as `sha256:`, over the canonical JSON serialization of exactly this object:

| Field | Exact value |
|---|---|
| `summary_schema_version` | integer `1` |
| `source_identity` | top-level canonical `source_identity` object |
| `observation_identity` | top-level canonical `observation_identity` object |
| `metadata_availability` | top-level enum value |
| `transcript_availability` | top-level enum value |
| `audio_availability` | top-level enum value |
| `metadata` | canonical metadata object when present; field omitted when absent |
| `provider_transcript_candidates` | canonical candidate tuple in section 7 order |
| `audio_evidence` | canonical audio object when present; field omitted when absent |

No other field is included. In particular the summary excludes `artifact_identity`, `diagnostics`, the aggregate record's `provenance_id`, the aggregate record itself, and all wall-clock or machine state.

The aggregate `evidence_digest` is computed first from this component summary. The `AGGREGATE` provenance record is then constructed with that digest and its exact component `parent_refs`; its `provenance_id` is computed under section 9. Finally, `artifact_identity` is computed over the section 11 semantic preimage. This order creates no digest cycle.

### 12.5 Diagnostics

- Maximum diagnostics is **256**.
- Aggregate canonical diagnostic bytes MUST NOT exceed **256 KiB**.
- SI-02 accepts only an already-bounded diagnostic tuple. More than 256 diagnostics or more than 256 KiB of canonical diagnostic bytes is invalid. Diagnostic collection, truncation, and overflow reporting belong to a later operation contract.
- Subject `EVIDENCE` requires `component_ref == artifact_identity`.
- Subject `METADATA` requires `component_ref == metadata.evidence_digest` when metadata exists.
- Subject `PROVIDER_TRANSCRIPT_CANDIDATE` requires `component_ref` equal to exactly one candidate `evidence_digest` when candidates exist.
- Subject `AUDIO_EVIDENCE` requires `component_ref == audio_evidence.content_digest` when audio exists.
- Subject `PROVENANCE` requires `component_ref` equal to exactly one `provenance_id`.
- `component_ref` MUST be absent only for `COMPONENT_UNAVAILABLE`, `COMPONENT_UNKNOWN`, or `COMPONENT_NOT_REQUESTED` when the diagnostic subject is `METADATA`, `PROVIDER_TRANSCRIPT_CANDIDATE`, or `AUDIO_EVIDENCE` and the applicable availability state has no component value. In all other cases `component_ref` is required.
- Availability diagnostic code MUST agree with the corresponding top-level availability value.
- `COMPONENT_AVAILABLE`, `COMPONENT_UNAVAILABLE`, `COMPONENT_UNKNOWN`, and `COMPONENT_NOT_REQUESTED` are valid only for subjects `METADATA`, `PROVIDER_TRANSCRIPT_CANDIDATE`, and `AUDIO_EVIDENCE`.
- `EVIDENCE_PARTIAL` is valid only for subject `EVIDENCE` and requires at least one top-level availability value other than `AVAILABLE`.
- `METADATA_INCOMPLETE` is valid only for subject `METADATA` with metadata present.
- `TRANSCRIPT_CANDIDATE_INCOMPLETE` is valid only for subject `PROVIDER_TRANSCRIPT_CANDIDATE` with a resolved candidate reference.
- `AUDIO_PROPERTIES_INCOMPLETE` is valid only for subject `AUDIO_EVIDENCE` with audio present and at least one optional audio property absent.
- `PROVENANCE_REPLAYED` is valid only for subject `PROVENANCE` and MUST reference a provenance record whose `acquisition_method` is `REPLAY`.
- `PROVENANCE_REPLAYED` MUST have severity `INFORMATIONAL`. It records provenance method only and does not assert or imply that durable replay input currently exists or remains available.
- Diagnostics MUST NOT make an invalid public model valid.

### 12.6 Ordering

- Provider transcript candidates MUST be serialized in the canonical order defined in section 7; input in any other order is non-canonical serialized input.
- Provenance records MUST be serialized by `provenance_id` ASCII bytes ascending.
- Diagnostics MUST be serialized in section 10 canonical order.
- Semantic tuples retain declared order unless this contract explicitly defines them as set-like.

### 12.7 Absence and unknowns

- Missing information remains absent or uses the applicable `UNKNOWN` enum.
- Unknown information MUST NOT be fabricated from filenames, URLs, wall-clock time, provider popularity, or machine state.
- `UNAVAILABLE` MUST NOT be used when the system merely failed to determine availability.

## 13. Equality and hashing

Every SI-02 immutable public model is structurally equal over all canonical public fields. No v1 exception exists.

Hashing MUST use the same canonical public fields as equality. Nested tuples participate by canonical tuple value. Two equal models MUST serialize to byte-identical canonical JSON.

Artifact identity is a separate semantic derivation. Two `AcquiredSourceEvidence` values that differ only in diagnostics are structurally unequal but intentionally share the same `artifact_identity` when all semantic-preimage fields are equal.

## 14. Canonical serialization and parsing

SI-02 adopts locked SI-01 and Step 5 canonical JSON rules exactly:

- strict UTF-8 without BOM;
- direct UTF-8 for non-ASCII text (`ensure_ascii=false` semantics);
- Unicode NFC before validation and byte accounting;
- lexicographically sorted object keys;
- compact `,` and `:` separators;
- no insignificant whitespace, trailing newline, or trailing bytes;
- absent optional fields omitted; JSON `null` rejected;
- enum values serialized exactly as their declared uppercase names;
- duplicate and unknown keys rejected;
- unsupported schema versions distinguished from malformed data;
- floats and non-finite values prohibited;
- booleans rejected where integers are required;
- persistent/public serialized input equal to canonical reserialization byte-for-byte.

The applicable per-model byte limit MUST be enforced before full parsing. Exact-limit input is accepted if otherwise valid; one byte over is rejected before full parsing.

Canonical parsing MUST validate nested model limits as well as the enclosing aggregate limit.

## 15. Resource limits

Locked candidate maxima for SI-02 v1:

| Resource | Maximum |
|---|---:|
| metadata canonical bytes | 24 KiB |
| transcript candidates per Evidence Artifact | 32 |
| preserved transcript-candidate Evidence bytes | 16 MiB each |
| transcript candidate canonical bytes | 4 KiB each |
| audio evidence byte length | 512 MiB |
| audio duration observation | 7,200,000 ms |
| audio evidence canonical bytes | 6 KiB |
| provenance records | 64 |
| component provenance parents per record (`METADATA`, `PROVIDER_TRANSCRIPT_CANDIDATE`, `AUDIO`) | 16 |
| aggregate provenance direct component parents (`AGGREGATE`) | 34 |
| provenance graph depth | 32 |
| provenance record canonical bytes | 8 KiB each |
| aggregate provenance canonical bytes | 256 KiB |
| diagnostics | 256 |
| diagnostic canonical bytes | 1 KiB each |
| aggregate diagnostic canonical bytes | 256 KiB |
| aggregate `AcquiredSourceEvidence` canonical bytes | 512 KiB |

The 512 KiB aggregate canonical limit is independent and may prevent all component maxima from being reached simultaneously. Exact-limit wording describes a pre-parse byte gate, not a guarantee that every byte count is reachable by valid field combinations.

These are representation and validation limits only. They grant no authority to acquire, read, write, retain, delete, decode, transcribe, retry, or publish anything.

## 16. Failure and diagnostic boundary

SI-02 defines validation failures and Evidence diagnostics, not operation outcomes.

Malformed serialized input, unsupported versions, invalid identities, invalid digests, identity disagreement, availability/value disagreement, unresolved provenance, cycles, invalid ordering, and exceeded limits MUST fail model construction/parsing.

SI-02 diagnostics describe valid but incomplete or informational Evidence observations only. Invalid construction and parsing failures are typed validation failures and are not embedded as diagnostics. SI-02 diagnostics MUST NOT replace an operation-level status, native exception, acquisition failure result, transcript result, or storage result. Diagnostic collection, truncation, and overflow reporting belong to a later operation contract.

## 17. Explicit authority exclusions

- No SI-02 model performs or authorizes network access.
- No SI-02 model performs or authorizes filesystem access.
- No SI-02 model owns a retry policy.
- No SI-02 model selects or generates a transcript.
- No SI-02 model invokes a transcription backend.
- No SI-02 model normalizes a source document.
- No SI-02 model creates, cleans, or inspects a temporary workspace.
- No SI-02 model performs cache lookup, cache publication, catalog mutation, reconciliation, promotion, or cleanup.
- SI-02 does not define a storage `ArtifactType` or use `ArtifactType.UNKNOWN`.
- SI-02 adds no existing package-level export authority.
- SI-02 grants no production YouTube acquisition authority.
- SI-02 grants no H3–H7, Knowledge, Narrative, Presentation, Director, pipeline, renderer, or orchestration authority.

## 18. Required contract tests after implementation approval

### 18.1 Closed vocabularies and models

- every enum member accepted and unknown values rejected;
- exact required/optional fields;
- immutable equality and hashing;
- schema-version rejection;
- booleans-as-integers, floats, `null`, unknown fields, and duplicate keys rejected;
- Exact-limit input passes the pre-parse size gate, subject to subsequent canonical parsing and validation. One byte over the configured limit is rejected before full parsing. No valid model fixture is required to reach an otherwise unreachable exact-limit size.

### 18.2 Metadata and language

- at least one metadata value required;
- every field byte/time bound;
- every supported language-tag form and canonical casing;
- `und` accepted;
- extensions, variants, private use, invalid casing, and non-ASCII rejected;
- no popularity/provider-specific fields.

### 18.3 Transcript candidates and audio

- candidate canonical ordering and duplicate identity rejection;
- duplicate candidate Evidence-digest detection;
- identical candidate Evidence digests require identical byte lengths before normalization;
- deterministic retention of the first candidate in canonical order for each Evidence digest;
- constructor rejection of candidate tuples that remain digest-duplicate;
- exact opaque candidate-ID grammar boundaries;
- Producer privacy obligation does not introduce heuristic parser behavior;
- evidence byte-length and evidence-format validation;
- no locator in the candidate model;
- later supplied bytes require digest and byte-length revalidation;
- missing candidate bytes cannot trigger implicit reacquisition;
- exact metadata, candidate, and audio digest grammar;
- media-type grammar;
- audio byte, duration, sample-rate, and channel boundaries;
- no path, URI, native handle, or decoder authority;
- absent audio properties remain absent.

### 18.4 Availability

- every availability/value combination;
- `AVAILABLE` requires corresponding non-empty/present value;
- all other availability values require absence/empty tuple;
- `UNKNOWN` remains distinct from `UNAVAILABLE` and `NOT_REQUESTED`.

### 18.5 Provenance

- provenance-ID derivation;
- every component reference resolves exactly once;
- exact role/reference compatibility;
- exactly one aggregate role and exact `aggregate_provenance_ref` resolution;
- aggregate component-summary digest derivation;
- missing, duplicate, unreachable, and unrelated records rejected;
- unique root and complete root reachability;
- component-role parent exact limit 16 accepted and 17 rejected;
- aggregate-role parent exact limit 34 accepted and 35 rejected;
- maximum one-metadata plus 32-candidate plus one-audio fan-out remains within 64 total records;
- maximum 34-parent fan-out remains acyclic and completely root-reachable;
- parent sorting and uniqueness;
- self-reference, two-node cycle, deep cycle, and unresolved parent rejection;
- exact record, parent, depth, and aggregate-byte boundaries;
- identity agreement and evidence-digest relationship;
- privacy exclusions.

### 18.6 Artifact identity and diagnostics

- exact aggregate component-summary and Artifact semantic-preimage derivation without a digest cycle;
- diagnostics excluded from Artifact identity but included in equality/hash;
- Producer required exactly once;
- diagnostic duplicate-key rejection and canonical ordering;
- exact 256 and 256 KiB rejection boundaries with no SI-02 truncation;
- exact diagnostic subject/reference compatibility and permitted absence;
- provider-transcript diagnostic reference resolves to exactly one candidate after digest normalization;
- diagnostics cannot legalize invalid models;
- no free-form or native exception text.

### 18.7 Serialization and architectural boundaries

- canonical round trips and non-canonical input rejection;
- UTF-8, BOM, NFC, direct non-ASCII, key ordering, compact separators, and no trailing newline;
- nested and aggregate size enforcement;
- zero filesystem, network, storage, cache, STT, workspace, H3–H7, Knowledge, Narrative, Presentation, Director, pipeline, renderer, or production YouTube acquisition calls;
- Step 5A–5E, H1, H2, storage exports, Director/pipeline, and full-suite regression.

## 19. Expected Lock Review questions

SI-02 draft.3 remains a draft until review confirms:

1. the evidence/component boundary is sufficiently useful with digest-and-length descriptors but without locators;
2. `SourceEvidenceMetadata` belongs in SI-02 rather than SI-04 normalization;
3. provider transcript candidate identity, format, byte-length, replay-supply, and digest semantics are sufficient and provider-neutral;
4. audio evidence fields and maxima are suitable for a replaceable later transcription boundary;
5. provenance roles, aggregate component summary, unique root, provenance-ID derivation, graph closure, depth, and evidence-digest relationships are fully mechanical and cycle-free;
6. Artifact identity correctly includes the semantic Evidence envelope and excludes diagnostics;
7. availability/value matrices, diagnostic subject/reference compatibility, and rejection-only overflow behavior do not leak operation-result authority;
8. every byte/count/depth limit is acceptable and internally reachable;
9. no acquisition, transcript-generation, filesystem, storage-publication, or operation authority has leaked into SI-02;
10. all public types resolve only to locked SI-01 or types defined in SI-02.

## 20. Design-lock criterion

SI-02 may become:

`DESIGN LOCKED — IMPLEMENTATION NOT YET APPROVED`

only after repository-aware review resolves every mandatory issue in section 19 and confirms compatibility with Core Architecture, locked SI-01, Step 5A–5E, H1, H2, Storage, Directors, pipeline, renderer, and the roadmap planning gate.

Locking SI-02 would not authorize implementation.
