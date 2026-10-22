"""The exclusion metabolite product over a small published release."""

from contextlib import closing
import hashlib
import subprocess
import uuid

import psycopg2
from psycopg2 import sql
import pytest

from omnipath_subsets.exclusion_metabolites import build, resolve, seeds, version

CLASSES = ("ions", "atoms", "small_molecules", "xenobiotics", "cofactors")
SOURCES = ("metaproviz_seed", "project_seed", "override_add", "chebi_hierarchy", "chebi_role")


def write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def seed_directory(tmp_path, *, metaproviz=None, cofactors=None, overrides=None):
    write(
        tmp_path / "metaproviz_seed.yaml",
        metaproviz
        or """
source: {repository: saezlab/MetaProViz, commit: abc, licence: BSD-3-Clause}
seeds:
  - {class: ions, namespace: kegg, ids: [C00076, C00238]}
  - {class: atoms, namespace: chebi, ids: ["CHEBI:18248"]}
""",
    )
    write(
        tmp_path / "project_cofactors.yaml",
        cofactors
        or """
class: cofactors
namespace: chebi
members:
  - {id: "CHEBI:57540", name: NAD}
""",
    )
    write(
        tmp_path / "overrides.yaml",
        overrides
        or """
add: [{class: ions, namespace: chebi, id: "CHEBI:29101", note: a curator adds it}]
veto: [{class: atoms, namespace: chebi, id: "CHEBI:18248", note: a curator removes it}]
""",
    )
    return tmp_path


# ---------------------------------------------------------------------------
# Seed files


def test_seeds_are_read_from_all_three_files_with_their_origin(tmp_path):
    loaded = seeds.read_seeds(seed_directory(tmp_path))
    found = {
        (s.exclusion_class, s.namespace, s.identifier, s.assignment_source, s.source_file)
        for s in loaded.seeds
    }
    assert found == {
        ("ions", "kegg", "C00076", "metaproviz_seed", "metaproviz_seed.yaml"),
        ("ions", "kegg", "C00238", "metaproviz_seed", "metaproviz_seed.yaml"),
        ("atoms", "chebi", "CHEBI:18248", "metaproviz_seed", "metaproviz_seed.yaml"),
        ("cofactors", "chebi", "CHEBI:57540", "project_seed", "project_cofactors.yaml"),
        ("ions", "chebi", "CHEBI:29101", "override_add", "overrides.yaml"),
    }
    assert [(v.exclusion_class, v.identifier) for v in loaded.vetoes] == [("atoms", "CHEBI:18248")]
    assert [path.name for path in loaded.paths] == [
        "metaproviz_seed.yaml",
        "project_cofactors.yaml",
        "overrides.yaml",
    ]


def test_empty_project_files_give_no_seeds(tmp_path):
    directory = seed_directory(tmp_path)
    write(directory / "project_cofactors.yaml", "# only a header\n")
    write(directory / "overrides.yaml", "# only a header\n")
    loaded = seeds.read_seeds(directory)
    assert {s.assignment_source for s in loaded.seeds} == {"metaproviz_seed"}
    assert loaded.vetoes == []


@pytest.mark.parametrize(
    "metaproviz, message",
    [
        (
            "seeds:\n  - {class: lipids, namespace: kegg, ids: [C00001]}\n",
            "lipids",
        ),
        (
            "seeds:\n  - {class: ions, namespace: wikidata, ids: [Q1]}\n",
            "wikidata",
        ),
        (
            "seeds:\n  - {class: ions, namespace: kegg, ids: [C00001, C00001]}\n",
            "duplicate",
        ),
        (
            "seeds:\n"
            "  - {class: ions, namespace: kegg, ids: [C00001]}\n"
            "  - {class: ions, namespace: kegg, ids: [C00001]}\n",
            "duplicate",
        ),
        (
            "seeds:\n  - {class: ions, namespace: kegg, ids: ['']}\n",
            "empty",
        ),
        (
            "seeds:\n  - {class: ions, namespace: kegg, ids: ['  ']}\n",
            "empty",
        ),
    ],
)
def test_a_malformed_seed_file_is_refused_with_the_reason(tmp_path, metaproviz, message):
    directory = seed_directory(tmp_path, metaproviz=metaproviz)
    with pytest.raises(seeds.SeedError, match=message):
        seeds.read_seeds(directory)


def test_a_veto_with_an_unknown_class_is_refused(tmp_path):
    directory = seed_directory(
        tmp_path,
        overrides="veto: [{class: nonsense, namespace: chebi, id: 'CHEBI:1', note: x}]\n",
    )
    with pytest.raises(seeds.SeedError, match="nonsense"):
        seeds.read_seeds(directory)


def test_the_vendored_metaproviz_seed_reads_and_keeps_its_provenance():
    loaded = seeds.read_seeds()
    counts = {}
    for seed in loaded.seeds:
        if seed.assignment_source == "metaproviz_seed":
            key = (seed.exclusion_class, seed.namespace)
            counts[key] = counts.get(key, 0) + 1
    assert counts == {
        ("ions", "kegg"): 14,
        ("small_molecules", "kegg"): 16,
        ("ions", "chebi"): 187,
        ("atoms", "chebi"): 280,
    }
    assert loaded.source["repository"] == "saezlab/MetaProViz"
    assert len(loaded.source["commit"]) == 40
    assert loaded.source["licence"] == "BSD-3-Clause"


