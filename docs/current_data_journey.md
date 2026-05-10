# Current data journey: raw → inputs_v2 → silver → gold → combined → PostgreSQL

This is the current active path through the builder, centered on `pypath/pypath/inputs_v2/` and `omnipath_build/`.

## One-line flow

```text
remote raw files/API responses
  → pypath.inputs_v2 Dataset.raw()
  → pypath Entity / OntologyTerm objects
  → data/silver/<source>/<version>/*.parquet
  → data/gold/<source>/{entities,relations}/*.parquet
  → data/combined/*.parquet
  → PostgreSQL tables, indexes, materialized views, bitmap tables
```

## Pseudocode overview

```python
run_pipeline(sources, data_root="data"):
    paths = {
        silver: data/silver,
        gold: data/gold,
        combined: data/combined,
        reports: data/reports,
    }

    resolver_mappings = build_or_reuse_id_resolver_tables("id_resolver/data")

    for source in sources parallelized:
        silver_dir = build_or_reuse_silver(source)
        gold_dir = build_or_reuse_gold(source, silver_dir, resolver_mappings)

    combined_dir = combine_all_gold_sources(data/gold, data/combined)

    if postgres_uri:
        load_combined_dir_to_postgres(combined_dir, postgres_uri)
```

## 1. Raw data enters through `pypath/pypath/inputs_v2/`

Each source module declares `Resource`, `Dataset`, `OntologyDataset`, or `ArtifactDataset` objects.

```python
# conceptually, inside pypath.inputs_v2.<source>
resource = Resource(
    config=ResourceConfig(...metadata...),
    dataset_name=Dataset(
        download=Download(url=..., filename=..., subfolder=...),
        raw_parser=parse_raw_rows,
        mapper=raw_row_to_silver_entity,
    ),
)
```

At runtime:

```python
Dataset.__call__():
    opener = Download.open()          # downloads/opens cached raw input
    for raw_row in raw_parser(opener):
        yield mapper(raw_row)         # emits pypath.internals.silver_schema.Entity
```

Important points:

- Raw downloads are managed by pypath/download-manager and cached under `pypath-data` by default.
- `Resource.__call__()` emits resource metadata as a CV-term entity.
- `Dataset` emits normalized `Entity` objects.
- `OntologyDataset` emits `OntologyTerm` objects and is also converted into silver CV-term entities.
- `ArtifactDataset` writes non-parquet rendered artifacts and is not part of the main entity/relation warehouse path.

## 2. Silver build: `inputs_v2` objects → columnar silver tables

Entry points:

```bash
uv run python -m omnipath_build.cli.commands silver --source <source>
uv run python -m omnipath_build.cli.commands gold <source>   # runs silver + gold + combine by default
```

Core code:

- `omnipath_build/silver/build.py`
- `omnipath_build/silver/tables.py`
- discovery package default: `pypath.inputs_v2`

Pseudocode:

```python
build_silver_source(source):
    functions = discover_resources("pypath.inputs_v2")

    for function in functions[source]:
        if function == "resource":
            write legacy nested resource.parquet
            continue

        writer = SilverTableWriter(data/silver/<source>/<version>)

        for entity in function.call():
            validate Entity shape
            recursively flatten entity + memberships into silver tables

    write inputs_module_hash.json
    update data/silver/<source>/latest -> {"version": version}
```

Silver physical outputs per source version:

```text
data/silver/<source>/<version>/
  entity_occurrence.parquet
  entity_identifier.parquet
  entity_annotation.parquet
  membership.parquet
  membership_annotation.parquet
  resource.parquet                  # metadata/legacy nested output where present
  inputs_module_hash.json            # used for rebuild/reuse decisions
```

The silver writer turns nested `Entity` structures into five canonical tables:

- `entity_occurrence`: every parent/member entity occurrence from source records
- `entity_identifier`: identifiers attached to occurrences
- `entity_annotation`: annotations attached to occurrences
- `membership`: parent/member links, e.g. interaction participants
- `membership_annotation`: annotations on those membership links

## 3. Resolver mappings: reference tables for canonical IDs

Before gold canonicalization, resolver mapping parquet files are built or reused.

```python
build_resolver_mappings("id_resolver/data"):
    materialize protein_identifier_lookup.parquet
    materialize chemical_identifier_lookup.parquet
```

These long lookup tables map all supported incoming identifiers to stable canonical identifiers:

- proteins → `MI:1097:Uniprot` primary accession
- chemicals/lipids → `MI:2010:Standard Inchi`

## 4. Gold build: per-source silver → per-source entities and relations

Core code:

- `omnipath_build/gold/build_entities.py`
- `omnipath_build/gold/build_relations.py`

Pseudocode:

