# SI-04 — Normalized Source Document Contract

> **Documentary Engine**
> **Contract:** SI-04 — Normalized Source Document Contract
> **Version:** 1.0.0
> **Status:** DESIGN LOCKED — IMPLEMENTATION NOT YET APPROVED
> **Scope:** Immutable provider-neutral normalized source-document representation only
> **Implementation authority:** NOT YET APPROVED
> **Repository authority:** Existing locked repository contracts, the approved prospective Core Architecture, and locked SI-01, SI-02, and SI-03 remain authoritative.

## 1. Purpose

SI-04 defines the immutable provider-neutral document Artifact produced by structurally normalizing already-validated SI-02 Evidence and, when present, one already-validated SI-03 Transcript Artifact. It sits before Knowledge extraction.

SI-04 owns only:

- normalized-document completeness and language vocabularies;
- bounded normalized metadata projection;
- normalized transcript-derived section and block representation;
- exact lineage to SI-02 and optional SI-03 Artifacts;
- the aggregate `NormalizedSourceDocument`;
- Artifact identity, equality, hashing, canonical serialization, bounded diagnostics, resource limits, replay, and cross-model invariants.

SI-04 performs structural normalization only. It creates no factual Knowledge and defines no acquisition, transcript production, semantic interpretation, selection policy, operation envelope, storage publication, or orchestration behavior.

## 2. Dependencies and precedence

- **SI04-DEP-001:** SI-04 adopts the approved Core Architecture prospectively.
- **SI04-DEP-002:** SI-04 imports `CanonicalSourceIdentity` and `SourceObservationIdentity` exactly from locked SI-01.
- **SI04-DEP-003:** SI-04 imports SI-02 `EvidenceLanguageTag`, `SourceEvidenceMetadata`, and `AcquiredSourceEvidence` semantics without reinterpretation.
- **SI04-DEP-004:** SI-04 imports SI-03 `TranscriptOrigin`, `TranscriptLanguage`, `TranscriptCompleteness`, `TranscriptSegment`, and `TranscriptArtifact` semantics without reinterpretation.
- **SI04-DEP-005:** Existing Step 5A–5E, H1, H2, Storage, Director, pipeline, renderer, export, and roadmap contracts remain unchanged.
- **SI04-DEP-006:** `NormalizedSourceDocument` is an architectural Artifact, not storage `ArtifactDescriptor`. SI-04 defines no `ArtifactType`, `CacheNamespace`, cache key, publication mapping, or `ArtifactType.UNKNOWN` workaround.
- **SI04-DEP-007:** If SI-04 conflicts with a locked dependency, the locked contract prevails and SI-04 remains blocked pending revision.

## 3. Primitive conventions

SI-04 reuses SI-01 `SchemaVersion`, `Utf8Text[N]`, `AsciiText[N]`, `UInt`, `PositiveInt`, and `Optional[T]`, and SI-02 `Millis`, `DigestSha256`, `LogicalComponentId`, `ComponentVersion`, `Tuple[T,N]`, and `NonEmptyTuple[T,N]` without modification.

All Unicode MUST be NFC before validation, byte accounting, equality, hashing, ordering, identity derivation, and serialization. Booleans are never valid integers. Floats and JSON `null` are prohibited in all SI-04 public models.

## 4. Structural normalization boundary

Allowed structural normalization is limited to:

- exact bounded projection of provider-neutral SI-02 metadata fields;
- exact one-to-one copying of SI-03 transcript segments into normalized blocks;
- deterministic construction of the single v1 transcript section;
- canonical tuple ordering and canonical JSON serialization;
- deterministic derivation of document language and completeness under this contract;
- validation of identities, lineage, limits, and cross-model agreement.

SI-04 MUST NOT summarize, paraphrase, translate, classify claims, rank Evidence, resolve contradictions, extract facts, infer entities, infer topics, perform sentiment analysis, speculate, repair meaning, add headings, merge or split transcript segments, or create Knowledge.

## 5. Closed vocabularies

### 5.1 `NormalizedDocumentCompleteness`

- `COMPLETE`
- `PARTIAL`

An unavailable document is not represented. There is no `UNAVAILABLE` or `UNKNOWN` Artifact state.

`COMPLETE` requires both:

1. normalized metadata projected from present SI-02 metadata; and
2. a linked SI-03 Transcript Artifact whose completeness is `COMPLETE`.

