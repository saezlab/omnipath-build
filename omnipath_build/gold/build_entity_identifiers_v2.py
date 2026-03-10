"""Build deterministic entity/instance identity snapshots (IEM v2).

Implements docs/iem_spec_updated.md:
- Deterministic readable entity_key
- Deterministic instance_key
- Snapshot outputs (no registry / no deltas)
"""
from __future__ import annotations

import logging
from pathlib import Path
from datetime import UTC, datetime
from dataclasses import dataclass
import re

import polars as pl

from pypath.internals.cv_terms import EntityTypeCv, IdentifierNamespaceCv
from omnipath_build.gold.build_entity_identifiers import (
    EXEMPT_ENTITY_TYPES,
    CHEMICAL_ENTITY_BUCKET,
    UNKNOWN_ENTITY_TYPE_KEY,
    MERGE_UNSAFE_IDENTIFIER_TYPES,
    MERGE_SAFE_IDENTIFIER_TYPES_BY_BUCKET,
)

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

BUCKET_CODE_BY_ENTITY_TYPE: dict[str, str] = {
    EntityTypeCv.PROTEIN.value: 'P',
    EntityTypeCv.GENE.value: 'G',
    EntityTypeCv.RNA.value: 'R',
    EntityTypeCv.DNA.value: 'D',
    EntityTypeCv.COMPLEX.value: 'C',
    EntityTypeCv.PATHWAY.value: 'PW',
    EntityTypeCv.REACTION.value: 'RXN',
    EntityTypeCv.INTERACTION.value: 'INT',
    EntityTypeCv.PROTEIN_FAMILY.value: 'PF',
    EntityTypeCv.PHYSICAL_ENTITY.value: 'PE',
    CHEMICAL_ENTITY_BUCKET: 'CH',
}

# Buckets where gene-symbol merges require tax scoping.
GENE_SYMBOL_TAX_SCOPED_BUCKETS: frozenset[str] = frozenset({'P', 'G', 'R', 'D'})

# Per-bucket anchor type priority (lower index = higher priority)
ANCHOR_PRIORITY_BY_BUCKET: dict[str, list[str]] = {
    'P': [
        IdentifierNamespaceCv.UNIPROT.value,
        IdentifierNamespaceCv.UNIPROT_TREMBL.value,
        IdentifierNamespaceCv.UNIPARC.value,
        IdentifierNamespaceCv.REFSEQ_PROTEIN.value,
        IdentifierNamespaceCv.ENTREZ.value,
    ],
    'G': [
        IdentifierNamespaceCv.ENTREZ.value,
        IdentifierNamespaceCv.HGNC.value,
        IdentifierNamespaceCv.ENSEMBL.value,
        IdentifierNamespaceCv.REFSEQ.value,
    ],
    'R': [IdentifierNamespaceCv.ENSEMBL.value, IdentifierNamespaceCv.REFSEQ.value],
    'D': [IdentifierNamespaceCv.ENSEMBL.value, IdentifierNamespaceCv.REFSEQ.value],
    'CH': [
        IdentifierNamespaceCv.STANDARD_INCHI_KEY.value,
        IdentifierNamespaceCv.STANDARD_INCHI.value,
        IdentifierNamespaceCv.CHEBI.value,
        IdentifierNamespaceCv.PUBCHEM_COMPOUND.value,
        IdentifierNamespaceCv.PUBCHEM.value,
        IdentifierNamespaceCv.CHEMBL_COMPOUND.value,
        IdentifierNamespaceCv.CHEMBL.value,
        IdentifierNamespaceCv.HMDB.value,
        IdentifierNamespaceCv.METANETX.value,
        IdentifierNamespaceCv.LIPIDMAPS.value,
        IdentifierNamespaceCv.SWISSLIPIDS.value,
        IdentifierNamespaceCv.DRUGBANK.value,
        IdentifierNamespaceCv.KEGG_COMPOUND.value,
        IdentifierNamespaceCv.ZINC.value,
        IdentifierNamespaceCv.BINDINGDB.value,
        IdentifierNamespaceCv.GUIDETOPHARMA.value,
    ],
    'C': [IdentifierNamespaceCv.COMPLEXPORTAL.value, IdentifierNamespaceCv.REACTOME_STABLE_ID.value],
    'PW': [IdentifierNamespaceCv.REACTOME_STABLE_ID.value, IdentifierNamespaceCv.REACTOME_ID.value],
    'RXN': [IdentifierNamespaceCv.REACTOME_STABLE_ID.value, IdentifierNamespaceCv.REACTOME_ID.value],
    'INT': [IdentifierNamespaceCv.INTACT.value, IdentifierNamespaceCv.BINDINGDB.value, IdentifierNamespaceCv.SIGNOR.value],
}

