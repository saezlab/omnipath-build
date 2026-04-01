# Target schema identifier mapping investigation

Generated from current per-source target-schema outputs in `data_v2/target_schema/*`.

## Key conclusions

- Protein standardization target should be **UniProt primary accession**.
- Small-molecule/lipid standardization target should be **Standard InChI**.
- Protein name-based mapping is only safe for narrow cases, especially `Gene Name Primary + taxonomy`.
- Chemical mapping has high payoff because many sources already carry ChEBI/HMDB/LipidMaps/PubChem-style identifiers.
- Secondary/non-canonical/scope-mismatched UniProt accessions are a real issue: some sources carry UniProt-looking accessions that are not present as canonical accessions in the current UniProt reference snapshot, so they would fail direct resolution unless we normalize them.

## Reference-map quality

### Protein reference keys to canonical UniProt

| Key type | Unique keys | Ambiguous keys |
|---|---:|---:|
| MI:1097:Uniprot | 45,917 | 0 |
| OM:0221:Uniprot Entry Name | 45,917 | 0 |
| OM:0200:Gene Name Primary | 45,115 | 102 |
| MI:0477:Entrez | 42,873 | 133 |
| MI:0476:Ensembl | 94,858 | 55 |

### Chemical reference keys to Standard InChI

| Key type | Unique keys | Ambiguous keys |
|---|---:|---:|
| MI:0474:Chebi | 182,761 | 0 |
| OM:0004:Hmdb | 18,978 | 381 |
| OM:0003:Lipidmaps | 12,317 | 88 |
| OM:0009:Swisslipids | 109 | 0 |
| OM:0002:Pubchem Compound | 296 | 0 |
| MI:0730:Pubchem | 100 | 0 |
| MI:0967:Chembl Compound | 19 | 0 |
| MI:2002:Drugbank | 65 | 0 |
| MI:2012:Kegg Compound | 13,833 | 855 |
| OM:0006:Bindingdb | 100 | 0 |

## Per-source report

### bindingdb

- Entities: **102**
- Identifier rows: **626**
- Entity types:
  - `MI:0328:Small Molecule`: 100
  - `MI:0326:Protein`: 2

#### Protein-side mapping

- Protein/gene/RNA/DNA entities: **2**
- Already carrying strong resolvable IDs (UniProt / entry name / Entrez / Ensembl): **2**
- Entities lacking strong protein IDs and therefore needing mapping: **0**
- Entities currently mappable via reference maps: **0**
- Candidate mapping routes: none from current identifiers
- Source UniProt identifiers not present as canonical UniProt accessions in the UniProt reference snapshot: **2**
- Of those, uniquely inferable to a canonical UniProt via alternate IDs on the same entity (likely secondary/obsolete accessions): **0**

#### Chemical/lipid-side mapping

- Small-molecule/lipid entities: **100**
- Already carrying Standard InChI: **100**
- Entities lacking Standard InChI and therefore needing mapping: **0**
- Entities currently mappable to Standard InChI via reference maps: **0**
- Candidate mapping routes: none from current identifiers

#### Recommendation

- Add a UniProt accession normalization step to handle secondary/obsolete accessions before canonical resolution.

### cellphonedb

- Entities: **4,119**
- Identifier rows: **4,119**
- Entity types:
  - `MI:0314:Complex`: 2,768
  - `MI:0326:Protein`: 1,351

#### Protein-side mapping

- Protein/gene/RNA/DNA entities: **1,351**
- Already carrying strong resolvable IDs (UniProt / entry name / Entrez / Ensembl): **1,351**
- Entities lacking strong protein IDs and therefore needing mapping: **0**
- Entities currently mappable via reference maps: **0**
- Candidate mapping routes: none from current identifiers
- Source UniProt identifiers not present as canonical UniProt accessions in the UniProt reference snapshot: **0**
- Of those, uniquely inferable to a canonical UniProt via alternate IDs on the same entity (likely secondary/obsolete accessions): **0**

#### Chemical/lipid-side mapping

- Small-molecule/lipid entities: **0**
- No small-molecule/lipid entities.

#### Recommendation