# ---------------------------------------------------------------------------
# Identifier spellings


@pytest.mark.parametrize(
    "namespace, identifier, expected",
    [
        ("chebi", "CHEBI:15377", {"CHEBI:15377", "15377"}),
        ("chebi", "15377", {"CHEBI:15377", "15377"}),
        ("chebi", "chebi:15377", {"CHEBI:15377", "15377"}),
        ("hmdb", "HMDB0000001", {"HMDB0000001", "HMDB00001"}),
        ("hmdb", "HMDB00008", {"HMDB0000008", "HMDB00008"}),
        ("kegg", "C00001", {"C00001"}),
        ("pubchem", "15377", {"15377"}),
    ],
)
def test_a_seed_is_looked_up_under_each_spelling_the_release_may_hold(
    namespace, identifier, expected
):
    assert set(resolve.spellings(namespace, identifier)) == expected


# ---------------------------------------------------------------------------
# List version


def git(directory, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.org", *args],
        cwd=directory,
        check=True,
        capture_output=True,
    )


def committed(directory):
    git(directory, "init", "-q")
    git(directory, "add", ".")
    git(directory, "commit", "-q", "-m", "seed files")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=directory, check=True, capture_output=True, text=True
    )
    return head.stdout.strip()


def digest(paths):
    sha = hashlib.sha256()
    for path in sorted(paths, key=lambda p: p.name):
        sha.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return sha.hexdigest()


def test_committed_seed_files_are_versioned_by_their_commit(tmp_path):
    directory = seed_directory(tmp_path)
    head = committed(directory)
    paths = seeds.read_seeds(directory).paths
    assert version.list_version(paths) == version.ListVersion(head, False)


def test_an_edited_seed_file_is_versioned_by_its_content_hash(tmp_path):
    directory = seed_directory(tmp_path)
    committed(directory)
    write(directory / "overrides.yaml", "add: []\nveto: []\n")
    paths = seeds.read_seeds(directory).paths
    result = version.list_version(paths)
    assert result.uncommitted is True
    assert result.value == "uncommitted:" + digest(paths)


def test_seed_files_outside_a_repository_are_versioned_by_their_content_hash(tmp_path):
    directory = seed_directory(tmp_path)
    paths = seeds.read_seeds(directory).paths
    result = version.list_version(paths)
    assert result == version.ListVersion("uncommitted:" + digest(paths), True)


def test_the_hash_changes_with_the_content_and_not_with_the_run(tmp_path):
    directory = seed_directory(tmp_path)
    paths = seeds.read_seeds(directory).paths
    first = version.list_version(paths)
    assert version.list_version(paths) == first
    write(directory / "project_cofactors.yaml", "members: []\n")
    assert version.list_version(paths).value != first.value


# ---------------------------------------------------------------------------
# A small published release


def ident(index):
    return str(uuid.UUID(int=index))


WATER, CO2, PROTON, FLUORIDE, IRON, COLLIDER, FALLBACK, TWIN_A, TWIN_B, PROTEIN = (
    ident(i) for i in range(1, 11)
)
HMDB_LONG, HMDB_SHORT = ident(11), ident(12)
FIXTURE_BUILD = "a" * 64

IDENTIFIER_TYPES = {
    1: "Chebi:MI:0474",
    2: "Kegg Compound:MI:2012",
    3: "Hmdb:OM:0004",
    4: "Pubchem Compound:OM:0002",
    5: "Name:OM:0202",
}

ENTITIES = [
    # entity, type, canonical type, canonical identifier, label
    (WATER, 3, 1, "CHEBI:15377", "water"),
    (CO2, 3, 2, "C00011", "carbon dioxide"),
    (PROTON, 2, 1, "CHEBI:15378", "hydron"),
    (FLUORIDE, 2, 1, "17051", "fluoride"),
    (IRON, 2, 1, "CHEBI:18248", "iron atom"),
    (COLLIDER, 3, 4, "15378", "a compound whose PubChem id equals a ChEBI number"),
    (FALLBACK, 3, 5, "pyrophosphate-like", "a name-only identity"),
    (TWIN_A, 3, 2, "C00027", "twin a"),
    (TWIN_B, 3, 2, "C00027x", "twin b"),
    (PROTEIN, 4, 1, "CHEBI:29101", "a protein that carries a ChEBI value"),
    (HMDB_LONG, 3, 3, "HMDB0000001", "one-methylhistidine"),
    (HMDB_SHORT, 3, 3, "HMDB00008", "2-hydroxybutyric acid"),
]

