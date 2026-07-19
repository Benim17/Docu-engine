# Visual Director Foundation 4.1.0

The three planning layers have separate responsibilities:

- **Semantic Engine:** decides what a scene contains, including its image, narration context, and exact timeline.
- **Visual Director:** will eventually describe how a completed semantic scene should be experienced.
- **Motion Engine:** executes camera instructions such as zoom and pan against the authoritative semantic timeline.

Version 4.1.0 introduces the Visual Director only as an identity/pass-through orchestration boundary. It creates neutral, separate `SceneVisualPlan` objects for future use and returns a validated deep copy of the semantic scenes. It does not add fields to semantic scenes, change `semantic_edit_plan.json`, create output artifacts, influence motion or transitions, or change rendered pixels.
