from __future__ import annotations

import builtins
import json
import subprocess
import sys
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

import engine.source_understanding as source_understanding
from engine.source_understanding import (
    CanonicalSourceIdentity,
    SI01InvalidFieldError,
    SI01InvalidYouTubeReferenceError,
    SI01MalformedDataError,
    SI01SizeLimitError,
    SI01UnsupportedVersionError,
    SourceKind,
    SourceObservationIdentity,
    SourceReference,
    canonicalize_youtube_reference,
)


VIDEO_ID = "AbCdEf_12-3"


def test_package_all_is_exactly_the_approved_si01_api():
    assert set(source_understanding.__all__) == {
        "SourceKind",
        "SourceReference",
        "CanonicalSourceIdentity",
        "SourceObservationIdentity",
        "canonicalize_youtube_reference",
        "SI01Error",
        "SI01MalformedDataError",
        "SI01UnsupportedVersionError",
        "SI01SizeLimitError",
        "SI01InvalidFieldError",
        "SI01InvalidYouTubeReferenceError",
    }
    assert len(source_understanding.__all__) == 11


def source_reference(**changes):
    values = {
        "schema_version": 1,
        "source_kind": SourceKind.YOUTUBE_VIDEO,
        "reference_value": f"https://youtu.be/{VIDEO_ID}",
        "display_label": "Räv",
    }
    values.update(changes)
    return SourceReference(**values)


def source_identity(**changes):
    values = {
        "schema_version": 1,
        "source_kind": SourceKind.YOUTUBE_VIDEO,
        "identity_scheme": "youtube-video-id",
        "identity_scheme_version": 1,
        "canonical_value": VIDEO_ID,
    }
    values.update(changes)
    return CanonicalSourceIdentity(**values)


def observation_identity(**changes):
    values = {
        "schema_version": 1,
        "source_identity": source_identity(),
        "observation_scheme": "opaque-future-scheme",
        "observation_scheme_version": 1,
        "observation_value": "opaque-value",
    }
    values.update(changes)
    return SourceObservationIdentity(**values)


def test_source_kind_is_the_exact_closed_v1_vocabulary():
    assert {item.value for item in SourceKind} == {
        "YOUTUBE_VIDEO", "WEB_PAGE", "PDF_DOCUMENT", "TEXT", "AUDIO_FILE", "VIDEO_FILE"
    }
    with pytest.raises(ValueError):
        SourceKind("UNKNOWN")
    with pytest.raises(SI01InvalidFieldError):
        source_reference(source_kind="UNKNOWN")


@pytest.mark.parametrize("value", [source_reference(), source_identity(), observation_identity()])
def test_models_are_frozen_structurally_equal_and_hashable(value):
    assert value == replace(value)
    assert hash(value) == hash(replace(value))
    with pytest.raises(FrozenInstanceError):
        value.schema_version = 2


def test_display_label_affects_reference_value_semantics_but_not_derived_identity():
    first = source_reference(display_label="First")
    second = source_reference(display_label="Second")
    assert first != second and hash(first) != hash(second)
    assert canonicalize_youtube_reference(first) == canonicalize_youtube_reference(second)


def test_optional_display_label_is_omitted_and_null_is_rejected():
    value = source_reference(display_label=None)
    assert "display_label" not in value.to_dict()
    payload = value.canonical_bytes()[:-1] + b',"display_label":null}'
    with pytest.raises(SI01MalformedDataError):
        SourceReference.from_json(payload)
    direct = value.to_dict()
    direct["display_label"] = None
    with pytest.raises(SI01MalformedDataError):
        SourceReference.from_dict(direct)


@pytest.mark.parametrize("factory", [source_reference, source_identity, observation_identity])
@pytest.mark.parametrize("version", [0, 2, -1])
def test_unsupported_integer_schema_versions_are_distinct(factory, version):
    with pytest.raises(SI01UnsupportedVersionError):
        factory(schema_version=version)


@pytest.mark.parametrize("factory", [source_reference, source_identity, observation_identity])
@pytest.mark.parametrize("version", [True, False, "1", 1.0])
def test_invalid_schema_version_types_are_field_errors_for_direct_construction(
    factory, version
):
    with pytest.raises(SI01InvalidFieldError):
        factory(schema_version=version)