# entity, identifier type, value as the release holds it
IDENTIFIERS = [
    (WATER, 1, "CHEBI:15377"),
    (WATER, 2, "C00001"),
    (CO2, 2, "C00011"),
    (PROTON, 1, "CHEBI:15378"),
    (FLUORIDE, 1, "17051"),
    (IRON, 1, "CHEBI:18248"),
    (COLLIDER, 4, "15378"),
    (FALLBACK, 5, "pyrophosphate-like"),
    (FALLBACK, 2, "C00013"),
    (TWIN_A, 2, "C00027"),
    (TWIN_B, 2, "C00027"),
    (PROTEIN, 1, "CHEBI:29101"),
    (HMDB_LONG, 3, "HMDB0000001"),
    (HMDB_SHORT, 3, "HMDB00008"),
]


@pytest.fixture
def release(postgres_dsn):
    """A release schema holding only the tables the product reads."""
    schema = "exclusion_" + uuid.uuid4().hex
    with closing(psycopg2.connect(postgres_dsn)) as connection:
        try:
            with connection.cursor() as cur:
                cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
                cur.execute(sql.SQL("SET search_path={},public").format(sql.Identifier(schema)))
                cur.execute(
                    """
                    CREATE TABLE parquet_release(singleton boolean PRIMARY KEY DEFAULT true,
                      version text, manifest_sha256 text);
                    CREATE TABLE vocab_entity_type(entity_type_id bigint PRIMARY KEY, name text);
                    CREATE TABLE vocab_identifier_type(identifier_type_id bigint PRIMARY KEY,
                      name text);
                    CREATE TABLE entity(entity_id uuid PRIMARY KEY, entity_type_id bigint,
                      canonical_identifier_type_id bigint, canonical_identifier text,
                      label text);
                    CREATE TABLE identifier_evidence(identifier_id uuid PRIMARY KEY,
                      identifier_type_id bigint, value text, value_normalized text);
                    CREATE TABLE entity_identifier_lookup(entity_id uuid, identifier_id uuid);
                    CREATE TABLE data_source(source_id bigint PRIMARY KEY, name text);
                    CREATE TABLE vocab_relation_predicate(relation_predicate_id bigint PRIMARY KEY,
                      name text);
                    CREATE TABLE entity_ontology_relation(source_id bigint,
                      subject_entity_id uuid, predicate_id bigint, object_entity_id uuid,
                      ontology_id text);
                    CREATE TABLE chemical_resolution_group_member(level_id smallint,
                      group_key text, entity_id uuid, inchikey text);
                    """
                )
                cur.execute(
                    "INSERT INTO parquet_release(version, manifest_sha256) VALUES ('fixture', %s)",
                    [FIXTURE_BUILD],
                )
                cur.executemany(
                    "INSERT INTO vocab_entity_type VALUES (%s, %s)",
                    [(1, "protein"), (2, "chemical_entity"), (3, "small_molecule"), (4, "gene")],
                )
                cur.executemany(
                    "INSERT INTO vocab_identifier_type VALUES (%s, %s)",
                    list(IDENTIFIER_TYPES.items()),
                )
                cur.executemany("INSERT INTO entity VALUES (%s,%s,%s,%s,%s)", ENTITIES)
                for number, (entity, type_id, value) in enumerate(IDENTIFIERS, start=1):
                    cur.execute(
                        "INSERT INTO identifier_evidence VALUES (%s,%s,%s,%s)",
                        [ident(1000 + number), type_id, value, value],
                    )
                    cur.execute(
                        "INSERT INTO entity_identifier_lookup VALUES (%s,%s)",
                        [entity, ident(1000 + number)],
                    )
            connection.commit()
            yield connection, schema
        finally:
            connection.rollback()
            with connection.cursor() as cur:
                cur.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
            connection.commit()


def cursor_of(release):
    connection, schema = release
    cur = connection.cursor()
    cur.execute(sql.SQL("SET search_path={},public").format(sql.Identifier(schema)))
    return cur


# ---------------------------------------------------------------------------
# The tables


def columns(cur, table):
    cur.execute(
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = %s ORDER BY ordinal_position",
        [table],
    )
    return cur.fetchall()


def test_the_product_creates_both_tables_with_the_contracted_shape(release):
    connection, schema = release
    build.ensure_tables(connection, schema=schema)
    cur = cursor_of(release)
    assert columns(cur, "exclusion_metabolite") == [
        ("entity_id", "uuid", "NO"),
        ("exclusion_class", "text", "NO"),
        ("assignment_source", "text", "NO"),
        ("origin", "text", "NO"),
        ("match_kind", "text", "NO"),
        ("seed_namespace", "text", "YES"),
        ("seed_identifier", "text", "YES"),
        ("evidence", "text", "NO"),
        ("via_entity_id", "uuid", "YES"),
        ("list_version", "text", "NO"),
        ("build_id", "text", "NO"),
    ]
    assert columns(cur, "exclusion_metabolite_report") == [
        ("kind", "text", "NO"),
        ("exclusion_class", "text", "YES"),
        ("namespace", "text", "YES"),
        ("resolved", "integer", "YES"),
        ("fallback_only", "integer", "YES"),
        ("unresolved", "integer", "YES"),
        ("detail", "jsonb", "YES"),
        ("build_id", "text", "NO"),
    ]
    cur.execute(
        "SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid "
        "AND a.attnum = ANY(i.indkey) WHERE i.indrelid = 'exclusion_metabolite'::regclass "
        "AND i.indisprimary ORDER BY array_position(i.indkey::int[], a.attnum::int)"
    )
    assert [row[0] for row in cur.fetchall()] == [
        "entity_id",
        "exclusion_class",
        "assignment_source",
    ]
    cur.execute(
        "SELECT pg_get_indexdef(indexrelid) FROM pg_index "
        "WHERE indrelid = 'exclusion_metabolite'::regclass AND NOT indisprimary"
    )
    definitions = [row[0] for row in cur.fetchall()]
    assert any("(exclusion_class, entity_id)" in definition for definition in definitions)


