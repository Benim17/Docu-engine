# Knowledge — Knowledge Foundation Contract

> **Documentary Engine**
> **Contract:** Knowledge — Knowledge Foundation Contract
> **Version:** 1.0.0
> **Status:** DESIGN LOCKED — IMPLEMENTATION NOT YET APPROVED
> **Scope:** Immutable supported factual Knowledge representation only
> **Implementation authority:** NOT YET APPROVED
> **Repository authority:** Existing locked repository contracts, the approved prospective Core Architecture, and locked SI-01 through SI-04 remain authoritative.

## 1. Purpose and authority

Evidence describes validated observations. `NormalizedSourceDocument` preserves source meaning structurally. Knowledge derives bounded factual understanding from that document. Narrative later decides how Knowledge is communicated.

This contract owns immutable supported claims, exact normalized-document support references, uncertainty, unresolved disagreement, Knowledge lineage/provenance, diagnostics, identity, canonical serialization, replay, and limits.

Knowledge MAY identify explicit factual assertions, parse structurally equivalent values without changing meaning, associate claims with exact source support, preserve uncertainty and disagreement, and represent explicit temporal assertions.

Knowledge MUST NOT fabricate facts, strengthen certainty, silently resolve or rank disagreements, remove material qualification, summarize away context, create Narrative framing, decide storytelling order, or weaken upstream Evidence authority.

## 2. Dependencies and precedence

- **KF-DEP-001:** This contract adopts the approved Core Architecture prospectively.
- **KF-DEP-002:** It imports SI-01 `CanonicalSourceIdentity` and `SourceObservationIdentity` exactly.
- **KF-DEP-003:** It uses SI-04 `NormalizedSourceDocument`, metadata-field, block, completeness, language, and lineage semantics without reinterpretation. SI-02/SI-03 lineage remains indirect through SI-04.
- **KF-DEP-004:** Step 5A–5E, H1, H2, Storage, Directors, pipeline, renderer, exports, and the roadmap planning gate remain unchanged.
- **KF-DEP-005:** `KnowledgeArtifact` is an architectural Artifact, not storage `ArtifactDescriptor`. No `ArtifactType`, `CacheNamespace`, publication mapping, cache identity, or `ArtifactType.UNKNOWN` workaround is defined.
- **KF-DEP-006:** A conflict with a locked dependency blocks this draft; the locked contract prevails.

## 3. Primitive conventions

This contract reuses locked `SchemaVersion`, `Utf8Text[N]`, `AsciiText[N]`, `UInt`, `PositiveInt`, `Optional[T]`, `Boolean`, `DigestSha256`, `LogicalComponentId`, `ComponentVersion`, `Tuple[T,N]`, and `NonEmptyTuple[T,N]` without modification.

Additional primitives:

| Type | Definition |
|---|---|
| `CanonicalDecimal` | ASCII matching `-?(?:0|[1-9][0-9]{0,37})\.[0-9]{0,17}[1-9]`; negative zero rejected |
| `SignedInt` | JSON integer in `-99999999999999999999999999999999999999..99999999999999999999999999999999999999`; booleans invalid |

Integers only are used for integer values. Decimal values use canonical decimal strings, never JSON floats. Unicode is NFC before validation, byte accounting, equality, hashing, ordering, identity, and serialization. JSON `null` is prohibited; absent optionals are omitted. Booleans are not integers.

## 4. Closed vocabularies

### 4.1 Claim and support

`KnowledgeClaimType`:

- `EXPLICIT_ASSERTION`
- `ATTRIBUTE`
- `RELATIONSHIP`
- `EVENT`
- `QUANTITY`
- `TEMPORAL`

`KnowledgeSupportMode`:

- `DIRECT`
- `STRUCTURALLY_DERIVED`

`KnowledgeSupportTarget`:

- `METADATA_TITLE`
- `METADATA_CREATOR_LABEL`
- `METADATA_CREATOR_IDENTITY`
- `METADATA_PUBLISHED_AT_MS`
- `METADATA_DURATION_MS`
- `METADATA_LANGUAGE_HINT`
- `METADATA_DESCRIPTION_EXCERPT`
- `TRANSCRIPT_BLOCK`