@pytest.mark.parametrize("factory", [source_reference, source_identity, observation_identity])
def test_integer_schema_version_one_is_accepted(factory):
    assert factory(schema_version=1).schema_version == 1


def test_serialized_float_schema_version_remains_malformed_json():
    payload = b'{"reference_value":"x","schema_version":1.0,"source_kind":"TEXT"}'
    with pytest.raises(SI01MalformedDataError):
        SourceReference.from_json(payload)


@pytest.mark.parametrize("factory,changes", [
    (source_reference, {"reference_value": ""}),
    (source_reference, {"reference_value": "x" * 4097}),
    (source_reference, {"display_label": "x" * 513}),
    (source_identity, {"identity_scheme": "å"}),
    (source_identity, {"identity_scheme": "x" * 65}),
    (source_identity, {"identity_scheme_version": 0}),
    (source_identity, {"identity_scheme_version": True}),
    (source_identity, {"canonical_value": "å"}),
    (observation_identity, {"observation_scheme": "å"}),
    (observation_identity, {"observation_scheme_version": False}),
    (observation_identity, {"observation_value": "x" * 257}),
    (observation_identity, {"source_identity": "not-an-identity"}),
])
def test_field_type_ascii_and_integer_boundaries(factory, changes):
    with pytest.raises(SI01InvalidFieldError):
        factory(**changes)


@pytest.mark.parametrize("value", [source_reference(), source_identity(), observation_identity()])
def test_canonical_json_is_utf8_compact_sorted_and_round_trips(value):
    encoded = value.canonical_bytes()
    assert encoded == encoded.strip()
    assert not encoded.endswith(b"\n")
    assert b" " not in encoded
    assert list(json.loads(encoded)) == sorted(json.loads(encoded))
    assert type(value).from_json(encoded) == value


def test_unicode_is_normalized_to_nfc_before_equality_hash_and_serialization():
    decomposed = source_reference(reference_value="Cafe\u0301", display_label="Ra\u0308v")
    composed = source_reference(reference_value="Café", display_label="Räv")
    assert decomposed == composed
    assert hash(decomposed) == hash(composed)
    assert b"Caf\xc3\xa9" in decomposed.canonical_bytes()
    assert b"R\xc3\xa4v" in decomposed.canonical_bytes()


@pytest.mark.parametrize("mutated", [
    b' {"reference_value":"x","schema_version":1,"source_kind":"TEXT"}',
    b'{"reference_value": "x","schema_version":1,"source_kind":"TEXT"}',
    b'{"reference_value":"x","schema_version":1,"source_kind":"TEXT"}\n',
    b'\xef\xbb\xbf{"reference_value":"x","schema_version":1,"source_kind":"TEXT"}',
    b'{"reference_value":"x","reference_value":"y","schema_version":1,"source_kind":"TEXT"}',
    b'{"future":1,"reference_value":"x","schema_version":1,"source_kind":"TEXT"}',
    b'{"schema_version":1,"source_kind":"TEXT"}',
    b'{"reference_value":null,"schema_version":1,"source_kind":"TEXT"}',
    b'{"reference_value":"x","schema_version":1.0,"source_kind":"TEXT"}',
    b'{"reference_value":"x","schema_version":NaN,"source_kind":"TEXT"}',
    b'{"reference_value":"x","schema_version":1,"source_kind":"TEXT"}x',
    b'\xff',
])
def test_malformed_noncanonical_duplicate_unknown_missing_and_numeric_json(mutated):
    with pytest.raises(SI01MalformedDataError):
        SourceReference.from_json(mutated)


def test_serialized_decomposed_and_escaped_non_ascii_forms_are_rejected():
    decomposed = b'{"reference_value":"Cafe\xcc\x81","schema_version":1,"source_kind":"TEXT"}'
    escaped = b'{"display_label":"R\\u00e4v","reference_value":"x","schema_version":1,"source_kind":"TEXT"}'
    for payload in (decomposed, escaped):
        with pytest.raises(SI01MalformedDataError):
            SourceReference.from_json(payload)