ROW = (
    "INSERT INTO exclusion_metabolite(entity_id, exclusion_class, assignment_source, origin, "
    "match_kind, evidence, via_entity_id, list_version, build_id) "
    "VALUES (%s, %s, %s, %s, %s, 'e', %s, 'v', 'b')"
)


@pytest.mark.parametrize(
    "row",
    [
        (WATER, "lipids", "metaproviz_seed", "curated", "entity", None),
        (WATER, "ions", "somewhere", "curated", "entity", None),
        (WATER, "ions", "metaproviz_seed", "invented", "entity", None),
        (WATER, "ions", "metaproviz_seed", "curated", "fuzzy", None),
        (WATER, "ions", "metaproviz_seed", "curated", "structure_key", None),
    ],
)
def test_the_table_refuses_values_outside_its_vocabularies(release, row):
    connection, schema = release
    build.ensure_tables(connection, schema=schema)
    cur = cursor_of(release)
    with pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute(ROW, row)


def test_the_table_accepts_every_class_and_a_key_row_with_its_member(release):
    connection, schema = release
    build.ensure_tables(connection, schema=schema)
    cur = cursor_of(release)
    for exclusion_class in CLASSES:
        cur.execute(ROW, (WATER, exclusion_class, "metaproviz_seed", "curated", "entity", None))
    cur.execute(ROW, (PROTON, "ions", "chebi_hierarchy", "derived", "structure_key", WATER))
    with pytest.raises(psycopg2.errors.UniqueViolation):
        cur.execute(ROW, (WATER, "ions", "metaproviz_seed", "curated", "entity", None))


def test_applying_the_table_definition_twice_changes_nothing(release):
    connection, schema = release
    build.ensure_tables(connection, schema=schema)
    cur = cursor_of(release)
    cur.execute(ROW, (WATER, "ions", "metaproviz_seed", "curated", "entity", None))
    connection.commit()
    before = columns(cur, "exclusion_metabolite")
    build.ensure_tables(connection, schema=schema)
    cur = cursor_of(release)
    assert columns(cur, "exclusion_metabolite") == before
    cur.execute("SELECT count(*) FROM exclusion_metabolite")
    assert cur.fetchone()[0] == 1


def test_the_review_view_lists_members_with_their_label_and_identifiers(release):
    connection, schema = release
    build.ensure_tables(connection, schema=schema)
    cur = cursor_of(release)
    cur.execute(ROW, (WATER, "small_molecules", "metaproviz_seed", "curated", "entity", None))
    cur.execute(
        "SELECT exclusion_class, label, chebi, kegg, assignment_source, origin, match_kind "
        "FROM exclusion_metabolite_members"
    )
    assert cur.fetchall() == [
        ("small_molecules", "water", "15377", "C00001", "metaproviz_seed", "curated", "entity")
    ]


# ---------------------------------------------------------------------------
# Resolution


def seed(exclusion_class, namespace, identifier):
    return seeds.Seed(
        exclusion_class=exclusion_class,
        namespace=namespace,
        identifier=identifier,
        assignment_source="metaproviz_seed",
        source_file="metaproviz_seed.yaml",
    )


def resolved(release, *wanted):
    cur = cursor_of(release)
    return {
        (r.seed.namespace, r.seed.identifier): r for r in resolve.resolve_seeds(cur, list(wanted))
    }


def test_a_chebi_seed_finds_its_entity_with_and_without_the_prefix(release):
    found = resolved(
        release,
        seed("ions", "chebi", "CHEBI:15378"),
        seed("ions", "chebi", "CHEBI:17051"),
        seed("ions", "chebi", "18248"),
    )
    assert found[("chebi", "CHEBI:15378")].entity_ids == (PROTON,)
    assert found[("chebi", "CHEBI:17051")].entity_ids == (FLUORIDE,)
    assert found[("chebi", "18248")].entity_ids == (IRON,)


def test_an_hmdb_seed_finds_one_entity_in_either_width(release):
    found = resolved(
        release,
        seed("small_molecules", "hmdb", "HMDB00001"),
        seed("small_molecules", "hmdb", "HMDB0000001"),
        seed("small_molecules", "hmdb", "HMDB0000008"),
    )
    assert found[("hmdb", "HMDB00001")].entity_ids == (HMDB_LONG,)
    assert found[("hmdb", "HMDB0000001")].entity_ids == (HMDB_LONG,)
    assert found[("hmdb", "HMDB0000008")].entity_ids == (HMDB_SHORT,)


