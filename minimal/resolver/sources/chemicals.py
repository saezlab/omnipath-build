from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Iterable

import polars as pl

from minimal.resolver.parquet import write_parquet_from_dict_rows
from minimal.resolver.paths import (
    activate_raw_download_data_dir,
    ensure_chemicals_data_dir,
)
from pypath.inputs_v2.chebi import resource as chebi_resource
from pypath.inputs_v2.hmdb import resource as hmdb_resource
from pypath.inputs_v2.lipidmaps import resource as lipidmaps_resource
from pypath.inputs_v2.swisslipids import resource as swisslipids_resource

CHEMICAL_IDENTIFIER_LOOKUP_SCHEMA: dict[str, pl.DataType] = {
    'source': pl.Utf8,
    'key_type': pl.Utf8,
    'key_value': pl.Utf8,
    'standard_inchi_key': pl.Utf8,
    'standard_inchi': pl.Utf8,
}

CHEMICAL_SOURCES: tuple[str, ...] = (
    'chebi',
    'hmdb',
    'lipidmaps',
    'swisslipids',
    'pubchem',
)
CHEMICAL_IDENTIFIER_LOOKUP_OUTPUT_FILENAME = 'chemical_identifier_lookup.parquet'


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
        'source': 'chebi',
        'key_type': 'MI:0474:Chebi',
        'key_value': key_value,
        'standard_inchi_key': standard_inchi_key,
        'standard_inchi': standard_inchi,
    }


def _hmdb_row(row: dict) -> dict | None:
    key_value = _clean(row.get('accession'))
    standard_inchi = _clean_inchi(row.get('inchi'))
    standard_inchi_key = _clean_inchikey(row.get('inchikey'))
    if not key_value or not standard_inchi or not standard_inchi_key:
        return None
    return {
        'source': 'hmdb',
        'key_type': 'OM:0004:Hmdb',
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
        'source': 'lipidmaps',
        'key_type': 'OM:0003:Lipidmaps',
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
        'source': 'swisslipids',
        'key_type': 'OM:0009:Swisslipids',
        'key_value': key_value,
        'standard_inchi_key': standard_inchi_key,
        'standard_inchi': standard_inchi,
    }


_CHEMICAL_DATASETS: dict[str, tuple[object, Callable[[dict], dict | None]]] = {
    'chebi': (chebi_resource.molecules, _chebi_row),
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
) -> Iterable[dict]:
    for source in _validate_chemical_sources(sources):
        if source == 'pubchem':
            from minimal.resolver.sources.pubchem import (
                iter_pubchem_compound_rows,
            )

            rows = iter_pubchem_compound_rows(pubchem_url)
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
) -> pl.DataFrame:
    activate_raw_download_data_dir()
    rows = list(
        _chemical_identifier_rows(
            sources,
            max_records=max_records,
            pubchem_url=pubchem_url,
        )
    )
    if not rows:
        return pl.DataFrame(schema=CHEMICAL_IDENTIFIER_LOOKUP_SCHEMA)
    return pl.DataFrame(rows, schema=CHEMICAL_IDENTIFIER_LOOKUP_SCHEMA).unique()


def materialize_chemical_sources(
    sources: Iterable[str],
    output_dir: str | Path | None = None,
    max_records: int | None = None,
    pubchem_url: str | Path | None = None,
) -> dict[str, int]:
    selected = _validate_chemical_sources(sources)
    output_dir = (
        Path(output_dir)
        if output_dir is not None
        else ensure_chemicals_data_dir()
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    activate_raw_download_data_dir()
    row_count = write_parquet_from_dict_rows(
        _chemical_identifier_rows(
            selected,
            max_records=max_records,
            pubchem_url=pubchem_url,
        ),
        CHEMICAL_IDENTIFIER_LOOKUP_SCHEMA,
        output_dir / CHEMICAL_IDENTIFIER_LOOKUP_OUTPUT_FILENAME,
    )

    return {'chemical_identifier_lookup_rows': row_count}