```python
build_gold_source(source, silver_dir, mapping_dir):
    entities_dir = data/gold/<source>/entities
    relations_dir = data/gold/<source>/relations

    temp_entities, temp_identifiers, occurrence_fingerprint_map = \
        extract_from_silver_tables(silver_dir)

    canonicalized_entities, canonical_identifiers = \
        canonicalize_with_id_resolver(temp_entities, temp_identifiers, mapping_dir)

    final_entities, entity_key_map = deduplicate_by_canonical_id(canonicalized_entities)

    write entities/entity.parquet
    write entities/entity_map.parquet              # fingerprint -> local entity_pk
    write entities/entity_occurrence_map.parquet   # occurrence -> local entity_pk

    build_relations(silver_dir, entity_map):
        classify records as entity_only, interaction_relation, membership_relation, etc.
        project memberships into subject-predicate-object relations
        attach evidence and annotation-derived relations

    write relations/entity_relation.parquet
    write relations/entity_relation_evidence.parquet
    write _SUCCESS.json
```

Per-source gold outputs:

```text
data/gold/<source>/
  entities/
    entity.parquet
    entity_map.parquet
    entity_occurrence_map.parquet
    canonicalization_summary.json
    canonicalization_report.md
  relations/
    entity_relation.parquet
    entity_relation_evidence.parquet
  _SUCCESS.json
  resources/ or archive metadata where generated
```

At this stage IDs are still local to each source (`entity_pk`, `relation_pk` are per-source keys).

## 5. Combined build: all gold sources → global warehouse parquets

Entry point:

```bash
uv run python -m omnipath_build.cli.commands combined \
  --gold-root data/gold \
  --output-dir data/combined
```

Core code: `omnipath_build/gold/combine.py`

Pseudocode:

```python
build_combined_parquets(gold_root=data/gold, output_dir=data/combined):
    source_dirs = find gold sources containing entities/entity.parquet

    combined_entity, entity_pk_map = combine_entities(source_dirs):
        read each source entity.parquet
        group by (canonical_identifier, canonical_identifier_type)
        merge identifiers, attributes, sources
        assign new global entity_pk
        remember (source, local_entity_pk) -> global entity_pk

    combined_relation, relation_pk_map = combine_relations(source_dirs, entity_pk_map):
        read each source entity_relation.parquet
        map local subject/object pks to global entity_pks
        group by (subject, predicate, object, relation_category)
        sum evidence_count, merge sources
        assign new global relation_pk

    combined_evidence = combine_relation_evidence(source_dirs, relation_pk_map)

    build_relation_annotation_term(output_dir)
    build_resources_parquet(gold_root)

    write data/combined/*.parquet
```

Combined outputs:

```text
data/combined/
  entity.parquet
  entity_relation.parquet
  entity_relation_evidence.parquet
  relation_annotation_term.parquet
  resources.parquet
  combined_build_summary.json
```

## 6. PostgreSQL load: combined parquets → relational warehouse

Entry point:

```bash
uv run python -m omnipath_build.cli.commands postgres \
  --output-dir data/combined \
  --postgres-uri postgresql://user:pass@host:5432/db \
  --schema public \
  --drop-existing
```

Core code:

- `omnipath_build/postgres/postgres.py`
- `omnipath_build/postgres/schema.py`
- `omnipath_build/postgres/indexes.py`
- `omnipath_build/postgres/materialized_views.py`
- `omnipath_build/postgres/bitmaps.py`

Pseudocode:

```python
load_combined_schema_to_postgres(output_dir, postgres_uri):
    assert output_dir/entity.parquet exists

    ensure_schema():
        create schema
        create pg_trgm extension
        create base tables

    load_tables():
        truncate existing base tables
        COPY entity.parquet into:
            entity
            entity_identifier       # exploded from entity.identifiers
        COPY entity_relation.parquet into entity_relation
        COPY entity_relation_evidence.parquet into entity_relation_evidence
        COPY relation_annotation_term.parquet into relation_annotation_term
        COPY resources.parquet into resources

    create_secondary_indexes()
    create_materialized_views()
    create_and_populate_bitmap_tables()
```

PostgreSQL base tables:

- `entity`
- `entity_identifier`
- `entity_relation`
- `entity_relation_evidence`
- `relation_annotation_term`
- `resources`

## Main orchestration knobs

The active pipeline CLI is `omnipath_build.pipeline.pipeline` via the `gold` command:

```bash
uv run python -m omnipath_build.cli.commands gold <source...> \
  --data-root data \
  --inputs-package pypath.inputs_v2 \
  --resolver-mapping-dir id_resolver/data \
  --jobs 4
```

By default this runs mappings, selected source silver/gold builds, and combined output. If `--postgres-uri` is passed to the pipeline layer, it also schedules the PostgreSQL load after combine.

Reuse/freshness behavior:

- Silver is versioned: `data/silver/<source>/<version>`.
- `data/silver/<source>/latest` points to the selected version.
- `inputs_module_hash.json` lets the pipeline reuse silver when the `inputs_v2` source code has not changed.
- Gold can be reused when silver was reused and `data/gold/<source>/_SUCCESS.json` exists.
- `--overwrite silver`, `--overwrite gold`, or `--overwrite` force rebuilds.