def test_a_bare_number_in_one_namespace_never_matches_another(release):
    # The collider carries PubChem 15378; the ChEBI seed 15378 is the proton.
    found = resolved(release, seed("ions", "chebi", "15378"), seed("ions", "pubchem", "15378"))
    assert found[("chebi", "15378")].entity_ids == (PROTON,)
    assert found[("pubchem", "15378")].entity_ids == (COLLIDER,)


def test_only_chemical_entities_resolve(release):
    # A protein carries the ChEBI value CHEBI:29101 and must not be listed.
    found = resolved(release, seed("ions", "chebi", "CHEBI:29101"))
    assert found[("chebi", "CHEBI:29101")].entity_ids == ()


def test_a_seed_that_several_entities_carry_returns_all_of_them(release):
    found = resolved(release, seed("small_molecules", "kegg", "C00027"))
    assert sorted(found[("kegg", "C00027")].entity_ids) == sorted([TWIN_A, TWIN_B])


def test_a_seed_found_only_on_a_name_identified_entity_is_fallback_only(release):
    found = resolved(
        release,
        seed("small_molecules", "kegg", "C00013"),
        seed("small_molecules", "kegg", "C00001"),
    )
    assert found[("kegg", "C00013")].entity_ids == (FALLBACK,)
    assert found[("kegg", "C00013")].fallback_only is True
    assert found[("kegg", "C00001")].fallback_only is False


def test_a_seed_that_finds_nothing_is_unresolved_and_not_dropped(release):
    found = resolved(release, seed("small_molecules", "kegg", "C99999"))
    assert found[("kegg", "C99999")].entity_ids == ()
    assert found[("kegg", "C99999")].fallback_only is False


def test_resolution_works_without_a_normalized_value_column(release):
    connection, _ = release
    cur = cursor_of(release)
    cur.execute("ALTER TABLE identifier_evidence DROP COLUMN value_normalized")
    found = resolved(release, seed("ions", "chebi", "CHEBI:15378"))
    assert found[("chebi", "CHEBI:15378")].entity_ids == (PROTON,)


# ---------------------------------------------------------------------------
# The curated floor


def built(release, directory=None):
    connection, schema = release
    stats = build.build_exclusion_metabolites(connection, schema=schema, seed_directory=directory)
    cur = cursor_of(release)
    return stats, cur


def test_the_build_publishes_one_row_per_resolved_curated_seed(release):
    stats, cur = built(release)
    cur.execute(
        "SELECT exclusion_class, entity_id::text, assignment_source, origin, match_kind, "
        "seed_namespace, seed_identifier FROM exclusion_metabolite "
        "ORDER BY exclusion_class, seed_identifier, entity_id"
    )
    rows = cur.fetchall()
    expected = {
        ("atoms", IRON, "chebi", "CHEBI:18248"),
        ("ions", WATER, "kegg", "C00001"),
        ("ions", CO2, "kegg", "C00011"),
        ("ions", PROTON, "chebi", "CHEBI:15378"),
        ("ions", FLUORIDE, "chebi", "CHEBI:17051"),
        ("small_molecules", WATER, "kegg", "C00001"),
        ("small_molecules", CO2, "kegg", "C00011"),
        ("small_molecules", FALLBACK, "kegg", "C00013"),
        ("small_molecules", TWIN_A, "kegg", "C00027"),
        ("small_molecules", TWIN_B, "kegg", "C00027"),
    }
    assert {(r[0], r[1], r[5], r[6]) for r in rows} == expected
    assert len(rows) == len(expected)
    assert {(r[2], r[3], r[4]) for r in rows} == {("metaproviz_seed", "curated", "entity")}
    assert stats["rows"] == len(expected)


def test_every_row_carries_the_list_version_and_the_release_digest(release):
    built(release)
    cur = cursor_of(release)
    cur.execute("SELECT DISTINCT list_version, build_id FROM exclusion_metabolite")
    [(list_version, build_id)] = cur.fetchall()
    assert build_id == FIXTURE_BUILD
    assert list_version


def test_the_report_counts_resolved_fallback_and_unresolved_seeds_per_class_and_namespace(release):
    built(release)
    cur = cursor_of(release)
    cur.execute(
        "SELECT exclusion_class, namespace, resolved, fallback_only, unresolved "
        "FROM exclusion_metabolite_report WHERE kind = 'seed_coverage' "
        "ORDER BY exclusion_class, namespace"
    )
    assert cur.fetchall() == [
        ("atoms", "chebi", 1, 0, 279),
        ("ions", "chebi", 2, 0, 185),
        ("ions", "kegg", 2, 0, 12),
        ("small_molecules", "kegg", 4, 1, 12),
    ]


