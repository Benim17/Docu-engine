# Visual Director 4.1.2

The three planning layers have separate responsibilities:

- **Semantic Engine:** decides what a scene contains, including its image, narration context, and exact timeline.
- **Visual Director:** describes how a completed semantic scene should eventually be experienced.
- **Motion Engine:** executes camera instructions against the authoritative semantic timeline.

The Visual Director remains an identity/pass-through runtime boundary. It returns a validated deep copy of semantic scenes and keeps all visual metadata in separate `SceneVisualPlan` objects. It does not change `semantic_edit_plan.json`, create output artifacts, or influence rendering.

## Shot Library

The Shot Library assigns one visual intent: `establishing`, `wide`, `medium`, `portrait`, `detail`, `document`, `map`, or `archive`. It deterministically matches semantic-stage narration, image descriptions, match terms, keywords, and tags. Fixed rule order resolves ties. Its exact fallback is `medium`, confidence `0.50`, and reason `Default fallback.`

## Narrative Intent Engine

The Narrative Intent Engine assigns the function a scene serves: `introduction`, `context`, `explanation`, `development`, `escalation`, `reveal`, `climax`, `reflection`, or `conclusion`.

Classification uses only existing narration, image descriptions, match terms, keywords, tags, scene index, scene count, and relative position. Stable phrase and keyword scores are evaluated first with explicit rule-order tie-breaking. Position may favor an introduction, context, or conclusion only when textual evidence does not identify a stronger function. Identical input therefore produces identical output.

The exact narrative fallback is `development`, confidence `0.50`, and reason `Default fallback.`

Visual intent describes a suitable visual composition; narrative intent describes the scene's role in the story. They remain separate fields in `SceneVisualPlan`. Neither is inserted into semantic scene dictionaries or exported semantic data.

## Current limitations

- Classification is conservative, lexical, and English-language oriented.
- It cannot infer meaning beyond supplied semantic text or inspect image pixels.
- No renderer, motion, transition, caption, pacing, or image-selection system consumes either classification yet.

## Future integration

Future versions may combine shot and narrative classifications with pacing, scene importance, and image ranking. Visual-to-Motion integration may eventually translate those decisions into camera instructions, but requires separate contracts and regression testing and is not part of 4.1.2.
