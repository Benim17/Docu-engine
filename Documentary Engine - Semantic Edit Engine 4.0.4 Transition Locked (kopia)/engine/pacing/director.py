from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Sequence, TypeVar


@dataclass(frozen=True)
class PacingProfile:
    """Temporal motion shape; fractions always cover one complete scene."""

    name: str
    hold_fraction: float
    ease_in_fraction: float
    peak_fraction: float
    ease_out_fraction: float
    settle_fraction: float
    speed_profile: str
    easing_profile: str
    reason: str

    def validate(self) -> None:
        fractions = (
            self.hold_fraction, self.ease_in_fraction, self.peak_fraction,
            self.ease_out_fraction, self.settle_fraction,
        )
        if any(value < 0.0 for value in fractions):
            raise ValueError(f"Pacing profile {self.name!r} contains a negative fraction.")
        if abs(sum(fractions) - 1.0) > 1e-9:
            raise ValueError(f"Pacing profile {self.name!r} fractions must sum to 1.0.")


_PROFILES = {
    "context": PacingProfile(
        "context_calm", 0.14, 0.26, 0.18, 0.27, 0.15,
        "calm_rise_fall", "integrated_smoothstep",
        "Context uses a longer opening hold, slow acceleration, and a gentle finish.",
    ),
    "introduction": PacingProfile(
        "introduction_smooth", 0.08, 0.22, 0.28, 0.27, 0.15,
        "smooth_medium_energy", "integrated_smoothstep",
        "Introduction uses a moderate hold and smooth medium-energy acceleration.",
    ),
    "development": PacingProfile(
        "development_dynamic", 0.04, 0.16, 0.38, 0.28, 0.14,
        "strong_sustained_peak", "integrated_smoothstep",
        "Development uses a short hold, the strongest sustained movement, and a gradual settle.",
    ),
    "conclusion": PacingProfile(
        "conclusion_settled", 0.10, 0.25, 0.15, 0.28, 0.22,
        "slow_pull_long_tail", "integrated_smoothstep",
        "Conclusion uses a measured hold, slow movement, long ease-out, and nearly static ending.",
    ),
}

_INTENT_PROFILE = {
    "introduction": "introduction", "context": "context", "explanation": "context",
    "development": "development", "escalation": "development", "reveal": "development",
    "climax": "development", "reflection": "conclusion", "conclusion": "conclusion",
}

for _profile in _PROFILES.values():
    _profile.validate()

PlanT = TypeVar("PlanT")


class PacingDirector:
    """Decorate existing motion plans without changing path or timeline data."""

    version = "4.3.0"

    def profile_for(self, narrative_intent: str) -> PacingProfile:
        return _PROFILES[_INTENT_PROFILE.get(str(narrative_intent), "development")]

    def apply(self, plans: Sequence[PlanT]) -> list[PlanT]:
        paced: list[PlanT] = []
        for plan in plans:
            profile = self.profile_for(str(getattr(plan, "narrative_intent", "development")))
            paced_plan = replace(
                plan,
                hold_fraction=profile.hold_fraction,
                ease_in_fraction=profile.ease_in_fraction,
                peak_fraction=profile.peak_fraction,
                ease_out_fraction=profile.ease_out_fraction,
                settle_fraction=profile.settle_fraction,
                pacing_profile=profile.name,
                speed_profile=profile.speed_profile,
                easing_profile=profile.easing_profile,
                pacing_reason=profile.reason,
            )
            self._validate_unchanged(plan, paced_plan)
            paced.append(paced_plan)
        return paced

    @staticmethod
    def _validate_unchanged(before: Any, after: Any) -> None:
        protected = (
            "start", "end", "start_zoom", "end_zoom", "start_x", "start_y",
            "end_x", "end_y", "preset", "confidence", "focus_x", "focus_y", "visual_intent",
            "narrative_intent", "guidance_reason",
        )
        if any(getattr(before, name) != getattr(after, name) for name in protected):
            raise ValueError("Pacing Director contract violation: motion path or timeline changed.")


def pacing_progress(raw: float, plan: Any) -> float:
    """Integrate a smooth deterministic speed curve into normalized distance."""
    x = max(0.0, min(1.0, float(raw)))
    hold = max(0.0, float(plan.hold_fraction))
    ease_in = max(0.0, float(plan.ease_in_fraction))
    peak = max(0.0, float(plan.peak_fraction))
    ease_out = max(0.0, float(plan.ease_out_fraction))
    settle = max(0.0, float(plan.settle_fraction))
    if abs(hold + ease_in + peak + ease_out + settle - 1.0) > 1e-9:
        raise ValueError("Pacing fractions must sum to 1.0.")
    if x <= hold:
        return 0.0
    movement_end = hold + ease_in + peak + ease_out
    if x >= movement_end:
        return 1.0

    # Integral of smoothstep(v) = 3v^2 - 2v^3. This produces continuous
    # velocity and acceleration at motion start, peak, and motion finish.
    rise_area = ease_in * 0.5
    full_area = rise_area + peak + ease_out * 0.5
    local = x - hold
    if local < ease_in:
        v = local / max(ease_in, 1e-12)
        area = ease_in * (v ** 3 - 0.5 * v ** 4)
    elif local < ease_in + peak:
        area = rise_area + (local - ease_in)
    else:
        v = (local - ease_in - peak) / max(ease_out, 1e-12)
        area = rise_area + peak + ease_out * (v - v ** 3 + 0.5 * v ** 4)
    return max(0.0, min(1.0, area / max(full_area, 1e-12)))