def test_the_report_names_the_seeds_that_did_not_resolve(release):
    built(release)
    cur = cursor_of(release)
    cur.execute(
        "SELECT detail FROM exclusion_metabolite_report "
        "WHERE kind = 'seed_coverage' AND exclusion_class = 'small_molecules'"
    )
    [(detail,)] = cur.fetchall()
    assert "C00533" in detail["unresolved"] and "C00001" not in detail["unresolved"]
    assert detail["fallback_only"] == ["C00013"]


def test_a_second_build_over_the_same_inputs_publishes_the_same_rows(release):
    connection, _ = release
    built(release)
    cur = cursor_of(release)
    cur.execute("SELECT * FROM exclusion_metabolite ORDER BY 1, 2, 3")
    first = cur.fetchall()
    built(release)
    cur = cursor_of(release)
    cur.execute("SELECT * FROM exclusion_metabolite ORDER BY 1, 2, 3")
    assert cur.fetchall() == first


def test_a_removed_seed_leaves_no_stale_row(release, tmp_path):
    built(release)
    directory = seed_directory(
        tmp_path,
        metaproviz="seeds:\n  - {class: ions, namespace: chebi, ids: ['CHEBI:15378']}\n",
        cofactors="members: []\n",
        overrides="add: []\nveto: []\n",
    )
    _, cur = built(release, directory)
    cur.execute("SELECT exclusion_class, entity_id::text FROM exclusion_metabolite")
    assert cur.fetchall() == [("ions", PROTON)]


def test_the_build_returns_counts_for_the_build_record(release):
    stats, _ = built(release)
    assert stats["build_id"] == FIXTURE_BUILD
    assert stats["rows"] == 10
    assert stats["classes"]["small_molecules"] == 5
    assert stats["classes"]["ions"] == 4
    assert stats["classes"]["atoms"] == 1
    assert stats["classes"]["xenobiotics"] == 0
    assert stats["unresolved_seeds"] == 279 + 185 + 12 + 12


def test_the_runner_dispatches_the_product_and_reports_its_counts(release):
    from omnipath_subsets import scientific

    connection, schema = release
    outcome = scientific.run_product(connection, "exclusion_metabolites", schema=schema)
    assert outcome["result"]["rows"] == 10
    assert outcome["result"]["build_id"] == FIXTURE_BUILD


# ---------------------------------------------------------------------------
# A release with a ChEBI hierarchy and structure keys

ION_ROOT, ION_CHILD, ION_GRAND, ATOM_ROOT, ATOM_CHILD, PROTEIN_CHILD, PART_OF_ONLY, BOTH = (
    ident(i) for i in range(20, 28)
)
VARIANT, OTHER_VARIANT, UNRELATED, LEVEL_TWO_ONLY = (ident(i) for i in range(30, 34))


def add_entity(cur, entity_id, label, identifiers, *, entity_type=2, canonical=1):
    """One entity with its identifiers, in the order of the release's tables."""
    first = identifiers[0][1]
    cur.execute(
        "INSERT INTO entity VALUES (%s,%s,%s,%s,%s)",
        [entity_id, entity_type, canonical, first, label],
    )
    for type_id, value in identifiers:
        identifier = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO identifier_evidence VALUES (%s,%s,%s,%s)",
            [identifier, type_id, value, value],
        )
        cur.execute("INSERT INTO entity_identifier_lookup VALUES (%s,%s)", [entity_id, identifier])


