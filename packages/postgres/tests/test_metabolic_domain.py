"""Metabolic domain from the classes and pathways a chemical is linked to."""

from contextlib import closing
import uuid

import psycopg2
from psycopg2 import sql
import pytest

from omnipath_postgres.relational.classify import classify_metabolic_domain


CHEMICALS = {
    # A class decides, whatever the pathways say.
    "palmitate": [("chemont", "Lipids and lipid-like molecules")]
    + [("pathway", f"Pentose phosphate pathway {i}") for i in range(5)],
    "sucrose": [("chemont", "Disaccharides"), ("chemont", "Ketals")],
    # Most of its classes: an alternative parent does not outvote the lineage.
    "ser-gln": [
        ("chemont", "Amino acids, peptides, and analogues"),
        ("chemont", "Dipeptides"),
        ("chemont", "Serine and derivatives"),
        ("chemont", "Fatty acids and conjugates"),
    ],
    # As many lipid as amino acid labels: precedence makes it a lipid.
    "phosphatidylserine": [("chemont", "Glycerophosphoserines")],
    # SwissLipids catalogs lipids only: its entries are lipids with no class
    # link, and its curation outranks a computed class.
    "cardiolipin": [("swisslipids", None)],
    "laccer": [("swisslipids", None), ("chemont", "Purine nucleotides")],
    # Any other resource says nothing about the domain.
    "imatinib": [("chembl", None)],
    # The class is the subject: an `associated_with` link has no direction.
    "lysine": [("chemont<", "Amino acids, peptides, and analogues")],
    # A currency metabolite: a few lipid and sugar pathways among many.
    "atp": [("pathway", "Glycerophospholipid metabolism"), ("pathway", "Glycolysis")]
    + [("pathway", f"Signalling pathway {i}") for i in range(8)],
    # Most of its pathways are amino acid pathways.
    "serine": [
        ("pathway", "Glycine, serine and threonine metabolism"),
        ("pathway", "Cysteine and methionine metabolism"),
        ("pathway", "Sphingolipid metabolism"),
    ],
    # A single pathway is too little to decide.
    "folate": [("pathway", "Disorders of bile acid synthesis")],
}


SOURCES = {"swisslipids": 1, "chembl": 2}


@pytest.fixture
def domain_database(postgres_dsn):
    schema = "metabolic_domain_" + uuid.uuid4().hex
    with closing(psycopg2.connect(postgres_dsn)) as connection:

        def query(text):
            return sql.SQL(text).format(s=sql.Identifier(schema))

        with connection.cursor() as cursor:
            cursor.execute(query("CREATE SCHEMA {s}"))
            cursor.execute(query("CREATE TABLE {s}.vocab_entity_type (entity_type_id bigint, name text)"))
            cursor.execute(
                query("CREATE TABLE {s}.vocab_identifier_type (identifier_type_id bigint, name text)")
            )
            cursor.execute(
                query(
                    "CREATE TABLE {s}.vocab_metabolic_domain "
                    "(metabolic_domain_id smallint PRIMARY KEY, name text, precedence integer)"
                )
            )
            cursor.execute(
                query(
                    "CREATE TABLE {s}.entity (entity_id bigint, entity_type_id bigint, "
                    "canonical_identifier_type_id bigint, label text, metabolic_domain_id smallint)"
                )
            )
            cursor.execute(
                query("CREATE TABLE {s}.relation (subject_entity_id bigint, object_entity_id bigint)")
            )
            cursor.execute(query("CREATE TABLE {s}.data_source (source_id bigint, name text)"))
            cursor.execute(
                query("CREATE TABLE {s}.entity_evidence_resolution (source_id bigint, entity_id bigint)")
            )
            cursor.executemany(
                query("INSERT INTO {s}.data_source VALUES (%s, %s)"),
                [(source_id, name) for name, source_id in SOURCES.items()],
            )
            cursor.execute(
                query(
                    "INSERT INTO {s}.vocab_entity_type VALUES "
                    "(1, 'small_molecule'), (2, 'named_thing'), (3, 'pathway')"
                )
            )
            cursor.execute(query("INSERT INTO {s}.vocab_identifier_type VALUES (1, 'chemont')"))
            ids = iter(range(1, 1000))
            for label, links in CHEMICALS.items():
                chemical = next(ids)
                cursor.execute(
                    query("INSERT INTO {s}.entity VALUES (%s, 1, NULL, %s, NULL)"), (chemical, label)
                )
                for kind, target in links:
                    if kind in SOURCES:
                        cursor.execute(
                            query("INSERT INTO {s}.entity_evidence_resolution VALUES (%s, %s)"),
                            (SOURCES[kind], chemical),
                        )
                        continue
                    linked = next(ids)
                    cursor.execute(
                        query("INSERT INTO {s}.entity VALUES (%s, %s, %s, %s, NULL)"),
                        (linked, 3 if kind == "pathway" else 2, None if kind == "pathway" else 1, target),
                    )
                    pair = (linked, chemical) if kind == "chemont<" else (chemical, linked)
                    cursor.execute(query("INSERT INTO {s}.relation VALUES (%s, %s)"), pair)
        connection.commit()
        try:
            yield connection, schema
        finally:
            with connection.cursor() as cursor:
                cursor.execute(query("DROP SCHEMA {s} CASCADE"))
            connection.commit()


def test_classes_decide_and_pathways_only_by_majority(domain_database):
    connection, schema = domain_database
    classify_metabolic_domain(connection, schema=schema)
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                "SELECT e.label, d.name FROM {s}.entity e "
                "JOIN {s}.vocab_metabolic_domain d USING (metabolic_domain_id)"
            ).format(s=sql.Identifier(schema))
        )
        domains = dict(cursor.fetchall())
    assert domains == {
        "palmitate": "lipid",
        "sucrose": "carbohydrate",
        "lysine": "amino_acid",
        "cardiolipin": "lipid",
        "laccer": "lipid",
        "imatinib": "other",
        "ser-gln": "amino_acid",
        "phosphatidylserine": "lipid",
        "atp": "other",
        "serine": "amino_acid",
        "folate": "other",
    }
