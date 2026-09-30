"""Repeated source text is validated once without skipping occurrence integrity."""

from collections import Counter
import hashlib
import sys
import weakref

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from omnipath_core.schema import PAYLOAD_SCHEMA
from omnipath_postgres import projection


def payload(text, *, entity_key="owner", relation_key=None, source=None, row_id=None):
    return {
        "entity_key": entity_key,
        "relation_key": relation_key,
        "source": source,
        "row_id": row_id,
        "payload_json": text,
    }


def count_validation(monkeypatch):
    parsed = Counter()
    hashed = Counter()
    original_loads = projection.json.loads
    original_sha256 = projection.hashlib.sha256

    def loads(text, **kwargs):
        parsed[text] += 1
        return original_loads(text, **kwargs)

    def sha256(text):
        hashed[text] += 1
        return original_sha256(text)

    monkeypatch.setattr(projection.json, "loads", loads)
    monkeypatch.setattr(projection.hashlib, "sha256", sha256)
    return parsed, hashed, original_sha256


def charge(text):
    return sys.getsizeof(text) + projection._PAYLOAD_CACHE_ENTRY_OVERHEAD


@pytest.mark.parametrize("batch_size", [1, 1024])
def test_exact_repetitions_parse_and_hash_once_with_every_owner_preserved(
    tmp_path, monkeypatch, batch_size
):
    text = '{ "reaction": "α", "participants": [1, null, true] }\n'
    rows = [
        payload(
            text,
            entity_key=None if index % 2 else f"entity:{index}",
            relation_key=f"relation:{index}" if index % 2 else None,
            source=[None, "", "upstream"][index % 3],
            row_id=[None, "", "0001", "0002"][index % 4],
        )
        for index in range(20)
    ]
    pq.write_table(
        pa.Table.from_pylist(rows, schema=PAYLOAD_SCHEMA), tmp_path / "evidence_payloads.parquet"
    )
    parsed, hashed, original_sha256 = count_validation(monkeypatch)
    references = list(projection.iter_validated_payloads(tmp_path, batch_size=batch_size))
    digest = original_sha256(text.encode("utf-8")).hexdigest()
    assert parsed == {text: 1}
    assert hashed == {text.encode("utf-8"): 1}
    assert references == [
        projection.PayloadReference(
            index,
            "relation" if index % 2 else "entity",
            row["relation_key"] if index % 2 else row["entity_key"],
            row["source"],
            row["row_id"],
            digest,
            "object",
        )
        for index, row in enumerate(rows)
    ]


def test_cache_is_scoped_to_one_reader_call(tmp_path, monkeypatch):
    text = '{"value":42}'
    monkeypatch.setattr(projection, "iter_rows", lambda *args, **kwargs: iter([payload(text)] * 3))
    parsed, hashed, _ = count_validation(monkeypatch)
    first = list(projection.iter_validated_payloads(tmp_path))
    second = list(projection.iter_validated_payloads(tmp_path))
    assert first == second
    assert parsed == {text: 2}
    assert hashed == {text.encode(): 2}


def test_semantically_equal_json_with_different_text_has_distinct_validation_and_digest(
    tmp_path, monkeypatch
):
    texts = ['{"unicode":"α"}', '{ "unicode": "α" }\n', '{"unicode":"\\u03b1"}']
    monkeypatch.setattr(
        projection, "iter_rows", lambda *args, **kwargs: iter(payload(text) for text in texts * 2)
    )
    parsed, hashed, original_sha256 = count_validation(monkeypatch)
    references = list(projection.iter_validated_payloads(tmp_path))
    assert parsed == {text: 1 for text in texts}
    assert hashed == {text.encode(): 1 for text in texts}
    assert [row.source_record_sha256 for row in references] == [
        original_sha256(text.encode()).hexdigest() for text in texts * 2
    ]
    assert len({row.source_record_sha256 for row in references}) == 3
    assert {row.source_record_type for row in references} == {"object"}


@pytest.mark.parametrize(
    "second,error",
    [
        (payload("{}", entity_key=None), "Payload 1 must refer to exactly one"),
        (payload("{}", relation_key="relation"), "Payload 1 must refer to exactly one"),
        (payload("{}", entity_key=" "), "entity_key must be a nonempty"),
        (payload("{}", entity_key=None, relation_key=""), "relation_key must be a nonempty"),
    ],
)
def test_cache_hit_cannot_skip_owner_pointer_or_key_validation(
    tmp_path, monkeypatch, second, error
):
    monkeypatch.setattr(
        projection, "iter_rows", lambda *args, **kwargs: iter([payload("{}"), second])
    )
    parsed, _, _ = count_validation(monkeypatch)
    stream = projection.iter_validated_payloads(tmp_path)
    assert next(stream).ordinal == 0
    with pytest.raises(ValueError, match=error):
        next(stream)
    assert parsed == {"{}": 1}


