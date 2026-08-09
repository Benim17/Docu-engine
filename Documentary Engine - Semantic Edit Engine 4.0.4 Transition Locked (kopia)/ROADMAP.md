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

The current major workstream is the **Cache and Storage Manager**, within the
Cache & Storage / Housekeeping Foundation phase.

- **Current phase:** Cache & Storage / Housekeeping Foundation
- **Current task:** Step 5B5 — Public lookup orchestration and regression hardening
- **Status:** NOT STARTED — NEXT APPROVED IMPLEMENTATION STEP
- **Next task:** Step 5C — Staging writer
- **Current branch:** `feature/cache-storage-foundation`

Step 5B5 is the next approved implementation step. It has not begun.

## Cache & Storage / Housekeeping Foundation

### Foundation milestones completed

- [x] Storage foundation
- [x] Deterministic cache-key contracts
- [x] Read-only storage inventory/safety foundation
- [x] Step 5A — Persistent cache-entry contracts
- [x] Step 5B — Read-only lookup design
- [x] Step 5B — Lock-lifecycle observation design
- [x] Step 5B1 — Read-only lookup adapter and resource limits

### Current: Step 5B2 — Structure and document validation

Internal implementation slices:

- [x] Step 5B2A — Final-entry structure validation
- [x] Step 5B2B — Canonical document reads/parsing and unsupported-version classification
- [x] Step 5B2C — Identity, expectations, and cross-document integrity validation

Step 5B2A and Step 5B2B/5B2C helpers remain internal. Contract-complete public
`MISS` and `LOCKED_OR_IN_PROGRESS` behavior is reserved for Step 5B5.

### Approved later Step 5B substeps

- [x] Step 5B3 — Payload validation
- [x] Step 5B4 — Stable snapshot, diagnostics, and read-only lock observation
- [ ] Step 5B5 — Public lookup orchestration and regression hardening

## Planned

### Persistent-cache construction

- [ ] Step 5C — Staging writer
- [ ] Step 5D — Locking and atomic promotion
- [ ] Step 5E — Read-only recovery inspection

These items require their applicable design and implementation approvals. Checked
status here records sequence and progress; it does not replace a locked contract.

### Housekeeping — design not yet locked

- [ ] Persistent cache index / fast cache catalog
- [ ] Retention and cleanup policy
- [ ] Safe cleanup executor
- [ ] Quota / storage-budget enforcement
- [ ] Automatic housekeeping / pruning

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