@pytest.mark.parametrize(("model", "limit"), [
    (SourceReference, 5120),
    (CanonicalSourceIdentity, 1024),
    (SourceObservationIdentity, 1536),
])
def test_exact_serialized_size_passes_gate_and_one_over_fails_before_parsing(model, limit):
    with pytest.raises(SI01MalformedDataError):
        model.from_json(b"x" * limit)
    with pytest.raises(SI01SizeLimitError):
        model.from_json(b"x" * (limit + 1))


@pytest.mark.parametrize("url", [
    f"https://youtube.com/watch?v={VIDEO_ID}",
    f"HTTPS://YOUTUBE.COM/watch?v={VIDEO_ID}",
    f"https://www.youtube.com:443/watch?v={VIDEO_ID}",
    f"https://youtube.com/shorts/{VIDEO_ID}",
    f"https://www.youtube.com/shorts/{VIDEO_ID}",
    f"https://youtu.be/{VIDEO_ID}",
    f"https://YOUTU.BE:443/{VIDEO_ID}",
])
def test_all_accepted_youtube_host_and_path_forms(url):
    assert canonicalize_youtube_reference(url) == source_identity()


@pytest.mark.parametrize("suffix", [
    "&t=0", "&t=86400000", "&start=00000001", "&si=value", "&feature=share",
    "&list=PL123", "&index=2", "&si=caf%C3%A9", "&feature=a+b",
])
def test_watch_ignored_query_values_are_validated_but_do_not_change_identity(suffix):
    base = f"https://youtube.com/watch?v={VIDEO_ID}"
    assert canonicalize_youtube_reference(base + suffix) == canonicalize_youtube_reference(base)


@pytest.mark.parametrize("suffix", ["#t=0", "#t=86400000", "#t=12s", "?t=1#t=2s"])
def test_valid_fragments_and_independent_query_time_do_not_change_identity(suffix):
    base = f"https://youtu.be/{VIDEO_ID}"
    assert canonicalize_youtube_reference(base + suffix) == source_identity()


def test_playlist_context_never_changes_individual_video_identity():
    plain = canonicalize_youtube_reference(f"https://youtu.be/{VIDEO_ID}")
    contextual = canonicalize_youtube_reference(
        f"https://youtube.com/watch?list=PL123&index=4&v={VIDEO_ID}"
    )
    assert contextual == plain
    assert contextual.canonical_value == VIDEO_ID


@pytest.mark.parametrize("url", [
    f"http://youtube.com/watch?v={VIDEO_ID}",
    f"ftp://youtube.com/watch?v={VIDEO_ID}",
    f"https://user@youtube.com/watch?v={VIDEO_ID}",
    f"https://youtube.com./watch?v={VIDEO_ID}",
    f"https://xn--youtube-9za.com/watch?v={VIDEO_ID}",
    f"https://example.com/watch?v={VIDEO_ID}",
    f"https://youtube.com:0443/watch?v={VIDEO_ID}",
    f"https://youtube.com:444/watch?v={VIDEO_ID}",
    f"https://youtube.com/embed/{VIDEO_ID}",
    f"https://youtube.com/live/{VIDEO_ID}",
    f"https://youtube.com/channel/{VIDEO_ID}",
    f"https://youtube.com/playlist?list={VIDEO_ID}",
    f"https://youtube.com/watch/?v={VIDEO_ID}",
    f"https://youtube.com//watch?v={VIDEO_ID}",
    f"https://youtube.com/shorts/{VIDEO_ID}/",
    f"https://youtu.be/{VIDEO_ID}/extra",
    "https://youtu.be/abcdefghij",
    "https://youtu.be/abcdefghijkl",
    "https://youtu.be/abcdefghij!",
])
def test_invalid_scheme_authority_path_and_id_forms_are_rejected(url):
    with pytest.raises(SI01InvalidYouTubeReferenceError):
        canonicalize_youtube_reference(url)