`KnowledgeStructuralDerivation`:

- `NONE`
- `EXACT_VALUE_PARSE`
- `EXACT_FIELD_COMPOSITION`

`DIRECT` requires derivation `NONE`. `STRUCTURALLY_DERIVED` requires one of the other values. Structural derivation may parse or compose explicitly represented values only when the resulting proposition preserves exactly the supported meaning. It cannot add a factual premise.

### 4.2 Values and time

`KnowledgeValueKind`:

- `TEXT`
- `INTEGER`
- `DECIMAL`
- `BOOLEAN`
- `TEMPORAL`

`KnowledgeTemporalPrecision`:

- `YEAR`
- `MONTH`
- `DAY`

### 4.3 Uncertainty

`KnowledgeUncertainty`:

- `ASSERTED`
- `APPROXIMATE`
- `SOURCE_UNCERTAIN`

These values preserve the source assertion as represented. They are not model confidence. No percentage, score, probability, or inferred certainty is permitted.

Absence of established support produces no accepted `KnowledgeClaim`. A source-supported statement that something is unknown or not established remains representable as an `ASSERTED` claim whose `assertion_text` preserves the source statement exactly.

### 4.4 Disagreement

`KnowledgeDisagreementKind`:

- `CONTRADICTION`
- `DIFFERING_VALUE`
- `DIFFERING_DATE`
- `DIFFERING_NAME`
- `SOURCE_DISAGREEMENT`

All disagreements are unresolved in v1. No truth selection or preferred-claim field exists.

### 4.5 Production and completeness

`KnowledgeProductionMethod`:

- `DETERMINISTIC_RULES`
- `MODEL_ASSISTED`

`KnowledgeExtractionScope`:

- `FULL_DOCUMENT`
- `PARTIAL_DOCUMENT`

`KnowledgeCompleteness`:

- `COMPLETE`
- `PARTIAL`

Completeness describes only represented extraction coverage. It does not mean all truth is known, the source is globally complete, extraction succeeded operationally, or unsupported facts do not exist elsewhere.

### 4.6 Diagnostics

`KnowledgeDiagnosticSubject`:

- `ARTIFACT`
- `CLAIM`
- `DISAGREEMENT`
- `PROVENANCE`

`KnowledgeDiagnosticSeverity`:

- `NON_FATAL`
- `INFORMATIONAL`

`KnowledgeDiagnosticCode`:

- `KNOWLEDGE_PARTIAL`
- `NO_FACTUAL_CLAIMS`
- `CLAIM_APPROXIMATE`
- `CLAIM_SOURCE_UNCERTAIN`
- `DISAGREEMENT_PRESENT`
- `MODEL_ASSISTED_OUTPUT`

Unknown enum values are rejected.

## 5. `KnowledgeTemporalValue`

Exact fields:

| Field | Type | Required |
|---|---|---:|
| `schema_version` | `SchemaVersion` | yes, equals 1 |
| `precision` | `KnowledgeTemporalPrecision` | yes |
| `year` | `PositiveInt` | yes, 1..9999 |
| `month` | `Optional[PositiveInt]` | precision-specific, 1..12 |
| `day` | `Optional[PositiveInt]` | precision-specific, valid Gregorian day |

`YEAR` forbids month/day. `MONTH` requires month and forbids day. `DAY` requires both. Missing precision MUST NOT be invented. Approximate time uses claim uncertainty `APPROXIMATE`; it does not add absent calendar components. No wall-clock time participates unless explicitly supported source content is itself the claim.

Maximum canonical size gate: **256 bytes**.

## 6. `KnowledgeClaimValue`

Exact fields:

| Field | Type | Required |
|---|---|---:|
| `schema_version` | `SchemaVersion` | yes, equals 1 |
| `kind` | `KnowledgeValueKind` | yes |
| `text_value` | `Optional[Utf8Text[4096]]` | kind-specific |
| `integer_value` | `Optional[SignedInt]` | kind-specific |
| `decimal_value` | `Optional[CanonicalDecimal]` | kind-specific |
| `boolean_value` | `Optional[Boolean]` | kind-specific |
| `temporal_value` | `Optional[KnowledgeTemporalValue]` | kind-specific |
| `unit_label` | `Optional[AsciiText[128]]` | no |

