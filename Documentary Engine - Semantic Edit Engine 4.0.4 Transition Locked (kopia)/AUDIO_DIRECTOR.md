# Audio Director 4.7.0

## Overview

Audio Director is a deterministic, metadata-only planning stage. It classifies scene audio intent and emotional tone, builds an energy curve, selects abstract music styles, plans ambience, narration ducking, scene transitions, and intentional silence, and exposes its reasoning through diagnostics.

It does not download or generate music, select real tracks, synthesize ambience, modify voiceover, mix audio, inspect waveforms, call FFmpeg, or use external AI or network services. Version 4.7.0 does not affect rendered video or audio.

## Pipeline position

`Semantic Engine → Image Intelligence → Caption Director → Story Director → Audio Director → render_video()`

Audio Director runs after Story Director has had an opportunity to publish metadata and before rendering begins. Its artifacts are not passed to `render_video()` in 4.7.0. A planning or publication failure is caught at the pipeline boundary and rendering continues unchanged.

## Inputs

The authoritative required input is `output/semantic_edit_plan.json`. It alone defines scene count, order, ID, start, end, and duration. Missing, unreadable, malformed, or contract-invalid semantic input fails closed and produces no ordinary fallback artifact.

Optional inputs are:

- `output/story_director_plan.json`
- `output/captions.json`
- `output/motion_plan.json`

Story Director metadata is accepted only for the supported `4.6.0` schema/planner pair and an exact scene count, ID, index, and available timing match. A motion plan is read-only and requires exact scene count, ID, index, and timing; it has no decision-making authority in 4.7.0. Missing, corrupt, stale, or incompatible optional inputs are ignored with explicit diagnostics.

Fail-closed means the required semantic contract is unusable and no new artifacts are published. Fallback means semantic input is valid and every scene remains plannable, but more than half of the scenes rely primarily on documented defaults because usable evidence is insufficient.

## Outputs

Successful planning publishes:

- `output/audio_plan.json`: project summary and final scene decisions
- `output/audio_diagnostics.json`: project and scene diagnostics from those same decisions

`audio_plan.json` contains schema/planner versions, status, dominant tone, default abstract music style, ordered energy curve, future target-loudness metadata, and every final scene plan. Scene plans contain immutable semantic identity/timing, intent, tone, energy, music, ambience, ducking, transitions, silence, and compact diagnostics.

`audio_diagnostics.json` contains project confidence, canonical warnings, fallback and missing-input information, curve/style/ambience/transition counters, and the ordered scene diagnostics. `scenes[i]` corresponds to `audio_plan.json.scenes[i]`; assembly verifies semantic order and identity before either artifact is built.

## Determinism

Identical input and config produce byte-identical JSON. Public serialization uses stable key ordering, UTF-8, a terminal newline, finite values rounded to four decimals, and canonically sorted diagnostic collections. Artifacts contain no timestamps, filesystem paths, branches, machine names, random values, or debug objects.

## Energy planning

Neutral base energy is `0.40`. Calculation order is base, intent adjustment, tone adjustment, bounded compatible Story Director values, clamp to `0.0–1.0`, four-decimal rounding, and one forward smoothing pass.

| Audio intent | Adjustment |
|---|---:|
| neutral, support | 0.00 |
| establish | -0.04 |
| reflection | -0.12 |
| release | -0.08 |
| transition | -0.02 |
| build | +0.12 |
| tension | +0.22 |
| resolution | +0.02 |
| climax | +0.38 |

| Emotional tone | Adjustment |
|---|---:|
| neutral | 0.00 |
| calm | -0.08 |
| reflective | -0.07 |
| somber | -0.05 |
| mysterious | +0.04 |
| hopeful | +0.03 |
| uplifting | +0.08 |
| tense, dramatic | +0.10 |
| triumphant | +0.12 |

Validated structural values contribute:

- `(tension - 0.5) × 0.18`
- `(emotional_intensity - 0.5) × 0.12`
- `revelation_strength × 0.06`