CASE_NORMALIZE_UPPER: frozenset[str] = frozenset({
    IdentifierNamespaceCv.UNIPROT.value,
    IdentifierNamespaceCv.UNIPROT_TREMBL.value,
    IdentifierNamespaceCv.UNIPARC.value,
    IdentifierNamespaceCv.REFSEQ.value,
    IdentifierNamespaceCv.REFSEQ_PROTEIN.value,
    IdentifierNamespaceCv.ENSEMBL.value,
    IdentifierNamespaceCv.ENTREZ.value,
    IdentifierNamespaceCv.HGNC.value,
    IdentifierNamespaceCv.STANDARD_INCHI_KEY.value,
    IdentifierNamespaceCv.GENE_NAME_PRIMARY.value,
    IdentifierNamespaceCv.GENE_NAME_SYNONYM.value,
})

# Preferred short codes for common identifier types used in keys.
# Remaining identifier types are auto-assigned deterministic short codes.
ID_TYPE_SHORT_OVERRIDES_BY_NAME: dict[str, str] = {
    'UNIPROT': 'UP',
    'UNIPROT_TREMBL': 'UPT',
    'UNIPARC': 'UPA',
    'REFSEQ': 'RS',
    'REFSEQ_PROTEIN': 'RSP',
    'ENSEMBL': 'ENS',
    'ENTREZ': 'EG',
    'ENSEMBL_GENOMES': 'ENSG',
    'HGNC': 'HGNC',
    'STANDARD_INCHI_KEY': 'IK',
    'STANDARD_INCHI': 'INCHI',
    'PUBCHEM_COMPOUND': 'CID',
    'CHEMBL_COMPOUND': 'CHEMBL',
    'BINDINGDB': 'BDB',
    'GUIDETOPHARMA': 'GTP',
    'REACTOME_STABLE_ID': 'RST',
    'REACTOME_ID': 'RID',
    'COMPLEXPORTAL': 'CPX',
    'INTACT': 'INTACT',
    'SIGNOR': 'SIGNOR',
    'GENE_NAME_PRIMARY': 'SYMBOL',
    'GENE_NAME_SYNONYM': 'GSYN',
    'NAME': 'NAME',
    'SYNONYM': 'SYN',
    'CV_TERM_ACCESSION': 'CV',
    'NCBI_TAX_ID': 'TAX',
}


def _default_short_code_from_name(enum_name: str) -> str:
    parts = [p for p in enum_name.split('_') if p]
    if not parts:
        return 'ID'
    if len(parts) == 1:
        return parts[0][:4]
    return ''.join(p[0] for p in parts)[:6]


def _build_id_type_short_codes() -> dict[str, str]:
    """Build a unique deterministic accession -> short code mapping."""
    mapping: dict[str, str] = {}
    used: set[str] = set()

    for member in sorted(IdentifierNamespaceCv, key=lambda m: m.name):
        base = ID_TYPE_SHORT_OVERRIDES_BY_NAME.get(member.name, _default_short_code_from_name(member.name))
        code = base
        suffix = 2
        while code in used:
            code = f'{base}{suffix}'
            suffix += 1

        mapping[str(member.value)] = code
        used.add(code)

    return mapping


ID_TYPE_SHORT_BY_ACCESSION = _build_id_type_short_codes()

IDS_CANONICAL_SCHEMA: dict[str, pl.DataType] = {
    'source_ref': pl.Utf8,
    'local_entity_id': pl.Int64,
    'entity_bucket': pl.Utf8,
    'type_id': pl.Utf8,
    'canonical_identifier': pl.Utf8,
    'is_merge_safe': pl.Boolean,
}

RECORD_IDENTITY_SNAPSHOT_SCHEMA: dict[str, pl.DataType] = {
    'run_id': pl.Utf8,
    'source_ref': pl.Utf8,
    'local_entity_id': pl.Int64,
    'entity_key': pl.Utf8,
    'entity_bucket': pl.Utf8,
    'tax_partition': pl.Utf8,
    'anchor_type_id': pl.Utf8,
    'anchor_type_accession': pl.Utf8,
    'anchor_identifier': pl.Utf8,
}

