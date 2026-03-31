# Target schema example rows

## bindingdb

### entities

```json
{
  "entity_id": 1,
  "entity_type": "MI:0328:Small Molecule",
  "display_name": null,
  "canonical_identifier": "InChI=1S/C31H42N2O7/c34-27(35)17-9-3-11-19-32-25(21-23-13-5-1-6-14-23)29(38)30(39)26(22-24-15-7-2-8-16-24)33(31(32)40)20-12-4-10-18-28(36)37/h1-2,5-8,13-16,25-26,29-30,38-39H,3-4,9-12,17-22H2,(H,34,35)(H,36,37)",
  "canonical_identifier_type": "MI:2010:Standard Inchi",
  "taxonomy_id": null,
  "source": "bindingdb"
}
```

### entity_identifiers

```json
{
  "entity_id": 1,
  "identifier": "O[C@@H]1[C@@H](O)[C@@H](Cc2ccccc2)N(CCCCCC(O)=O)C(=O)N(CCCCCC(O)=O)[C@@H]1Cc1ccccc1",
  "identifier_type": "MI:0239:Smiles",
  "is_canonical": false,
  "source": "bindingdb"
}
```

### interactions

```json
{
  "interaction_id": 1,
  "entity_a_id": 1,
  "entity_b_id": 2,
  "direction": null,
  "sign": null,
  "mechanism_term": null,
  "statement_term": null,
  "record_attributes": [
    {
      "term": "MI:0643:Ki",
      "value": "0.24",
      "unit": "OM:0722"
    },
    {
      "term": "MI:0837:Ph",
      "value": "5.5000",
      "unit": null
    },
    {
      "term": "OM:0701:Temperature Celsius",
      "value": "37.00 C",
      "unit": "OM:0725"
    },
    {
      "term": "MI:0612:Comment",
      "value": "Curated from the literature by BindingDB",
      "unit": null
    }
  ],
  "entity_a_attributes": null,
  "entity_b_attributes": null,
  "evidence": [
    {
      "term": "MI:0446:Pubmed",
      "value": "8784449",
      "unit": null
    },
    {
      "term": "MI:0574:Doi",
      "value": "10.1021/jm9602571",
      "unit": null
    }
  ],
  "source": "bindingdb"
}
```

### associations

_empty_

### annotations

_empty_

## cellphonedb

### entities

```json
{
  "entity_id": 1,
  "entity_type": "MI:0314:Complex",
  "display_name": "Dehydroepiandrosterone_bySTS",
  "canonical_identifier": "Dehydroepiandrosterone_bySTS",
  "canonical_identifier_type": "OM:0202:Name",
  "taxonomy_id": null,
  "source": "cellphonedb"
}
```

### entity_identifiers

```json
{
  "entity_id": 1,
  "identifier": "Dehydroepiandrosterone_bySTS",
  "identifier_type": "OM:0202:Name",
  "is_canonical": true,
  "source": "cellphonedb"
}
```

### interactions

```json
{
  "interaction_id": 1,
  "entity_a_id": 1070,
  "entity_b_id": 1071,
  "direction": null,
  "sign": null,
  "mechanism_term": null,
  "statement_term": null,
  "record_attributes": [
    {
      "term": "OM:1207:Interaction Annotation",
      "value": "Adhesion by Cadherin",
      "unit": null
    },
    {
      "term": "OM:1207:Interaction Annotation",
      "value": "Adhesion-Adhesion",
      "unit": null
    },
    {
      "term": "MI:0612:Comment",
      "value": "CellPhoneDBcore<=4.1",
      "unit": null
    }
  ],
  "entity_a_attributes": null,
  "entity_b_attributes": null,
  "evidence": [
    {
      "term": "MI:0446:Pubmed",
      "value": "12392763",
      "unit": null
    }
  ],
  "source": "cellphonedb"
}
```

### associations

```json
{
  "association_id": 1,
  "parent_entity_id": 1,
  "member_entity_id": 2,
  "role_term_id": null,
  "stoichiometry": null,
  "record_attributes": null,
  "parent_attributes": [
    {
      "term": "MI:0612:Comment",
      "value": "CellPhoneDBcore<=4.1",
      "unit": null
    }
  ],
  "member_attributes": null,
  "evidence": null,
  "source": "cellphonedb"
}
```

### annotations

_empty_

## chebi

### entities

```json
{
  "entity_id": 1,
  "entity_type": "MI:0328:Small Molecule",
  "display_name": "(+)-Atherospermoline",
  "canonical_identifier": "InChI=1S/C36H38N2O6/c1-37-13-11-23-18-31(41-3)32-20-26(23)27(37)15-21-5-8-25(9-6-21)43-30-17-22(7-10-29(30)39)16-28-34-24(12-14-38(28)2)19-33(42-4)35(40)36(34)44-32/h5-10,17-20,27-28,39-40H,11-16H2,1-4H3/t27-,28-/m0/s1",
  "canonical_identifier_type": "MI:2010:Standard Inchi",
  "taxonomy_id": null,
  "source": "chebi"
}
```

### entity_identifiers

```json
{
  "entity_id": 1,
  "identifier": "COc1cc2c3cc1Oc1c(O)c(OC)cc4c1[C@H](Cc1ccc(O)c(c1)Oc1ccc(cc1)C[C@@H]3N(C)CC2)N(C)CC4",
  "identifier_type": "MI:0239:Smiles",
  "is_canonical": false,
  "source": "chebi"
}
```

