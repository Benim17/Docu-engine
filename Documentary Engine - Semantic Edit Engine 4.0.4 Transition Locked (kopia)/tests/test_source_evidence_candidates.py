from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from engine.source_understanding import CanonicalSourceIdentity, SourceKind, SourceObservationIdentity
from engine.source_understanding.source_evidence import (
    EvidenceLanguageTag,
    ProviderTranscriptCandidate,
    ProviderTranscriptEvidenceFormat,
    ProviderTranscriptKind,
    SI02InvalidFieldError,
    SI02InvariantError,
    SI02MalformedDataError,
    SI02SizeLimitError,
    normalize_provider_transcript_candidates,
)


def identity(value="AbCdEf_12-3"):
    return CanonicalSourceIdentity(1, SourceKind.YOUTUBE_VIDEO, "youtube-video-id", 1, value)


def observation(source=None):
    source = source or identity()
    return SourceObservationIdentity(1, source, "opaque", 1, "observation")


def digest(character):
    return "sha256:" + character * 64


def candidate(**changes):
    source = changes.pop("source_identity", identity())
    values = {
        "schema_version": 1,
        "source_identity": source,
        "observation_identity": observation(source),
        "candidate_id": "candidate-1",
        "candidate_kind": ProviderTranscriptKind.MANUAL,
        "language_hint": EvidenceLanguageTag("en"),
        "is_translatable": False,
        "evidence_byte_length": 12,
        "evidence_format": ProviderTranscriptEvidenceFormat.WEBVTT,
        "evidence_digest": digest("1"),
        "provenance_ref": digest("2"),
    }
    values.update(changes)
    return ProviderTranscriptCandidate(**values)


def test_candidate_exact_fields_round_trip_immutability_and_hashing():
    value = candidate()
    assert ProviderTranscriptCandidate.from_json(value.canonical_bytes()) == value
    assert value == replace(value) and hash(value) == hash(replace(value))
    with pytest.raises(FrozenInstanceError):
        value.candidate_id = "changed"
    assert set(value.to_dict()) == {
        "schema_version", "source_identity", "observation_identity", "candidate_id",
        "candidate_kind", "language_hint", "is_translatable", "evidence_byte_length",
        "evidence_format", "evidence_digest", "provenance_ref",
    }


@pytest.mark.parametrize("changes", [
    {"candidate_id": ""}, {"candidate_id": "contains space"},
    {"candidate_id": "x" * 257}, {"candidate_kind": "FUTURE"},
    {"evidence_format": "VTT2"}, {"is_translatable": 1},
    {"evidence_byte_length": 0}, {"evidence_byte_length": True},
    {"evidence_byte_length": 16_777_217},
])
def test_candidate_field_boundaries(changes):
    with pytest.raises(SI02InvalidFieldError):
        candidate(**changes)


def test_candidate_requires_intrinsic_source_observation_agreement():
    with pytest.raises(SI02InvariantError):
        candidate(observation_identity=observation(identity("ZbCdEf_12-3")))


def test_serialized_language_must_be_canonical_and_unknown_fields_are_rejected():
    value = candidate().to_dict()
    value["language_hint"] = "EN"
    with pytest.raises(SI02MalformedDataError):
        ProviderTranscriptCandidate.from_dict(value)
    value = candidate().to_dict()
    value["locator"] = "https://provider.invalid/transcript"
    with pytest.raises(SI02MalformedDataError):
        ProviderTranscriptCandidate.from_dict(value)


def test_canonical_total_order_uses_all_locked_tie_breakers():
    values = [
        candidate(candidate_id="b", evidence_digest=digest("4")),
        candidate(candidate_id="a", candidate_kind=ProviderTranscriptKind.AUTOMATIC,
                  evidence_digest=digest("3")),
        candidate(candidate_id="a", evidence_digest=digest("2")),
        candidate(candidate_id="a", language_hint=EvidenceLanguageTag("sv"),
                  evidence_digest=digest("1")),
    ]
    ordered = sorted(values, key=ProviderTranscriptCandidate.canonical_order_key)
    assert [(v.language_hint.value, v.candidate_kind.value, v.candidate_id) for v in ordered] == [
        ("en", "MANUAL", "a"), ("en", "MANUAL", "b"),
        ("en", "AUTOMATIC", "a"), ("sv", "MANUAL", "a"),
    ]


def test_normalization_sorts_deduplicates_digest_and_does_not_mutate_input():
    first = candidate(candidate_id="z", evidence_digest=digest("3"))
    duplicate = candidate(candidate_id="a", evidence_digest=digest("3"))
    other = candidate(candidate_id="b", evidence_digest=digest("4"))
    supplied = [first, other, duplicate]
    result = normalize_provider_transcript_candidates(supplied)
    assert supplied == [first, other, duplicate]
    assert result == (duplicate, other)


def test_normalization_rejects_same_digest_with_different_lengths():
    with pytest.raises(SI02InvariantError):
        normalize_provider_transcript_candidates(
            [candidate(evidence_byte_length=1), candidate(candidate_id="other", evidence_byte_length=2)]
        )


def test_normalization_rejects_noncandidate_and_empty_is_valid():
    assert normalize_provider_transcript_candidates([]) == ()
    with pytest.raises(SI02InvalidFieldError):
        normalize_provider_transcript_candidates([object()])


def test_4_kib_preparse_gate():
    with pytest.raises(SI02MalformedDataError):
        ProviderTranscriptCandidate.from_json(b"x" * 4096)
    with pytest.raises(SI02SizeLimitError):
        ProviderTranscriptCandidate.from_json(b"x" * 4097)
