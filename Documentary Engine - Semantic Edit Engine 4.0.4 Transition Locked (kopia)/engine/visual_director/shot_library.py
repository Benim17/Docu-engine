from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping


SUPPORTED_SHOT_TYPES = (
    "establishing",
    "wide",
    "medium",
    "portrait",
    "detail",
    "document",
    "map",
    "archive",
)


@dataclass(frozen=True)
class ShotClassification:
    visual_intent: str
    confidence: float
    reason: str


@dataclass(frozen=True)
class ShotRule:
    visual_intent: str
    keywords: frozenset[str]


# Rule order is the deterministic tie-breaker. Specific semantic formats are
# evaluated before broader composition categories.
SHOT_RULES = (
    ShotRule("map", frozenset({
        "map", "maps", "atlas", "geography", "geographic", "route", "routes",
        "border", "borders", "territory", "territories",
    })),
    ShotRule("document", frozenset({
        "document", "documents", "letter", "letters", "record", "records",
        "newspaper", "newspapers", "headline", "headlines", "manuscript",
        "certificate", "report", "reports", "file", "files", "chart", "charts",
    })),
    ShotRule("portrait", frozenset({
        "portrait", "person", "people", "individual", "face", "man", "men",
        "woman", "women", "leader", "leaders", "president", "king", "queen",
        "doctor", "nurse", "judge", "officer",
    })),
    ShotRule("archive", frozenset({
        "archive", "archival", "vintage", "historic", "historical",
        "black-and-white", "photograph", "photographs", "footage",
    })),
    ShotRule("establishing", frozenset({
        "establishing", "exterior", "skyline", "cityscape", "landscape",
        "building", "buildings", "hospital", "street", "streets", "location",
        "overview", "aerial",
    })),
    ShotRule("detail", frozenset({
        "close-up", "detail", "evidence", "artifact", "artifacts", "object",
        "objects", "gavel", "weapon", "hand", "hands",
    })),
    ShotRule("wide", frozenset({
        "wide", "panorama", "panoramic", "crowd", "crowds", "room", "interior",
        "group", "groups",
    })),
)


def _text(value: Any) -> str:
    if isinstance(value, Mapping):
        return " ".join(_text(value[key]) for key in sorted(value, key=str))
    if isinstance(value, (set, frozenset)):
        return " ".join(sorted(_text(item) for item in value))
    if isinstance(value, (list, tuple)):
        return " ".join(_text(item) for item in value)
    return str(value or "")


def _semantic_tokens(scene: Mapping[str, Any]) -> set[str]:
    fields = (
        "voiceover_text",
        "image_description",
        "match_terms",
        "semantic_keywords",
        "keywords",
        "tags",
    )
    combined = " ".join(_text(scene.get(field, "")) for field in fields).lower()
    return set(re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)?", combined))


def classify_scene(scene: Mapping[str, Any]) -> ShotClassification:
    """Classify one semantic scene using deterministic, local keyword rules."""
    tokens = _semantic_tokens(scene)
    ranked: list[tuple[int, int, ShotRule, list[str]]] = []
    for priority, rule in enumerate(SHOT_RULES):
        matches = sorted(tokens & rule.keywords)
        if matches:
            ranked.append((len(matches), -priority, rule, matches))

    if not ranked:
        return ShotClassification("medium", 0.50, "Default fallback.")

    match_count, _, rule, matches = max(ranked, key=lambda item: (item[0], item[1]))
    confidence = min(0.95, 0.68 + 0.07 * match_count)
    reason = f"Matched {rule.visual_intent} indicators: {', '.join(matches)}."
    return ShotClassification(rule.visual_intent, round(confidence, 2), reason)
