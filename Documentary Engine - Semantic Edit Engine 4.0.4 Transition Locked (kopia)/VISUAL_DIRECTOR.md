# Documentary Engine 4.5.0 visual architecture

The pipeline layers have separate responsibilities:

- **Semantic Engine:** decides scene order, narration context, exact timeline, and the candidate images available to each scene.
- **Image Intelligence:** inspects every available candidate, produces a deterministic weighted ranking, and selects one immutable source image.
- **Caption Director:** produces pure caption layout metadata without changing caption content or timing.
- **Visual Director:** describes how a completed semantic scene should eventually be experienced.
- **Motion Planner:** selects the camera path between existing start and end positions.
- **Pacing Director:** deterministically shapes how progress evolves along that path.
- **Motion Engine:** renders the completed camera instructions against the authoritative semantic timeline.

The resulting order is:

`Semantic Engine → Image Intelligence → Caption Director → Visual Director → Motion Planner → Pacing Director → Motion Engine`

## Image Intelligence 4.4.0

Image Intelligence is the only image-evaluation boundary. For every semantic scene it scores composition, technical quality, documentary suitability, caption compatibility, motion compatibility, and semantic compatibility. Scores are weighted and rounded deterministically; equal final scores are resolved by case-normalized filename and then the original filename. It writes a complete ranking, score breakdown, and fixed-format selection reasoning to `output/image_intelligence_plan.json`.

The module does not generate media or import, inspect, or depend on Visual Director, Motion Planner, Pacing Director, Motion Engine, captions, transitions, or render state. Once selected, the image is immutable downstream input. Downstream modules may be replaced without changing Image Intelligence.

## Caption Director 4.5.0

Caption Director is the single authority for caption layout decisions. It may decide position, normalized vertical anchor, safe margin, maximum width, maximum lines, deterministic word wrapping, conservative highlight words, config-driven highlight color, and readability metadata. Face avoidance has highest priority, followed by detected-subject avoidance, safe margins, reading comfort, placement stability, and conservative highlighting.

It reads only `semantic_edit_plan.json`, `captions.json`, `image_intelligence_plan.json`, `motion_analysis.json`, and `config.json`. It does not import or inspect Visual Director, Motion Planner, Pacing Director, Motion Engine, rendering code, FFmpeg, semantic implementation, or Image Intelligence implementation. Its complete scene-by-scene diagnostics are written to `output/caption_director_plan.json`.

The plan is metadata-only in 4.5.0. Existing caption text, word timestamps, caption start/end times, narration, semantic timeline, selected images, transitions, motion, pacing, and rendered content remain untouched. Invalid or missing optional metadata produces a deterministic fallback plan using the existing configured placement; it never blocks rendering.

The Visual Director remains an identity/pass-through boundary for completed semantic and image-selection data. It returns a validated deep copy of scenes and keeps all visual metadata in separate `SceneVisualPlan` objects. It does not change `semantic_edit_plan.json`, scene order, the immutable selected image, or timing.

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

## Pacing Director

Version 4.3.0 inserts a separate Pacing Director after the Motion Planner and before the Motion Engine:

`Semantic Engine → Image Intelligence → Caption Director → Visual Director → Motion Planner → Pacing Director → Motion Engine`

The Motion Planner continues to own where the camera moves. The Pacing Director owns only how motion progresses between those unchanged start and end positions. It attaches `hold_fraction`, `ease_in_fraction`, `peak_fraction`, `ease_out_fraction`, `settle_fraction`, `speed_profile`, and easing metadata. The Motion Engine consumes this metadata as a renderer.

Narrative intent selects one of four fixed profiles: calm context, smooth introduction, dynamic development, or settled conclusion. Explanation follows context; escalation, reveal, and climax follow development; reflection follows conclusion. The speed curve integrates deterministic smoothstep acceleration and deceleration, so velocity starts and ends at zero. There is no randomness.

Pacing is fail-closed: start and end times, zoom endpoints, pan endpoints, motion preset, focal point, and visual/narrative intent are checked after decoration. Scene order, duration, images, captions, transitions, and audio remain outside the Pacing Director. Per-scene diagnostics expose both intents, motion preset, pacing profile, opening hold, easing profile, and separate motion/pacing reasoning.

## Current limitations

- Classification is conservative, lexical, and English-language oriented.
- Visual Director cannot infer meaning beyond supplied semantic text or inspect image pixels; pixel inspection belongs only to Image Intelligence.
- Guidance is deterministic and rule-based; it does not inspect pixels or learn from rendered output.
- Caption layout, pacing, transitions, caption content/timing, audio, immutable image selection, and semantic timing remain independent systems.

## Future integration

Future versions may calibrate Image Intelligence weights without coupling it to downstream planning. More camera behaviors and pacing profiles may be added behind the same separate, fail-closed contracts.