### interactions

_empty_

### associations

_empty_

### annotations

```json
{
  "subject_type": "entity",
  "subject_id": 143209,
  "cv_term": "CHEBI:23367:CHEBI:23367",
  "source": "chebi"
}
```

## corum

### entities

```json
{
  "entity_id": 1,
  "entity_type": "MI:0314:Complex",
  "display_name": "BCL6-HDAC4 complex",
  "canonical_identifier": "1",
  "canonical_identifier_type": "OM:OM",
  "taxonomy_id": "9606",
  "source": "corum"
}
```

### entity_identifiers

```json
{
  "entity_id": 1,
  "identifier": "1",
  "identifier_type": "OM:0027:Corum",
  "is_canonical": true,
  "source": "corum"
}
```

### interactions

_empty_

### associations

```json
{
  "association_id": 1,
  "parent_entity_id": 1,
  "member_entity_id": 2,
  "role_term_id": null,
  "stoichiometry": null,
  "record_attributes": null,
  "parent_attributes": [
    {
      "term": "OM:0617:Funcat",
      "value": "DNA conformation modification (e.g. chromatin)",
      "unit": null
    },
    {
      "term": "OM:0617:Funcat",
      "value": "transcription repression",
      "unit": null
    },
    {
      "term": "OM:0617:Funcat",
      "value": "organization of chromosome structure",
      "unit": null
    },
    {
      "term": "OM:0617:Funcat",
      "value": "B-cell",
      "unit": null
    },
    {
      "term": "OM:0617:Funcat",
      "value": "nucleus",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "GO:0006265",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "GO:0045892",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "GO:0051276",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "GO:0030183",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "GO:0005634",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "GO:0016575",
      "unit": null
    }
  ],
  "member_attributes": null,
  "evidence": [
    {
      "term": "MI:0446:Pubmed",
      "value": "11929873",
      "unit": null
    }
  ],
  "source": "corum"
}
```

### annotations

```json
{
  "subject_type": "entity",
  "subject_id": 7199,
  "cv_term": "GO:0006265:GO:0006265",
  "source": "corum"
}
```

## foodb

### entities

```json
{
  "entity_id": 1,
  "entity_type": "OM:0020:Food",
  "display_name": "Angelica",
  "canonical_identifier": "357850.0",
  "canonical_identifier_type": "OM:OM",
  "taxonomy_id": "357850.0",
  "source": "foodb"
}
```

### entity_identifiers

```json
{
  "entity_id": 1,
  "identifier": "Angelica",
  "identifier_type": "OM:0202:Name",
  "is_canonical": false,
  "source": "foodb"
}
```

### interactions

_empty_

### associations

```json
{
  "association_id": 1,
  "parent_entity_id": 1,
  "member_entity_id": 2,
  "role_term_id": null,
  "stoichiometry": null,
  "record_attributes": null,
  "parent_attributes": [
    {
      "term": "OM:0613:Description",
      "value": "Angelica is a genus of about 60 species of tall biennial and perennial herbs in the family Apiaceae, native to temperate and subarctic regions of the Northern Hemisphere, reaching as far north as Iceland and Lapland. They grow to 1–3 m tall, with large bipinnate leaves and large compound umbels of white or greenish-white flowers. Some species can be found in purple moor and rush pastures.",
      "unit": null
    },
    {
      "term": "OM:0665:Scientific Name",
      "value": "Angelica keiskei",
      "unit": null
    },
    {
      "term": "OM:0660:Food Class",
      "value": "Herbs and Spices",
      "unit": null
    },
    {
      "term": "OM:0661:Food Subclass",
      "value": "Herbs",
      "unit": null
    }
  ],
  "member_attributes": [
    {
      "term": "OM:0602:Mass Dalton",
      "value": "270.05282343",
      "unit": null
    },
    {
      "term": "OM:0662:Compound Class",
      "value": "Flavonoids",
      "unit": null
    },
    {
      "term": "OM:0663:Compound Subclass",
      "value": "Flavones",
      "unit": null
    },
    {
      "term": "OM:0680:Concentration Mean",
      "value": "0.0",
      "unit": null
    },
    {
      "term": "OM:0684:Concentration Unit",
      "value": "mg/100g",
      "unit": null
    },
    {
      "term": "OM:0687:Experimental Method",
      "value": "Chromatography after hydrolysis",
      "unit": null
    }
  ],
  "evidence": null,
  "source": "foodb"
}
```

### annotations

_empty_

## guidetopharma

### entities

```json
{
  "entity_id": 1,
  "entity_type": "MI:0328:Small Molecule",
  "display_name": null,
  "canonical_identifier": "8752",
  "canonical_identifier_type": "OM:OM",
  "taxonomy_id": null,
  "source": "guidetopharma"
}
```

### entity_identifiers

```json
{
  "entity_id": 1,
  "identifier": "8752",
  "identifier_type": "OM:0008:Guidetopharma",
  "is_canonical": true,
  "source": "guidetopharma"
}
```

### interactions

