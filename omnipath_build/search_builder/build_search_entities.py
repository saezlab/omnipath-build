"""Build Meilisearch entity documents from global tables.

This script aggregates normalized global tables into denormalized Meilisearch documents:

Input (global tables):
- entity.parquet (entity_id, entity_type_id)
- entity_identifier.parquet (id, entity_id, type_id, identifier)
- entity_identifier_resource.parquet (id, entity_identifier_id, source_entity_id)
- membership.parquet (id, parent_id, member_id, annotation_value, annotation_unit, source_id)
- membership_annotation.parquet (if available)

Output:
- search_entities.parquet with columns:
  - entity_id: int
  - entity_type: str (formatted as "Label:entity_id" like "Protein:385235")
  - names: list[str]
  - synonyms: list[str]
  - gene_symbols: list[str]
  - descriptions: list[str]
  - references: list[str]
  - identifiers: str (JSON string mapping "type_name:type_id" to value, excludes names/synonyms/gene_symbols)
  - sources: list[str] (formatted as "source_name:source_id")
  - complexes: list[int] (entity_ids of complexes this entity is part of)
  - cv_terms: list[int] (entity_ids of CV terms annotating this entity)
  - num_interactions: int
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
import polars as pl

from .schema import (
    build_cv_term_mapping,
    build_accession_to_entity_id_sets,
    format_entity_type_label,
)

__all__ = ["build_search_entities"]

logger = logging.getLogger(__name__)


def build_search_entities(
    global_tables_dir: Path,
    output_path: Path,
) -> Path:
    """Build Meilisearch entity documents from global tables.

    Args:
        global_tables_dir: Directory containing global table parquet files
        output_path: Output path for search_entities.parquet

    Returns:
        Path to the created search_entities.parquet file
    """
    logger.info("=" * 80)
    logger.info("Building Meilisearch entity documents from global tables")
    logger.info("=" * 80)

    # Load global tables
    logger.info("Loading global tables from %s", global_tables_dir)
    entities = pl.read_parquet(global_tables_dir / "entity.parquet")
    identifiers = pl.read_parquet(global_tables_dir / "entity_identifier.parquet")
    identifier_resources = pl.read_parquet(global_tables_dir / "entity_identifier_resource.parquet")
    memberships = pl.read_parquet(global_tables_dir / "membership.parquet")

    logger.info("  Entities: %s", f"{len(entities):,}")
    logger.info("  Identifiers: %s", f"{len(identifiers):,}")
    logger.info("  Identifier Resources: %s", f"{len(identifier_resources):,}")
    logger.info("  Memberships: %s", f"{len(memberships):,}")

    # Build CV term mapping (accession -> entity_id)
    logger.info("Building CV term mapping")
    cv_term_mapping = build_cv_term_mapping(global_tables_dir / "entity_identifier.parquet")
    logger.info("  Mapped %s CV terms", f"{len(cv_term_mapping):,}")

    # Convert accession sets to entity_id sets for filtering
    id_sets = build_accession_to_entity_id_sets(cv_term_mapping)
    logger.info("  ID sets built: %s", list(id_sets.keys()))

    # Filter out interactions (we only want non-interaction entities)
    interaction_type_id = id_sets['interaction_type']
    non_interaction_entities = entities.filter(pl.col('entity_type_id') != interaction_type_id)
    logger.info("Filtered out interactions: %s non-interaction entities remaining", f"{len(non_interaction_entities):,}")

    # Build CV term accession lookup (entity_id -> accession)
    cv_term_accessions = cv_term_mapping.select([
        pl.col('entity_id').alias('cv_entity_id'),
        pl.col('accession'),
    ])

    # Join entity types with accessions to get formatted labels with IDs
    entities_with_labels = (
        non_interaction_entities
        .join(
            cv_term_accessions.rename({'cv_entity_id': 'entity_type_id'}),
            on='entity_type_id',
            how='left'
        )
        .with_columns([
            # Format as "Label:entity_id" (e.g., "Protein:385235")
            (
                pl.col('accession').map_elements(
                    format_entity_type_label,
                    return_dtype=pl.Utf8
                ) + pl.lit(':') + pl.col('entity_type_id').cast(pl.Utf8)
            ).alias('entity_type')
        ])
        .select(['entity_id', 'entity_type'])
    )

    # Aggregate identifiers by category
    logger.info("Aggregating identifiers by category")
    names = _aggregate_identifiers_by_type(identifiers, id_sets['names'], 'names')
    synonyms = _aggregate_identifiers_by_type(identifiers, id_sets['synonyms'], 'synonyms')
    gene_symbols = _aggregate_identifiers_by_type(identifiers, id_sets['gene_symbols'], 'gene_symbols')

    # Aggregate descriptions from memberships
    logger.info("Aggregating descriptions")
    descriptions = _aggregate_descriptions(memberships, id_sets['descriptions'])

    # Aggregate references from memberships
    logger.info("Aggregating references")
    references = _aggregate_references(memberships, id_sets['references'])

    # Collect all identifiers as dict (excluding names, synonyms, gene_symbols)
    logger.info("Collecting all identifiers")
    excluded_type_ids = id_sets['names'] | id_sets['synonyms'] | id_sets['gene_symbols']
    all_identifiers_agg = _collect_all_identifiers(identifiers, cv_term_accessions, identifiers, excluded_type_ids)

    # Aggregate sources
    logger.info("Aggregating sources")
    sources = _aggregate_sources(identifier_resources, identifiers, cv_term_accessions)

    # Aggregate memberships
    logger.info("Aggregating memberships")
    complex_memberships = _aggregate_membership_parents(
        memberships,
        entities,
        id_sets['complex_type'],
        'complexes'
    )
    cv_term_memberships = _aggregate_membership_parents(
        memberships,
        entities,
        id_sets['cv_term_type'],
        'cv_terms'
    )

    # Count interactions
    logger.info("Counting interactions")
    interaction_counts = _count_interaction_memberships(memberships, entities, interaction_type_id)

    # Join everything together
    logger.info("Assembling final Meilisearch documents")
    search_entities = (
        entities_with_labels
        .join(names, on='entity_id', how='left')
        .join(synonyms, on='entity_id', how='left')
        .join(gene_symbols, on='entity_id', how='left')
        .join(descriptions, on='entity_id', how='left')
        .join(references, on='entity_id', how='left')
        .join(all_identifiers_agg, on='entity_id', how='left')
        .join(sources, on='entity_id', how='left')
        .join(complex_memberships, on='entity_id', how='left')
        .join(cv_term_memberships, on='entity_id', how='left')
        .join(interaction_counts, on='entity_id', how='left')
    )

    # Fill nulls with empty lists/defaults
    search_entities = _fill_defaults(search_entities)

    # Sort by entity_id
    search_entities = search_entities.sort('entity_id')

    # Write output
    logger.info("Writing %s search entities to %s", f"{len(search_entities):,}", output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    search_entities.write_parquet(output_path)

    logger.info("=" * 80)
    logger.info("Search entity building complete!")
    logger.info("=" * 80)

    return output_path


# =============================================================================
# Helper functions
# =============================================================================

def _aggregate_identifiers_by_type(
    identifiers: pl.DataFrame,
    type_ids: frozenset[int],
    column_name: str,
) -> pl.DataFrame:
    """Aggregate identifier values for entities with specific type_ids.

    Args:
        identifiers: DataFrame with [id, entity_id, type_id, identifier]
        type_ids: Set of type_id values to filter for
        column_name: Output column name

    Returns:
        DataFrame with [entity_id, {column_name}] where column is list of identifiers
    """
    if not type_ids:
        return pl.DataFrame(schema={'entity_id': pl.Int64, column_name: pl.List(pl.Utf8)})

    filtered = identifiers.filter(pl.col('type_id').is_in(list(type_ids)))

    if len(filtered) == 0:
        return pl.DataFrame(schema={'entity_id': pl.Int64, column_name: pl.List(pl.Utf8)})

    return (
        filtered
        .group_by('entity_id')
        .agg(pl.col('identifier').unique().sort())
        .rename({'identifier': column_name})
    )


def _collect_all_identifiers(
    identifiers: pl.DataFrame,
    cv_term_accessions: pl.DataFrame,
    all_identifiers: pl.DataFrame,
    excluded_type_ids: frozenset[int],
) -> pl.DataFrame:
    """Collect all identifiers as JSON string mapping "type_name:type_id" to value.

    Args:
        identifiers: DataFrame with [id, entity_id, type_id, identifier]
        cv_term_accessions: DataFrame with [cv_entity_id, accession]
        all_identifiers: Full identifiers DataFrame to lookup names for identifier types
        excluded_type_ids: Set of type_ids to exclude (names, synonyms, gene_symbols)

    Returns:
        DataFrame with [entity_id, identifiers] where identifiers is a JSON string like:
        '{"uniprot:3874827": "P0A6M2", "chebi:3874830": "CHEBI:15377"}'
    """
    # Filter out excluded identifier types
    filtered_identifiers = identifiers.filter(~pl.col('type_id').is_in(list(excluded_type_ids)))

    if len(filtered_identifiers) == 0:
        return pl.DataFrame(schema={'entity_id': pl.Int64, 'identifiers': pl.Utf8})

    # Build a mapping from type_id (entity_id of identifier type) to its name
    # Identifier types have a NAME identifier themselves
    name_type_id = cv_term_accessions.filter(
        pl.col('accession') == 'OM:0202'  # NAME identifier type
    )

    if len(name_type_id) > 0:
        name_type_entity_id = name_type_id['cv_entity_id'][0]

        # Get names for all identifier types
        type_names = (
            all_identifiers
            .filter(pl.col('type_id') == name_type_entity_id)
            .select([
                pl.col('entity_id').alias('type_entity_id'),
                pl.col('identifier').alias('type_name'),
            ])
        )
    else:
        # Fallback if NAME type not found
        type_names = pl.DataFrame(schema={'type_entity_id': pl.Int64, 'type_name': pl.Utf8})

    # Join identifiers with type names
    identifiers_with_types = (
        filtered_identifiers
        .join(
            type_names.rename({'type_entity_id': 'type_id'}),
            on='type_id',
            how='left'
        )
        # Fallback to accession if name not found
        .join(
            cv_term_accessions.rename({'cv_entity_id': 'type_id'}),
            on='type_id',
            how='left'
        )
        .with_columns([
            pl.when(pl.col('type_name').is_not_null())
              .then(pl.col('type_name'))
              .otherwise(pl.col('accession'))
              .alias('type_display')
        ])
        # Create the dict key as "type_name:type_id"
        .with_columns([
            (pl.col('type_display') + pl.lit(':') + pl.col('type_id').cast(pl.Utf8)).alias('dict_key')
        ])
    )

    # Group by entity_id and create JSON string using map_elements
    def create_json_dict(keys: list[str], values: list[str]) -> str:
        """Create JSON string from parallel lists of keys and values."""
        return json.dumps(dict(zip(keys, values)))

    return (
        identifiers_with_types
        .group_by('entity_id')
        .agg([
            pl.col('dict_key'),
            pl.col('identifier'),
        ])
        .with_columns([
            pl.struct(['dict_key', 'identifier'])
              .map_elements(
                  lambda s: create_json_dict(s['dict_key'], s['identifier']),
                  return_dtype=pl.Utf8
              )
              .alias('identifiers')
        ])
        .select(['entity_id', 'identifiers'])
    )


def _aggregate_sources(
    identifier_resources: pl.DataFrame,
    identifiers: pl.DataFrame,
    cv_term_accessions: pl.DataFrame,
) -> pl.DataFrame:
    """Aggregate source entities formatted as "name:id" for each entity.

    Args:
        identifier_resources: DataFrame with [id, entity_identifier_id, source_entity_id]
        identifiers: DataFrame with [id, entity_id, type_id, identifier]
        cv_term_accessions: DataFrame with [cv_entity_id, accession]

    Returns:
        DataFrame with [entity_id, sources] where sources is list of "source_name:source_id" strings
    """
    # Join to get entity_id for each identifier
    with_entity_id = identifier_resources.join(
        identifiers.select(['id', 'entity_id']).rename({'id': 'entity_identifier_id'}),
        on='entity_identifier_id',
        how='inner'
    )

    # Get NAME type id to lookup source names
    name_type_id = cv_term_accessions.filter(
        pl.col('accession') == 'OM:0202'  # NAME identifier type
    )

    if len(name_type_id) > 0:
        name_type_entity_id = name_type_id['cv_entity_id'][0]

        # Get names for source entities
        source_names = (
            identifiers
            .filter(pl.col('type_id') == name_type_entity_id)
            .select([
                pl.col('entity_id').alias('source_entity_id'),
                pl.col('identifier').alias('source_name'),
            ])
            # Take first name if multiple
            .group_by('source_entity_id')
            .agg(pl.col('source_name').first())
        )

        # Join with source names and format as "name:id"
        with_source_names = (
            with_entity_id
            .join(source_names, on='source_entity_id', how='left')
            .with_columns([
                pl.when(pl.col('source_name').is_not_null())
                  .then(pl.col('source_name') + pl.lit(':') + pl.col('source_entity_id').cast(pl.Utf8))
                  .otherwise(pl.lit('Unknown:') + pl.col('source_entity_id').cast(pl.Utf8))
                  .alias('source_formatted')
            ])
        )
    else:
        # Fallback if NAME type not found
        with_source_names = (
            with_entity_id
            .with_columns([
                (pl.lit('Source:') + pl.col('source_entity_id').cast(pl.Utf8)).alias('source_formatted')
            ])
        )

    # Aggregate formatted sources per entity
    return (
        with_source_names
        .group_by('entity_id')
        .agg(pl.col('source_formatted').unique().sort())
        .rename({'source_formatted': 'sources'})
    )


def _aggregate_descriptions(
    memberships: pl.DataFrame,
    description_type_ids: frozenset[int],
) -> pl.DataFrame:
    """Aggregate description annotation values for entities.

    Args:
        memberships: DataFrame with [id, parent_id, member_id, annotation_value, ...]
        description_type_ids: Set of entity_ids for description CV terms

    Returns:
        DataFrame with [entity_id, descriptions] where descriptions is list of description texts
    """
    if not description_type_ids:
        return pl.DataFrame(schema={'entity_id': pl.Int64, 'descriptions': pl.List(pl.Utf8)})

    # Filter memberships where parent is a description CV term
    description_memberships = memberships.filter(
        pl.col('parent_id').is_in(list(description_type_ids))
    )

    if len(description_memberships) == 0:
        return pl.DataFrame(schema={'entity_id': pl.Int64, 'descriptions': pl.List(pl.Utf8)})

    # Aggregate annotation_value per member_id
    return (
        description_memberships
        .filter(pl.col('annotation_value').is_not_null())
        .group_by('member_id')
        .agg(pl.col('annotation_value').unique().sort())
        .rename({'member_id': 'entity_id', 'annotation_value': 'descriptions'})
    )


def _aggregate_references(
    memberships: pl.DataFrame,
    reference_type_ids: frozenset[int],
) -> pl.DataFrame:
    """Aggregate reference annotation values for entities.

    Args:
        memberships: DataFrame with [id, parent_id, member_id, annotation_value, ...]
        reference_type_ids: Set of entity_ids for reference CV terms

    Returns:
        DataFrame with [entity_id, references] where references is list of reference identifiers
    """
    if not reference_type_ids:
        return pl.DataFrame(schema={'entity_id': pl.Int64, 'references': pl.List(pl.Utf8)})

    # Filter memberships where parent is a reference CV term
    reference_memberships = memberships.filter(
        pl.col('parent_id').is_in(list(reference_type_ids))
    )

    if len(reference_memberships) == 0:
        return pl.DataFrame(schema={'entity_id': pl.Int64, 'references': pl.List(pl.Utf8)})

    # Aggregate annotation_value per member_id
    return (
        reference_memberships
        .filter(pl.col('annotation_value').is_not_null())
        .group_by('member_id')
        .agg(pl.col('annotation_value').unique().sort())
        .rename({'member_id': 'entity_id', 'annotation_value': 'references'})
    )


def _aggregate_membership_parents(
    memberships: pl.DataFrame,
    entities: pl.DataFrame,
    parent_type_id: int,
    column_name: str,
) -> pl.DataFrame:
    """Aggregate parent entity IDs of a specific type.

    Args:
        memberships: DataFrame with [id, parent_id, member_id, ...]
        entities: DataFrame with [entity_id, entity_type_id]
        parent_type_id: Entity type_id to filter parents by
        column_name: Output column name

    Returns:
        DataFrame with [entity_id, {column_name}] mapping members to list of parent IDs
    """
    # Join to get parent entity types
    memberships_with_types = memberships.join(
        entities.rename({'entity_id': 'parent_id', 'entity_type_id': 'parent_type_id'}),
        on='parent_id',
        how='inner'
    )

    # Filter by parent type
    filtered = memberships_with_types.filter(pl.col('parent_type_id') == parent_type_id)

    if len(filtered) == 0:
        return pl.DataFrame(schema={'entity_id': pl.Int64, column_name: pl.List(pl.Int64)})

    # Aggregate parent IDs per member
    return (
        filtered
        .group_by('member_id')
        .agg(pl.col('parent_id').unique().sort())
        .rename({'member_id': 'entity_id', 'parent_id': column_name})
    )


def _count_interaction_memberships(
    memberships: pl.DataFrame,
    entities: pl.DataFrame,
    interaction_type_id: int,
) -> pl.DataFrame:
    """Count how many interactions each entity participates in.

    Args:
        memberships: DataFrame with [id, parent_id, member_id, ...]
        entities: DataFrame with [entity_id, entity_type_id]
        interaction_type_id: Entity type_id for interactions

    Returns:
        DataFrame with [entity_id, num_interactions]
    """
    # Join to get parent entity types
    memberships_with_types = memberships.join(
        entities.rename({'entity_id': 'parent_id', 'entity_type_id': 'parent_type_id'}),
        on='parent_id',
        how='inner'
    )

    # Filter by interaction type
    interaction_memberships = memberships_with_types.filter(
        pl.col('parent_type_id') == interaction_type_id
    )

    if len(interaction_memberships) == 0:
        return pl.DataFrame(schema={'entity_id': pl.Int64, 'num_interactions': pl.Int64})

    # Count unique interactions per member
    return (
        interaction_memberships
        .group_by('member_id')
        .agg(pl.col('parent_id').n_unique())
        .rename({'member_id': 'entity_id', 'parent_id': 'num_interactions'})
    )


def _fill_defaults(df: pl.DataFrame) -> pl.DataFrame:
    """Fill null values with appropriate defaults.

    Args:
        df: DataFrame with potential null values in list/int columns

    Returns:
        DataFrame with nulls filled
    """
    return df.with_columns([
        pl.col('names').fill_null(pl.lit([], dtype=pl.List(pl.Utf8))),
        pl.col('synonyms').fill_null(pl.lit([], dtype=pl.List(pl.Utf8))),
        pl.col('gene_symbols').fill_null(pl.lit([], dtype=pl.List(pl.Utf8))),
        pl.col('descriptions').fill_null(pl.lit([], dtype=pl.List(pl.Utf8))),
        pl.col('references').fill_null(pl.lit([], dtype=pl.List(pl.Utf8))),
        # identifiers is now a JSON string, fill with empty JSON object
        pl.col('identifiers').fill_null(pl.lit('{}', dtype=pl.Utf8)),
        # sources is now a list of strings (formatted as "name:id")
        pl.col('sources').fill_null(pl.lit([], dtype=pl.List(pl.Utf8))),
        pl.col('complexes').fill_null(pl.lit([], dtype=pl.List(pl.Int64))),
        pl.col('cv_terms').fill_null(pl.lit([], dtype=pl.List(pl.Int64))),
        pl.col('num_interactions').fill_null(0),
    ])
