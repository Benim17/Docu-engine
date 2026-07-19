from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping


SUPPORTED_NARRATIVE_INTENTS = (
    "introduction",
    "context",
    "explanation",
    "development",
    "escalation",
    "reveal",
    "climax",
    "reflection",
    "conclusion",
)


@dataclass(frozen=True)
class NarrativeClassification:
    narrative_intent: str
    narrative_confidence: float
    narrative_reason: str


@dataclass(frozen=True)
class NarrativeRule:
    narrative_intent: str
    phrases: tuple[str, ...]
    keywords: frozenset[str]
    reason: str


# Rule order is the explicit tie-breaker. Higher-intensity and more specific
# functions precede general structural functions.
NARRATIVE_RULES = (
    NarrativeRule(
        "climax",
        ("turning point", "final confrontation", "decisive moment", "highest point"),
        frozenset({"culmination", "decisive", "peak", "verdict", "breakthrough"}),
        "Scene contains decisive or highest-intensity indicators.",
    ),
    NarrativeRule(
        "reveal",
        ("came to light", "for the first time", "it emerged"),
        frozenset({"discover", "discovered", "discovery", "reveal", "revealed", "uncovered", "exposed", "disclosed"}),
        "Narration contains discovery and disclosure indicators.",
    ),
    NarrativeRule(
        "escalation",
        ("grew worse", "became urgent", "rising tension"),
        frozenset({"danger", "conflict", "crisis", "urgent", "urgency", "tension", "threat", "worsened", "violence", "risk"}),
        "Scene contains increasing danger, conflict, or urgency indicators.",
    ),
    NarrativeRule(
        "explanation",
        ("this means", "as a result", "the reason", "in other words"),
        frozenset({"because", "explains", "explanation", "process", "therefore", "why", "how"}),
        "Narration contains explanatory or causal indicators.",
    ),
    NarrativeRule(
        "reflection",
        ("looking back", "in retrospect", "years later"),
        frozenset({"remember", "remembered", "reflection", "legacy", "aftermath", "contemplate", "impact"}),
        "Scene contains retrospective or contemplative indicators.",
    ),
    NarrativeRule(
        "conclusion",
        ("in the end", "to conclude", "in conclusion", "the story ends"),
        frozenset({"finally", "conclusion", "ultimately", "closing", "summary", "ended"}),
        "Narration contains closing or summary indicators.",
    ),
    NarrativeRule(
        "introduction",
        ("this is the story", "our story begins", "the story begins"),
        frozenset({"introduce", "introduction", "begins", "beginning", "opens"}),
        "Narration contains opening or introductory indicators.",
    ),
    NarrativeRule(
        "context",
        ("at the time", "years before", "historical background"),
        frozenset({"background", "history", "before", "previously", "origin", "context", "earlier"}),
        "Scene contains historical or background context indicators.",
    ),
)


def _text(value: Any) -> str:
    if isinstance(value, Mapping):
        return " ".join(_text(value[key]) for key in sorted(value, key=str))
    if isinstance(value, (set, frozenset)):
        return " ".join(sorted(_text(item) for item in value))
    if isinstance(value, (list, tuple)):
        return " ".join(_text(item) for item in value)
    return str(value or "")


def _semantic_text(scene: Mapping[str, Any]) -> str:
    fields = (
        "voiceover_text",
        "narration",
        "text",
        "image_description",
        "match_terms",
        "semantic_keywords",
        "keywords",
        "tags",
    )
    return " ".join(_text(scene.get(field, "")) for field in fields).lower()


def classify_narrative_intent(
    scene: Mapping[str, Any],
    scene_index: int = 0,
    total_scene_count: int = 0,
) -> NarrativeClassification:
    """Classify a scene's narrative function with deterministic local rules."""
    combined = _semantic_text(scene)
    tokens = set(re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)?", combined))
    ranked: list[tuple[int, int, NarrativeRule, int]] = []

    for priority, rule in enumerate(NARRATIVE_RULES):
        phrase_matches = sum(phrase in combined for phrase in rule.phrases)
        keyword_matches = len(tokens & rule.keywords)
        score = phrase_matches * 3 + keyword_matches * 2
        if score:
            ranked.append((score, -priority, rule, phrase_matches + keyword_matches))

    if ranked:
        score, _, rule, match_count = max(ranked, key=lambda item: (item[0], item[1]))
        confidence = min(0.95, 0.68 + 0.04 * score + 0.01 * match_count)
        return NarrativeClassification(
            rule.narrative_intent,
            round(confidence, 2),
            rule.reason,
        )

    if total_scene_count > 0 and scene_index == 0:
        return NarrativeClassification(
            "introduction", 0.60, "First scene position favors an introduction."
        )

    if total_scene_count > 1 and scene_index == total_scene_count - 1:
        return NarrativeClassification(
            "conclusion", 0.60, "Final scene position favors a conclusion."
        )

    if total_scene_count >= 4 and 0 < scene_index / total_scene_count <= 0.25:
        return NarrativeClassification(
            "context", 0.55, "Early scene position favors contextual setup."
        )

    return NarrativeClassification("development", 0.50, "Default fallback.")
