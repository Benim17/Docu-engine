from dataclasses import FrozenInstanceError, replace
from copy import deepcopy
import json

import pytest

from engine.storage import (
    ARTIFACT_CONTRACT_VERSION,
    ArtifactCategory,
    ArtifactContractError,
    ArtifactDescriptor,
    ArtifactGroup,
    ArtifactGroupMembership,
    ArtifactGroupType,
    ArtifactType,
    ConfigDependency,
    GroupMemberRequirement,
    InputDependency,
    IntegrityStatus,
    PresenceStatus,
    ProducerIdentity,
    ProtectionClass,
    RuntimeLocation,
    deserialize_artifact_descriptor,
    serialize_artifact_descriptor,
)


REQUIRED_ARTIFACT_TYPES = {
    "source_image", "source_voiceover", "image_manifest", "repository_config",
    "effective_run_config", "style_config", "caption_audio_normalized",
    "source_images_concat", "semantic_timeline_concat", "base_video_intermediate",
    "semantic_video_intermediate", "captions_json", "captions_srt",
    "image_intelligence_plan", "semantic_edit_plan", "caption_director_plan",
    "story_director_plan", "audio_plan", "audio_diagnostics", "motion_analysis",
    "motion_plan", "final_documentary", "run_log", "validation_report",
    "validation_hash_inventory", "validation_package", "release_archive", "unknown",
}


def descriptor(**changes):
    values = {
        "artifact_type": ArtifactType.SEMANTIC_EDIT_PLAN,
        "primary_category": ArtifactCategory.CACHE,
        "logical_id": "semantic-edit-plan",
        "cacheable": True,
        "protection": ProtectionClass.PROTECTED_GIT_TRACKED,
        "producer": ProducerIdentity("engine.semantic", "4.4.0", "4.4.0"),
        "input_dependencies": (
            InputDependency("captions", ArtifactType.CAPTIONS_JSON),
            InputDependency("source_image_001", ArtifactType.SOURCE_IMAGE),
        ),
        "config_dependencies": (
            ConfigDependency("semantic.preferred_beat_duration"),
            ConfigDependency("captions.language"),
        ),
    }
    values.update(changes)
    return ArtifactDescriptor(**values)


def test_contract_defines_every_required_primary_category_and_artifact_type():
    assert {
        "source", "config", "cache", "work", "output", "logs", "validation", "archive",
    } <= {item.value for item in ArtifactCategory}
    assert REQUIRED_ARTIFACT_TYPES <= {item.value for item in ArtifactType}
    assert ArtifactType.BASE_VIDEO_INTERMEDIATE is not ArtifactType.SEMANTIC_VIDEO_INTERMEDIATE


@pytest.mark.parametrize("artifact_type", list(ArtifactType))
def test_every_artifact_type_can_be_constructed(artifact_type):
    protection = (
        ProtectionClass.PROTECTED_UNKNOWN
        if artifact_type is ArtifactType.UNKNOWN
        else ProtectionClass.PROTECTED_GIT_TRACKED
    )
    item = descriptor(
        artifact_type=artifact_type,
        protection=protection,
        cacheable=artifact_type is not ArtifactType.UNKNOWN,
    )
    assert item.artifact_type is artifact_type


def test_descriptor_requires_exactly_one_typed_primary_category():
    descriptor(primary_category=ArtifactCategory.CACHE)
    with pytest.raises(ArtifactContractError, match="exactly one"):
        descriptor(primary_category=None)
    with pytest.raises(ArtifactContractError, match="exactly one"):
        descriptor(primary_category=(ArtifactCategory.CACHE, ArtifactCategory.WORK))


def test_unknown_artifact_is_explicit_and_fail_closed():
    item = descriptor(
        artifact_type=ArtifactType.UNKNOWN,
        primary_category=ArtifactCategory.WORK,
        cacheable=False,
        protection=ProtectionClass.PROTECTED_UNKNOWN,
    )
    assert item.cleanup_candidate is False
    with pytest.raises(ArtifactContractError, match="protected_unknown"):
        replace(item, protection=ProtectionClass.ELIGIBLE_WORK)
    with pytest.raises(ArtifactContractError, match="cannot be cacheable"):
        replace(item, cacheable=True)


@pytest.mark.parametrize("category", [
    ArtifactCategory.SOURCE,
    ArtifactCategory.CONFIG,
    ArtifactCategory.OUTPUT,
    ArtifactCategory.VALIDATION,
    ArtifactCategory.ARCHIVE,
])
def test_protected_categories_cannot_be_cleanup_candidates(category):
    with pytest.raises(ArtifactContractError, match="cannot be cleanup candidates"):
        descriptor(primary_category=category, protection=ProtectionClass.ELIGIBLE_WORK)


def test_cacheable_does_not_grant_cleanup_eligibility():
    item = descriptor(cacheable=True, protection=ProtectionClass.PROTECTED_GIT_TRACKED)
    assert item.cacheable is True
    assert item.cleanup_candidate is False