`PARTIAL` requires a valid document contribution but at least one of:

- normalized metadata is absent;
- the Transcript Artifact is absent;
- the linked Transcript Artifact completeness is `PARTIAL` or `UNKNOWN`.

The matrix is exact. SI-04 derives completeness from represented inputs and never from operation success, policy intent, diagnostic absence, segment count, timestamps, or apparent textual quality.

### 5.2 `NormalizedDocumentLanguageStatus`

- `TRANSCRIPT_DECLARED`
- `TRANSCRIPT_DETECTED`
- `METADATA_DECLARED`
- `UNKNOWN`

### 5.3 Section and block vocabularies

`NormalizedSectionKind`:

- `TRANSCRIPT`

`NormalizedContentBlockKind`:

- `TRANSCRIPT_SEGMENT`

The closed v1 vocabularies intentionally authorize no semantic section classification.

### 5.4 Diagnostic vocabularies

`NormalizedDocumentDiagnosticSubject`:

- `DOCUMENT`
- `METADATA`
- `TRANSCRIPT`
- `LANGUAGE`

`NormalizedDocumentDiagnosticSeverity`:

- `NON_FATAL`
- `INFORMATIONAL`

`NormalizedDocumentDiagnosticCode`:

- `DOCUMENT_PARTIAL`
- `METADATA_ABSENT`
- `TRANSCRIPT_ABSENT`
- `TRANSCRIPT_PARTIAL`
- `TRANSCRIPT_COMPLETENESS_UNKNOWN`
- `LANGUAGE_UNKNOWN`

Unknown enum values MUST be rejected.

## 6. `NormalizedDocumentLanguage`

Exact fields:

| Field | Type | Required |
|---|---|---:|
| `schema_version` | `SchemaVersion` | yes, MUST equal 1 |
| `status` | `NormalizedDocumentLanguageStatus` | yes |
| `language_tag` | SI-02 `EvidenceLanguageTag` | yes |

Derivation is exact:

1. When a Transcript Artifact is present with SI-03 language status `DECLARED`, use its exact tag and `TRANSCRIPT_DECLARED`.
2. When a Transcript Artifact is present with status `DETECTED`, use its exact tag and `TRANSCRIPT_DETECTED`.
3. When a Transcript Artifact is present with status `UNKNOWN`, use `und` and `UNKNOWN`; metadata MUST NOT override transcript-language unknownness.
4. When no Transcript Artifact is present and normalized metadata has a known `language_hint`, use that exact tag and `METADATA_DECLARED`.
5. Otherwise use `und` and `UNKNOWN`.

Known statuses require a tag other than `und`. `UNKNOWN` requires exactly `und`. SI-04 performs no language detection, translation, negotiation, reconciliation, or fallback beyond this closed precedence rule.

Maximum canonical serialized size gate: **256 bytes**.

## 7. `NormalizedSourceMetadata`

Exact bounded projection of one SI-02 `SourceEvidenceMetadata`:

| Field | Type | Required |
|---|---|---:|
| `schema_version` | `SchemaVersion` | yes, MUST equal 1 |
| `source_metadata_evidence_digest` | `DigestSha256` | yes |
| `source_metadata_provenance_ref` | `DigestSha256` | yes |
| `title` | `Optional[Utf8Text[1024]]` | no |
| `creator_label` | `Optional[Utf8Text[512]]` | no |
| `creator_identity` | `Optional[AsciiText[512]]` | no |
| `published_at_ms` | `Optional[Millis]` | no |
| `duration_ms` | `Optional[Millis]` | no |
| `language_hint` | `Optional[EvidenceLanguageTag]` | no |
| `description_excerpt` | `Optional[Utf8Text[16384]]` | no |

Every field MUST equal the corresponding SI-02 metadata field exactly; the two renamed linkage fields equal SI-02 `evidence_digest` and `provenance_ref`. At least one optional metadata value MUST be present. SI-04 does not rewrite, extend, summarize, interpret, or complete metadata.

Maximum canonical serialized size gate: **24 KiB**.

## 8. `NormalizedContentBlock`

One exact transcript-derived content block.