@pytest.fixture
def hierarchy(release):
    """The small release plus a ChEBI hierarchy and structure keys.

    ion root <- ion child <- ion grand, and the root is also a child of the
    grand child, a cycle a real hierarchy should not hold but the closure must
    survive. The proton and the iron atom sit under the roots and are curated
    seeds too. A protein under the ion root and an entity that is only `part_of`
    it must not enter the class.
    """
    connection, schema = release
    cur = cursor_of(release)
    add_entity(cur, ION_ROOT, "ion", [(1, "CHEBI:24867")])
    add_entity(cur, ION_CHILD, "a child ion", [(1, "CHEBI:90001")])
    add_entity(cur, ION_GRAND, "a grandchild ion", [(1, "CHEBI:90002")])
    add_entity(cur, ATOM_ROOT, "atom", [(1, "CHEBI:33250")])
    add_entity(cur, ATOM_CHILD, "a child atom", [(1, "CHEBI:90003")])
    add_entity(cur, PROTEIN_CHILD, "a protein", [(1, "CHEBI:90004")], entity_type=1)
    add_entity(cur, PART_OF_ONLY, "a part of the ion root", [(1, "CHEBI:90005")])
    add_entity(cur, BOTH, "an ion that is also an atom class", [(1, "CHEBI:90010")])
    add_entity(cur, VARIANT, "a water variant", [(1, "CHEBI:90006")])
    add_entity(cur, OTHER_VARIANT, "another water variant", [(1, "CHEBI:90007")])
    add_entity(cur, UNRELATED, "an unrelated molecule", [(1, "CHEBI:90008")])
    add_entity(cur, LEVEL_TWO_ONLY, "a coarser neighbour", [(1, "CHEBI:90009")])
    cur.execute("INSERT INTO data_source VALUES (1, 'chebi')")
    cur.execute("INSERT INTO vocab_relation_predicate VALUES (1,'subclass_of'),(2,'part_of')")
    cur.executemany(
        "INSERT INTO entity_ontology_relation VALUES (1,%s,%s,%s,'chebi')",
        [
            (ION_CHILD, 1, ION_ROOT),
            (ION_GRAND, 1, ION_CHILD),
            (ION_ROOT, 1, ION_GRAND),  # the cycle
            (PROTON, 1, ION_ROOT),
            (PROTEIN_CHILD, 1, ION_ROOT),
            (PART_OF_ONLY, 2, ION_ROOT),
            (ATOM_CHILD, 1, ATOM_ROOT),
            (IRON, 1, ATOM_ROOT),
            (BOTH, 1, ION_ROOT),  # one entity under both roots
            (BOTH, 1, ATOM_ROOT),
        ],
    )
    cur.executemany(
        "INSERT INTO chemical_resolution_group_member VALUES (%s,%s,%s,%s)",
        [
            (1, "KEYWATER", WATER, "KEYWATER-AAAAAAAAAA-N"),
            (1, "KEYWATER", CO2, "KEYWATER-BBBBBBBBBB-N"),
            (1, "KEYWATER", VARIANT, "KEYWATER-CCCCCCCCCC-N"),
            (1, "KEYWATER", OTHER_VARIANT, "KEYWATER-DDDDDDDDDD-N"),
            (1, "KEYOTHER", UNRELATED, "KEYOTHER-AAAAAAAAAA-N"),
            (2, "COARSE", WATER, "KEYWATER-AAAAAAAAAA-N"),
            (2, "COARSE", LEVEL_TWO_ONLY, "COARSE-AAAAAAAAAA-N"),
        ],
    )
    connection.commit()
    return release


def table_rows(release, where="true"):
    cur = cursor_of(release)
    cur.execute(
        "SELECT exclusion_class, entity_id::text, assignment_source, origin, match_kind, "
        f"via_entity_id::text FROM exclusion_metabolite WHERE {where} "
        "ORDER BY 1, 2, 3"
    )
    return cur.fetchall()


def test_every_descendant_of_a_root_is_in_its_class_and_the_roots_are_too(hierarchy):
    built(hierarchy)
    derived = {
        (r[0], r[1])
        for r in table_rows(hierarchy, "assignment_source = 'chebi_hierarchy'")
        if r[4] == "entity"
    }
    assert derived == {
        ("ions", ION_ROOT),
        ("ions", ION_CHILD),
        ("ions", ION_GRAND),
        ("ions", PROTON),
        ("ions", BOTH),
        ("atoms", ATOM_ROOT),
        ("atoms", ATOM_CHILD),
        ("atoms", IRON),
        ("atoms", BOTH),
    }
    assert {r[3] for r in table_rows(hierarchy, "assignment_source = 'chebi_hierarchy'")} == {
        "derived"
    }


def test_a_protein_and_a_part_of_edge_do_not_enter_a_class(hierarchy):
    built(hierarchy)
    listed = {r[1] for r in table_rows(hierarchy)}
    assert PROTEIN_CHILD not in listed
    assert PART_OF_ONLY not in listed


def test_a_seed_the_closure_also_finds_has_one_row_per_source(hierarchy):
    built(hierarchy)
    rows = table_rows(hierarchy, f"entity_id = '{IRON}' AND exclusion_class = 'atoms'")
    assert [(r[2], r[3]) for r in rows] == [
        ("chebi_hierarchy", "derived"),
        ("metaproviz_seed", "curated"),
    ]


def test_a_cycle_in_the_hierarchy_stops_at_the_depth_guard(hierarchy):
    stats, cur = built(hierarchy)
    cur.execute(
        "SELECT count(*) FROM exclusion_metabolite WHERE entity_id = %s AND exclusion_class = 'ions'",
        [ION_ROOT],
    )
    assert cur.fetchone()[0] == 2  # the seed and the closure, once each
    assert stats["classes"]["ions"] >= 4


def test_an_unresolved_root_derives_nothing_and_says_so(release):
    stats, cur = built(release)
    assert stats["unresolved_roots"] == ["atoms", "ions"]
    cur.execute("SELECT count(*) FROM exclusion_metabolite WHERE origin = 'derived'")
    assert cur.fetchone()[0] == 0


def test_a_variant_of_a_listed_member_is_listed_by_structure_key_in_that_class(hierarchy):
    built(hierarchy)
    keyed = table_rows(hierarchy, "match_kind = 'structure_key'")
    assert {(r[0], r[1], r[5]) for r in keyed} == {
        ("ions", VARIANT, WATER),
        ("ions", OTHER_VARIANT, WATER),
        ("small_molecules", VARIANT, WATER),
        ("small_molecules", OTHER_VARIANT, WATER),
    }
    assert {r[2] for r in keyed} == {"metaproviz_seed"}


