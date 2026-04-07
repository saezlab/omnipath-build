# OmniPath resources

Use this as a quick guide for choosing source datasets.

## Interaction-focused resources

### SIGNOR (`signor`)
- Type: interaction + annotation
- Best for: causal signaling, direction/sign, pathway signaling context

### Reactome (`reactome`)
- Type: interaction + annotation
- Best for: curated pathways, reactions, pathway membership

### WikiPathways (`wikipathways`)
- Type: interaction + annotation
- Best for: community-curated pathways and pathway structure

### IntAct (`intact`)
- Type: interaction + annotation
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
- Best for: protein function, localization, disease associations, cross-references

### HPO (`hpo`)
- Type: annotation
- Best for: gene-phenotype associations and disease phenotype terms

### ChEBI (`chebi`)
- Type: annotation
- Best for: small-molecule ontology and chemical classification

### CORUM (`corum`)
- Type: annotation
- Best for: mammalian protein complexes

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

## Quick selection hints

- user asks about **causal signaling** -> start with `signor`
- user asks about **pathways** -> start with `reactome` and `wikipathways`
- user asks about **protein complexes** -> start with `corum`
- user asks about **phenotypes or disease terms** -> start with `hpo`
- user asks about **protein function or localization** -> start with `uniprot`
- user asks about **small-molecule ontology or ChEBI terms** -> start with `chebi`
- user asks about **drug-target or ligand binding** -> start with `bindingdb`, `guidetopharma`, or `chembl`
- user asks about **chemical-protein networks** -> start with `stitch`
- user asks about **metabolites** -> consider `hmdb`, `chebi`, `lipidmaps`, `swisslipids`, and then interaction resources such as `bindingdb` or `stitch`
