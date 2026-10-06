"""Entrez hub from NCBI gene2ensembl.gz plus genes gene2ensembl does not list.

gene2ensembl links an NCBI gene to Ensembl, so it lacks genes Ensembl does not annotate:
tRNAs and many non-coding or predicted genes. Those come from the eukaryote gene_info
files, as identity plus the NCBI symbol (a ``name`` row, used for the label only, so it
adds no symbol lookups). Bacteria and viruses are left out: their proteins resolve through
UniProt and they would multiply the hub.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..common import explode_record
from ..schema import as_values
from ..stream import iter_url_lines
from ..writer import HubParquetWriter

URL = "https://ftp.ncbi.nlm.nih.gov/gene/DATA/gene2ensembl.gz"
GENE_INFO_URLS = tuple(
    f"https://ftp.ncbi.nlm.nih.gov/gene/DATA/GENE_INFO/{group}.gene_info.gz"
    for group in (
        "Mammalia/All_Mammalia",
        "Non-mammalian_vertebrates/All_Non-mammalian_vertebrates",
        "Invertebrates/All_Invertebrates",
        "Plants/All_Plants",
        "Fungi/All_Fungi",
    )
)


def emit(
    writer: HubParquetWriter,
    lines: Iterable[str] | None = None,
    gene_info: Iterable[Iterable[str]] | None = None,
) -> None:
    seen: set[tuple[str, str]] = set()
    for line in lines if lines is not None else iter_url_lines(URL):
        if writer.full:
            return
        if line.startswith("#"):
            continue
        fields = line.rstrip("\n").split("\t")
        if len(fields) < 7:
            continue
        taxon = fields[0] if fields[0].isdigit() else "0"
        entrez = "" if fields[1] == "-" else fields[1].strip()
        ensg = "" if fields[2] in {"", "-"} else fields[2].split(".", 1)[0]
        ensp = "" if fields[6] in {"", "-"} else fields[6].split(".", 1)[0]
        if not entrez:
            continue
        key = (taxon, entrez)
        # gene2ensembl includes noncoding transcripts as direct gene links.
        # Preserve RefSeq versions as sequence identities in the hub contract.
        enst = "" if fields[4] in {"", "-"} else fields[4].split(".", 1)[0]
        rna = "" if fields[3] in {"", "-"} else fields[3].strip()
        protein = "" if fields[5] in {"", "-"} else fields[5].strip()
        field_map = {
            "ensg": ensg,
            "enst": enst,
            "ensp": ensp,
            "refseq": rna,
            "refseq_protein": protein,
        }
        if key in seen:
            for source_type, raw in field_map.items():
                for value in as_values(raw):
                    if not writer.add(source_type, value, entrez, taxon, "gene2ensembl"):
                        return
            continue
        seen.add(key)
        if not explode_record(
            writer,
            hub_id=entrez,
            hub_type="entrez",
            backend="gene2ensembl",
            taxonomy_id=taxon,
            fields=field_map,
        ):
            return
    sources = gene_info if gene_info is not None else (iter_url_lines(u) for u in GENE_INFO_URLS)
    for source in sources:
        for line in source:
            if writer.full:
                return
            if line.startswith("#"):
                continue
            # tax_id, GeneID, Symbol, ...
            fields = line.rstrip("\n").split("\t", 3)
            if len(fields) < 3 or not fields[0].isdigit() or not fields[1].isdigit():
                continue
            key = (fields[0], fields[1])
            if key in seen:
                continue
            seen.add(key)
            symbol = "" if fields[2] in {"", "-", "NEWENTRY"} else fields[2].strip()
            if not explode_record(
                writer,
                hub_id=fields[1],
                hub_type="entrez",
                backend="gene_info",
                taxonomy_id=fields[0],
                fields={"name": symbol},
            ):
                return
