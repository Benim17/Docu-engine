"""Strict, pure canonical JSON primitives for Source Understanding contracts."""

from __future__ import annotations

import json
import unicodedata
from typing import Any


class SI01Error(ValueError):
    """Base error for violations of the locked SI-01 contract."""


class SI01MalformedDataError(SI01Error):
    """Serialized input is malformed or is not canonical SI-01 JSON."""


class SI01UnsupportedVersionError(SI01Error):
    """A syntactically valid model uses an unsupported schema version."""


class SI01SizeLimitError(SI01Error):
    """Serialized input or output exceeds its model's SI-01 byte limit."""


class SI01InvalidFieldError(SI01Error):
    """A model field violates the locked SI-01 field contract."""


class SI01InvalidYouTubeReferenceError(SI01Error):
    """A YouTube reference violates the locked lexical identity scheme."""


def _normalize_json(value: Any, field: str = "value") -> Any:
    if value is None:
        raise SI01MalformedDataError(f"{field} must not contain JSON null.")
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        raise SI01MalformedDataError(f"{field} must not contain floats.")
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if type(value) in (list, tuple):
        return [_normalize_json(item, f"{field}[{index}]") for index, item in enumerate(value)]
    if type(value) is dict:
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise SI01MalformedDataError(f"{field} object keys must be strings.")
            normalized_key = unicodedata.normalize("NFC", key)
            if normalized_key in normalized:
                raise SI01MalformedDataError(
                    f"{field} contains keys that collide after NFC normalization."
                )
            normalized[normalized_key] = _normalize_json(item, f"{field}.{normalized_key}")
        return normalized
    raise SI01MalformedDataError(
        f"{field} contains unsupported type {type(value).__name__}."
    )


def canonical_json_bytes(value: Any, *, maximum_bytes: int | None = None) -> bytes:
    """Return strict UTF-8, NFC, compact, sorted, newline-free JSON bytes."""

    normalized = _normalize_json(value)
    try:
        encoded = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise SI01MalformedDataError("Value is not canonical SI-01 JSON.") from exc
    if maximum_bytes is not None and len(encoded) > maximum_bytes:
        raise SI01SizeLimitError(
            f"Canonical SI-01 JSON exceeds the {maximum_bytes}-byte limit."
        )
    return encoded


def parse_canonical_json(serialized: str | bytes, *, maximum_bytes: int) -> Any:
    """Apply the byte gate, then parse and verify canonical SI-01 JSON."""

    if isinstance(serialized, bytes):
        encoded = serialized
    elif isinstance(serialized, str):
        try:
            encoded = serialized.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise SI01MalformedDataError("SI-01 JSON must be valid UTF-8.") from exc
    else:
        raise SI01MalformedDataError("SI-01 JSON must be text or UTF-8 bytes.")

    if len(encoded) > maximum_bytes:
        raise SI01SizeLimitError(
            f"Serialized SI-01 JSON exceeds the {maximum_bytes}-byte limit."
        )
    if encoded.startswith(b"\xef\xbb\xbf"):
        raise SI01MalformedDataError("SI-01 JSON must not contain a BOM.")
    try:
        text = encoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SI01MalformedDataError("SI-01 JSON must be valid UTF-8.") from exc

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise SI01MalformedDataError(f"Duplicate JSON key: {key}.")
            result[key] = value
        return result

    def forbidden_float(value: str) -> Any:
        raise SI01MalformedDataError(f"Floating-point JSON is forbidden: {value}.")

    def forbidden_constant(value: str) -> Any:
        raise SI01MalformedDataError(f"Non-finite JSON is forbidden: {value}.")

    try:
        value = json.loads(
            text,
            object_pairs_hook=pairs,
            parse_float=forbidden_float,
            parse_constant=forbidden_constant,
        )
    except SI01MalformedDataError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise SI01MalformedDataError("Input is not valid strict JSON.") from exc

    if canonical_json_bytes(value) != encoded:
        raise SI01MalformedDataError("SI-01 JSON is not canonically encoded.")
    return value