```json
{
  "interaction_id": 1,
  "entity_a_id": 1,
  "entity_b_id": 2,
  "direction": null,
  "sign": null,
  "mechanism_term": null,
  "statement_term": null,
  "record_attributes": [
    {
      "term": "OM:0625:Endogenous",
      "value": "false",
      "unit": null
    },
    {
      "term": "OM:0628:Affinity Median",
      "value": "6.46999979019165",
      "unit": "OM:0704"
    }
  ],
  "entity_a_attributes": null,
  "entity_b_attributes": null,
  "evidence": [
    {
      "term": "MI:0446:Pubmed",
      "value": "24393039",
      "unit": null
    }
  ],
  "source": "guidetopharma"
}
```

### associations

_empty_

### annotations

```json
{
  "subject_type": "interaction",
  "subject_id": 73,
  "cv_term": "OM:0902:Full Agonist",
  "source": "guidetopharma"
}
```

## hmdb

### entities

```json
{
  "entity_id": 1,
  "entity_type": "MI:0328:Small Molecule",
  "display_name": "1-Methylhistidine",
  "canonical_identifier": "332-80-9",
  "canonical_identifier_type": "MI:MI",
  "taxonomy_id": null,
  "source": "hmdb"
}
```

### entity_identifiers

```json
{
  "entity_id": 1,
  "identifier": "CN1C=NC(C[C@H](N)C(O)=O)=C1",
  "identifier_type": "MI:0239:Smiles",
  "is_canonical": false,
  "source": "hmdb"
}
```

### interactions

_empty_

### associations

_empty_

### annotations

_empty_

## hpo

### entities

```json
{
  "entity_id": 1,
  "entity_type": "MI:0326:Protein",
  "display_name": "NAT2",
  "canonical_identifier": "10",
  "canonical_identifier_type": "MI:MI",
  "taxonomy_id": "9606",
  "source": "hpo"
}
```

### entity_identifiers

```json
{
  "entity_id": 1,
  "identifier": "10",
  "identifier_type": "MI:0477:Entrez",
  "is_canonical": true,
  "source": "hpo"
}
```

### interactions

_empty_

### associations

_empty_

### annotations

```json
{
  "subject_type": "entity",
  "subject_id": 22462,
  "cv_term": "HP:0001319:HP:0001319",
  "source": "hpo"
}
```

## intact

### entities

```json
{
  "entity_id": 1,
  "entity_type": "MI:0326:Protein",
  "display_name": null,
  "canonical_identifier": "A8KA44",
  "canonical_identifier_type": "MI:MI",
  "taxonomy_id": "9606",
  "source": "intact"
}
```

### entity_identifiers

```json
{
  "entity_id": 1,
  "identifier": "ENSP00000346300.3",
  "identifier_type": "MI:0476:Ensembl",
  "is_canonical": false,
  "source": "intact"
}
```

### interactions

