"""Builder-only SQL projections from an assigned catalogue."""

from pathlib import Path

from .build_reference import norm, quote, scan


def specification(reference):
    """Return named canonical projections used to compile the compact index."""
    root = Path(reference)
    specs = []

    def add(name, relative, key, columns="*", where="true"):
        path = root / relative
        if path.is_file():
            specs.append(
                (
                    name,
                    relative,
                    key,
                    f"SELECT {key}::VARCHAR _key,{columns} FROM {scan(path)} WHERE {where} AND {key} IS NOT NULL",
                )
            )

    for domain in ("chemical", "gene_protein"):
        add(f"{domain}-entities", f"{domain}-entities/entities.parquet", "entity_id")
        add(f"{domain}-identity", f"{domain}-entities/identity_identifiers.parquet", "identifier")
        add(
            f"{domain}-identity-forward",
            f"{domain}-entities/identity_identifiers.parquet",
            "entity_id",
            "entity_id,namespace,identifier",
        )
    chemical_hubs = (
        "chebi",
        "pubchem",
        "chembl",
        "hmdb",
        "lipidmaps",
        "swisslipids",
        "bigg",
        "metanetx",
        "refmet",
        "ramp",
        "kegg",
    )
    for hub in chemical_hubs:
        where = "namespace IN ('kegg','cas','drugbank')"
        if hub in {"chebi", "bigg"}:
            where += f" OR namespace='{hub}'"
        add(
            f"claims-{hub}",
            f"claims-{hub}/identifier_claims.parquet",
            "identifier",
            "entity_id,namespace,identifier",
            f"({where})",
        )
    sequence_key = "CASE WHEN namespace IN ('refseq_protein','genbank') THEN regexp_replace(identifier, '\\.[0-9]+$', '') ELSE identifier END"
    add(
        "claims-uniprot",
        "claims-uniprot/identifier_claims.parquet",
        sequence_key,
        "entity_id,namespace,identifier",
    )
    add(
        "uniprot-ensg-forward",
        "claims-uniprot/identifier_claims.parquet",
        "entity_id",
        "entity_id,namespace,identifier",
        "namespace='ensg'",
    )
    add(
        "claims-entrez",
        "claims-entrez/identifier_claims.parquet",
        "identifier",
        "record_id,namespace,identifier",
        "namespace='ensg'",
    )
    add("gene-resolution", "gene-resolution/entrez_identifiers.parquet", "identifier")
    add("source-gene-resolution", "source-gene-resolution/identifiers.parquet", "identifier")
    add(
        "records-uniprot",
        "records-uniprot/records.parquet",
        "anchor",
        "anchor,taxon,anchor_count,reviewed",
    )
    add("gene-products", "gene-products/gene_products.parquet", "entrez_id")
    add("gene-products-forward", "gene-products/gene_products.parquet", "protein_entity_id")

    products = root / "gene-products/gene_products.parquet"
    claims = root / "claims-uniprot/identifier_claims.parquet"
    if products.is_file() and claims.is_file():
        specs.append(
            (
                "gene_protein-gene-forward",
                "gene-products/gene_products.parquet",
                "entity_id",
                f"""SELECT 'entrez:' || p.entrez_id _key,'entrez:' || p.entrez_id entity_id,
                c.namespace,c.identifier FROM {scan(products)} p
                JOIN {scan(claims)} c ON c.entity_id=p.protein_entity_id
                WHERE c.namespace IN ('genesymbol','genesymbol-syn','hgnc','ensg')
                AND (SELECT count(DISTINCT other.entrez_id) FROM {scan(products)} other WHERE other.protein_entity_id=p.protein_entity_id)=1""",
            )
        )

    for domain, hubs in (
        ("chemical", tuple(h for h in chemical_hubs if h not in {"pubchem", "kegg"})),
        ("gene_protein", ("uniprot", "entrez", "ramp_gene")),
    ):
        if domain == "gene_protein":
            add(
                "gene-resolution-forward", "gene-resolution/entrez_identifiers.parquet", "entity_id"
            )
        for hub in hubs:
            add(
                f"{domain}-{hub}-forward",
                f"claims-{hub}/identifier_claims.parquet",
                "entity_id",
                "entity_id,namespace,identifier",
            )
            members = root / f"members-{hub}/members.parquet"
            source = root / "inputs" / f"{hub}.parquet"
            if hub not in {"uniprot", "entrez"} and members.is_file() and source.is_file():
                specs.append(
                    (
                        f"{domain}-{hub}-names",
                        f"inputs/{hub}.parquet",
                        "entity_id",
                        f"""SELECT m.entity_id _key,m.entity_id,h.source_type namespace,h.source_id identifier
                    FROM {scan(members)} m JOIN {scan(source)} h ON m.local_id={norm(quote(hub), "h.hub_id")}
                    WHERE h.source_type IN ('name','synonym','lipid_shorthand','systematic_name')
                      AND h.source_id IS NOT NULL AND trim(h.source_id)<>''""",
                    )
                )
    return specs
