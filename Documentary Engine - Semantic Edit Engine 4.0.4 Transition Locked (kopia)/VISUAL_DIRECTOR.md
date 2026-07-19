# Documentary Engine 4.7.0 architecture

The pipeline layers have separate responsibilities:

- **Semantic Engine:** decides scene order, narration context, exact timeline, and the candidate images available to each scene.
- **Image Intelligence:** inspects every available candidate, produces a deterministic weighted ranking, and selects one immutable source image.
- **Caption Director:** produces pure caption layout metadata without changing caption content or timing.
- **Story Director:** describes document structure and scene dramaturgy as metadata without directing visual expression.
- **Audio Director:** creates deterministic metadata for scene intent/tone, energy, abstract music, ambience, ducking, transitions, and intentional silence without producing or mixing audio.
- **Visual Director:** describes how a completed semantic scene should eventually be experienced.
- **Motion Planner:** selects the camera path between existing start and end positions.
- **Pacing Director:** deterministically shapes how progress evolves along that path.
- **Motion Engine:** renders the completed camera instructions against the authoritative semantic timeline.

The resulting order is:

`Semantic Engine → Image Intelligence → Caption Director → Story Director → Audio Director → render_video()`

Inside the existing render path, Visual Director, Motion Planner, Pacing Director, and Motion Engine retain their established order and responsibilities. Audio Director artifacts are metadata-only and are not passed to the renderer in 4.7.0.

## Image Intelligence 4.4.0

Image Intelligence is the only image-evaluation boundary. For every semantic scene it scores composition, technical quality, documentary suitability, caption compatibility, motion compatibility, and semantic compatibility. Scores are weighted and rounded deterministically; equal final scores are resolved by case-normalized filename and then the original filename. It writes a complete ranking, score breakdown, and fixed-format selection reasoning to `output/image_intelligence_plan.json`.

The module does not generate media or import, inspect, or depend on Visual Director, Motion Planner, Pacing Director, Motion Engine, captions, transitions, or render state. Once selected, the image is immutable downstream input. Downstream modules may be replaced without changing Image Intelligence.

## Caption Director 4.5.0

Caption Director is the single authority for caption layout decisions. It may decide position, normalized vertical anchor, safe margin, maximum width, maximum lines, deterministic word wrapping, conservative highlight words, config-driven highlight color, and readability metadata. Face avoidance has highest priority, followed by detected-subject avoidance, safe margins, reading comfort, placement stability, and conservative highlighting.

It reads only `semantic_edit_plan.json`, `captions.json`, `image_intelligence_plan.json`, `motion_analysis.json`, and `config.json`. It does not import or inspect Visual Director, Motion Planner, Pacing Director, Motion Engine, rendering code, FFmpeg, semantic implementation, or Image Intelligence implementation. Its complete scene-by-scene diagnostics are written to `output/caption_director_plan.json`.

The plan is metadata-only in 4.5.0. Existing caption text, word timestamps, caption start/end times, narration, semantic timeline, selected images, transitions, motion, pacing, and rendered content remain untouched. Invalid or missing optional metadata produces a deterministic fallback plan using the existing configured placement; it never blocks rendering.

## Story Director 4.6.0

Story Director is authoritative for the new detailed story analysis in `output/story_director_plan.json`. It describes whole-document shape, central question, turning point, climax, resolution, tension curve, per-scene story role and phase, continuity, beats, and an adjacent-scene story graph.

It reads only the minimal semantic plan, captions, and config metadata. It has no dependency on Visual Director, Motion Planner, Pacing Director, Motion Engine, rendering, or FFmpeg. It is deterministic and fail-closed. Version 4.6.0 is metadata-only: it does not alter narration, captions, scene order, timeline, images, motion, pacing, transitions, or visual output. Visual Director retains its existing `narrative_intent` and does not read Story Director data. Future integration requires a separate sprint.

The Visual Director remains an identity/pass-through boundary for completed semantic and image-selection data. It returns a validated deep copy of scenes and keeps all visual metadata in separate `SceneVisualPlan` objects. It does not change `semantic_edit_plan.json`, scene order, the immutable selected image, or timing.

## Audio Director 4.7.0

Audio Director writes `output/audio_plan.json` and `output/audio_diagnostics.json`. It consumes the semantic plan as the sole authority for scene identity, order, and timing; compatible Story Director metadata may strengthen decisions. Captions and motion plan files are optional, read-only adapter inputs. Motion metadata has no decision-making authority in 4.7.0.

The stage is deterministic, metadata-only, and fail-safe at the pipeline boundary. It does not import the renderer or FFmpeg, does not create audio, and does not modify narration, captions, semantic timing, images, visual planning, motion, pacing, or video output. See [AUDIO_DIRECTOR.md](AUDIO_DIRECTOR.md) for contracts, configuration, examples, and isolated use.

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

`Semantic Engine → Image Intelligence → Caption Director → Story Director → Audio Director → render_video()`

The renderer internally continues with `Visual Director → Motion Planner → Pacing Director → Motion Engine`; Audio Director does not alter that path.

The Motion Planner continues to own where the camera moves. The Pacing Director owns only how motion progresses between those unchanged start and end positions. It attaches `hold_fraction`, `ease_in_fraction`, `peak_fraction`, `ease_out_fraction`, `settle_fraction`, `speed_profile`, and easing metadata. The Motion Engine consumes this metadata as a renderer.

Narrative intent selects one of four fixed profiles: calm context, smooth introduction, dynamic development, or settled conclusion. Explanation follows context; escalation, reveal, and climax follow development; reflection follows conclusion. The speed curve integrates deterministic smoothstep acceleration and deceleration, so velocity starts and ends at zero. There is no randomness.

Pacing is fail-closed: start and end times, zoom endpoints, pan endpoints, motion preset, focal point, and visual/narrative intent are checked after decoration. Scene order, duration, images, captions, transitions, and audio remain outside the Pacing Director. Per-scene diagnostics expose both intents, motion preset, pacing profile, opening hold, easing profile, and separate motion/pacing reasoning.

## Current limitations

- Visual Director and Narrative Intent classification are conservative, lexical, and primarily English-language oriented.
- Visual Director cannot infer meaning beyond supplied semantic text or inspect image pixels; pixel inspection belongs only to Image Intelligence.
- Guidance is deterministic and rule-based; it does not inspect pixels or learn from rendered output.
- Story analysis, caption layout, pacing, visual transitions, caption content/timing, audio metadata, immutable image selection, and semantic timing remain independent systems.

## Future integration

Future versions may calibrate Image Intelligence weights without coupling it to downstream planning. More camera behaviors and pacing profiles may be added behind the same separate, fail-closed contracts.