```json
{
  "interaction_id": 1,
  "entity_a_id": 1,
  "entity_b_id": 2,
  "direction": null,
  "sign": null,
  "mechanism_term": null,
  "statement_term": null,
  "record_attributes": [
    {
      "term": "OM:1201:Confidence Value",
      "value": "intact-miscore:0.62",
      "unit": null
    },
    {
      "term": "OM:1207:Interaction Annotation",
      "value": "comment:homomint",
      "unit": null
    },
    {
      "term": "OM:1207:Interaction Annotation",
      "value": "comment:mint",
      "unit": null
    },
    {
      "term": "OM:1207:Interaction Annotation",
      "value": "partial coverage:partial coverage",
      "unit": null
    },
    {
      "term": "OM:1208:Interaction Checksum",
      "value": "intact-crc:3E8C78E5182277F9",
      "unit": null
    },
    {
      "term": "OM:1208:Interaction Checksum",
      "value": "rigid:rbINobWATHyE+GffskCPt+EsR0M",
      "unit": null
    }
  ],
  "entity_a_attributes": [
    {
      "term": "OM:1221:Alias",
      "value": "psi-mi:crkl_human(display_long)",
      "unit": null
    },
    {
      "term": "OM:1221:Alias",
      "value": "uniprotkb:CRKL(gene name)",
      "unit": null
    },
    {
      "term": "OM:1221:Alias",
      "value": "psi-mi:CRKL(display_short)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "dip:DIP-29165N",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "efo:\"Orphanet:261330",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "panther:PTHR19969(orthology-group)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "ensembl:ENSG00000099942.13(gene)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "ensembl:ENST00000354336.8(transcript)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "ensembl:ENST00000411769.1(transcript)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0001558\"(regulation of cell growth)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0001568\"(blood vessel development)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0001655\"(urogenital system development)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0001764\"(neuron migration)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0001783\"(B cell apoptotic process)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0001784\"(phosphotyrosine residue binding)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0003151\"(outflow tract morphogenesis)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0003723\"(RNA binding)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0005654\"(nucleoplasm)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0005737\"(cytoplasm)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0005829\"(cytosol)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0006629\"(lipid metabolic process)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0007167\"(enzyme-linked receptor protein signaling pathway)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0007254\"(JNK cascade)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0007265\"(Ras protein signal transduction)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0007283\"(spermatogenesis)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0007338\"(single fertilization)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0008284\"(positive regulation of cell population proliferation)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0008543\"(fibroblast growth factor receptor signaling pathway)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0008584\"(male gonad development)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0009952\"(anterior/posterior pattern specification)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0010629\"(negative regulation of gene expression)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0016358\"(dendrite development)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0016477\"(cell migration)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0021766\"(hippocampus development)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0021987\"(cerebral cortex development)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0030010\"(establishment of cell polarity)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0030971\"(receptor tyrosine kinase binding)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "rcsb pdb:2BZX",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0031594\"(neuromuscular junction)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0032991\"(protein-containing complex)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0033628\"(regulation of cell adhesion mediated by integrin)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0035022\"(positive regulation of Rac protein signal transduction)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0035556\"(intracellular signal transduction)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0035591\"(signaling adaptor activity)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0035685\"(helper T cell diapedesis)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0038026\"(reelin-mediated signaling pathway)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0042802\"(identical protein binding)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0045296\"(cadherin binding)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0048384\"(retinoic acid receptor signaling pathway)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0048538\"(thymus development)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0050773\"(regulation of dendrite development)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0050852\"(T cell receptor signaling pathway)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0060017\"(parathyroid gland development)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0060326\"(cell chemotaxis)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0060392\"(negative regulation of SMAD protein signal transduction)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0061629\"(RNA polymerase II-specific DNA-binding transcription factor binding)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0070374\"(positive regulation of ERK1 and ERK2 cascade)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0071466\"(cellular response to xenobiotic stimulus)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0071560\"(cellular response to transforming growth factor beta stimulus)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0086100\"(endothelin receptor signaling pathway)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0095500\"(acetylcholine receptor signaling pathway)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0098698\"(postsynaptic specialization assembly)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0098749\"(cerebellar neuron development)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0098761\"(cellular response to interleukin-7)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0098890\"(extrinsic component of postsynaptic membrane)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0160093\"(chordate pharynx development)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:1900026\"(positive regulation of substrate adhesion-dependent cell spreading)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:1903977\"(positive regulation of glial cell migration)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:1904395\"(positive regulation of skeletal muscle acetylcholine-gated channel clustering)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:1904888\"(cranial skeletal system development)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:2000404\"(regulation of T cell migration)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "interpro:IPR000980(SH2 motif)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "interpro:IPR001452(Src homology-3)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "interpro:IPR035457",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "interpro:IPR035458",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "interpro:IPR036028",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "interpro:IPR036860",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "interpro:IPR051184",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "mint:P46109",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "rcsb pdb:2BZY",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "rcsb pdb:2DBK",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "rcsb pdb:2EO3",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "rcsb pdb:2LQN",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "rcsb pdb:2LQW",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "reactome:R-HSA-170968",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "reactome:R-HSA-186763",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "reactome:R-HSA-8875555",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "reactome:R-HSA-8875656",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "reactome:R-HSA-9027284",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "reactome:R-HSA-912631",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "refseq:NP_005198.1",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1025685(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1030628(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1104578(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1140070(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1157952(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1157953(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1177900(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1209373(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1345414(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1574676(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1591228(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1592646(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1618801(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1762467(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1762468(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1762469(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1766256(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1820778(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1823851(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1823852(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1844909(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1844910(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1849359(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1849360(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1894853(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1899379(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1933897(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:2017814(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:2073519(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:2119716(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:2119717(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:219092(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:2261449(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:573444(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:585862(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:589016(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:596532(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:673704(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:702459(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:704287(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:704288(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:711227(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:719587(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:742815(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:765991(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:878213(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:878416(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:907866(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:938051(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:941986(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:949122(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:996300(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "mint:MINT-8028116(identity)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "uniprotkb:P46109(original identifier)",
      "unit": null
    },
    {
      "term": "OM:1224:Participant Checksum",
      "value": "rogid:1YwlbUPPuijisz7AJM4Qkp7yJBI9606",
      "unit": null
    }
  ],
  "entity_b_attributes": [
    {
      "term": "OM:1221:Alias",
      "value": "psi-mi:cblb_human(display_long)",
      "unit": null
    },
    {
      "term": "OM:1221:Alias",
      "value": "uniprotkb:Signal transduction protein CBL-B(gene name synonym)",
      "unit": null
    },
    {
      "term": "OM:1221:Alias",
      "value": "uniprotkb:SH3-binding protein CBL-B(gene name synonym)",
      "unit": null
    },
    {
      "term": "OM:1221:Alias",
      "value": "uniprotkb:Casitas B-lineage lymphoma proto-oncogene b(gene name synonym)",
      "unit": null
    },
    {
      "term": "OM:1221:Alias",
      "value": "uniprotkb:RING finger protein 56(gene name synonym)",
      "unit": null
    },
    {
      "term": "OM:1221:Alias",
      "value": "uniprotkb:CBLB(gene name)",
      "unit": null
    },
    {
      "term": "OM:1221:Alias",
      "value": "psi-mi:CBLB(display_short)",
      "unit": null
    },
    {
      "term": "OM:1221:Alias",
      "value": "uniprotkb:RNF56(gene name synonym)",
      "unit": null
    },
    {
      "term": "OM:1221:Alias",
      "value": "uniprotkb:Nbla00127(orf name)",
      "unit": null
    },
    {
      "term": "OM:1221:Alias",
      "value": "uniprotkb:RING-type E3 ubiquitin transferase CBL-B(gene name synonym)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "dip:DIP-33091N",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "panther:PTHR23007(orthology-group)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "ensembl:ENSG00000114423.23(gene)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "ensembl:ENST00000394030.8(transcript)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0001784\"(phosphotyrosine residue binding)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0002669\"(positive regulation of T cell anergy)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0002870\"(T cell anergy)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0005509\"(calcium ion binding)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0005654\"(nucleoplasm)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0005829\"(cytosol)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0005886\"(plasma membrane)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0006607\"(NLS-bearing protein import into nucleus)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0006955\"(immune response)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0007165\"(signal transduction)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0008270\"(zinc ion binding)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0016567\"(protein ubiquitination)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0030163\"(protein catabolic process)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0030971\"(receptor tyrosine kinase binding)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0031398\"(positive regulation of protein ubiquitination)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0035556\"(intracellular signal transduction)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0035739\"(CD4-positive, alpha-beta T cell proliferation)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0042059\"(negative regulation of epidermal growth factor receptor signaling pathway)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0045121\"(membrane raft)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0045732\"(positive regulation of protein catabolic process)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0050852\"(T cell receptor signaling pathway)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0050860\"(negative regulation of T cell receptor signaling pathway)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0061630\"(ubiquitin protein ligase activity)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0098794\"(postsynapse)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0098978\"(glutamatergic synapse)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0099149\"(regulation of postsynaptic neurotransmitter receptor internalization)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:0140252\"(regulation protein catabolic process at postsynapse)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:2000562\"(negative regulation of CD4-positive, alpha-beta T cell proliferation)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "go:\"GO:2000583\"(regulation of platelet-derived growth factor receptor-alpha signaling pathway)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "interpro:IPR001841(Zinc finger, RING-type)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "interpro:IPR003153(Adaptor protein Cbl, N-terminal helical)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "interpro:IPR011992(EF-Hand type)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "interpro:IPR013083(Zinc finger, RING/FYVE/PHD-type)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "interpro:IPR014741(Adaptor protein Cbl, EF hand-like)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "interpro:IPR014742(Adaptor protein Cbl, SH2-like)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "interpro:IPR015940(Ubiquitin-associated/translation elongation factor EF1B, N-terminal, eukaryote)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "interpro:IPR017907",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "interpro:IPR018957",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "interpro:IPR024159",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "interpro:IPR024162",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "interpro:IPR036537",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "interpro:IPR036860",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "interpro:IPR039520",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "mint:Q13191",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "rcsb pdb:2AK5",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "rcsb pdb:2BZ8",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "rcsb pdb:2DO6",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "rcsb pdb:2J6F",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "rcsb pdb:2JNH",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "rcsb pdb:2LDR",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "rcsb pdb:2OOA",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "rcsb pdb:2OOB",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "rcsb pdb:3PFV",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "rcsb pdb:3VGO",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "rcsb pdb:3ZNI",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "rcsb pdb:8GCY",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "rcsb pdb:8QNG",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "rcsb pdb:8QNH",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "rcsb pdb:8QNI",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "rcsb pdb:8QTG",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "rcsb pdb:8QTH",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "rcsb pdb:8QTJ",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "rcsb pdb:8QTK",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "rcsb pdb:8VW4",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "rcsb pdb:8VW5",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "rcsb pdb:9FQH",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "rcsb pdb:9FQI",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "rcsb pdb:9FQJ",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "reactome:R-HSA-983168",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "refseq:NP_001308717.1",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "refseq:NP_733762.2",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "refseq:XP_011511559.1",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "refseq:XP_047305068.1",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "refseq:XP_054204201.1",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "refseq:XP_054204202.1",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1004922(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1004923(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1011361(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1019010(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1020880(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1024306(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1027998(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1028778(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1040814(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1042005(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1042742(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1042781(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1045528(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1049283(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1051612(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1061090(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1084722(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1105739(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1140013(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1140014(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1141128(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1175367(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1349370(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1359493(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1618580(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1728695(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1733102(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:175933(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:176565(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1854458(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1917206(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1934677(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:194083(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1963979(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:1978706(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:2007667(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:2014857(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:2057110(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:2064424(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:2067729(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:2244470(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:2244688(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:239297(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:425286(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:454928(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:542913(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:563288(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:570274(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:616682(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:631323(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:633763(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:637445(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:645326(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:649049(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:651244(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:652599(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:691763(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:702460(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:702911(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:729596(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:743652(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:743654(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:744322(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:904929(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:927319(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:931388(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "iedb:991857(see-also)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "mint:MINT-8028119(identity)",
      "unit": null
    },
    {
      "term": "OM:1222:Participant Xref",
      "value": "uniprotkb:Q13191(original identifier)",
      "unit": null
    },
    {
      "term": "OM:1224:Participant Checksum",
      "value": "rogid:cJ5eLH0TMyM6OtmS1sM3rhsrsIk9606",
      "unit": null
    },
    {
      "term": "OM:1225:Participant Feature",
      "value": "binding-associated region:702-715(MINT-8028122)",
      "unit": null
    },
    {
      "term": "OM:1225:Participant Feature",
      "value": "phosphorylated residue:709-709(MINT-8028124)",
      "unit": null
    }
  ],
  "evidence": [
    {
      "term": "MI:0446:Pubmed",
      "value": "10022120",
      "unit": null
    }
  ],
  "source": "intact"
}
```