The default `max_energy_delta` is `0.30`. It can be configured from `0.01` through `0.45`. A structurally validated climax may preserve a larger rise up to the absolute `0.45` safety boundary when its role and phase are climax, tension is at least `0.70`, and confidence is at least `0.75`. Text alone cannot enable this exception.

The forward pass also limits abrupt downward changes. A raw release at `0.30` after energy `0.90` becomes `0.60` with the default delta. This intentionally preserves audio continuity while still making release lower than climax; following resolution is prevented from becoming a new accidental rise.

## Music

Music styles are abstract metadata: `none`, `documentary`, `ambient`, `cinematic`, `minimal`, `emotional_piano`, `orchestral`, `hybrid`, `electronic`, `historical`, `nature`, and `suspense`.

Weak or informational evidence selects `documentary`. Calm/reflection selects `ambient` or `minimal`; somber material may select `emotional_piano`. Suspense and cinematic/orchestral choices require compatible structural support. A topic word alone never selects historical, nature, or electronic music.

Music intensity starts at `final_energy × 0.72`, subtracts `0.08` for support/neutral/establish intent, and adds `0.08` for supported climax. It is clamped and rounded. Thus style and intensity use final smoothed energy, not raw energy.

## Ambience

Ambience requires explicit validated environment metadata plus support or at least three aligned semantic signals. A single word such as “rain” or “regn” is insufficient. Unknown environments produce disabled `none`, never `generic_environment`. Battlefield requires an explicit type and at least two strong aligned signals.

Enabled intensity is bounded by `min(energy × 0.45, 0.35)`, reduced by `0.08` when narration is present, never negative, and never greater than scene energy. Zero usable intensity disables the layer. Intentional silence suppresses previously supported ambience and records the distinction in fallback reasoning and diagnostics.

## Ducking

Ducking is future mixing metadata only. Normal narrated defaults are music `-12 dB`, ambience `-6 dB`, attack `120 ms`, and release `500 ms`. Dense narration uses `-14 dB` music and `-8 dB` ambience. Disabled layers receive `0.0 dB` reduction. Missing narration, intentional silence, or no enabled layers disables ducking with stable zero attack/release values.

## Transitions

Each internal scene boundary is planned once and the same value becomes scene A's `transition_out` and scene B's `transition_in`. Deterministic priority is:

`silence → impact → hard_cut → riser → ambient_bridge → crossfade`

The external first entry is `fade_in`; the external final exit is `fade_out`. Impact, hard cut, and riser require structural support and can be disabled by config. Intentional silence remains a separate dramaturgical decision. Compatible ambience may create an ambient bridge only after stronger supported decisions have been considered.

## Intentional silence

Silence requires both explicit `intentional_pause` metadata and compatible structural support such as epilogue, revelation, resolution, or climax with sufficient intensity/revelation and confidence. Reflection or a pause word alone is insufficient.

Pre-scene silence is at most `300 ms`; post-scene silence is at most `400 ms`. It suppresses music and ambience and disables ducking. These values are future mixing metadata and never change semantic timestamps.

## Fallback and diagnostics

A conservative evidence-based support scene may legitimately use neutral tone and documentary music without being fallback. A scene is fallback only when its principal decisions use defaults because usable structural, semantic, or text evidence is insufficient.

- fallback dominant: `fallback_count / scene_count > 0.50`
- exactly 50 percent: `planned`
- empty project: `planned`, confidence `0.0`, flat curve
- flat curve: empty/single scene or range at most `0.05`
- extreme energy: at most `0.10` or at least `0.90`
- style changes: adjacent final music-style changes
- ambience count: final enabled ambience after silence
- unsupported aggressive transitions: downgraded attempts only
- scene without usable input: no accepted decision/environment signal and no narration

Project confidence is the four-decimal clamped mean scene confidence, minus `0.10` for fallback dominance and once-only `0.05` for rejected optional Story Director metadata.

