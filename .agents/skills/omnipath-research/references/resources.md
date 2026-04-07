# OmniPath resources

Use this as a quick guide for choosing source datasets.

## Interaction-focused resources

### SIGNOR (`signor`)
- Type: interaction + annotation
- Best for: causal signaling, direction/sign, pathway signaling context

### Reactome (`reactome`)
- Type: interaction + association + annotation
- Annotation ontologies: Gene Ontology, Reactome Pathway Ontology
- Best for: curated pathways, reactions, pathway membership

### WikiPathways (`wikipathways`)
- Type: interaction + annotation
- Annotation ontologies: WikiPathways Ontology
- Best for: community-curated pathways and pathway structure

### IntAct (`intact`)
- Type: interaction + annotation
- Annotation ontologies: Molecular Interactions Ontology
- Best for: molecular interaction evidence across proteins, chemicals, and nucleic acids

### BindingDB (`bindingdb`)
- Type: interaction
- Best for: protein-ligand binding interactions and affinities

### Guide to Pharmacology (`guidetopharma`)
- Type: interaction + annotation
- Best for: ligand-target pharmacology and quantitative activity information

### STITCH (`stitch`)
- Type: interaction + annotation
- Best for: chemical-protein interaction networks, including predicted associations

### CellPhoneDB (`cellphonedb`)
- Type: interaction
- Best for: ligand-receptor and cell-cell communication interactions

### NeuronChat (`neuronchat`)
- Type: interaction
- Best for: neural cell-cell communication interactions

### MEBOCOST DB (`mebocost`)
- Type: interaction
- Best for: metabolite-sensor interactions

## Annotation-focused resources

### UniProt (`uniprot`)
- Type: annotation
- Annotation ontologies: Gene Ontology, UniProt Keywords
- Best for: protein function, localization, disease associations, ontology-backed protein annotations, cross-references

### HPO (`hpo`)
- Type: annotation
- Annotation ontologies: Human Phenotype Ontology
- Best for: gene-phenotype associations and disease phenotype terms

### ChEBI (`chebi`)
- Type: annotation
- Annotation ontologies: ChEBI
- Best for: small-molecule ontology and chemical classification

### CORUM (`corum`)
- Type: association + annotation
- Annotation ontologies: Gene Ontology
- Best for: mammalian protein complexes and GO-annotated complex records

## Other chemical or food resources

These are useful when the question is chemistry- or metabolite-centered, even if they are not marked as interaction or annotation resources in the summary.

### ChEMBL (`chembl`)
- Best for: bioactive molecules and drug-like compounds

### HMDB (`hmdb`)
- Best for: human metabolites

### LIPID MAPS (`lipidmaps`)
- Best for: lipid structures and lipid identifiers

### SwissLipids (`swisslipids`)
- Best for: curated lipid structures and lipid classes

### FooDB (`foodb`)
- Best for: food constituents and food chemistry

### Phenol-Explorer (`phenol_explorer`)
- Best for: polyphenols and food bioactives

### PTFI Discover (`ptfi`)
- Best for: food composition and food metabolomics

## Notes on interpretation

- `Type` is a coarse capability summary matching the current resource categories: `interaction`, `association`, `annotation`.
- `Annotation ontologies` means ontology vocabularies whose terms appear as annotations.