- No immediate identifier-mapping action needed.

### chebi

- Entities: **194,065**
- Identifier rows: **1,575,548**
- Entity types:
  - `MI:0328:Small Molecule`: 194,065

#### Protein-side mapping

- Protein/gene/RNA/DNA entities: **0**
- No protein/gene/RNA/DNA entities.

#### Chemical/lipid-side mapping

- Small-molecule/lipid entities: **194,065**
- Already carrying Standard InChI: **181,273**
- Entities lacking Standard InChI and therefore needing mapping: **12,792**
- Entities currently mappable to Standard InChI via reference maps: **98**
- Candidate mapping routes:
  - `MI:2012:Kegg Compound` -> `MI:2010:Standard Inchi`: 91 entities
  - `OM:0004:Hmdb` -> `MI:2010:Standard Inchi`: 9 entities
  - `OM:0003:Lipidmaps` -> `MI:2010:Standard Inchi`: 2 entities

#### Recommendation

- Chemical/lipid structure mapping is worthwhile here.

### corum

- Entities: **6,590**
- Identifier rows: **9,510**
- Entity types:
  - `MI:0326:Protein`: 3,674
  - `MI:0314:Complex`: 2,916

#### Protein-side mapping

- Protein/gene/RNA/DNA entities: **3,674**
- Already carrying strong resolvable IDs (UniProt / entry name / Entrez / Ensembl): **3,674**
- Entities lacking strong protein IDs and therefore needing mapping: **0**
- Entities currently mappable via reference maps: **0**
- Candidate mapping routes: none from current identifiers
- Source UniProt identifiers not present as canonical UniProt accessions in the UniProt reference snapshot: **24**
- Of those, uniquely inferable to a canonical UniProt via alternate IDs on the same entity (likely secondary/obsolete accessions): **0**

#### Chemical/lipid-side mapping

- Small-molecule/lipid entities: **0**
- No small-molecule/lipid entities.

#### Recommendation

- Add a UniProt accession normalization step to handle secondary/obsolete accessions before canonical resolution.

### foodb

- Entities: **10,045**
- Identifier rows: **57,069**
- Entity types:
  - `MI:0328:Small Molecule`: 9,945
  - `OM:0020:Food`: 100

#### Protein-side mapping

- Protein/gene/RNA/DNA entities: **0**
- No protein/gene/RNA/DNA entities.

#### Chemical/lipid-side mapping

- Small-molecule/lipid entities: **9,945**
- Already carrying Standard InChI: **0**
- Entities lacking Standard InChI and therefore needing mapping: **9,945**
- Entities currently mappable to Standard InChI via reference maps: **712**
- Candidate mapping routes:
  - `MI:0474:Chebi` -> `MI:2010:Standard Inchi`: 680 entities
  - `MI:2012:Kegg Compound` -> `MI:2010:Standard Inchi`: 272 entities

#### Recommendation

- Chemical/lipid structure mapping is worthwhile here.

### guidetopharma

- Entities: **399**
- Identifier rows: **2,377**
- Entity types:
  - `MI:0326:Protein`: 200
  - `MI:0328:Small Molecule`: 199

#### Protein-side mapping

- Protein/gene/RNA/DNA entities: **200**
- Already carrying strong resolvable IDs (UniProt / entry name / Entrez / Ensembl): **94**
- Entities lacking strong protein IDs and therefore needing mapping: **106**
- Entities currently mappable via reference maps: **0**
- Candidate mapping routes: none from current identifiers
- Source UniProt identifiers not present as canonical UniProt accessions in the UniProt reference snapshot: **0**
- Of those, uniquely inferable to a canonical UniProt via alternate IDs on the same entity (likely secondary/obsolete accessions): **0**

#### Chemical/lipid-side mapping

- Small-molecule/lipid entities: **199**
- Already carrying Standard InChI: **99**
- Entities lacking Standard InChI and therefore needing mapping: **100**
- Entities currently mappable to Standard InChI via reference maps: **0**
- Candidate mapping routes: none from current identifiers

#### Recommendation

