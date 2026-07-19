# Visual Director 4.2.0

The three planning layers have separate responsibilities:

- **Semantic Engine:** decides what a scene contains, including its image, narration context, and exact timeline.
- **Visual Director:** describes how a completed semantic scene should eventually be experienced.
- **Motion Engine:** executes camera instructions against the authoritative semantic timeline.

The Visual Director remains an identity/pass-through boundary for semantic data. It returns a validated deep copy of semantic scenes and keeps all visual metadata in separate `SceneVisualPlan` objects. It does not change `semantic_edit_plan.json`, scene order, image selection, or timing.

## Shot Library

The Shot Library assigns one visual intent: `establishing`, `wide`, `medium`, `portrait`, `detail`, `document`, `map`, or `archive`. It deterministically matches semantic-stage narration, image descriptions, match terms, keywords, and tags. Fixed rule order resolves ties. Its exact fallback is `medium`, confidence `0.50`, and reason `Default fallback.`

## Narrative Intent Engine

The Narrative Intent Engine assigns the function a scene serves: `introduction`, `context`, `explanation`, `development`, `escalation`, `reveal`, `climax`, `reflection`, or `conclusion`.

Classification uses only existing narration, image descriptions, match terms, keywords, tags, scene index, scene count, and relative position. Stable phrase and keyword scores are evaluated first with explicit rule-order tie-breaking. Position may favor an introduction, context, or conclusion only when textual evidence does not identify a stronger function. Identical input therefore produces identical output.

The exact narrative fallback is `development`, confidence `0.50`, and reason `Default fallback.`

Visual intent describes a suitable visual composition; narrative intent describes the scene's role in the story. They remain separate fields in `SceneVisualPlan`. Neither is inserted into semantic scene dictionaries or exported semantic data.

## Visual-to-Motion integration

Version 4.2.0 adds a deterministic translation from each `SceneVisualPlan` to separate `MotionGuidance`. Shot intent normally selects camera composition, while strong narrative moments (`escalation`, `reveal`, `climax`, and `conclusion`) take priority. The supported behaviors are existing Motion Engine presets: `safe_push_in`, `subject_push_in`, `focus_reveal`, `documentary_float`, and `slow_pull_out`.

Motion guidance may set a bounded intensity and settle hold. The Motion Engine continues to own detected focal coordinates, crop safety, zoom bounds, continuity, and rendering. It fails closed if guidance count or order differs from the authoritative scene sequence. With no guidance, the pre-4.2 subject-aware planner remains available.

The serialized motion plan records the applied visual intent, narrative intent, and guidance reason for inspection. Visual metadata is never inserted into semantic scene dictionaries.

## Current limitations

- Classification is conservative, lexical, and English-language oriented.
- It cannot infer meaning beyond supplied semantic text or inspect image pixels.
- Guidance is deterministic and rule-based; it does not inspect pixels or learn from rendered output.
- Pacing, transitions, captions, and image selection remain independent systems.

## Future integration

Future versions may combine shot and narrative classifications with pacing, scene importance, and image ranking. More camera behaviors may be added behind the same separate, fail-closed contract.