## Configuration

```json
{
  "audio_director": {
    "enabled": true,
    "plan_json": "output/audio_plan.json",
    "diagnostics_json": "output/audio_diagnostics.json",
    "target_loudness_lufs": -14.0,
    "max_energy_delta": 0.3,
    "allow_aggressive_transitions": true
  }
}
```

| Field | Type | Default | Valid values and effect |
|---|---|---|---|
| `enabled` | boolean | `true` | `false` performs no reads/writes and preserves old outputs |
| `plan_json` | string | `output/audio_plan.json` | relative file path within project root |
| `diagnostics_json` | string | `output/audio_diagnostics.json` | distinct relative file path within project root |
| `target_loudness_lufs` | number | `-14.0` | `-40.0…-5.0`; metadata only |
| `max_energy_delta` | number | `0.30` | `0.01…0.45`; final smoothing only |
| `allow_aggressive_transitions` | boolean | `true` | disables impact, hard cut, and riser when false |

Absolute paths, parent traversal, paths resolving outside the project root, symlink escapes, directories, empty paths, and two paths resolving to the same target are rejected. Nested relative directories are supported.

## Rollback-protected two-file publication

Both artifacts are validated and serialized before publication. Two tempfiles on the target filesystem are written, flushed, `fsync`ed, and closed before ordered `os.replace` operations. Two replaces are not a single atomic transaction, so publication is accurately described as rollback-protected rather than fully atomic.

If diagnostics publication fails after plan publication, the old plan is restored or the new plan is removed when no old file existed. Old diagnostics remains untouched. If rollback itself fails, the error retains distinct publish/rollback causes, the new plan is removed when possible, and a recovery tempfile containing the old plan is retained when available. Disabled mode is a no-op; planning failure preserves the old complete pair.

## Isolated use

```python
from pathlib import Path
from engine.audio_director import write_audio_director_outputs

result = write_audio_director_outputs(Path("project"), config)
if result is None:
    print("Audio Director is disabled.")
else:
    print(result.plan_path)
    print(result.diagnostics_path)
```

No video render or separate CLI is required.

## Complete two-scene examples

The following examples are exercised by `tests/test_audio_director_docs.py` against the public model contract.

### audio_plan.json example