- Protein mapping would require source-specific enrichment or should stay unresolved.
- Chemical/lipid mapping would require source-specific enrichment or should stay unresolved.

### hmdb

- Entities: **100**
- Identifier rows: **5,041**
- Entity types:
  - `MI:0328:Small Molecule`: 100

#### Protein-side mapping

- Protein/gene/RNA/DNA entities: **0**
- No protein/gene/RNA/DNA entities.

#### Chemical/lipid-side mapping

- Small-molecule/lipid entities: **100**
- Already carrying Standard InChI: **100**
- Entities lacking Standard InChI and therefore needing mapping: **0**
- Entities currently mappable to Standard InChI via reference maps: **0**
- Candidate mapping routes: none from current identifiers

#### Recommendation

- No immediate identifier-mapping action needed.

### hpo

- Entities: **5,199**
- Identifier rows: **10,391**
- Entity types:
  - `MI:0326:Protein`: 5,199

#### Protein-side mapping

- Protein/gene/RNA/DNA entities: **5,199**
- Already carrying strong resolvable IDs (UniProt / entry name / Entrez / Ensembl): **5,199**
- Entities lacking strong protein IDs and therefore needing mapping: **0**
- Entities currently mappable via reference maps: **0**
- Candidate mapping routes: none from current identifiers
- Source UniProt identifiers not present as canonical UniProt accessions in the UniProt reference snapshot: **0**
- Of those, uniquely inferable to a canonical UniProt via alternate IDs on the same entity (likely secondary/obsolete accessions): **0**

#### Chemical/lipid-side mapping

- Small-molecule/lipid entities: **0**
- No small-molecule/lipid entities.

#### Recommendation

- No immediate identifier-mapping action needed.

### intact

- Entities: **591**
- Identifier rows: **4,495**
- Entity types:
  - `MI:0326:Protein`: 591

#### Protein-side mapping

- Protein/gene/RNA/DNA entities: **591**
- Already carrying strong resolvable IDs (UniProt / entry name / Entrez / Ensembl): **578**
- Entities lacking strong protein IDs and therefore needing mapping: **13**
- Entities currently mappable via reference maps: **0**
- Candidate mapping routes: none from current identifiers
- Source UniProt identifiers not present as canonical UniProt accessions in the UniProt reference snapshot: **2,176**
- Of those, uniquely inferable to a canonical UniProt via alternate IDs on the same entity (likely secondary/obsolete accessions): **0**

#### Chemical/lipid-side mapping

- Small-molecule/lipid entities: **0**
- No small-molecule/lipid entities.

#### Recommendation

- Protein mapping would require source-specific enrichment or should stay unresolved.
- Add a UniProt accession normalization step to handle secondary/obsolete accessions before canonical resolution.

### lipidmaps

- Entities: **100**
- Identifier rows: **970**
- Entity types:
  - `OM:0011:Lipid`: 100

#### Protein-side mapping

- Protein/gene/RNA/DNA entities: **0**
- No protein/gene/RNA/DNA entities.

#### Chemical/lipid-side mapping

- Small-molecule/lipid entities: **100**
- Already carrying Standard InChI: **100**
- Entities lacking Standard InChI and therefore needing mapping: **0**
- Entities currently mappable to Standard InChI via reference maps: **0**
- Candidate mapping routes: none from current identifiers

#### Recommendation

- No immediate identifier-mapping action needed.

### mebocost

- Entities: **3,166**
- Identifier rows: **7,925**
- Entity types:
  - `MI:0328:Small Molecule`: 1,583
  - `MI:0326:Protein`: 1,583

#### Protein-side mapping

- Protein/gene/RNA/DNA entities: **1,583**
- Already carrying strong resolvable IDs (UniProt / entry name / Entrez / Ensembl): **0**
- Entities lacking strong protein IDs and therefore needing mapping: **1,583**
- Entities currently mappable via reference maps: **1,564**
- Candidate mapping routes:
  - `OM:0200:Gene Name Primary` -> canonical UniProt: 1,564 entities
- Source UniProt identifiers not present as canonical UniProt accessions in the UniProt reference snapshot: **0**
- Of those, uniquely inferable to a canonical UniProt via alternate IDs on the same entity (likely secondary/obsolete accessions): **0**

