"""Locked SI-01 immutable source identity models and pure YouTube canonicalization."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping
from urllib.parse import unquote_to_bytes, urlsplit

from ._canonical_json import (
    SI01InvalidFieldError,
    SI01InvalidYouTubeReferenceError,
    SI01MalformedDataError,
    SI01UnsupportedVersionError,
    canonical_json_bytes,
    parse_canonical_json,
)


_SOURCE_REFERENCE_LIMIT = 5_120
_CANONICAL_IDENTITY_LIMIT = 1_024
_OBSERVATION_IDENTITY_LIMIT = 1_536
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_TIME = re.compile(r"^[0-9]{1,8}$")
_IGNORED_QUERY_KEYS = frozenset({"t", "start", "si", "feature", "list", "index"})
_STRUCTURAL_DECODE_BYTES = frozenset(b"&=%#?/\\")


class SourceKind(str, Enum):
    YOUTUBE_VIDEO = "YOUTUBE_VIDEO"
    WEB_PAGE = "WEB_PAGE"
    PDF_DOCUMENT = "PDF_DOCUMENT"
    TEXT = "TEXT"
    AUDIO_FILE = "AUDIO_FILE"
    VIDEO_FILE = "VIDEO_FILE"


def _object(value: Any, model: str) -> Mapping[str, Any]:
    if type(value) is not dict:
        raise SI01MalformedDataError(f"{model} must be a JSON object.")
    return value


def _fields(
    value: Mapping[str, Any], required: frozenset[str], optional: frozenset[str], model: str
) -> None:
    actual = set(value)
    unknown = sorted(actual - required - optional)
    missing = sorted(required - actual)
    if unknown:
        raise SI01MalformedDataError(f"Unknown {model} fields: {', '.join(unknown)}.")
    if missing:
        raise SI01MalformedDataError(f"Missing {model} fields: {', '.join(missing)}.")


def _schema_version(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SI01InvalidFieldError("schema_version must be an integer.")
    if value != 1:
        raise SI01UnsupportedVersionError(f"Unsupported schema_version: {value!r}.")
    return value


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SI01InvalidFieldError(f"{field} must be a positive integer.")
    return value


def _utf8_text(value: Any, maximum: int, field: str) -> str:
    if not isinstance(value, str):
        raise SI01InvalidFieldError(f"{field} must be Unicode text.")
    normalized = unicodedata.normalize("NFC", value)
    try:
        length = len(normalized.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise SI01InvalidFieldError(f"{field} must be valid Unicode text.") from exc
    if not 1 <= length <= maximum:
        raise SI01InvalidFieldError(f"{field} must contain 1..{maximum} UTF-8 bytes.")
    return normalized


def _ascii_text(value: Any, maximum: int, field: str) -> str:
    if not isinstance(value, str):
        raise SI01InvalidFieldError(f"{field} must be ASCII text.")
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise SI01InvalidFieldError(f"{field} must be ASCII text.") from exc
    if not 1 <= len(encoded) <= maximum:
        raise SI01InvalidFieldError(f"{field} must contain 1..{maximum} ASCII bytes.")
    return value


def _source_kind(value: Any) -> SourceKind:
    if isinstance(value, SourceKind):
        return value
    try:
        return SourceKind(value)
    except (TypeError, ValueError) as exc:
        raise SI01InvalidFieldError(f"Unsupported source_kind: {value!r}.") from exc


@dataclass(frozen=True)
class SourceReference:
    schema_version: int
    source_kind: SourceKind
    reference_value: str
    display_label: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _schema_version(self.schema_version))
        object.__setattr__(self, "source_kind", _source_kind(self.source_kind))
        object.__setattr__(
            self, "reference_value", _utf8_text(self.reference_value, 4096, "reference_value")
        )
        if self.display_label is not None:
            object.__setattr__(
                self, "display_label", _utf8_text(self.display_label, 512, "display_label")
            )

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "reference_value": self.reference_value,
            "schema_version": self.schema_version,
            "source_kind": self.source_kind.value,
        }
        if self.display_label is not None:
            value["display_label"] = self.display_label
        return value

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict(), maximum_bytes=_SOURCE_REFERENCE_LIMIT)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SourceReference":
        data = _object(value, "SourceReference")
        _fields(
            data,
            frozenset({"schema_version", "source_kind", "reference_value"}),
            frozenset({"display_label"}),
            "SourceReference",
        )
        if "display_label" in data and data["display_label"] is None:
            raise SI01MalformedDataError(
                "SourceReference.display_label must be absent rather than null."
            )
        return cls(
            schema_version=_schema_version(data["schema_version"]),
            source_kind=_source_kind(data["source_kind"]),
            reference_value=data["reference_value"],
            display_label=data.get("display_label"),
        )

    @classmethod
    def from_json(cls, serialized: str | bytes) -> "SourceReference":
        return cls.from_dict(parse_canonical_json(serialized, maximum_bytes=_SOURCE_REFERENCE_LIMIT))


@dataclass(frozen=True)
class CanonicalSourceIdentity:
    schema_version: int
    source_kind: SourceKind
    identity_scheme: str
    identity_scheme_version: int
    canonical_value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _schema_version(self.schema_version))
        object.__setattr__(self, "source_kind", _source_kind(self.source_kind))
        object.__setattr__(
            self, "identity_scheme", _ascii_text(self.identity_scheme, 64, "identity_scheme")
        )
        object.__setattr__(
            self,
            "identity_scheme_version",
            _positive_int(self.identity_scheme_version, "identity_scheme_version"),
        )
        object.__setattr__(
            self, "canonical_value", _ascii_text(self.canonical_value, 512, "canonical_value")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_value": self.canonical_value,
            "identity_scheme": self.identity_scheme,
            "identity_scheme_version": self.identity_scheme_version,
            "schema_version": self.schema_version,
            "source_kind": self.source_kind.value,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict(), maximum_bytes=_CANONICAL_IDENTITY_LIMIT)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CanonicalSourceIdentity":
        data = _object(value, "CanonicalSourceIdentity")
        _fields(
            data,
            frozenset(
                {
                    "schema_version",
                    "source_kind",
                    "identity_scheme",
                    "identity_scheme_version",
                    "canonical_value",
                }
            ),
            frozenset(),
            "CanonicalSourceIdentity",
        )
        return cls(
            schema_version=_schema_version(data["schema_version"]),
            source_kind=_source_kind(data["source_kind"]),
            identity_scheme=data["identity_scheme"],
            identity_scheme_version=data["identity_scheme_version"],
            canonical_value=data["canonical_value"],
        )

    @classmethod
    def from_json(cls, serialized: str | bytes) -> "CanonicalSourceIdentity":
        return cls.from_dict(
            parse_canonical_json(serialized, maximum_bytes=_CANONICAL_IDENTITY_LIMIT)
        )


@dataclass(frozen=True)
class SourceObservationIdentity:
    schema_version: int
    source_identity: CanonicalSourceIdentity
    observation_scheme: str
    observation_scheme_version: int
    observation_value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _schema_version(self.schema_version))
        if not isinstance(self.source_identity, CanonicalSourceIdentity):
            raise SI01InvalidFieldError(
                "source_identity must be a CanonicalSourceIdentity."
            )
        object.__setattr__(
            self,
            "observation_scheme",
            _ascii_text(self.observation_scheme, 64, "observation_scheme"),
        )
        object.__setattr__(
            self,
            "observation_scheme_version",
            _positive_int(self.observation_scheme_version, "observation_scheme_version"),
        )
        object.__setattr__(
            self,
            "observation_value",
            _ascii_text(self.observation_value, 256, "observation_value"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_scheme": self.observation_scheme,
            "observation_scheme_version": self.observation_scheme_version,
            "observation_value": self.observation_value,
            "schema_version": self.schema_version,
            "source_identity": self.source_identity.to_dict(),
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict(), maximum_bytes=_OBSERVATION_IDENTITY_LIMIT)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SourceObservationIdentity":
        data = _object(value, "SourceObservationIdentity")
        _fields(
            data,
            frozenset(
                {
                    "schema_version",
                    "source_identity",
                    "observation_scheme",
                    "observation_scheme_version",
                    "observation_value",
                }
            ),
            frozenset(),
            "SourceObservationIdentity",
        )
        return cls(
            schema_version=_schema_version(data["schema_version"]),
            source_identity=CanonicalSourceIdentity.from_dict(data["source_identity"]),
            observation_scheme=data["observation_scheme"],
            observation_scheme_version=data["observation_scheme_version"],
            observation_value=data["observation_value"],
        )

    @classmethod
    def from_json(cls, serialized: str | bytes) -> "SourceObservationIdentity":
        return cls.from_dict(
            parse_canonical_json(serialized, maximum_bytes=_OBSERVATION_IDENTITY_LIMIT)
        )


def _invalid(message: str) -> SI01InvalidYouTubeReferenceError:
    return SI01InvalidYouTubeReferenceError(message)


def _strict_percent_decode(raw: str, field: str) -> str:
    index = 0
    while index < len(raw):
        if raw[index] != "%":
            index += 1
            continue
        if index + 2 >= len(raw) or not re.fullmatch(r"[0-9A-Fa-f]{2}", raw[index + 1:index + 3]):
            raise _invalid(f"Malformed percent escape in {field}.")
        if int(raw[index + 1:index + 3], 16) in _STRUCTURAL_DECODE_BYTES:
            raise _invalid(f"Encoded structural delimiter in {field}.")
        index += 3
    try:
        return unquote_to_bytes(raw).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _invalid(f"Percent-decoded {field} must be UTF-8.") from exc


def _time_value(value: str, field: str, *, suffix_allowed: bool = False) -> None:
    candidate = value[:-1] if suffix_allowed and value.endswith("s") else value
    if not _TIME.fullmatch(candidate) or int(candidate) > 86_400_000:
        raise _invalid(f"Invalid YouTube {field} value.")


def _query(raw_query: str, *, require_video: bool) -> str | None:
    decoded: dict[str, str] = {}
    raw_values: dict[str, str] = {}
    if raw_query:
        for member in raw_query.split("&"):
            if not member or member.count("=") != 1:
                raise _invalid("Each YouTube query member must contain exactly one '='.")
            raw_key, raw_value = member.split("=", 1)
            if not raw_key:
                raise _invalid("YouTube query keys must not be empty.")
            if "%" in raw_key:
                # Keys are closed ASCII tokens; encoded keys are never canonical.
                _strict_percent_decode(raw_key, "query key")
                raise _invalid("Percent-encoded YouTube query keys are forbidden.")
            key = _strict_percent_decode(raw_key, "query key")
            value = _strict_percent_decode(raw_value, f"query value {key!r}")
            if not key or key in decoded:
                raise _invalid("Duplicate or empty decoded YouTube query key.")
            decoded[key] = value
            raw_values[key] = raw_value

    allowed = _IGNORED_QUERY_KEYS | ({"v"} if require_video else frozenset())
    if set(decoded) - allowed:
        raise _invalid("Unknown YouTube query key.")
    if not require_video and "v" in decoded:
        raise _invalid("The v query key is forbidden for this YouTube form.")
    if require_video and "v" not in decoded:
        raise _invalid("The /watch form requires exactly one v query key.")

    video_id = decoded.get("v")
    if video_id is not None:
        if "%" in raw_values["v"] or not _VIDEO_ID.fullmatch(video_id):
            raise _invalid("Invalid or percent-encoded YouTube video ID.")

    for key, value in decoded.items():
        if key == "v":
            continue
        if not value:
            raise _invalid(f"YouTube query value {key!r} must not be blank.")
        if key in {"t", "start"}:
            _time_value(value, key)
        else:
            normalized = unicodedata.normalize("NFC", value)
            try:
                size = len(normalized.encode("utf-8"))
            except UnicodeEncodeError as exc:
                raise _invalid(f"YouTube query value {key!r} must be Unicode.") from exc
            if not 1 <= size <= 512:
                raise _invalid(f"YouTube query value {key!r} exceeds its byte bound.")
    return video_id


def canonicalize_youtube_reference(reference: str | SourceReference) -> CanonicalSourceIdentity:
    """Derive youtube-video-id/v1 by lexical parsing only, with no I/O."""

    if isinstance(reference, SourceReference):
        if reference.source_kind is not SourceKind.YOUTUBE_VIDEO:
            raise _invalid("SourceReference must have source_kind YOUTUBE_VIDEO.")
        raw = reference.reference_value
    elif isinstance(reference, str):
        raw = reference
    else:
        raise _invalid("YouTube reference must be text or a SourceReference.")
    if not raw:
        raise _invalid("YouTube reference must not be empty.")
    if ord(raw[0]) <= 0x20:
        raise _invalid(
            "YouTube references must not begin with an ASCII C0 control or space."
        )
    if any(character in raw for character in ("\t", "\r", "\n")):
        raise _invalid("YouTube references must not contain TAB, CR, or LF.")

    try:
        parts = urlsplit(raw)
    except ValueError as exc:
        raise _invalid("YouTube reference is not a valid absolute URI.") from exc
    if parts.scheme.lower() != "https" or not parts.netloc:
        raise _invalid("YouTube references require HTTPS and an authority.")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise _invalid("YouTube user information is forbidden.")

    authority = parts.netloc
    if any(ord(character) > 127 for character in authority):
        raise _invalid("YouTube host and port must be ASCII.")
    if authority.count(":") > 1:
        raise _invalid("Invalid YouTube authority.")
    if ":" in authority:
        raw_host, raw_port = authority.rsplit(":", 1)
        if raw_port != "443":
            raise _invalid("Only absent or lexical port 443 is accepted.")
    else:
        raw_host = authority
    host = raw_host.lower()
    if host.endswith("."):
        raise _invalid("Trailing DNS dots are forbidden.")
    if host not in {"youtube.com", "www.youtube.com", "youtu.be"}:
        raise _invalid("Unsupported YouTube host.")

    if "#" in raw and parts.fragment == "":
        raise _invalid("Empty YouTube fragments are forbidden.")
    if parts.fragment:
        fragment = _strict_percent_decode(parts.fragment, "fragment")
        if not fragment.startswith("t="):
            raise _invalid("Unsupported YouTube fragment.")
        _time_value(fragment[2:], "fragment time", suffix_allowed=True)

    path = parts.path
    if host in {"youtube.com", "www.youtube.com"}:
        if path == "/watch":
            video_id = _query(parts.query, require_video=True)
        elif path.startswith("/shorts/") and path.count("/") == 2:
            video_id = path[len("/shorts/"):]
            if "%" in video_id or not _VIDEO_ID.fullmatch(video_id):
                raise _invalid("Invalid YouTube Shorts video ID.")
            _query(parts.query, require_video=False)
        else:
            raise _invalid("Unsupported YouTube path.")
    else:
        if path.count("/") != 1 or not path.startswith("/"):
            raise _invalid("Unsupported youtu.be path.")
        video_id = path[1:]
        if "%" in video_id or not _VIDEO_ID.fullmatch(video_id):
            raise _invalid("Invalid youtu.be video ID.")
        _query(parts.query, require_video=False)

    assert video_id is not None
    return CanonicalSourceIdentity(
        schema_version=1,
        source_kind=SourceKind.YOUTUBE_VIDEO,
        identity_scheme="youtube-video-id",
        identity_scheme_version=1,
        canonical_value=video_id,
    )
