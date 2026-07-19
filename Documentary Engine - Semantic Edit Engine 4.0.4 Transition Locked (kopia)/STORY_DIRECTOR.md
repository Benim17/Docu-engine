# Story Director 4.6.0

Story Director describes the documentary's existing narrative structure. It is a deterministic, metadata-only specialist: it never rewrites or reorders the story and never gives visual, motion, pacing, caption-style, transition, audio, or rendering instructions.

## Pipeline position

`Semantic Engine → Image Intelligence → Caption Director → Story Director → Visual Director → Motion Planner → Pacing Director → Motion Engine`

Version 4.6.0 creates `output/story_director_plan.json` after Caption Director and before Visual Director planning. No downstream module consumes Story Director metadata in this version. Any future integration is a separate sprint with a separate contract review.

## Inputs and independence

The minimal authoritative inputs are `semantic_edit_plan.json`, `captions.json`, and the `story_director` section of `config.json`. Captions are used only as a narration fallback when a semantic scene has insufficient text.

Story Director does not import, call, or inspect Visual Director, Motion Planner, Pacing Director, Motion Engine, the renderer, FFmpeg, external AI services, or the network. It has no reverse or circular dependencies.

## Output schema

The versioned top-level schema contains:

- `document_story`: story shape, opening strategy, central question, turning point, climax, resolution, tension curve, coherence, reason, confidence, and fallback state.
- `scenes`: exactly one ordered decision per existing semantic scene, with role, phase, bounded intensity/tension/density/revelation scores, continuity relation, story beats, reason, confidence, and fallback state.
- `story_graph.edges`: adjacent existing scenes only. Every edge preserves source order and explains its relationship.
- `diagnostics`: fallback and warning counts, stable warnings, and `deterministic: true`.

Example document analysis:

```json
{
  "story_shape": "investigation",
  "opening_strategy": "direct_question",
  "central_question": "What happened at the hospital?",
  "turning_point_scene": 3,
  "climax_scene": 5,
  "resolution_type": "open_question",
  "overall_tension_curve": [0.42, 0.58, 0.73],
  "story_coherence_score": 0.71,
  "reason": "Investigation selected from question and evidence signals.",
  "confidence": 0.76,
  "fallback_used": false
}
```

Example scene analysis:

```json
{
  "scene_id": "scene_002",
  "scene_index": 1,
  "story_role": "evidence",
  "story_phase": "rising_action",
  "emotional_intensity": 0.38,
  "tension": 0.61,
  "information_density": 0.72,
  "revelation_strength": 0.26,
  "continuity_relation": "adds_evidence",
  "story_beats": ["evidence"],
  "reason": "Scene 2/5; role evidence from relative position and evidence signals.",
  "confidence": 0.71,
  "fallback_used": false
}
```

## Determinism and fallback

Rules use only stable position, text, punctuation, lexical signals, information volume, adjacent-scene continuity, and existing semantic metadata. Scores are clamped to `0.0–1.0`. Fixed rule order and stable lexical sorting resolve ties. Serialization contains no timestamps, random IDs, absolute paths, or machine-specific values, so identical inputs produce byte-identical JSON.

Missing or invalid metadata produces a valid deterministic fallback plan. Each known scene still receives exactly one fallback decision. A project with no scenes receives an empty valid plan with diagnostics. Story Director file or planning failures are caught at the pipeline boundary, logged, and never stop rendering.

## Responsibility boundary

Story Director may describe a scene as `climax` with high tension. It may not request a zoom, caption color, pacing change, transition, image replacement, timing change, or narration edit. Visual Director's existing `narrative_intent` remains intact and independent in 4.6.0.
