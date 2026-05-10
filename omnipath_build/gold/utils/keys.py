from __future__ import annotations

import hashlib


def compute_entity_key(
    canonical_identifier: str | None,
    canonical_identifier_type: str | None,
    taxonomy_id: str | None,
) -> str:
    """Stable business key for an entity.

    Deterministic hash of canonical identifier, type, and taxonomy.
    Taxonomy is included because the same identifier can refer to
    different biological entities in different taxa.
    """
    key_parts = [
        canonical_identifier or '',
        canonical_identifier_type or '',
        taxonomy_id or '',
    ]
    key_str = '|'.join(key_parts)
    return hashlib.sha256(key_str.encode('utf-8')).hexdigest()


def compute_relation_key(
    subject_entity_key: str,
    predicate: str | None,
    object_entity_key: str,
    relation_category: str | None,
) -> str:
    """Stable business key for a relation.

    Deterministic hash of the subject entity key, predicate,
    object entity key, and relation category.
    """
    key_parts = [
        subject_entity_key,
        predicate or '',
        object_entity_key,
        relation_category or '',
    ]
    key_str = '|'.join(key_parts)
    return hashlib.sha256(key_str.encode('utf-8')).hexdigest()
