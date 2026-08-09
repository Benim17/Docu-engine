"""Pure immutable H1A persistent-cache catalog contracts.

This module performs no filesystem access. Catalog lookup, storage, publication,
locking, enumeration, and reconciliation belong to later H1 slices.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from functools import total_ordering
from pathlib import Path as _Path
from typing import Any, Mapping, Protocol, runtime_checkable

from .cache_lookup import (
    BoundedFileRead,
    CacheLookupFilesystemError,
    CacheLookupIOError,
    CacheLookupPermissionError,
    CacheLookupReason,
    FileIdentity,
    FilesystemObjectType,
    LocalReadOnlyCacheFilesystem,
    SymlinkRejectedError,
    UnstableFilesystemObjectError,
    UnsupportedFilesystemObjectError,
    ValidatedCacheRoot,
)
from .cache_recovery import (
    CacheRecoveryReason,
    CacheRecoveryStatus,
    FinalRecoveryState,
    LockRecoveryState,
)
from .persistent_cache import (
    CACHE_ENTRY_CONTRACT_VERSION,
    CacheArtifactMetadata,
    CacheEntryContractError,
    CacheKeyReference,
    CacheNamespace,
    CacheProducerMetadata,
    canonical_json_bytes,
    derive_entry_digest,
    parse_canonical_json,
)


CACHE_CATALOG_LAYOUT_VERSION = 1
CACHE_CATALOG_RECORD_VERSION = 1
MAX_CATALOG_RECORD_BYTES = 65_536
MAX_CATALOG_PAGE_RECORDS = 256
MAX_CATALOG_DIRECTORY_ENTRIES = 4_096
MAX_CATALOG_RELATIVE_PATH_UTF8_BYTES = 1_024
MAX_CATALOG_TRAVERSAL_DEPTH = 64
MAX_CATALOG_OPERATION_DIAGNOSTICS = 32
MAX_CATALOG_RECORD_REVISION = 9_223_372_036_854_775_807

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_QUALIFIED_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_UTC_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
)


class CacheCatalogContractError(ValueError):
    """Raised when an H1 catalog value violates the locked record contract."""


class CacheCatalogUnsupportedVersionError(CacheCatalogContractError):
    """Raised for a recognizable positive future catalog record version."""


class CacheCatalogRecordState(str, Enum):
    LIVE = "live"
    TOMBSTONE = "tombstone"


class CacheCatalogFinalProvenance(str, Enum):
    STEP5B_HIT = "step5b_hit"
    STEP5D_PROMOTION = "step5d_promotion"


class CacheCatalogRecoveryProvenance(str, Enum):
    STEP5E_OBSERVATION = "step5e_observation"


class CacheCatalogVerificationLevel(str, Enum):
    FULL_PAYLOAD_SHA256 = "full_payload_sha256"


def _catalog_error(action):
    try:
        return action()
    except CacheCatalogContractError:
        raise
    except (CacheEntryContractError, TypeError, ValueError) as exc:
        raise CacheCatalogContractError(str(exc)) from exc


def _object(value: Any, name: str) -> Mapping[str, Any]:
    if type(value) is not dict:
        raise CacheCatalogContractError(f"{name} must be a JSON object.")
    return value


def _fields(value: Mapping[str, Any], expected: frozenset[str], name: str) -> None:
    actual = set(value)
    unknown = sorted(actual - expected)
    missing = sorted(expected - actual)
    if unknown:
        raise CacheCatalogContractError(
            f"Unknown {name} fields: {', '.join(unknown)}."
        )
    if missing:
        raise CacheCatalogContractError(
            f"Missing {name} fields: {', '.join(missing)}."
        )


def _record_version(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CacheCatalogContractError(
            "catalog_record_version must be a positive integer."
        )
    if value != CACHE_CATALOG_RECORD_VERSION:
        raise CacheCatalogUnsupportedVersionError(
            f"Unsupported catalog_record_version: {value}."
        )
    return value


def _positive_int(value: Any, name: str, *, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CacheCatalogContractError(f"{name} must be a positive integer.")
    if maximum is not None and value > maximum:
        raise CacheCatalogContractError(f"{name} exceeds its locked maximum.")
    return value


def _nonnegative_int(value: Any, name: str, *, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CacheCatalogContractError(f"{name} must be a non-negative integer.")
    if maximum is not None and value > maximum:
        raise CacheCatalogContractError(f"{name} exceeds its locked maximum.")
    return value


def _digest(value: Any, name: str, *, qualified: bool = False) -> str:
    pattern = _QUALIFIED_SHA256 if qualified else _SHA256
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        expected = "sha256:<64 lowercase hex>" if qualified else "64 lowercase hex"
        raise CacheCatalogContractError(f"{name} must use {expected}.")
    return value


def _canonical_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise CacheCatalogContractError(f"{name} must be non-empty canonical text.")
    if value.startswith(("/", "\\")) or "\x00" in value:
        raise CacheCatalogContractError(f"{name} must not be a physical path.")
    return value


def _timestamp(value: Any) -> str:
    if not isinstance(value, str) or _UTC_TIMESTAMP.fullmatch(value) is None:
        raise CacheCatalogContractError(
            "created_at_utc must use YYYY-MM-DDTHH:MM:SSZ."
        )
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise CacheCatalogContractError(
            "created_at_utc must be a valid UTC timestamp."
        ) from exc
    return value


def _enum(value: Any, expected, name: str):
    if isinstance(value, expected):
        return value
    if not isinstance(value, str):
        raise CacheCatalogContractError(f"{name} must be a supported string value.")
    try:
        return expected(value)
    except ValueError as exc:
        raise CacheCatalogContractError(f"Unsupported {name}: {value!r}.") from exc


def _reason(value: Any) -> CacheRecoveryReason | CacheLookupReason | None:
    if value is None:
        return None
    if isinstance(value, (CacheRecoveryReason, CacheLookupReason)):
        return value
    if not isinstance(value, str):
        raise CacheCatalogContractError("recovery reason must be supported or null.")
    for reason_type in (CacheRecoveryReason, CacheLookupReason):
        try:
            return reason_type(value)
        except ValueError:
            pass
    raise CacheCatalogContractError(f"Unsupported recovery reason: {value!r}.")


def _canonical_record_bytes(value: Mapping[str, Any]) -> bytes:
    serialized = _catalog_error(lambda: canonical_json_bytes(value))
    if len(serialized) > MAX_CATALOG_RECORD_BYTES:
        raise CacheCatalogContractError(
            f"Catalog record exceeds {MAX_CATALOG_RECORD_BYTES} bytes."
        )
    return serialized


@total_ordering
@dataclass(frozen=True)
class CacheCatalogIdentity:
    namespace: CacheNamespace
    entry_digest: str
    cache_key_reference: CacheKeyReference

    def __post_init__(self) -> None:
        if not isinstance(self.namespace, CacheNamespace):
            raise CacheCatalogContractError("namespace must be CacheNamespace.")
        _digest(self.entry_digest, "entry_digest")
        if not isinstance(self.cache_key_reference, CacheKeyReference):
            raise CacheCatalogContractError(
                "cache_key_reference must be CacheKeyReference."
            )
        derived = _catalog_error(
            lambda: derive_entry_digest(self.cache_key_reference.to_cache_key())
        )
        if self.entry_digest != derived:
            raise CacheCatalogContractError(
                "entry_digest does not match cache_key_reference."
            )

    @property
    def sort_key(self) -> tuple[str, str, int, str]:
        return (
            self.namespace.domain,
            self.namespace.producer_id,
            self.namespace.producer_schema_version,
            self.entry_digest,
        )

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, CacheCatalogIdentity):
            return NotImplemented
        return self.sort_key < other.sort_key

    def to_dict(self) -> dict[str, Any]:
        return {
            "cache_key_reference": self.cache_key_reference.to_dict(),
            "entry_digest": self.entry_digest,
            "namespace": self.namespace.to_dict(),
        }

    @classmethod
    def from_values(
        cls,
        namespace: Any,
        entry_digest: Any,
        cache_key_reference: Any,
    ) -> "CacheCatalogIdentity":
        return cls(
            _catalog_error(lambda: CacheNamespace.from_dict(namespace)),
            entry_digest,
            _catalog_error(lambda: CacheKeyReference.from_dict(cache_key_reference)),
        )


@dataclass(frozen=True)
class CacheCatalogFinalSummary:
    provenance: CacheCatalogFinalProvenance
    cache_entry_contract_version: int
    producer_id: str
    producer_version: str
    producer_schema_version: int
    artifact_kind: str
    artifact_contract_version: int
    runtime_fingerprint_digest: str
    created_at_utc: str
    payload_manifest_digest: str
    payload_file_count: int
    payload_total_bytes: int
    verification_level: CacheCatalogVerificationLevel = (
        CacheCatalogVerificationLevel.FULL_PAYLOAD_SHA256
    )

    def __post_init__(self) -> None:
        if not isinstance(self.provenance, CacheCatalogFinalProvenance):
            raise CacheCatalogContractError(
                "final provenance must be CacheCatalogFinalProvenance."
            )
        if (
            isinstance(self.cache_entry_contract_version, bool)
            or self.cache_entry_contract_version != CACHE_ENTRY_CONTRACT_VERSION
        ):
            raise CacheCatalogContractError(
                "final summary requires the supported cache-entry contract version."
            )
        _catalog_error(
            lambda: CacheProducerMetadata(
                self.producer_id,
                self.producer_version,
                self.producer_schema_version,
            )
        )
        _catalog_error(
            lambda: CacheArtifactMetadata(
                self.artifact_kind, "catalog-summary", self.artifact_contract_version
            )
        )
        _digest(
            self.runtime_fingerprint_digest,
            "runtime_fingerprint_digest",
            qualified=True,
        )
        _timestamp(self.created_at_utc)
        _digest(
            self.payload_manifest_digest,
            "payload_manifest_digest",
            qualified=True,
        )
        _nonnegative_int(self.payload_file_count, "payload_file_count")
        _nonnegative_int(self.payload_total_bytes, "payload_total_bytes")
        if self.verification_level is not CacheCatalogVerificationLevel.FULL_PAYLOAD_SHA256:
            raise CacheCatalogContractError(
                "final summary requires full_payload_sha256 verification."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_contract_version": self.artifact_contract_version,
            "artifact_kind": self.artifact_kind,
            "cache_entry_contract_version": self.cache_entry_contract_version,
            "created_at_utc": self.created_at_utc,
            "payload_file_count": self.payload_file_count,
            "payload_manifest_digest": self.payload_manifest_digest,
            "payload_total_bytes": self.payload_total_bytes,
            "producer_id": self.producer_id,
            "producer_schema_version": self.producer_schema_version,
            "producer_version": self.producer_version,
            "provenance": self.provenance.value,
            "runtime_fingerprint_digest": self.runtime_fingerprint_digest,
            "verification_level": self.verification_level.value,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "CacheCatalogFinalSummary":
        data = _object(value, "final summary")
        expected = frozenset(
            {
                "provenance",
                "cache_entry_contract_version",
                "producer_id",
                "producer_version",
                "producer_schema_version",
                "artifact_kind",
                "artifact_contract_version",
                "runtime_fingerprint_digest",
                "created_at_utc",
                "payload_manifest_digest",
                "payload_file_count",
                "payload_total_bytes",
                "verification_level",
            }
        )
        _fields(data, expected, "final summary")
        return cls(
            _enum(
                data["provenance"],
                CacheCatalogFinalProvenance,
                "final provenance",
            ),
            data["cache_entry_contract_version"],
            data["producer_id"],
            data["producer_version"],
            data["producer_schema_version"],
            data["artifact_kind"],
            data["artifact_contract_version"],
            data["runtime_fingerprint_digest"],
            data["created_at_utc"],
            data["payload_manifest_digest"],
            data["payload_file_count"],
            data["payload_total_bytes"],
            _enum(
                data["verification_level"],
                CacheCatalogVerificationLevel,
                "verification_level",
            ),
        )


_FAILURE_RECOVERY_STATUSES = frozenset(
    {
        CacheRecoveryStatus.RECOVERY_UNSAFE,
        CacheRecoveryStatus.RECOVERY_UNSUPPORTED,
        CacheRecoveryStatus.RECOVERY_UNSTABLE,
        CacheRecoveryStatus.RECOVERY_INVALID,
    }
)


@dataclass(frozen=True)
class CacheCatalogRecoverySummary:
    status: CacheRecoveryStatus
    reason: CacheRecoveryReason | CacheLookupReason | None
    staging_candidate_count: int
    final_state: FinalRecoveryState
    lock_state: LockRecoveryState
    provenance: CacheCatalogRecoveryProvenance = (
        CacheCatalogRecoveryProvenance.STEP5E_OBSERVATION
    )

    def __post_init__(self) -> None:
        if self.provenance is not CacheCatalogRecoveryProvenance.STEP5E_OBSERVATION:
            raise CacheCatalogContractError(
                "recovery provenance must be step5e_observation."
            )
        if not isinstance(self.status, CacheRecoveryStatus):
            raise CacheCatalogContractError("status must be CacheRecoveryStatus.")
        if self.reason is not None and not isinstance(
            self.reason, (CacheRecoveryReason, CacheLookupReason)
        ):
            raise CacheCatalogContractError("reason must be a supported reason or null.")
        if self.status in _FAILURE_RECOVERY_STATUSES and self.reason is None:
            raise CacheCatalogContractError("failure recovery status requires a reason.")
        if self.status not in _FAILURE_RECOVERY_STATUSES and self.reason is not None:
            raise CacheCatalogContractError("lifecycle recovery status requires null reason.")
        _nonnegative_int(
            self.staging_candidate_count,
            "staging_candidate_count",
            maximum=64,
        )
        if not isinstance(self.final_state, FinalRecoveryState):
            raise CacheCatalogContractError("final_state must be FinalRecoveryState.")
        if not isinstance(self.lock_state, LockRecoveryState):
            raise CacheCatalogContractError("lock_state must be LockRecoveryState.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "final_state": self.final_state.value,
            "lock_state": self.lock_state.value,
            "provenance": self.provenance.value,
            "reason": None if self.reason is None else self.reason.value,
            "staging_candidate_count": self.staging_candidate_count,
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "CacheCatalogRecoverySummary":
        data = _object(value, "recovery summary")
        expected = frozenset(
            {
                "provenance",
                "status",
                "reason",
                "staging_candidate_count",
                "final_state",
                "lock_state",
            }
        )
        _fields(data, expected, "recovery summary")
        return cls(
            _enum(data["status"], CacheRecoveryStatus, "recovery status"),
            _reason(data["reason"]),
            data["staging_candidate_count"],
            _enum(data["final_state"], FinalRecoveryState, "final_state"),
            _enum(data["lock_state"], LockRecoveryState, "lock_state"),
            _enum(
                data["provenance"],
                CacheCatalogRecoveryProvenance,
                "recovery provenance",
            ),
        )


_IDENTITY_FIELDS = frozenset(
    {"entry_digest", "namespace", "cache_key_reference"}
)
_BASE_FIELDS = frozenset(
    {"catalog_record_version", "record_state", "record_revision"}
) | _IDENTITY_FIELDS
_LIVE_FIELDS = _BASE_FIELDS | frozenset(
    {"last_validated_final", "last_recovery_observation"}
)


@dataclass(frozen=True, kw_only=True)
class CacheCatalogRecord:
    identity: CacheCatalogIdentity
    record_revision: int
    catalog_record_version: int = CACHE_CATALOG_RECORD_VERSION

    def __post_init__(self) -> None:
        if type(self) is CacheCatalogRecord:
            raise CacheCatalogContractError(
                "CacheCatalogRecord is parsed as a live record or tombstone."
            )
        if not isinstance(self.identity, CacheCatalogIdentity):
            raise CacheCatalogContractError("identity must be CacheCatalogIdentity.")
        _record_version(self.catalog_record_version)
        _positive_int(
            self.record_revision,
            "record_revision",
            maximum=MAX_CATALOG_RECORD_REVISION,
        )

    @property
    def record_state(self) -> CacheCatalogRecordState:
        raise NotImplementedError

    def _base_dict(self) -> dict[str, Any]:
        return {
            "cache_key_reference": self.identity.cache_key_reference.to_dict(),
            "catalog_record_version": self.catalog_record_version,
            "entry_digest": self.identity.entry_digest,
            "namespace": self.identity.namespace.to_dict(),
            "record_revision": self.record_revision,
            "record_state": self.record_state.value,
        }

    def to_dict(self) -> dict[str, Any]:
        raise NotImplementedError

    def canonical_bytes(self) -> bytes:
        return _canonical_record_bytes(self.to_dict())

    @staticmethod
    def from_dict(value: Any) -> "CacheCatalogRecord":
        data = _object(value, "catalog record")
        missing_dispatch = sorted(
            {"catalog_record_version", "record_state"} - set(data)
        )
        if missing_dispatch:
            raise CacheCatalogContractError(
                f"Missing catalog record fields: {', '.join(missing_dispatch)}."
            )
        _record_version(data["catalog_record_version"])
        state = _enum(data["record_state"], CacheCatalogRecordState, "record_state")
        if state is CacheCatalogRecordState.LIVE:
            return CacheCatalogLiveRecord._from_data(data)
        return CacheCatalogTombstone._from_data(data)

    @staticmethod
    def from_json(serialized: str | bytes) -> "CacheCatalogRecord":
        if not isinstance(serialized, (str, bytes)):
            raise CacheCatalogContractError(
                "Catalog record JSON must be text or UTF-8 bytes."
            )
        try:
            encoded = serialized.encode("utf-8") if isinstance(serialized, str) else serialized
        except UnicodeEncodeError as exc:
            raise CacheCatalogContractError("Catalog record JSON must be UTF-8.") from exc
        if len(encoded) > MAX_CATALOG_RECORD_BYTES:
            raise CacheCatalogContractError(
                f"Catalog record exceeds {MAX_CATALOG_RECORD_BYTES} bytes."
            )
        parsed = _catalog_error(lambda: parse_canonical_json(serialized))
        record = CacheCatalogRecord.from_dict(parsed)
        record.canonical_bytes()
        return record


def _identity_from_record_data(data: Mapping[str, Any]) -> CacheCatalogIdentity:
    return CacheCatalogIdentity.from_values(
        data["namespace"], data["entry_digest"], data["cache_key_reference"]
    )


@dataclass(frozen=True, kw_only=True)
class CacheCatalogLiveRecord(CacheCatalogRecord):
    last_validated_final: CacheCatalogFinalSummary | None = None
    last_recovery_observation: CacheCatalogRecoverySummary | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.last_validated_final is not None and not isinstance(
            self.last_validated_final, CacheCatalogFinalSummary
        ):
            raise CacheCatalogContractError(
                "last_validated_final must be CacheCatalogFinalSummary or null."
            )
        if self.last_recovery_observation is not None and not isinstance(
            self.last_recovery_observation, CacheCatalogRecoverySummary
        ):
            raise CacheCatalogContractError(
                "last_recovery_observation must be CacheCatalogRecoverySummary or null."
            )
        if self.last_validated_final is None and self.last_recovery_observation is None:
            raise CacheCatalogContractError(
                "Live catalog record requires at least one trusted summary."
            )
        if self.last_validated_final is not None:
            if (
                self.last_validated_final.producer_id
                != self.identity.namespace.producer_id
                or self.last_validated_final.producer_schema_version
                != self.identity.namespace.producer_schema_version
            ):
                raise CacheCatalogContractError(
                    "Final summary producer identity must match catalog namespace."
                )

    @property
    def record_state(self) -> CacheCatalogRecordState:
        return CacheCatalogRecordState.LIVE

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._base_dict(),
            "last_recovery_observation": (
                None
                if self.last_recovery_observation is None
                else self.last_recovery_observation.to_dict()
            ),
            "last_validated_final": (
                None
                if self.last_validated_final is None
                else self.last_validated_final.to_dict()
            ),
        }

    @classmethod
    def _from_data(cls, data: Mapping[str, Any]) -> "CacheCatalogLiveRecord":
        _fields(data, _LIVE_FIELDS, "live catalog record")
        final = data["last_validated_final"]
        recovery = data["last_recovery_observation"]
        return cls(
            identity=_identity_from_record_data(data),
            record_revision=data["record_revision"],
            catalog_record_version=data["catalog_record_version"],
            last_validated_final=(
                None if final is None else CacheCatalogFinalSummary.from_dict(final)
            ),
            last_recovery_observation=(
                None
                if recovery is None
                else CacheCatalogRecoverySummary.from_dict(recovery)
            ),
        )


@dataclass(frozen=True, kw_only=True)
class CacheCatalogTombstone(CacheCatalogRecord):
    @property
    def record_state(self) -> CacheCatalogRecordState:
        return CacheCatalogRecordState.TOMBSTONE

    def to_dict(self) -> dict[str, Any]:
        return self._base_dict()

    @classmethod
    def _from_data(cls, data: Mapping[str, Any]) -> "CacheCatalogTombstone":
        _fields(data, _BASE_FIELDS, "catalog tombstone")
        return cls(
            identity=_identity_from_record_data(data),
            record_revision=data["record_revision"],
            catalog_record_version=data["catalog_record_version"],
        )


def parse_cache_catalog_record(serialized: str | bytes) -> CacheCatalogRecord:
    """Parse one strict bounded canonical H1 catalog record."""

    return CacheCatalogRecord.from_json(serialized)


def serialize_cache_catalog_record(record: CacheCatalogRecord) -> bytes:
    """Serialize one validated immutable H1 catalog record canonically."""

    if not isinstance(record, CacheCatalogRecord):
        raise CacheCatalogContractError("record must be CacheCatalogRecord.")
    return record.canonical_bytes()


def catalog_identity_sort_key(
    identity: CacheCatalogIdentity,
) -> tuple[str, str, int, str]:
    if not isinstance(identity, CacheCatalogIdentity):
        raise CacheCatalogContractError("identity must be CacheCatalogIdentity.")
    return identity.sort_key


class CacheCatalogLookupStatus(str, Enum):
    RECORD_FOUND = "record_found"
    RECORD_ABSENT = "record_absent"
    CATALOG_UNAVAILABLE = "catalog_unavailable"
    CATALOG_CORRUPT = "catalog_corrupt"
    CATALOG_UNSUPPORTED = "catalog_unsupported"
    CATALOG_UNSAFE = "catalog_unsafe"
    CATALOG_UNSTABLE = "catalog_unstable"
    CATALOG_IO_FAILURE = "catalog_io_failure"


class CacheCatalogSubject(str, Enum):
    CATALOG_ROOT = "catalog_root"
    RECORD = "record"


@dataclass(frozen=True, order=True)
class CacheCatalogDiagnostic:
    subject: CacheCatalogSubject
    identity: tuple[str, str, int, str]
    code: str
    relative_path: str

    def __post_init__(self) -> None:
        if not isinstance(self.subject, CacheCatalogSubject):
            raise TypeError("subject must be CacheCatalogSubject.")
        if (
            not isinstance(self.identity, tuple)
            or len(self.identity) != 4
            or not isinstance(self.identity[0], str)
            or not isinstance(self.identity[1], str)
            or isinstance(self.identity[2], bool)
            or not isinstance(self.identity[2], int)
            or not isinstance(self.identity[3], str)
        ):
            raise TypeError("identity must be one canonical catalog sort key.")
        if not isinstance(self.code, str) or re.fullmatch(r"[a-z0-9_]+", self.code) is None:
            raise ValueError("code must be stable lowercase identifier data.")
        if (
            not isinstance(self.relative_path, str)
            or self.relative_path.startswith(("/", "\\"))
            or ".." in _Path(self.relative_path).parts
            or "\x00" in self.relative_path
        ):
            raise ValueError("relative_path must be sanitized catalog-relative text.")


@dataclass(frozen=True)
class CacheCatalogLookupResult:
    status: CacheCatalogLookupStatus
    record: CacheCatalogLiveRecord | None = None
    tombstone_revision: int | None = None
    diagnostics: tuple[CacheCatalogDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, CacheCatalogLookupStatus):
            raise TypeError("status must be CacheCatalogLookupStatus.")
        if (self.status is CacheCatalogLookupStatus.RECORD_FOUND) != isinstance(
            self.record, CacheCatalogLiveRecord
        ):
            raise ValueError("Only RECORD_FOUND carries one live catalog record.")
        if self.tombstone_revision is not None:
            _positive_int(
                self.tombstone_revision,
                "tombstone_revision",
                maximum=MAX_CATALOG_RECORD_REVISION,
            )
            if self.status is not CacheCatalogLookupStatus.RECORD_ABSENT:
                raise ValueError("Tombstone revision requires RECORD_ABSENT.")
        if (
            not isinstance(self.diagnostics, tuple)
            or any(not isinstance(item, CacheCatalogDiagnostic) for item in self.diagnostics)
            or len(self.diagnostics) > MAX_CATALOG_OPERATION_DIAGNOSTICS
            or self.diagnostics
            != tuple(
                sorted(
                    set(self.diagnostics),
                    key=lambda item: (
                        item.subject.value,
                        item.identity,
                        item.code,
                        item.relative_path,
                    ),
                )
            )
        ):
            raise ValueError("diagnostics must be bounded, unique, and deterministic.")


@runtime_checkable
class CacheCatalogReadOnlyBackend(Protocol):
    """Catalog-only read surface bound to one previously validated cache root."""

    @property
    def cache_root(self) -> ValidatedCacheRoot: ...

    def inspect_root(self) -> FileIdentity: ...

    def inspect_catalog_relative(self, relative_path: _Path) -> FileIdentity: ...

    def read_record_bounded(self, identity: CacheCatalogIdentity) -> BoundedFileRead: ...


@dataclass(frozen=True)
class LocalCacheCatalogReadOnlyBackend:
    cache_root: ValidatedCacheRoot
    _filesystem: LocalReadOnlyCacheFilesystem = LocalReadOnlyCacheFilesystem()

    def __post_init__(self) -> None:
        if not isinstance(self.cache_root, ValidatedCacheRoot):
            raise TypeError("cache_root must be ValidatedCacheRoot.")
        if not isinstance(self._filesystem, LocalReadOnlyCacheFilesystem):
            raise TypeError("_filesystem must be LocalReadOnlyCacheFilesystem.")

    @classmethod
    def from_root(cls, path: str | _Path) -> "LocalCacheCatalogReadOnlyBackend":
        filesystem = LocalReadOnlyCacheFilesystem()
        return cls(ValidatedCacheRoot.from_path(path, filesystem=filesystem), filesystem)

    def _catalog_path(self, relative_path: _Path) -> _Path:
        if not isinstance(relative_path, _Path) or relative_path.is_absolute():
            raise TypeError("relative_path must be a relative Path.")
        if (
            not relative_path.parts
            or relative_path.parts[0] != "catalog"
            or any(part in {"", ".", ".."} for part in relative_path.parts)
            or len(relative_path.parts) > MAX_CATALOG_TRAVERSAL_DEPTH
            or len(relative_path.as_posix().encode("utf-8"))
            > MAX_CATALOG_RELATIVE_PATH_UTF8_BYTES
        ):
            raise CacheCatalogContractError("Path is outside the canonical catalog namespace.")
        return self.cache_root.resolved_path / relative_path

    def inspect_root(self) -> FileIdentity:
        return self._filesystem.inspect(self.cache_root.resolved_path)

    def inspect_catalog_relative(self, relative_path: _Path) -> FileIdentity:
        return self._filesystem.inspect(self._catalog_path(relative_path))

    def read_record_bounded(self, identity: CacheCatalogIdentity) -> BoundedFileRead:
        return self._filesystem.read_regular_file_bounded(
            self._catalog_path(derive_catalog_record_relative_path(identity)),
            max_bytes=MAX_CATALOG_RECORD_BYTES,
        )


def derive_catalog_record_relative_path(identity: CacheCatalogIdentity) -> _Path:
    """Derive the only locked v1 path for one trusted catalog identity."""

    if not isinstance(identity, CacheCatalogIdentity):
        raise CacheCatalogContractError("identity must be CacheCatalogIdentity.")
    digest = identity.entry_digest
    relative = _Path(
        "catalog",
        f"v{CACHE_CATALOG_LAYOUT_VERSION}",
        "records",
        identity.namespace.domain,
        identity.namespace.producer_id,
        str(identity.namespace.producer_schema_version),
        digest[:2],
        digest[2:4],
        f"{digest}.json",
    )
    if len(relative.parts) > MAX_CATALOG_TRAVERSAL_DEPTH:
        raise CacheCatalogContractError("Catalog path exceeds traversal depth limit.")
    if len(relative.as_posix().encode("utf-8")) > MAX_CATALOG_RELATIVE_PATH_UTF8_BYTES:
        raise CacheCatalogContractError("Catalog path exceeds UTF-8 byte limit.")
    if any(part in {"", ".", ".."} or "/" in part or "\\" in part for part in relative.parts):
        raise CacheCatalogContractError("Catalog path contains a noncanonical component.")
    return relative


def _diagnostic(
    status: CacheCatalogLookupStatus,
    identity: CacheCatalogIdentity,
    relative: _Path,
) -> tuple[CacheCatalogDiagnostic, ...]:
    return (
        CacheCatalogDiagnostic(
            CacheCatalogSubject.CATALOG_ROOT
            if status is CacheCatalogLookupStatus.CATALOG_UNAVAILABLE
            else CacheCatalogSubject.RECORD,
            identity.sort_key,
            status.value,
            relative.as_posix(),
        ),
    )


def _lookup_result(
    status: CacheCatalogLookupStatus,
    identity: CacheCatalogIdentity,
    relative: _Path,
    *,
    record: CacheCatalogLiveRecord | None = None,
    tombstone_revision: int | None = None,
) -> CacheCatalogLookupResult:
    diagnostics = () if status in {
        CacheCatalogLookupStatus.RECORD_FOUND,
        CacheCatalogLookupStatus.RECORD_ABSENT,
    } else _diagnostic(status, identity, relative)
    return CacheCatalogLookupResult(status, record, tombstone_revision, diagnostics)


def _safe_parent_chain(
    backend: CacheCatalogReadOnlyBackend,
    relative: _Path,
) -> tuple[tuple[tuple[_Path, FileIdentity], ...], CacheCatalogLookupStatus | None]:
    observed: list[tuple[_Path, FileIdentity]] = []
    for index, component in enumerate(relative.parts[:-1]):
        current = _Path(*relative.parts[: index + 1])
        try:
            item = backend.inspect_catalog_relative(current)
        except FileNotFoundError:
            return tuple(observed), (
                CacheCatalogLookupStatus.CATALOG_UNAVAILABLE
                if index <= 1
                else CacheCatalogLookupStatus.RECORD_ABSENT
            )
        if item.object_type is not FilesystemObjectType.DIRECTORY:
            return tuple(observed), CacheCatalogLookupStatus.CATALOG_UNSAFE
        observed.append((current, item))
    return tuple(observed), None


def _chain_remains_stable(
    backend: CacheCatalogReadOnlyBackend,
    parents: tuple[tuple[_Path, FileIdentity], ...],
) -> bool:
    return all(
        before.same_stable_object(backend.inspect_catalog_relative(path))
        for path, before in parents
    )


def lookup_catalog_record(
    identity: CacheCatalogIdentity,
    *,
    backend: CacheCatalogReadOnlyBackend,
) -> CacheCatalogLookupResult:
    """Read exactly one catalog identity without cache or catalog mutation."""

    if not isinstance(identity, CacheCatalogIdentity):
        raise TypeError("identity must be CacheCatalogIdentity.")
    if not isinstance(backend, CacheCatalogReadOnlyBackend):
        raise TypeError("backend must implement CacheCatalogReadOnlyBackend.")
    relative = derive_catalog_record_relative_path(identity)
    try:
        root_before = backend.inspect_root()
        if root_before.object_type is not FilesystemObjectType.DIRECTORY:
            return _lookup_result(CacheCatalogLookupStatus.CATALOG_UNSAFE, identity, relative)
        if not backend.cache_root.identity.same_stable_object(root_before):
            return _lookup_result(CacheCatalogLookupStatus.CATALOG_UNSTABLE, identity, relative)
        parents, parent_status = _safe_parent_chain(backend, relative)
        if parent_status is not None:
            if not _chain_remains_stable(backend, parents):
                return _lookup_result(CacheCatalogLookupStatus.CATALOG_UNSTABLE, identity, relative)
            if not backend.cache_root.identity.same_stable_object(backend.inspect_root()):
                return _lookup_result(CacheCatalogLookupStatus.CATALOG_UNSTABLE, identity, relative)
            return _lookup_result(parent_status, identity, relative)
        try:
            target_before = backend.inspect_catalog_relative(relative)
        except FileNotFoundError:
            if not _chain_remains_stable(backend, parents):
                return _lookup_result(CacheCatalogLookupStatus.CATALOG_UNSTABLE, identity, relative)
            if not backend.cache_root.identity.same_stable_object(backend.inspect_root()):
                return _lookup_result(CacheCatalogLookupStatus.CATALOG_UNSTABLE, identity, relative)
            return _lookup_result(CacheCatalogLookupStatus.RECORD_ABSENT, identity, relative)
        if target_before.object_type is not FilesystemObjectType.REGULAR_FILE:
            return _lookup_result(CacheCatalogLookupStatus.CATALOG_UNSAFE, identity, relative)
        read = backend.read_record_bounded(identity)
        if not read.stable_read or not target_before.same_stable_object(read.pre_read_identity):
            return _lookup_result(CacheCatalogLookupStatus.CATALOG_UNSTABLE, identity, relative)
        if read.limit_exceeded or read.data is None:
            return _lookup_result(CacheCatalogLookupStatus.CATALOG_CORRUPT, identity, relative)
        if not _chain_remains_stable(backend, parents):
            return _lookup_result(CacheCatalogLookupStatus.CATALOG_UNSTABLE, identity, relative)
        if not backend.cache_root.identity.same_stable_object(backend.inspect_root()):
            return _lookup_result(CacheCatalogLookupStatus.CATALOG_UNSTABLE, identity, relative)
        record = parse_cache_catalog_record(read.data)
        if record.identity != identity:
            return _lookup_result(CacheCatalogLookupStatus.CATALOG_CORRUPT, identity, relative)
        if isinstance(record, CacheCatalogTombstone):
            return _lookup_result(
                CacheCatalogLookupStatus.RECORD_ABSENT,
                identity,
                relative,
                tombstone_revision=record.record_revision,
            )
        return _lookup_result(
            CacheCatalogLookupStatus.RECORD_FOUND, identity, relative, record=record
        )
    except CacheCatalogUnsupportedVersionError:
        return _lookup_result(CacheCatalogLookupStatus.CATALOG_UNSUPPORTED, identity, relative)
    except CacheCatalogContractError:
        return _lookup_result(CacheCatalogLookupStatus.CATALOG_CORRUPT, identity, relative)
    except (SymlinkRejectedError, UnsupportedFilesystemObjectError):
        return _lookup_result(CacheCatalogLookupStatus.CATALOG_UNSAFE, identity, relative)
    except (FileNotFoundError, UnstableFilesystemObjectError):
        return _lookup_result(CacheCatalogLookupStatus.CATALOG_UNSTABLE, identity, relative)
    except (CacheLookupPermissionError, CacheLookupIOError, CacheLookupFilesystemError, OSError):
        return _lookup_result(CacheCatalogLookupStatus.CATALOG_IO_FAILURE, identity, relative)
