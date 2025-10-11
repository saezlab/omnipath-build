# RaMP Sources Column Analysis Report

**Date:** 2025-10-11
**Database:** RaMP OmniPathMetabo
**File analyzed:** `databases/omnipath/data/ramp/ramp_omnipathmetabo/bronze/latest.parquet`
**Total records:** 283,382
**Unique compounds (ramp_id):** 242,470

---

## Executive Summary

The RaMP database aggregates metabolite data from multiple sources (HMDB, ChEBI, LIPID MAPS). Each compound has a unique `ramp_id`, but may appear multiple times (once per original source database). The `sources` column contains cross-reference identifiers in CURIE format (`id_type:id`) that need to be extracted for enhanced entity mapping.

### Key Findings:

1. **11 different ID types** are present in the `sources` column
2. **Each external ID maps to exactly ONE RAMP ID** (1:1 mapping confirmed)
3. **24.5% of records** have duplicate IDs within their `sources` string
4. **99.2% of duplicate RAMP IDs** represent the same molecular skeleton (identical connectivity layer)

---

## 1. ID Types in Sources Column

The `sources` column contains comma-separated identifiers in CURIE format. Analysis of all 283,382 records revealed:

| ID Type | Total Occurrences | Distinct IDs | Description |
|---------|------------------|--------------|-------------|
| **hmdb** | 708,601 | 298,933 | Human Metabolome Database |
| **pubchem** | 316,008 | 143,359 | PubChem Compound IDs |
| **chemspider** | 165,620 | 32,224 | ChemSpider IDs |
| **chebi** | 160,122 | 31,955 | Chemical Entities of Biological Interest |
| **LIPIDMAPS** | 125,036 | 45,559 | LIPID MAPS Structure Database |
| **swisslipids** | 75,312 | 12,344 | SwissLipids Database |
| **CAS** | 31,317 | 15,672 | CAS Registry Numbers |
| **kegg** | 23,255 | 6,891 | KEGG Compound IDs |
| **wikidata** | 11,419 | 3,389 | Wikidata Entity IDs |
| **lipidbank** | 4,284 | 2,507 | LipidBank IDs |
| **plantfa** | 1,118 | 557 | PlantFA Database IDs |

### Example Format:
```
hmdb:HMDB0006277, pubchem:441294, chemspider:136695, chebi:28624, LIPIDMAPS:LMPR0104540003, CAS:50814-15-8
```

---

## 2. ID Duplication Patterns

### 2.1 Why Do IDs Appear Multiple Times?

Two distinct types of duplication exist:

#### A. Between Records (RaMP Aggregation)
- Same `ramp_id` appears 1-3 times with different `chem_data_source` (hmdb, chebi, lipidmaps)
- **40,912 duplicate records** (283,382 total records → 242,470 unique compounds)
- **33,511 RAMP IDs (13.8%)** have multiple records
- All records with same `ramp_id` have **identical** `sources` strings

**Example:** `RAMP_C_000000446` appears 3 times:
- Record 1: `chem_data_source: chebi`, `chem_source_id: chebi:18250`
- Record 2: `chem_data_source: hmdb`, `chem_source_id: hmdb:HMDB0000652`
- Record 3: `chem_data_source: hmdb`, `chem_source_id: hmdb:HMDB0000580`
- All 3 records share identical `sources` column with all cross-references combined

#### B. Within Sources String (Cross-Reference Redundancy)
- **24.5% of records** have duplicate IDs in their `sources` field
- Same external ID repeated 2-4 times in one `sources` string
- **Cause:** Each original database (HMDB, ChEBI, LIPID MAPS) provides overlapping cross-references

**Example:** `RAMP_C_000000446` sources string contains:
- `chebi:37397` appears **3 times**
- `hmdb:HMDB0000580` appears **2 times**
- `chebi:18250` appears **2 times**

### 2.2 Critical Finding: 1-to-1 Mapping

**Each external ID maps to exactly ONE RAMP ID** (after deduplication):
- **593,390** total distinct external IDs
- **593,390** unique (external_id, ramp_id) pairs
- **0** IDs mapping to multiple RAMP IDs

This means:
- No conflicts when extracting and deduplicating IDs
- Each external identifier uniquely identifies one compound in RaMP
- Safe to use any ID from `sources` as a compound identifier