Exactly one value field is present and agrees with `kind`. `unit_label` is allowed only for `INTEGER` or `DECIMAL`, preserves an explicitly supported unit, and MUST NOT perform unit conversion. Values outside the bounded signed-integer or canonical-decimal grammar are not representable as structured numeric values and remain supported `TEXT` without fabricated conversion.

Maximum canonical size gate: **6 KiB**.

## 7. `KnowledgeSupportReference`

One immutable support snapshot referencing an exact SI-04 location without duplicating its full Artifact graph.

| Field | Type | Required |
|---|---|---:|
| `schema_version` | `SchemaVersion` | yes, equals 1 |
| `support_id` | `DigestSha256` | yes |
| `source_document_artifact_identity` | `DigestSha256` | yes |
| `target` | `KnowledgeSupportTarget` | yes |
| `section_ordinal` | `Optional[UInt]` | target-specific, must equal 0 |
| `block_ordinal` | `Optional[UInt]` | target-specific, 0..19,999 |
| `supported_value_digest` | `DigestSha256` | yes |
| `support_excerpt` | `Optional[Utf8Text[4096]]` | target-specific |

`support_id` is SHA-256 over the canonical object containing every field except `support_id`.

For `TRANSCRIPT_BLOCK`, section and block ordinals are required; `support_excerpt` is required and MUST be an exact nonempty contiguous Unicode substring of the canonical block text. For textual metadata targets, ordinals are absent and an exact nonempty contiguous excerpt of that field is required. For integer metadata targets, ordinals and excerpt are absent. `supported_value_digest` is SHA-256 over canonical JSON serialization of the exact complete targeted SI-04 field value or block object, not merely the excerpt.

Originating construction or explicit association validation verifies the document identity, target existence, ordinals, digest, and exact excerpt. Standalone parsing validates intrinsic grammar and identity derivation only and loads no SI-04 Artifact.

Maximum canonical size gate: **6 KiB**.

## 8. `KnowledgeClaim`

Exact fields:

| Field | Type | Required |
|---|---|---:|
| `schema_version` | `SchemaVersion` | yes, equals 1 |
| `claim_identity` | `DigestSha256` | yes |
| `claim_type` | `KnowledgeClaimType` | yes |
| `support_mode` | `KnowledgeSupportMode` | yes |
| `structural_derivation` | `KnowledgeStructuralDerivation` | yes |
| `assertion_text` | `Utf8Text[4096]` | yes |
| `subject_label` | `Optional[Utf8Text[512]]` | no |
| `predicate_label` | `Optional[AsciiText[128]]` | no |
| `value` | `Optional[KnowledgeClaimValue]` | no |
| `uncertainty` | `KnowledgeUncertainty` | yes |
| `support_refs` | `NonEmptyTuple[DigestSha256, 64]` | yes |

`claim_identity` is SHA-256 over canonical JSON of exactly `claim_type`, `assertion_text`, `subject_label` when present, `predicate_label` when present, `value` when present, and `uncertainty`. Support mode, derivation, and support references are deliberately excluded so the same semantic claim has one identity across independently supported occurrences.

`support_refs` is set-like: unique and ASCII-sorted. Every reference resolves to one support record in the enclosing Artifact. `DIRECT` means the factual proposition is explicit in at least one referenced support. `STRUCTURALLY_DERIVED` permits only exact value parsing or exact field composition from all referenced supports without adding meaning.

Exact claim-type structural matrix:

