"""Pure validation primitives for the locked SI-02 Evidence contract."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from ._canonical_json import (
    SI01MalformedDataError,
    SI01SizeLimitError,
    canonical_json_bytes as _si01_canonical_json_bytes,
    parse_canonical_json as _si01_parse_canonical_json,
)


class SI02Error(ValueError):
    """Base error for violations of the locked SI-02 contract."""


class SI02MalformedDataError(SI02Error):
    """Serialized input is malformed or is not canonical SI-02 JSON."""


class SI02UnsupportedVersionError(SI02Error):
    """A syntactically valid SI-02 model uses an unsupported version."""


class SI02SizeLimitError(SI02Error):
    """Serialized input or output exceeds an SI-02 byte limit."""


class SI02InvalidFieldError(SI02Error):
    """An SI-02 field violates its locked intrinsic contract."""


class SI02InvariantError(SI02Error):
    """Individually valid SI-02 values violate a cross-model invariant."""


_DIGEST_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_OPAQUE_CANDIDATE_ID = re.compile(r"^[A-Za-z0-9._~-]{1,256}$")
_LOGICAL_COMPONENT_ID = re.compile(
    r"^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$"
)
_COMPONENT_VERSION = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._+~-]{0,62}[A-Za-z0-9])?$"
)


def canonical_json_bytes(value: Any, *, maximum_bytes: int | None = None) -> bytes:
    """Serialize with SI-01's locked canonical rules and SI-02 errors."""

    try:
        return _si01_canonical_json_bytes(value, maximum_bytes=maximum_bytes)
    except SI01SizeLimitError as exc:
        raise SI02SizeLimitError(str(exc).replace("SI-01", "SI-02")) from exc
    except SI01MalformedDataError as exc:
        raise SI02MalformedDataError(str(exc).replace("SI-01", "SI-02")) from exc


def parse_canonical_json(serialized: str | bytes, *, maximum_bytes: int) -> Any:
    """Parse with SI-01's locked canonical rules and SI-02 errors."""

    try:
        return _si01_parse_canonical_json(serialized, maximum_bytes=maximum_bytes)
    except SI01SizeLimitError as exc:
        raise SI02SizeLimitError(str(exc).replace("SI-01", "SI-02")) from exc
    except SI01MalformedDataError as exc:
        raise SI02MalformedDataError(str(exc).replace("SI-01", "SI-02")) from exc


def schema_version(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SI02InvalidFieldError("schema_version must be an integer.")
    if value != 1:
        raise SI02UnsupportedVersionError(f"Unsupported schema_version: {value!r}.")
    return value


def uint(value: Any, field: str, *, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SI02InvalidFieldError(f"{field} must be an unsigned integer.")
    if maximum is not None and value > maximum:
        raise SI02InvalidFieldError(f"{field} must not exceed {maximum}.")
    return value


def positive_int(value: Any, field: str, *, maximum: int | None = None) -> int:
    result = uint(value, field, maximum=maximum)
    if result == 0:
        raise SI02InvalidFieldError(f"{field} must be a positive integer.")
    return result


def boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise SI02InvalidFieldError(f"{field} must be a boolean.")
    return value


def ascii_text(value: Any, maximum: int, field: str) -> str:
    if not isinstance(value, str):
        raise SI02InvalidFieldError(f"{field} must be ASCII text.")
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise SI02InvalidFieldError(f"{field} must be ASCII text.") from exc
    if not 1 <= len(encoded) <= maximum:
        raise SI02InvalidFieldError(
            f"{field} must contain 1..{maximum} ASCII bytes."
        )
    return value


def utf8_text(value: Any, maximum: int, field: str) -> str:
    if not isinstance(value, str):
        raise SI02InvalidFieldError(f"{field} must be Unicode text.")
    normalized = unicodedata.normalize("NFC", value)
    try:
        encoded = normalized.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise SI02InvalidFieldError(f"{field} must be valid Unicode text.") from exc
    if not 1 <= len(encoded) <= maximum:
        raise SI02InvalidFieldError(
            f"{field} must contain 1..{maximum} UTF-8 bytes."
        )
    return normalized


def digest_sha256(value: Any, field: str) -> str:
    candidate = ascii_text(value, 71, field)
    if not _DIGEST_SHA256.fullmatch(candidate):
        raise SI02InvalidFieldError(f"{field} must be an algorithm-qualified SHA-256 digest.")
    return candidate


def opaque_candidate_id(value: Any, field: str = "candidate_id") -> str:
    candidate = ascii_text(value, 256, field)
    if not _OPAQUE_CANDIDATE_ID.fullmatch(candidate):
        raise SI02InvalidFieldError(f"{field} has invalid opaque-candidate syntax.")
    return candidate


def logical_component_id(value: Any, field: str) -> str:
    candidate = ascii_text(value, 128, field)
    if not _LOGICAL_COMPONENT_ID.fullmatch(candidate):
        raise SI02InvalidFieldError(f"{field} has invalid logical-component syntax.")
    return candidate


def component_version(value: Any, field: str) -> str:
    candidate = ascii_text(value, 64, field)
    if not _COMPONENT_VERSION.fullmatch(candidate):
        raise SI02InvalidFieldError(f"{field} has invalid component-version syntax.")
    return candidate