#### Chemical/lipid-side mapping

- Small-molecule/lipid entities: **1,583**
- Already carrying Standard InChI: **0**
- Entities lacking Standard InChI and therefore needing mapping: **1,583**
- Entities currently mappable to Standard InChI via reference maps: **1,191**
- Candidate mapping routes:
  - `OM:0004:Hmdb` -> `MI:2010:Standard Inchi`: 1,191 entities

#### Recommendation

- Protein mapping is worthwhile here.
- Chemical/lipid structure mapping is worthwhile here.

### neuronchat

- Entities: **746**
- Identifier rows: **746**
- Entity types:
  - `MI:0314:Complex`: 746

#### Protein-side mapping

- Protein/gene/RNA/DNA entities: **0**
- No protein/gene/RNA/DNA entities.

#### Chemical/lipid-side mapping

- Small-molecule/lipid entities: **0**
- No small-molecule/lipid entities.

#### Recommendation

- No immediate identifier-mapping action needed.

### omnipath_ontology

- Entities: **0**
- Identifier rows: **0**
- Entity types: none

#### Protein-side mapping

- Protein/gene/RNA/DNA entities: **0**
- No protein/gene/RNA/DNA entities.

#### Chemical/lipid-side mapping

- Small-molecule/lipid entities: **0**
- No small-molecule/lipid entities.

#### Recommendation

- No immediate identifier-mapping action needed.

### phenol_explorer

- Entities: **7,945**
- Identifier rows: **48,297**
- Entity types:
  - `MI:0328:Small Molecule`: 7,486
  - `OM:0020:Food`: 459

#### Protein-side mapping

- Protein/gene/RNA/DNA entities: **0**
- No protein/gene/RNA/DNA entities.

#### Chemical/lipid-side mapping

- Small-molecule/lipid entities: **7,486**
- Already carrying Standard InChI: **0**
- Entities lacking Standard InChI and therefore needing mapping: **7,486**
- Entities currently mappable to Standard InChI via reference maps: **3,561**
- Candidate mapping routes:
  - `MI:0474:Chebi` -> `MI:2010:Standard Inchi`: 3,554 entities
  - `OM:0002:Pubchem Compound` -> `MI:2010:Standard Inchi`: 57 entities

#### Recommendation

- Chemical/lipid structure mapping is worthwhile here.

### ptfi

- Entities: **119,732**
- Identifier rows: **268,560**
- Entity types:
  - `MI:0328:Small Molecule`: 119,632
  - `OM:0020:Food`: 100

#### Protein-side mapping

- Protein/gene/RNA/DNA entities: **0**
- No protein/gene/RNA/DNA entities.

#### Chemical/lipid-side mapping

- Small-molecule/lipid entities: **119,632**
- Already carrying Standard InChI: **0**
- Entities lacking Standard InChI and therefore needing mapping: **119,632**
- Entities currently mappable to Standard InChI via reference maps: **0**
- Candidate mapping routes: none from current identifiers

#### Recommendation

- Chemical/lipid mapping would require source-specific enrichment or should stay unresolved.

### reactome

- Entities: **42,024**
- Identifier rows: **158,987**
- Entity types:
  - `OM:0015:Reaction`: 15,666
  - `MI:0314:Complex`: 12,899
  - `MI:0326:Protein`: 5,780
  - `MI:0328:Small Molecule`: 3,174
  - `OM:0016:Physical Entity`: 1,941
  - `OM:0010:Protein Family`: 1,436
  - `MI:0681:Dna`: 872
  - `MI:0320:Rna`: 236
  - `OM:0019:Degradation`: 20

#### Protein-side mapping

- Protein/gene/RNA/DNA entities: **5,780**
- Already carrying strong resolvable IDs (UniProt / entry name / Entrez / Ensembl): **5,745**
- Entities lacking strong protein IDs and therefore needing mapping: **35**
- Entities currently mappable via reference maps: **0**
- Candidate mapping routes: none from current identifiers
- Source UniProt identifiers not present as canonical UniProt accessions in the UniProt reference snapshot: **403**
- Of those, uniquely inferable to a canonical UniProt via alternate IDs on the same entity (likely secondary/obsolete accessions): **0**

