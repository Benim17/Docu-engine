from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace

import pytest

from engine.source_understanding.source_evidence import (
    EvidenceLanguageTag,
    SI02InvalidFieldError,
    SI02InvariantError,
    SI02MalformedDataError,
    SI02SizeLimitError,
    SI02UnsupportedVersionError,
    SourceEvidenceMetadata,
)


DIGEST = "sha256:" + "1" * 64
PROVENANCE = "sha256:" + "2" * 64


def metadata(**changes):
    values = {
        "schema_version": 1,
        "evidence_digest": DIGEST,
        "provenance_ref": PROVENANCE,
        "title": "Räv",
    }
    values.update(changes)
    return SourceEvidenceMetadata(**values)


def test_exact_fields_and_optional_omission():
    value = metadata()
    assert value.to_dict() == {
        "evidence_digest": DIGEST,
        "provenance_ref": PROVENANCE,
        "schema_version": 1,
        "title": "Räv",
    }
    assert list(json.loads(value.canonical_bytes())) == sorted(value.to_dict())


def test_all_approved_metadata_values_round_trip():
    value = metadata(
        creator_label="Skapare",
        creator_identity="creator-1",
        published_at_ms=0,
        duration_ms=1200,
        language_hint=EvidenceLanguageTag("SV"),
        description_excerpt="Beskrivning",
    )
    assert SourceEvidenceMetadata.from_json(value.canonical_bytes()) == value
    assert value.to_dict()["language_hint"] == "sv"


def test_metadata_is_frozen_structurally_equal_and_hashable():
    value = metadata()
    assert value == replace(value)
    assert hash(value) == hash(replace(value))
    with pytest.raises(FrozenInstanceError):
        value.title = "changed"


def test_at_least_one_metadata_value_is_required():
    with pytest.raises(SI02InvariantError):
        SourceEvidenceMetadata(1, DIGEST, PROVENANCE)


@pytest.mark.parametrize("changes", [
    {"title": ""}, {"title": "x" * 1025}, {"title": None, "creator_label": "x" * 513},
    {"title": None, "creator_identity": "å"},
    {"published_at_ms": -1}, {"published_at_ms": True}, {"duration_ms": 1.5},
    {"title": None, "description_excerpt": "x" * 16385},
])
def test_field_type_text_ascii_and_integer_boundaries(changes):
    with pytest.raises(SI02InvalidFieldError):
        metadata(**changes)


def test_unicode_is_nfc_before_equality_hash_and_serialization():
    first = metadata(title="Cafe\u0301")
    second = metadata(title="Café")
    assert first == second and hash(first) == hash(second)
    assert b"Caf\xc3\xa9" in first.canonical_bytes()


@pytest.mark.parametrize("payload", [
    {"evidence_digest": DIGEST, "provenance_ref": PROVENANCE, "title": "x"},
    {"schema_version": 1, "provenance_ref": PROVENANCE, "title": "x"},
    {"schema_version": 1, "evidence_digest": DIGEST, "provenance_ref": PROVENANCE,
     "title": "x", "popularity": 1},
    {"schema_version": 1, "evidence_digest": DIGEST, "provenance_ref": PROVENANCE,
     "title": None},
])
def test_missing_unknown_and_null_fields_are_rejected(payload):
    with pytest.raises(SI02MalformedDataError):
        SourceEvidenceMetadata.from_dict(payload)


def test_serialized_language_casing_must_already_be_canonical():
    payload = metadata(language_hint=EvidenceLanguageTag("sv")).to_dict()
    payload["language_hint"] = "SV"
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with pytest.raises(SI02MalformedDataError):
        SourceEvidenceMetadata.from_json(encoded)


def test_noncanonical_json_duplicate_float_boolean_and_null_are_rejected():
    valid = metadata().canonical_bytes()
    for payload in (
        b" " + valid,
        valid + b"\n",
        valid.replace(b'"schema_version":1', b'"schema_version":1.0'),
        valid.replace(b'"schema_version":1', b'"schema_version":true'),
        valid[:-1] + b',"title":null}',
        valid[:-1] + b',"title":"x"}',
    ):
        with pytest.raises((SI02MalformedDataError, SI02InvalidFieldError)):
            SourceEvidenceMetadata.from_json(payload)


def test_version_errors_are_distinct():
    with pytest.raises(SI02UnsupportedVersionError):
        metadata(schema_version=2)
    with pytest.raises(SI02InvalidFieldError):
        metadata(schema_version=True)


def test_24_kib_preparse_gate():
    with pytest.raises(SI02MalformedDataError):
        SourceEvidenceMetadata.from_json(b"x" * (24 * 1024))
    with pytest.raises(SI02SizeLimitError):
        SourceEvidenceMetadata.from_json(b"x" * (24 * 1024 + 1))
