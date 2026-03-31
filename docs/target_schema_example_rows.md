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
  "entity_attributes": null,
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
  "entity_attributes": [
    {
      "term": "MI:0612:Comment",
      "value": "CellPhoneDBcore<=4.1",
      "unit": null
    }
  ],
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
  "parent_attributes": null,
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
  "entity_attributes": [
    {
      "term": "OM:0602:Mass Dalton",
      "value": "594.708",
      "unit": null
    },
    {
      "term": "OM:0631:Mw Monoisotopic",
      "value": "594.27299",
      "unit": null
    },
    {
      "term": "OM:0623:Molecular Charge",
      "value": "0",
      "unit": null
    }
  ],
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
  "subject_id": 146519,
  "cv_term": "CHEBI:38976",
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
  "canonical_identifier_type": "OM:0027:Corum",
  "entity_attributes": [
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
    }
  ],
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
  "parent_attributes": null,
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
  "subject_id": 4035,
  "cv_term": "GO:0006265",
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
  "canonical_identifier": "Angelica",
  "canonical_identifier_type": "OM:0202:Name",
  "entity_attributes": [
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
  "is_canonical": true,
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
  "parent_attributes": null,
  "member_attributes": [
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
  "canonical_identifier_type": "OM:0008:Guidetopharma",
  "entity_attributes": null,
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
  "subject_type": "entity",
  "subject_id": 304,
  "cv_term": "OM:0040:Gpcr",
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
  "canonical_identifier": "InChI=1S/C7H11N3O2/c1-10-3-5(9-4-10)2-6(8)7(11)12/h3-4,6H,2,8H2,1H3,(H,11,12)/t6-/m0/s1",
  "canonical_identifier_type": "MI:2010:Standard Inchi",
  "entity_attributes": [
    {
      "term": "OM:0613:Description",
      "value": "1-Methylhistidine, also known as 1-MHis or 1MH, belongs to the class of organic compounds known as histidine and derivatives. 1MH is also classified as a methylamino acid. Methylamino acids are primarily proteogenic amino acids (found in proteins) which have been methylated (in situ) on their side chains by various methyltransferase enzymes. Histidine can be methylated at either the N1 or N3 position of its imidazole ring, yielding the isomers 1-methylhistidine (1MH; also referred to as pi-methylhistidine) or 3-methylhistidine (3MH; tau-methylhistidine), respectively. There is considerable confusion with regard to the nomenclature of the methylated nitrogen atoms on the imidazole ring of histidine and other histidine-containing peptides such as anserine. In particular, older literature (mostly prior to the year 2000) designated anserine (Npi methylated) as beta-alanyl-N1-methyl-histidine, whereas according to standard IUPAC nomenclature, anserine is correctly named as beta-alanyl-N3-methyl-histidine. As a result, many papers published prior to the year 2000 incorrectly identified 1MH as a specific marker for dietary consumption or various pathophysiological effects when they really were referring to 3MH (PMID: 24137022).  Recent discoveries have shown that 1MH is produced in essentially all mammals (and other vertebrates) via the enzyme known as METTL9 (PMID: 33563959). METTL9 is a broad-specificity methyltransferase that mediates the formation of the majority of 1MH present in mammalian proteomes. METTL9-catalyzed methylation requires a His-x-His (HxH) motif, where \"x\" is a small amino acid. This HxH motif is found in a number of abundant mammalian proteins such as ARMC6, S100A9, and NDUFB3 (PMID: 33563959). Because of its abundance in many muscle-related proteins, 1MH has been found to be a good biomarker for the consumption of meat (PMID: 21527577). Dietary studies have shown that poultry consumption (p-trend = 0.0006) and chicken consumption (p-trend = 0.0003) are associated with increased levels of 1MH in human plasma (PMID: 30018457). The consumption of fish, especially salmon and cod, has also been shown to increase the levels of 1MH in serum and urine (PMID: 31401679). As a general rule, urinary 1MH is associated with white meat intake (p< 0.001), whereas urinary 3MH is associated with red meat intake (p< 0.001) (PMID: 34091671).",
      "unit": null
    }
  ],
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
  "canonical_identifier_type": "MI:0477:Entrez",
  "entity_attributes": [
    {
      "term": "OM:0202:Name",
      "value": "Autosomal recessive inheritance",
      "unit": null
    }
  ],
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
  "subject_id": 2793,
  "cv_term": "HP:0003676",
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
  "canonical_identifier_type": "MI:1097:Uniprot",
  "entity_attributes": [
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
    }
  ],
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
    }
  ],
  "entity_a_attributes": null,
  "entity_b_attributes": [
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
  "subject_id": 167,
  "cv_term": "MI:1332",
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
  "canonical_identifier": "InChI=1S/C40H66O5/c1-7-9-11-23-29-35(3)31-25-19-15-13-17-21-27-33-37(43-5)39(41)45-40(42)38(44-6)34-28-22-18-14-16-20-26-32-36(4)30-24-12-10-8-2/h7-8,35-38H,1-2,9-16,19-20,23-34H2,3-6H3",
  "canonical_identifier_type": "MI:2010:Standard Inchi",
  "entity_attributes": [
    {
      "term": "OM:0602:Mass Dalton",
      "value": "626.491026",
      "unit": null
    },
    {
      "term": "OM:0614:Lipid Category",
      "value": "Fatty Acyls [FA]",
      "unit": null
    },
    {
      "term": "OM:0615:Lipid Main Class",
      "value": "Other Fatty Acyls [FA00]",
      "unit": null
    }
  ],
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
  "canonical_identifier": "HMDB0006247",
  "canonical_identifier_type": "OM:0004:Hmdb",
  "entity_attributes": null,
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
  "is_canonical": true,
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
  "canonical_identifier_type": "OM:0202:Name",
  "entity_attributes": null,
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
  "canonical_identifier_type": "OM:0028:Phenol Explorer",
  "entity_attributes": [
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
  "parent_attributes": null,
  "member_attributes": [
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
  "canonical_identifier": "almond milk",
  "canonical_identifier_type": "OM:0202:Name",
  "entity_attributes": [
    {
      "term": "OM:0685:Sample Count",
      "value": "6",
      "unit": null
    }
  ],
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
  "is_canonical": true,
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
  "parent_attributes": null,
  "member_attributes": [
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
  "canonical_identifier_type": "OM:0130:Reactome Stable Id",
  "entity_attributes": null,
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
    }
  ],
  "entity_a_attributes": null,
  "entity_b_attributes": null,
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
  "parent_attributes": null,
  "member_attributes": null,
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
  "canonical_identifier": "SIGNOR-C1",
  "canonical_identifier_type": "OM:0007:Signor",
  "entity_attributes": null,
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
  "is_canonical": true,
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
  "subject_id": 9214,
  "cv_term": "MI:0203",
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
  "canonical_identifier_type": "MI:0239:Smiles",
  "entity_attributes": [
    {
      "term": "OM:0619:Lipid Hierarchy Level",
      "value": "Class",
      "unit": null
    },
    {
      "term": "OM:0615:Lipid Main Class",
      "value": "SLM:000399814",
      "unit": null
    },
    {
      "term": "OM:0623:Molecular Charge",
      "value": "0",
      "unit": null
    }
  ],
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
  "canonical_identifier": "A0A087WPF7",
  "canonical_identifier_type": "MI:1097:Uniprot",
  "entity_attributes": [
    {
      "term": "OM:0601:Sequence Length",
      "value": "1261",
      "unit": null
    },
    {
      "term": "OM:0602:Mass Dalton",
      "value": "138920",
      "unit": null
    },
    {
      "term": "OM:0603:Function",
      "value": "FUNCTION: Component of a Polycomb group (PcG) multiprotein PRC1-like complex, a complex class required to maintain the transcriptionally repressive state of many genes, including Hox genes, throughout development. PcG PRC1 complex acts via chromatin remodeling and modification of histones; it mediates monoubiquitination of histone H2A 'Lys-119', rendering chromatin heritably changed in its expressibility. The PRC1-like complex that contains PCGF5, RNF2, CSNK2B, RYBP and AUTS2 has decreased histone H2A ubiquitination activity, due to the phosphorylation of RNF2 by CSNK2B. As a consequence, the complex mediates transcriptional activation (By similarity). In the cytoplasm, plays a role in axon and dendrite elongation and in neuronal migration during embryonic brain development. Promotes reorganization of the actin cytoskeleton, lamellipodia formation and neurite elongation via its interaction with RAC guanine nucleotide exchange factors, which then leads to the activation of RAC1 (PubMed:25533347). {ECO:0000250|UniProtKB:Q8WXX7, ECO:0000269|PubMed:25533347}.",
      "unit": null
    },
    {
      "term": "OM:0604:Subcellular Location",
      "value": "SUBCELLULAR LOCATION: Nucleus {ECO:0000269|PubMed:19948250, ECO:0000269|PubMed:25519132, ECO:0000269|PubMed:25533347}. Cytoplasm, cytoskeleton {ECO:0000269|PubMed:25533347}. Cell projection, growth cone {ECO:0000269|PubMed:25533347}. Note=Detected both in cytoplasm and nucleus (PubMed:25533347). Colocalizes with RAC1 at actin-rich growth cones (PubMed:25533347). Detected on the promoter region of actively transcribed genes (PubMed:25519132). {ECO:0000269|PubMed:25519132, ECO:0000269|PubMed:25533347}.",
      "unit": null
    },
    {
      "term": "OM:0610:Protein Family",
      "value": "AUTS2 family",
      "unit": null
    },
    {
      "term": "OM:0620:Amino Acid Sequence",
      "value": "MDGPTRGHGLRKKRRSRSQRDRERRSRAGLGTGAAGGIGAGRTRAPSLASSSGSDKEDNGKPPSSAPSRPRPPRRKRRESTSAEEDIIDGFAMTSFVTFEALEKDVAVKPQERAEKRQTPLTKKKREALTNGLSFHSKKSRLSHSHHYSSDRENDRNLCQHLGKRKKMPKGLRQLKPGQNSCRDSDSESASGESKGFQRSSSRERLSDSSAPSSLGTGYFCDSDSDQEEKASDASSEKLFNTVLVNKDPELGVGALPEHNQDAGPIVPKISGLERSQEKSQDCCKEPVFEPVVLKDPHPQLPQLPSQAQAEPQLQIPSPGPDLVPRTEAPPQFPPPSTQPAQGPPEAQLQPAPLPQVQQRPPRPQSPSHLLQQTLPPVQSHPSSQSLSQPLSAYNSSSLSLNSLSSRSSTPAKTQPAPPHISHHPSASPFPLSLPNHSPLHSFTPTLQPPAHSHHPNMFAPPTALPPPPPLTSGSLQVPGHPAGSTYSEQDILRQELNTRFLASQSADRGASLGPPPYLRTEFHQHQHQHQHTHQHTHQHTFTPFPHAIPPTAIMPTPAPPMFDKYPTKVDPFYRHSLFHSYPPAVSGIPPMIPPTGPFGSLQGAFQPKTSNPIDVAARPGTVPHTLLQKDPRLTDPFRPMLRKPGKWCAMHVHIAWQIYHHQQKVKKQMQSDPHKLDFGLKPEFLSRPPGPSLFGAIHHPHDLARPSTLFSAAGAAHPTGTPFGPPPHHSNFLNPAAHLEPFNRPSTFTGLAAVGGNAFGGLGNPSVTPNSVFGHKDSPSVQNFSNPHEPWNRLHRTPPSFPTPPPWLKPGELERSASAAAHDRDRDVDKRDSSVSKDDKERESVEKRHPSHPSPAPPVPVSALGHNRSSTDPTTRGHLNTEAREKDKPKEKERDHSGSRKDLTTEEHKAKESHLPERDGHSHEGRAAGEEPKQLSRVPSPYVRTPGVDSTRPNSTSSREAEPRKGEPAYENPKKNAEVKVKEERKEDHDLPTEAPQAHRTSEAPPPSSSASASVHPGPLASMPMTVGVTGIHAMNSIGSLDRTRMVTPFMGLSPIPGGERFPYPSFHWDPMRDPLRDPYRDLDMHRRDPLGRDFLLRNDPLHRLSTPRLYEADRSFRDREPHDYSHHHHHHHHPLAVDPRREHERGGHLDERERLHVLREDYEHPRLHPVHPASLDGHLPHPSLLTPGLPSMHYPRISPTAGHQNGLLNKTPPTAALSAPPPLISTLGGRPGSPRRTTPLSAEIRERPPSHTLKDIEAR",
      "unit": null
    }
  ],
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
  "subject_id": 41890,
  "cv_term": "GO:0006488",
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
  "canonical_identifier": "D3Z5U8",
  "canonical_identifier_type": "MI:1097:Uniprot",
  "entity_attributes": null,
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
    }
  ],
  "entity_a_attributes": null,
  "entity_b_attributes": null,
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
  "subject_id": 24648,
  "cv_term": "Curation:AnalysisCollection",
  "source": "wikipathways"
}
```

