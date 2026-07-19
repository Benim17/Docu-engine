from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from .director import AudioDirector
from .models import AudioContractError, AudioDiagnostics, AudioPlan, serialize_audio_artifact


class AudioDirectorIOError(AudioContractError):
    """Raised when Audio Director cannot safely read or publish artifacts."""

    def __init__(self, message: str, *, publish_error=None, rollback_error=None) -> None:
        super().__init__(message)
        self.publish_error = publish_error
        self.rollback_error = rollback_error


@dataclass(frozen=True)
class AudioDirectorSettings:
    enabled: bool = True
    plan_json: str = "output/audio_plan.json"
    diagnostics_json: str = "output/audio_diagnostics.json"
    target_loudness_lufs: float = -14.0
    max_energy_delta: float = 0.30
    allow_aggressive_transitions: bool = True


@dataclass(frozen=True)
class AudioDirectorWriteResult:
    plan_path: Path
    diagnostics_path: Path
    plan: AudioPlan
    diagnostics: AudioDiagnostics
    input_warnings: tuple[str, ...]


def parse_audio_director_settings(config: Mapping[str, Any]) -> AudioDirectorSettings:
    raw = config.get("audio_director", {})
    if not isinstance(raw, Mapping):
        raise AudioDirectorIOError("audio_director config must be an object.")
    enabled = raw.get("enabled", True)
    aggressive = raw.get("allow_aggressive_transitions", True)
    if not isinstance(enabled, bool) or not isinstance(aggressive, bool):
        raise AudioDirectorIOError("Audio Director boolean settings must be boolean.")
    plan_json = raw.get("plan_json", "output/audio_plan.json")
    diagnostics_json = raw.get("diagnostics_json", "output/audio_diagnostics.json")
    if not isinstance(plan_json, str) or not plan_json.strip():
        raise AudioDirectorIOError("audio_director.plan_json must be a non-empty string.")
    if not isinstance(diagnostics_json, str) or not diagnostics_json.strip():
        raise AudioDirectorIOError("audio_director.diagnostics_json must be a non-empty string.")
    if plan_json == diagnostics_json:
        raise AudioDirectorIOError("Audio plan and diagnostics paths must be different.")
    loudness_raw = raw.get("target_loudness_lufs", -14.0)
    delta_raw = raw.get("max_energy_delta", 0.30)
    if isinstance(loudness_raw, bool) or not isinstance(loudness_raw, (int, float)):
        raise AudioDirectorIOError("target_loudness_lufs must be numeric.")
    if isinstance(delta_raw, bool) or not isinstance(delta_raw, (int, float)):
        raise AudioDirectorIOError("max_energy_delta must be numeric.")
    loudness, delta = float(loudness_raw), float(delta_raw)
    if not -40.0 <= loudness <= -5.0:
        raise AudioDirectorIOError("target_loudness_lufs must be between -40.0 and -5.0.")
    if not 0.01 <= delta <= 0.45:
        raise AudioDirectorIOError("max_energy_delta must be between 0.01 and 0.45.")
    return AudioDirectorSettings(enabled, plan_json, diagnostics_json, loudness, delta, aggressive)


def write_audio_director_outputs(
    root: Path,
    config: Mapping[str, Any],
) -> AudioDirectorWriteResult | None:
    """Read approved metadata and atomically publish both public artifacts."""
    settings = parse_audio_director_settings(config)
    if not settings.enabled:
        return None
    plan_path = _resolve_output_path(root, settings.plan_json)
    diagnostics_path = _resolve_output_path(root, settings.diagnostics_json)
    if plan_path == diagnostics_path:
        raise AudioDirectorIOError("Audio plan and diagnostics resolve to the same path.")
    semantic_path = root / str(config.get("semantic_edit_engine", {}).get("plan_json", "output/semantic_edit_plan.json"))
    story_path = root / str(config.get("story_director", {}).get("plan_json", "output/story_director_plan.json"))
    captions_path = root / str(config.get("captions_json", "output/captions.json"))
    motion_path = root / str(config.get("motion_engine", {}).get("plan_json", "output/motion_plan.json"))
    semantic = _read_required_object(semantic_path)
    story, story_warning = _read_optional_object(story_path, "story_director")
    captions, captions_warning = _read_optional_object(captions_path, "captions")
    motion, motion_warning = _read_optional_object(motion_path, "motion_plan")
    warnings = [value for value in (story_warning, captions_warning, motion_warning) if value]
    if story is not None and not _story_shape_usable(story):
        warnings.append("story_director_optional_metadata_invalid")
        story = None
    if motion is not None:
        motion_issue = _scene_alignment_issue(semantic, motion)
        if motion_issue:
            warnings.append(motion_issue)
    if captions is not None and not isinstance(captions.get("captions", []), list):
        warnings.append("captions_optional_metadata_invalid")

    director = AudioDirector(
        target_loudness_lufs=settings.target_loudness_lufs,
        max_energy_delta=settings.max_energy_delta,
        allow_aggressive_transitions=settings.allow_aggressive_transitions,
    )
    artifacts = director.build_audio_artifacts(semantic, story)
    diagnostics = _add_input_warnings(artifacts.diagnostics, tuple(warnings))
    plan = replace(
        artifacts.plan,
        scenes=tuple(
            replace(scene, diagnostics=diagnostics.scenes[index])
            for index, scene in enumerate(artifacts.plan.scenes)
        ),
    )
    plan.validate()
    diagnostics.validate()
    plan_bytes = serialize_audio_artifact(plan).encode("utf-8")
    diagnostic_bytes = serialize_audio_artifact(diagnostics).encode("utf-8")
    _publish_pair(plan_path, plan_bytes, diagnostics_path, diagnostic_bytes)
    return AudioDirectorWriteResult(
        plan_path, diagnostics_path, plan, diagnostics, tuple(sorted(set(warnings))),
    )


