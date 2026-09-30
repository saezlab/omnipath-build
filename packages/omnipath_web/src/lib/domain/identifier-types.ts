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
};

export function getIdentifierLabel(slug: string): string {
  return IDENTIFIER_SLUGS[slug.toLowerCase()] || slug;
}
