# Documentary Engine Roadmap

This file is the repository source of truth for development order. Detailed locked
contract documents override shorthand roadmap text if they conflict.

Before selecting or implementing a major development step, developers and AI agents
must:

1. read this roadmap;
2. compare the requested work with **Current task** and **Next task**;
3. report a conflict before implementing an out-of-order major step;
4. update this roadmap when an approved milestone is completed; and
5. never treat speculative future items as approved implementation contracts.

## Completed

- Audio Director 4.7.0: deterministic intent/tone, energy and abstract music,
  ambience, ducking, transitions, intentional silence, diagnostics,
  rollback-protected artifact publishing, config, and pipeline fail-safe
- Story Director 4.6.0: deterministic document structure, scene dramaturgy,
  story graph, diagnostics, and metadata-only fallback planning
- Caption Director 4.5.0: deterministic caption layout metadata, conservative
  highlighting, subject avoidance, and fail-closed planning
- Image Intelligence 4.4.0: deterministic candidate inspection, quality ranking,
  immutable image selection, and scene-level diagnostics
- Visual Director 4.1.0 foundation
- Shot Library 4.1.1
- Narrative Intent Engine 4.1.2
- Visual-to-Motion integration 4.2.0
- Pacing Director 4.3.0

## Current

The current major workstream is the **Source Ingestion & Understanding
Foundation**. The post-H2 planning gate selected the Source Ingestion transition.

- **Current phase:** Source Ingestion & Understanding Foundation
- **Current task:** Pure SI-01–SI-04 + Knowledge foundation implementation
- **Status:** IMPLEMENTATION APPROVED — PURE FOUNDATION ONLY
- **Next architectural task:** Source Ingestion Producer and Operation Foundation Contract
- **Planned implementation branch:** `feature/source-understanding-foundation`
- **Documentation transition branch:** `docs/source-understanding-transition`

The documentation transition must be reviewed and merged before the planned
implementation branch is created from updated `main`. Approval is limited to the
pure, in-memory domain slice defined below. It does not approve operational Source
Ingestion or Knowledge extraction.

The persistent-cache construction sequence remains complete through Step 5E, H1 —
Persistent Cache Catalog / Index remains complete, and H2 — Catalog Rebuild /
Reconciliation remains contract-complete. H3–H7 remain future and unapproved.

## Cache & Storage / Housekeeping Foundation

### Foundation milestones completed

- [x] Storage foundation
- [x] Deterministic cache-key contracts
- [x] Read-only storage inventory/safety foundation
- [x] Step 5A — Persistent cache-entry contracts
- [x] Step 5B — Read-only lookup design
- [x] Step 5B — Lock-lifecycle observation design
- [x] Step 5B1 — Read-only lookup adapter and resource limits
- [x] Step 5B — Read-only persistent cache lookup

### Completed: Step 5B — Read-only persistent cache lookup

Internal implementation slices:

- [x] Step 5B2A — Final-entry structure validation
- [x] Step 5B2B — Canonical document reads/parsing and unsupported-version classification
- [x] Step 5B2C — Identity, expectations, and cross-document integrity validation

Step 5B2A and Step 5B2B/5B2C helpers remain internal. Step 5B5 composes them into
the contract-complete public read-only lookup behavior.

### Approved later Step 5B substeps

- [x] Step 5B3 — Payload validation
- [x] Step 5B4 — Stable snapshot, diagnostics, and read-only lock observation
- [x] Step 5B5 — Public lookup orchestration and regression hardening

## Planned

### Persistent-cache construction

- [x] Step 5C — Staging writer
- [x] Step 5D — Locking and atomic promotion
- [x] Step 5E — Read-only recovery inspection

These items require their applicable design and implementation approvals. Checked
status here records sequence and progress; it does not replace a locked contract.

### Housekeeping

- [x] H1 — Persistent Cache Catalog / Index
- [x] H2 — Catalog Rebuild / Reconciliation
- [ ] H3 — Retention & Cleanup Policy — future, implementation not approved
- [ ] H4 — Immutable Cleanup Planning — future, implementation not approved
- [ ] H5 — Identity-Safe Cleanup Mutation — future, implementation not approved
- [ ] H6 — Quota / Storage-Budget Policy — future, implementation not approved
- [ ] H7 — Automatic Housekeeping — future, implementation not approved

