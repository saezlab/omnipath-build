"""Synthetic identifier hubs + reference library shared by the canonicalization tests.

The hubs mirror the shape of the real ``data/reference/hubs/*.parquet`` files
(``source_type, source_id, hub_id, taxonomy_id, backend``) and cover the
cases the matching rules must get right:

* TP53: reviewed accession, TrEMBL fragment, secondary accession, ENSG with a
  version suffix, bare HGNC number, synonym ``P53``; the same symbol ``TP53``
  is a synonym of mouse Trp53.
* calmodulin ``P0DP23``: one protein linked to three Entrez genes.
* gene 999 with two reviewed accessions (canonical falls back to Entrez).
* a yeast accession outside the taxon scope.
* water (ChEBI + HMDB agreeing on one InChIKey), aspirin (ChEBI + ChEMBL),
  a structure-less ChEBI class, a ChEBI class folded into an HMDB compound
  through HMDB's cross-reference, and a ChEBI mixture with two InChIKeys.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
from functools import lru_cache
import shutil

import pyarrow as pa
import pyarrow.parquet as pq

from omnipath_build.canonical import build_library
from omnipath_build.hubs.schema import HUB_SCHEMA

WATER = "XLYOFNOQVPJJNP-UHFFFAOYSA-N"
ASPIRIN = "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"
LINKED = "KKKKKKKKKKKKKK-LLLLLLLLLL-N"
MIX_A = "AAAAAAAAAAAAAA-BBBBBBBBBB-C"
MIX_B = "DDDDDDDDDDDDDD-BBBBBBBBBB-C"


def _hub(hubs: Path, name: str, rows, backend: str | None = None) -> None:
    table = pa.Table.from_pylist(
        [
            {
                "source_type": a,
                "source_id": b,
                "hub_id": c,
                "taxonomy_id": d,
                "backend": backend or name,
            }
            for a, b, c, d in rows
        ],
        schema=HUB_SCHEMA,
    )
    pq.write_table(table, hubs / f"{name}.parquet")


def write_hubs(hubs: Path) -> None:
    hubs.mkdir(parents=True, exist_ok=True)
    _hub(
        hubs,
        "uniprot",
        [
            ("uniprot", "P04637", "P04637", "9606"),
            ("uniprot_entry", "P53_HUMAN", "P04637", "0"),
            ("genesymbol", "TP53", "P04637", "0"),
            ("genesymbol-syn", "P53", "P04637", "0"),
            ("entrez", "7157", "P04637", "0"),
            ("ensg", "ENSG00000141510.15", "P04637", "0"),
            ("hgnc", "11998", "P04637", "0"),
            ("uniprot-sec", "Q15086", "P04637", "0"),
            ("uniprot", "A0A0U1RQF1", "A0A0U1RQF1", "9606"),
            ("uniprot_entry", "A0A0U1RQF1_HUMAN", "A0A0U1RQF1", "0"),
            ("entrez", "7157", "A0A0U1RQF1", "0"),
            ("genesymbol", "TP53", "A0A0U1RQF1", "0"),
            ("uniprot", "P0DP23", "P0DP23", "9606"),
            ("uniprot_entry", "CALM1_HUMAN", "P0DP23", "0"),
            ("genesymbol", "CALM1", "P0DP23", "0"),
            ("entrez", "801", "P0DP23", "0"),
            ("entrez", "805", "P0DP23", "0"),
            ("entrez", "808", "P0DP23", "0"),
            ("uniprot", "P02340", "P02340", "10090"),
            ("uniprot_entry", "P53_MOUSE", "P02340", "0"),
            ("genesymbol", "Trp53", "P02340", "0"),
            ("genesymbol-syn", "TP53", "P02340", "0"),
            ("entrez", "22059", "P02340", "0"),
            ("uniprot", "P11111", "P11111", "9606"),
            ("uniprot_entry", "AAA_HUMAN", "P11111", "0"),
            ("entrez", "999", "P11111", "0"),
            ("genesymbol", "AAA", "P11111", "0"),
            ("uniprot", "P22222", "P22222", "9606"),
            ("uniprot_entry", "AAB_HUMAN", "P22222", "0"),
            ("entrez", "999", "P22222", "0"),
            ("genesymbol", "AAA", "P22222", "0"),
            ("uniprot", "Q99999", "Q99999", "4932"),
            ("uniprot_entry", "Q99999_YEAST", "Q99999", "0"),
            ("uniprot", "P12345", "P12345", "11676"),
            ("uniprot_entry", "POL_HV1H2", "P12345", "0"),
            ("genesymbol", "pol", "P12345", "0"),
        ],
    )
    _hub(
        hubs,
        "entrez",
        [
            ("entrez", "7157", "7157", "9606"),
            ("ensg", "ENSG00000141510", "7157", "9606"),
            ("entrez", "55", "55", "9606"),
            ("ensg", "ENSG00000000055", "55", "9606"),
        ],
        backend="gene2ensembl",
    )
    pq.write_table(
        pa.table(
            {
                "ncbi_tax_id": ["9606", "10090"],
                "common_name": ["human", "mouse"],
                "latin_name": ["H", "M"],
            }
        ),
        hubs / "organism.parquet",
    )
    _hub(
        hubs,
        "chebi",
        [
            ("chebi", "CHEBI:15377", "CHEBI:15377", "0"),
            ("inchikey", WATER, "CHEBI:15377", "0"),
            ("name", "water", "CHEBI:15377", "0"),
            ("synonym", "H2O", "CHEBI:15377", "0"),
            ("kegg", "C00001", "CHEBI:15377", "0"),
            ("cas", "7732-18-5", "CHEBI:15377", "0"),
            ("chebi", "CHEBI:15365", "CHEBI:15365", "0"),
            ("inchikey", "InChIKey=" + ASPIRIN, "CHEBI:15365", "0"),
            ("name", "acetylsalicylic acid", "CHEBI:15365", "0"),
            ("chebi", "CHEBI:24431", "CHEBI:24431", "0"),
            ("name", "chemical entity", "CHEBI:24431", "0"),
            ("chebi", "CHEBI:16974", "CHEBI:16974", "0"),
            ("name", "some class without structure", "CHEBI:16974", "0"),
            ("chebi", "CHEBI:1", "CHEBI:1", "0"),
            ("inchikey", MIX_A, "CHEBI:1", "0"),
            ("inchikey", MIX_B, "CHEBI:1", "0"),
        ],
    )
    _hub(
        hubs,
        "hmdb",
        [
            ("hmdb", "HMDB0002111", "HMDB0002111", "0"),
            ("inchikey", WATER, "HMDB0002111", "0"),
            ("name", "Water", "HMDB0002111", "0"),
            ("chebi", "15377", "HMDB0002111", "0"),
            ("pubchem", "962", "HMDB0002111", "0"),
            ("hmdb", "HMDB0002024", "HMDB0002024", "0"),
            ("inchikey", LINKED, "HMDB0002024", "0"),
            ("chebi", "16974", "HMDB0002024", "0"),
            ("name", "Linked compound", "HMDB0002024", "0"),
            ("drugbank", "DB09145", "HMDB0002024", "0"),
        ],
    )
    _hub(
        hubs,
        "chembl",
        [
            ("chembl", "CHEMBL25", "CHEMBL25", "0"),
            ("inchikey", ASPIRIN, "CHEMBL25", "0"),
            ("name", "ASPIRIN", "CHEMBL25", "0"),
        ],
    )

    _hub(
        hubs,
        "bigg",
        [
            ("bigg", "h2o", "h2o", "0"),
            ("bigg", "h2o_c", "h2o", "0"),
            ("chebi", "CHEBI:15377", "h2o", "0"),
        ],
    )
    _hub(
        hubs,
        "metanetx",
        [
            ("metanetx", "MNXM2", "MNXM2", "0"),
            ("chebi", "CHEBI:15377", "MNXM2", "0"),
        ],
    )

    from omnipath_build.reference.build_reference import CHEMICAL, PROTEIN

    for name in CHEMICAL + PROTEIN:
        if not (hubs / f"{name}.parquet").exists():
            _hub(hubs, name, [])


@lru_cache(maxsize=1)
def _template():
    directory = tempfile.TemporaryDirectory(prefix="parquet-test-reference-")
    root = Path(directory.name)
    write_hubs(root / "hubs")
    reference = build_library(root / "hubs", root / "library").library_dir
    return directory, reference


def build_fixture_library(root: Path) -> Path:
    """Independent copies of one production-built reference per test process."""
    _, reference = _template()
    destination = root / "library"
    shutil.copytree(reference, destination)
    return destination