### associations

_empty_

### annotations

```json
{
  "subject_type": "interaction",
  "subject_id": 83,
  "cv_term": "MI:0915:Physical Association",
  "source": "intact"
}
```

## lipidmaps

### entities

```json
{
  "entity_id": 1,
  "entity_type": "OM:0011:Lipid",
  "display_name": "C40H66O5",
  "canonical_identifier": "178363",
  "canonical_identifier_type": "MI:MI",
  "taxonomy_id": null,
  "source": "lipidmaps"
}
```

### entity_identifiers

```json
{
  "entity_id": 1,
  "identifier": "C(C(OC)CCC#CCCCCCC(C)CCCCC=C)(=O)OC(C(OC)CCC#CCCCCCC(C)CCCCC=C)=O",
  "identifier_type": "MI:0239:Smiles",
  "is_canonical": false,
  "source": "lipidmaps"
}
```

### interactions

_empty_

### associations

_empty_

### annotations

_empty_

## mebocost

### entities

```json
{
  "entity_id": 1,
  "entity_type": "MI:0328:Small Molecule",
  "display_name": "25-Hydroxycholesterol",
  "canonical_identifier": "25-Hydroxycholesterol",
  "canonical_identifier_type": "OM:OM",
  "taxonomy_id": null,
  "source": "mebocost"
}
```