These later layers are intended to let Documentary Engine retain valuable,
expensive-to-produce artifacts while safely identifying and deleting regenerable,
expired, or stale material. They are planning direction only and are not approved
implementation contracts.

## Next major product milestone: Source Ingestion & Understanding

After Cache & Storage / Housekeeping reaches the required maturity, the next major
product direction is **Source Ingestion & Understanding**.

The first ingestion target is **YouTube URL**. YouTube is the initial vertical slice,
not a special one-off architecture. The ingestion layer should later normalize all
supported source types into a shared internal source representation.

Planned source expansion:

- web pages and articles;
- PDF and other documents;
- text;
- audio and video files; and
- multiple sources in one documentary project.

Target high-level pipeline:

```text
Source
  ↓
Source Ingest
  ↓
Transcript / Content Extraction
  ↓
Knowledge Extraction / Summarization
  ↓
Narrative / Script Generation
  ↓
Visual Planning
  ↓
Image / Asset Generation or Selection
  ↓
Voice / Audio Director
  ↓
Motion / Layout
  ↓
Captions
  ↓
Renderer
  ↓
Finished Documentary
```

This milestone remains future product direction until its contracts and implementation
steps are separately reviewed and approved.

### Source Understanding architecture completed

The following representation contracts are complete and **DESIGN LOCKED**:

- [x] Core Architecture — implementation not approved by that contract alone
- [x] SI-01 — Source Identity
- [x] SI-02 — Source Evidence Models
- [x] SI-03 — Transcript Foundation
- [x] SI-04 — Normalized Source Document
- [x] Knowledge — Knowledge Foundation

These contracts define the pure representation chain:

```text
External Source
  ↓
SI-01 Identity
  ↓
SI-02 Evidence
  ↓
SI-03 Transcript
  ↓
SI-04 Normalized Source Document
  ↓
Knowledge
```

They do not make production Source Ingestion operational and do not approve a
Knowledge extraction Producer.

### Approved implementation slice: pure foundation only

Implementation approval is limited to the following in-memory, pure-domain work:

- **SI-01:** immutable models, canonical serialization/parsing, identity derivation,
  and zero-network YouTube identity canonicalization;
- **SI-02:** immutable Evidence models, deterministic provenance validation,
  canonical serialization/parsing, and pure candidate normalization;
- **SI-03:** immutable Transcript models, canonical serialization/parsing, Artifact
  identity, intrinsic validation, pure SI-02 association validation, and
  deterministic transcript projection;
- **SI-04:** immutable normalized-document models, canonical serialization/parsing,
  exact metadata/transcript projections, intrinsic validation, and pure SI-02/SI-03
  association validation; and
- **Knowledge:** immutable representation models, canonical serialization/parsing,
  support/claim/disagreement/Artifact identities, deterministic ordering, intrinsic
  validation, and pure SI-04 association validation.

The approved slice must remain:

- in-memory and pure-domain only;
- zero network;
- zero filesystem or workspace access;
- zero model invocation;
- zero persistent cache or storage publication; and
- zero Director, pipeline, or renderer integration.

### Explicitly unapproved

The following remain unapproved and require separate architecture, lock review, and
implementation approval where applicable:

- live acquisition and production YouTube acquisition;
- network clients, redirects, authentication, credentials, and rate limiting;
- provider transcript selection or parsing;
- `LocalTranscriptProvider`, `SpeechToTextBackend`, local STT, and model inference;
- temporary workspace and media lifecycle;
- Knowledge semantic extraction Producer and model-assisted factual extraction;
- Source Ingestion operation/orchestration envelopes;
- retries, cancellation, deadlines, budgets, and multi-source orchestration;
- storage/cache integration and new `ArtifactType` or `CacheNamespace` mappings;
- H3–H7;
- Narrative, Presentation, and Render;
- Director, pipeline, and renderer integration; and
- package-level export changes unless separately approved.

## Roadmap maintenance

### When a step is completed

- mark it completed;
- update **Current task**;
- advance **Next task**;
- record the relevant commit or tag when useful; and
- keep completed milestones visibly checked so development history remains
  understandable.

Do not erase completed roadmap items. Preserve project history while keeping the next
approved step obvious.