- `EXPLICIT_ASSERTION`: `assertion_text` required; `subject_label`, `predicate_label`, and `value` optional.
- `ATTRIBUTE`: `subject_label`, `predicate_label`, and `value` required.
- `RELATIONSHIP`: `subject_label`, `predicate_label`, and `value` required; `value.kind` MUST equal `TEXT`.
- `EVENT`: `assertion_text` required; `subject_label`, `predicate_label`, and `value` optional.
- `QUANTITY`: `predicate_label` and `value` required; `value.kind` MUST equal `INTEGER` or `DECIMAL`; `subject_label` optional.
- `TEMPORAL`: `predicate_label` and `value` required; `value.kind` MUST equal `TEMPORAL`; `subject_label` optional.

Every incompatible claim-type/required-field/value-kind combination is invalid.

Unsupported inference is invalid and cannot be represented as an accepted claim. A model-generated assertion is not factual merely because it parses. A separately authorized future producer must establish the support mode and semantic support obligation; deterministic model validation and explicit association validation remain mandatory. SI-04 content remains authoritative over generated claim text.

Empty claims, unsupported aliases, silent entity merging, inferred real-world identity, translation, paraphrase that changes qualification, and numerical confidence are forbidden. Labels are supported textual referents, not global entity identities. V1 intentionally defines no Entity model: ambiguity remains in supported labels and assertion text rather than being silently resolved.

Maximum canonical claim size gate: **16 KiB**.

## 9. `KnowledgeDisagreement`

Exact fields:

| Field | Type | Required |
|---|---|---:|
| `schema_version` | `SchemaVersion` | yes, equals 1 |
| `disagreement_id` | `DigestSha256` | yes |
| `kind` | `KnowledgeDisagreementKind` | yes |
| `claim_refs` | `NonEmptyTuple[DigestSha256, 32]` | yes, 2..32 |

`claim_refs` is unique and ASCII-sorted and every reference resolves to one claim. `disagreement_id` is SHA-256 over canonical JSON of `kind` and `claim_refs`. Every referenced claim must have nonempty resolved support. Disagreement asserts only that supported claims differ under the declared kind. It does not identify truth, rank sources, merge values, or remove any claim.

Exact disagreement compatibility matrix:

- Every disagreement references 2..32 distinct resolved claims and every referenced `claim_identity` is unique.
- `DIFFERING_VALUE` requires equal present `subject_label` values, equal present `predicate_label` values, a typed `value` on every claim, and at least two differing canonical values.
- `DIFFERING_DATE` requires equal present `subject_label` values, equal present `predicate_label` values, `value.kind == TEMPORAL` on every claim, and at least two differing canonical temporal values.
- `DIFFERING_NAME` requires supported textual labels. Same-referent applicability remains an authorized Knowledge Producer obligation.
- For `CONTRADICTION`, semantic incompatibility remains an authorized Knowledge Producer obligation.
- `SOURCE_DISAGREEMENT` requires the claims to have distinct support sets. Semantic disagreement remains an authorized Knowledge Producer obligation.

Intrinsic parsing validates only the structural portions of this compatibility matrix. Semantic applicability that grammar cannot establish remains a Knowledge Producer obligation and cannot be inferred from model output alone.

Maximum canonical size gate: **4 KiB**.

## 10. `KnowledgeProvenance`

Exact fields:

| Field | Type | Required |
|---|---|---:|
| `schema_version` | `SchemaVersion` | yes, equals 1 |
| `production_method` | `KnowledgeProductionMethod` | yes |
| `extraction_scope` | `KnowledgeExtractionScope` | yes |
| `source_document_artifact_identity` | `DigestSha256` | yes |
| `source_document_completeness` | SI-04 `NormalizedDocumentCompleteness` | yes |
| `model_runtime_fingerprint` | `Optional[DigestSha256]` | method-specific |

`DETERMINISTIC_RULES` forbids the runtime fingerprint. `MODEL_ASSISTED` requires it. The fingerprint is an opaque digest slot only. Its preimage, model, runtime, prompt/policy, dependency, privacy, and reproducibility rules belong to a future Knowledge Producer contract. This contract neither creates nor interprets it.

Knowledge completeness is derived exactly: `COMPLETE` if and only if extraction scope is `FULL_DOCUMENT` and source-document completeness is `COMPLETE`; every other valid combination requires `PARTIAL`. This rule also applies when no factual claim was established. `FULL_DOCUMENT` is a Producer assertion that every document field/block was examined under its authorized extraction policy; it does not assert that every truth was found.