#### Chemical/lipid-side mapping

- Small-molecule/lipid entities: **3,174**
- Already carrying Standard InChI: **0**
- Entities lacking Standard InChI and therefore needing mapping: **3,174**
- Entities currently mappable to Standard InChI via reference maps: **2,116**
- Candidate mapping routes:
  - `MI:0474:Chebi` -> `MI:2010:Standard Inchi`: 2,116 entities

#### Recommendation

- Protein mapping would require source-specific enrichment or should stay unresolved.
- Chemical/lipid structure mapping is worthwhile here.
- Add a UniProt accession normalization step to handle secondary/obsolete accessions before canonical resolution.

### signor

- Entities: **16,173**
- Identifier rows: **46,031**
- Entity types:
  - `MI:0326:Protein`: 9,290
  - `MI:0328:Small Molecule`: 5,574
  - `MI:0314:Complex`: 799
  - `MI:2261:Phenotype`: 220
  - `MI:0320:Rna`: 156
  - `OM:0010:Protein Family`: 107
  - `MI:2260:Stimulus`: 27

#### Protein-side mapping

- Protein/gene/RNA/DNA entities: **9,290**
- Already carrying strong resolvable IDs (UniProt / entry name / Entrez / Ensembl): **9,290**
- Entities lacking strong protein IDs and therefore needing mapping: **0**
- Entities currently mappable via reference maps: **0**
- Candidate mapping routes: none from current identifiers
- Source UniProt identifiers not present as canonical UniProt accessions in the UniProt reference snapshot: **28,898**
- Of those, uniquely inferable to a canonical UniProt via alternate IDs on the same entity (likely secondary/obsolete accessions): **0**

#### Chemical/lipid-side mapping

- Small-molecule/lipid entities: **5,574**
- Already carrying Standard InChI: **0**
- Entities lacking Standard InChI and therefore needing mapping: **5,574**
- Entities currently mappable to Standard InChI via reference maps: **4,463**
- Candidate mapping routes:
  - `MI:0474:Chebi` -> `MI:2010:Standard Inchi`: 4,463 entities

#### Recommendation

- Chemical/lipid structure mapping is worthwhile here.
- Add a UniProt accession normalization step to handle secondary/obsolete accessions before canonical resolution.

### swisslipids

- Entities: **100**
- Identifier rows: **751**
- Entity types:
  - `OM:0011:Lipid`: 100

#### Protein-side mapping

- Protein/gene/RNA/DNA entities: **0**
- No protein/gene/RNA/DNA entities.

#### Chemical/lipid-side mapping

- Small-molecule/lipid entities: **100**
- Already carrying Standard InChI: **81**
- Entities lacking Standard InChI and therefore needing mapping: **19**
- Entities currently mappable to Standard InChI via reference maps: **2**
- Candidate mapping routes:
  - `MI:0474:Chebi` -> `MI:2010:Standard Inchi`: 2 entities

#### Recommendation

- Chemical/lipid structure mapping is worthwhile here.

### uniprot

- Entities: **45,917**
- Identifier rows: **615,651**
- Entity types:
  - `MI:0326:Protein`: 45,917

#### Protein-side mapping

- Protein/gene/RNA/DNA entities: **45,917**
- Already carrying strong resolvable IDs (UniProt / entry name / Entrez / Ensembl): **45,917**
- Entities lacking strong protein IDs and therefore needing mapping: **0**
- Entities currently mappable via reference maps: **0**
- Candidate mapping routes: none from current identifiers
- Source UniProt identifiers not present as canonical UniProt accessions in the UniProt reference snapshot: **0**
- Of those, uniquely inferable to a canonical UniProt via alternate IDs on the same entity (likely secondary/obsolete accessions): **0**

#### Chemical/lipid-side mapping

- Small-molecule/lipid entities: **0**
- No small-molecule/lipid entities.

#### Recommendation

- Use as the primary protein reference authority.

### wikipathways

