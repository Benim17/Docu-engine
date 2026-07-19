from dataclasses import replace
from copy import deepcopy
import builtins
import json
import math
import os

import pytest

from engine.storage import (
    ArtifactCategory,
    ArtifactContractError,
    ArtifactDescriptor,
    ArtifactGroup,
    ArtifactGroupType,
    ArtifactType,
    CacheKey,
    CacheKeyContractError,
    CacheKeyMaterial,
    ConfigFingerprint,
    GroupCacheKeyMaterial,
    InputFingerprint,
    InputDependency,
    ConfigDependency,
    IntegrityStatus,
    PresenceStatus,
    ProducerIdentity,
    ProtectionClass,
    RuntimeFingerprint,
    RuntimeLocation,
    derive_cache_key,
    serialize_cache_key_material,
)


SHA_A = "a" * 64
SHA_B = "b" * 64


def artifact(**changes):
    values = {
        "artifact_type": ArtifactType.SEMANTIC_EDIT_PLAN,
        "primary_category": ArtifactCategory.CACHE,
        "logical_id": "semantic-edit-plan",
        "cacheable": True,
        "protection": ProtectionClass.ELIGIBLE_CACHE,
        "producer": ProducerIdentity("engine.semantic", "4.4.0", "4.4.0"),
    }
    values.update(changes)
    return ArtifactDescriptor(**values)


def material(**changes):
    values = {
        "artifact": artifact(),
        "input_fingerprints": (
            InputFingerprint("captions", ArtifactType.CAPTIONS_JSON, SHA_A),
            InputFingerprint("image", ArtifactType.SOURCE_IMAGE, SHA_B, "images/001.jpg"),
        ),
        "config_fingerprints": (
            ConfigFingerprint("captions.language", "svenska"),
            ConfigFingerprint("semantic.preferred_beat_duration", 4.0),
        ),
    }
    values.update(changes)
    return CacheKeyMaterial(**values)


def audio_group(**changes):
    values = {
        "group": ArtifactGroup(
            ArtifactGroupType.AUDIO_PLAN_BUNDLE,
            "audio-plan-bundle",
            (ArtifactType.AUDIO_PLAN, ArtifactType.AUDIO_DIAGNOSTICS),
        ),
        "producer": ProducerIdentity("engine.audio_director", "4.7.0", "1.0"),
        "input_fingerprints": (
            InputFingerprint("semantic", ArtifactType.SEMANTIC_EDIT_PLAN, SHA_A),
        ),
        "config_fingerprints": (ConfigFingerprint("audio.max_energy_delta", 0.3),),
    }
    values.update(changes)
    return GroupCacheKeyMaterial(**values)


def key(value):
    return str(derive_cache_key(value))


def test_identical_material_has_stable_key_and_serialization():
    assert key(material()) == key(material())
    assert serialize_cache_key_material(material()) == serialize_cache_key_material(material())
    assert key(material()).startswith("cache-v1.0:sha256:")
    assert len(key(material())) == len("cache-v1.0:sha256:") + 64


def test_dictionary_insertion_order_does_not_affect_key():
    first = ConfigFingerprint("semantic.rules", {"tone": "calm", "energy": 0.4})
    second = ConfigFingerprint("semantic.rules", {"energy": 0.4, "tone": "calm"})
    assert key(material(config_fingerprints=(first,))) == key(
        material(config_fingerprints=(second,))
    )


def test_config_dependency_order_does_not_affect_key():
    first = material()
    second = material(config_fingerprints=tuple(reversed(first.config_fingerprints)))
    assert first == second
    assert key(first) == key(second)


def test_group_member_order_does_not_affect_key():
    first = audio_group()
    reversed_group = ArtifactGroup(
        ArtifactGroupType.AUDIO_PLAN_BUNDLE,
        "audio-plan-bundle",
        tuple(reversed(first.group.required_members)),
    )
    assert key(first) == key(audio_group(group=reversed_group))


def test_semantically_ordered_inputs_change_key_when_reordered():
    first = material()
    second = material(input_fingerprints=tuple(reversed(first.input_fingerprints)))
    assert key(first) != key(second)