| Field | Type | Required |
|---|---|---:|
| `schema_version` | `SchemaVersion` | yes, MUST equal 1 |
| `ordinal` | `UInt` | yes, 0..19,999 |
| `kind` | `NormalizedContentBlockKind` | yes, MUST equal `TRANSCRIPT_SEGMENT` |
| `transcript_segment_ordinal` | `UInt` | yes, 0..19,999 |
| `text` | `Utf8Text[16384]` | yes |
| `start_ms` | `Optional[Millis]` | no, if present 0..7,200,000 |
| `end_ms` | `Optional[Millis]` | no, if present 0..7,200,000 |
| `speaker_label` | `Optional[Utf8Text[256]]` | no |

For block ordinal `i`, every field after `kind` MUST equal the corresponding field of SI-03 segment ordinal `i`; block `ordinal` and `transcript_segment_ordinal` MUST both equal `i`. Timestamp paired-presence, span, speaker, Unicode, control-character, and text-preservation rules remain exactly those of SI-03.

This deliberate text duplication makes the normalized document independently consumable and replayable without loading SI-03. Exact segment ordinal lineage prevents semantic regrouping. SI-04 MUST NOT merge, split, omit, reorder, edit, or synthesize transcript segments.

Maximum canonical serialized size gate: **20 KiB**.

## 9. `NormalizedDocumentSection`

Exact fields:

| Field | Type | Required |
|---|---|---:|
| `schema_version` | `SchemaVersion` | yes, MUST equal 1 |
| `ordinal` | `UInt` | yes, MUST equal 0 |
| `kind` | `NormalizedSectionKind` | yes, MUST equal `TRANSCRIPT` |
| `blocks` | `NonEmptyTuple[NormalizedContentBlock, 20000]` | yes |

When a Transcript Artifact is present, the document contains exactly one section, it has ordinal `0`, and its blocks are the exact one-to-one mapping of all transcript segments in SI-03 ordinal order. When no Transcript Artifact is present, the section tuple is empty. SI-04 adds no heading because doing so would require an assertion absent from the source models.

Maximum aggregate canonical block bytes per section: **12 MiB**. Maximum canonical section size gate: **13 MiB**.

## 10. `NormalizedDocumentLineage`

Exact fields:

| Field | Type | Required |
|---|---|---:|
| `schema_version` | `SchemaVersion` | yes, MUST equal 1 |
| `source_evidence_artifact_identity` | `DigestSha256` | yes |
| `source_evidence_aggregate_provenance_ref` | `DigestSha256` | yes |
| `transcript_artifact_identity` | `Optional[DigestSha256]` | no |
| `transcript_completeness` | `Optional[TranscriptCompleteness]` | no |
| `transcript_language` | `Optional[TranscriptLanguage]` | no |

Originating construction or explicit association validation with supplied Artifacts MUST verify:

- the SI-02 identity and aggregate provenance reference exactly;
- the SI-02, optional SI-03, and top-level source/observation identities all agree;
- when SI-03 is supplied, its `provenance_link.source_evidence_artifact_identity` equals this SI-02 Artifact identity;
- when SI-03 is supplied, its `artifact_identity` equals `transcript_artifact_identity`;
- when SI-03 is supplied, `transcript_completeness` and `transcript_language` equal its exact public values;
- present `transcript_artifact_identity` requires both transcript snapshot fields present;
- absent `transcript_artifact_identity` requires both transcript snapshot fields absent.

Standalone canonical parsing validates intrinsic field grammar, presence rules, and inclusion in document Artifact identity only. It does not load SI-02 or SI-03, perform lookup, or reacquire anything. Any caller claiming a live association with separately supplied Artifacts MUST explicitly revalidate it.

Maximum canonical serialized size gate: **1 KiB**.

## 11. `NormalizedDocumentDiagnostic`

Exact fields:

| Field | Type | Required |
|---|---|---:|
| `schema_version` | `SchemaVersion` | yes, MUST equal 1 |
| `subject` | `NormalizedDocumentDiagnosticSubject` | yes |
| `severity` | `NormalizedDocumentDiagnosticSeverity` | yes |
| `code` | `NormalizedDocumentDiagnosticCode` | yes |
| `ordinal` | `UInt` | yes, 0..255 |

Diagnostics contain no free-form message. Exact compatibility matrix:

- `DOCUMENT_PARTIAL`: subject `DOCUMENT`, completeness `PARTIAL`.
- `METADATA_ABSENT`: subject `METADATA`, metadata absent.
- `TRANSCRIPT_ABSENT`: subject `TRANSCRIPT`, Transcript Artifact and sections absent.
- `TRANSCRIPT_PARTIAL`: subject `TRANSCRIPT`, `lineage.transcript_completeness == PARTIAL`.
- `TRANSCRIPT_COMPLETENESS_UNKNOWN`: subject `TRANSCRIPT`, `lineage.transcript_completeness == UNKNOWN`.
- `LANGUAGE_UNKNOWN`: subject `LANGUAGE`, normalized language status `UNKNOWN`.

Every unlisted subject/code combination is invalid. Diagnostic uniqueness key is `(subject, severity, code)`. Duplicates are invalid. Canonical order is ordinal ascending, then subject declaration order, severity declaration order, and code declaration order. Non-canonical serialized ordering is rejected.

Maximum diagnostics: **256**. Maximum canonical bytes per diagnostic: **512 bytes**. Maximum aggregate canonical diagnostic bytes: **128 KiB**.

## 12. `NormalizedSourceDocument`

Exact fields:

| Field | Type | Required |
|---|---|---:|
| `schema_version` | `SchemaVersion` | yes, MUST equal 1 |
| `artifact_identity` | `DigestSha256` | yes |
| `source_identity` | SI-01 `CanonicalSourceIdentity` | yes |
| `observation_identity` | SI-01 `SourceObservationIdentity` | yes |
| `producer_id` | `LogicalComponentId` | yes |
| `producer_version` | `ComponentVersion` | yes |
| `completeness` | `NormalizedDocumentCompleteness` | yes |
| `language` | `NormalizedDocumentLanguage` | yes |
| `metadata` | `Optional[NormalizedSourceMetadata]` | no |
| `sections` | `Tuple[NormalizedDocumentSection, 1]` | yes |
| `lineage` | `NormalizedDocumentLineage` | yes |
| `diagnostics` | `Tuple[NormalizedDocumentDiagnostic, 256]` | yes |

At least one source contribution MUST exist: metadata present or Transcript Artifact linked with its exact nonempty normalized section. Neither contribution means no document and construction is invalid.

Maximum normalized block text bytes: **8 MiB**. Maximum aggregate canonical block bytes: **12 MiB**. Maximum canonical document size gate: **16 MiB**. These gates are independently enforced.

## 13. Cross-model invariants

### 13.1 Identity agreement

- `observation_identity.source_identity` MUST equal top-level `source_identity`.
- Supplied SI-02 and optional SI-03 identities MUST equal top-level identities during originating construction or explicit association validation.
- Identity mismatch is invalid, never diagnostic.

### 13.2 Contribution matrix

- Metadata present requires exact resolution to SI-02 metadata and its Evidence/provenance digests.
- Metadata absent requires SI-02 metadata absent during explicit association validation.
- Transcript lineage present requires exactly one section and exact one-to-one block mapping.
- Transcript lineage absent requires empty sections.
- Transcript lineage presence requires its exact completeness and language snapshots; absence forbids both snapshots.
- A linked Transcript Artifact MUST itself be associated with the same SI-02 Artifact under SI-03 rules.
- Completeness and language MUST equal the exact derivations in sections 5 and 6.

### 13.3 Deterministic ordering

- The only v1 section has ordinal `0`.
- Blocks are contiguous and unique with ordinals exactly `0..len(blocks)-1` in tuple order.
- Block and transcript-segment ordinals agree exactly.
- Transcript timestamp ordering is preserved without repair.
- Metadata object fields serialize by canonical key order; metadata has no semantic tuple ordering.
- Diagnostics use section 11 order.

### 13.4 Text preservation and no fabrication

- Transcript-derived block text MUST be byte-identical UTF-8 to canonical SI-03 segment text.
- Metadata values MUST be value-identical to canonical SI-02 metadata values.
- SI-04 performs no additional trimming, whitespace collapse, line-ending conversion, punctuation repair, casing change, correction, translation, redaction, or content insertion.
- Missing metadata, transcript, timestamps, speaker labels, and language remain absent or unknown under the exact model rules.
- No value may be inferred from filenames, URLs, popularity, wall-clock time, machine state, or diagnostics.

## 14. Artifact identity

`artifact_identity` MUST equal SHA-256, algorithm-qualified as `sha256:`, over canonical JSON serialization of exactly:

| Field | Exact value |
|---|---|
| `identity_schema_version` | integer `1` |
| `source_identity` | canonical top-level object |
| `observation_identity` | canonical top-level object |
| `producer_id` | top-level value |
| `producer_version` | top-level value |
| `completeness` | top-level enum value |
| `language` | canonical language object |
| `metadata` | canonical metadata object when present; omitted when absent |
| `sections` | canonical section tuple |
| `lineage` | canonical lineage object |

No other field is included. Diagnostics are excluded from Artifact identity but included in structural equality and hashing. The derivation has no cycle: referenced SI-02 and SI-03 identities exist before normalized-document identity is computed. The preimage contains no time of normalization, machine identity, path, cache key, namespace, storage descriptor, or persistence state.

## 15. Equality and hashing

Every SI-04 immutable public model is structurally equal over all canonical public fields. Hashing uses exactly the equality fields. Nested tuples participate by canonical tuple value.

Two documents differing only in diagnostics are structurally unequal but intentionally share `artifact_identity` when every semantic-preimage field is equal.

## 16. Canonical serialization and parsing

SI-04 adopts locked SI-01, SI-02, SI-03, and Step 5 canonical JSON rules exactly:

- strict UTF-8 without BOM;
- direct UTF-8 for non-ASCII text;
- NFC before validation and byte accounting;
- lexicographically sorted object keys;
- compact `,` and `:` separators;
- no insignificant whitespace, trailing newline, or trailing bytes;
- absent optional fields omitted; JSON `null` rejected;
- exact uppercase enum names;
- duplicate and unknown keys rejected;
- unsupported schema versions distinguished from malformed input;
- floats and non-finite values prohibited;
- booleans rejected where integers are required;
- serialized public input equal to canonical reserialization byte-for-byte.

The applicable per-model byte gate MUST be enforced before full parsing. Exact-limit input passes the gate subject to later validation. One byte over is rejected before full parsing. No valid fixture is required to reach an otherwise unreachable exact-limit size. Nested and aggregate limits are independently enforced.

## 17. Resource limits

| Resource | Maximum |
|---|---:|
| normalized language canonical bytes | 256 bytes |
| normalized metadata canonical bytes | 24 KiB |
| transcript sections | 1 |
| blocks | 20,000 |
| block text | 16 KiB each |
| block canonical bytes | 20 KiB each |
| aggregate normalized block text | 8 MiB |
| aggregate canonical block bytes | 12 MiB |
| section canonical bytes | 13 MiB |
| timestamp | 7,200,000 ms |
| speaker label | 256 UTF-8 bytes |
| lineage canonical bytes | 1 KiB |
| diagnostics | 256 |
| diagnostic canonical bytes | 512 bytes each |
| aggregate diagnostic canonical bytes | 128 KiB |
| normalized document canonical bytes | 16 MiB |

These limits are representation and validation gates only. They grant no authority to read, acquire, transcribe, interpret, store, publish, retry, or delete anything.

## 18. Replay and association validation

A preserved canonical `NormalizedSourceDocument` MUST be parseable, intrinsically validated, compared, hashed, and consumed with exactly zero network access and zero filesystem access. Its normalized metadata and block content are embedded; replay does not require loading SI-02 or SI-03.

Standalone parsing MUST NOT perform external Artifact lookup or attempt reacquisition. Intrinsically invalid lineage fails construction/parsing. Failure to resolve or agree with separately supplied SI-02/SI-03 Artifacts fails originating construction or explicit association validation.

Explicit association validation is pure over already-supplied immutable models. It performs no acquisition, file access, transcript generation, storage lookup, or cache lookup.

## 19. Failure and diagnostic boundary

Malformed input, unsupported versions, invalid ordering, invalid identity, intrinsic lineage invalidity, failed explicit association, invalid contribution/completeness/language matrices, altered projections, invalid diagnostics, or exceeded limits MUST fail the applicable construction, parsing, or association validation boundary.

Diagnostics describe valid representable normalized-document observations only. They do not legalize invalid models and do not replace operation status, acquisition failure, transcript result, Knowledge result, or native exception. Diagnostic collection, truncation, overflow handling, and operation envelopes remain outside SI-04.

## 20. Explicit authority exclusions