- Entities: **39,502**
- Identifier rows: **237,168**
- Entity types:
  - `MI:0328:Small Molecule`: 27,127
  - `MI:0326:Protein`: 11,176
  - `MI:0314:Complex`: 611
  - `OM:0016:Physical Entity`: 330
  - `MI:0320:Rna`: 258

#### Protein-side mapping

- Protein/gene/RNA/DNA entities: **11,176**
- Already carrying strong resolvable IDs (UniProt / entry name / Entrez / Ensembl): **9,623**
- Entities lacking strong protein IDs and therefore needing mapping: **1,553**
- Entities currently mappable via reference maps: **0**
- Candidate mapping routes: none from current identifiers
- Source UniProt identifiers not present as canonical UniProt accessions in the UniProt reference snapshot: **31,118**
- Of those, uniquely inferable to a canonical UniProt via alternate IDs on the same entity (likely secondary/obsolete accessions): **3,781**
- Example likely secondary/obsolete UniProt accessions:
  - source UniProt `F8WD74` -> canonical UniProt `P78330` (tax=9606, display_name=PSPH)
  - source UniProt `H0YDY3` -> canonical UniProt `Q13490` (tax=9606, display_name=BIRC2)
  - source UniProt `A0A087WS56` -> canonical UniProt `P11276` (tax=10090, display_name=Fn1)
  - source UniProt `E9Q8Z8` -> canonical UniProt `P30999` (tax=10090, display_name=Ctnnd1)
  - source UniProt `G3V355` -> canonical UniProt `Q9NQX3` (tax=9606, display_name=GPHN)
  - source UniProt `H3BM92` -> canonical UniProt `O15305` (tax=9606, display_name=PMM2)
  - source UniProt `H0Y967` -> canonical UniProt `P43250` (tax=9606, display_name=GRK6)
  - source UniProt `E9PSE0` -> canonical UniProt `Q9BUB5` (tax=9606, display_name=Mnk1)
  - source UniProt `E5RIE5` -> canonical UniProt `Q9P0J1` (tax=9606, display_name=PDH)
  - source UniProt `A0A6Q8PFR8` -> canonical UniProt `Q92597` (tax=9606, display_name=NDRG1)

#### Chemical/lipid-side mapping

- Small-molecule/lipid entities: **27,127**
- Already carrying Standard InChI: **0**
- Entities lacking Standard InChI and therefore needing mapping: **27,127**
- Entities currently mappable to Standard InChI via reference maps: **22,333**
- Candidate mapping routes:
  - `MI:0474:Chebi` -> `MI:2010:Standard Inchi`: 22,148 entities
  - `MI:2012:Kegg Compound` -> `MI:2010:Standard Inchi`: 13,779 entities
  - `OM:0004:Hmdb` -> `MI:2010:Standard Inchi`: 12,197 entities
  - `OM:0002:Pubchem Compound` -> `MI:2010:Standard Inchi`: 2,474 entities

#### Recommendation

- Protein mapping would require source-specific enrichment or should stay unresolved.
- Chemical/lipid structure mapping is worthwhile here.
- Add a UniProt accession normalization step to handle secondary/obsolete accessions before canonical resolution.

## Notes on secondary and non-canonical UniProt accessions

The current UniProt target-schema source only emits the canonical `Entry` accession plus other cross-references; it does **not** emit secondary accessions from UniProt itself. In addition, the current UniProt source snapshot is restricted to reviewed proteins from human, mouse, and rat. Therefore a UniProt-like accession missing from the reference snapshot can reflect several different situations: genuine secondary/old accession, isoform-specific accession, out-of-review-scope accession, out-of-taxonomy-scope accession, or a malformed/source-specific value incorrectly typed as UniProt.

So the counts above should be interpreted as **non-canonical or out-of-reference-scope UniProt IDs**, not automatically as confirmed secondary accessions.

Recommended normalization order for protein IDs before cross-source resolution:

1. If the source UniProt accession exists in canonical UniProt accessions, keep it.
2. Else, if it looks like an isoform accession and the base accession exists in the reference set, normalize `P12345-2 -> P12345`.
3. Else resolve it through a UniProt secondary-accession -> primary-accession map.
4. Else try fallback unique mappings from UniProt entry name, Entrez+taxonomy, Ensembl+taxonomy, or Gene Name Primary+taxonomy.
5. If multiple primary accessions are possible, do not auto-map.

This normalization should happen before choosing the target-schema canonical identifier and before deduplication.

## How original pypath handled ID translation

The original `pypath` codebase already had a fairly rich identifier translation layer centered on `pypath.utils.mapping.Mapper`.

Key files:

- `pypath/pypath/utils/mapping.py`
- `pypath/pypath/internals/maps.py`
- `pypath/pypath/inputs/uniprot.py`
- `pypath/pypath/inputs/uniprot_idmapping.py`

### Main design

`pypath` used a general-purpose mapping service (`Mapper`) which:

- loaded mapping tables on demand;
- cached them;
- translated between many identifier systems;
- supported organism-aware mappings;
- used UniProt as a major hub for protein ID conversion;
- used UniChem / HMDB / RaMP / custom file mappings for chemicals and other entities.

The built-in mapping registry in `pypath/pypath/internals/maps.py` defines explicit conversion routes such as:

- `genesymbol -> uniprot`
- `entrez -> uniprot`
- `refseqp -> uniprot`
- `uniprot-entry -> uniprot`
- `uniprot-sec -> uniprot-pri`

That last one is especially important for us: original `pypath` explicitly modeled **secondary UniProt accession -> primary UniProt accession** translation.

### UniProt cleanup pipeline

The old `Mapper` had a UniProt cleanup routine that effectively did the following steps for UniProt targets:

1. **Translate secondary UniProt IDs to primary IDs** using `primary_uniprot()`.
   - This uses `map_name(..., id_type='uniprot-sec', target_id_type='uniprot-pri')`.
   - The underlying pairs come from `pypath.inputs.uniprot.get_uniprot_sec()`.
2. **Optionally translate TrEMBL IDs to SwissProt** via gene symbols.
   - Implemented in `trembl_swissprot()`.
3. **Optionally translate deleted / obsolete UniProt IDs** by loading archived UniProt information, extracting gene symbol and remapping back to current UniProt.
   - Implemented in `translate_deleted_uniprots_by_genesymbol()`.
4. **Check validity against the organism proteome** unless configured to keep invalid UniProt IDs.

So in the original pypath design, UniProt normalization was not just direct exact matching: there was an explicit cleanup and rescue path for non-primary, deleted, and less-preferred accessions.

### Relevant original UniProt functions

- `pypath.inputs.uniprot.get_uniprot_sec(organism=9606)`
  - downloads and yields `(secondary_accession, primary_accession)` pairs
- `Mapper.primary_uniprot(...)`
  - normalizes secondary accessions to primary
- `Mapper.trembl_swissprot(...)`
  - tries to replace TrEMBL with SwissProt through gene symbols
- `Mapper.translate_deleted_uniprot_by_genesymbol(...)`
  - attempts recovery of deleted accessions through archived gene symbol information

### Implication for our target-schema mapping plan

The original `pypath` behavior strongly supports adding an explicit **UniProt normalization stage** before canonical protein resolution in target schema.

A good target-schema equivalent would be:

1. normalize exact canonical UniProt IDs;
2. normalize isoform-style IDs to base accession where appropriate;
3. apply secondary UniProt accession -> primary accession mapping;
4. optionally handle deleted/obsolete accessions if we want parity with original `pypath` behavior;
5. then fall back to unique entry-name / Entrez / Ensembl / gene-symbol mappings.

### Chemical translation in original pypath

For chemicals, original `pypath` did not rely on one single universal canonicalizer in the same way as UniProt for proteins, but it already exposed translation backends via:

- **UniChem** (`pypath.inputs.unichem`, `UnichemMapping`)
- **HMDB** mapping tables
- **RaMP** mapping tables
- source-specific mapping functions in some input modules

So the old design also supports our plan conceptually:
- proteins were normalized through a strong UniProt-centered translation layer;
- chemical translation relied on cross-reference mapping resources rather than a single protein-like authority.