@pytest.mark.parametrize("root", [
    "/Users/one/project/output/plan.json",
    "C:\\Users\\one\\project\\output\\plan.json",
    "\\\\server\\share\\project\\output\\plan.json",
])
def test_runtime_project_roots_do_not_affect_key(root):
    located = artifact(runtime_location=RuntimeLocation(root))
    assert key(material(artifact=located)) == key(material(artifact=artifact()))
    serialized = serialize_cache_key_material(material(artifact=located)).decode("utf-8")
    assert root not in serialized and "runtime_location" not in serialized


def test_relevant_config_value_changes_key():
    assert key(material(config_fingerprints=(ConfigFingerprint("semantic.duration", 4.0),))) != key(
        material(config_fingerprints=(ConfigFingerprint("semantic.duration", 5.0),))
    )


def test_irrelevant_output_path_is_not_part_of_material():
    first = material(artifact=artifact(runtime_location=RuntimeLocation("/tmp/run-one/output.json")))
    second = material(artifact=artifact(runtime_location=RuntimeLocation("/tmp/run-two/output.json")))
    assert key(first) == key(second)
    assert b"output_path" not in serialize_cache_key_material(first)


def test_input_digest_role_and_artifact_type_each_invalidate_key():
    base = material(input_fingerprints=(
        InputFingerprint("voiceover", ArtifactType.SOURCE_VOICEOVER, SHA_A),
    ))
    assert key(base) != key(material(input_fingerprints=(
        InputFingerprint("voiceover", ArtifactType.SOURCE_VOICEOVER, SHA_B),
    )))
    assert key(base) != key(material(input_fingerprints=(
        InputFingerprint("narration", ArtifactType.SOURCE_VOICEOVER, SHA_A),
    )))
    assert key(base) != key(material(input_fingerprints=(
        InputFingerprint("voiceover", ArtifactType.SOURCE_IMAGE, SHA_A),
    )))


def test_equal_content_at_different_paths_has_equal_fingerprint():
    unix = InputFingerprint("image", ArtifactType.SOURCE_IMAGE, SHA_A, "images/001.jpg")
    windows = InputFingerprint("image", ArtifactType.SOURCE_IMAGE, SHA_A, "images\\001.jpg")
    assert unix == windows


def test_equal_digests_remain_distinct_when_roles_differ():
    voice = InputFingerprint("voiceover", ArtifactType.SOURCE_VOICEOVER, SHA_A)
    image = InputFingerprint("image", ArtifactType.SOURCE_IMAGE, SHA_A)
    assert voice != image
    assert key(material(input_fingerprints=(voice,))) != key(material(input_fingerprints=(image,)))


def test_artifact_producer_and_schema_changes_invalidate_key():
    base = material()
    assert key(base) != key(material(artifact=replace(base.artifact, artifact_type=ArtifactType.MOTION_PLAN)))
    assert key(base) != key(material(artifact=replace(
        base.artifact, producer=ProducerIdentity("engine.semantic", "4.4.1", "4.4.0")
    )))
    assert key(base) != key(material(artifact=replace(
        base.artifact, producer=ProducerIdentity("engine.semantic", "4.4.0", "4.4.1")
    )))


def test_logical_identity_and_declared_dependencies_invalidate_key():
    base = material()
    assert key(base) != key(material(artifact=replace(base.artifact, logical_id="other-plan")))
    assert key(base) != key(material(artifact=replace(
        base.artifact,
        input_dependencies=(
            InputDependency("captions", ArtifactType.CAPTIONS_JSON),
        ),
    )))
    assert key(base) != key(material(artifact=replace(
        base.artifact,
        config_dependencies=(ConfigDependency("semantic.duration"),),
    )))


def test_storage_category_protection_and_cacheability_do_not_affect_content_key():
    base = artifact(
        primary_category=ArtifactCategory.CACHE,
        protection=ProtectionClass.ELIGIBLE_CACHE,
        cacheable=True,
    )
    reclassified = artifact(
        primary_category=ArtifactCategory.WORK,
        protection=ProtectionClass.PROTECTED_GIT_TRACKED,
        cacheable=False,
    )
    assert key(material(artifact=base)) == key(material(artifact=reclassified))
    payload = serialize_cache_key_material(material(artifact=base))
    for excluded in (b"primary_category", b"protection", b"cacheable"):
        assert excluded not in payload


