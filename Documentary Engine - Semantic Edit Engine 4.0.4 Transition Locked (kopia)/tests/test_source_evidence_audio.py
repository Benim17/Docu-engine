from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from engine.source_understanding import CanonicalSourceIdentity, SourceKind, SourceObservationIdentity
from engine.source_understanding.source_evidence import (
    AudioContainer,
    SI02InvalidFieldError,
    SI02InvariantError,
    SI02MalformedDataError,
    SI02SizeLimitError,
    ValidatedAudioEvidence,
)


def digest(character):
    return "sha256:" + character * 64


def identity(value="AbCdEf_12-3"):
    return CanonicalSourceIdentity(1, SourceKind.YOUTUBE_VIDEO, "youtube-video-id", 1, value)


def observation(source=None):
    source = source or identity()
    return SourceObservationIdentity(1, source, "opaque", 1, "observation")


def audio(**changes):
    source = changes.pop("source_identity", identity())
    values = {
        "schema_version": 1,
        "source_identity": source,
        "observation_identity": observation(source),
        "content_digest": digest("a"),
        "byte_length": 1024,
        "container": AudioContainer.WAV,
        "media_type": "audio/wav",
        "provenance_ref": digest("b"),
    }
    values.update(changes)
    return ValidatedAudioEvidence(**values)


def test_minimal_audio_exact_fields_round_trip_and_optional_omission():
    value = audio()
    assert set(value.to_dict()) == {
        "schema_version", "source_identity", "observation_identity", "content_digest",
        "byte_length", "container", "media_type", "provenance_ref",
    }
    assert ValidatedAudioEvidence.from_json(value.canonical_bytes()) == value


def test_all_observed_optional_properties_round_trip():
    value = audio(
        duration_ms=7_200_000,
        codec_label="pcm_s16le",
        sample_rate_hz=768_000,
        channel_count=64,
        container=AudioContainer.OTHER,
        media_type="audio/x-custom",
    )
    assert ValidatedAudioEvidence.from_json(value.canonical_bytes()) == value


def test_audio_is_frozen_structurally_equal_and_hashable():
    value = audio()
    assert value == replace(value) and hash(value) == hash(replace(value))
    with pytest.raises(FrozenInstanceError):
        value.media_type = "audio/flac"


@pytest.mark.parametrize("changes", [
    {"byte_length": 0}, {"byte_length": True}, {"byte_length": 536_870_913},
    {"duration_ms": 0}, {"duration_ms": 7_200_001},
    {"sample_rate_hz": 0}, {"sample_rate_hz": 768_001},
    {"channel_count": 0}, {"channel_count": 65},
    {"codec_label": ""}, {"codec_label": "å"}, {"codec_label": "x" * 129},
    {"container": "AAC"},
])
def test_audio_numeric_ascii_and_enum_boundaries(changes):
    with pytest.raises(SI02InvalidFieldError):
        audio(**changes)


@pytest.mark.parametrize("media_type", [
    "Audio/wav", "audio/WAV", "audio/wav;rate=48000", "audio", "/wav", "audio/",
    "audio /wav", "audio/wäv", "a" * 64 + "/wav", "audio/" + "x" * 64,
])
def test_media_type_grammar_rejects_noncanonical_and_parameterized_values(media_type):
    with pytest.raises(SI02InvalidFieldError):
        audio(media_type=media_type)


@pytest.mark.parametrize("media_type", [
    "audio/wav", "audio/x-wav", "application/ogg", "video/webm", "x/x", "a+b/c.d"
])
def test_media_type_grammar_accepts_exact_lowercase_type_subtype(media_type):
    assert audio(media_type=media_type).media_type == media_type


def test_audio_requires_intrinsic_source_observation_agreement():
    with pytest.raises(SI02InvariantError):
        audio(observation_identity=observation(identity("ZbCdEf_12-3")))


def test_paths_uris_native_handles_and_unknown_fields_are_not_model_fields():
    value = audio().to_dict()
    for field, forbidden_value in (
        ("path", "/tmp/audio.wav"),
        ("uri", "https://provider.invalid/audio"),
        ("file_descriptor", 3),
        ("decoder", "ffmpeg"),
    ):
        mutated = dict(value)
        mutated[field] = forbidden_value
        with pytest.raises(SI02MalformedDataError):
            ValidatedAudioEvidence.from_dict(mutated)


def test_optional_null_is_rejected_in_serialized_model():
    value = audio().to_dict()
    value["duration_ms"] = None
    with pytest.raises(SI02MalformedDataError):
        ValidatedAudioEvidence.from_dict(value)


def test_6_kib_preparse_gate():
    with pytest.raises(SI02MalformedDataError):
        ValidatedAudioEvidence.from_json(b"x" * 6144)
    with pytest.raises(SI02SizeLimitError):
        ValidatedAudioEvidence.from_json(b"x" * 6145)
