from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from engine.source_understanding.source_evidence import (
    SI02InvalidFieldError,
    SI02MalformedDataError,
    SI02SizeLimitError,
    SourceEvidenceDiagnostic,
    SourceEvidenceDiagnosticCode,
    SourceEvidenceDiagnosticSeverity,
    SourceEvidenceDiagnosticSubject,
)


def digest(character):
    return "sha256:" + character * 64


def diagnostic(**changes):
    values = {
        "schema_version": 1,
        "subject": SourceEvidenceDiagnosticSubject.METADATA,
        "severity": SourceEvidenceDiagnosticSeverity.NON_FATAL,
        "code": SourceEvidenceDiagnosticCode.METADATA_INCOMPLETE,
        "ordinal": 0,
        "component_ref": digest("1"),
    }
    values.update(changes)
    return SourceEvidenceDiagnostic(**values)


def test_diagnostic_exact_fields_round_trip_immutability_and_hashing():
    value = diagnostic()
    assert set(value.to_dict()) == {
        "schema_version", "subject", "severity", "code", "ordinal", "component_ref"
    }
    assert SourceEvidenceDiagnostic.from_json(value.canonical_bytes()) == value
    assert value == replace(value) and hash(value) == hash(replace(value))
    with pytest.raises(FrozenInstanceError):
        value.ordinal = 1


def test_absent_component_reference_is_omitted_not_null():
    value = diagnostic(component_ref=None)
    assert "component_ref" not in value.to_dict()
    serialized = value.to_dict(); serialized["component_ref"] = None
    with pytest.raises(SI02MalformedDataError):
        SourceEvidenceDiagnostic.from_dict(serialized)


@pytest.mark.parametrize("changes", [
    {"subject": "FUTURE"}, {"severity": "WARNING"}, {"code": "NATIVE_ERROR"},
    {"ordinal": -1}, {"ordinal": 256}, {"ordinal": True},
    {"component_ref": "not-a-digest"},
])
def test_diagnostic_intrinsic_field_boundaries(changes):
    with pytest.raises(SI02InvalidFieldError):
        diagnostic(**changes)


def test_uniqueness_key_excludes_ordinal_and_canonical_order_uses_locked_declaration_order():
    first = diagnostic(ordinal=2)
    same_key = diagnostic(ordinal=1)
    assert first.uniqueness_key() == same_key.uniqueness_key()
    values = [
        diagnostic(ordinal=1, subject=SourceEvidenceDiagnosticSubject.PROVENANCE,
                   code=SourceEvidenceDiagnosticCode.PROVENANCE_REPLAYED),
        diagnostic(ordinal=0, subject=SourceEvidenceDiagnosticSubject.PROVENANCE,
                   code=SourceEvidenceDiagnosticCode.PROVENANCE_REPLAYED),
        diagnostic(ordinal=0, subject=SourceEvidenceDiagnosticSubject.EVIDENCE,
                   code=SourceEvidenceDiagnosticCode.EVIDENCE_PARTIAL),
    ]
    ordered = sorted(values, key=SourceEvidenceDiagnostic.canonical_order_key)
    assert [(value.ordinal, value.subject.value) for value in ordered] == [
        (0, "EVIDENCE"), (0, "PROVENANCE"), (1, "PROVENANCE")
    ]


def test_model_has_no_free_text_native_error_path_url_or_timestamp_fields():
    value = diagnostic().to_dict()
    for field in ("message", "native_error", "path", "url", "timestamp"):
        mutated = dict(value); mutated[field] = "forbidden"
        with pytest.raises(SI02MalformedDataError):
            SourceEvidenceDiagnostic.from_dict(mutated)


def test_intrinsic_model_does_not_prematurely_apply_artifact_compatibility_matrix():
    value = diagnostic(
        subject=SourceEvidenceDiagnosticSubject.EVIDENCE,
        code=SourceEvidenceDiagnosticCode.COMPONENT_AVAILABLE,
        component_ref=None,
    )
    assert value.subject is SourceEvidenceDiagnosticSubject.EVIDENCE


def test_1_kib_preparse_gate():
    with pytest.raises(SI02MalformedDataError):
        SourceEvidenceDiagnostic.from_json(b"x" * 1024)
    with pytest.raises(SI02SizeLimitError):
        SourceEvidenceDiagnostic.from_json(b"x" * 1025)
