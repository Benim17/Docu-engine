from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from PIL import Image, ImageFilter, ImageStat


STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "did", "do", "for",
    "from", "had", "has", "have", "he", "her", "hers", "him", "his", "in", "into", "is",
    "it", "its", "of", "on", "one", "or", "she", "that", "the", "their", "them", "there",
    "they", "this", "to", "was", "were", "with", "would", "att", "av", "blev", "de", "den",
    "det", "du", "en", "ett", "för", "från", "han", "har", "hon", "i", "inte", "med", "men",
    "och", "om", "på", "som", "till", "var", "vi", "är",
}

AI_WARNING_TERMS = {
    "ai generated", "artificial intelligence", "distorted anatomy", "extra fingers",
    "unrealistic lighting", "synthetic", "render", "illustration", "fantasy",
}
DOCUMENTARY_TERMS = {
    "archive", "court", "courtroom", "document", "evidence", "hospital", "investigation",
    "medical", "news", "photograph", "real", "records", "report", "trial",
}


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _tokens(text: str) -> list[str]:
    raw = re.findall(r"[a-z0-9]+(?:-[a-z]+)?", text.lower())
    result: list[str] = []
    for token in raw:
        if token in STOP_WORDS or len(token) < 2:
            continue
        result.append(token)
        if token.endswith("s") and len(token) > 4:
            result.append(token[:-1])
    return result


def _profile_text(profile: Mapping[str, Any]) -> str:
    return " ".join([
        str(profile.get("file", "")),
        str(profile.get("description", "")),
        " ".join(str(value) for value in profile.get("tags", [])),
        " ".join(str(value) for value in profile.get("ocr", [])),
    ])


def _natural_key(path: Path) -> tuple[tuple[int, Any], ...]:
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", path.name)
    )


@dataclass(frozen=True)
class CandidateScore:
    image: str
    rank: int
    composition_score: float
    technical_score: float
    documentary_score: float
    caption_compatibility: float
    motion_compatibility: float
    semantic_compatibility: float
    repetition_penalty: float
    final_score: float
    match_terms: tuple[str, ...]
    reasoning: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["match_terms"] = list(self.match_terms)
        return payload


@dataclass(frozen=True)
class ImageDecision:
    scene_index: int
    selected_image: str
    selection_reasoning: str
    semantic_reference_image: str
    semantic_match_terms: tuple[str, ...]
    semantic_score: float
    candidate_ranking: tuple[CandidateScore, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_index": self.scene_index,
            "selected_image": self.selected_image,
            "selection_reasoning": self.selection_reasoning,
            "semantic_reference_image": self.semantic_reference_image,
            "semantic_match_terms": list(self.semantic_match_terms),
            "semantic_score": self.semantic_score,
            "candidate_ranking": [candidate.to_dict() for candidate in self.candidate_ranking],
        }