def test_an_entity_that_matched_on_its_own_gets_no_key_row_and_nothing_is_merged(hierarchy):
    built(hierarchy)
    for exclusion_class in ("ions", "small_molecules"):
        for entity in (WATER, CO2):
            rows = table_rows(
                hierarchy, f"entity_id = '{entity}' AND exclusion_class = '{exclusion_class}'"
            )
            assert {r[4] for r in rows} == {"entity"}
    assert UNRELATED not in {r[1] for r in table_rows(hierarchy)}
    assert LEVEL_TWO_ONLY not in {r[1] for r in table_rows(hierarchy)}


def test_a_variant_is_not_listed_in_a_class_its_member_is_not_in(hierarchy):
    built(hierarchy)
    assert not table_rows(hierarchy, f"entity_id = '{VARIANT}' AND exclusion_class = 'atoms'")


def report(release, kind):
    cur = cursor_of(release)
    cur.execute(
        "SELECT exclusion_class, resolved, detail FROM exclusion_metabolite_report "
        "WHERE kind = %s ORDER BY exclusion_class",
        [kind],
    )
    return cur.fetchall()


def test_the_report_gives_the_size_of_every_class(hierarchy):
    built(hierarchy)
    sizes = {r[0]: (r[1], r[2]) for r in report(hierarchy, "class_size")}
    assert set(sizes) == set(CLASSES)
    assert sizes["xenobiotics"][0] == 0
    assert sizes["atoms"][0] == 4  # root, child, iron, and the entity under both roots
    assert sizes["atoms"][1]["entity_matches"] == 4
    assert sizes["ions"][1]["structure_key_matches"] == 2
    assert sizes["ions"][0] == sizes["ions"][1]["entity_matches"] + 2


def test_the_report_compares_the_closure_with_the_seeds(hierarchy):
    built(hierarchy)
    missed = {r[0]: r for r in report(hierarchy, "seed_not_derived")}
    # CHEBI:17051 (fluoride) is an ion seed the fixture hierarchy does not reach.
    assert FLUORIDE in missed["ions"][2]["entities"]
    assert PROTON not in missed["ions"][2]["entities"]
    assert missed["ions"][1] == len(missed["ions"][2]["entities"])
    extra = {r[0]: r for r in report(hierarchy, "derived_not_seeded")}
    # The ion root is itself an ion seed, so the closure does not add it.
    assert ION_ROOT not in extra["ions"][2]["entities"]
    assert "a child ion" in extra["ions"][2]["labels"]
    assert "a child atom" in extra["atoms"][2]["labels"]
    assert "iron atom" not in extra["atoms"][2]["labels"]


def test_the_report_lists_keys_that_pulled_in_a_variant_with_the_variant_labels(hierarchy):
    built(hierarchy)
    rows = {r[0]: r for r in report(hierarchy, "shared_key")}
    assert rows["ions"][1] == 1
    [entry] = rows["ions"][2]["keys"]
    assert entry["key"] == "KEYWATER"
    assert entry["via"] == WATER or entry["via"] == CO2
    assert sorted(entry["labels"]) == ["a water variant", "another water variant"]


def test_the_report_counts_listed_members_that_have_no_structure_key(hierarchy):
    built(hierarchy)
    rows = {r[0]: r for r in report(hierarchy, "no_key")}
    assert rows["ions"][1] >= 1
    assert "hydron" in rows["ions"][2]["labels"]
    assert rows["atoms"][1] >= 1


def test_two_builds_over_the_same_release_and_inputs_give_identical_tables(hierarchy):
    def snapshot():
        cur = cursor_of(hierarchy)
        cur.execute("SELECT * FROM exclusion_metabolite ORDER BY 1, 2, 3")
        rows = cur.fetchall()
        cur.execute(
            "SELECT kind, exclusion_class, namespace, resolved, fallback_only, unresolved, "
            "detail::text, build_id FROM exclusion_metabolite_report ORDER BY 1, 2, 3, 7"
        )
        return rows, cur.fetchall()

    built(hierarchy)
    first = snapshot()
    built(hierarchy)
    assert snapshot() == first
    assert first[0] and first[1]


def test_every_resolvable_curated_seed_has_a_row_and_each_other_is_only_in_the_report(hierarchy):
    connection, _ = hierarchy
    built(hierarchy)
    cur = cursor_of(hierarchy)
    loaded = seeds.read_seeds().seeds
    found = {
        (r.seed.exclusion_class, e)
        for r in resolve.resolve_seeds(cur, loaded)
        for e in r.entity_ids
    }
    cur.execute(
        "SELECT exclusion_class, entity_id::text FROM exclusion_metabolite "
        "WHERE assignment_source = 'metaproviz_seed' AND match_kind = 'entity'"
    )
    assert {(c, e) for c, e in cur.fetchall()} == found
    unresolved = [r.seed for r in resolve.resolve_seeds(cur, loaded) if not r.resolved]
    assert unresolved
    cur.execute(
        "SELECT sum(unresolved) FROM exclusion_metabolite_report WHERE kind = 'seed_coverage'"
    )
    assert cur.fetchone()[0] == len(unresolved)
