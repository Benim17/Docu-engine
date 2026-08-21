from __future__ import annotations

import hashlib
from dataclasses import FrozenInstanceError, replace

import pytest

from engine.source_understanding import CanonicalSourceIdentity, SourceKind, SourceObservationIdentity
from engine.source_understanding._evidence_validation import canonical_json_bytes
from engine.source_understanding.source_evidence import (
    SI02InvalidFieldError,
    SI02InvariantError,
    SI02MalformedDataError,
    SI02SizeLimitError,
    SourceAcquisitionMethod,
    SourceAcquisitionProvenance,
    SourceAcquisitionProvenanceRole,
)


def digest(character):
    return "sha256:" + character * 64


def identity(value="AbCdEf_12-3"):
    return CanonicalSourceIdentity(1, SourceKind.YOUTUBE_VIDEO, "youtube-video-id", 1, value)


def observation(source=None):
    source = source or identity()
    return SourceObservationIdentity(1, source, "opaque", 1, "observation")


def provenance(**changes):
    source = changes.pop("source_identity", identity())
    values = {
        "schema_version": 1,
        "source_identity": source,
        "observation_identity": observation(source),
        "role": SourceAcquisitionProvenanceRole.METADATA,
        "adapter_id": "source.metadata",
        "adapter_version": "1.0.0",
        "acquisition_method": SourceAcquisitionMethod.REPLAY,
        "evidence_digest": digest("e"),
        "parent_refs": (),
    }
    supplied_id = changes.pop("provenance_id", None)
    values.update(changes)
    preimage = {
        "acquisition_method": values["acquisition_method"].value if hasattr(values["acquisition_method"], "value") else values["acquisition_method"],
        "adapter_id": values["adapter_id"],
        "adapter_version": values["adapter_version"],
        "evidence_digest": values["evidence_digest"],
        "observation_identity": values["observation_identity"].to_dict(),
        "parent_refs": list(values["parent_refs"]),
        "role": values["role"].value if hasattr(values["role"], "value") else values["role"],
        "schema_version": values["schema_version"],
        "source_identity": values["source_identity"].to_dict(),
    }
    values["provenance_id"] = supplied_id or (
        "sha256:" + hashlib.sha256(canonical_json_bytes(preimage)).hexdigest()
    )
    return SourceAcquisitionProvenance(**values)


def test_provenance_exact_preimage_round_trip_immutability_and_hashing():
    value = provenance()
    assert "provenance_id" not in value.identity_preimage()
    assert set(value.to_dict()) == set(value.identity_preimage()) | {"provenance_id"}
    assert SourceAcquisitionProvenance.from_json(value.canonical_bytes()) == value
    assert value == replace(value) and hash(value) == hash(replace(value))
    with pytest.raises(FrozenInstanceError):
        value.adapter_id = "changed"


def test_provenance_id_must_match_exact_canonical_preimage():
    with pytest.raises(SI02InvariantError):
        provenance(provenance_id=digest("f"))
    original = provenance()
    changed = provenance(adapter_version="1.0.1")
    assert original.provenance_id != changed.provenance_id


@pytest.mark.parametrize("changes", [
    {"role": "FUTURE"}, {"adapter_id": "Upper"}, {"adapter_id": "a-"},
    {"adapter_version": ""}, {"adapter_version": "version with space"},
    {"acquisition_method": "NETWORK"}, {"parent_refs": []},
])
def test_intrinsic_field_and_tuple_boundaries(changes):
    with pytest.raises(SI02InvalidFieldError):
        provenance(**changes)


def test_parent_refs_must_be_unique_and_ascii_sorted():
    with pytest.raises(SI02InvariantError):
        provenance(parent_refs=(digest("2"), digest("1")))
    with pytest.raises(SI02InvariantError):
        provenance(parent_refs=(digest("1"), digest("1")))
    assert provenance(parent_refs=(digest("1"), digest("2"))).parent_refs == (
        digest("1"), digest("2")
    )


def test_component_parent_limit_16_and_aggregate_limit_34():
    component_parents = tuple(
        "sha256:" + f"{index:064x}" for index in range(16)
    )
    assert len(provenance(parent_refs=component_parents).parent_refs) == 16
    with pytest.raises(SI02InvariantError):
        provenance(parent_refs=component_parents + ("sha256:" + f"{16:064x}",))

    aggregate_parents = tuple(
        "sha256:" + f"{index:064x}" for index in range(34)
    )
    assert len(provenance(role=SourceAcquisitionProvenanceRole.AGGREGATE,
                          parent_refs=aggregate_parents).parent_refs) == 34
    with pytest.raises(SI02InvariantError):
        provenance(role=SourceAcquisitionProvenanceRole.AGGREGATE,
                   parent_refs=aggregate_parents + ("sha256:" + f"{34:064x}",))


def test_self_reference_is_rejected():
    base = provenance()
    with pytest.raises(SI02InvariantError):
        provenance(provenance_id=base.provenance_id, parent_refs=(base.provenance_id,))


def test_identity_agreement_is_intrinsic():
    with pytest.raises(SI02InvariantError):
        provenance(observation_identity=observation(identity("ZbCdEf_12-3")))


def test_serialized_unknown_null_and_nonarray_parents_are_rejected():
    value = provenance().to_dict()
    mutated = dict(value); mutated["path"] = "/tmp/evidence"
    with pytest.raises(SI02MalformedDataError):
        SourceAcquisitionProvenance.from_dict(mutated)
    mutated = dict(value); mutated["adapter_id"] = None
    with pytest.raises(SI02MalformedDataError):
        SourceAcquisitionProvenance.from_dict(mutated)
    mutated = dict(value); mutated["parent_refs"] = "not-an-array"
    with pytest.raises(SI02MalformedDataError):
        SourceAcquisitionProvenance.from_dict(mutated)


def test_8_kib_preparse_gate():
    with pytest.raises(SI02MalformedDataError):
        SourceAcquisitionProvenance.from_json(b"x" * 8192)
    with pytest.raises(SI02SizeLimitError):
        SourceAcquisitionProvenance.from_json(b"x" * 8193)