---

## 3. Data Consistency Within RAMP IDs

For the same `ramp_id` across multiple records, we analyzed which fields are consistent:

### 3.1 Always Identical Fields
- ✓ **sources** (100% identical)
- ✓ **synonyms** (100% identical)
- ✓ **classes** (100% identical)

### 3.2 Frequently Different Fields

| Field | % RAMP IDs with Differences | Explanation |
|-------|----------------------------|-------------|
| **iso_smiles** | 94.4% | Different SMILES representations from different databases |
| **common_name** | 89.4% | Databases use different naming conventions |
| **inchi_key** | 62.1% | Often stereoisomers or protonation states |
| **monoisotop_mass** | 45.9% | Measurement/calculation differences |
| **mol_formula** | 6.5% | Usually due to protonation state differences |
| **mw** | 1.9% | Minor calculation differences |

### 3.3 Example: Different Data for Same RAMP ID

**RAMP_C_000223445** has 2 records:

| Source | Common Name | SMILES |
|--------|-------------|--------|
| LIPID MAPS | 13-OxoODE | `C(/C=C/C(=O)CCCCC)=C\CCCCCCCC(=O)O` |
| ChEBI | 13-oxo-9E,11E-ODE | `CCCCCC(=O)\C=C\C=C\CCCCCCCC(O)=O` |

Same InChIKey: `JHXAZBBVQSRKJR-KDFHGORWSA-N`
Different SMILES notation and names, but representing the same compound.

---

## 4. InChIKey Connectivity Analysis

InChIKey structure: `XXXXXXXXXXXXXX-YYYYYYYYYY-Z`
- First 14 chars: **Connectivity layer** (molecular skeleton)
- Next 10 chars: **Stereochemistry layer**
- Last char: **Protonation layer**

### Results for 33,483 RAMP IDs with Multiple Records:

| Category | Count | % | Interpretation |
|----------|-------|---|----------------|
| **Identical InChIKeys** | 12,677 | 37.9% | Exact same compound, different source |
| **Same connectivity, different stereo** | 20,537 | 61.3% | Stereoisomers/tautomers/protonation states |
| **Different connectivity** | 269 | 0.8% | Actually different molecules (potential errors) |
| **TOTAL matching first 14 chars** | **33,214** | **99.2%** | Same molecular skeleton |

### Interpretation:

**99.2% of duplicate RAMP IDs represent the same molecular skeleton**, with variations in:
- Stereochemistry (cis/trans, R/S configurations)
- Protonation states (acid vs. conjugate base)
- Positional isomers (especially in complex lipids)

**Only 0.8% are truly different molecules**, likely due to:
- Database mapping errors
- Complex chemical families where databases disagree

### Example: Same Connectivity, Different Stereochemistry

**RAMP_C_000076320** - Triglyceride variants:
- `hmdb`: `TG(22:0/13:0/13:0)` → InChIKey: `ODHFRQJJHKVVAR-QSCHNALKSA-N`
- `hmdb`: `TG(13:0/13:0/22:0)` → InChIKey: `ODHFRQJJHKVVAR-DYVQZXGMSA-N`
- `lipidmaps`: `TG(13:0/13:0/22:0)[iso3]` → InChIKey: `ODHFRQJJHKVVAR-QSCHNALKSA-N`

All share connectivity: `ODHFRQJJHKVVAR`, differing only in stereochemistry layer.

---

## 5. Data Source Breakdown

| chem_data_source | # Compounds | # Records | Avg Sources Length |
|------------------|-------------|-----------|-------------------|
| **hmdb** | 198,509 | 217,776 | 84.7 chars |
| **lipidmaps** | 44,692 | 45,460 | 161.9 chars |
| **chebi** | 17,672 | 20,146 | 154.9 chars |

- HMDB contributes the most compounds
- LIPID MAPS entries have longest `sources` strings (more cross-references)
- Total: 260,873 unique source database entries → 242,470 unique RAMP compounds

---

## 6. Recommendations for ID Extraction

Based on this analysis, when extracting IDs from the `sources` column:

### Implementation Strategy:

1. **Split by comma** to separate individual IDs
2. **Trim whitespace** from each ID
3. **Deduplicate** IDs within each compound (same ID can appear 2-4 times)
4. **Parse CURIE format**: Split on `:` to get `id_type` and `id_value`
5. **Extract to separate columns** for each ID type:
   - `hmdb_ids` (most abundant, 298,933 distinct)
   - `pubchem_ids` (143,359 distinct)
   - `chebi_ids` (31,955 distinct)
   - `chemspider_ids` (32,224 distinct)
   - `lipidmaps_ids` (45,559 distinct)
   - `swisslipids_ids` (12,344 distinct)
   - `cas_ids` (15,672 distinct)
   - `kegg_ids` (6,891 distinct)
   - `wikidata_ids` (3,389 distinct)
   - `lipidbank_ids` (2,507 distinct)
   - `plantfa_ids` (557 distinct)

### SQL Example:

```sql
WITH split_sources AS (
    SELECT
        ramp_id,
        TRIM(UNNEST(string_split(sources, ','))) AS id_value
    FROM ramp_data
),
deduped AS (
    SELECT DISTINCT
        ramp_id,
        id_value
    FROM split_sources
    WHERE id_value LIKE '%:%'
),
parsed AS (
    SELECT
        ramp_id,
        split_part(id_value, ':', 1) AS id_type,
        split_part(id_value, ':', 2) AS id
    FROM deduped
)
SELECT
    ramp_id,
    STRING_AGG(CASE WHEN id_type = 'hmdb' THEN id END, ',') AS hmdb_ids,
    STRING_AGG(CASE WHEN id_type = 'pubchem' THEN id END, ',') AS pubchem_ids,
    STRING_AGG(CASE WHEN id_type = 'chebi' THEN id END, ',') AS chebi_ids
    -- ... etc for other ID types
FROM parsed
GROUP BY ramp_id
```

### Handling Multiple Records per RAMP ID:

Since all records with same `ramp_id` have identical `sources` strings, you can either:
- **Option A:** Extract IDs once per `ramp_id` (deduplicate by `ramp_id` first)
- **Option B:** Extract from all records (results will be identical for same `ramp_id`)

**Recommended:** Option A (deduplicate first) for efficiency.

---

## 7. Quality Assurance Notes

### Strengths:
- ✓ Perfect 1:1 mapping between external IDs and RAMP compounds
- ✓ 99.2% of duplicate records represent same molecular skeleton
- ✓ Cross-references are comprehensive (up to 11 different ID types)
- ✓ `sources`, `synonyms`, and `classes` are perfectly consistent within RAMP IDs

### Limitations:
- ⚠ 24.5% of records have redundant IDs in `sources` (requires deduplication)
- ⚠ 0.8% of duplicate RAMP IDs have different connectivity layers (potential mapping errors)
- ⚠ SMILES and names vary significantly between sources (94.4% and 89.4% respectively)
- ⚠ InChIKeys differ in 62.1% of duplicate records (stereochemistry variations)

### Recommendations:
1. Always deduplicate IDs within the `sources` string
2. When choosing a primary structure (SMILES/InChIKey), prefer records with complete data
3. Consider connectivity layer (first 14 chars of InChIKey) for grouping stereoisomers
4. Investigate the 269 RAMP IDs with different connectivity layers for potential errors

---

## Appendix: Sample Data

### Example Record with Multiple IDs:

```
ramp_id: RAMP_C_000000446
common_name: chondroitin 4'-sulfate
sources: hmdb:HMDB0000580, hmdb:HMDB0000652, chebi:37397, kegg:C00607,
         CAS:50814-15-8, hmdb:HMDB0000591, hmdb:HMDB00580, hmdb:HMDB00591,
         chebi:18250, chemspider:58145265, kegg:C00634, pubchem:70678540,
         CAS:24967-93-9, hmdb:HMDB00652, chebi:37397, chebi:18250,
         chebi:37397, hmdb:HMDB0000580, chemspider:58145265,
         pubchem:70678540, hmdb:HMDB0000652, wikidata:Q408014
```

**After deduplication, this yields:**
- HMDB: 5 distinct IDs
- ChEBI: 2 distinct IDs
- KEGG: 2 distinct IDs
- CAS: 2 distinct IDs
- ChemSpider: 1 ID
- PubChem: 1 ID
- Wikidata: 1 ID

---

**Report generated by:** Claude Code
**Analysis scripts:** `/exploration/check_ramp_sources.py` (existing), custom SQL queries