### entity_identifiers

```json
{
  "entity_id": 1,
  "identifier": "HMDB0006247",
  "identifier_type": "OM:0004:Hmdb",
  "is_canonical": false,
  "source": "mebocost"
}
```

### interactions

```json
{
  "interaction_id": 1,
  "entity_a_id": 1,
  "entity_b_id": 2,
  "direction": null,
  "sign": null,
  "mechanism_term": null,
  "statement_term": null,
  "record_attributes": null,
  "entity_a_attributes": null,
  "entity_b_attributes": null,
  "evidence": [
    {
      "term": "MI:0446:Pubmed",
      "value": "16611739",
      "unit": null
    }
  ],
  "source": "mebocost"
}
```

### associations

_empty_

### annotations

_empty_

## neuronchat

### entities

```json
{
  "entity_id": 1,
  "entity_type": "MI:0314:Complex",
  "display_name": "VIP_VIPR1_source",
  "canonical_identifier": "VIP_VIPR1_source",
  "canonical_identifier_type": "OM:OM",
  "taxonomy_id": null,
  "source": "neuronchat"
}
```

### entity_identifiers

```json
{
  "entity_id": 1,
  "identifier": "VIP_VIPR1_source",
  "identifier_type": "OM:0202:Name",
  "is_canonical": true,
  "source": "neuronchat"
}
```

### interactions

```json
{
  "interaction_id": 1,
  "entity_a_id": 1,
  "entity_b_id": 2,
  "direction": 1,
  "sign": null,
  "mechanism_term": null,
  "statement_term": null,
  "record_attributes": [
    {
      "term": "OM:1207:Interaction Annotation",
      "value": "Neuropeptide",
      "unit": null
    },
    {
      "term": "OM:1207:Interaction Annotation",
      "value": "ligand-receptor",
      "unit": null
    }
  ],
  "entity_a_attributes": null,
  "entity_b_attributes": null,
  "evidence": null,
  "source": "neuronchat"
}
```

### associations

_empty_

### annotations

_empty_

## omnipath_ontology

### entities

_empty_

### entity_identifiers

_empty_

### interactions

_empty_

### associations

_empty_

### annotations

_empty_

## phenol_explorer

### entities

```json
{
  "entity_id": 1,
  "entity_type": "OM:0020:Food",
  "display_name": "Beer [Alcohol free]",
  "canonical_identifier": "531",
  "canonical_identifier_type": "OM:OM",
  "taxonomy_id": null,
  "source": "phenol_explorer"
}
```

### entity_identifiers

```json
{
  "entity_id": 1,
  "identifier": "531",
  "identifier_type": "OM:0028:Phenol Explorer",
  "is_canonical": true,
  "source": "phenol_explorer"
}
```

### interactions

_empty_

### associations

```json
{
  "association_id": 1,
  "parent_entity_id": 1,
  "member_entity_id": 2,
  "role_term_id": null,
  "stoichiometry": null,
  "record_attributes": null,
  "parent_attributes": [
    {
      "term": "OM:0660:Food Class",
      "value": "Alcoholic beverages",
      "unit": null
    },
    {
      "term": "OM:0661:Food Subclass",
      "value": "Beers",
      "unit": null
    }
  ],
  "member_attributes": [
    {
      "term": "OM:0662:Compound Class",
      "value": "Flavonoids",
      "unit": null
    },
    {
      "term": "OM:0663:Compound Subclass",
      "value": "Chalcones",
      "unit": null
    },
    {
      "term": "OM:0602:Mass Dalton",
      "value": "354.396",
      "unit": null
    },
    {
      "term": "OM:0666:Aglycone",
      "value": "Xanthohumol",
      "unit": null
    },
    {
      "term": "OM:0680:Concentration Mean",
      "value": "0.0003",
      "unit": null
    },
    {
      "term": "OM:0681:Concentration Min",
      "value": "0.0003",
      "unit": null
    },
    {
      "term": "OM:0682:Concentration Max",
      "value": "0.0003",
      "unit": null
    },
    {
      "term": "OM:0684:Concentration Unit",
      "value": "mg/100 ml",
      "unit": null
    },
    {
      "term": "OM:0685:Sample Count",
      "value": "1",
      "unit": null
    },
    {
      "term": "OM:0686:Data Point Count",
      "value": "1",
      "unit": null
    },
    {
      "term": "OM:0687:Experimental Method",
      "value": "Chromatography",
      "unit": null
    }
  ],
  "evidence": null,
  "source": "phenol_explorer"
}
```

