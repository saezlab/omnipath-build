# SMILES and InChIKey Analysis Summary

## Overview

This document summarizes the analysis of distinct SMILES and InChIKey counts across metabolite databases (HMDB, LipidMaps, SwissLipids, and RAMP), explaining why these counts differ and what causes the discrepancies.

## Summary Statistics

| Database     | Total Records | Distinct SMILES | Distinct InChIKeys | Difference |
|--------------|---------------|-----------------|--------------------| ---------- |
| HMDB         | 217,920       | 217,488         | 217,488            | 0          |
| LipidMaps    | 47,582        | 47,176          | 47,075             | 101        |
| SwissLipids  | 779,249       | 594,258         | 593,207            | 1,051      |
| RAMP         | 283,382       | 279,981         | 263,327            | 16,654     |

## Key Findings by Database

### SwissLipids: Wildcard Templates Explain the Discrepancy

**Problem**: 779,249 total records but only ~594k distinct SMILES/InChIKeys

**Explanation**: SwissLipids contains extensive **generic lipid templates** using wildcard notation.

#### Details:

- **185,043 records (23.7%)** contain wildcard SMILES with `[*]` placeholders
- These represent **only 390 distinct wildcard patterns**
- **Average: ~475 records per wildcard pattern**

#### Why so many records with so few patterns?

Each wildcard SMILES is a **structural template** representing hundreds of related lipids:

**Example**: Pattern `[*]C(=O)OCC(COC([*])=O)OC([*])=O)` (Triacylglycerol backbone)
- Used in **38,508 different records**
- Each represents a different variant: TG(36:0), TG(37:0), TG(38:0), TG(38:1), etc.
- Different formulas (C39H74O6, C40H76O6, C41H78O6...)
- Different exact masses
- Same structural backbone with variable fatty acid chains

#### Distribution by Hierarchical Level:

| Level                    | Total Records | Missing InChIKeys | % Missing |
|--------------------------|---------------|-------------------|-----------|
| Isomeric subspecies      | 592,413       | 3                 | 0.0%      |
| Structural subspecies    | 111,867       | 111,867           | 100.0%    |
| Molecular subspecies     | 62,516        | 62,516            | 100.0%    |
| Species                  | 10,347        | 10,347            | 100.0%    |
| Class                    | 806           | 805               | 99.9%     |
| Category                 | 7             | 7                 | 100.0%    |

**Key Insight**: Only the **Isomeric subspecies** level has concrete, fully-defined structures with valid InChIKeys. Higher-level classifications use generic templates.

#### Examples of Wildcard Templates:

1. **Lysophosphatidate family** - Same pattern for different chain lengths:
   ```
   SMILES: [O-]P([O-])(=O)OCC(CO[*])O[*]
   InChIKey: InChIKey=none

   - LPA(10:0), Formula: C13H25O7P
   - LPA(12:0), Formula: C15H29O7P
   - LPA(14:0), Formula: C17H33O7P
   - LPA(16:0), Formula: C19H37O7P
   ... hundreds more variants
   ```

2. **Triacylglycerols** - 38,508 records sharing one pattern:
   ```
   SMILES: [*]C(=O)OCC(COC([*])=O)OC([*])=O)
   - TG(36:0), TG(37:0), TG(38:0), TG(38:1)...
   ```

**Conclusion**: SwissLipids' "duplicate" problem is intentional - it's a hierarchical database where generic templates define lipid families, and only the most specific level (Isomeric subspecies) contains complete structures.

---

### RAMP: Multiple SMILES per InChIKey

**Problem**: 279,981 distinct SMILES but only 263,327 distinct InChIKeys (difference: ~16,654)

**Explanation**: Multiple SMILES representations map to the same InChIKey due to different notations, protonation states, and cross-source inconsistencies.

#### Details:

- **14,455 InChIKeys (5.5%)** have multiple SMILES
- **15,945 extra SMILES** beyond 1:1 mapping
- Very few wildcards: only 651 records (0.2%) with 590 distinct patterns

#### Reasons for Multiple SMILES per InChIKey:

1. **Different protonation states / tautomers**:
   ```
   3-aminoisobutyric acid (InChIKey: QCHPKSFMDHPSNR-UHFFFAOYSA-N)
   - Neutral:    CC(CN)C(O)=O
   - Zwitterion: C(C(C)C[NH3+])([O-])=O
   ```

2. **Different SMILES notations for same structure**:
   ```
   (S)-dihydrolipoic acid (InChIKey: IZFHEQBZOYJLPK-ZETCQYMHSA-N)
   - C(CCCC(=O)O)[C@H](S)CCS
   - OC(=O)CCCC[C@H](S)CCS
   (Same structure, different atom ordering)
   ```

3. **Different stereochemistry notation**:
   ```
   Lipid double bonds represented as:
   - /C=C\  (cis notation)
   - \C=C/  (trans notation)
   Different styles but same compound
   ```

#### Source Analysis:

Records with multiple SMILES per InChIKey by source:

| Source     | Records | % of Source Total |
|------------|---------|-------------------|
| HMDB       | 11,576  | 5.3%              |
| LipidMaps  | 9,845   | 21.7%             |
| ChEBI      | 9,849   | 48.9%             |

