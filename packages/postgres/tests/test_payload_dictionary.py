"""Raw payload dictionaries preserve occurrences without expanding repeated text."""

from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from omnipath_core.schema import PAYLOAD_SCHEMA
from omnipath_postgres.compatibility.record_layout import projection
from test_payload_validation_cache import count_validation


def dictionary(indices, texts):
    return pa.DictionaryArray.from_arrays(pa.array(indices, type=pa.int32()), pa.array(texts))


def batch(raw, *, prefix="owner", malformed=False):
    size = len(raw)
    return pa.RecordBatch.from_arrays(
        [
            pa.array([None if malformed else f"{prefix}:{index}" for index in range(size)]),
            pa.array([None] * size, type=pa.string()),
            pa.array([None if index % 2 else "source" for index in range(size)]),
            pa.array(["" if index % 2 else "0001" for index in range(size)]),
            raw,
        ],
        names=["entity_key", "relation_key", "source", "row_id", "payload_json"],
    )


def install_reader(monkeypatch, groups, *, raw_type=None, missing=False, fail_decoder=False):
    events = []
    raw_type = pa.dictionary(pa.int32(), pa.string()) if raw_type is None else raw_type
    fields = [field for field in PAYLOAD_SCHEMA if field.name != "payload_json"]
    if not missing:
        fields.append(pa.field("payload_json", raw_type))

    class Parquet:
        metadata = SimpleNamespace(num_row_groups=len(groups))
        schema_arrow = pa.schema(fields)

        def __init__(self, _path, **kwargs):
            assert kwargs == {
                "memory_map": False,
                "pre_buffer": False,
                "buffer_size": 65536,
                "read_dictionary": ["payload_json"],
            }

        def iter_batches(self, *, row_groups, batch_size, use_threads):
            assert use_threads is False
            assert batch_size == (1024 if pa.types.is_dictionary(raw_type) else 64)
            group = row_groups[0]
            events.append(("open", group))
            if fail_decoder:
                raise RuntimeError("decoder construction failed")

            def rows():
                try:
                    yield from groups[group]
                finally:
                    events.append(("closed", group))

            return rows()

        def close(self):
            events.append("file_closed")

    monkeypatch.setattr(projection.pq, "ParquetFile", Parquet)
    return events


def test_equal_native_dictionary_copies_reuse_validation_for_every_owner(tmp_path, monkeypatch):
    text = '{ "value": "α" }\n'
    first = dictionary([0] * 20, [text])
    second = dictionary([0] * 20, [text])
    assert first.dictionary.buffers()[2].address != second.dictionary.buffers()[2].address
    events = install_reader(
        monkeypatch, [[batch(first, prefix="first"), batch(second, prefix="second")]]
    )
    parsed, hashed, original_sha256 = count_validation(monkeypatch)
    references = list(projection.iter_validated_payloads(tmp_path))
    assert parsed == {text: 1}
    assert hashed == {text.encode(): 1}
    assert [row.ordinal for row in references] == list(range(40))
    assert [row.owner_key for row in references] == [
        f"{prefix}:{index}" for prefix in ("first", "second") for index in range(20)
    ]
    assert [row.source for row in references] == ["source", None] * 20
    assert [row.row_id for row in references] == ["0001", ""] * 20
    assert {row.source_record_sha256 for row in references} == {
        original_sha256(text.encode()).hexdigest()
    }
    assert {row.source_record_type for row in references} == {"object"}
    assert events == [("open", 0), ("closed", 0), "file_closed"]


def test_reused_indices_after_dictionary_change_or_group_boundary_do_not_reuse_metadata(
    tmp_path, monkeypatch
):
    # Equal JSON text in the next group is validated again; changed text at the
    # same index must immediately change its exact digest and parsed shape.
    groups = [
        [batch(dictionary([0], ["{}"])), batch(dictionary([0], ["[]"]))],
        [batch(dictionary([0], ["[]"])), batch(dictionary([0], ["null"]))],
    ]
    install_reader(monkeypatch, groups)
    parsed, hashed, original_sha256 = count_validation(monkeypatch)
    references = list(projection.iter_validated_payloads(tmp_path))
    assert parsed == {"{}": 1, "[]": 2, "null": 1}
    assert hashed == {b"{}": 1, b"[]": 2, b"null": 1}
    assert [row.source_record_type for row in references] == ["object", "array", "array", "scalar"]
    assert [row.source_record_sha256 for row in references] == [
        original_sha256(text.encode()).hexdigest() for text in ("{}", "[]", "[]", "null")
    ]


def test_null_indices_null_dictionary_values_and_literal_json_null_are_distinct(
    tmp_path, monkeypatch
):
    install_reader(
        monkeypatch,
        [[batch(dictionary([None, 0, 1, 2, 3, 4, 0], [None, "null", "false", "[]", "{}"]))]],
    )
    parsed, _, _ = count_validation(monkeypatch)
    references = list(projection.iter_validated_payloads(tmp_path))
    assert parsed == {"null": 1, "false": 1, "[]": 1, "{}": 1}
    assert [row.source_record_type for row in references] == [
        None,
        None,
        "scalar",
        "scalar",
        "array",
        "object",
        None,
    ]
    assert [row.source_record_sha256 is None for row in references] == [
        True,
        True,
        False,
        False,
        False,
        False,
        True,
    ]


