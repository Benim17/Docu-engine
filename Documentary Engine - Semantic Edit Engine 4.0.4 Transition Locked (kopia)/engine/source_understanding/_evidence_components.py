"""Immutable component models for the locked SI-02 Evidence contract."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ._evidence_validation import (
    SI02InvariantError,
    SI02InvalidFieldError,
    boolean,
    SI02MalformedDataError,
    ascii_text,
    canonical_json_bytes,
    digest_sha256,
    parse_canonical_json,
    opaque_candidate_id,
    positive_int,
    schema_version,
    uint,
    utf8_text,
)
from .source_evidence import (
    AudioContainer,
    EvidenceLanguageTag,
    ProviderTranscriptEvidenceFormat,
    ProviderTranscriptKind,
)
from .source_identity import CanonicalSourceIdentity, SourceObservationIdentity


_METADATA_LIMIT = 24 * 1024
_CANDIDATE_LIMIT = 4 * 1024
_AUDIO_LIMIT = 6 * 1024
_MEDIA_TYPE = re.compile(
    r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,62}/[a-z0-9][a-z0-9!#$&^_.+-]{0,62}$"
)
_METADATA_REQUIRED = frozenset({"schema_version", "evidence_digest", "provenance_ref"})
_METADATA_OPTIONAL = frozenset(
    {
        "title",
        "creator_label",
        "creator_identity",
        "published_at_ms",
        "duration_ms",
        "language_hint",
        "description_excerpt",
    }
)


def _object(value: Any, model: str) -> Mapping[str, Any]:
    if type(value) is not dict:
        raise SI02MalformedDataError(f"{model} must be a JSON object.")
    return value


def _exact_fields(
    value: Mapping[str, Any], required: frozenset[str], optional: frozenset[str], model: str
) -> None:
    actual = set(value)
    unknown = sorted(actual - required - optional)
    missing = sorted(required - actual)
    if unknown:
        raise SI02MalformedDataError(f"Unknown {model} fields: {', '.join(unknown)}.")
    if missing:
        raise SI02MalformedDataError(f"Missing {model} fields: {', '.join(missing)}.")
    null_fields = sorted(field for field in actual if value[field] is None)
    if null_fields:
        raise SI02MalformedDataError(
            f"{model} fields must be absent rather than null: {', '.join(null_fields)}."
        )


@dataclass(frozen=True)
class SourceEvidenceMetadata:
    schema_version: int
    evidence_digest: str
    provenance_ref: str
    title: str | None = None
    creator_label: str | None = None
    creator_identity: str | None = None
    published_at_ms: int | None = None
    duration_ms: int | None = None
    language_hint: EvidenceLanguageTag | None = None
    description_excerpt: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", schema_version(self.schema_version))
        object.__setattr__(
            self, "evidence_digest", digest_sha256(self.evidence_digest, "evidence_digest")
        )
        object.__setattr__(
            self, "provenance_ref", digest_sha256(self.provenance_ref, "provenance_ref")
        )
        if self.title is not None:
            object.__setattr__(self, "title", utf8_text(self.title, 1024, "title"))
        if self.creator_label is not None:
            object.__setattr__(
                self, "creator_label", utf8_text(self.creator_label, 512, "creator_label")
            )
        if self.creator_identity is not None:
            object.__setattr__(
                self,
                "creator_identity",
                ascii_text(self.creator_identity, 512, "creator_identity"),
            )
        if self.published_at_ms is not None:
            object.__setattr__(
                self, "published_at_ms", uint(self.published_at_ms, "published_at_ms")
            )
        if self.duration_ms is not None:
            object.__setattr__(self, "duration_ms", uint(self.duration_ms, "duration_ms"))
        if self.language_hint is not None and not isinstance(
            self.language_hint, EvidenceLanguageTag
        ):
            object.__setattr__(
                self, "language_hint", EvidenceLanguageTag(self.language_hint)  # type: ignore[arg-type]
            )
        if self.description_excerpt is not None:
            object.__setattr__(
                self,
                "description_excerpt",
                utf8_text(self.description_excerpt, 16384, "description_excerpt"),
            )
        if all(getattr(self, field) is None for field in _METADATA_OPTIONAL):
            raise SI02InvariantError(
                "SourceEvidenceMetadata requires at least one metadata value."
            )
        self.canonical_bytes()

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "evidence_digest": self.evidence_digest,
            "provenance_ref": self.provenance_ref,
            "schema_version": self.schema_version,
        }
        for field in (
            "title",
            "creator_label",
            "creator_identity",
            "published_at_ms",
            "duration_ms",
            "description_excerpt",
        ):
            field_value = getattr(self, field)
            if field_value is not None:
                value[field] = field_value
        if self.language_hint is not None:
            value["language_hint"] = self.language_hint.value
        return value

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict(), maximum_bytes=_METADATA_LIMIT)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SourceEvidenceMetadata":
        data = _object(value, "SourceEvidenceMetadata")
        _exact_fields(data, _METADATA_REQUIRED, _METADATA_OPTIONAL, "SourceEvidenceMetadata")
        language = data.get("language_hint")
        return cls(
            schema_version=schema_version(data["schema_version"]),
            evidence_digest=data["evidence_digest"],
            provenance_ref=data["provenance_ref"],
            title=data.get("title"),
            creator_label=data.get("creator_label"),
            creator_identity=data.get("creator_identity"),
            published_at_ms=data.get("published_at_ms"),
            duration_ms=data.get("duration_ms"),
            language_hint=(
                EvidenceLanguageTag.from_canonical(language) if language is not None else None
            ),
            description_excerpt=data.get("description_excerpt"),
        )

    @classmethod
    def from_json(cls, serialized: str | bytes) -> "SourceEvidenceMetadata":
        return cls.from_dict(parse_canonical_json(serialized, maximum_bytes=_METADATA_LIMIT))


def _enum(value: Any, enum_type: type, field: str):
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise SI02InvalidFieldError(f"Unsupported {field}: {value!r}.") from exc


@dataclass(frozen=True)
class ProviderTranscriptCandidate:
    schema_version: int
    source_identity: CanonicalSourceIdentity
    observation_identity: SourceObservationIdentity
    candidate_id: str
    candidate_kind: ProviderTranscriptKind
    language_hint: EvidenceLanguageTag
    is_translatable: bool
    evidence_byte_length: int
    evidence_format: ProviderTranscriptEvidenceFormat
    evidence_digest: str
    provenance_ref: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", schema_version(self.schema_version))
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
        object.__setattr__(self, "candidate_id", opaque_candidate_id(self.candidate_id))
        object.__setattr__(
            self,
            "candidate_kind",
            _enum(self.candidate_kind, ProviderTranscriptKind, "candidate_kind"),
        )
        if not isinstance(self.language_hint, EvidenceLanguageTag):
            object.__setattr__(
                self, "language_hint", EvidenceLanguageTag(self.language_hint)  # type: ignore[arg-type]
            )
        object.__setattr__(
            self, "is_translatable", boolean(self.is_translatable, "is_translatable")
        )
        object.__setattr__(
            self,
            "evidence_byte_length",
            positive_int(
                self.evidence_byte_length,
                "evidence_byte_length",
                maximum=16_777_216,
            ),
        )
        object.__setattr__(
            self,
            "evidence_format",
            _enum(self.evidence_format, ProviderTranscriptEvidenceFormat, "evidence_format"),
        )
        object.__setattr__(
            self, "evidence_digest", digest_sha256(self.evidence_digest, "evidence_digest")
        )
        object.__setattr__(
            self, "provenance_ref", digest_sha256(self.provenance_ref, "provenance_ref")
        )
        self.canonical_bytes()

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "candidate_kind": self.candidate_kind.value,
            "evidence_byte_length": self.evidence_byte_length,
            "evidence_digest": self.evidence_digest,
            "evidence_format": self.evidence_format.value,
            "is_translatable": self.is_translatable,
            "language_hint": self.language_hint.value,
            "observation_identity": self.observation_identity.to_dict(),
            "provenance_ref": self.provenance_ref,
            "schema_version": self.schema_version,
            "source_identity": self.source_identity.to_dict(),
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict(), maximum_bytes=_CANDIDATE_LIMIT)

    def canonical_order_key(self) -> tuple[Any, ...]:
        kind_order = list(ProviderTranscriptKind).index(self.candidate_kind)
        format_order = list(ProviderTranscriptEvidenceFormat).index(self.evidence_format)
        return (
            self.language_hint.value.encode("ascii"),
            kind_order,
            self.candidate_id.encode("ascii"),
            format_order,
            self.evidence_digest.encode("ascii"),
            self.canonical_bytes(),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProviderTranscriptCandidate":
        data = _object(value, "ProviderTranscriptCandidate")
        required = frozenset(
            {
                "schema_version",
                "source_identity",
                "observation_identity",
                "candidate_id",
                "candidate_kind",
                "language_hint",
                "is_translatable",
                "evidence_byte_length",
                "evidence_format",
                "evidence_digest",
                "provenance_ref",
            }
        )
        _exact_fields(data, required, frozenset(), "ProviderTranscriptCandidate")
        return cls(
            schema_version=schema_version(data["schema_version"]),
            source_identity=CanonicalSourceIdentity.from_dict(data["source_identity"]),
            observation_identity=SourceObservationIdentity.from_dict(
                data["observation_identity"]
            ),
            candidate_id=data["candidate_id"],
            candidate_kind=_enum(
                data["candidate_kind"], ProviderTranscriptKind, "candidate_kind"
            ),
            language_hint=EvidenceLanguageTag.from_canonical(data["language_hint"]),
            is_translatable=data["is_translatable"],
            evidence_byte_length=data["evidence_byte_length"],
            evidence_format=_enum(
                data["evidence_format"],
                ProviderTranscriptEvidenceFormat,
                "evidence_format",
            ),
            evidence_digest=data["evidence_digest"],
            provenance_ref=data["provenance_ref"],
        )

    @classmethod
    def from_json(cls, serialized: str | bytes) -> "ProviderTranscriptCandidate":
        return cls.from_dict(parse_canonical_json(serialized, maximum_bytes=_CANDIDATE_LIMIT))


def normalize_provider_transcript_candidates(
    candidates: Iterable[ProviderTranscriptCandidate],
) -> tuple[ProviderTranscriptCandidate, ...]:
    """Apply SI-02's exact pure pre-construction candidate normalization."""

    supplied = tuple(candidates)
    for candidate in supplied:
        if not isinstance(candidate, ProviderTranscriptCandidate):
            raise SI02InvalidFieldError(
                "Every provider transcript candidate must be independently valid."
            )

    lengths_by_digest: dict[str, int] = {}
    for candidate in supplied:
        previous = lengths_by_digest.setdefault(
            candidate.evidence_digest, candidate.evidence_byte_length
        )
        if previous != candidate.evidence_byte_length:
            raise SI02InvariantError(
                "Candidates sharing an evidence_digest must declare the same byte length."
            )

    retained: list[ProviderTranscriptCandidate] = []
    seen_digests: set[str] = set()
    for candidate in sorted(supplied, key=ProviderTranscriptCandidate.canonical_order_key):
        if candidate.evidence_digest not in seen_digests:
            retained.append(candidate)
            seen_digests.add(candidate.evidence_digest)
    return tuple(retained)