def _read_required_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise AudioDirectorIOError(f"Required semantic input is missing: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AudioDirectorIOError(f"Required semantic input is unreadable: {path.name}") from exc
    if not isinstance(value, dict):
        raise AudioDirectorIOError(f"Required semantic input must be an object: {path.name}")
    return value


def _resolve_output_path(root: Path, configured: str) -> Path:
    relative = Path(configured)
    if relative.is_absolute():
        raise AudioDirectorIOError("Audio output paths must be relative to the project root.")
    root_resolved = root.resolve()
    candidate = (root_resolved / relative).resolve(strict=False)
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise AudioDirectorIOError("Audio output path escapes the project root.") from exc
    if candidate.exists() and candidate.is_dir():
        raise AudioDirectorIOError("Audio output path must identify a file, not a directory.")
    return candidate


def _read_optional_object(path: Path, label: str) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, f"{label}_optional_input_missing"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, f"{label}_optional_input_unreadable"
    if not isinstance(value, dict):
        return None, f"{label}_optional_input_not_object"
    return value, None


def _story_shape_usable(story: Mapping[str, Any]) -> bool:
    return isinstance(story.get("scenes"), list)


def _scene_alignment_issue(semantic: Mapping[str, Any], optional: Mapping[str, Any]) -> str | None:
    semantic_scenes = semantic.get("scenes", [])
    optional_scenes = optional.get("scenes", optional.get("plans"))
    if not isinstance(optional_scenes, list):
        return "motion_plan_optional_metadata_invalid"
    if len(optional_scenes) != len(semantic_scenes):
        return "motion_plan_scene_count_mismatch"
    for index, (expected, candidate) in enumerate(zip(semantic_scenes, optional_scenes)):
        if not isinstance(candidate, Mapping):
            return f"motion_plan_scene_{index}_invalid"
        expected_id = str(expected.get("scene_id") or f"scene_{index + 1:03d}")
        candidate_id = candidate.get("scene_id")
        if candidate_id != expected_id:
            return f"motion_plan_scene_{index}_identity_mismatch"
        if candidate.get("scene_index") != index:
            return f"motion_plan_scene_{index}_identity_mismatch"
        for key in ("start", "end", "duration"):
            if key not in candidate:
                return f"motion_plan_scene_{index}_timing_mismatch"
            try:
                mismatch = abs(float(candidate[key]) - float(expected.get(key, 0.0))) > 0.001
            except (TypeError, ValueError, OverflowError):
                return f"motion_plan_scene_{index}_timing_mismatch"
            if mismatch:
                return f"motion_plan_scene_{index}_timing_mismatch"
    return None


def _add_input_warnings(diagnostics: AudioDiagnostics, warnings: tuple[str, ...]) -> AudioDiagnostics:
    if not warnings:
        return diagnostics
    canonical = tuple(sorted(set((*diagnostics.project.warnings, *warnings))))
    missing_names = {
        "story_director_optional_input_missing": "story_director_plan",
        "captions_optional_input_missing": "captions",
        "motion_plan_optional_input_missing": "motion_plan",
    }
    adapter_missing = tuple(missing_names[warning] for warning in warnings if warning in missing_names)
    missing = tuple(sorted(set((*diagnostics.project.missing_inputs, *adapter_missing))))
    project = replace(diagnostics.project, warnings=canonical, missing_inputs=missing)
    return replace(diagnostics, project=project)


def _write_temp(target: Path, payload: bytes) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="wb", prefix=f".{target.name}.", suffix=".tmp", dir=target.parent, delete=False,
    )
    path = Path(handle.name)
    try:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    except Exception:
        handle.close()
        path.unlink(missing_ok=True)
        raise
    finally:
        if not handle.closed:
            handle.close()
    return path


def _publish_pair(plan_path: Path, plan: bytes, diagnostics_path: Path, diagnostics: bytes) -> None:
    old_plan = plan_path.read_bytes() if plan_path.exists() else None
    old_diagnostics = diagnostics_path.read_bytes() if diagnostics_path.exists() else None
    temp_plan: Path | None = None
    temp_diagnostics: Path | None = None
    first_published = False
    try:
        temp_plan = _write_temp(plan_path, plan)
        temp_diagnostics = _write_temp(diagnostics_path, diagnostics)
        os.replace(temp_plan, plan_path)
        temp_plan = None
        first_published = True
        os.replace(temp_diagnostics, diagnostics_path)
        temp_diagnostics = None
    except Exception as exc:
        rollback_error = None
        recovery_path: Path | None = None
        if first_published:
            try:
                if old_plan is None:
                    plan_path.unlink(missing_ok=True)
                else:
                    recovery_path = _write_temp(plan_path, old_plan)
                    os.replace(recovery_path, plan_path)
                    recovery_path = None
                if old_diagnostics is None:
                    diagnostics_path.unlink(missing_ok=True)
            except Exception as rollback_exc:  # pragma: no cover - catastrophic filesystem failure
                rollback_error = rollback_exc
                try:
                    plan_path.unlink(missing_ok=True)
                except OSError:
                    pass
        message = f"Audio Director publish failed: {type(exc).__name__}"
        if rollback_error is not None:
            message += f"; rollback failed: {type(rollback_error).__name__}"
        raise AudioDirectorIOError(
            message, publish_error=exc, rollback_error=rollback_error,
        ) from exc
    finally:
        for path in (temp_plan, temp_diagnostics):
            if path is not None:
                path.unlink(missing_ok=True)