@pytest.mark.parametrize("text", ["{broken", '{"v":NaN}', '{"v":Infinity}', '{"v":1e999}'])
def test_invalid_dictionary_text_fails_at_its_first_referenced_ordinal_and_closes(
    tmp_path, monkeypatch, text
):
    # The unused invalid dictionary value is not checked before its row occurs.
    events = install_reader(monkeypatch, [[batch(dictionary([0, 0, 1, 1], ["{}", text]))]])
    parsed, _, _ = count_validation(monkeypatch)
    stream = projection.iter_validated_payloads(tmp_path)
    assert next(stream).ordinal == 0
    assert next(stream).ordinal == 1
    with pytest.raises(ValueError, match="Invalid payload_json at payload 2"):
        next(stream)
    assert parsed == {"{}": 1, text: 1}
    assert events == [("open", 0), ("closed", 0), "file_closed"]


def test_owner_integrity_is_checked_before_cached_dictionary_lookup(tmp_path, monkeypatch):
    events = install_reader(
        monkeypatch,
        [[batch(dictionary([0], ["{}"])), batch(dictionary([0], ["{}"]), malformed=True)]],
    )
    parsed, _, _ = count_validation(monkeypatch)
    stream = projection.iter_validated_payloads(tmp_path)
    assert next(stream).ordinal == 0
    with pytest.raises(ValueError, match="Payload 1 must refer to exactly one"):
        next(stream)
    assert parsed == {"{}": 1}
    assert events == [("open", 0), ("closed", 0), "file_closed"]


def test_plain_and_null_column_fallback_uses_small_decoder_batches(tmp_path, monkeypatch):
    events = install_reader(
        monkeypatch,
        [[batch(pa.array(["[]", "[]", None], type=pa.string()))], [batch(pa.nulls(2))]],
        raw_type=pa.string(),
    )
    parsed, _, _ = count_validation(monkeypatch)
    references = list(projection.iter_validated_payloads(tmp_path))
    assert parsed == {"[]": 1}
    assert [row.source_record_type for row in references] == ["array", "array", None, None, None]
    assert events == [("open", 0), ("closed", 0), ("open", 1), ("closed", 1), "file_closed"]


def test_missing_raw_column_fails_explicitly_and_closes_file(tmp_path, monkeypatch):
    events = install_reader(monkeypatch, [[]], missing=True)
    with pytest.raises(ValueError, match="missing the payload_json column"):
        next(projection.iter_validated_payloads(tmp_path))
    assert events == ["file_closed"]


def test_decoder_construction_failure_closes_raw_file(tmp_path, monkeypatch):
    events = install_reader(monkeypatch, [[]], fail_decoder=True)
    with pytest.raises(RuntimeError, match="decoder construction failed"):
        next(projection.iter_validated_payloads(tmp_path))
    assert events == [("open", 0), "file_closed"]


def test_early_close_releases_decoder_and_file(tmp_path, monkeypatch):
    events = install_reader(monkeypatch, [[batch(dictionary([0] * 2, ["{}"]))]])
    stream = projection.iter_validated_payloads(tmp_path)
    next(stream)
    stream.close()
    assert events == [("open", 0), ("closed", 0), "file_closed"]


def test_dictionary_index_memo_remains_bounded_and_evicts_lru(monkeypatch):
    monkeypatch.setattr(projection, "_MAX_PAYLOAD_CACHE_ENTRIES", 2)
    parsed, _, _ = count_validation(monkeypatch)
    cache = projection._DictionaryPayloadValidationCache(pa.array(["{}", "[]", "null"]))
    for ordinal, index in enumerate([0, 1, 0, 2, 1]):
        cache.validate(index, ordinal)
        assert len(cache.entries) <= 2
    assert parsed == {"{}": 1, "[]": 2, "null": 1}
    assert list(cache.entries) == [2, 1]
    assert all(isinstance(index, int) for index in cache.entries)
    assert all(len(value) == 2 for value in cache.entries.values())


def test_real_large_repeated_text_stays_dictionary_encoded_and_validates_once(
    tmp_path, monkeypatch
):
    text = '{"content":"' + "x" * (2 * 1024 * 1024) + '"}'
    count = 1024
    table = pa.Table.from_batches([batch(dictionary([0] * count, [text]))])
    path = tmp_path / "evidence_payloads.parquet"
    pq.write_table(
        table, path, store_schema=False, compression="zstd", use_dictionary=["payload_json"]
    )
    assert pq.ParquetFile(path).schema_arrow.field("payload_json").type == pa.string()
    stream = projection._iter_payload_rows(path, batch_size=1024)
    try:
        row = next(stream)
        value = row["payload_json"]
        assert isinstance(value, projection._DictionaryPayloadValue)
        assert len(value.cache.dictionary) == 1
        assert value.cache.dictionary.nbytes < len(text) + 16
    finally:
        stream.close()
    parsed, hashed, original_sha256 = count_validation(monkeypatch)
    references = list(projection.iter_validated_payloads(tmp_path))
    assert parsed == {text: 1}
    assert hashed == {text.encode(): 1}
    assert len(references) == count
    assert [row.ordinal for row in references] == list(range(count))
    assert {row.source_record_sha256 for row in references} == {
        original_sha256(text.encode()).hexdigest()
    }
