# Legacy parity of the inputs_v2 ports

14 October 2026 · pypath `15de653` · build cache on nicesrv (`/root/data/pypath-cache`) ·
harness [`scripts/legacy_parity.py`](../../../scripts/legacy_parity.py)

## What was compared

For each resource, the legacy pypath input (`pypath.inputs`) and its inputs_v2
port read **the same cached file**, and both are reduced to pairs in the
source's own identifiers: interaction partners, an entity and its annotation
(class, cell type, phenotype, pathway), a member and its group, a metabolite
and its cross-references; complexes are compared by their member sets. Signs
are compared where both sides give one.

- Nothing is downloaded. inputs_v2 opens the cached files; a legacy download
  (through `Curl`, the download manager, `requests` or `urllib`) is served the
  inputs_v2 file of the same request, or fails with `NotCached`. Where legacy
  writes the URL of the same file differently (a GitHub spelling, OmniPath's
  rescued copy), the spec says so.
- Legacy identifier translation (`pypath.utils.mapping`) is replaced by
  identity, so legacy keeps the source's identifiers as inputs_v2 does.
- A pair found only the other way round (an undirected interaction listed in
  one orientation) counts as found.
- HMDB is compared on the first 20,000 metabolites of the file on both sides;
  everything else in full.

The JSON file of each resource has the counts, up to 25 examples of each kind
of difference and the files each side read, with their checksums.

## Results

TABLE

"Legacy keys" are distinct legacy pairs (or complexes); "found" includes
pairs found the other way round; "only v2" counts v2 relations with no legacy
pair, often because v2 imports more of the file.

## Fixed in inputs_v2 (rebuilt on nicesrv as 2026.10.14, not yet released)

- **CellChat**: 988 human (995 mouse) interactions had the enzyme, or an
  enzyme-transporter complex, as the ligand of a metabolite. The ligand is
  now the metabolite with its ChEBI term; the proxy genes are associated with
  it in new `*_metabolite_genes` datasets.
- **CellPhoneDB**: the protein and complex roles (receptor, secreted,
  transmembrane, peripheral, integrin, with the receptor and secreted classes)
  were not imported; they are, and agree with legacy's reading. Metabolite
  ligands carry their ChEBI terms (48 of 52). HLA-A, -B, -C are proteins.
- **Macrophage**: the partner cells are read as legacy read them (complexes
  split into their genes, forms in brackets removed): 1,658 of the 1,702
  legacy pairs found, 174 before.
- **ORegAnno**: a factor or gene cell of several names (`STAT1/STAT3`) gives
  its first name as the symbol, as legacy.
- **miRDeathDB, ncRDeathDB**: each miRBase accession listed for a microRNA
  gives a relation, as legacy; before, a symbol with several products had
  none.
- **HMDB**: no metabolite had its KEGG compound ID (a pattern with two groups);
  now they do.

## For decision

Places where v2 does not do what legacy did, or adds what the source does not
state. Each is how the module is now; nothing was changed for these.

1. **CollecTRI**: legacy renames miRNA targets to the mature miRNA
   (`hsa-miR-223`); v2 keeps the gene symbol (`MIR223`). 120 pairs.
2. **HIPPIE**: legacy keeps interactions of score 0.75 or more; v2 keeps all
   (1.1 million relations) with the score. At 0.75 the two agree.
3. **NetPath**: an interaction of three or more proteins is a complex in v2;
   legacy made every pair of its members, and also each protein with itself.
4. **Lambert 2018**: legacy annotated all 2,765 candidate genes with their DBD
   family and an `is_tf` flag; v2 has the 1,639 genes judged TFs.
5. **TF census**: v2 leaves out the loci of class x (judged not TFs); legacy
   kept them with their class.
6. **TopDB**: v2 has the proteins named by UniProt entry names; legacy also
   kept about 1,020 entries named by PDB chain (`2K3C_A`).
7. **ORegAnno**: v2 imports all species; legacy took one organism (human by
   default). v2 also keeps the dash of a one-name cell (`HLA-A`), which legacy
   removed.
8. **Macrophage**: v2 removes each bracket of a cell; legacy removed from the
   first to the last bracket and lost the members between
   (`NFKB1(p50):RELA(p65)` was NFKB1 alone). v2 also keeps the rows with a gene
   as partner, which legacy left out; in 38 of them the gene is partner 1 of an
   activation by a complex, which reads reversed but is how the sheet states it.
9. **Adhesome**: for the 2007 network v2 takes the interactions and their
   direction from the publication, as the website lists every interaction in
   both orientations; 158 legacy pairs from the website are not among them.
10. **CellChat and CellPhoneDB metabolite ChEBI terms**: the CellChat table of
    ChEBI terms, and the CellPhoneDB terms for metabolites the source gives
    without one, are identifiers the source does not state (each looked up by
    name in ChEBI). By the rule "do not add what the source does not provide",
    these would be names only.
11. **GO**: the v2 module imports the ontology only; legacy's GO inputs read
    the GOA gene annotations, which no v2 module imports.
12. **SPIKE**: legacy wrote a complex partner as its members' Entrez IDs; v2
    refers to the SPIKE complex (with its members in the complex dataset).
13. **Guide to Pharmacology**: legacy names peptide ligands by gene symbol, v2
    by GtoP ligand, UniProt and Ensembl IDs; `interactions.csv` has a
    `Ligand Gene Symbol` column that v2 does not read.

v2 also cleans source errors that legacy kept, without a decision needed:
CORUM `Q9NZL9-`, GPCRdb HTML in names, dbPTM `X OR Y` kinases, miRecords
`UNKNOWN` targets, SwissLipids and HMDB malformed IDs, PhosphoSite RefSeq
substrates, SIGNOR complexes with the same members, ICELLNET DKK names.

## Not comparable on the same file

| Resource | Why |
| --- | --- |
| SIGNOR interactions, ComplexPortal, Matrisome | the two generations read different exports |
| HPMR, phospho.ELM, MIMP, iPTMnet, PepCyber, Reactome, Rhea, CancerCellMap, CancerSEA, MetAtlas | legacy reads files v2 does not |
| Baccin 2019, LMPID, LOCATE, scConnect, 3DID, I3D | legacy needs UniProt or Pfam tables |
| SwitchesELM, DisGeNET | legacy calls a web service (OLS, the DisGeNET API) |
| Almen 2009 | legacy keys the proteins by IPI, v2 by UniProt |
| LIPID MAPS, miR2Disease, PDB | the legacy function fails on the current file |
| GO | see decision 11 |
| MEBOCOST | no legacy input |

These are left for the served-level comparison against the legacy web service.

## Notes on the harness

- The legacy package does not import cleanly any more: module-level lookups
  call UniChem, miRBase and Ensembl. The harness stubs them, and serves
  organism names for human, mouse and rat from a small table.
- `pypath.inputs.rdata` (legacy) patches the `rdata` converter on import,
  after which the v2 CellChat parser fails; each resource runs in its own
  process, v2 before legacy.
- Run on nicesrv, from the build checkout:
  `PYPATH_DOWNLOAD_DATADIR=/root/data/pypath-cache uv run --frozen --with pycurl python scripts/legacy_parity.py --out <dir>`.