def test_runtime_backend_model_and_library_changes_invalidate_key():
    base = RuntimeFingerprint(
        backend_name="whisper",
        backend_version="1.0",
        model_name="large-v3",
        model_revision="a",
        library_versions={"mlx-whisper": "0.4.3"},
        algorithm_profile="caption-v1",
    )
    assert key(material(runtime_fingerprint=base)) != key(material(runtime_fingerprint=RuntimeFingerprint(
        backend_name="whisper", backend_version="2.0", model_name="large-v3",
        model_revision="a", library_versions={"mlx-whisper": "0.4.3"},
        algorithm_profile="caption-v1",
    )))
    assert key(material(runtime_fingerprint=base)) != key(material(runtime_fingerprint=RuntimeFingerprint(
        backend_name="whisper", backend_version="1.0", model_name="large-v3",
        model_revision="b", library_versions={"mlx-whisper": "0.4.3"},
        algorithm_profile="caption-v1",
    )))


def test_cache_contract_version_changes_key():
    first = derive_cache_key(material(cache_contract_version="1.0"))
    second = derive_cache_key(material(cache_contract_version="1.1"))
    assert first != second
    assert first.digest != second.digest
    assert str(first) != str(second)


def test_unicode_canonical_equivalents_have_equal_keys():
    composed = ConfigFingerprint("caption.label", "Café")
    decomposed = ConfigFingerprint("caption.label", "Cafe\u0301")
    assert key(material(config_fingerprints=(composed,))) == key(
        material(config_fingerprints=(decomposed,))
    )


def test_bool_and_integer_are_not_confused():
    assert key(material(config_fingerprints=(ConfigFingerprint("feature.value", True),))) != key(
        material(config_fingerprints=(ConfigFingerprint("feature.value", 1),))
    )


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_non_finite_floats_are_rejected(value):
    with pytest.raises(CacheKeyContractError, match="finite"):
        ConfigFingerprint("semantic.energy", value)


@pytest.mark.parametrize("value", [0.0, 1.0, 4.5, 0.1, 1e-300, 1e300])
def test_finite_float_values_are_stable(value):
    config = ConfigFingerprint("numeric.value", value)
    assert serialize_cache_key_material(material(config_fingerprints=(config,))) == serialize_cache_key_material(
        material(config_fingerprints=(ConfigFingerprint("numeric.value", value),))
    )


def test_negative_zero_normalizes_to_positive_zero():
    positive = ConfigFingerprint("numeric.zero", 0.0)
    negative = ConfigFingerprint("numeric.zero", -0.0)
    assert positive == negative
    assert key(material(config_fingerprints=(positive,))) == key(
        material(config_fingerprints=(negative,))
    )


def test_list_order_is_preserved_and_tuple_normalizes_to_list():
    ordered = ConfigFingerprint("semantic.phases", ["start", "end"])
    reversed_value = ConfigFingerprint("semantic.phases", ["end", "start"])
    tuple_value = ConfigFingerprint("semantic.phases", ("start", "end"))
    assert key(material(config_fingerprints=(ordered,))) != key(
        material(config_fingerprints=(reversed_value,))
    )
    assert ordered == tuple_value


@pytest.mark.parametrize("value", [{"a", "b"}, b"bytes", object()])
def test_non_json_and_unknown_types_are_rejected(value):
    with pytest.raises(CacheKeyContractError, match="unsupported type"):
        ConfigFingerprint("unsupported.value", value)


def test_non_string_dict_keys_are_rejected():
    with pytest.raises(CacheKeyContractError, match="keys must be strings"):
        ConfigFingerprint("invalid.object", {1: "one"})


@pytest.mark.parametrize("value", [
    "/dramatic opening",
    "C:\\Users\\editor\\project",
    "https://example.com/resource",
    "file:///local/resource",
    "\\\\server\\share",
    "text/with/slashes",
])
def test_generic_config_strings_are_canonicalized_as_data(value):
    config = ConfigFingerprint("generic.value", value)
    assert config.value == value


def test_machine_specific_config_paths_are_excluded_by_caller_policy():
    without_output_path = material(config_fingerprints=(ConfigFingerprint("semantic.duration", 4),))
    same_relevant_config = material(config_fingerprints=(ConfigFingerprint("semantic.duration", 4),))
    assert key(without_output_path) == key(same_relevant_config)
    assert b"output.path" not in serialize_cache_key_material(without_output_path)


