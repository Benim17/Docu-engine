"""Pure Step 5A contracts for versioned persistent cache entries.

This module performs no filesystem access.  Paths are lexical values only;
payload validation, lookup, locking, staging, promotion, and recovery belong to
later storage steps.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from .cache_keys import CacheKey


CACHE_ENTRY_CONTRACT_VERSION = 1
PAYLOAD_MANIFEST_VERSION = 1
RUNTIME_FINGERPRINT_SCHEMA_VERSION = 1
CACHE_KEY_CANONICAL_VERSION = 1

_NAMESPACE_SEGMENT = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,78}[a-z0-9])?$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_QUALIFIED_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_UTC_TIMESTAMP = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")
_MEDIA_TYPE = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$")
_ROLE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?$")
_WRITER_TOKEN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?$")


class CacheEntryContractError(ValueError):
    """Raised when a Step 5A cache-entry value violates contract v1."""


def _nonempty_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise CacheEntryContractError(f"{field_name} must be non-empty canonical text.")
    return value


def _not_physical_path(value: Any, field_name: str) -> str:
    text = _nonempty_text(value, field_name)
    if text.startswith(("/", "\\\\")) or _WINDOWS_ABSOLUTE.match(text):
        raise CacheEntryContractError(f"{field_name} must not contain a physical path.")
    return text


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CacheEntryContractError(f"{field_name} must be a positive integer.")
    return value


def _nonnegative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CacheEntryContractError(f"{field_name} must be a non-negative integer.")
    return value


def _version(value: Any, expected: int, field_name: str) -> int:
    if value != expected or isinstance(value, bool):
        raise CacheEntryContractError(f"Unsupported {field_name}: {value!r}.")
    return expected


def _digest(value: Any, field_name: str, *, qualified: bool) -> str:
    pattern = _QUALIFIED_SHA256 if qualified else _SHA256
    if not isinstance(value, str) or not pattern.fullmatch(value):
        syntax = "sha256:<64 lowercase hex>" if qualified else "64 lowercase hexadecimal characters"
        raise CacheEntryContractError(f"{field_name} must use {syntax}.")
    return value


def _timestamp(value: Any) -> str:
    if not isinstance(value, str) or not _UTC_TIMESTAMP.fullmatch(value):
        raise CacheEntryContractError("created_at_utc must use YYYY-MM-DDTHH:MM:SSZ.")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise CacheEntryContractError("created_at_utc must be a valid UTC timestamp.") from exc
    return value


def _object(value: Any, field_name: str) -> Mapping[str, Any]:
    if type(value) is not dict:
        raise CacheEntryContractError(f"{field_name} must be a JSON object.")
    return value


def _fields(value: Mapping[str, Any], expected: frozenset[str], field_name: str) -> None:
    actual = set(value)
    unknown = sorted(actual - expected)
    missing = sorted(expected - actual)
    if unknown:
        raise CacheEntryContractError(f"Unknown {field_name} fields: {', '.join(unknown)}.")
    if missing:
        raise CacheEntryContractError(f"Missing {field_name} fields: {', '.join(missing)}.")


def _reject_floats(value: Any, field_name: str = "value") -> None:
    if isinstance(value, float):
        raise CacheEntryContractError(f"{field_name} must not contain floats.")
    if value is None or isinstance(value, (bool, int, str)):
        return
    if type(value) is list or type(value) is tuple:
        for index, item in enumerate(value):
            _reject_floats(item, f"{field_name}[{index}]")
        return
    if type(value) is dict:
        for key, item in value.items():
            if not isinstance(key, str):
                raise CacheEntryContractError(f"{field_name} object keys must be strings.")
            _reject_floats(item, f"{field_name}.{key}")
        return
    raise CacheEntryContractError(
        f"{field_name} contains unsupported type {type(value).__name__}."
    )


def canonical_json_bytes(value: Any) -> bytes:
    """Encode contract JSON deterministically without whitespace or a newline."""

    _reject_floats(value)
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CacheEntryContractError("Value is not canonical contract JSON.") from exc


def parse_canonical_json(serialized: str | bytes) -> Any:
    """Parse strict UTF-8 JSON, rejecting duplicate keys, floats, and noncanonical bytes."""

    if isinstance(serialized, bytes):
        try:
            text = serialized.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise CacheEntryContractError("Contract JSON must be UTF-8.") from exc
        if serialized.startswith(b"\xef\xbb\xbf"):
            raise CacheEntryContractError("Contract JSON must not contain a BOM.")
    elif isinstance(serialized, str):
        text = serialized
    else:
        raise CacheEntryContractError("Contract JSON must be text or UTF-8 bytes.")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise CacheEntryContractError(f"Duplicate JSON key: {key}.")
            result[key] = value
        return result

    def forbidden_float(value: str) -> Any:
        raise CacheEntryContractError(f"Floating-point JSON value is forbidden: {value}.")

    def forbidden_constant(value: str) -> Any:
        raise CacheEntryContractError(f"Non-finite JSON value is forbidden: {value}.")

    try:
        result = json.loads(
            text,
            object_pairs_hook=pairs,
            parse_float=forbidden_float,
            parse_constant=forbidden_constant,
        )
    except CacheEntryContractError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise CacheEntryContractError("Contract document is not valid strict JSON.") from exc
    _reject_floats(result)
    if canonical_json_bytes(result) != text.encode("utf-8"):
        raise CacheEntryContractError("Contract JSON is not canonically encoded.")
    return result


@dataclass(frozen=True)
class CacheNamespace:
    domain: str
    producer_id: str
    producer_schema_version: int

    def __post_init__(self) -> None:
        for field_name, value in (("domain", self.domain), ("producer_id", self.producer_id)):
            if not isinstance(value, str) or not _NAMESPACE_SEGMENT.fullmatch(value) or ".." in value:
                raise CacheEntryContractError(
                    f"namespace.{field_name} must be a 1-80 character lowercase ASCII segment."
                )
        _positive_int(self.producer_schema_version, "namespace.producer_schema_version")
        if len(self.canonical) > 240:
            raise CacheEntryContractError("Canonical namespace must not exceed 240 characters.")

    @property
    def canonical(self) -> str:
        return f"{self.domain}/{self.producer_id}/{self.producer_schema_version}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "producer_id": self.producer_id,
            "producer_schema_version": self.producer_schema_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CacheNamespace":
        data = _object(value, "namespace")
        expected = frozenset({"domain", "producer_id", "producer_schema_version"})
        _fields(data, expected, "namespace")
        return cls(data["domain"], data["producer_id"], data["producer_schema_version"])


def derive_entry_digest(cache_key: CacheKey) -> str:
    if not isinstance(cache_key, CacheKey):
        raise CacheEntryContractError("cache_key must be a validated CacheKey.")
    return hashlib.sha256(cache_key.canonical_bytes()).hexdigest()


def digest_shards(entry_digest: str) -> tuple[str, str]:
    digest = _digest(entry_digest, "entry_digest", qualified=False)
    return digest[:2], digest[2:4]


def _root_path(cache_root: str | Path) -> Path:
    if not isinstance(cache_root, (str, Path)):
        raise CacheEntryContractError("cache_root must be a lexical path value.")
    if isinstance(cache_root, str) and not cache_root:
        raise CacheEntryContractError("cache_root must not be empty.")
    return Path(cache_root)


def derive_final_entry_path(cache_root: str | Path, namespace: CacheNamespace, cache_key: CacheKey) -> Path:
    if not isinstance(namespace, CacheNamespace):
        raise CacheEntryContractError("namespace must be a validated CacheNamespace.")
    digest = derive_entry_digest(cache_key)
    first, second = digest_shards(digest)
    return _root_path(cache_root) / "entries" / "v1" / namespace.domain / namespace.producer_id / str(namespace.producer_schema_version) / first / second / digest


def derive_staging_entry_path(cache_root: str | Path, namespace: CacheNamespace, cache_key: CacheKey, writer_token: str) -> Path:
    if not isinstance(namespace, CacheNamespace):
        raise CacheEntryContractError("namespace must be a validated CacheNamespace.")
    if not isinstance(writer_token, str) or not _WRITER_TOKEN.fullmatch(writer_token) or ".." in writer_token:
        raise CacheEntryContractError("writer_token must be a validated opaque path segment.")
    digest = derive_entry_digest(cache_key)
    return _root_path(cache_root) / "staging" / "v1" / namespace.domain / namespace.producer_id / str(namespace.producer_schema_version) / f"{digest}.{writer_token}"


def derive_lock_path(cache_root: str | Path, namespace: CacheNamespace, cache_key: CacheKey) -> Path:
    if not isinstance(namespace, CacheNamespace):
        raise CacheEntryContractError("namespace must be a validated CacheNamespace.")
    digest = derive_entry_digest(cache_key)
    first, second = digest_shards(digest)
    return _root_path(cache_root) / "locks" / "v1" / namespace.domain / namespace.producer_id / str(namespace.producer_schema_version) / first / second / f"{digest}.lock"


@dataclass(frozen=True)
class CacheKeyReference:
    canonical_version: int
    canonical_value: str

    def __post_init__(self) -> None:
        _version(self.canonical_version, CACHE_KEY_CANONICAL_VERSION, "cache key canonical_version")
        try:
            parsed = CacheKey.parse(self.canonical_value)
        except ValueError as exc:
            raise CacheEntryContractError("canonical_value must be a validated canonical CacheKey.") from exc
        if str(parsed) != self.canonical_value:
            raise CacheEntryContractError("canonical_value must use canonical CacheKey text.")

    @classmethod
    def from_cache_key(cls, cache_key: CacheKey) -> "CacheKeyReference":
        if not isinstance(cache_key, CacheKey):
            raise CacheEntryContractError("cache_key must be a validated CacheKey.")
        return cls(CACHE_KEY_CANONICAL_VERSION, str(cache_key))

    def to_cache_key(self) -> CacheKey:
        return CacheKey.parse(self.canonical_value)

    def to_dict(self) -> dict[str, Any]:
        return {"canonical_value": self.canonical_value, "canonical_version": self.canonical_version}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CacheKeyReference":
        data = _object(value, "cache_key")
        _fields(data, frozenset({"canonical_version", "canonical_value"}), "cache_key")
        return cls(data["canonical_version"], data["canonical_value"])


def _immutable_json_object(value: Any, field_name: str) -> tuple[tuple[str, bytes], ...]:
    data = _object(value, field_name)
    _reject_floats(data, field_name)
    pairs = []
    for key, item in data.items():
        _nonempty_text(key, f"{field_name} key")
        _reject_physical_paths(item, f"{field_name}.{key}")
        pairs.append((key, canonical_json_bytes(item)))
    return tuple(sorted(pairs))


def _reject_physical_paths(value: Any, field_name: str) -> None:
    if isinstance(value, str):
        _not_physical_path(value, field_name)
    elif type(value) is list or type(value) is tuple:
        for index, item in enumerate(value):
            _reject_physical_paths(item, f"{field_name}[{index}]")
    elif type(value) is dict:
        for key, item in value.items():
            _reject_physical_paths(item, f"{field_name}.{key}")


@dataclass(frozen=True, init=False)
class CacheRuntimeFingerprint:
    schema_version: int
    _values: tuple[tuple[str, bytes], ...] = field(repr=False)

    def __init__(self, schema_version: int, values: Mapping[str, Any]) -> None:
        _positive_int(schema_version, "runtime_fingerprint.schema_version")
        object.__setattr__(self, "schema_version", schema_version)
        object.__setattr__(self, "_values", _immutable_json_object(values, "runtime_fingerprint.values"))

    @property
    def values(self) -> dict[str, Any]:
        return {key: json.loads(value.decode("utf-8")) for key, value in self._values}

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "values": self.values}

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CacheRuntimeFingerprint":
        data = _object(value, "runtime_fingerprint")
        _fields(data, frozenset({"schema_version", "values"}), "runtime_fingerprint")
        return cls(data["schema_version"], data["values"])

    @classmethod
    def from_json(cls, serialized: str | bytes) -> "CacheRuntimeFingerprint":
        return cls.from_dict(_object(parse_canonical_json(serialized), "runtime_fingerprint"))


@dataclass(frozen=True)
class CacheArtifactMetadata:
    artifact_kind: str
    logical_id: str
    artifact_contract_version: int

    def __post_init__(self) -> None:
        _not_physical_path(self.artifact_kind, "artifact.artifact_kind")
        _not_physical_path(self.logical_id, "artifact.logical_id")
        _positive_int(self.artifact_contract_version, "artifact.artifact_contract_version")

    def to_dict(self) -> dict[str, Any]:
        return {"artifact_contract_version": self.artifact_contract_version, "artifact_kind": self.artifact_kind, "logical_id": self.logical_id}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CacheArtifactMetadata":
        data = _object(value, "artifact")
        _fields(data, frozenset({"artifact_kind", "logical_id", "artifact_contract_version"}), "artifact")
        return cls(data["artifact_kind"], data["logical_id"], data["artifact_contract_version"])


@dataclass(frozen=True)
class CacheProducerMetadata:
    producer_id: str
    producer_version: str
    producer_schema_version: int

    def __post_init__(self) -> None:
        if not isinstance(self.producer_id, str) or not _NAMESPACE_SEGMENT.fullmatch(self.producer_id) or ".." in self.producer_id:
            raise CacheEntryContractError("producer.producer_id must be a validated namespace segment.")
        _not_physical_path(self.producer_version, "producer.producer_version")
        _positive_int(self.producer_schema_version, "producer.producer_schema_version")

    def to_dict(self) -> dict[str, Any]:
        return {"producer_id": self.producer_id, "producer_schema_version": self.producer_schema_version, "producer_version": self.producer_version}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CacheProducerMetadata":
        data = _object(value, "producer")
        _fields(data, frozenset({"producer_id", "producer_version", "producer_schema_version"}), "producer")
        return cls(data["producer_id"], data["producer_version"], data["producer_schema_version"])


def _manifest_path(value: Any) -> str:
    path = _nonempty_text(value, "manifest relative_path")
    if "\\" in path or "\x00" in path or path.startswith("/") or path.endswith("/"):
        raise CacheEntryContractError("manifest relative_path must be a portable relative path.")
    parts = path.split("/")
    if any(not part or part in {".", ".."} or part.endswith((" ", ".")) for part in parts):
        raise CacheEntryContractError("manifest relative_path contains a forbidden path segment.")
    return path


@dataclass(frozen=True)
class PayloadManifestRecord:
    relative_path: str
    size_bytes: int
    digest: str
    media_type: str
    role: str

    def __post_init__(self) -> None:
        _manifest_path(self.relative_path)
        _nonnegative_int(self.size_bytes, "manifest size_bytes")
        _digest(self.digest, "manifest digest", qualified=True)
        if not isinstance(self.media_type, str) or not _MEDIA_TYPE.fullmatch(self.media_type):
            raise CacheEntryContractError("manifest media_type must be a valid type/subtype value.")
        if not isinstance(self.role, str) or not _ROLE.fullmatch(self.role):
            raise CacheEntryContractError("manifest role must be a lowercase semantic token.")

    def to_dict(self) -> dict[str, Any]:
        return {"digest": self.digest, "media_type": self.media_type, "relative_path": self.relative_path, "role": self.role, "size_bytes": self.size_bytes}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PayloadManifestRecord":
        data = _object(value, "manifest record")
        _fields(data, frozenset({"relative_path", "size_bytes", "digest", "media_type", "role"}), "manifest record")
        return cls(data["relative_path"], data["size_bytes"], data["digest"], data["media_type"], data["role"])


@dataclass(frozen=True)
class PayloadManifest:
    files: tuple[PayloadManifestRecord, ...]
    manifest_version: int = PAYLOAD_MANIFEST_VERSION

    def __post_init__(self) -> None:
        _version(self.manifest_version, PAYLOAD_MANIFEST_VERSION, "manifest_version")
        if not isinstance(self.files, tuple) or any(not isinstance(item, PayloadManifestRecord) for item in self.files):
            raise CacheEntryContractError("manifest files must be an immutable tuple of records.")
        paths = [item.relative_path for item in self.files]
        if paths != sorted(paths):
            raise CacheEntryContractError("manifest files must be sorted by relative_path.")
        if len(paths) != len(set(paths)):
            raise CacheEntryContractError("manifest relative paths must be unique.")
        # ASCII-only folding is normative; non-ASCII code points remain unchanged.
        portable = ["".join(char.lower() if "A" <= char <= "Z" else char for char in path) for path in paths]
        if len(portable) != len(set(portable)):
            raise CacheEntryContractError("manifest paths must not collide by ASCII case.")

    def to_dict(self) -> dict[str, Any]:
        return {"files": [item.to_dict() for item in self.files], "manifest_version": self.manifest_version}

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PayloadManifest":
        data = _object(value, "manifest")
        _fields(data, frozenset({"manifest_version", "files"}), "manifest")
        files = data["files"]
        if type(files) is not list:
            raise CacheEntryContractError("manifest files must be a JSON array.")
        return cls(tuple(PayloadManifestRecord.from_dict(item) for item in files), data["manifest_version"])

    @classmethod
    def from_json(cls, serialized: str | bytes) -> "PayloadManifest":
        return cls.from_dict(_object(parse_canonical_json(serialized), "manifest"))


@dataclass(frozen=True)
class CompletenessMarker:
    entry_digest: str
    metadata_digest: str
    manifest_digest: str
    cache_entry_contract_version: int = CACHE_ENTRY_CONTRACT_VERSION

    def __post_init__(self) -> None:
        _version(self.cache_entry_contract_version, CACHE_ENTRY_CONTRACT_VERSION, "cache_entry_contract_version")
        _digest(self.entry_digest, "entry_digest", qualified=False)
        _digest(self.metadata_digest, "metadata_digest", qualified=True)
        _digest(self.manifest_digest, "manifest_digest", qualified=True)

    def to_dict(self) -> dict[str, Any]:
        return {"cache_entry_contract_version": self.cache_entry_contract_version, "entry_digest": self.entry_digest, "manifest_digest": self.manifest_digest, "metadata_digest": self.metadata_digest}

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CompletenessMarker":
        data = _object(value, "COMPLETE")
        _fields(data, frozenset({"cache_entry_contract_version", "entry_digest", "metadata_digest", "manifest_digest"}), "COMPLETE")
        return cls(data["entry_digest"], data["metadata_digest"], data["manifest_digest"], data["cache_entry_contract_version"])

    @classmethod
    def from_json(cls, serialized: str | bytes) -> "CompletenessMarker":
        return cls.from_dict(_object(parse_canonical_json(serialized), "COMPLETE"))


@dataclass(frozen=True)
class CacheEntryMetadata:
    entry_digest: str
    cache_key: CacheKeyReference
    namespace: CacheNamespace
    artifact: CacheArtifactMetadata
    producer: CacheProducerMetadata
    runtime_fingerprint: CacheRuntimeFingerprint
    created_at_utc: str
    payload_manifest_digest: str
    payload_file_count: int
    payload_total_bytes: int
    cache_entry_contract_version: int = CACHE_ENTRY_CONTRACT_VERSION

    def __post_init__(self) -> None:
        _version(self.cache_entry_contract_version, CACHE_ENTRY_CONTRACT_VERSION, "cache_entry_contract_version")
        _digest(self.entry_digest, "entry_digest", qualified=False)
        for name, value, expected in (
            ("cache_key", self.cache_key, CacheKeyReference),
            ("namespace", self.namespace, CacheNamespace),
            ("artifact", self.artifact, CacheArtifactMetadata),
            ("producer", self.producer, CacheProducerMetadata),
            ("runtime_fingerprint", self.runtime_fingerprint, CacheRuntimeFingerprint),
        ):
            if not isinstance(value, expected):
                raise CacheEntryContractError(f"{name} must be {expected.__name__}.")
        if self.entry_digest != derive_entry_digest(self.cache_key.to_cache_key()):
            raise CacheEntryContractError("entry_digest does not match cache_key canonical bytes.")
        if self.namespace.producer_id != self.producer.producer_id:
            raise CacheEntryContractError("namespace and producer IDs must match.")
        if self.namespace.producer_schema_version != self.producer.producer_schema_version:
            raise CacheEntryContractError("namespace and producer schema versions must match.")
        _timestamp(self.created_at_utc)
        _digest(self.payload_manifest_digest, "payload_manifest_digest", qualified=True)
        _nonnegative_int(self.payload_file_count, "payload_file_count")
        _nonnegative_int(self.payload_total_bytes, "payload_total_bytes")

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact": self.artifact.to_dict(),
            "cache_entry_contract_version": self.cache_entry_contract_version,
            "cache_key": self.cache_key.to_dict(),
            "created_at_utc": self.created_at_utc,
            "entry_digest": self.entry_digest,
            "namespace": self.namespace.to_dict(),
            "payload_file_count": self.payload_file_count,
            "payload_manifest_digest": self.payload_manifest_digest,
            "payload_total_bytes": self.payload_total_bytes,
            "producer": self.producer.to_dict(),
            "runtime_fingerprint": self.runtime_fingerprint.to_dict(),
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CacheEntryMetadata":
        data = _object(value, "metadata")
        expected = frozenset({"cache_entry_contract_version", "entry_digest", "cache_key", "namespace", "artifact", "producer", "runtime_fingerprint", "created_at_utc", "payload_manifest_digest", "payload_file_count", "payload_total_bytes"})
        _fields(data, expected, "metadata")
        return cls(
            data["entry_digest"],
            CacheKeyReference.from_dict(data["cache_key"]),
            CacheNamespace.from_dict(data["namespace"]),
            CacheArtifactMetadata.from_dict(data["artifact"]),
            CacheProducerMetadata.from_dict(data["producer"]),
            CacheRuntimeFingerprint.from_dict(data["runtime_fingerprint"]),
            data["created_at_utc"],
            data["payload_manifest_digest"],
            data["payload_file_count"],
            data["payload_total_bytes"],
            data["cache_entry_contract_version"],
        )

    @classmethod
    def from_json(cls, serialized: str | bytes) -> "CacheEntryMetadata":
        return cls.from_dict(_object(parse_canonical_json(serialized), "metadata"))


@dataclass(frozen=True)
class CacheEntryContract:
    """Pure aggregate that enforces metadata/manifest cross-field invariants."""

    metadata: CacheEntryMetadata
    manifest: PayloadManifest

    def __post_init__(self) -> None:
        if not isinstance(self.metadata, CacheEntryMetadata) or not isinstance(self.manifest, PayloadManifest):
            raise CacheEntryContractError("CacheEntryContract requires metadata and manifest models.")
        if self.metadata.payload_file_count != len(self.manifest.files):
            raise CacheEntryContractError("metadata payload count does not match manifest.")
        if self.metadata.payload_total_bytes != sum(item.size_bytes for item in self.manifest.files):
            raise CacheEntryContractError("metadata payload byte total does not match manifest.")
        expected_digest = "sha256:" + hashlib.sha256(self.manifest.canonical_bytes()).hexdigest()
        if self.metadata.payload_manifest_digest != expected_digest:
            raise CacheEntryContractError("metadata manifest digest does not match manifest bytes.")


class CacheLookupStatus(str, Enum):
    UNSAFE_PATH = "unsafe_path"
    UNSUPPORTED_VERSION = "unsupported_version"
    INVALID_ENTRY = "invalid_entry"
    INTEGRITY_FAILURE = "integrity_failure"
    PRODUCER_MISMATCH = "producer_mismatch"
    SCHEMA_MISMATCH = "schema_mismatch"
    RUNTIME_FINGERPRINT_MISMATCH = "runtime_fingerprint_mismatch"
    LOCKED_OR_IN_PROGRESS = "locked_or_in_progress"
    MISS = "miss"
    HIT = "hit"


CACHE_LOOKUP_STATUS_PRECEDENCE = (
    CacheLookupStatus.UNSAFE_PATH,
    CacheLookupStatus.UNSUPPORTED_VERSION,
    CacheLookupStatus.INVALID_ENTRY,
    CacheLookupStatus.INTEGRITY_FAILURE,
    CacheLookupStatus.PRODUCER_MISMATCH,
    CacheLookupStatus.SCHEMA_MISMATCH,
    CacheLookupStatus.RUNTIME_FINGERPRINT_MISMATCH,
    CacheLookupStatus.LOCKED_OR_IN_PROGRESS,
    CacheLookupStatus.MISS,
    CacheLookupStatus.HIT,
)


@dataclass(frozen=True)
class CacheLookupExpectation:
    namespace: CacheNamespace
    producer_id: str
    producer_schema_version: int
    runtime_fingerprint: CacheRuntimeFingerprint

    def __post_init__(self) -> None:
        if not isinstance(self.namespace, CacheNamespace):
            raise CacheEntryContractError("lookup namespace must be a CacheNamespace.")
        if self.producer_id != self.namespace.producer_id or self.producer_schema_version != self.namespace.producer_schema_version:
            raise CacheEntryContractError("lookup producer identity must match namespace.")
        if not isinstance(self.runtime_fingerprint, CacheRuntimeFingerprint):
            raise CacheEntryContractError("lookup runtime_fingerprint must be explicit.")


@dataclass(frozen=True)
class CacheLookupResult:
    status: CacheLookupStatus
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, CacheLookupStatus):
            raise CacheEntryContractError("lookup status must be a CacheLookupStatus.")
        if not isinstance(self.diagnostics, tuple) or any(not isinstance(item, str) or not item for item in self.diagnostics):
            raise CacheEntryContractError("lookup diagnostics must be an immutable tuple of text.")