@pytest.mark.parametrize("text", ["{broken", '{"v":NaN}', '{"v":Infinity}', '{"v":1e999}'])
def test_invalid_json_never_enters_cache_and_is_revalidated(text, monkeypatch):
    parsed, hashed, _ = count_validation(monkeypatch)
    cache = projection._PayloadValidationCache()
    cache.validate("{}", 0)
    before = cache.entries.copy(), cache.size_bytes
    for ordinal in (1, 2):
        with pytest.raises(ValueError, match=f"Invalid payload_json at payload {ordinal}"):
            cache.validate(text, ordinal)
    assert (cache.entries, cache.size_bytes) == before
    assert parsed[text] == 2
    assert text.encode() not in hashed


def test_entry_limit_evicts_least_recently_used_body(monkeypatch):
    parsed, _, _ = count_validation(monkeypatch)
    cache = projection._PayloadValidationCache(max_bytes=100_000, max_entries=2)
    for ordinal, text in enumerate(['{"a":1}', '{"b":1}', '{"a":1}', '{"c":1}', '{"b":1}']):
        cache.validate(text, ordinal)
        assert len(cache.entries) <= 2
        assert cache.size_bytes == sum(charge(key) for key in cache.entries)
    assert parsed == {'{"a":1}': 1, '{"b":1}': 2, '{"c":1}': 1}
    assert list(cache.entries) == ['{"c":1}', '{"b":1}']


def test_byte_limit_accounts_unicode_key_size_and_entry_overhead(monkeypatch):
    texts = ['{"a":"ascii"}', '{"b":"α"}']
    budget = sum(charge(text) for text in texts) - 1
    parsed, _, _ = count_validation(monkeypatch)
    cache = projection._PayloadValidationCache(max_bytes=budget, max_entries=256)
    for ordinal, text in enumerate([texts[0], texts[1], texts[0]]):
        cache.validate(text, ordinal)
        assert list(cache.entries) == [text]
        assert cache.size_bytes == charge(text) <= budget
    assert parsed == {texts[0]: 2, texts[1]: 1}


def test_oversized_bodies_bypass_cache_without_evicting_small_valid_text(monkeypatch):
    huge = '["' + "α" * 2048 + '"]'
    small = "{}"
    parsed, _, _ = count_validation(monkeypatch)
    cache = projection._PayloadValidationCache(max_bytes=charge(small), max_entries=2)
    for ordinal, text in enumerate([small, huge, huge, small]):
        digest, shape = cache.validate(text, ordinal)
        assert shape == ("array" if text == huge else "object")
        assert digest == hashlib.new("sha256", text.encode()).hexdigest()
    assert parsed == {huge: 2, small: 1}
    assert list(cache.entries) == [small]
    assert cache.size_bytes == charge(small)


@pytest.mark.parametrize("limits", [{"max_bytes": 0}, {"max_entries": 0}])
def test_zero_budget_disables_cache_without_changing_results(monkeypatch, limits):
    parsed, _, _ = count_validation(monkeypatch)
    cache = projection._PayloadValidationCache(**limits)
    assert cache.validate("[]", 0) == cache.validate("[]", 1)
    assert parsed == {"[]": 2}
    assert not cache.entries
    assert cache.size_bytes == 0


def test_parsed_body_is_released_before_yielding_cached_metadata(tmp_path, monkeypatch):
    class TrackedDict(dict):
        pass

    references = []
    original_loads = projection.json.loads

    def loads(text, **kwargs):
        body = TrackedDict(original_loads(text, **kwargs))
        references.append(weakref.ref(body))
        return body

    monkeypatch.setattr(projection.json, "loads", loads)
    monkeypatch.setattr(
        projection, "iter_rows", lambda *args, **kwargs: iter([payload("{}"), payload("{}")])
    )
    stream = projection.iter_validated_payloads(tmp_path)
    try:
        assert next(stream).source_record_type == "object"
        assert len(references) == 1
        assert references[0]() is None
        assert next(stream).source_record_type == "object"
        assert len(references) == 1
    finally:
        stream.close()


def test_json_shapes_and_null_raw_body_preserve_provenance(tmp_path, monkeypatch):
    texts = ["null", "true", "42", '"source"', "[]", "{}", None]
    monkeypatch.setattr(
        projection, "iter_rows", lambda *args, **kwargs: iter(payload(text) for text in texts * 2)
    )
    parsed, _, original_sha256 = count_validation(monkeypatch)
    references = list(projection.iter_validated_payloads(tmp_path))
    assert parsed == {text: 1 for text in texts if text is not None}
    expected_shapes = ["scalar"] * 4 + ["array", "object", None]
    assert [row.source_record_type for row in references] == expected_shapes * 2
    assert [row.source_record_sha256 for row in references] == [
        original_sha256(text.encode()).hexdigest() if text is not None else None
        for text in texts * 2
    ]
    assert [row.ordinal for row in references] == list(range(14))