Maximum canonical size gate: **2 KiB**.

## 11. `KnowledgeDiagnostic`

Exact fields:

| Field | Type | Required |
|---|---|---:|
| `schema_version` | `SchemaVersion` | yes, equals 1 |
| `subject` | `KnowledgeDiagnosticSubject` | yes |
| `severity` | `KnowledgeDiagnosticSeverity` | yes |
| `code` | `KnowledgeDiagnosticCode` | yes |
| `ordinal` | `UInt` | yes, 0..255 |
| `subject_ref` | `Optional[DigestSha256]` | subject-specific |

Exact matrix:

- `KNOWLEDGE_PARTIAL`: `ARTIFACT`, absent reference, Artifact completeness `PARTIAL`.
- `NO_FACTUAL_CLAIMS`: `ARTIFACT`, absent reference, claims empty.
- `CLAIM_APPROXIMATE`: `CLAIM`, reference resolves one claim with `APPROXIMATE`.
- `CLAIM_SOURCE_UNCERTAIN`: `CLAIM`, reference resolves one claim with `SOURCE_UNCERTAIN`.
- `DISAGREEMENT_PRESENT`: `DISAGREEMENT`, reference resolves one disagreement.
- `MODEL_ASSISTED_OUTPUT`: `PROVENANCE`, reference equals `source_document_artifact_identity`, method `MODEL_ASSISTED`, severity `INFORMATIONAL`.

Every unlisted combination is invalid. Diagnostics have no free-form message and cannot legalize invalid claims. Uniqueness key is `(subject,severity,code,subject_ref-or-absent)`. Canonical order is ordinal, subject declaration order, severity declaration order, code declaration order, then absent reference before ASCII reference.

Maximum canonical size: **512 bytes**. Maximum count: **256**. Maximum aggregate diagnostic bytes: **128 KiB**.

## 12. `KnowledgeArtifact`

Exact fields:

| Field | Type | Required |
|---|---|---:|
| `schema_version` | `SchemaVersion` | yes, equals 1 |
| `artifact_identity` | `DigestSha256` | yes |
| `source_identity` | SI-01 `CanonicalSourceIdentity` | yes |
| `observation_identity` | SI-01 `SourceObservationIdentity` | yes |
| `producer_id` | `LogicalComponentId` | yes |
| `producer_version` | `ComponentVersion` | yes |
| `completeness` | `KnowledgeCompleteness` | yes |
| `provenance` | `KnowledgeProvenance` | yes |
| `supports` | `Tuple[KnowledgeSupportReference, 50000]` | yes |
| `claims` | `Tuple[KnowledgeClaim, 20000]` | yes |
| `disagreements` | `Tuple[KnowledgeDisagreement, 10000]` | yes |
| `diagnostics` | `Tuple[KnowledgeDiagnostic, 256]` | yes |

Claims may be empty when no accepted factual assertion was established. If claims are empty, supports and disagreements MUST be empty. Completeness still follows the exact provenance matrix in section 10. Every support must be referenced by at least one claim; unreachable support records are invalid. Claim identities, support IDs, and disagreement IDs are unique.

Canonical collection order is support ID ASCII ascending, claim identity ASCII ascending, disagreement ID ASCII ascending, and diagnostic order from section 11. Serialized input in any other order is non-canonical.

## 13. Cross-model and semantic invariants

- `observation_identity.source_identity` equals top-level `source_identity`.
- During originating construction or explicit association validation, the supplied SI-04 identities equal top-level identities and its Artifact identity/completeness equal provenance snapshots.
- Every support references that same SI-04 Artifact.
- Every claim has resolved support and satisfies the exact support-mode/derivation matrix.
- Unsupported inference, certainty strengthening, silent contradiction resolution, and unreferenced factual content are invalid Producer output.
- Structurally equal values may be normalized only with exact source support and without changing precision, units, qualification, ambiguity, or uncertainty.
- Every materially conflicting supported accepted claim remains present and is linked by at least one matching disagreement record. No disagreement record may link claims that do not differ under its declared kind.
- Labels do not create global entity identity. Alias equivalence and entity merging are absent in v1.
- Temporal values preserve exact represented precision; absent components remain absent.
- Knowledge validation never treats model output alone as support.