def test_cleanup_eligible_protection_must_match_category():
    with pytest.raises(ArtifactContractError, match="eligible_cache requires"):
        descriptor(
            primary_category=ArtifactCategory.WORK,
            protection=ProtectionClass.ELIGIBLE_CACHE,
        )
    with pytest.raises(ArtifactContractError, match="eligible_work requires"):
        descriptor(
            primary_category=ArtifactCategory.CACHE,
            protection=ProtectionClass.ELIGIBLE_WORK,
        )


def test_presence_and_integrity_are_independent_contract_dimensions():
    missing = descriptor(presence=PresenceStatus.MISSING, integrity=IntegrityStatus.UNKNOWN)
    corrupt = descriptor(presence=PresenceStatus.PRESENT, integrity=IntegrityStatus.CORRUPT)
    assert missing.presence is PresenceStatus.MISSING
    assert corrupt.integrity is IntegrityStatus.CORRUPT
    assert {item.value for item in IntegrityStatus} == {
        "unverified", "complete", "partial", "corrupt", "unknown",
    }
    with pytest.raises(ArtifactContractError, match="missing artifact"):
        descriptor(presence=PresenceStatus.MISSING, integrity=IntegrityStatus.COMPLETE)


def test_artifact_group_supports_required_and_optional_members():
    group = ArtifactGroup(
        ArtifactGroupType.AUDIO_PLAN_BUNDLE,
        "audio-plan-bundle",
        (ArtifactType.AUDIO_DIAGNOSTICS, ArtifactType.AUDIO_PLAN),
        (ArtifactType.RUN_LOG,),
    )
    assert group.require_complete_for_reuse is True
    assert group.to_dict()["required_members"] == ["audio_diagnostics", "audio_plan"]


def test_audio_plan_bundle_requires_both_atomic_members():
    with pytest.raises(ArtifactContractError, match="requires audio_plan and audio_diagnostics"):
        ArtifactGroup(
            ArtifactGroupType.AUDIO_PLAN_BUNDLE,
            "audio-plan-bundle",
            (ArtifactType.AUDIO_PLAN,),
        )
    with pytest.raises(ArtifactContractError, match="complete before reuse"):
        ArtifactGroup(
            ArtifactGroupType.AUDIO_PLAN_BUNDLE,
            "audio-plan-bundle",
            (ArtifactType.AUDIO_PLAN, ArtifactType.AUDIO_DIAGNOSTICS),
            require_complete_for_reuse=False,
        )


def test_group_membership_marks_required_audio_members():
    membership = ArtifactGroupMembership(
        ArtifactGroupType.AUDIO_PLAN_BUNDLE,
        "audio-plan-bundle",
        GroupMemberRequirement.REQUIRED,
    )
    item = descriptor(
        artifact_type=ArtifactType.AUDIO_PLAN,
        group_membership=membership,
    )
    assert item.identity_dict()["group_membership"]["requirement"] == "required"


def test_identical_descriptors_have_byte_identical_utf8_serialization():
    first = serialize_artifact_descriptor(descriptor()).encode("utf-8")
    second = serialize_artifact_descriptor(descriptor()).encode("utf-8")
    assert first == second
    assert first.endswith(b"\n")
    assert b"timestamp" not in first and b"uuid" not in first
    assert list(json.loads(first)) == sorted(json.loads(first))


def test_absolute_runtime_paths_are_excluded_from_identity_serialization():
    first = descriptor(runtime_location=RuntimeLocation("/Users/one/project/output/plan.json"))
    second = descriptor(runtime_location=RuntimeLocation("/Volumes/two/project/output/plan.json"))
    assert serialize_artifact_descriptor(first) == serialize_artifact_descriptor(second)
    assert "/Users/one" not in serialize_artifact_descriptor(first)
    assert "runtime_location" not in first.identity_dict()
    assert first == second
    assert hash(first) == hash(second)


@pytest.mark.parametrize("logical_id", [
    "/Users/local/project/output.json",
    "C:\\Users\\local\\output.json",
    "\\\\server\\share\\output.json",
])
def test_absolute_paths_are_rejected_from_logical_identity(logical_id):
    with pytest.raises(ArtifactContractError, match="absolute path"):
        descriptor(logical_id=logical_id)


def test_semantically_ordered_input_dependencies_preserve_order():
    forward = descriptor(input_dependencies=(
        InputDependency("image_1", ArtifactType.SOURCE_IMAGE),
        InputDependency("image_2", ArtifactType.SOURCE_IMAGE),
    ))
    reverse = descriptor(input_dependencies=tuple(reversed(forward.input_dependencies)))
    assert [item["role"] for item in forward.identity_dict()["input_dependencies"]] == [
        "image_1", "image_2",
    ]
    assert serialize_artifact_descriptor(forward) != serialize_artifact_descriptor(reverse)


def test_set_like_config_dependencies_serialize_in_canonical_order():
    first = descriptor(config_dependencies=(
        ConfigDependency("semantic.preferred_beat_duration"),
        ConfigDependency("captions.language"),
    ))
    second = descriptor(config_dependencies=tuple(reversed(first.config_dependencies)))
    assert serialize_artifact_descriptor(first) == serialize_artifact_descriptor(second)


