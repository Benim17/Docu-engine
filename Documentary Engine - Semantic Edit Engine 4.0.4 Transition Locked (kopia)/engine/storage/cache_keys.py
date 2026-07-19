"""Pure deterministic cache keys for expected, pre-production artifact content.

Keys include logical artifact or group contracts, resolved input digests,
relevant config values, producer identity, and optional supplied runtime/backend
fingerprints. They exclude runtime locations and physical presence/integrity.
A key does not prove that a stored payload exists, is complete, or is correct;
payload verification belongs to a later storage step.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Mapping

from .models import (
    ArtifactContractError,
    ArtifactDescriptor,
    ArtifactGroup,
    ArtifactType,
    ProducerIdentity,
)


CACHE_KEY_CONTRACT_VERSION = "1.0"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_ABSOLUTE_PATTERN = re.compile(r"^[A-Za-z]:[\\/]")
_CONTRACT_VERSION_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+)*$")


class CacheKeyContractError(ArtifactContractError):
    """Raised when cache key material is unsafe or non-canonical."""


def _text(value: Any, field_name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        raise CacheKeyContractError(f"{field_name} must be a non-empty string.")
    normalized = unicodedata.normalize("NFC", value)
    if _looks_absolute(normalized):
        raise CacheKeyContractError(f"{field_name} must not contain an absolute path.")
    return normalized


def _looks_absolute(value: str) -> bool:
    return value.startswith(("/", "\\\\")) or bool(_WINDOWS_ABSOLUTE_PATTERN.match(value))


def _logical_name(value: str | None) -> str | None:
    normalized = _text(value, "logical_name", optional=True)
    if normalized is None:
        return None
    normalized = normalized.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part != "."]
    if not parts or any(part in {"", ".."} for part in parts):
        raise CacheKeyContractError("logical_name must be a normalized relative logical name.")
    return "/".join(parts)


def _config_path(value: str) -> str:
    normalized = _text(value, "config path")
    assert normalized is not None
    if "\\" in normalized or normalized.startswith(".") or normalized.endswith("."):
        raise CacheKeyContractError("Config path must be a dotted logical path.")
    parts = normalized.split(".")
    if any(not part or part == ".." for part in parts):
        raise CacheKeyContractError("Config path must be a dotted logical path.")
    return normalized


def _sha256_digest(value: Any, field_name: str = "sha256 digest") -> str:
    if not isinstance(value, str):
        raise CacheKeyContractError(f"{field_name} must be text.")
    normalized = value.lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise CacheKeyContractError(
            f"{field_name} must contain exactly 64 hexadecimal characters without whitespace."
        )
    return normalized


def _contract_version(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _CONTRACT_VERSION_PATTERN.fullmatch(value):
        raise CacheKeyContractError(
            f"{field_name} must be a dotted numeric contract version."
        )
    return value


def _normalize_json_value(value: Any, field_name: str = "value") -> Any:
    """Normalize supported JSON values without mutating caller-owned objects.

    Accepted Python values are None, bool, int, finite float, str, list, tuple,
    and dict with string keys. Tuples normalize to JSON arrays. Sets, bytes,
    non-string mapping keys, and all unknown objects are rejected. Negative zero
    normalizes to positive 0.0. String content is Unicode NFC normalized and
    generic strings are treated as data. Callers must omit machine-specific path
    settings rather than relying on heuristic path detection in this layer.
    """

    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CacheKeyContractError(f"{field_name} must contain only finite floats.")
        return 0.0 if value == 0.0 else value
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, (list, tuple)):
        return [
            _normalize_json_value(item, f"{field_name}[{index}]")
            for index, item in enumerate(value)
        ]
    if type(value) is dict:
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CacheKeyContractError(f"{field_name} object keys must be strings.")
            normalized_key = unicodedata.normalize("NFC", key)
            if normalized_key in normalized:
                raise CacheKeyContractError(
                    f"{field_name} contains keys that collide after Unicode normalization."
                )
            normalized[normalized_key] = _normalize_json_value(
                item, f"{field_name}.{normalized_key}"
            )
        return normalized
    raise CacheKeyContractError(
        f"{field_name} contains unsupported type {type(value).__name__!r}."
    )


def _canonical_json_bytes(value: Any) -> bytes:
    normalized = _normalize_json_value(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


@dataclass(frozen=True)
class InputFingerprint:
    """Resolved content digest for one semantically ordered input role."""

    role: str
    artifact_type: ArtifactType
    digest: str
    logical_name: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _text(self.role, "input role"))
        if not isinstance(self.artifact_type, ArtifactType):
            raise CacheKeyContractError("artifact_type must be an ArtifactType.")
        object.__setattr__(self, "digest", _sha256_digest(self.digest))
        object.__setattr__(self, "logical_name", _logical_name(self.logical_name))

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_type": self.artifact_type.value,
            "digest": self.digest,
            "logical_name": self.logical_name,
            "role": self.role,
        }


@dataclass(frozen=True, init=False)
class ConfigFingerprint:
    """One relevant logical config path and its immutable normalized JSON value."""

    path: str
    _canonical_value: bytes = field(repr=False)

    def __init__(self, path: str, value: Any) -> None:
        object.__setattr__(self, "path", _config_path(path))
        object.__setattr__(self, "_canonical_value", _canonical_json_bytes(value))

    @property
    def value(self) -> Any:
        return json.loads(self._canonical_value.decode("utf-8"))

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "value": self.value}


@dataclass(frozen=True, init=False)
class RuntimeFingerprint:
    """Explicit backend/runtime factors supplied by a producer, never auto-detected."""

    backend_name: str | None
    backend_version: str | None
    model_name: str | None
    model_revision: str | None
    library_versions: tuple[tuple[str, str], ...]
    platform_profile: str | None
    algorithm_profile: str | None

    def __init__(
        self,
        *,
        backend_name: str | None = None,
        backend_version: str | None = None,
        model_name: str | None = None,
        model_revision: str | None = None,
        library_versions: Mapping[str, str] | None = None,
        platform_profile: str | None = None,
        algorithm_profile: str | None = None,
    ) -> None:
        object.__setattr__(self, "backend_name", _text(backend_name, "backend_name", optional=True))
        object.__setattr__(
            self, "backend_version", _text(backend_version, "backend_version", optional=True)
        )
        object.__setattr__(self, "model_name", _text(model_name, "model_name", optional=True))
        object.__setattr__(
            self, "model_revision", _text(model_revision, "model_revision", optional=True)
        )
        if library_versions is None:
            normalized_libraries: tuple[tuple[str, str], ...] = ()
        elif type(library_versions) is dict:
            pairs: list[tuple[str, str]] = []
            for name, version in library_versions.items():
                normalized_name = _text(name, "library name")
                normalized_version = _text(version, f"library version for {name!r}")
                assert normalized_name is not None and normalized_version is not None
                pairs.append((normalized_name, normalized_version))
            if len({name for name, _ in pairs}) != len(pairs):
                raise CacheKeyContractError(
                    "Library names must remain unique after Unicode normalization."
                )
            normalized_libraries = tuple(sorted(pairs))
        else:
            raise CacheKeyContractError("library_versions must be a dict with string values.")
        object.__setattr__(self, "library_versions", normalized_libraries)
        object.__setattr__(
            self, "platform_profile", _text(platform_profile, "platform_profile", optional=True)
        )
        object.__setattr__(
            self,
            "algorithm_profile",
            _text(algorithm_profile, "algorithm_profile", optional=True),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "algorithm_profile": self.algorithm_profile,
            "backend_name": self.backend_name,
            "backend_version": self.backend_version,
            "library_versions": dict(self.library_versions),
            "model_name": self.model_name,
            "model_revision": self.model_revision,
            "platform_profile": self.platform_profile,
        }


def _validated_configs(configs: tuple[ConfigFingerprint, ...]) -> tuple[ConfigFingerprint, ...]:
    if not isinstance(configs, tuple) or any(
        not isinstance(config, ConfigFingerprint) for config in configs
    ):
        raise CacheKeyContractError("config_fingerprints must be an immutable tuple.")
    paths = [config.path for config in configs]
    if len(set(paths)) != len(paths):
        raise CacheKeyContractError("Config fingerprint paths must be unique.")
    return tuple(sorted(configs, key=lambda config: config.path))


def _validated_inputs(inputs: tuple[InputFingerprint, ...]) -> tuple[InputFingerprint, ...]:
    if not isinstance(inputs, tuple) or any(
        not isinstance(item, InputFingerprint) for item in inputs
    ):
        raise CacheKeyContractError("input_fingerprints must be an immutable tuple.")
    return inputs


@dataclass(frozen=True)
class CacheKeyMaterial:
    """Pre-production key material for one artifact descriptor.

    Input fingerprints are always ordered. Callers must provide semantic order,
    or establish their own canonical order for otherwise set-like dependencies.
    """

    artifact: ArtifactDescriptor
    input_fingerprints: tuple[InputFingerprint, ...] = ()
    config_fingerprints: tuple[ConfigFingerprint, ...] = ()
    runtime_fingerprint: RuntimeFingerprint | None = None
    cache_contract_version: str = CACHE_KEY_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.artifact, ArtifactDescriptor):
            raise CacheKeyContractError("artifact must be an ArtifactDescriptor.")
        object.__setattr__(self, "input_fingerprints", _validated_inputs(self.input_fingerprints))
        object.__setattr__(self, "config_fingerprints", _validated_configs(self.config_fingerprints))
        if self.runtime_fingerprint is not None and not isinstance(
            self.runtime_fingerprint, RuntimeFingerprint
        ):
            raise CacheKeyContractError(
                "runtime_fingerprint must be a RuntimeFingerprint or None."
            )
        object.__setattr__(
            self,
            "cache_contract_version",
            _contract_version(self.cache_contract_version, "cache_contract_version"),
        )

    def to_dict(self) -> dict[str, Any]:
        artifact_identity = self.artifact.identity_dict()
        for policy_or_physical_field in (
            "cacheable",
            "integrity",
            "presence",
            "primary_category",
            "protection",
        ):
            artifact_identity.pop(policy_or_physical_field)
        return {
            "artifact": artifact_identity,
            "cache_contract_version": self.cache_contract_version,
            "config_fingerprints": [item.to_dict() for item in self.config_fingerprints],
            "input_fingerprints": [item.to_dict() for item in self.input_fingerprints],
            "runtime_fingerprint": (
                self.runtime_fingerprint.to_dict()
                if self.runtime_fingerprint is not None
                else None
            ),
            "subject_kind": "artifact",
        }


@dataclass(frozen=True)
class GroupCacheKeyMaterial:
    """Pre-production key material shared by every member of an atomic group."""

    group: ArtifactGroup
    producer: ProducerIdentity
    input_fingerprints: tuple[InputFingerprint, ...] = ()
    config_fingerprints: tuple[ConfigFingerprint, ...] = ()
    runtime_fingerprint: RuntimeFingerprint | None = None
    group_contract_version: str = "1.0"
    cache_contract_version: str = CACHE_KEY_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.group, ArtifactGroup):
            raise CacheKeyContractError("group must be an ArtifactGroup.")
        if not isinstance(self.producer, ProducerIdentity):
            raise CacheKeyContractError("producer must be a ProducerIdentity.")
        object.__setattr__(self, "input_fingerprints", _validated_inputs(self.input_fingerprints))
        object.__setattr__(self, "config_fingerprints", _validated_configs(self.config_fingerprints))
        if self.runtime_fingerprint is not None and not isinstance(
            self.runtime_fingerprint, RuntimeFingerprint
        ):
            raise CacheKeyContractError(
                "runtime_fingerprint must be a RuntimeFingerprint or None."
            )
        object.__setattr__(
            self,
            "group_contract_version",
            _contract_version(self.group_contract_version, "group_contract_version"),
        )
        object.__setattr__(
            self,
            "cache_contract_version",
            _contract_version(self.cache_contract_version, "cache_contract_version"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "cache_contract_version": self.cache_contract_version,
            "config_fingerprints": [item.to_dict() for item in self.config_fingerprints],
            "group": self.group.to_dict(),
            "group_contract_version": self.group_contract_version,
            "input_fingerprints": [item.to_dict() for item in self.input_fingerprints],
            "producer": self.producer.to_dict(),
            "runtime_fingerprint": (
                self.runtime_fingerprint.to_dict()
                if self.runtime_fingerprint is not None
                else None
            ),
            "subject_kind": "artifact_group",
        }


CacheMaterial = CacheKeyMaterial | GroupCacheKeyMaterial


@dataclass(frozen=True)
class CacheKey:
    """Versioned key in ``cache-v<version>:sha256:<lowercase hex>`` format."""

    digest: str
    contract_version: str = CACHE_KEY_CONTRACT_VERSION
    algorithm: str = "sha256"

    def __post_init__(self) -> None:
        if self.algorithm != "sha256":
            raise CacheKeyContractError(f"Unsupported cache key algorithm: {self.algorithm!r}.")
        if not isinstance(self.digest, str) or not _SHA256_PATTERN.fullmatch(self.digest):
            raise CacheKeyContractError("Cache key digest must be 64 lowercase hexadecimal characters.")
        object.__setattr__(self, "contract_version", _contract_version(
            self.contract_version, "cache key contract_version"
        ))

    def __str__(self) -> str:
        return f"cache-v{self.contract_version}:{self.algorithm}:{self.digest}"

    @classmethod
    def parse(cls, value: str) -> "CacheKey":
        if not isinstance(value, str) or value.count(":") != 2:
            raise CacheKeyContractError(
                "Cache key must use cache-v<version>:algorithm:digest format."
            )
        version_component, algorithm, digest = value.split(":", 2)
        if not version_component.startswith("cache-v"):
            raise CacheKeyContractError("Cache key is missing its contract version.")
        contract_version = version_component.removeprefix("cache-v")
        if contract_version != CACHE_KEY_CONTRACT_VERSION:
            raise CacheKeyContractError(
                f"Unsupported cache key contract version: {contract_version!r}."
            )
        return cls(digest=digest, contract_version=contract_version, algorithm=algorithm)


def serialize_cache_key_material(material: CacheMaterial) -> bytes:
    """Return canonical UTF-8 JSON bytes for supplied pre-production material."""

    if not isinstance(material, (CacheKeyMaterial, GroupCacheKeyMaterial)):
        raise CacheKeyContractError("Unsupported cache key material type.")
    return _canonical_json_bytes(material.to_dict())


def derive_cache_key(material: CacheMaterial) -> CacheKey:
    """Derive a SHA-256 identity; this does not verify or access any payload."""

    serialized = serialize_cache_key_material(material)
    return CacheKey(
        digest=hashlib.sha256(serialized).hexdigest(),
        contract_version=material.cache_contract_version,
    )