**ChEBI has the highest proportion** (48.9%) - includes many protonation states and tautomers.

#### Cross-Source Conflicts:

The same InChIKey often appears in multiple sources with **different SMILES**:

**Example 1: Pelargonidin**
- InChIKey: XVFMGWDSJLBXDZ-UHFFFAOYSA-O
- Present in: HMDB, LipidMaps, ChEBI
- **3 different SMILES representations**

**Example 2: PS(18:0/14:0)** (Phosphatidylserine lipid)
- InChIKey: FMJXBRZFHKZLFQ-GPOMZPHUSA-N
- HMDB version: `[H][C@](N)(COP(O)(=O)OC[C@@]([H])(COC(=O)CCCCCCCCCCCCCCCCC)OC(=O)CCCCCCCCCCCCC)C(O)=O`
- LipidMaps version: `C(O)(=O)[C@@]([H])(N)COP(OC[C@]([H])(OC(CCCCCCCCCCCCC)=O)COC(CCCCCCCCCCCCCCCCC)=O)(=O)O`
- Same compound, different atom ordering and notation style

**Example 3: Missing SMILES**

Some records lack SMILES entirely, typically:
- Polymers: hyaluronic acid, chondroitin sulfate, poly(glycyl-L-arginine)
- Variable-length structures: dolichyl compounds, polyprenyl diphosphates
- Complex polysaccharides

**Conclusion**: RAMP's SMILES/InChIKey difference stems from aggregating data from multiple sources that use different SMILES conventions, plus ChEBI's inclusion of multiple chemical forms (neutral, zwitterion, tautomers) for the same InChIKey.

---

### HMDB: Clean Data

**Problem**: None - nearly perfect 1:1 mapping

**Statistics**:
- Total records: 217,920
- Distinct SMILES: 217,488
- Distinct InChIKeys: 217,488
- Difference: 0

**Conclusion**: HMDB has the cleanest data with consistent SMILES and InChIKey mapping.

---

### LipidMaps: Minimal Issues

**Problem**: Minor discrepancy (101 difference)

**Statistics**:
- Total records: 47,582
- Distinct SMILES: 47,176
- Distinct InChIKeys: 47,075
- Difference: 101

**Conclusion**: LipidMaps has very clean data with minimal stereoisomer or notation variations.

---

## Implications for Data Integration

### For Building a Unified Metabolite Database:

1. **SwissLipids**:
   - Consider **filtering to "Isomeric subspecies" level only** for concrete structures
   - Or maintain hierarchy but clearly distinguish between templates and concrete structures
   - Wildcard templates should be handled separately or excluded from structure-based searches

2. **RAMP**:
   - Need **SMILES canonicalization** to resolve different notations
   - Consider prioritizing certain sources (e.g., HMDB > LipidMaps > ChEBI) for SMILES
   - May need to pick one SMILES representation per InChIKey
   - ChEBI records need special handling due to protonation states

3. **General Strategy**:
   - Use **InChIKey as primary identifier** for structure deduplication
   - Store all SMILES variants but designate one as "canonical"
   - Flag records with wildcards separately
   - Document source and protonation state for each SMILES

### Counting Distinct Compounds:

**Recommendation**: Use **distinct InChIKeys** as the count of unique compounds, but:
- Exclude invalid InChIKeys ('InChIKey=none', 'none', '-', NULL)
- Exclude wildcard structures for chemical structure counts
- For SwissLipids, count only "Isomeric subspecies" level if you want concrete structures

**Adjusted Counts (excluding wildcards and invalid InChIKeys)**:

| Database     | Valid Distinct Structures |
|--------------|---------------------------|
| HMDB         | 217,488                   |
| LipidMaps    | 47,075                    |
| SwissLipids  | 593,204 (Isomeric only)   |
| RAMP         | 263,327                   |

---

## Scripts Used for Analysis

All analysis scripts are available in `/exploration/`:

1. `count_distinct_identifiers.py` - Count distinct SMILES and InChIKeys per source
2. `analyze_swisslipids_duplicates.py` - Investigate SwissLipids duplication
3. `analyze_missing_structures.py` - Check why structures are missing
4. `explain_wildcard_multiplicity.py` - Explain wildcard pattern usage
5. `analyze_ramp_structures.py` - Analyze RAMP structural data
6. `check_ramp_smiles_inchikey_mapping.py` - Check multiple SMILES per InChIKey
7. `check_ramp_sources.py` - Identify source-specific issues
8. `check_smiles_vs_inchikey.py` - Compare SMILES vs InChIKey availability

---

## Summary

The differences between distinct SMILES and InChIKey counts are due to:

1. **SwissLipids**: Extensive use of wildcard templates representing lipid families (~185k records with only 390 patterns)
2. **RAMP**: Multiple SMILES notations for the same compound (~16k extra SMILES) due to:
   - Cross-source inconsistencies
   - Different protonation states (especially from ChEBI)
   - Different SMILES notation styles
3. **HMDB & LipidMaps**: Clean data with minimal issues

For data integration, use InChIKey as the primary deduplication key, exclude wildcard structures, and implement SMILES canonicalization to handle notation variations.
