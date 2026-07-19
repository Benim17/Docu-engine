"""Pure, deterministic artifact contracts for Cache & Storage Foundation.

This module deliberately performs no filesystem I/O. Runtime locations are kept
separate from artifact identity and are never included in identity serialization.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence, TypeVar


ARTIFACT_CONTRACT_VERSION = "1.0"


class ArtifactContractError(ValueError):
    """Raised when artifact metadata violates the storage contract."""


class ArtifactCategory(str, Enum):
    SOURCE = "source"
    CONFIG = "config"
    CACHE = "cache"
    WORK = "work"
    OUTPUT = "output"
    LOGS = "logs"
    VALIDATION = "validation"
    ARCHIVE = "archive"


class ArtifactType(str, Enum):
    SOURCE_IMAGE = "source_image"
    SOURCE_VOICEOVER = "source_voiceover"
    IMAGE_MANIFEST = "image_manifest"
    REPOSITORY_CONFIG = "repository_config"
    EFFECTIVE_RUN_CONFIG = "effective_run_config"
    STYLE_CONFIG = "style_config"
    CAPTION_AUDIO_NORMALIZED = "caption_audio_normalized"
    SOURCE_IMAGES_CONCAT = "source_images_concat"
    SEMANTIC_TIMELINE_CONCAT = "semantic_timeline_concat"
    BASE_VIDEO_INTERMEDIATE = "base_video_intermediate"
    SEMANTIC_VIDEO_INTERMEDIATE = "semantic_video_intermediate"
    CAPTIONS_JSON = "captions_json"
    CAPTIONS_SRT = "captions_srt"
    IMAGE_INTELLIGENCE_PLAN = "image_intelligence_plan"
    SEMANTIC_EDIT_PLAN = "semantic_edit_plan"
    CAPTION_DIRECTOR_PLAN = "caption_director_plan"
    STORY_DIRECTOR_PLAN = "story_director_plan"
    AUDIO_PLAN = "audio_plan"
    AUDIO_DIAGNOSTICS = "audio_diagnostics"
    MOTION_ANALYSIS = "motion_analysis"
    MOTION_PLAN = "motion_plan"
    FINAL_DOCUMENTARY = "final_documentary"
    RUN_LOG = "run_log"
    VALIDATION_REPORT = "validation_report"
    VALIDATION_HASH_INVENTORY = "validation_hash_inventory"
    VALIDATION_PACKAGE = "validation_package"
    RELEASE_ARCHIVE = "release_archive"
    UNKNOWN = "unknown"


class ProtectionClass(str, Enum):
    IMMUTABLE_SOURCE = "immutable_source"
    PROTECTED_CONFIG = "protected_config"
    PROTECTED_OUTPUT = "protected_output"
    PROTECTED_VALIDATION = "protected_validation"
    PROTECTED_ARCHIVE = "protected_archive"
    PROTECTED_GIT_TRACKED = "protected_git_tracked"
    PROTECTED_UNKNOWN = "protected_unknown"
    ELIGIBLE_CACHE = "eligible_cache"
    ELIGIBLE_WORK = "eligible_work"


class PresenceStatus(str, Enum):
    PRESENT = "present"
    MISSING = "missing"


class IntegrityStatus(str, Enum):
    UNVERIFIED = "unverified"
    COMPLETE = "complete"
    PARTIAL = "partial"
    CORRUPT = "corrupt"
    UNKNOWN = "unknown"


class ArtifactGroupType(str, Enum):
    AUDIO_PLAN_BUNDLE = "audio_plan_bundle"


class GroupMemberRequirement(str, Enum):
    REQUIRED = "required"
    OPTIONAL = "optional"


_EnumT = TypeVar("_EnumT", bound=Enum)


def _enum_value(enum_type: type[_EnumT], value: Any, field: str) -> _EnumT:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ArtifactContractError(f"Unsupported {field}: {value!r}.") from exc


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ArtifactContractError(f"{field} must be a non-empty string.")
    return value


def _optional_text(value: Any, field: str) -> str | None:
    if value is None:
        return None
    return _text(value, field)


def _logical_identifier(value: Any, field: str) -> str:
    result = _text(value, field)
    if result.startswith(("/", "\\\\")) or re.match(r"^[A-Za-z]:[\\/]", result):
        raise ArtifactContractError(f"{field} must not be an absolute path.")
    return result


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ArtifactContractError(f"{field} must be an object.")
    return value


def _required(data: Mapping[str, Any], field: str) -> Any:
    if field not in data:
        raise ArtifactContractError(f"Missing required field: {field}.")
    return data[field]


def _reject_unknown_fields(data: Mapping[str, Any], allowed: frozenset[str], field: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ArtifactContractError(f"Unknown {field} fields: {', '.join(unknown)}.")


@dataclass(frozen=True)
class ProducerIdentity:
    module: str
    version: str
    schema_version: str | None = None

    def __post_init__(self) -> None:
        _text(self.module, "producer.module")
        _text(self.version, "producer.version")
        _optional_text(self.schema_version, "producer.schema_version")

    def to_dict(self) -> dict[str, Any]:
        return {
            "module": self.module,
            "schema_version": self.schema_version,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProducerIdentity":
        data = _mapping(value, "producer")
        _reject_unknown_fields(
            data, frozenset({"module", "version", "schema_version"}), "producer"
        )
        return cls(
            module=_required(data, "module"),
            version=_required(data, "version"),
            schema_version=data.get("schema_version"),
        )


@dataclass(frozen=True)
class InputDependency:
    role: str
    artifact_type: ArtifactType
    digest: str | None = None

    def __post_init__(self) -> None:
        _text(self.role, "input_dependency.role")
        if not isinstance(self.artifact_type, ArtifactType):
            raise ArtifactContractError("input_dependency.artifact_type must be an ArtifactType.")
        _optional_text(self.digest, "input_dependency.digest")

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_type": self.artifact_type.value,
            "digest": self.digest,
            "role": self.role,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "InputDependency":
        data = _mapping(value, "input dependency")
        _reject_unknown_fields(
            data, frozenset({"role", "artifact_type", "digest"}), "input dependency"
        )
        return cls(
            role=_required(data, "role"),
            artifact_type=_enum_value(
                ArtifactType, _required(data, "artifact_type"), "input dependency artifact_type"
            ),
            digest=data.get("digest"),
        )


@dataclass(frozen=True)
class ConfigDependency:
    logical_path: str
    digest: str | None = None

    def __post_init__(self) -> None:
        path = _logical_identifier(self.logical_path, "config_dependency.logical_path")
        if ".." in path.split("."):
            raise ArtifactContractError("config_dependency.logical_path must be logical, not absolute.")
        _optional_text(self.digest, "config_dependency.digest")

    def to_dict(self) -> dict[str, Any]:
        return {"digest": self.digest, "logical_path": self.logical_path}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ConfigDependency":
        data = _mapping(value, "config dependency")
        _reject_unknown_fields(
            data, frozenset({"logical_path", "digest"}), "config dependency"
        )
        return cls(
            logical_path=_required(data, "logical_path"),
            digest=data.get("digest"),
        )


@dataclass(frozen=True)
class RuntimeLocation:
    """Observed location metadata, explicitly excluded from artifact identity."""

    path: str

    def __post_init__(self) -> None:
        _text(self.path, "runtime_location.path")


@dataclass(frozen=True)
class ArtifactGroupMembership:
    group_type: ArtifactGroupType
    group_id: str
    requirement: GroupMemberRequirement

    def __post_init__(self) -> None:
        if not isinstance(self.group_type, ArtifactGroupType):
            raise ArtifactContractError("group_membership.group_type must be an ArtifactGroupType.")
        _text(self.group_id, "group_membership.group_id")
        if not isinstance(self.requirement, GroupMemberRequirement):
            raise ArtifactContractError(
                "group_membership.requirement must be a GroupMemberRequirement."
            )

    def to_dict(self) -> dict[str, str]:
        return {
            "group_id": self.group_id,
            "group_type": self.group_type.value,
            "requirement": self.requirement.value,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ArtifactGroupMembership":
        data = _mapping(value, "group membership")
        _reject_unknown_fields(
            data,
            frozenset({"group_type", "group_id", "requirement"}),
            "group membership",
        )
        return cls(
            group_type=_enum_value(
                ArtifactGroupType, _required(data, "group_type"), "group membership group_type"
            ),
            group_id=_required(data, "group_id"),
            requirement=_enum_value(
                GroupMemberRequirement,
                _required(data, "requirement"),
                "group membership requirement",
            ),
        )


@dataclass(frozen=True)
class ArtifactGroup:
    group_type: ArtifactGroupType
    group_id: str
    required_members: tuple[ArtifactType, ...]
    optional_members: tuple[ArtifactType, ...] = ()
    require_complete_for_reuse: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.group_type, ArtifactGroupType):
            raise ArtifactContractError("artifact_group.group_type must be an ArtifactGroupType.")
        _text(self.group_id, "artifact_group.group_id")
        if not self.required_members:
            raise ArtifactContractError("artifact_group.required_members cannot be empty.")
        for name, members in (
            ("required_members", self.required_members),
            ("optional_members", self.optional_members),
        ):
            if not isinstance(members, tuple) or any(
                not isinstance(member, ArtifactType) for member in members
            ):
                raise ArtifactContractError(f"artifact_group.{name} must be a tuple of ArtifactType values.")
            if len(set(members)) != len(members):
                raise ArtifactContractError(f"artifact_group.{name} cannot contain duplicates.")
        if set(self.required_members) & set(self.optional_members):
            raise ArtifactContractError("Required and optional group members must be disjoint.")
        if not isinstance(self.require_complete_for_reuse, bool):
            raise ArtifactContractError("require_complete_for_reuse must be boolean.")
        object.__setattr__(
            self, "required_members", tuple(sorted(self.required_members, key=lambda item: item.value))
        )
        object.__setattr__(
            self, "optional_members", tuple(sorted(self.optional_members, key=lambda item: item.value))
        )
        if self.group_type is ArtifactGroupType.AUDIO_PLAN_BUNDLE:
            expected = {ArtifactType.AUDIO_PLAN, ArtifactType.AUDIO_DIAGNOSTICS}
            if set(self.required_members) != expected:
                raise ArtifactContractError(
                    "audio_plan_bundle requires audio_plan and audio_diagnostics."
                )
            if not self.require_complete_for_reuse:
                raise ArtifactContractError("audio_plan_bundle must be complete before reuse.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "group_type": self.group_type.value,
            "optional_members": [member.value for member in self.optional_members],
            "require_complete_for_reuse": self.require_complete_for_reuse,
            "required_members": [member.value for member in self.required_members],
        }


_NON_CLEANUP_CATEGORIES = frozenset({
    ArtifactCategory.SOURCE,
    ArtifactCategory.CONFIG,
    ArtifactCategory.OUTPUT,
    ArtifactCategory.LOGS,
    ArtifactCategory.VALIDATION,
    ArtifactCategory.ARCHIVE,
})


@dataclass(frozen=True)
class ArtifactDescriptor:
    artifact_type: ArtifactType
    primary_category: ArtifactCategory
    logical_id: str
    cacheable: bool
    protection: ProtectionClass
    producer: ProducerIdentity
    input_dependencies: tuple[InputDependency, ...] = ()
    config_dependencies: tuple[ConfigDependency, ...] = ()
    group_membership: ArtifactGroupMembership | None = None
    presence: PresenceStatus = PresenceStatus.PRESENT
    integrity: IntegrityStatus = IntegrityStatus.UNVERIFIED
    runtime_location: RuntimeLocation | None = field(default=None, compare=False, hash=False)
    contract_version: str = ARTIFACT_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.artifact_type, ArtifactType):
            raise ArtifactContractError("artifact_type must be an ArtifactType.")
        if not isinstance(self.primary_category, ArtifactCategory):
            raise ArtifactContractError("primary_category must be exactly one ArtifactCategory.")
        _logical_identifier(self.logical_id, "logical_id")
        if not isinstance(self.cacheable, bool):
            raise ArtifactContractError("cacheable must be boolean.")
        if not isinstance(self.protection, ProtectionClass):
            raise ArtifactContractError("protection must be a ProtectionClass.")
        if not isinstance(self.producer, ProducerIdentity):
            raise ArtifactContractError("producer must be a ProducerIdentity.")
        if not isinstance(self.presence, PresenceStatus):
            raise ArtifactContractError("presence must be a PresenceStatus.")
        if not isinstance(self.integrity, IntegrityStatus):
            raise ArtifactContractError("integrity must be an IntegrityStatus.")
        if self.presence is PresenceStatus.MISSING and self.integrity in {
            IntegrityStatus.COMPLETE,
            IntegrityStatus.PARTIAL,
            IntegrityStatus.CORRUPT,
        }:
            raise ArtifactContractError(
                "A missing artifact cannot have a validated physical integrity status."
            )
        if self.runtime_location is not None and not isinstance(self.runtime_location, RuntimeLocation):
            raise ArtifactContractError("runtime_location must be a RuntimeLocation or None.")
        if self.group_membership is not None and not isinstance(
            self.group_membership, ArtifactGroupMembership
        ):
            raise ArtifactContractError(
                "group_membership must be an ArtifactGroupMembership or None."
            )
        if self.contract_version != ARTIFACT_CONTRACT_VERSION:
            raise ArtifactContractError(
                f"Unsupported artifact contract version: {self.contract_version!r}."
            )
        for name, dependencies, expected in (
            ("input_dependencies", self.input_dependencies, InputDependency),
            ("config_dependencies", self.config_dependencies, ConfigDependency),
        ):
            if not isinstance(dependencies, tuple) or any(
                not isinstance(dependency, expected) for dependency in dependencies
            ):
                raise ArtifactContractError(f"{name} must be an immutable tuple of contracts.")
        config_paths = [dependency.logical_path for dependency in self.config_dependencies]
        if len(set(config_paths)) != len(config_paths):
            raise ArtifactContractError("config_dependencies cannot repeat a logical path.")
        object.__setattr__(
            self,
            "config_dependencies",
            tuple(sorted(
                self.config_dependencies,
                key=lambda dependency: (dependency.logical_path, dependency.digest or ""),
            )),
        )
        if self.artifact_type is ArtifactType.UNKNOWN:
            if self.protection is not ProtectionClass.PROTECTED_UNKNOWN:
                raise ArtifactContractError("Unknown artifacts must be protected_unknown.")
            if self.cacheable:
                raise ArtifactContractError("Unknown artifacts cannot be cacheable.")
        if self.primary_category in _NON_CLEANUP_CATEGORIES and self.protection in {
            ProtectionClass.ELIGIBLE_CACHE,
            ProtectionClass.ELIGIBLE_WORK,
        }:
            raise ArtifactContractError(
                f"{self.primary_category.value} artifacts cannot be cleanup candidates."
            )
        if self.protection is ProtectionClass.ELIGIBLE_CACHE and self.primary_category is not ArtifactCategory.CACHE:
            raise ArtifactContractError("eligible_cache requires primary category cache.")
        if self.protection is ProtectionClass.ELIGIBLE_WORK and self.primary_category is not ArtifactCategory.WORK:
            raise ArtifactContractError("eligible_work requires primary category work.")

    @property
    def cleanup_candidate(self) -> bool:
        """Contract eligibility only; never an instruction or permission to delete."""

        return self.protection in {
            ProtectionClass.ELIGIBLE_CACHE,
            ProtectionClass.ELIGIBLE_WORK,
        }

    def identity_dict(self) -> dict[str, Any]:
        """Return deterministic identity data, excluding runtime location."""

        return {
            "artifact_type": self.artifact_type.value,
            "cacheable": self.cacheable,
            "config_dependencies": [
                dependency.to_dict() for dependency in self.config_dependencies
            ],
            "contract_version": self.contract_version,
            "group_membership": (
                self.group_membership.to_dict() if self.group_membership is not None else None
            ),
            "input_dependencies": [dependency.to_dict() for dependency in self.input_dependencies],
            "integrity": self.integrity.value,
            "logical_id": self.logical_id,
            "presence": self.presence.value,
            "primary_category": self.primary_category.value,
            "producer": self.producer.to_dict(),
            "protection": self.protection.value,
        }

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        *,
        runtime_location: RuntimeLocation | None = None,
    ) -> "ArtifactDescriptor":
        data = _mapping(value, "artifact descriptor")
        _reject_unknown_fields(
            data,
            frozenset({
                "artifact_type",
                "cacheable",
                "config_dependencies",
                "contract_version",
                "group_membership",
                "input_dependencies",
                "integrity",
                "logical_id",
                "presence",
                "primary_category",
                "producer",
                "protection",
            }),
            "artifact descriptor",
        )
        inputs = _required(data, "input_dependencies")
        configs = _required(data, "config_dependencies")
        if not isinstance(inputs, Sequence) or isinstance(inputs, (str, bytes)):
            raise ArtifactContractError("input_dependencies must be an array.")
        if not isinstance(configs, Sequence) or isinstance(configs, (str, bytes)):
            raise ArtifactContractError("config_dependencies must be an array.")
        membership = data.get("group_membership")
        return cls(
            artifact_type=_enum_value(
                ArtifactType, _required(data, "artifact_type"), "artifact_type"
            ),
            primary_category=_enum_value(
                ArtifactCategory, _required(data, "primary_category"), "primary_category"
            ),
            logical_id=_required(data, "logical_id"),
            cacheable=_required(data, "cacheable"),
            protection=_enum_value(
                ProtectionClass, _required(data, "protection"), "protection"
            ),
            producer=ProducerIdentity.from_dict(_required(data, "producer")),
            input_dependencies=tuple(InputDependency.from_dict(item) for item in inputs),
            config_dependencies=tuple(ConfigDependency.from_dict(item) for item in configs),
            group_membership=(
                ArtifactGroupMembership.from_dict(membership) if membership is not None else None
            ),
            presence=_enum_value(PresenceStatus, _required(data, "presence"), "presence"),
            integrity=_enum_value(IntegrityStatus, _required(data, "integrity"), "integrity"),
            runtime_location=runtime_location,
            contract_version=_required(data, "contract_version"),
        )


def serialize_artifact_descriptor(descriptor: ArtifactDescriptor) -> str:
    if not isinstance(descriptor, ArtifactDescriptor):
        raise ArtifactContractError("Only ArtifactDescriptor instances can be serialized.")
    return json.dumps(
        descriptor.identity_dict(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"


def deserialize_artifact_descriptor(serialized: str | bytes) -> ArtifactDescriptor:
    if isinstance(serialized, bytes):
        try:
            serialized = serialized.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ArtifactContractError("Artifact descriptor must be UTF-8.") from exc
    if not isinstance(serialized, str):
        raise ArtifactContractError("Serialized artifact descriptor must be text or UTF-8 bytes.")
    try:
        value = json.loads(serialized)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ArtifactContractError("Artifact descriptor is not valid JSON.") from exc
    return ArtifactDescriptor.from_dict(_mapping(value, "artifact descriptor"))