Semantic support and disagreement correctness are Producer obligations that deterministic structure validation cannot establish from grammar alone. Explicit association validates exact source locations, snapshots, excerpts, and digests. A future authorized Knowledge Producer contract must define extraction-policy and semantic-validation responsibility before implementation approval.

## 14. Artifact identity

`artifact_identity` is SHA-256, algorithm-qualified as `sha256:`, over canonical JSON of exactly:

| Field | Exact value |
|---|---|
| `identity_schema_version` | integer 1 |
| `source_identity` | canonical top-level object |
| `observation_identity` | canonical top-level object |
| `producer_id` | top-level value |
| `producer_version` | top-level value |
| `completeness` | top-level value |
| `provenance` | canonical provenance object |
| `supports` | canonical support tuple |
| `claims` | canonical claim tuple |
| `disagreements` | canonical disagreement tuple |

Diagnostics are excluded from Artifact identity but included in structural equality and hashing. No digest cycle exists: supports and claims are identified before Artifact identity. The preimage contains no wall-clock time, machine identity, path, cache/storage identity, or hidden runtime state.

## 15. Equality, hashing, and canonical serialization

All immutable public models are structurally equal and hashed over every canonical public field. Two Artifacts differing only in diagnostics are structurally unequal but share Artifact identity when semantic-preimage fields match.

Canonical JSON follows locked SI-01–SI-04 and Step 5 exactly: strict UTF-8, no BOM, direct non-ASCII UTF-8, NFC, lexicographically sorted keys, compact separators, no insignificant whitespace or trailing bytes/newline, omitted absent optionals, rejected `null`, duplicate and unknown keys rejected, unsupported versions distinct from malformed input, no floats/non-finite values, booleans rejected as integers, and byte-for-byte canonical serialized input.

Every per-model limit is a pre-parse gate. Exact-limit input passes the gate subject to validation; one byte over is rejected before full parsing. No valid fixture must reach an unreachable exact limit. Nested and aggregate gates are independent.

## 16. Resource limits

| Resource | Maximum |
|---|---:|
| temporal value | 256 bytes |
| claim value | 6 KiB |
| support reference | 6 KiB |
| supports | 50,000 |
| support references per claim | 64 |
| support excerpt | 4 KiB |
| claim assertion/label text aggregate | 8 MiB |
| claim | 16 KiB |
| claims | 20,000 |
| aggregate canonical claims | 12 MiB |
| disagreement | 4 KiB |
| claim references per disagreement | 32 |
| disagreements | 10,000 |
| aggregate canonical disagreements | 8 MiB |
| provenance | 2 KiB |
| diagnostics | 256 |
| diagnostic | 512 bytes |
| aggregate diagnostics | 128 KiB |
| aggregate canonical supports | 12 MiB |
| Knowledge Artifact | 32 MiB |

Aggregate limits intentionally constrain simultaneous maxima. These are representation gates only and grant no extraction, model, network, filesystem, storage, cache, or operation authority.

## 17. Replay and validation boundaries

A preserved canonical `KnowledgeArtifact` is parseable, intrinsically validated, compared, hashed, and consumed with zero network and zero filesystem access. Support snapshots are embedded; standalone parsing does not load SI-04.

Intrinsic parsing validates grammar, IDs/digests, presence matrices, ordering, internal references, uniqueness, completeness/provenance agreement, and limits. It does not claim semantic truth or externally resolve supports.

Explicit association validation accepts an already-supplied immutable SI-04 Artifact and validates every identity, lineage snapshot, target, ordinal, complete-value digest, and excerpt. Missing SI-04 MUST NOT trigger lookup or reacquisition.

## 18. Failure and diagnostic boundary

