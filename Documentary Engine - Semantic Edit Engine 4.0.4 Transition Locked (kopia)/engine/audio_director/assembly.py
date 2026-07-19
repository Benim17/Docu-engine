from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .director import _canonical_strings, _clamp
from .models import (
    AudioContractError, AudioDiagnostics, AudioPlan, ProjectAudioDiagnostics,
    ProjectAudioSummary, SceneAudioDiagnostics, SceneAudioPlan,
)
from .sound_planning import ProjectSoundAnalysis


FALLBACK_DOMINANCE_THRESHOLD = 0.50
FLAT_ENERGY_THRESHOLD = 0.05
EXTREME_ENERGY_LOW = 0.10
EXTREME_ENERGY_HIGH = 0.90


@dataclass(frozen=True)
class AssembledAudioArtifacts:
    plan: AudioPlan
    diagnostics: AudioDiagnostics


def assemble_audio_artifacts(sound: ProjectSoundAnalysis) -> AssembledAudioArtifacts:
    """Assemble validated public artifacts from one final internal analysis."""
    if len(sound.scenes) != sound.energy_plan.project_summary.scene_count:
        raise AudioContractError("Final internal scene count differs from energy planning.")
    if tuple(item.energy_music for item in sound.scenes) != sound.energy_plan.scenes:
        raise AudioContractError("Final internal scenes do not match energy planning order.")
    internal_ids = tuple(item.energy_music.scene.scene_id for item in sound.scenes)
    if internal_ids != sound.semantic_scene_ids:
        raise AudioContractError("Final internal scene identity or order differs from semantic input.")
    scene_plans = []
    scene_diagnostics = []
    for final in sound.scenes:
        source = final.energy_music.scene
        missing = ("story_director_plan",) if source.story_role is None else ()
        signals = _canonical_strings((
            *source.accepted_signals,
            *final.source_signals,
        ))
        warnings = _canonical_strings((*final.energy_music.warnings, *final.warnings))
        diagnostics = SceneAudioDiagnostics(
            confidence=source.confidence,
            warnings=warnings,
            fallback_used=source.fallback_used,
            fallback_reason=source.fallback_reason,
            missing_inputs=missing,
            source_signals=signals,
            resolved_conflicts=source.resolved_conflicts,
        )
        scene_diagnostics.append(diagnostics)
        scene_plans.append(SceneAudioPlan(
            scene_id=source.scene_id,
            scene_index=source.scene_index,
            start=source.start,
            end=source.end,
            duration=source.duration,
            audio_intent=source.audio_intent,
            emotional_tone=source.emotional_tone,
            energy=final.energy_music.energy,
            music=final.music,
            ambience=final.ambience,
            ducking=final.ducking,
            transition_in=final.transition_in,
            transition_out=final.transition_out,
            silence=final.silence,
            diagnostics=diagnostics,
        ))

    scenes = tuple(scene_plans)
    diagnostics_scenes = tuple(scene_diagnostics)
    count = len(scenes)
    fallback_count = sum(item.fallback_used for item in diagnostics_scenes)
    fallback_dominant = bool(count and fallback_count / count > FALLBACK_DOMINANCE_THRESHOLD)
    status = "fallback" if fallback_dominant else "planned"
    curve = tuple(scene.energy for scene in scenes)
    styles = tuple(scene.music.style for scene in scenes)
    project_warnings = set(sound.diagnostics.warnings)
    for diagnostic in diagnostics_scenes:
        project_warnings.update(diagnostic.warnings)
    if fallback_dominant:
        project_warnings.add("fallback_dominant_audio_plan")
    rejected_optional = any(
        warning.startswith("story_director_") and warning != "story_director_plan_missing"
        for warning in project_warnings
    )
    mean_confidence = (
        sum(item.confidence for item in diagnostics_scenes) / count if count else 0.0
    )
    confidence = mean_confidence
    if fallback_dominant:
        confidence -= 0.10
    if rejected_optional:
        confidence -= 0.05
    confidence = _clamp(confidence)
    missing_inputs = _canonical_strings(
        value for item in diagnostics_scenes for value in item.missing_inputs
    )
    conflicts = _canonical_strings(
        value for item in diagnostics_scenes for value in item.resolved_conflicts
    )
    project = ProjectAudioDiagnostics(
        confidence=confidence,
        warnings=_canonical_strings(project_warnings),
        fallback_count=fallback_count,
        missing_inputs=missing_inputs,
        resolved_conflicts=conflicts,
        flat_energy_curve=(not curve or max(curve) - min(curve) <= FLAT_ENERGY_THRESHOLD),
        extreme_energy_count=sum(
            energy <= EXTREME_ENERGY_LOW or energy >= EXTREME_ENERGY_HIGH
            for energy in curve
        ),
        music_style_change_count=sum(left != right for left, right in zip(styles, styles[1:])),
        unsupported_aggressive_transition_count=sound.diagnostics.unsupported_aggressive_transition_count,
        ambience_scene_count=sum(scene.ambience.enabled for scene in scenes),
        scene_without_usable_input_count=sum(
            not (
                final.energy_music.scene.accepted_signals
                or final.source_signals
                or final.narration_present
            )
            for final in sound.scenes
        ),
        fallback_dominant=fallback_dominant,
    )
    final_styles = _default_music_style(styles)
    summary = ProjectAudioSummary(
        dominant_tone=_dominant_tone(scenes),
        default_music_style=final_styles,
        energy_curve=curve,
        scene_count=count,
        target_loudness_lufs=-14.0,
    )
    plan = AudioPlan(summary, scenes, status=status)
    diagnostics = AudioDiagnostics(project, diagnostics_scenes)
    _validate_consistency(plan, diagnostics, sound.semantic_scene_ids)
    return AssembledAudioArtifacts(plan, diagnostics)


