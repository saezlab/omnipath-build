/** Identifier type vocabulary mapping and helpers */

export const IDENTIFIER_SLUGS: Record<string, string> = {
  uniprot: 'UniProt',
  genesymbol: 'Gene Symbol',
  entrez: 'Entrez Gene',
  ensg: 'Ensembl Gene',
  chebi: 'ChEBI',
  chembl: 'ChEMBL',
  pubchem: 'PubChem',
  inchikey: 'InChIKey',
  hgnc: 'HGNC',
  hmdb: 'HMDB',
  kegg: 'KEGG',
  kegg_pathway: 'KEGG pathway',
  kegg_reaction: 'KEGG reaction',
  rhea: 'Rhea',
  reactome: 'Reactome',
  lipidmaps: 'LIPID MAPS',
  swisslipids: 'SwissLipids',
  mirbase: 'miRBase',
  cas: 'CAS',
  bigg: 'BiGG',
  ec: 'EC',
  pfocr: 'PFOCR',
  wikipathways: 'WikiPathways',
  ensembl: 'Ensembl',
  metanetx: 'MetaNetX',
  chembl_target: 'ChEMBL target',
};

export function getIdentifierLabel(slug: string): string {
  return IDENTIFIER_SLUGS[slug.toLowerCase()] || slug;
}