Malformed input, invalid IDs, unsupported versions, unresolved internal references, invalid order, invalid presence matrices, duplicate/unreachable records, failed explicit association, invalid completeness, or exceeded limits fail the applicable boundary. Diagnostics represent valid observations only and never replace extraction outcome, operation status, native exceptions, or invalid claims.

Unsupported inference is rejected Producer output, not a diagnostic and not an accepted factual claim. Semantic support failure discovered by an authorized Producer or association validator prevents Artifact construction/association; it is not silently downgraded.

## 19. Explicit authority exclusions

- No unrestricted model authority and no Knowledge extraction implementation is defined.
- No Narrative framing, storytelling order, hooks, scripts, scenes, pacing, tone, Presentation, visual direction, or Render authority.
- No source acquisition, transcript generation, language translation, network, filesystem, workspace, or temporary-file authority.
- No cache lookup/publication, storage mapping, `ArtifactType`, `ArtifactDescriptor`, `CacheNamespace`, or `ArtifactType.UNKNOWN`.
- No request/result envelopes, retries, scheduling, cancellation, Directors, or pipeline modification.
- No H3–H7 or production YouTube acquisition authority.
- No roadmap or package-export authority.

## 20. Required contract tests after implementation approval

- exact enums, fields, immutability, equality/hash, versions, canonical JSON, malformed/non-canonical rejection, and every size gate;
- exact value-kind, temporal-precision, claim-type/required-field/value-kind, support-target, support-mode/derivation, provenance-method, and completeness matrices;
- support ID, claim identity, disagreement ID, and Artifact identity derivations;
- exact target resolution, full-value digest, excerpt containment, ordinals, and SI-04 association mismatch rejection;
- direct and structural claims require resolved support; unsupported inference and unreachable support rejected;
- uncertainty preserved without confidence scores or strengthening; absence of established support produces no accepted claim, while exact source statements of unknown status remain `ASSERTED`;
- exact structural disagreement compatibility for every kind; semantic Producer obligations retained where grammar cannot establish applicability; every claim retained with no winner/ranking;
- entity merging/alias inference absent and temporal precision preserved;
- exact set uniqueness and ordering for supports, claims, disagreements, refs, and diagnostics;
- empty-claim Artifact matrix and exact diagnostic compatibility;
- diagnostics excluded from Artifact identity and included in equality/hash;
- zero-access standalone replay and no missing-upstream lookup/reacquisition;
- SI-01–SI-04, Step 5A–5E, H1/H2, Storage, Directors, pipeline, renderer, exports, and full-suite regressions.

## 21. Expected Lock Review questions

Draft.2 remains a draft until review confirms:

1. whether assertion text, the exact claim-type structural matrix, and optional typed values are sufficiently useful without an Entity graph;
2. whether direct versus structural support and the two structural derivations are exact enough to exclude unsupported inference, with absence of established support producing no claim;
3. whether support snapshots/digests/excerpts provide sufficient traceability without duplicating SI-04;
4. whether asserted, approximate, and source-uncertain states cover accepted factual claims while source-supported statements of unknown status remain exact `ASSERTED` claims;
5. whether the structural disagreement matrix and explicit semantic Producer obligations prohibit invalid links and silent truth resolution;
6. whether temporal precision and decimal representation are sufficient and bounded;
7. whether completeness and extraction scope are representation-only and mechanically coherent;
8. whether model runtime fingerprint placement avoids hidden model authority while deferring its preimage correctly;
9. whether claim/support/disagreement identities and Artifact identity are complete and cycle-free;
10. whether semantic Producer obligations are sufficiently separated from deterministic parsing and association validation;
11. whether diagnostics and all numeric limits are practical and internally consistent;
12. whether every upstream, repository, Narrative, Presentation, Render, storage, and roadmap boundary is preserved.

## 22. Design-lock criterion

This contract may become `DESIGN LOCKED — IMPLEMENTATION NOT YET APPROVED` only after repository-aware review resolves every mandatory issue above and confirms compatibility with Core Architecture, locked SI-01–SI-04, Step 5A–5E, H1/H2, Storage, Directors, pipeline, renderer, exports, and the roadmap gate.

Locking this contract would not authorize implementation.