- No factual Knowledge creation, fact extraction, claim classification, entity inference, ranking, contradiction resolution, or uncertainty resolution.
- No summarization, paraphrasing, translation, sentiment, topic interpretation, speculation, or narrative generation.
- No network, provider, redirect, authentication, or source-acquisition authority.
- No filesystem, path, workspace, temporary-file, cleanup, or recovery authority.
- No transcript acquisition, selection, generation, parsing behavior, `LocalTranscriptProvider`, or `SpeechToTextBackend` behavior.
- No request/result envelope, retry, cancellation, scheduling, or orchestration authority.
- No storage `ArtifactType`, `ArtifactDescriptor`, `CacheNamespace`, cache lookup, publication, reconciliation, or `ArtifactType.UNKNOWN` workaround.
- No H3–H7, package-export, Director, pipeline, renderer, Knowledge, Narrative, Presentation, or Render authority.
- No production YouTube acquisition authority.

## 21. Required contract tests after implementation approval

### 21.1 Models and canonical serialization

- exact enum membership, exact fields, immutability, schema versions, equality, and hashing;
- duplicate/unknown keys, malformed UTF-8, BOM, floats, booleans-as-integers, `null`, non-NFC, non-canonical ordering, whitespace, and trailing bytes rejected;
- exact-gate passage subject to validation and one-over pre-parse rejection for every model and aggregate gate;
- canonical round trips and structural hashing laws.

### 21.2 Contribution, completeness, and language

- all three required source combinations accepted;
- neither metadata nor transcript rejected as unavailable document;
- exact `COMPLETE` and `PARTIAL` matrix;
- exact transcript-first language derivation and metadata-only fallback;
- `und`/known-status matrix and no detection, reconciliation, translation, or fallback behavior.

### 21.3 Metadata, sections, blocks, and preservation

- exact SI-02 metadata projection and absent-field preservation;
- exact one-section/empty-section presence matrix;
- exact one-to-one SI-03 segment/block mapping, ordinals, timestamps, speakers, and text;
- altered, omitted, inserted, merged, split, or reordered blocks rejected;
- exact aggregate text/block/section/document bounds;
- no trimming, whitespace rewrite, punctuation repair, semantic grouping, headings, summarization, paraphrasing, translation, or fabrication.

### 21.4 Lineage and association

- exact SI-02 Artifact identity and aggregate provenance reference;
- exact optional SI-03 Artifact identity and its SI-02 provenance linkage;
- source/observation identity agreement across every supplied model;
- standalone parsing performs intrinsic validation only with no external loading;
- every cross-Artifact mismatch fails originating construction or explicit association validation;
- missing Artifacts never trigger lookup or reacquisition.

### 21.5 Diagnostics, identity, replay, and boundaries

- exact diagnostic subject/code/state matrix, uniqueness, ordering, and limits;
- diagnostics excluded from Artifact identity but included in equality/hash;
- exact Artifact semantic preimage and absence of digest cycles;
- preserved document replay performs zero network, filesystem, SI-02/SI-03 loading, storage, cache, Knowledge, Director, pipeline, or renderer calls;
- Step 5A–5E, SI-01–SI-03, H1, H2, Storage, Directors, pipeline, renderer, exports, and full-suite regression.

## 22. Expected Lock Review questions

SI-04 draft.1 remains a draft until review confirms:

1. whether exact one-to-one transcript block copying is the correct tradeoff between standalone replay and duplication;
2. whether metadata-only and transcript-only partial documents are sufficient without an operation policy;
3. whether the exact completeness matrix correctly distinguishes a complete normalized representation from source availability;
4. whether transcript-first language precedence preserves meaning without interpretation;
5. whether the single closed transcript section is sufficient and avoids semantic grouping;
6. whether normalized metadata is useful without reinterpreting SI-02 Evidence;
7. whether lineage is mechanically complete for standalone parsing and explicit SI-02/SI-03 association;
8. whether the Artifact identity preimage is complete, cycle-free, and free of storage/cache identity;
9. whether diagnostic ownership and compatibility remain representation-only;
10. whether all numeric limits are mutually consistent and practical;
11. whether every structural-normalization allowance and semantic prohibition is exact;
12. whether SI-04 preserves every locked upstream and repository authority boundary.

## 23. Design-lock criterion

SI-04 may become:

`DESIGN LOCKED — IMPLEMENTATION NOT YET APPROVED`

only after repository-aware review resolves every mandatory issue in section 22 and confirms compatibility with Core Architecture, locked SI-01–SI-03, Step 5A–5E, H1, H2, Storage, Directors, pipeline, renderer, exports, and the roadmap planning gate.

Locking SI-04 would not authorize implementation.