### annotations

_empty_

## ptfi

### entities

```json
{
  "entity_id": 1,
  "entity_type": "OM:0020:Food",
  "display_name": "almond milk",
  "canonical_identifier": "FOODON_00001587",
  "canonical_identifier_type": "OM:OM",
  "taxonomy_id": null,
  "source": "ptfi"
}
```

### entity_identifiers

```json
{
  "entity_id": 1,
  "identifier": "almond milk",
  "identifier_type": "OM:0202:Name",
  "is_canonical": false,
  "source": "ptfi"
}
```

### interactions

_empty_

### associations

```json
{
  "association_id": 1,
  "parent_entity_id": 1,
  "member_entity_id": 2,
  "role_term_id": null,
  "stoichiometry": null,
  "record_attributes": null,
  "parent_attributes": [
    {
      "term": "OM:0685:Sample Count",
      "value": "6",
      "unit": null
    }
  ],
  "member_attributes": [
    {
      "term": "OM:0662:Compound Class",
      "value": "Uncategorized",
      "unit": null
    },
    {
      "term": "OM:0679:Concentration Value",
      "value": "104845.000759692",
      "unit": null
    },
    {
      "term": "OM:0680:Concentration Mean",
      "value": "104845.000759692",
      "unit": null
    },
    {
      "term": "OM:0688:Concentration Median",
      "value": "0",
      "unit": null
    },
    {
      "term": "OM:0681:Concentration Min",
      "value": "0",
      "unit": null
    },
    {
      "term": "OM:0682:Concentration Max",
      "value": "607035.828365455",
      "unit": null
    },
    {
      "term": "OM:0684:Concentration Unit",
      "value": "normIntensity",
      "unit": null
    },
    {
      "term": "OM:0685:Sample Count",
      "value": "6",
      "unit": null
    }
  ],
  "evidence": null,
  "source": "ptfi"
}
```

### annotations

_empty_

## reactome

### entities

```json
{
  "entity_id": 1,
  "entity_type": "MI:0314:Complex",
  "display_name": "TWIST1:CDH1 gene, (SETD8:TWIST1:CDH1 gene)",
  "canonical_identifier": "R-HSA-9931111",
  "canonical_identifier_type": "OM:OM",
  "taxonomy_id": null,
  "source": "reactome"
}
```

### entity_identifiers

```json
{
  "entity_id": 1,
  "identifier": "R-HSA-9931111",
  "identifier_type": "OM:0130:Reactome Stable Id",
  "is_canonical": true,
  "source": "reactome"
}
```

### interactions

```json
{
  "interaction_id": 1,
  "entity_a_id": 5788,
  "entity_b_id": 5789,
  "direction": 1,
  "sign": 1,
  "mechanism_term": null,
  "statement_term": "ACTIVATION",
  "record_attributes": [
    {
      "term": "OM:1212:Control Type",
      "value": "ACTIVATION",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-9764790",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-9764560",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-9764265",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-9764274",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-9759476",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-418990",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-421270",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-446728",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-1500931",
      "unit": null
    }
  ],
  "entity_a_attributes": [
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-9764790",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-9764560",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-9764265",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-9764274",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-9759476",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-418990",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-421270",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-446728",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-1500931",
      "unit": null
    }
  ],
  "entity_b_attributes": [
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-9764790",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-9764560",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-9764265",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-9764274",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-9759476",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-418990",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-421270",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-446728",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-1500931",
      "unit": null
    }
  ],
  "evidence": null,
  "source": "reactome"
}
```

### associations

```json
{
  "association_id": 1,
  "parent_entity_id": 1,
  "member_entity_id": 2,
  "role_term_id": null,
  "stoichiometry": null,
  "record_attributes": null,
  "parent_attributes": [
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-9764725",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-9764560",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-9764265",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-9764274",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-9759476",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-418990",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-421270",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-446728",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-1500931",
      "unit": null
    }
  ],
  "member_attributes": [
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-9764725",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-9764560",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-9764265",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-9764274",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-9759476",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-418990",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-421270",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-446728",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "R-HSA-1500931",
      "unit": null
    }
  ],
  "evidence": null,
  "source": "reactome"
}
```

### annotations

_empty_

## signor

### entities

```json
{
  "entity_id": 1,
  "entity_type": "MI:0314:Complex",
  "display_name": "NFY",
  "canonical_identifier": "NFY",
  "canonical_identifier_type": "OM:OM",
  "taxonomy_id": "9606",
  "source": "signor"
}
```

### entity_identifiers

```json
{
  "entity_id": 1,
  "identifier": "SIGNOR-C1",
  "identifier_type": "OM:0007:Signor",
  "is_canonical": false,
  "source": "signor"
}
```

### interactions