@pytest.mark.parametrize("url", [
    f" https://youtu.be/{VIDEO_ID}",
    f"\x00https://youtu.be/{VIDEO_ID}",
    f"https://you\ttube.com/watch?v={VIDEO_ID}",
    f"https://youtube.com\r/watch?v={VIDEO_ID}",
    f"https://youtube.com/watch\r?v={VIDEO_ID}",
    f"https://youtube.com/watch?v={VIDEO_ID}\r&si=value",
    "https://youtu.be/AbCd\nEf_12-3",
])
def test_raw_controls_and_leading_space_are_rejected_before_urlsplit(url, monkeypatch):
    def forbidden_urlsplit(_reference):
        raise AssertionError("urlsplit must not receive lexically invalid raw input")

    monkeypatch.setattr(
        "engine.source_understanding.source_identity.urlsplit", forbidden_urlsplit
    )
    with pytest.raises(SI01InvalidYouTubeReferenceError):
        canonicalize_youtube_reference(url)


@pytest.mark.parametrize("query", [
    "", "v=", f"v={VIDEO_ID}&v={VIDEO_ID}", f"v={VIDEO_ID}&&t=1",
    f"v={VIDEO_ID}&flag", f"v={VIDEO_ID}&a=b=c", f"v={VIDEO_ID}&=x",
    f"%76={VIDEO_ID}", f"v={VIDEO_ID}&unknown=x", f"v={VIDEO_ID}&t=",
    f"v={VIDEO_ID}&t=86400001", f"v={VIDEO_ID}&t=12s", f"v={VIDEO_ID}&t=+1",
    f"v={VIDEO_ID}&si=" + "x" * 513, f"v={VIDEO_ID}&si=%26",
    f"v={VIDEO_ID}&si=%", f"v={VIDEO_ID}&si=%FF", f"v={VIDEO_ID}&si=x&%73i=y",
    f"v=AbCdEf%5F12-3", f"v={VIDEO_ID}%2526",
])
def test_raw_watch_query_tokenization_decoding_allowlist_and_bounds(query):
    with pytest.raises(SI01InvalidYouTubeReferenceError):
        canonicalize_youtube_reference(f"https://youtube.com/watch?{query}")


@pytest.mark.parametrize("query", [
    f"v={VIDEO_ID}", "unknown=x", "list=", "list=x&list=y", "t=86400001", "t=1s"
])
def test_short_and_youtu_be_queries_forbid_v_unknown_duplicates_blank_and_bad_time(query):
    with pytest.raises(SI01InvalidYouTubeReferenceError):
        canonicalize_youtube_reference(f"https://youtu.be/{VIDEO_ID}?{query}")


@pytest.mark.parametrize("fragment", ["", "t=", "t=86400001", "t=1m", "x=1", "t%3D1", "t=1&x=2"])
def test_invalid_fragment_forms_are_rejected(fragment):
    with pytest.raises(SI01InvalidYouTubeReferenceError):
        canonicalize_youtube_reference(f"https://youtu.be/{VIDEO_ID}#{fragment}")


def test_youtube_output_is_exact_locked_identity():
    assert canonicalize_youtube_reference(f"https://youtu.be/{VIDEO_ID}").to_dict() == {
        "canonical_value": VIDEO_ID,
        "identity_scheme": "youtube-video-id",
        "identity_scheme_version": 1,
        "schema_version": 1,
        "source_kind": "YOUTUBE_VIDEO",
    }


def test_si01_execution_is_zero_network_and_zero_filesystem(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("SI-01 attempted forbidden I/O")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    import socket
    import urllib.request
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)

    reference = source_reference()
    assert SourceReference.from_json(reference.canonical_bytes()) == reference
    assert CanonicalSourceIdentity.from_json(source_identity().canonical_bytes()) == source_identity()
    assert SourceObservationIdentity.from_json(
        observation_identity().canonical_bytes()
    ) == observation_identity()
    assert canonicalize_youtube_reference(reference) == source_identity()


def test_si01_import_boundary_does_not_load_forbidden_engine_layers():
    script = r'''
import sys
import engine.source_understanding
forbidden = (
    "engine.storage", "engine.pipeline", "engine.source_ingestion", "engine.knowledge",
    "engine.story_director", "engine.audio_director", "engine.caption_director",
    "engine.visual_director",
)
loaded = sorted(
    name for name in sys.modules
    if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)
)
raise SystemExit("forbidden imports: " + ", ".join(loaded) if loaded else 0)
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
