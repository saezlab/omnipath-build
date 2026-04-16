# OmniPath resources

Quick guide for choosing source datasets.

## Interaction-heavy

- **SIGNOR (`signor`)** — interaction + annotation; best for causal signaling, direction, sign
- **Reactome (`reactome`)** — interaction + association + annotation; best for curated pathways, reactions, pathway membership
- **WikiPathways (`wikipathways`)** — interaction + annotation; best for community-curated pathways
- **IntAct (`intact`)** — interaction + annotation; best for molecular interaction evidence across proteins, chemicals, and nucleic acids
- **BindingDB (`bindingdb`)** — interaction; best for protein-ligand binding and affinities
- **Guide to Pharmacology (`guidetopharma`)** — interaction + annotation; best for ligand-target pharmacology
- **STITCH (`stitch`)** — interaction + annotation; best for chemical-protein interaction networks
- **CellPhoneDB (`cellphonedb`)** — interaction; best for ligand-receptor / cell-cell communication
- **NeuronChat (`neuronchat`)** — interaction; best for neural cell-cell communication
- **MEBOCOST DB (`mebocost`)** — interaction; best for metabolite-sensor interactions

## Annotation or association-heavy

- **UniProt (`uniprot`)** — annotation; best for protein function, localization, disease associations, cross-references
- **HPO (`hpo`)** — annotation; best for gene-phenotype associations
- **ChEBI (`chebi`)** — annotation; best for small-molecule ontology and chemical classification
- **CORUM (`corum`)** — association + annotation; best for mammalian protein complexes

## Chemical / metabolite / food-centric

- **ChEMBL (`chembl`)** — bioactive molecules and drug-like compounds
- **HMDB (`hmdb`)** — human metabolites
- **LIPID MAPS (`lipidmaps`)** — lipid structures and identifiers
- **SwissLipids (`swisslipids`)** — curated lipid structures and classes
- **FooDB (`foodb`)** — food constituents and food chemistry
- **Phenol-Explorer (`phenol_explorer`)** — polyphenols and food bioactives
- **PTFI Discover (`ptfi`)** — food composition and food metabolomics

## Notes

- `Type` is a coarse summary: `interaction`, `association`, `annotation`.
- Annotation ontologies typically appear in `entity_annotation.parquet` or `interaction_annotation.parquet`.