```json
{
  "interaction_id": 1,
  "entity_a_id": 2853,
  "entity_b_id": 2854,
  "direction": null,
  "sign": -1,
  "mechanism_term": null,
  "statement_term": "MI:0217:Phosphorylation Reaction",
  "record_attributes": [
    {
      "term": "comment",
      "value": "We demonstrate that phosphorylation of ser4 and/or thr2/thr3 abrogates the interaction of baf with dna and reduces its interaction with the lem domain. Coexpression of vrk1 and gfp-baf greatly diminishes the association of baf with the nuclear chromatin/matrix and leads to its dispersal throughout the cell",
      "unit": null
    }
  ],
  "entity_a_attributes": null,
  "entity_b_attributes": [
    {
      "term": "phosphorylated residue",
      "value": "4-4",
      "unit": null
    }
  ],
  "evidence": [
    {
      "term": "MI:0446:Pubmed",
      "value": "16495336",
      "unit": null
    }
  ],
  "source": "signor"
}
```

### associations

```json
{
  "association_id": 1,
  "parent_entity_id": 1,
  "member_entity_id": 2,
  "role_term_id": null,
  "stoichiometry": null,
  "record_attributes": null,
  "parent_attributes": null,
  "member_attributes": null,
  "evidence": null,
  "source": "signor"
}
```

### annotations

```json
{
  "subject_type": "interaction",
  "subject_id": 30071,
  "cv_term": "MI:0364:Inferred By Curator",
  "source": "signor"
}
```

## swisslipids

### entities

```json
{
  "entity_id": 1,
  "entity_type": "OM:0011:Lipid",
  "display_name": "Ceramide (iso-d17:1(4E))",
  "canonical_identifier": "CC(C)CCCCCCCCC\\C=C\\[C@@H](O)[C@H](CO)NC([*])=O",
  "canonical_identifier_type": "MI:MI",
  "taxonomy_id": null,
  "source": "swisslipids"
}
```

### entity_identifiers

```json
{
  "entity_id": 1,
  "identifier": "CC(C)CCCCCCCCC\\C=C\\[C@@H](O)[C@H](CO)NC([*])=O",
  "identifier_type": "MI:0239:Smiles",
  "is_canonical": true,
  "source": "swisslipids"
}
```

### interactions

_empty_

### associations

_empty_

### annotations

_empty_

## uniprot

### entities

```json
{
  "entity_id": 1,
  "entity_type": "MI:0326:Protein",
  "display_name": "Autism susceptibility gene 2 protein homolog",
  "canonical_identifier": "319974",
  "canonical_identifier_type": "MI:MI",
  "taxonomy_id": "10090",
  "source": "uniprot"
}
```

### entity_identifiers

```json
{
  "entity_id": 1,
  "identifier": "mmu:319974",
  "identifier_type": "MI:0470:Kegg",
  "is_canonical": false,
  "source": "uniprot"
}
```

### interactions

_empty_

### associations

_empty_

### annotations

```json
{
  "subject_type": "entity",
  "subject_id": 15353,
  "cv_term": "GO:0051928:GO:0051928",
  "source": "uniprot"
}
```

## wikipathways

### entities

```json
{
  "entity_id": 1,
  "entity_type": "MI:0326:Protein",
  "display_name": "Scarb1",
  "canonical_identifier": "20778",
  "canonical_identifier_type": "MI:MI",
  "taxonomy_id": "10090",
  "source": "wikipathways"
}
```

### entity_identifiers

```json
{
  "entity_id": 1,
  "identifier": "ENSMUSG00000037936",
  "identifier_type": "MI:0476:Ensembl",
  "is_canonical": false,
  "source": "wikipathways"
}
```

### interactions

```json
{
  "interaction_id": 1,
  "entity_a_id": 1,
  "entity_b_id": 2,
  "direction": 1,
  "sign": null,
  "mechanism_term": null,
  "statement_term": null,
  "record_attributes": [
    {
      "term": "OM:1207:Interaction Annotation",
      "value": "DirectedInteraction",
      "unit": null
    },
    {
      "term": "OM:1207:Interaction Annotation",
      "value": "Interaction",
      "unit": null
    },
    {
      "term": "OM:0222:Wikipathways",
      "value": "WP1",
      "unit": null
    },
    {
      "term": "OM:0223:Wikipathways Version",
      "value": "WP1_r137182",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "WP1",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "PW:0001933",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "CL:0000182",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "DOID:1287",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "Curation:AnalysisCollection",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "PW:0000724",
      "unit": null
    }
  ],
  "entity_a_attributes": [
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "WP1",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "PW:0001933",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "CL:0000182",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "DOID:1287",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "Curation:AnalysisCollection",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "PW:0000724",
      "unit": null
    }
  ],
  "entity_b_attributes": [
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "WP1",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "PW:0001933",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "CL:0000182",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "DOID:1287",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "Curation:AnalysisCollection",
      "unit": null
    },
    {
      "term": "OM:0204:Cv Term Accession",
      "value": "PW:0000724",
      "unit": null
    }
  ],
  "evidence": null,
  "source": "wikipathways"
}
```

### associations

_empty_

### annotations

```json
{
  "subject_type": "interaction",
  "subject_id": 21207,
  "cv_term": "DOID:557:DOID:557",
  "source": "wikipathways"
}
```

