"""Build chemical identifier resolver mappings from supported sources.

Chemical resolver rows normalize source-specific identifiers such as ChEBI,
ChEMBL, HMDB, LipidMaps, SwissLipids, and PubChem to standard InChI keys. A
standard InChI value is retained when available, but canonicalization resolves
chemical evidence by standard InChI key so equivalent source identifiers
collapse to one canonical chemical entity.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Iterable

import polars as pl

from omnipath_build.resolver.identifier_types import (
    IDENTIFIER_TYPE_SCHEMA,
    identifier_type_id,
    identifier_type_rows,
)
from omnipath_build.resolver.paths import (
    activate_raw_download_data_dir,
    ensure_chemicals_data_dir,
)
from pypath.internals.cv_terms import (
    IdentifierNamespaceCv,
    cv_term_label_accession,
)
from pypath.inputs_v2.chebi import resource as chebi_resource
from pypath.inputs_v2.chembl import resource as chembl_resource
from pypath.inputs_v2.hmdb import resource as hmdb_resource
from pypath.inputs_v2.lipidmaps import resource as lipidmaps_resource
from pypath.inputs_v2.swisslipids import resource as swisslipids_resource

CHEMICAL_IDENTIFIER_LOOKUP_SCHEMA: dict[str, pl.DataType] = {
    'key_identifier_type_id': pl.UInt32,
    'key_value': pl.Utf8,
    'canonical_identifier_type_id': pl.UInt32,
    'canonical_identifier': pl.Utf8,
}

CHEMICAL_SOURCES: tuple[str, ...] = (
    'chebi',
    'chembl',
    'hmdb',
    'lipidmaps',
    'swisslipids',
    'pubchem',
)
CHEMICAL_IDENTIFIER_LOOKUP_OUTPUT_FILENAME = 'chemical_identifier_lookup.parquet'
IDENTIFIER_TYPE_OUTPUT_FILENAME = 'identifier_type.parquet'
CHEBI_TYPE = cv_term_label_accession(IdentifierNamespaceCv.CHEBI)
CHEMBL_COMPOUND_TYPE = cv_term_label_accession(
    IdentifierNamespaceCv.CHEMBL_COMPOUND
)
HMDB_TYPE = cv_term_label_accession(IdentifierNamespaceCv.HMDB)
LIPIDMAPS_TYPE = cv_term_label_accession(IdentifierNamespaceCv.LIPIDMAPS)
SWISSLIPIDS_TYPE = cv_term_label_accession(IdentifierNamespaceCv.SWISSLIPIDS)
STANDARD_INCHI_KEY_TYPE = cv_term_label_accession(
    IdentifierNamespaceCv.STANDARD_INCHI_KEY
)


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_inchikey(value: object) -> str | None:
    text = _clean(value)
    if text is None or text.lower() in {'none', 'inchikey=none'}:
        return None
    return text.removeprefix('InChIKey=')


def _clean_inchi(value: object) -> str | None:
    text = _clean(value)
    if text is None or text.lower() in {'none', 'inchi=none'}:
        return None
    return text


def _chebi_row(row: dict) -> dict | None:
    match = re.fullmatch(
        r'(?:CHEBI:)?(\d+)',
        str(row.get('chebi_id') or '').strip(),
    )
    key_value = match.group(1) if match else None
    standard_inchi = _clean_inchi(row.get('inchi'))
    standard_inchi_key = _clean_inchikey(row.get('inchikey'))
    if not key_value or not standard_inchi or not standard_inchi_key:
        return None
    return {
        'key_type': CHEBI_TYPE,
        'key_value': key_value,
        'standard_inchi_key': standard_inchi_key,
        'standard_inchi': standard_inchi,
    }


def _chembl_row(row: dict) -> dict | None:
    key_value = _clean(row.get('chembl_id'))
    standard_inchi_key = _clean_inchikey(row.get('standard_inchi_key'))
    if not key_value or not standard_inchi_key:
        return None
    return {
        'key_type': CHEMBL_COMPOUND_TYPE,
        'key_value': key_value,
        'standard_inchi_key': standard_inchi_key,
    }


def _hmdb_row(row: dict) -> dict | None:
    key_value = _clean(row.get('accession'))
    standard_inchi = _clean_inchi(row.get('inchi'))
    standard_inchi_key = _clean_inchikey(row.get('inchikey'))
    if not key_value or not standard_inchi or not standard_inchi_key:
        return None
    return {
        'key_type': HMDB_TYPE,
        'key_value': key_value,
        'standard_inchi_key': standard_inchi_key,
        'standard_inchi': standard_inchi,
    }


def _lipidmaps_row(row: dict) -> dict | None:
    key_value = _clean(row.get('LM_ID'))
    standard_inchi = _clean_inchi(row.get('INCHI'))
    standard_inchi_key = _clean_inchikey(row.get('INCHI_KEY'))
    if not key_value or not standard_inchi or not standard_inchi_key:
        return None
    return {
        'key_type': LIPIDMAPS_TYPE,
        'key_value': key_value,
        'standard_inchi_key': standard_inchi_key,
        'standard_inchi': standard_inchi,
    }


def _swisslipids_row(row: dict) -> dict | None:
    key_value = _clean(row.get('Lipid ID'))
    standard_inchi = _clean_inchi(row.get('InChI (pH7.3)'))
    standard_inchi_key = _clean_inchikey(row.get('InChI key (pH7.3)'))
    if not key_value or not standard_inchi or not standard_inchi_key:
        return None
    return {
        'key_type': SWISSLIPIDS_TYPE,
        'key_value': key_value,
        'standard_inchi_key': standard_inchi_key,
        'standard_inchi': standard_inchi,
    }


_CHEMICAL_DATASETS: dict[str, tuple[object, Callable[[dict], dict | None]]] = {
    'chebi': (chebi_resource.molecules, _chebi_row),
    'chembl': (chembl_resource.molecules, _chembl_row),
    'hmdb': (hmdb_resource.metabolites, _hmdb_row),
    'lipidmaps': (lipidmaps_resource.lipids, _lipidmaps_row),
    'swisslipids': (swisslipids_resource.lipids, _swisslipids_row),
}


def _validate_chemical_sources(sources: Iterable[str]) -> tuple[str, ...]:
    selected = tuple(sources)
    unsupported = sorted(set(selected) - set(CHEMICAL_SOURCES))
    if unsupported:
        raise ValueError(f'Unsupported chemical source(s): {unsupported}')
    return selected


def _chemical_identifier_rows(
    sources: Iterable[str],
    max_records: int | None = None,
    pubchem_url: str | Path | None = None,
    pubchem_shards: int | None = None,
    chemical_lookup_path: str | Path | None = None,
) -> Iterable[dict]:
    for source in _validate_chemical_sources(sources):
        if source == 'pubchem':
            from omnipath_build.resolver.sources.pubchem import (
                iter_pubchem_compound_rows,
            )

            rows = iter_pubchem_compound_rows(
                pubchem_url,
                filter_inchikeys=_chemical_filter_inchikeys(
                    chemical_lookup_path,
                ),
                shard_count=pubchem_shards,
            )
            emitted = 0
            for row in rows:
                yield row
                emitted += 1
                if max_records is not None and emitted >= max_records:
                    break
            continue

        dataset, mapper = _CHEMICAL_DATASETS[source]
        emitted = 0
        for raw_row in dataset.raw():
            row = mapper(raw_row)
            if row is None:
                continue
            yield row
            emitted += 1
            if max_records is not None and emitted >= max_records:
                break


def build_chemical_identifier_lookup(
    sources: Iterable[str] = CHEMICAL_SOURCES,
    max_records: int | None = None,
    pubchem_url: str | Path | None = None,
    pubchem_shards: int | None = None,
) -> pl.DataFrame:
    """Return normalized chemical identifier lookup rows as a dataframe."""

    activate_raw_download_data_dir()
    rows = list(
        _chemical_identifier_rows(
            sources,
            max_records=max_records,
            pubchem_url=pubchem_url,
            pubchem_shards=pubchem_shards,
            chemical_lookup_path=Path('omnipath_build/data/chemicals')
            / CHEMICAL_IDENTIFIER_LOOKUP_OUTPUT_FILENAME,
        )
    )
    if not rows:
        return pl.DataFrame(schema=CHEMICAL_IDENTIFIER_LOOKUP_SCHEMA)
    return _normalize_chemical_identifier_lookup(rows)[0]


def materialize_chemical_sources(
    sources: Iterable[str],
    output_dir: str | Path | None = None,
    max_records: int | None = None,
    pubchem_url: str | Path | None = None,
    pubchem_shards: int | None = None,
) -> dict[str, int]:
    """Write chemical resolver parquet files and return output row counts."""

    selected = _validate_chemical_sources(sources)
    output_dir = (
        Path(output_dir)
        if output_dir is not None
        else ensure_chemicals_data_dir()
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    activate_raw_download_data_dir()
    chemical_lookup_path = output_dir / CHEMICAL_IDENTIFIER_LOOKUP_OUTPUT_FILENAME
    lookup, identifier_types = _normalize_chemical_identifier_lookup(
        _chemical_identifier_rows(
            selected,
            max_records=max_records,
            pubchem_url=pubchem_url,
            pubchem_shards=pubchem_shards,
            chemical_lookup_path=chemical_lookup_path,
        )
    )
    lookup.write_parquet(output_dir / CHEMICAL_IDENTIFIER_LOOKUP_OUTPUT_FILENAME)
    identifier_types.write_parquet(output_dir / IDENTIFIER_TYPE_OUTPUT_FILENAME)

    return {
        'chemical_identifier_lookup_rows': lookup.height,
        'identifier_type_rows': identifier_types.height,
    }


def _chemical_filter_inchikeys(
    lookup_path: str | Path | None,
) -> frozenset[str] | None:
    path = Path(lookup_path) if lookup_path is not None else None
    if path is None or not path.exists():
        fallback = Path('omnipath_build/data/chemicals') / (
            CHEMICAL_IDENTIFIER_LOOKUP_OUTPUT_FILENAME
        )
        path = fallback if fallback.exists() else None
    if path is None:
        return None

    scan = pl.scan_parquet(path)
    columns = set(scan.collect_schema().names())
    if {'source', 'standard_inchi_key'} <= columns:
        values = (
            scan.filter(
                pl.col('source').str.to_lowercase().is_in(['chebi', 'hmdb'])
                & pl.col('standard_inchi_key').is_not_null()
                & (pl.col('standard_inchi_key') != '')
            )
            .select('standard_inchi_key')
            .unique()
            .collect()
            .get_column('standard_inchi_key')
            .to_list()
        )
        return frozenset(values)
    if {'key_identifier_type_id', 'canonical_identifier'} <= columns:
        identifier_type_path = path.with_name(IDENTIFIER_TYPE_OUTPUT_FILENAME)
        if not identifier_type_path.exists():
            return None
        type_ids = (
            pl.scan_parquet(identifier_type_path)
            .filter(
                pl.col('name')
                .str.to_lowercase()
                .str.split(':')
                .list.first()
                .is_in(['chebi', 'hmdb'])
            )
            .select('identifier_type_id')
            .collect()
            .get_column('identifier_type_id')
            .to_list()
        )
        values = (
            scan.filter(
                pl.col('key_identifier_type_id').is_in(type_ids)
                & pl.col('canonical_identifier').is_not_null()
                & (pl.col('canonical_identifier') != '')
            )
            .select('canonical_identifier')
            .unique()
            .collect()
            .get_column('canonical_identifier')
            .to_list()
        )
        return frozenset(values)
    return None


def _normalize_chemical_identifier_lookup(
    rows: Iterable[dict],
) -> tuple[pl.DataFrame, pl.DataFrame]:
    normalized_rows: list[dict[str, object]] = []
    type_names = {STANDARD_INCHI_KEY_TYPE}
    for row in rows:
        key_type = row.get('key_type')
        if key_type is None:
            continue
        key_type = str(key_type)
        type_names.add(key_type)
        standard_inchi_key = row.get('standard_inchi_key')
        normalized_rows.append(
            {
                'key_identifier_type_id': identifier_type_id(key_type),
                'key_value': row.get('key_value'),
                'canonical_identifier_type_id': identifier_type_id(
                    STANDARD_INCHI_KEY_TYPE
                ),
                'canonical_identifier': standard_inchi_key,
            }
        )

    identifier_types = pl.DataFrame(
        identifier_type_rows(type_names),
        schema=IDENTIFIER_TYPE_SCHEMA,
    )
    if not normalized_rows:
        return (
            pl.DataFrame(schema=CHEMICAL_IDENTIFIER_LOOKUP_SCHEMA),
            identifier_types,
        )

    lookup = (
        pl.DataFrame(normalized_rows, schema=CHEMICAL_IDENTIFIER_LOOKUP_SCHEMA)
        .filter(
            pl.col('key_value').is_not_null()
            & (pl.col('key_value') != '')
            & pl.col('canonical_identifier').is_not_null()
            & (pl.col('canonical_identifier') != '')
        )
        .unique()
    )
    return lookup, identifier_types