def _slug(text: str) -> str:
    s = re.sub(r'[^A-Za-z0-9]+', '_', text.strip().upper())
    s = re.sub(r'_+', '_', s).strip('_')
    return s or 'SOURCE'


@dataclass(frozen=True)
class Anchor:
    type_id: str
    canonical_identifier: str
    merge_safe: bool


def _canonicalize(type_id: str, value: str) -> str:
    out = str(value).strip()
    out = ' '.join(out.split())
    if type_id in CASE_NORMALIZE_UPPER:
        out = out.upper()
    if type_id == IdentifierNamespaceCv.CHEBI.value:
        out = re.sub(r'^(CHEBI:)+', 'CHEBI:', out, flags=re.IGNORECASE)
        if out.isdigit():
            out = f'CHEBI:{out}'
    return out


def _entity_bucket(entity_type: str | None) -> str:
    if entity_type in EXEMPT_ENTITY_TYPES:
        return 'CH'
    return BUCKET_CODE_BY_ENTITY_TYPE.get(entity_type or UNKNOWN_ENTITY_TYPE_KEY, 'X')


def _build_merge_safe_by_bucket_code() -> dict[str, frozenset[str]]:
    by_code: dict[str, set[str]] = {}
    for bucket, id_types in MERGE_SAFE_IDENTIFIER_TYPES_BY_BUCKET.items():
        if bucket == CHEMICAL_ENTITY_BUCKET:
            code = 'CH'
        else:
            code = BUCKET_CODE_BY_ENTITY_TYPE.get(bucket, 'X')
        by_code.setdefault(code, set()).update(str(x) for x in id_types)
    return {k: frozenset(v) for k, v in by_code.items()}


MERGE_SAFE_BY_BUCKET_CODE = _build_merge_safe_by_bucket_code()


def _extract_tax_annotations(local_tables_dir: Path) -> pl.DataFrame:
    """Extract per-entity NCBI tax IDs from local instance + annotation tables."""
    instance_files = sorted(local_tables_dir.rglob('local_entity_instance_*.parquet'))
    annotation_files = sorted(local_tables_dir.rglob('local_entity_annotation_*.parquet'))

    if not instance_files or not annotation_files:
        return pl.DataFrame({
            'source_ref': pl.Series([], dtype=pl.Utf8),
            'local_entity_id': pl.Series([], dtype=pl.Int64),
            'tax_id': pl.Series([], dtype=pl.Utf8),
        })

    instance_parts: list[pl.DataFrame] = []
    for path in instance_files:
        df = pl.read_parquet(path)
        if len(df) == 0:
            continue
        instance_parts.append(
            df.select(['source_ref', 'local_entity_instance_id', 'local_entity_id'])
            .with_columns([
                pl.col('source_ref').cast(pl.Utf8),
                pl.col('local_entity_instance_id').cast(pl.Int64),
                pl.col('local_entity_id').cast(pl.Int64),
            ])
        )

    annotation_parts: list[pl.DataFrame] = []
    for path in annotation_files:
        df = pl.read_parquet(path)
        if len(df) == 0:
            continue
        annotation_parts.append(
            df.select(['source_ref', 'local_entity_instance_id', 'cv_term_accession', 'value'])
            .with_columns([
                pl.col('source_ref').cast(pl.Utf8),
                pl.col('local_entity_instance_id').cast(pl.Int64),
                pl.col('cv_term_accession').cast(pl.Utf8),
                pl.col('value').cast(pl.Utf8),
            ])
            .filter(pl.col('cv_term_accession') == IdentifierNamespaceCv.NCBI_TAX_ID.value)
            .filter(pl.col('value').is_not_null())
        )

    if not instance_parts or not annotation_parts:
        return pl.DataFrame({
            'source_ref': pl.Series([], dtype=pl.Utf8),
            'local_entity_id': pl.Series([], dtype=pl.Int64),
            'tax_id': pl.Series([], dtype=pl.Utf8),
        })

    instances = pl.concat(instance_parts, how='diagonal_relaxed')
    annotations = pl.concat(annotation_parts, how='diagonal_relaxed')

    return (
        annotations
        .join(instances, on=['source_ref', 'local_entity_instance_id'], how='inner')
        .select([
            'source_ref',
            'local_entity_id',
            pl.col('value').alias('tax_id'),
        ])
        .filter(pl.col('tax_id').str.len_chars() > 0)
        .group_by(['source_ref', 'local_entity_id'])
        .agg(pl.col('tax_id').first())
    )