class ImageIntelligence:
    """Deterministic image evaluation boundary for Documentary Engine 4.4.0.

    This module intentionally has no knowledge of visual, motion, pacing, caption,
    transition, or rendering modules. It accepts scene context plus image candidates
    and returns an immutable ranking.
    """

    version = "4.4.0"
    weights = {
        "semantic": 0.35,
        "composition": 0.15,
        "technical": 0.15,
        "documentary": 0.15,
        "caption": 0.08,
        "motion": 0.12,
    }

    def __init__(self) -> None:
        self._pixel_cache: dict[tuple[str, int, int], tuple[float, float, float, float, float]] = {}

    def rank_scene(
        self,
        scene_index: int,
        scene_count: int,
        narration: str,
        candidates: Sequence[tuple[Path, Mapping[str, Any]]],
        usage: Mapping[str, int] | None = None,
        previous_image: str | None = None,
        repeat_penalty: float = 3.5,
        immediate_repeat_penalty: float = 25.0,
        semantic_usage: Mapping[str, int] | None = None,
        semantic_previous_image: str | None = None,
    ) -> ImageDecision:
        if not candidates:
            raise ValueError("Image Intelligence requires at least one candidate image.")

        usage = usage or {}
        ordered_candidates = sorted(candidates, key=lambda candidate: _natural_key(candidate[0]))
        scored: list[dict[str, Any]] = []
        for image_path, profile in ordered_candidates:
            if not image_path.is_file():
                raise FileNotFoundError(f"Candidate image does not exist: {image_path}")
            composition, technical, documentary_pixels, caption, motion = self._pixel_scores(image_path)
            embedded_text = " ".join(str(value) for value in profile.get("ocr", [])).strip()
            if embedded_text:
                caption = _clamp(caption - min(55.0, 18.0 + len(embedded_text) * 0.75))
            documentary = self._documentary_score(profile, documentary_pixels)
            semantic, terms = self._semantic_score(narration, profile, scene_index, scene_count, ordered_candidates)
            repetition = float(usage.get(image_path.name, 0)) * repeat_penalty
            if previous_image == image_path.name:
                repetition += immediate_repeat_penalty
            final = (
                semantic * self.weights["semantic"]
                + composition * self.weights["composition"]
                + technical * self.weights["technical"]
                + documentary * self.weights["documentary"]
                + caption * self.weights["caption"]
                + motion * self.weights["motion"]
                - repetition
            )
            scored.append({
                "image": image_path.name,
                "composition_score": round(composition, 4),
                "technical_score": round(technical, 4),
                "documentary_score": round(documentary, 4),
                "caption_compatibility": round(caption, 4),
                "motion_compatibility": round(motion, 4),
                "semantic_compatibility": round(semantic, 4),
                "repetition_penalty": round(repetition, 4),
                "final_score": round(final, 4),
                "match_terms": tuple(terms),
            })

        # The normalized filename is the explicit, stable tie-breaker. Input order,
        # file-system order, and dictionary insertion order cannot affect selection.
        scored.sort(key=lambda item: (-item["final_score"], item["image"].casefold(), item["image"]))
        ranking: list[CandidateScore] = []
        for rank, item in enumerate(scored, 1):
            reason = self._candidate_reason(item, rank)
            ranking.append(CandidateScore(rank=rank, reasoning=reason, **item))
        winner = ranking[0]
        reference_image, reference_terms, reference_score = self._semantic_reference(
            scene_index, scene_count, narration, ordered_candidates,
            semantic_usage or {}, semantic_previous_image,
            repeat_penalty, immediate_repeat_penalty,
        )
        selection_reason = (
            f"Selected {winner.image}: highest deterministic score {winner.final_score:.4f}; "
            f"semantic {winner.semantic_compatibility:.2f}, composition {winner.composition_score:.2f}, "
            f"technical {winner.technical_score:.2f}, documentary {winner.documentary_score:.2f}, "
            f"caption {winner.caption_compatibility:.2f}, motion {winner.motion_compatibility:.2f}."
        )
        return ImageDecision(
            scene_index, winner.image, selection_reason, reference_image,
            tuple(reference_terms), round(reference_score, 3), tuple(ranking),
        )

    @staticmethod
    def _concept_groups(candidates: Sequence[tuple[Path, Mapping[str, Any]]]) -> dict[str, set[str]]:
        groups: dict[str, set[str]] = {}
        for _, profile in candidates:
            phrases = [str(profile.get("description", ""))]
            phrases.extend(str(value) for value in profile.get("tags", []))
            phrases.extend(str(value) for value in profile.get("ocr", []))
            for phrase in phrases:
                tokens = set(_tokens(phrase))
                if tokens:
                    groups.setdefault("_".join(sorted(tokens)[:5]), set()).update(tokens)
        return groups

    @staticmethod
    def _expanded(tokens: list[str], concepts: Mapping[str, set[str]]) -> Counter[str]:
        result = Counter(tokens)
        present = set(tokens)
        for concept, members in concepts.items():
            if present & members:
                result[concept] += 1.6
                for member in members:
                    result[member] += 0.14
        return result

    def _semantic_reference(
        self,
        scene_index: int,
        scene_count: int,
        narration: str,
        candidates: Sequence[tuple[Path, Mapping[str, Any]]],
        usage: Mapping[str, int],
        previous_image: str | None,
        repeat_penalty: float,
        immediate_repeat_penalty: float,
    ) -> tuple[str, list[str], float]:
        """Preserve pre-4.4 semantic metadata while quality owns image selection."""
        concepts = self._concept_groups(candidates)
        beat_tokens = self._expanded(_tokens(narration), concepts)
        expected = scene_index / max(1, scene_count - 1)
        ranked = []
        for image_index, (path, profile) in enumerate(candidates):
            image_tokens = self._expanded(_tokens(_profile_text(profile)), concepts)
            shared = set(beat_tokens) & set(image_tokens)
            semantic = sum(
                min(float(beat_tokens[token]), 3.0) * min(float(image_tokens[token]), 3.0)
                for token in shared
            )
            terms = sorted(
                shared,
                key=lambda token: (-beat_tokens[token] * image_tokens[token], token),
            )[:6]
            actual = image_index / max(1, len(candidates) - 1)
            chronology = 1.7 * (1.0 - min(1.0, abs(expected - actual)))
            penalty = float(usage.get(path.name, 0)) * repeat_penalty
            if previous_image == path.name:
                penalty += immediate_repeat_penalty
            priority = float(profile.get("priority", 0.0))
            phase = 0.0
            if "preferred_phase" in profile:
                distance = abs(expected - float(profile["preferred_phase"]))
                phase = 12.0 * max(0.0, 1.0 - distance / 0.24)
            score = semantic + chronology + priority + phase - penalty
            ranked.append((round(score, 12), path.name, terms))
        ranked.sort(key=lambda item: (-item[0], item[1].casefold(), item[1]))
        score, image, terms = ranked[0]
        return image, terms, score

    def _pixel_scores(self, path: Path) -> tuple[float, float, float, float, float]:
        stat = path.stat()
        cache_key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
        cached = self._pixel_cache.get(cache_key)
        if cached is not None:
            return cached
        with Image.open(path) as source:
            source.load()
            width, height = source.size
            rgb = source.convert("RGB")
            sample = rgb.copy()
            sample.thumbnail((384, 384), Image.Resampling.LANCZOS)
            gray = sample.convert("L")
            luminance = ImageStat.Stat(gray)
            contrast = float(luminance.stddev[0])
            brightness = float(luminance.mean[0])
            edges = gray.filter(ImageFilter.FIND_EDGES)
            edge_mean = float(ImageStat.Stat(edges).mean[0])

            megapixels = width * height / 1_000_000.0
            resolution = _clamp(30.0 + 42.0 * math.log2(max(0.25, megapixels) + 1.0))
            sharpness = _clamp(edge_mean * 4.4)
            exposure = _clamp(100.0 - abs(brightness - 128.0) * 0.75)
            technical = 0.48 * resolution + 0.34 * sharpness + 0.18 * exposure

            sw, sh = gray.size
            center = gray.crop((sw // 4, sh // 4, sw * 3 // 4, sh * 3 // 4))
            center_edges = float(ImageStat.Stat(center.filter(ImageFilter.FIND_EDGES)).mean[0])
            edge_balance = _clamp(100.0 - abs(edge_mean - center_edges) * 5.0)
            tonal_range = _clamp(contrast * 2.35)
            composition = 0.58 * edge_balance + 0.42 * tonal_range

            extrema_penalty = max(0.0, abs(brightness - 128.0) - 72.0) * 0.8
            documentary_pixels = _clamp(72.0 + contrast * 0.35 - extrema_penalty)

            ocr_safe_space = self._negative_space_score(gray)
            caption = 0.65 * ocr_safe_space + 0.35 * exposure

            target_ratio = 9.0 / 16.0
            ratio = width / max(1.0, float(height))
            retained = min(1.0, min(ratio, target_ratio) / max(ratio, target_ratio))
            crop_score = 100.0 * math.sqrt(retained)
            motion = 0.62 * crop_score + 0.38 * edge_balance

        result = tuple(round(_clamp(value), 4) for value in (
            composition, technical, documentary_pixels, caption, motion,
        ))
        self._pixel_cache[cache_key] = result
        return result

    @staticmethod
    def _negative_space_score(gray: Image.Image) -> float:
        width, height = gray.size
        variances = []
        for row in range(3):
            for column in range(3):
                box = (
                    column * width // 3,
                    row * height // 3,
                    (column + 1) * width // 3,
                    (row + 1) * height // 3,
                )
                variances.append(float(ImageStat.Stat(gray.crop(box)).var[0]))
        quietest = min(variances) if variances else 0.0
        return _clamp(100.0 - math.sqrt(max(0.0, quietest)) * 4.2)

    @staticmethod
    def _documentary_score(profile: Mapping[str, Any], pixel_score: float) -> float:
        text = _profile_text(profile).lower()
        positive = sum(1 for term in DOCUMENTARY_TERMS if term in text)
        negative = sum(1 for term in AI_WARNING_TERMS if term in text)
        realism = float(profile.get("documentary_score", profile.get("realism_score", 70.0)))
        score = 0.55 * pixel_score + 0.45 * _clamp(realism) + positive * 2.0 - negative * 14.0
        return _clamp(score)

    @staticmethod
    def _semantic_score(
        narration: str,
        profile: Mapping[str, Any],
        scene_index: int,
        scene_count: int,
        candidates: Sequence[tuple[Path, Mapping[str, Any]]],
    ) -> tuple[float, list[str]]:
        narration_counts = Counter(_tokens(narration))
        profile_counts = Counter(_tokens(_profile_text(profile)))
        shared = set(narration_counts) & set(profile_counts)
        terms = sorted(shared, key=lambda term: (-narration_counts[term] * profile_counts[term], term))[:6]
        overlap = sum(min(narration_counts[term], 3) * min(profile_counts[term], 3) for term in shared)
        semantic = min(70.0, overlap * 9.0)
        priority = _clamp(float(profile.get("priority", 0.0)) * 4.0, -10.0, 10.0)
        expected = scene_index / max(1, scene_count - 1)
        phase = 0.0
        if "preferred_phase" in profile:
            distance = abs(expected - float(profile["preferred_phase"]))
            phase = 20.0 * max(0.0, 1.0 - distance / 0.24)
        else:
            ordered_names = sorted((path.name for path, _ in candidates), key=lambda name: (name.casefold(), name))
            actual = ordered_names.index(str(profile.get("file", ""))) / max(1, len(ordered_names) - 1)
            phase = 8.0 * (1.0 - min(1.0, abs(expected - actual)))
        return _clamp(18.0 + semantic + priority + phase), terms

    @staticmethod
    def _candidate_reason(item: Mapping[str, Any], rank: int) -> str:
        terms = ", ".join(item["match_terms"]) if item["match_terms"] else "no direct lexical match"
        return (
            f"Rank {rank}; final {item['final_score']:.4f}; {terms}; "
            f"repetition penalty {item['repetition_penalty']:.2f}."
        )
