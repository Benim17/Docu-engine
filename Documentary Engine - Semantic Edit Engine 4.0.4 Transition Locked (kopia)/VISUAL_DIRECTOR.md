# Visual Director Shot Library 4.1.1

The three planning layers have separate responsibilities:

- **Semantic Engine:** decides what a scene contains, including its image, narration context, and exact timeline.
- **Visual Director:** will eventually describe how a completed semantic scene should be experienced.
- **Motion Engine:** executes camera instructions such as zoom and pan against the authoritative semantic timeline.

The Visual Director remains an identity/pass-through orchestration boundary. It returns a validated deep copy of the semantic scenes and keeps all visual metadata in separate `SceneVisualPlan` objects. It does not add fields to semantic scenes, change `semantic_edit_plan.json`, create output artifacts, influence motion or transitions, or change rendered pixels.

## Shot Library

Version 4.1.1 classifies each completed semantic scene with one supported visual intent:

- `establishing`
- `wide`
- `medium`
- `portrait`
- `detail`
- `document`
- `map`
- `archive`

Classification uses deterministic keyword matching over semantic-stage narration, image descriptions, match terms, keywords, and tags. The intent with the most matching indicators wins; fixed rule order resolves ties. Confidence increases with the number of distinct indicators. Scenes without a match use `medium`, confidence `0.50`, and reason `Default fallback.`

## Current limitations

- Classification is intentionally lexical and cannot infer meaning beyond supplied semantic text.
- Visual intent is diagnostic only and is not exported into the semantic plan.
- No renderer, camera, transition, caption, pacing, or image-selection system consumes the result yet.

## Roadmap

Future releases may use visual intent to inform camera movement, pacing, image selection, and cinematic direction. Those integrations require separate contracts and regression testing; they are explicitly outside 4.1.1.