def test_unicode_normalized_dict_key_collision_is_rejected():
    with pytest.raises(CacheKeyContractError, match="collide"):
        ConfigFingerprint("invalid.object", {"é": 1, "e\u0301": 2})


@pytest.mark.parametrize("digest", ["a" * 63, "a" * 65, "g" * 64, " a" * 32])
def test_invalid_sha256_digests_are_rejected(digest):
    with pytest.raises(CacheKeyContractError, match="64 hexadecimal"):
        InputFingerprint("image", ArtifactType.SOURCE_IMAGE, digest)


def test_uppercase_input_digest_is_normalized_to_lowercase():
    fingerprint = InputFingerprint("image", ArtifactType.SOURCE_IMAGE, "A" * 64)
    assert fingerprint.digest == SHA_A


def test_cache_key_parse_validates_algorithm_digest_and_round_trips():
    derived = derive_cache_key(material())
    assert CacheKey.parse(str(derived)) == derived
    with pytest.raises(CacheKeyContractError, match="algorithm"):
        CacheKey.parse("cache-v1.0:md5:" + "a" * 64)
    with pytest.raises(CacheKeyContractError, match="lowercase"):
        CacheKey.parse("cache-v1.0:sha256:" + "A" * 64)
    with pytest.raises(CacheKeyContractError, match="lowercase"):
        CacheKey.parse("cache-v1.0:sha256:short")


def test_cache_key_parse_requires_and_preserves_supported_contract_version():
    digest = "a" * 64
    parsed = CacheKey.parse(f"cache-v1.0:sha256:{digest}")
    assert parsed.contract_version == "1.0"
    assert str(parsed) == f"cache-v1.0:sha256:{digest}"
    with pytest.raises(CacheKeyContractError, match="contract version"):
        CacheKey.parse(f"cache-v9.0:sha256:{digest}")
    with pytest.raises(CacheKeyContractError, match="missing"):
        CacheKey.parse(f"version-1.0:sha256:{digest}")
    with pytest.raises(CacheKeyContractError, match="format"):
        CacheKey.parse(f"sha256:{digest}")


def test_same_digest_with_different_contract_versions_remains_distinct():
    digest = "a" * 64
    current = CacheKey(digest, contract_version="1.0")
    future = CacheKey(digest, contract_version="1.1")
    assert current != future
    assert str(current) != str(future)


@pytest.mark.parametrize("logical_name", [
    "/absolute/image.jpg", "C:\\images\\image.jpg", "\\\\server\\share\\image.jpg",
    "images/../secret.jpg",
])
def test_unsafe_logical_names_are_rejected(logical_name):
    with pytest.raises(CacheKeyContractError):
        InputFingerprint("image", ArtifactType.SOURCE_IMAGE, SHA_A, logical_name)


def test_config_paths_are_logical_unique_and_sorted():
    first = ConfigFingerprint("z.value", 1)
    second = ConfigFingerprint("a.value", 2)
    value = material(config_fingerprints=(first, second))
    assert [item.path for item in value.config_fingerprints] == ["a.value", "z.value"]
    with pytest.raises(CacheKeyContractError, match="unique"):
        material(config_fingerprints=(first, ConfigFingerprint("z.value", 2)))


@pytest.mark.parametrize("path", ["/config/value", "C:\\config\\value", ".hidden", "a..b"])
def test_unsafe_config_paths_are_rejected(path):
    with pytest.raises(CacheKeyContractError):
        ConfigFingerprint(path, 1)


def test_group_key_is_stable_and_preproduction_only():
    assert key(audio_group()) == key(audio_group())
    payload = serialize_cache_key_material(audio_group())
    assert b"payload" not in payload and b"runtime_location" not in payload