def test_set_like_group_members_serialize_in_canonical_order():
    first = ArtifactGroup(
        ArtifactGroupType.AUDIO_PLAN_BUNDLE,
        "audio-plan-bundle",
        (ArtifactType.AUDIO_PLAN, ArtifactType.AUDIO_DIAGNOSTICS),
        (ArtifactType.RUN_LOG, ArtifactType.VALIDATION_REPORT),
    )
    second = ArtifactGroup(
        ArtifactGroupType.AUDIO_PLAN_BUNDLE,
        "audio-plan-bundle",
        tuple(reversed(first.required_members)),
        tuple(reversed(first.optional_members)),
    )
    assert first == second
    assert hash(first) == hash(second)
    assert first.to_dict() == second.to_dict()


@pytest.mark.parametrize(("field", "unknown"), [
    ("artifact_type", "future_artifact"),
    ("primary_category", "temporaryish"),
    ("protection", "probably_safe"),
    ("presence", "somewhere"),
    ("integrity", "mostly_complete"),
])
def test_unknown_enum_values_fail_closed_during_deserialization(field, unknown):
    payload = descriptor().identity_dict()
    payload[field] = unknown
    with pytest.raises(ArtifactContractError, match=f"Unsupported {field}"):
        deserialize_artifact_descriptor(json.dumps(payload))


def test_unknown_nested_enum_value_fails_closed_during_deserialization():
    payload = descriptor().identity_dict()
    payload["input_dependencies"][0]["artifact_type"] = "future_source"
    with pytest.raises(ArtifactContractError, match="input dependency artifact_type"):
        deserialize_artifact_descriptor(json.dumps(payload))


def test_unknown_extra_fields_fail_closed_during_deserialization():
    payload = descriptor().identity_dict()
    payload["future_field"] = "not silently ignored"
    with pytest.raises(ArtifactContractError, match="Unknown artifact descriptor fields"):
        deserialize_artifact_descriptor(json.dumps(payload))

    payload = descriptor().identity_dict()
    payload["producer"]["future_field"] = True
    with pytest.raises(ArtifactContractError, match="Unknown producer fields"):
        deserialize_artifact_descriptor(json.dumps(payload))


@pytest.mark.parametrize("field", [
    "artifact_type", "primary_category", "logical_id", "cacheable", "protection",
    "producer", "input_dependencies", "config_dependencies", "presence", "integrity",
    "contract_version",
])
def test_missing_required_descriptor_fields_are_rejected(field):
    payload = descriptor().identity_dict()
    del payload[field]
    with pytest.raises(ArtifactContractError, match=f"Missing required field: {field}"):
        deserialize_artifact_descriptor(json.dumps(payload))


def test_contract_version_is_explicit_and_unknown_versions_fail_closed():
    assert descriptor().contract_version == ARTIFACT_CONTRACT_VERSION == "1.0"
    with pytest.raises(ArtifactContractError, match="Unsupported artifact contract version"):
        descriptor(contract_version="2.0")
    with pytest.raises(ArtifactContractError, match="Unsupported artifact contract version"):
        descriptor(contract_version=True)
    with pytest.raises(ArtifactContractError, match="producer.version"):
        ProducerIdentity("engine.semantic", True)


def test_round_trip_preserves_identity_contract():
    original = descriptor(
        group_membership=ArtifactGroupMembership(
            ArtifactGroupType.AUDIO_PLAN_BUNDLE,
            "audio-plan-bundle",
            GroupMemberRequirement.REQUIRED,
        )
    )
    restored = deserialize_artifact_descriptor(serialize_artifact_descriptor(original))
    assert restored == original


def test_models_are_frozen_and_copy_mutable_constructor_inputs():
    item = descriptor()
    with pytest.raises(FrozenInstanceError):
        item.logical_id = "changed"

    inputs = [InputDependency("voiceover", ArtifactType.SOURCE_VOICEOVER)]
    with pytest.raises(ArtifactContractError, match="immutable tuple"):
        descriptor(input_dependencies=inputs)
    inputs.append(InputDependency("image", ArtifactType.SOURCE_IMAGE))
    assert item.input_dependencies != tuple(inputs)


def test_deserialization_does_not_mutate_input_dicts_or_lists():
    payload = descriptor().identity_dict()
    original = deepcopy(payload)
    restored = ArtifactDescriptor.from_dict(payload)
    assert payload == original
    assert restored.identity_dict() == original


def test_models_do_not_create_move_or_delete_files(tmp_path):
    before = tuple(tmp_path.iterdir())
    serialized = serialize_artifact_descriptor(
        descriptor(runtime_location=RuntimeLocation(str(tmp_path / "not-created.json")))
    )
    deserialize_artifact_descriptor(serialized)
    assert tuple(tmp_path.iterdir()) == before == ()


def test_storage_contract_is_not_imported_by_pipeline():
    from engine import pipeline

    assert "engine.storage" not in pipeline.__dict__