@dataclass(frozen=True)
class ValidatedAudioEvidence:
    schema_version: int
    source_identity: CanonicalSourceIdentity
    observation_identity: SourceObservationIdentity
    content_digest: str
    byte_length: int
    container: AudioContainer
    media_type: str
    provenance_ref: str
    duration_ms: int | None = None
    codec_label: str | None = None
    sample_rate_hz: int | None = None
    channel_count: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", schema_version(self.schema_version))
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
            self, "content_digest", digest_sha256(self.content_digest, "content_digest")
        )
        object.__setattr__(
            self,
            "byte_length",
            positive_int(self.byte_length, "byte_length", maximum=536_870_912),
        )
        object.__setattr__(
            self, "container", _enum(self.container, AudioContainer, "container")
        )
        media_type = ascii_text(self.media_type, 128, "media_type")
        if not _MEDIA_TYPE.fullmatch(media_type):
            raise SI02InvalidFieldError(
                "media_type must be a lowercase parameter-free type/subtype."
            )
        object.__setattr__(self, "media_type", media_type)
        object.__setattr__(
            self, "provenance_ref", digest_sha256(self.provenance_ref, "provenance_ref")
        )
        if self.duration_ms is not None:
            object.__setattr__(
                self,
                "duration_ms",
                positive_int(self.duration_ms, "duration_ms", maximum=7_200_000),
            )
        if self.codec_label is not None:
            object.__setattr__(
                self, "codec_label", ascii_text(self.codec_label, 128, "codec_label")
            )
        if self.sample_rate_hz is not None:
            object.__setattr__(
                self,
                "sample_rate_hz",
                positive_int(self.sample_rate_hz, "sample_rate_hz", maximum=768_000),
            )
        if self.channel_count is not None:
            object.__setattr__(
                self,
                "channel_count",
                positive_int(self.channel_count, "channel_count", maximum=64),
            )
        self.canonical_bytes()

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "byte_length": self.byte_length,
            "container": self.container.value,
            "content_digest": self.content_digest,
            "media_type": self.media_type,
            "observation_identity": self.observation_identity.to_dict(),
            "provenance_ref": self.provenance_ref,
            "schema_version": self.schema_version,
            "source_identity": self.source_identity.to_dict(),
        }
        for field in ("duration_ms", "codec_label", "sample_rate_hz", "channel_count"):
            field_value = getattr(self, field)
            if field_value is not None:
                value[field] = field_value
        return value

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict(), maximum_bytes=_AUDIO_LIMIT)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ValidatedAudioEvidence":
        data = _object(value, "ValidatedAudioEvidence")
        required = frozenset(
            {
                "schema_version",
                "source_identity",
                "observation_identity",
                "content_digest",
                "byte_length",
                "container",
                "media_type",
                "provenance_ref",
            }
        )
        optional = frozenset(
            {"duration_ms", "codec_label", "sample_rate_hz", "channel_count"}
        )
        _exact_fields(data, required, optional, "ValidatedAudioEvidence")
        return cls(
            schema_version=schema_version(data["schema_version"]),
            source_identity=CanonicalSourceIdentity.from_dict(data["source_identity"]),
            observation_identity=SourceObservationIdentity.from_dict(
                data["observation_identity"]
            ),
            content_digest=data["content_digest"],
            byte_length=data["byte_length"],
            container=_enum(data["container"], AudioContainer, "container"),
            media_type=data["media_type"],
            provenance_ref=data["provenance_ref"],
            duration_ms=data.get("duration_ms"),
            codec_label=data.get("codec_label"),
            sample_rate_hz=data.get("sample_rate_hz"),
            channel_count=data.get("channel_count"),
        )

    @classmethod
    def from_json(cls, serialized: str | bytes) -> "ValidatedAudioEvidence":
        return cls.from_dict(parse_canonical_json(serialized, maximum_bytes=_AUDIO_LIMIT))