def test_audio_group_requires_complete_unique_non_overlapping_specification():
    with pytest.raises(ArtifactContractError):
        ArtifactGroup(
            ArtifactGroupType.AUDIO_PLAN_BUNDLE,
            "audio-plan-bundle",
            (ArtifactType.AUDIO_PLAN,),
        )
    with pytest.raises(ArtifactContractError):
        ArtifactGroup(
            ArtifactGroupType.AUDIO_PLAN_BUNDLE,
            "audio-plan-bundle",
            (ArtifactType.AUDIO_PLAN, ArtifactType.AUDIO_DIAGNOSTICS, ArtifactType.AUDIO_PLAN),
        )
    with pytest.raises(ArtifactContractError):
        ArtifactGroup(
            ArtifactGroupType.AUDIO_PLAN_BUNDLE,
            "audio-plan-bundle",
            (ArtifactType.AUDIO_PLAN, ArtifactType.AUDIO_DIAGNOSTICS),
            (ArtifactType.AUDIO_PLAN,),
        )


def test_physical_presence_and_integrity_do_not_affect_preproduction_key():
    present = artifact(presence=PresenceStatus.PRESENT, integrity=IntegrityStatus.COMPLETE)
    missing = artifact(presence=PresenceStatus.MISSING, integrity=IntegrityStatus.UNKNOWN)
    assert key(material(artifact=present)) == key(material(artifact=missing))


def test_runtime_fingerprint_library_order_is_canonical():
    first = RuntimeFingerprint(library_versions={"opencv": "4.10", "pillow": "12"})
    second = RuntimeFingerprint(library_versions={"pillow": "12", "opencv": "4.10"})
    assert first == second
    assert key(material(runtime_fingerprint=first)) == key(material(runtime_fingerprint=second))


def test_runtime_library_names_cannot_collide_after_unicode_normalization():
    with pytest.raises(CacheKeyContractError, match="unique"):
        RuntimeFingerprint(library_versions={"Café": "1", "Cafe\u0301": "2"})


@pytest.mark.parametrize("field", [
    "backend_name", "backend_version", "model_name", "model_revision",
    "platform_profile", "algorithm_profile",
])
def test_empty_runtime_fields_are_rejected(field):
    with pytest.raises(CacheKeyContractError, match="non-empty"):
        RuntimeFingerprint(**{field: ""})


def test_optional_group_members_are_part_of_expected_output_structure():
    base = audio_group()
    with_optional_output = ArtifactGroup(
        ArtifactGroupType.AUDIO_PLAN_BUNDLE,
        "audio-plan-bundle",
        (ArtifactType.AUDIO_PLAN, ArtifactType.AUDIO_DIAGNOSTICS),
        (ArtifactType.RUN_LOG,),
    )
    assert key(base) != key(audio_group(group=with_optional_output))


def test_cache_key_operations_do_not_read_write_or_inspect_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(builtins, "open", lambda *args, **kwargs: pytest.fail("must not read files"))
    monkeypatch.setattr(os, "getenv", lambda *args, **kwargs: pytest.fail("must not read env"))
    before = tuple(tmp_path.iterdir())
    assert key(material())
    assert tuple(tmp_path.iterdir()) == before == ()


def test_callers_dicts_and_lists_are_not_mutated():
    config_value = {"items": ["one", {"nested": "värde"}]}
    libraries = {"opencv": "4.10"}
    original_config = deepcopy(config_value)
    original_libraries = deepcopy(libraries)
    config = ConfigFingerprint("semantic.rules", config_value)
    runtime = RuntimeFingerprint(library_versions=libraries)
    config_value["items"].append("changed")
    libraries["opencv"] = "changed"
    assert config.value == original_config
    assert dict(runtime.library_versions) == original_libraries


def test_cache_key_material_rejects_mutable_dependency_collections():
    with pytest.raises(CacheKeyContractError, match="immutable tuple"):
        material(input_fingerprints=[])
    with pytest.raises(CacheKeyContractError, match="immutable tuple"):
        material(config_fingerprints=[])


def test_canonical_material_contains_no_physical_file_metadata():
    payload = json.loads(serialize_cache_key_material(material()))
    serialized = json.dumps(payload)
    for excluded in (
        "mtime", "atime", "inode", "file_size", "runtime_location", "presence", "integrity",
    ):
        assert excluded not in serialized


def test_very_large_integers_are_preserved_deterministically():
    value = 10 ** 200
    config = ConfigFingerprint("numeric.large_integer", value)
    assert config.value == value
    assert serialize_cache_key_material(material(config_fingerprints=(config,))) == (
        serialize_cache_key_material(material(config_fingerprints=(config,)))
    )