def _default_music_style(styles: Sequence[str]) -> str:
    if not styles:
        return "documentary"
    order = (
        "documentary", "ambient", "minimal", "emotional_piano", "cinematic",
        "suspense", "orchestral", "none",
    )
    counts = {style: styles.count(style) for style in order}
    return min(order, key=lambda style: (-counts[style], order.index(style)))


def _dominant_tone(scenes: Sequence[SceneAudioPlan]) -> str:
    if not scenes:
        return "neutral"
    order = (
        "neutral", "calm", "reflective", "mysterious", "tense", "somber",
        "hopeful", "dramatic", "uplifting", "triumphant",
    )
    totals = {
        tone: (
            round(sum(scene.diagnostics.confidence for scene in scenes if scene.emotional_tone == tone), 4),
            sum(scene.emotional_tone == tone for scene in scenes),
        )
        for tone in order
    }
    return min(order, key=lambda tone: (-totals[tone][0], -totals[tone][1], order.index(tone)))


def _validate_consistency(
    plan: AudioPlan,
    diagnostics: AudioDiagnostics,
    semantic_scene_ids: Sequence[str],
) -> None:
    plan.validate()
    diagnostics.validate()
    if len(plan.scenes) != len(diagnostics.scenes):
        raise AudioContractError("Audio plan and diagnostics scene counts differ.")
    if tuple(scene.scene_id for scene in plan.scenes) != tuple(semantic_scene_ids):
        raise AudioContractError("Audio plan scene identity differs from semantic input.")
    for index, (scene, diagnostic) in enumerate(zip(plan.scenes, diagnostics.scenes)):
        if scene.diagnostics != diagnostic:
            raise AudioContractError(f"Plan and standalone diagnostics differ at index {index}.")
        if index and plan.scenes[index - 1].transition_out != scene.transition_in:
            raise AudioContractError(f"Transition boundary mismatch at index {index}.")
        if scene.silence.intentional_silence and (
            scene.music.enabled or scene.ambience.enabled or scene.ducking.ducking_enabled
        ):
            raise AudioContractError(f"Intentional silence conflicts with enabled layers at index {index}.")
    if plan.project_summary.energy_curve != tuple(scene.energy for scene in plan.scenes):
        raise AudioContractError("Final energy curve differs from scene energies.")