<!-- audio-plan-example -->
```json
{
  "schema_version": "1.0",
  "planner_version": "4.7.0",
  "deterministic": true,
  "status": "planned",
  "project_summary": {"dominant_tone": "calm", "default_music_style": "documentary", "energy_curve": [0.32, 0.45], "scene_count": 2, "target_loudness_lufs": -14.0},
  "scene_count": 2,
  "scenes": [
    {
      "scene_id": "scene_001", "scene_index": 0, "start": 0.0, "end": 5.0, "duration": 5.0,
      "audio_intent": "support", "emotional_tone": "calm", "energy": 0.32,
      "music": {"enabled": true, "style": "ambient", "intensity": 0.1504, "rationale": "Style ambient from intent support, tone calm, energy 0.3200."},
      "ambience": {"enabled": false, "type": "none", "intensity": 0.0, "confidence": 0.0, "source_basis": [], "fallback_reason": "Environment evidence did not reach the conservative threshold."},
      "ducking": {"ducking_enabled": true, "music_reduction_db": -12.0, "ambience_reduction_db": 0.0, "attack_ms": 120, "release_ms": 500, "rationale": "Metadata ducking protects narration readability."},
      "transition_in": {"type": "fade_in", "duration_ms": 800, "rationale": "First scene entry."},
      "transition_out": {"type": "crossfade", "duration_ms": 800, "rationale": "Conservative scene boundary."},
      "silence": {"pre_scene_silence_ms": 0, "post_scene_silence_ms": 0, "music_suppressed": false, "ambience_suppressed": false, "intentional_silence": false, "rationale": "No explicit structurally supported pause."},
      "diagnostics": {"confidence": 0.385, "warnings": ["story_director_plan_missing"], "fallback_used": false, "fallback_reason": null, "missing_inputs": ["story_director_plan"], "source_signals": ["semantic_intent:context", "text_calm:calm", "text_calm:quiet", "text_calm:steady"], "resolved_conflicts": [], "deterministic": true, "planner_version": "4.7.0"}
    },
    {
      "scene_id": "scene_002", "scene_index": 1, "start": 5.0, "end": 10.0, "duration": 5.0,
      "audio_intent": "resolution", "emotional_tone": "hopeful", "energy": 0.45,
      "music": {"enabled": true, "style": "documentary", "intensity": 0.324, "rationale": "Style documentary from intent resolution, tone hopeful, energy 0.4500."},
      "ambience": {"enabled": false, "type": "none", "intensity": 0.0, "confidence": 0.0, "source_basis": [], "fallback_reason": "Environment evidence did not reach the conservative threshold."},
      "ducking": {"ducking_enabled": true, "music_reduction_db": -12.0, "ambience_reduction_db": 0.0, "attack_ms": 120, "release_ms": 500, "rationale": "Metadata ducking protects narration readability."},
      "transition_in": {"type": "crossfade", "duration_ms": 800, "rationale": "Conservative scene boundary."},
      "transition_out": {"type": "fade_out", "duration_ms": 800, "rationale": "Final scene exit."},
      "silence": {"pre_scene_silence_ms": 0, "post_scene_silence_ms": 0, "music_suppressed": false, "ambience_suppressed": false, "intentional_silence": false, "rationale": "No explicit structurally supported pause."},
      "diagnostics": {"confidence": 0.385, "warnings": ["story_director_plan_missing"], "fallback_used": false, "fallback_reason": null, "missing_inputs": ["story_director_plan"], "source_signals": ["semantic_intent:conclusion", "text_hopeful:future", "text_hopeful:hope", "text_hopeful:possible", "text_hopeful:recovery"], "resolved_conflicts": [], "deterministic": true, "planner_version": "4.7.0"}
    }
  ]
}
```

### audio_diagnostics.json example

<!-- audio-diagnostics-example -->
```json
{
  "schema_version": "1.0", "planner_version": "4.7.0", "deterministic": true, "scene_count": 2,
  "project": {
    "confidence": 0.385,
    "warnings": ["ambience_not_supported_for_any_scene", "story_director_plan_missing"],
    "fallback_count": 0,
    "missing_inputs": ["story_director_plan"],
    "resolved_conflicts": [],
    "flat_energy_curve": false,
    "extreme_energy_count": 0,
    "music_style_change_count": 1,
    "unsupported_aggressive_transition_count": 0,
    "ambience_scene_count": 0,
    "scene_without_usable_input_count": 0,
    "fallback_dominant": false,
    "deterministic": true,
    "planner_version": "4.7.0"
  },
  "scenes": [
    {"confidence": 0.385, "warnings": ["story_director_plan_missing"], "fallback_used": false, "fallback_reason": null, "missing_inputs": ["story_director_plan"], "source_signals": ["semantic_intent:context", "text_calm:calm", "text_calm:quiet", "text_calm:steady"], "resolved_conflicts": [], "deterministic": true, "planner_version": "4.7.0"},
    {"confidence": 0.385, "warnings": ["story_director_plan_missing"], "fallback_used": false, "fallback_reason": null, "missing_inputs": ["story_director_plan"], "source_signals": ["semantic_intent:conclusion", "text_hopeful:future", "text_hopeful:hope", "text_hopeful:possible", "text_hopeful:recovery"], "resolved_conflicts": [], "deterministic": true, "planner_version": "4.7.0"}
  ]
}
```

## Known limitations and future work

Planning is conservative and rule-based. It does not inspect audio, choose tracks, measure LUFS, synchronize beats, master audio, or enforce copyright. A future mixing stage may consume this metadata without changing Audio Director's upstream-independent planning contract.
