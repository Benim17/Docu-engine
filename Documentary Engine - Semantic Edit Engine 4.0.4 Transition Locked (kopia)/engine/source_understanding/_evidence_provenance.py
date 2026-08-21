"""Immutable provenance records for the locked SI-02 Evidence contract."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping

from ._evidence_validation import (
    SI02InvalidFieldError,
    SI02InvariantError,
    SI02MalformedDataError,
    canonical_json_bytes,
    component_version,
    digest_sha256,
    logical_component_id,
    parse_canonical_json,
    schema_version,
    uint,
)
from .source_evidence import (
    SourceAcquisitionMethod,
    SourceAcquisitionProvenanceRole,
    SourceEvidenceDiagnosticCode,
    SourceEvidenceDiagnosticSeverity,
    SourceEvidenceDiagnosticSubject,
)
from .source_identity import CanonicalSourceIdentity, SourceObservationIdentity


_PROVENANCE_LIMIT = 8 * 1024
_DIAGNOSTIC_LIMIT = 1024


def _object(value: Any, model: str) -> Mapping[str, Any]:
    if type(value) is not dict:
        raise SI02MalformedDataError(f"{model} must be a JSON object.")
    return value


def _exact_fields(value: Mapping[str, Any], required: frozenset[str], model: str) -> None:
    actual = set(value)
    unknown = sorted(actual - required)
    missing = sorted(required - actual)
    if unknown:
        raise SI02MalformedDataError(f"Unknown {model} fields: {', '.join(unknown)}.")
    if missing:
        raise SI02MalformedDataError(f"Missing {model} fields: {', '.join(missing)}.")
    null_fields = sorted(field for field in actual if value[field] is None)
    if null_fields:
        raise SI02MalformedDataError(
            f"{model} fields must not be null: {', '.join(null_fields)}."
        )


def _enum(value: Any, enum_type: type, field: str):
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise SI02InvalidFieldError(f"Unsupported {field}: {value!r}.") from exc


def _qualified_sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


@dataclass(frozen=True)
class SourceAcquisitionProvenance:
    schema_version: int
    provenance_id: str
    source_identity: CanonicalSourceIdentity
    observation_identity: SourceObservationIdentity
    role: SourceAcquisitionProvenanceRole
    adapter_id: str
    adapter_version: str
    acquisition_method: SourceAcquisitionMethod
    evidence_digest: str
    parent_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", schema_version(self.schema_version))
        object.__setattr__(
            self, "provenance_id", digest_sha256(self.provenance_id, "provenance_id")
        )
        if not isinstance(self.source_identity, CanonicalSourceIdentity):
            raise SI02InvalidFieldError(
                "source_identity must be a CanonicalSourceIdentity."
            )
        if not isinstance(self.observation_identity, SourceObservationIdentity):
            raise SI02InvalidFieldError(
                "observation_identity must be a SourceObservationIdentity."
            )
        if self.observation_identity.source_identity != self.source_identity:
            raise SI02InvariantError(
                "observation_identity.source_identity must equal source_identity."
            )
        object.__setattr__(
            self,
            "role",
            _enum(self.role, SourceAcquisitionProvenanceRole, "role"),
        )
        object.__setattr__(
            self, "adapter_id", logical_component_id(self.adapter_id, "adapter_id")
        )
        object.__setattr__(
            self,
            "adapter_version",
            component_version(self.adapter_version, "adapter_version"),
        )
        object.__setattr__(
            self,
            "acquisition_method",
            _enum(self.acquisition_method, SourceAcquisitionMethod, "acquisition_method"),
        )
        object.__setattr__(
            self, "evidence_digest", digest_sha256(self.evidence_digest, "evidence_digest")
        )
        if type(self.parent_refs) is not tuple:
            raise SI02InvalidFieldError("parent_refs must be an immutable tuple.")
        validated_parents = tuple(
            digest_sha256(value, f"parent_refs[{index}]")
            for index, value in enumerate(self.parent_refs)
        )
        if len(set(validated_parents)) != len(validated_parents):
            raise SI02InvariantError("parent_refs must be unique.")
        if validated_parents != tuple(sorted(validated_parents, key=str.encode)):
            raise SI02InvariantError("parent_refs must be sorted by ascending ASCII bytes.")
        maximum_parents = (
            34 if self.role is SourceAcquisitionProvenanceRole.AGGREGATE else 16
        )
        if len(validated_parents) > maximum_parents:
            raise SI02InvariantError(
                f"{self.role.value} provenance permits at most {maximum_parents} parents."
            )
        if self.provenance_id in validated_parents:
            raise SI02InvariantError("A provenance record must not reference itself.")
        object.__setattr__(self, "parent_refs", validated_parents)
        expected = _qualified_sha256(self.identity_preimage())
        if self.provenance_id != expected:
            raise SI02InvariantError(
                "provenance_id must equal SHA-256 of the canonical record without provenance_id."
            )
        self.canonical_bytes()

    def identity_preimage(self) -> dict[str, Any]:
        return {
            "acquisition_method": self.acquisition_method.value,
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "evidence_digest": self.evidence_digest,
            "observation_identity": self.observation_identity.to_dict(),
            "parent_refs": list(self.parent_refs),
            "role": self.role.value,
            "schema_version": self.schema_version,
            "source_identity": self.source_identity.to_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        value = self.identity_preimage()
        value["provenance_id"] = self.provenance_id
        return value

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict(), maximum_bytes=_PROVENANCE_LIMIT)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SourceAcquisitionProvenance":
        data = _object(value, "SourceAcquisitionProvenance")
        required = frozenset(
            {
                "schema_version",
                "provenance_id",
                "source_identity",
                "observation_identity",
                "role",
                "adapter_id",
                "adapter_version",
                "acquisition_method",
                "evidence_digest",
                "parent_refs",
            }
        )
        _exact_fields(data, required, "SourceAcquisitionProvenance")
        parent_refs = data["parent_refs"]
        if type(parent_refs) is not list:
            raise SI02MalformedDataError(
                "Serialized SourceAcquisitionProvenance.parent_refs must be an array."
            )
        return cls(
            schema_version=schema_version(data["schema_version"]),
            provenance_id=data["provenance_id"],
            source_identity=CanonicalSourceIdentity.from_dict(data["source_identity"]),
            observation_identity=SourceObservationIdentity.from_dict(
                data["observation_identity"]
            ),
            role=_enum(data["role"], SourceAcquisitionProvenanceRole, "role"),
            adapter_id=data["adapter_id"],
            adapter_version=data["adapter_version"],
            acquisition_method=_enum(
                data["acquisition_method"], SourceAcquisitionMethod, "acquisition_method"
            ),
            evidence_digest=data["evidence_digest"],
            parent_refs=tuple(parent_refs),
        )

    @classmethod
    def from_json(cls, serialized: str | bytes) -> "SourceAcquisitionProvenance":
        return cls.from_dict(parse_canonical_json(serialized, maximum_bytes=_PROVENANCE_LIMIT))


@dataclass(frozen=True)
class SourceEvidenceDiagnostic:
    schema_version: int
    subject: SourceEvidenceDiagnosticSubject
    severity: SourceEvidenceDiagnosticSeverity
    code: SourceEvidenceDiagnosticCode
    ordinal: int
    component_ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", schema_version(self.schema_version))
        object.__setattr__(
            self, "subject", _enum(self.subject, SourceEvidenceDiagnosticSubject, "subject")
        )
        object.__setattr__(
            self,
            "severity",
            _enum(self.severity, SourceEvidenceDiagnosticSeverity, "severity"),
        )
        object.__setattr__(
            self, "code", _enum(self.code, SourceEvidenceDiagnosticCode, "code")
        )
        object.__setattr__(self, "ordinal", uint(self.ordinal, "ordinal", maximum=255))
        if self.component_ref is not None:
            object.__setattr__(
                self,
                "component_ref",
                digest_sha256(self.component_ref, "component_ref"),
            )
        self.canonical_bytes()

    def uniqueness_key(self) -> tuple[Any, ...]:
        return (self.subject, self.severity, self.code, self.component_ref)

    def canonical_order_key(self) -> tuple[Any, ...]:
        return (
            self.ordinal,
            list(SourceEvidenceDiagnosticSubject).index(self.subject),
            list(SourceEvidenceDiagnosticSeverity).index(self.severity),
            list(SourceEvidenceDiagnosticCode).index(self.code),
            b"" if self.component_ref is None else self.component_ref.encode("ascii"),
        )

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "code": self.code.value,
            "ordinal": self.ordinal,
            "schema_version": self.schema_version,
            "severity": self.severity.value,
            "subject": self.subject.value,
        }
        if self.component_ref is not None:
            value["component_ref"] = self.component_ref
        return value

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict(), maximum_bytes=_DIAGNOSTIC_LIMIT)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SourceEvidenceDiagnostic":
        data = _object(value, "SourceEvidenceDiagnostic")
        required = frozenset({"schema_version", "subject", "severity", "code", "ordinal"})
        actual = set(data)
        unknown = sorted(actual - required - {"component_ref"})
        missing = sorted(required - actual)
        if unknown:
            raise SI02MalformedDataError(
                f"Unknown SourceEvidenceDiagnostic fields: {', '.join(unknown)}."
            )
        if missing:
            raise SI02MalformedDataError(
                f"Missing SourceEvidenceDiagnostic fields: {', '.join(missing)}."
            )
        if any(data[field] is None for field in actual):
            raise SI02MalformedDataError(
                "SourceEvidenceDiagnostic fields must be absent rather than null."
            )
        return cls(
            schema_version=schema_version(data["schema_version"]),
            subject=_enum(data["subject"], SourceEvidenceDiagnosticSubject, "subject"),
            severity=_enum(
                data["severity"], SourceEvidenceDiagnosticSeverity, "severity"
            ),
            code=_enum(data["code"], SourceEvidenceDiagnosticCode, "code"),
            ordinal=data["ordinal"],
            component_ref=data.get("component_ref"),
        )

    @classmethod
    def from_json(cls, serialized: str | bytes) -> "SourceEvidenceDiagnostic":
        return cls.from_dict(parse_canonical_json(serialized, maximum_bytes=_DIAGNOSTIC_LIMIT))
