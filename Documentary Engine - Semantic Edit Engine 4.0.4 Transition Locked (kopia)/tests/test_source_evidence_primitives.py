from __future__ import annotations

import builtins
import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import engine.source_understanding as source_understanding
from engine.source_understanding import _evidence_validation as validation
from engine.source_understanding.source_evidence import (
    AudioContainer,
    EvidenceLanguageTag,
    ProviderTranscriptEvidenceFormat,
    ProviderTranscriptKind,
    SI02InvalidFieldError,
    SI02MalformedDataError,
    SI02SizeLimitError,
    SI02UnsupportedVersionError,
    SourceAcquisitionMethod,
    SourceAcquisitionProvenanceRole,
    SourceComponentAvailability,
    SourceEvidenceDiagnosticCode,
    SourceEvidenceDiagnosticSeverity,
    SourceEvidenceDiagnosticSubject,
)


def test_exact_closed_v1_vocabularies():
    assert [item.value for item in SourceComponentAvailability] == [
        "AVAILABLE", "UNAVAILABLE", "UNKNOWN", "NOT_REQUESTED"
    ]
    assert [item.value for item in ProviderTranscriptKind] == [
        "MANUAL", "AUTOMATIC", "UNKNOWN"
    ]
    assert [item.value for item in AudioContainer] == [
        "WAV", "FLAC", "MP3", "M4A", "OGG", "WEBM", "OTHER"
    ]
    assert [item.value for item in SourceAcquisitionMethod] == [
        "PROVIDER_API", "PROVIDER_PAGE", "USER_SUPPLIED", "LOCAL_FILE", "REPLAY"
    ]
    assert [item.value for item in ProviderTranscriptEvidenceFormat] == [
        "PLAIN_TEXT", "WEBVTT", "SRT", "TTML", "JSON", "OTHER"
    ]
    assert [item.value for item in SourceAcquisitionProvenanceRole] == [
        "AGGREGATE", "METADATA", "PROVIDER_TRANSCRIPT_CANDIDATE", "AUDIO"
    ]
    assert [item.value for item in SourceEvidenceDiagnosticSubject] == [
        "EVIDENCE", "METADATA", "PROVIDER_TRANSCRIPT_CANDIDATE", "AUDIO_EVIDENCE", "PROVENANCE"
    ]
    assert [item.value for item in SourceEvidenceDiagnosticSeverity] == [
        "NON_FATAL", "INFORMATIONAL"
    ]
    assert [item.value for item in SourceEvidenceDiagnosticCode] == [
        "COMPONENT_AVAILABLE", "COMPONENT_UNAVAILABLE", "COMPONENT_UNKNOWN",
        "COMPONENT_NOT_REQUESTED", "EVIDENCE_PARTIAL", "METADATA_INCOMPLETE",
        "TRANSCRIPT_CANDIDATE_INCOMPLETE", "AUDIO_PROPERTIES_INCOMPLETE",
        "PROVENANCE_REPLAYED",
    ]


@pytest.mark.parametrize("value,canonical", [
    ("und", "und"), ("UND", "und"), ("en", "en"), ("SV", "sv"),
    ("zh-hant", "zh-Hant"), ("pt-br", "pt-BR"),
    ("sr-latn-rs", "sr-Latn-RS"), ("es-419", "es-419"),
])
def test_language_tag_direct_construction_canonicalizes_valid_casing(value, canonical):
    assert EvidenceLanguageTag(value).value == canonical


@pytest.mark.parametrize("value", [
    "", "e", "languagexx", "en-US-extra", "en-u-ca-gregory", "en-x-private",
    "en--US", "en_US", "sv-Åååå", "und-US", "123", "en-1234",
])
def test_language_tag_rejects_unsupported_forms(value):
    with pytest.raises(SI02InvalidFieldError):
        EvidenceLanguageTag(value)


def test_serialized_language_tag_requires_canonical_casing():
    assert EvidenceLanguageTag.from_canonical("sr-Latn-RS") == EvidenceLanguageTag("sr-latn-rs")
    with pytest.raises(SI02MalformedDataError):
        EvidenceLanguageTag.from_canonical("SR-latn-rs")


def test_language_tag_is_frozen_hashable_and_ordered_by_canonical_ascii():
    value = EvidenceLanguageTag("SV")
    assert value == EvidenceLanguageTag("sv")
    assert hash(value) == hash(EvidenceLanguageTag("sv"))
    assert EvidenceLanguageTag("en") < EvidenceLanguageTag("sv")
    with pytest.raises(FrozenInstanceError):
        value.value = "en"


@pytest.mark.parametrize("value", [True, False, -1, 1.0, "1"])
def test_uint_rejects_booleans_negative_and_nonintegers(value):
    with pytest.raises(SI02InvalidFieldError):
        validation.uint(value, "value")


def test_digest_and_identifier_grammars_are_exact():
    digest = "sha256:" + "0" * 64
    assert validation.digest_sha256(digest, "digest") == digest
    assert validation.opaque_candidate_id("candidate._~-1") == "candidate._~-1"
    assert validation.logical_component_id("source.provider-v1", "adapter_id") == "source.provider-v1"
    assert validation.component_version("1.0.0+pure", "version") == "1.0.0+pure"
    for invalid in ("0" * 64, "sha256:" + "A" * 64, "sha1:" + "0" * 64):
        with pytest.raises(SI02InvalidFieldError):
            validation.digest_sha256(invalid, "digest")


def test_si02_canonical_wrapper_translates_errors_without_changing_si01():
    assert validation.canonical_json_bytes({"value": "Räv"}) == b'{"value":"R\xc3\xa4v"}'
    with pytest.raises(SI02MalformedDataError):
        validation.parse_canonical_json(b'{"value":null}', maximum_bytes=100)
    with pytest.raises(SI02SizeLimitError):
        validation.parse_canonical_json(b"x" * 101, maximum_bytes=100)
    with pytest.raises(SI02UnsupportedVersionError):
        validation.schema_version(2)


def test_existing_package_level_si01_api_is_unchanged():
    assert len(source_understanding.__all__) == 11
    assert "EvidenceLanguageTag" not in source_understanding.__all__


def test_si02_primitive_import_does_not_load_forbidden_layers():
    script = r'''
import sys
import engine.source_understanding.source_evidence
forbidden = (
    "engine.storage", "engine.pipeline", "engine.source_ingestion", "engine.knowledge",
    "engine.story_director", "engine.audio_director", "engine.caption_director",
    "engine.visual_director",
)
loaded = sorted(
    name for name in sys.modules
    if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)
)
raise SystemExit("forbidden imports: " + ", ".join(loaded) if loaded else 0)
'''
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_language_and_validation_are_zero_network_and_zero_filesystem(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("SI-02 primitive attempted forbidden I/O")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    import socket
    import urllib.request
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    assert EvidenceLanguageTag("EN-us").value == "en-US"
    assert validation.canonical_json_bytes({"language": "en-US"})
