"""Locked SI-02 pure-domain Source Evidence public submodule."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from ._evidence_validation import (
    SI02Error,
    SI02InvalidFieldError,
    SI02InvariantError,
    SI02MalformedDataError,
    SI02SizeLimitError,
    SI02UnsupportedVersionError,
)


class SourceComponentAvailability(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"
    NOT_REQUESTED = "NOT_REQUESTED"


class ProviderTranscriptKind(str, Enum):
    MANUAL = "MANUAL"
    AUTOMATIC = "AUTOMATIC"
    UNKNOWN = "UNKNOWN"


class AudioContainer(str, Enum):
    WAV = "WAV"
    FLAC = "FLAC"
    MP3 = "MP3"
    M4A = "M4A"
    OGG = "OGG"
    WEBM = "WEBM"
    OTHER = "OTHER"


class SourceAcquisitionMethod(str, Enum):
    PROVIDER_API = "PROVIDER_API"
    PROVIDER_PAGE = "PROVIDER_PAGE"
    USER_SUPPLIED = "USER_SUPPLIED"
    LOCAL_FILE = "LOCAL_FILE"
    REPLAY = "REPLAY"


class ProviderTranscriptEvidenceFormat(str, Enum):
    PLAIN_TEXT = "PLAIN_TEXT"
    WEBVTT = "WEBVTT"
    SRT = "SRT"
    TTML = "TTML"
    JSON = "JSON"
    OTHER = "OTHER"


class SourceAcquisitionProvenanceRole(str, Enum):
    AGGREGATE = "AGGREGATE"
    METADATA = "METADATA"
    PROVIDER_TRANSCRIPT_CANDIDATE = "PROVIDER_TRANSCRIPT_CANDIDATE"
    AUDIO = "AUDIO"


class SourceEvidenceDiagnosticSubject(str, Enum):
    EVIDENCE = "EVIDENCE"
    METADATA = "METADATA"
    PROVIDER_TRANSCRIPT_CANDIDATE = "PROVIDER_TRANSCRIPT_CANDIDATE"
    AUDIO_EVIDENCE = "AUDIO_EVIDENCE"
    PROVENANCE = "PROVENANCE"


class SourceEvidenceDiagnosticSeverity(str, Enum):
    NON_FATAL = "NON_FATAL"
    INFORMATIONAL = "INFORMATIONAL"


class SourceEvidenceDiagnosticCode(str, Enum):
    COMPONENT_AVAILABLE = "COMPONENT_AVAILABLE"
    COMPONENT_UNAVAILABLE = "COMPONENT_UNAVAILABLE"
    COMPONENT_UNKNOWN = "COMPONENT_UNKNOWN"
    COMPONENT_NOT_REQUESTED = "COMPONENT_NOT_REQUESTED"
    EVIDENCE_PARTIAL = "EVIDENCE_PARTIAL"
    METADATA_INCOMPLETE = "METADATA_INCOMPLETE"
    TRANSCRIPT_CANDIDATE_INCOMPLETE = "TRANSCRIPT_CANDIDATE_INCOMPLETE"
    AUDIO_PROPERTIES_INCOMPLETE = "AUDIO_PROPERTIES_INCOMPLETE"
    PROVENANCE_REPLAYED = "PROVENANCE_REPLAYED"


_LANGUAGE = re.compile(r"^[A-Za-z]{2,8}$")
_SCRIPT = re.compile(r"^[A-Za-z]{4}$")
_REGION = re.compile(r"^(?:[A-Za-z]{2}|[0-9]{3})$")


def _canonical_language_tag(value: object) -> str:
    if not isinstance(value, str):
        raise SI02InvalidFieldError("EvidenceLanguageTag must be ASCII text.")
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise SI02InvalidFieldError("EvidenceLanguageTag must be ASCII text.") from exc
    if not 1 <= len(encoded) <= 18:
        raise SI02InvalidFieldError("EvidenceLanguageTag must contain at most 18 ASCII bytes.")
    if value.lower() == "und":
        return "und"

    parts = value.split("-")
    if not parts or not _LANGUAGE.fullmatch(parts[0]):
        raise SI02InvalidFieldError("EvidenceLanguageTag has an invalid language subtag.")
    if parts[0].lower() == "und":
        raise SI02InvalidFieldError("The unknown language tag 'und' cannot have subtags.")
    canonical = [parts[0].lower()]
    tail = parts[1:]
    if tail and _SCRIPT.fullmatch(tail[0]):
        canonical.append(tail.pop(0).title())
    if tail and _REGION.fullmatch(tail[0]):
        region = tail.pop(0)
        canonical.append(region if region.isdigit() else region.upper())
    if tail:
        raise SI02InvalidFieldError("EvidenceLanguageTag has unsupported subtags.")
    return "-".join(canonical)


@dataclass(frozen=True, order=True)
class EvidenceLanguageTag:
    """Canonical bounded provider-declared language hint."""

    value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _canonical_language_tag(self.value))

    @classmethod
    def from_canonical(cls, value: object) -> "EvidenceLanguageTag":
        result = cls(value)  # type: ignore[arg-type]
        if result.value != value:
            raise SI02MalformedDataError(
                "Serialized EvidenceLanguageTag must already use canonical casing."
            )
        return result

    def __str__(self) -> str:
        return self.value


__all__ = [
    "SourceComponentAvailability",
    "ProviderTranscriptKind",
    "AudioContainer",
    "SourceAcquisitionMethod",
    "ProviderTranscriptEvidenceFormat",
    "SourceAcquisitionProvenanceRole",
    "SourceEvidenceDiagnosticSubject",
    "SourceEvidenceDiagnosticSeverity",
    "SourceEvidenceDiagnosticCode",
    "EvidenceLanguageTag",
    "SI02Error",
    "SI02MalformedDataError",
    "SI02UnsupportedVersionError",
    "SI02SizeLimitError",
    "SI02InvalidFieldError",
    "SI02InvariantError",
    "SourceEvidenceMetadata",
    "ProviderTranscriptCandidate",
    "normalize_provider_transcript_candidates",
    "ValidatedAudioEvidence",
    "SourceAcquisitionProvenance",
    "SourceEvidenceDiagnostic",
    "AcquiredSourceEvidence",
]


# Imported after EvidenceLanguageTag is defined to avoid a private-module cycle.
from ._evidence_components import (
    ProviderTranscriptCandidate,
    SourceEvidenceMetadata,
    ValidatedAudioEvidence,
    normalize_provider_transcript_candidates,
)
from ._evidence_provenance import SourceAcquisitionProvenance, SourceEvidenceDiagnostic
from ._evidence_aggregate import AcquiredSourceEvidence