def _is_tax_scoped_gene_name_merge_safe(bucket: str, type_id: str, tax_id: str | None) -> bool:
    return (
        type_id == IdentifierNamespaceCv.GENE_NAME_PRIMARY.value
        and bucket in GENE_SYMBOL_TAX_SCOPED_BUCKETS
        and tax_id not in (None, '', 'UNK')
    )


def _id_type_short(type_id: str) -> str:
    """Return short code for an identifier type accession."""
    if type_id == 'NONE':
        return 'NONE'
    return ID_TYPE_SHORT_BY_ACCESSION.get(type_id, type_id.replace(':', '_'))


def _choose_anchor(bucket: str, id_rows: list[tuple[str, str, bool]]) -> Anchor:
    if not id_rows:
        return Anchor(type_id='NONE', canonical_identifier='NONE', merge_safe=False)

    priority = {tid: i for i, tid in enumerate(ANCHOR_PRIORITY_BY_BUCKET.get(bucket, []))}

    def rank(row: tuple[str, str, bool]) -> tuple[int, str, str]:
        t, v, _ = row
        return (priority.get(t, 1_000_000), t, v)

    merge_safe_rows = [r for r in id_rows if r[2]]
    if merge_safe_rows:
        t, v, _ = sorted(merge_safe_rows, key=rank)[0]
        return Anchor(type_id=t, canonical_identifier=v, merge_safe=True)

    t, v, _ = sorted(id_rows, key=rank)[0]
    return Anchor(type_id=t, canonical_identifier=v, merge_safe=False)


