"""Hub source emitters: one module per source."""

from __future__ import annotations

from collections.abc import Callable

from ..common import emit_from_records
from ..writer import HubParquetWriter
from .bigg import emit as emit_bigg
from .chebi import emit as emit_chebi
from .chembl import emit as emit_chembl
from .ensg import emit as emit_ensg
from .entrez import emit as emit_entrez
from .hmdb import emit as emit_hmdb
from .lipidmaps import emit as emit_lipidmaps
from .metanetx import emit as emit_metanetx
from .mirbase import emit as emit_mirbase
from .pubchem import emit as emit_pubchem
from .refmet import emit as emit_refmet
from .ramp import emit_chemical as emit_ramp, emit_gene as emit_ramp_gene
from .swisslipids import emit as emit_swisslipids
from .taxon_species import emit as emit_taxon_species
from .uniprot import emit as emit_uniprot

HUB_EMITTERS: dict[str, Callable[[HubParquetWriter], None]] = {
    "entrez": emit_entrez,
    "uniprot": emit_uniprot,
    "ensg": emit_ensg,
    "chebi": emit_chebi,
    "pubchem": emit_pubchem,
    "refmet": emit_refmet,
    "ramp": emit_ramp,
    "ramp_gene": emit_ramp_gene,
    "chembl": emit_chembl,
    "hmdb": emit_hmdb,
    "lipidmaps": emit_lipidmaps,
    "swisslipids": emit_swisslipids,
    "bigg": emit_bigg,
    "metanetx": emit_metanetx,
    "mirbase": emit_mirbase,
    "taxon_species": emit_taxon_species,
}

__all__ = [
    "HUB_EMITTERS",
    "emit_bigg",
    "emit_from_records",
    "emit_metanetx",
]
