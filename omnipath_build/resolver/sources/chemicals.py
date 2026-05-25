"""Build chemical identifier resolver mappings from supported sources.

Chemical resolver rows normalize source-specific identifiers such as ChEBI,
ChEMBL, HMDB, LipidMaps, RaMP, RefMet, SwissLipids, and PubChem to standard
InChI keys. A standard InChI value is retained when available, but
canonicalization resolves chemical evidence by standard InChI key so
equivalent source identifiers collapse to one canonical chemical entity.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from collections.abc import Callable, Iterable

import polars as pl

from pypath.inputs_v2.hmdb import resource as hmdb_resource
from pypath.inputs_v2.chebi import resource as chebi_resource
from pypath.inputs_v2.chembl import resource as chembl_resource
from pypath.inputs_v2.refmet import resource as refmet_resource
from pypath.internals.cv_terms import (
    IdentifierNamespaceCv,
    cv_term_label_accession,
)
from pypath.inputs_v2.lipidmaps import resource as lipidmaps_resource
from pypath.inputs_v2.swisslipids import resource as swisslipids_resource
from omnipath_build.resolver.paths import (
    ensure_chemicals_data_dir,
    activate_raw_download_data_dir,
)
from omnipath_build.resolver.identifier_types import (
    IDENTIFIER_TYPE_SCHEMA,
    identifier_type_id,
    identifier_type_rows,
)

CHEMICAL_IDENTIFIER_LOOKUP_SCHEMA: dict[str, pl.DataType] = {
    'key_identifier_type_id': pl.UInt32,
    'key_value': pl.Utf8,
    'canonical_identifier_type_id': pl.UInt32,
    'canonical_identifier': pl.Utf8,
}

CHEMICAL_SOURCES: tuple[str, ...] = (
    'chebi',
    'hmdb',
    'lipidmaps',
    'swisslipids',
    'chembl',
    'refmet',
    'ramp',
    'pubchem',
)
LOOKUP_DEPENDENT_CHEMICAL_SOURCES = frozenset({'chembl', 'pubchem'})
CHEMICAL_IDENTIFIER_LOOKUP_OUTPUT_FILENAME = (
    'chemical_identifier_lookup.parquet'
)
CHEMICAL_IDENTIFIER_LOOKUP_AMBIGUOUS_OUTPUT_FILENAME = (
    'chemical_identifier_lookup_ambiguous.parquet'
)
IDENTIFIER_TYPE_OUTPUT_FILENAME = 'identifier_type.parquet'
CHEBI_TYPE = cv_term_label_accession(IdentifierNamespaceCv.CHEBI)
CHEMBL_COMPOUND_TYPE = cv_term_label_accession(
    IdentifierNamespaceCv.CHEMBL_COMPOUND
)
HMDB_TYPE = cv_term_label_accession(IdentifierNamespaceCv.HMDB)
LIPIDMAPS_TYPE = cv_term_label_accession(IdentifierNamespaceCv.LIPIDMAPS)
RAMP_ID_TYPE = cv_term_label_accession(IdentifierNamespaceCv.RAMP_ID)
REFMET_TYPE = cv_term_label_accession(IdentifierNamespaceCv.REFMET)
SWISSLIPIDS_TYPE = cv_term_label_accession(IdentifierNamespaceCv.SWISSLIPIDS)
STANDARD_INCHI_KEY_TYPE = cv_term_label_accession(
    IdentifierNamespaceCv.STANDARD_INCHI_KEY
)
PUBCHEM_COMPOUND_TYPE = cv_term_label_accession(
    IdentifierNamespaceCv.PUBCHEM_COMPOUND
)
CHEMICAL_SOURCE_IDENTIFIER_TYPES = {
    'chebi': CHEBI_TYPE,
    'chembl': CHEMBL_COMPOUND_TYPE,
    'hmdb': HMDB_TYPE,
    'lipidmaps': LIPIDMAPS_TYPE,
    'pubchem': PUBCHEM_COMPOUND_TYPE,
    'ramp': RAMP_ID_TYPE,
    'refmet': REFMET_TYPE,
    'swisslipids': SWISSLIPIDS_TYPE,
}


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


def _ramp_row(row: dict) -> dict | None:
    key_value = _clean(row.get('ramp_id'))
    standard_inchi_key = _clean_inchikey(row.get('inchi_key'))
    if not key_value or not standard_inchi_key:
        return None
    return {
        'key_type': RAMP_ID_TYPE,
        'key_value': key_value,
        'standard_inchi_key': standard_inchi_key,
    }


def _refmet_row(row: dict) -> dict | None:
    key_value = _clean(row.get('refmet_id') or row.get(' refmet_id'))
    standard_inchi_key = _clean_inchikey(row.get('inchi_key'))
    if not key_value or not standard_inchi_key:
        return None
    return {
        'key_type': REFMET_TYPE,
        'key_value': key_value,
        'standard_inchi_key': standard_inchi_key,
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
    'refmet': (refmet_resource.metabolites, _refmet_row),
    'swisslipids': (swisslipids_resource.lipids, _swisslipids_row),
}


def _validate_chemical_sources(sources: Iterable[str]) -> tuple[str, ...]:
    selected = tuple(sources)
    unsupported = sorted(set(selected) - set(CHEMICAL_SOURCES))
    if unsupported:
        raise ValueError(f'Unsupported chemical source(s): {unsupported}')
    return selected


def _order_chemical_sources(sources: Iterable[str]) -> tuple[str, ...]:
    """Return sources in dependency order, independent lookups before filters."""

    selected = set(_validate_chemical_sources(sources))
    return tuple(source for source in CHEMICAL_SOURCES if source in selected)


def _chemical_identifier_rows(
    sources: Iterable[str],
    max_records: int | None = None,
    pubchem_url: str | Path | None = None,
    pubchem_shards: int | None = None,
    chemical_lookup_path: str | Path | None = None,
    chemical_lookup_sources: Iterable[str] | None = None,
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
                    sources=chemical_lookup_sources,
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
        if source == 'ramp':
            from pypath.inputs_v2.rampdb import resource as ramp_resource

            emitted = 0
            for raw_row in ramp_resource.chem_props.raw():
                row = _ramp_row(raw_row)
                if row is None:
                    continue
                yield row
                emitted += 1
                if max_records is not None and emitted >= max_records:
                    break
            continue

        dataset, mapper = _CHEMICAL_DATASETS[source]
        raw_kwargs = {}
        if source == 'chembl' and chemical_lookup_path is not None:
            raw_kwargs['chemical_resolver_lookup_path'] = chemical_lookup_path
            if chemical_lookup_sources:
                raw_kwargs['chemical_resolver_sources'] = tuple(
                    chemical_lookup_sources
                )
        emitted = 0
        for raw_row in dataset.raw(**raw_kwargs):
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
    return _split_chemical_identifier_lookup(rows)[0]


def materialize_chemical_sources(
    sources: Iterable[str],
    output_dir: str | Path | None = None,
    max_records: int | None = None,
    pubchem_url: str | Path | None = None,
    pubchem_shards: int | None = None,
    skip_existing: bool = True,
    continue_on_error: bool = False,
) -> dict[str, int]:
    """Write chemical resolver parquet files and return output row counts."""

    selected = _order_chemical_sources(sources)
    output_dir = (
        Path(output_dir)
        if output_dir is not None
        else ensure_chemicals_data_dir()
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    activate_raw_download_data_dir()
    chemical_lookup_path = (
        output_dir / CHEMICAL_IDENTIFIER_LOOKUP_OUTPUT_FILENAME
    )
    ambiguous_path = (
        output_dir / CHEMICAL_IDENTIFIER_LOOKUP_AMBIGUOUS_OUTPUT_FILENAME
    )
    identifier_type_path = output_dir / IDENTIFIER_TYPE_OUTPUT_FILENAME
    (
        existing_lookup,
        existing_ambiguous,
        existing_identifier_types,
    ) = _read_existing_chemical_lookup(
        chemical_lookup_path,
        ambiguous_path,
        identifier_type_path,
    )
    loaded_sources = (
        _loaded_chemical_sources(
            _combine_existing_chemical_lookup(
                existing_lookup,
                existing_ambiguous,
            ),
            existing_identifier_types,
        )
        if skip_existing
        else set()
    )
    rows: list[dict] = []
    completed_sources: list[str] = [
        source for source in CHEMICAL_SOURCES if source in loaded_sources
    ]

    for source in selected:
        if source in loaded_sources:
            print(
                f'[resolver] skip source={source} existing_dir={output_dir}',
                flush=True,
            )
            continue
        lookup_sources = tuple(completed_sources)
        if source in LOOKUP_DEPENDENT_CHEMICAL_SOURCES and (
            rows or existing_lookup is not None
        ):
            _write_chemical_lookup_files(
                rows,
                output_dir,
                existing_lookup=existing_lookup,
                existing_ambiguous=existing_ambiguous,
                existing_identifier_types=existing_identifier_types,
            )

        try:
            rows.extend(
                _chemical_identifier_rows(
                    (source,),
                    max_records=max_records,
                    pubchem_url=pubchem_url,
                    pubchem_shards=pubchem_shards,
                    chemical_lookup_path=chemical_lookup_path,
                    chemical_lookup_sources=lookup_sources,
                )
            )
        except Exception as exc:
            if not continue_on_error:
                raise
            print(
                '[warning] '
                f'[resolver.{source}] materialize failed; continuing: '
                f'{exc.__class__.__name__}: {exc}',
                file=sys.stderr,
                flush=True,
            )
            continue
        else:
            completed_sources.append(source)

    if (
        not rows
        and existing_lookup is not None
        and existing_identifier_types is not None
    ):
        return {
            'chemical_identifier_lookup_rows': existing_lookup.height,
            'chemical_identifier_lookup_ambiguous_rows': (
                existing_ambiguous.height
                if existing_ambiguous is not None
                else _empty_chemical_lookup().height
            ),
            'identifier_type_rows': existing_identifier_types.height,
        }

    lookup, ambiguous, identifier_types = _write_chemical_lookup_files(
        rows,
        output_dir,
        existing_lookup=existing_lookup,
        existing_ambiguous=existing_ambiguous,
        existing_identifier_types=existing_identifier_types,
    )

    return {
        'chemical_identifier_lookup_rows': lookup.height,
        'chemical_identifier_lookup_ambiguous_rows': ambiguous.height,
        'identifier_type_rows': identifier_types.height,
    }


def _write_chemical_lookup_files(
    rows: Iterable[dict],
    output_dir: Path,
    *,
    existing_lookup: pl.DataFrame | None = None,
    existing_ambiguous: pl.DataFrame | None = None,
    existing_identifier_types: pl.DataFrame | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    lookup, ambiguous, identifier_types = _split_chemical_identifier_lookup(
        rows
    )
    existing_frames = [
        frame
        for frame in (existing_lookup, existing_ambiguous)
        if frame is not None and not frame.is_empty()
    ]
    if existing_frames:
        all_lookup = pl.concat(
            [*existing_frames, lookup, ambiguous],
            how='vertical_relaxed',
        ).unique()
        lookup, ambiguous = _split_chemical_lookup_frame(all_lookup)
    if existing_identifier_types is not None:
        identifier_types = (
            pl.concat(
                [existing_identifier_types, identifier_types],
                how='vertical_relaxed',
            )
            .unique(subset=['identifier_type_id'])
            .sort('identifier_type_id')
        )
    lookup.write_parquet(
        output_dir / CHEMICAL_IDENTIFIER_LOOKUP_OUTPUT_FILENAME
    )
    ambiguous.write_parquet(
        output_dir / CHEMICAL_IDENTIFIER_LOOKUP_AMBIGUOUS_OUTPUT_FILENAME
    )
    identifier_types.write_parquet(output_dir / IDENTIFIER_TYPE_OUTPUT_FILENAME)
    return lookup, ambiguous, identifier_types


def _read_existing_chemical_lookup(
    lookup_path: Path,
    ambiguous_path: Path,
    identifier_type_path: Path,
) -> tuple[pl.DataFrame | None, pl.DataFrame | None, pl.DataFrame | None]:
    if not lookup_path.exists() or not identifier_type_path.exists():
        return None, None, None
    ambiguous = (
        pl.read_parquet(ambiguous_path)
        if ambiguous_path.exists()
        else _empty_chemical_lookup()
    )
    return (
        pl.read_parquet(lookup_path),
        ambiguous,
        pl.read_parquet(identifier_type_path),
    )


def _loaded_chemical_sources(
    lookup: pl.DataFrame | None,
    identifier_types: pl.DataFrame | None,
) -> set[str]:
    if lookup is None or identifier_types is None or lookup.is_empty():
        return set()

    loaded: set[str] = set()
    type_rows = identifier_types.select(
        'identifier_type_id',
        'name',
    ).iter_rows(named=True)
    type_ids = {
        str(row['name']): row['identifier_type_id'] for row in type_rows
    }
    for source, type_name in CHEMICAL_SOURCE_IDENTIFIER_TYPES.items():
        type_id = type_ids.get(type_name)
        if type_id is None:
            continue
        count = lookup.filter(
            pl.col('key_identifier_type_id') == type_id
        ).height
        if count > 0:
            loaded.add(source)
    return loaded


def _combine_existing_chemical_lookup(
    lookup: pl.DataFrame | None,
    ambiguous: pl.DataFrame | None,
) -> pl.DataFrame | None:
    frames = [
        frame
        for frame in (lookup, ambiguous)
        if frame is not None and not frame.is_empty()
    ]
    if not frames:
        return lookup
    return pl.concat(frames, how='vertical_relaxed').unique()


def _chemical_filter_inchikeys(
    lookup_path: str | Path | None,
    sources: Iterable[str] | None = None,
) -> frozenset[str] | None:
    source_values = (
        frozenset(str(source).strip().lower() for source in sources if source)
        if sources is not None
        else frozenset({'chebi', 'hmdb'})
    )
    if not source_values:
        return None
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
                pl.col('source').str.to_lowercase().is_in(sorted(source_values))
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
        source_type_names = sorted(
            CHEMICAL_SOURCE_IDENTIFIER_TYPES[source]
            for source in source_values
            if source in CHEMICAL_SOURCE_IDENTIFIER_TYPES
        )
        if not source_type_names:
            return None
        type_ids = (
            pl.scan_parquet(identifier_type_path)
            .filter(pl.col('name').is_in(source_type_names))
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


def _split_chemical_identifier_lookup(
    rows: Iterable[dict],
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
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
            _empty_chemical_lookup(),
            _empty_chemical_lookup(),
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
    unambiguous, ambiguous = _split_chemical_lookup_frame(lookup)
    return unambiguous, ambiguous, identifier_types


def _empty_chemical_lookup() -> pl.DataFrame:
    return pl.DataFrame(schema=CHEMICAL_IDENTIFIER_LOOKUP_SCHEMA)


def _split_chemical_lookup_frame(
    lookup: pl.DataFrame,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    if lookup.is_empty():
        empty = _empty_chemical_lookup()
        return empty, empty

    join_keys = [
        'key_identifier_type_id',
        'key_value',
        'canonical_identifier_type_id',
    ]
    ambiguous_keys = (
        lookup.group_by(join_keys)
        .agg(
            pl.col('canonical_identifier')
            .n_unique()
            .alias('canonical_identifier_count')
        )
        .filter(pl.col('canonical_identifier_count') > 1)
        .select(join_keys)
    )
    if ambiguous_keys.is_empty():
        return lookup, _empty_chemical_lookup()

    ambiguous = lookup.join(ambiguous_keys, on=join_keys, how='semi')
    unambiguous = lookup.join(ambiguous_keys, on=join_keys, how='anti')
    return unambiguous, ambiguous