def build_entity_identifiers_v2(
    local_tables_dir: Path,
    run_id: str | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Build deterministic IEM snapshots.

    Returns:
      - record_identity_snapshot
      - entity_identifier_snapshot_with_id (id, entity_key, type_id, identifier)
      - entity_identifier_resource (id, entity_identifier_id, source_ref)
      - instance_identity_snapshot
    """
    local_tables_dir = Path(local_tables_dir)
    run_id = run_id or datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')

    entity_files = sorted(
        p
        for p in local_tables_dir.rglob('local_entity_*.parquet')
        if 'annotation' not in p.name and 'identifier' not in p.name and 'instance' not in p.name
    )
    identifier_files = sorted(local_tables_dir.rglob('local_entity_identifier_*.parquet'))

    if not entity_files:
        empty = pl.DataFrame()
        return empty, empty, empty, empty

    tax_annotations = _extract_tax_annotations(local_tables_dir)

    all_entities: list[pl.DataFrame] = []
    for path in entity_files:
        df = pl.read_parquet(path)
        if len(df) == 0:
            continue
        entity_part = (
            df.select(['source_ref', 'local_entity_id', 'entity_type'])
            .with_columns([
                pl.col('source_ref').cast(pl.Utf8),
                pl.col('local_entity_id').cast(pl.Int64),
                pl.col('entity_type').cast(pl.Utf8),
            ])
        )
        if len(tax_annotations) > 0:
            entity_part = entity_part.join(
                tax_annotations,
                on=['source_ref', 'local_entity_id'],
                how='left',
            )
        else:
            entity_part = entity_part.with_columns(pl.lit(None, dtype=pl.Utf8).alias('tax_id'))
        all_entities.append(entity_part)

    if not all_entities:
        empty = pl.DataFrame()
        return empty, empty, empty, empty

    all_identifiers: list[pl.DataFrame] = []
    for path in identifier_files:
        df = pl.read_parquet(path)
        if len(df) == 0:
            continue
        all_identifiers.append(
            df.select([
                'source_ref',
                'local_entity_id',
                pl.col('type_id').cast(pl.Utf8).alias('type_id'),
                pl.col('identifier').cast(pl.Utf8).alias('identifier'),
            ])
            .filter(pl.col('type_id').is_not_null() & pl.col('identifier').is_not_null())
        )

    entities_all = pl.concat(all_entities, how='diagonal_relaxed').unique(subset=['source_ref', 'local_entity_id'])
    ids_all = pl.concat(all_identifiers, how='diagonal_relaxed') if all_identifiers else pl.DataFrame({
        'source_ref': pl.Series([], dtype=pl.Utf8),
        'local_entity_id': pl.Series([], dtype=pl.Int64),
        'type_id': pl.Series([], dtype=pl.Utf8),
        'identifier': pl.Series([], dtype=pl.Utf8),
    })

    records = (
        entities_all
        .with_columns(pl.col('entity_type').fill_null(UNKNOWN_ENTITY_TYPE_KEY))
        .with_columns(pl.col('entity_type').map_elements(_entity_bucket, return_dtype=pl.Utf8).alias('entity_bucket'))
        .with_columns(pl.col('tax_id').cast(pl.Utf8))
        .with_columns(pl.lit(None, dtype=pl.Utf8).alias('tax_partition'))
    )

    ids_with_context = (
        ids_all
        .join(
            records.select(['source_ref', 'local_entity_id', 'entity_bucket', 'tax_id']),
            on=['source_ref', 'local_entity_id'],
            how='left',
        )
        .with_columns(pl.col('entity_bucket').fill_null('X'))
    )

    # Canonicalize and mark merge safety
    rows: list[dict] = []
    for row in ids_with_context.iter_rows(named=True):
        bucket = row['entity_bucket']
        t = str(row['type_id'])
        v = _canonicalize(t, str(row['identifier']))
        tax_id = row.get('tax_id')
        allowed = MERGE_SAFE_BY_BUCKET_CODE.get(bucket)
        if _is_tax_scoped_gene_name_merge_safe(bucket, t, tax_id):
            is_merge_safe = True
            merge_partition = str(tax_id)
        elif allowed is not None:
            is_merge_safe = t in allowed
            merge_partition = None
        else:
            is_merge_safe = t not in MERGE_UNSAFE_IDENTIFIER_TYPES
            merge_partition = None
        rows.append({
            'source_ref': str(row['source_ref']),
            'local_entity_id': int(row['local_entity_id']),
            'entity_bucket': bucket,
            'type_id': t,
            'canonical_identifier': v,
            'is_merge_safe': bool(is_merge_safe),
            'merge_partition': merge_partition,
        })

    ids_canonical_schema = {
        **IDS_CANONICAL_SCHEMA,
        'merge_partition': pl.Utf8,
    }
    ids_canonical = pl.DataFrame(rows, schema=ids_canonical_schema) if rows else pl.DataFrame({
        'source_ref': pl.Series([], dtype=pl.Utf8),
        'local_entity_id': pl.Series([], dtype=pl.Int64),
        'entity_bucket': pl.Series([], dtype=pl.Utf8),
        'type_id': pl.Series([], dtype=pl.Utf8),
        'canonical_identifier': pl.Series([], dtype=pl.Utf8),
        'is_merge_safe': pl.Series([], dtype=pl.Boolean),
        'merge_partition': pl.Series([], dtype=pl.Utf8),
    })

    grouped = (
        records
        .join(
            ids_canonical,
            on=['source_ref', 'local_entity_id', 'entity_bucket'],
            how='left',
        )
        .group_by(['source_ref', 'local_entity_id', 'entity_bucket', 'tax_id'])
        .agg([
            pl.col('type_id').drop_nulls().alias('type_ids'),
            pl.col('canonical_identifier').drop_nulls().alias('canonical_ids'),
            pl.col('is_merge_safe').drop_nulls().alias('merge_safe_flags'),
        ])
    )

    record_meta_by_key: dict[tuple[str, int], tuple[str, str | None]] = {}
    record_id_rows: dict[tuple[str, int], list[tuple[str, str, bool]]] = {}
    record_keys: list[tuple[str, int]] = []

    for row in grouped.iter_rows(named=True):
        key = (str(row['source_ref']), int(row['local_entity_id']))
        record_keys.append(key)
        record_meta_by_key[key] = (str(row['entity_bucket']), row.get('tax_id'))

        type_ids = list(row.get('type_ids') or [])
        canonical_ids = list(row.get('canonical_ids') or [])
        flags = list(row.get('merge_safe_flags') or [])
        record_id_rows[key] = list(zip(type_ids, canonical_ids, flags, strict=False))

    # Merge records by ANY shared merge-safe key; only gene-symbol merges are tax-scoped.
    ms_edges = (
        ids_canonical
        .filter(pl.col('is_merge_safe'))
        .select([
            'source_ref',
            'local_entity_id',
            'entity_bucket',
            'merge_partition',
            'type_id',
            'canonical_identifier',
        ])
        .unique()
    ) if len(ids_canonical) > 0 else pl.DataFrame({
        'source_ref': pl.Series([], dtype=pl.Utf8),
        'local_entity_id': pl.Series([], dtype=pl.Int64),
        'entity_bucket': pl.Series([], dtype=pl.Utf8),
        'merge_partition': pl.Series([], dtype=pl.Utf8),
        'type_id': pl.Series([], dtype=pl.Utf8),
        'canonical_identifier': pl.Series([], dtype=pl.Utf8),
    })

    rec_index = {rk: i for i, rk in enumerate(record_keys)}
    parent = list(range(len(record_keys)))
    rank = [0] * len(record_keys)

    def _find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def _union(i: int, j: int) -> None:
        ri = _find(i)
        rj = _find(j)
        if ri == rj:
            return
        if rank[ri] < rank[rj]:
            parent[ri] = rj
        elif rank[ri] > rank[rj]:
            parent[rj] = ri
        else:
            parent[rj] = ri
            rank[ri] += 1

    if len(ms_edges) > 0:
        ms_grouped = (
            ms_edges
            .group_by(['type_id', 'canonical_identifier', 'entity_bucket', 'merge_partition'])
            .agg([
                pl.struct(['source_ref', 'local_entity_id']).alias('members'),
            ])
        )
        for row in ms_grouped.iter_rows(named=True):
            members = row.get('members') or []
            if len(members) < 2:
                continue
            base = members[0]
            base_key = (str(base['source_ref']), int(base['local_entity_id']))
            base_idx = rec_index.get(base_key)
            if base_idx is None:
                continue
            for m in members[1:]:
                other_key = (str(m['source_ref']), int(m['local_entity_id']))
                other_idx = rec_index.get(other_key)
                if other_idx is None:
                    continue
                _union(base_idx, other_idx)

    component_members: dict[int, list[tuple[str, int]]] = {}
    for rk, idx in rec_index.items():
        root = _find(idx)
        component_members.setdefault(root, []).append(rk)

    ms_id_rows_by_record: dict[tuple[str, int], list[tuple[str, str, bool]]] = {}
    for row in ms_edges.iter_rows(named=True):
        rk = (str(row['source_ref']), int(row['local_entity_id']))
        ms_id_rows_by_record.setdefault(rk, []).append(
            (str(row['type_id']), str(row['canonical_identifier']), True)
        )

    snapshot_rows: list[dict] = []
    for members in component_members.values():
        first = members[0]
        bucket, tax_id = record_meta_by_key[first]
        tax_partition = None

        component_id_rows: list[tuple[str, str, bool]] = []
        for rk in members:
            component_id_rows.extend(ms_id_rows_by_record.get(rk, []))

        if component_id_rows:
            anchor = _choose_anchor(bucket, component_id_rows)
            anchor_type_short = _id_type_short(anchor.type_id)
            entity_key = f'{bucket}:{anchor_type_short}:{anchor.canonical_identifier}'
            if _is_tax_scoped_gene_name_merge_safe(bucket, anchor.type_id, tax_id):
                entity_key = f'{entity_key}:{tax_id}'
                tax_partition = str(tax_id)
            anchor_type_accession = anchor.type_id
            anchor_identifier = anchor.canonical_identifier
        else:
            # No merge-safe ID in this component (singleton by construction)
            rk = first
            id_rows = record_id_rows.get(rk, [])
            anchor = _choose_anchor(bucket, id_rows)
            anchor_type_short = _id_type_short(anchor.type_id)
            anchor_type_accession = anchor.type_id
            anchor_identifier = anchor.canonical_identifier
            source_slug = _slug(rk[0])
            entity_key = (
                f'{bucket}:SN:{anchor_type_short}:{anchor.canonical_identifier}:'
                f'{source_slug}.{rk[1]}'
            )

        for source_ref, local_entity_id in members:
            snapshot_rows.append({
                'run_id': str(run_id),
                'source_ref': str(source_ref),
                'local_entity_id': int(local_entity_id),
                'entity_key': str(entity_key),
                'entity_bucket': str(bucket),
                'tax_partition': None if tax_partition is None else str(tax_partition),
                'anchor_type_id': str(anchor_type_short),
                'anchor_type_accession': None if anchor_type_accession is None else str(anchor_type_accession),
                'anchor_identifier': None if anchor_identifier is None else str(anchor_identifier),
            })

    record_identity_snapshot = (
        pl.DataFrame(snapshot_rows, schema=RECORD_IDENTITY_SNAPSHOT_SCHEMA)
        .sort(['source_ref', 'local_entity_id'])
        if snapshot_rows else
        pl.DataFrame({
            'run_id': pl.Series([], dtype=pl.Utf8),
            'source_ref': pl.Series([], dtype=pl.Utf8),
            'local_entity_id': pl.Series([], dtype=pl.Int64),
            'entity_key': pl.Series([], dtype=pl.Utf8),
            'entity_bucket': pl.Series([], dtype=pl.Utf8),
            'tax_partition': pl.Series([], dtype=pl.Utf8),
            'anchor_type_id': pl.Series([], dtype=pl.Utf8),
            'anchor_type_accession': pl.Series([], dtype=pl.Utf8),
            'anchor_identifier': pl.Series([], dtype=pl.Utf8),
        })
    )

    # Instance snapshot
    instance_files = sorted(local_tables_dir.rglob('local_entity_instance_*.parquet'))
    if instance_files:
        parts = [pl.read_parquet(p) for p in instance_files if p.exists()]
        parts = [p for p in parts if len(p) > 0]
    else:
        parts = []

    if parts:
        instances_all = pl.concat(parts, how='diagonal_relaxed')
        instance_identity_snapshot = (
            instances_all
            .join(
                record_identity_snapshot.select(['source_ref', 'local_entity_id', 'entity_key']),
                on=['source_ref', 'local_entity_id'],
                how='left',
            )
            .with_columns([
                pl.lit(run_id).alias('run_id'),
                pl.format('INS:{}:{}', pl.col('source_ref'), pl.col('local_entity_instance_id')).alias('instance_key'),
            ])
            .select(['run_id', 'source_ref', 'local_entity_instance_id', 'instance_key', 'entity_key'])
            .sort(['source_ref', 'local_entity_instance_id'])
        )
    else:
        instance_identity_snapshot = pl.DataFrame({
            'run_id': pl.Series([], dtype=pl.Utf8),
            'source_ref': pl.Series([], dtype=pl.Utf8),
            'local_entity_instance_id': pl.Series([], dtype=pl.Int64),
            'instance_key': pl.Series([], dtype=pl.Utf8),
            'entity_key': pl.Series([], dtype=pl.Utf8),
        })

    # Entity identifier snapshot (recommended in spec)
    ids_for_entity = (
        ids_canonical
        .join(
            record_identity_snapshot.select(['source_ref', 'local_entity_id', 'entity_key']),
            on=['source_ref', 'local_entity_id'],
            how='inner',
        )
        .select(['entity_key', 'type_id', 'canonical_identifier', 'source_ref'])
        .unique()
    ) if len(ids_canonical) > 0 else pl.DataFrame({
        'entity_key': pl.Series([], dtype=pl.Utf8),
        'type_id': pl.Series([], dtype=pl.Utf8),
        'canonical_identifier': pl.Series([], dtype=pl.Utf8),
        'source_ref': pl.Series([], dtype=pl.Utf8),
    })

    entity_identifier_snapshot = (
        ids_for_entity
        .select(['entity_key', 'type_id', pl.col('canonical_identifier').alias('identifier')])
        .unique()
        .sort(['entity_key', 'type_id', 'identifier'])
        .with_row_index('id', offset=1)
    )

    entity_identifier_resource = (
        ids_for_entity
        .join(
            entity_identifier_snapshot.select(['id', 'entity_key', 'type_id', 'identifier']),
            left_on=['entity_key', 'type_id', 'canonical_identifier'],
            right_on=['entity_key', 'type_id', 'identifier'],
            how='inner',
        )
        .select([
            pl.col('id').alias('entity_identifier_id'),
            'source_ref',
        ])
        .unique()
        .sort(['entity_identifier_id', 'source_ref'])
        .with_row_index('id', offset=1)
    )

    logger.info(
        'IEM v2 built: records=%s instances=%s entity_identifiers=%s resources=%s',
        f'{len(record_identity_snapshot):,}',
        f'{len(instance_identity_snapshot):,}',
        f'{len(entity_identifier_snapshot):,}',
        f'{len(entity_identifier_resource):,}',
    )

    return (
        record_identity_snapshot,
        entity_identifier_snapshot,
        entity_identifier_resource,
        instance_identity_snapshot,
    )
