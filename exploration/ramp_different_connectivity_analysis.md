# RaMP Different Connectivity Layer Analysis

**Investigation of the 0.8% of RAMP IDs with different connectivity layers**

## Summary

Out of 269 RAMP IDs (0.8% of all duplicated IDs) where the same `ramp_id` maps to compounds with different connectivity layers (first 14 characters of InChIKey), we identified distinct patterns:

### Categories:

| Category | Count | % | Description |
|----------|-------|---|-------------|
| **Different molecular formulas** | 201 | 74.7% | Genuinely different compounds incorrectly grouped |
| **Same molecular formula** | 68 | 25.3% | Structural isomers, tautomers, or ring forms |

---

## Category 1: Different Molecular Formulas (Truly Different Compounds)

**74.7% of problematic cases** - These are genuinely different molecules that should NOT share the same `ramp_id`.

### Most Extreme Cases:

1. **RAMP_C_000005772**: 175 different connectivity layers
   - **Type**: Phosphoinositides (PIP, PIP2, PIP3)
   - **Formula variation**: C49H87O13P vs C49H83O13P vs C49H85O13P (and many more)
   - **Issue**: Entire family of structurally distinct phospholipids with different fatty acid chains
   - **Examples**:
     - PIP2(20:3/18:1)
     - PI(20:4/20:1)
     - PIP2(18:1/16:0)
     - PIP3(18:0/18:1)

2. **RAMP_C_000160815**: 140 different connectivity layers
   - **Type**: Phosphatidylcholines (PC)
   - **Formula variation**: C46H80NO7P vs C45H80NO8P vs C44H78NO8P
   - **Issue**: Different chain length combinations (PC(15:0/24:1), PC(22:0/20:3), etc.)

3. **RAMP_C_000012147**: 110 different connectivity layers
   - **Type**: Triglycerides (TG)
   - **Formula variation**: C55H96O6 vs C61H106O6 vs C53H98O6
   - **Issue**: Various fatty acid combinations
   - **Examples**:
     - TG(16:1/20:1/20:4)
     - TG(16:0/16:0/16:1)
     - TG(20:0/20:0/20:4)

4. **RAMP_C_000007587**: 19 different connectivity layers (Sphingomyelins)
   - **Formula variation**: C47H97N2O6P vs C45H87N2O7P vs C37H77N2O7P
   - **Issue**: Different sphingosine base and fatty acid combinations
   - **Examples**:
     - SM(d18:0/24:0) - C47H97N2O6P
     - SM(d18:0/18:1) - C41H83N2O6P
     - SM(d18:1/12:0) - C35H71N2O6P
     - SM(d18:0/14:1(OH)) - C37H73N2O7P

### Pattern Analysis:

These errors primarily occur with **complex lipids** where:
- Different databases disagree on the specific fatty acid composition
- The compound family is broad (e.g., "sphingomyelin" as a class vs specific SM species)
- Cross-reference aggregation grouped distinct species under one RAMP ID

**Affected compound classes:**
- Phosphoinositides (PIP, PIP2, PIP3)
- Phosphatidylcholines (PC)
- Phosphatidylethanolamines (PE)
- Triglycerides (TG)
- Sphingomyelins (SM)
- Gangliosides (GM1, GM3, GD3)
- Ceramides and glycoceramides

**Data sources involved:**
- HMDB: 253 problematic RAMP IDs
- ChEBI: 172 problematic RAMP IDs
- LIPID MAPS: 121 problematic RAMP IDs

---

## Category 2: Same Molecular Formula (Structural Isomers/Tautomers)

**25.3% of problematic cases** - These are chemically related forms of the same compound.

### Subcategories:

#### A. Positional Isomers (Different connectivity)

**Example: RAMP_C_000000001 - Methylhistidine**
- Same formula: **C7H11N3O2**
- **1-Methylhistidine** (N-tele-methyl-L-histidine)
  - InChIKey: `BRMWTNUJHUMWMS-LURJTMIESA-N`
  - Sources: HMDB, ChEBI
- **3-Methylhistidine** (N-pros-methyl-L-histidine)
  - InChIKey: `JDHILDINMRGULE-LURJTMIESA-N`
  - Sources: HMDB, ChEBI

**Interpretation**: These are TRUE positional isomers - methyl group at different positions on the imidazole ring. Should potentially have separate RAMP IDs.

---

#### B. Ring Forms / Tautomers

**Example: RAMP_C_000000077 - D-Glucose**
- Same formula: **C6H12O6**
- Three different forms:
  1. **beta-D-glucose** (pyranose ring, beta anomer)
     - InChIKey: `WQZGKKKJIJFFOK-VFUOTHLCSA-N`
  2. **D-glucopyranose** (pyranose ring, alpha anomer)
     - InChIKey: `WQZGKKKJIJFFOK-GASJEMHNSA-N`
  3. **aldehydo-D-glucose** (open-chain aldehyde form)
     - InChIKey: `GZCGUPFRVQAUEE-SLPGGIOYSA-N`

**Interpretation**: These are interconverting forms in aqueous solution. Different databases report different forms. Reasonable to group together.

---

**Example: RAMP_C_000000079 - Fructose 6-phosphate**
- Same formula: **C6H13O9P** (or C6H11O9P for dianion)
- Two ring forms:
  1. **keto-D-fructose 6-phosphate** (open-chain)
     - InChIKey: `GSXOAOHZAIYLCY-HSUXUTPPSA-N`
  2. **D-fructofuranose 6-phosphate** (furanose ring)
     - InChIKey: `BGWGXPAPYGQALX-VRPWFDPXSA-N`

**Interpretation**: Ring-chain tautomerism. Both forms exist in equilibrium.

---

#### C. Ionization/Oxidation States

**Example: RAMP_C_000000347 - Calcium**
- Same formula: **Ca**
- Different oxidation states:
  1. **calcium(0)** - neutral atom
     - InChIKey: `OYPRJOBELJOOCE-UHFFFAOYSA-N`
  2. **calcium(2+)** - dication
     - InChIKey: `BHPQYMZQTOCNFJ-UHFFFAOYSA-N`

**Interpretation**: Different chemical species. Ca²⁺ is the biologically relevant form.

---

#### D. Geometric Isomers

**Example: RAMP_C_000000462 - Decenedioic acid**
- Same formula: **C10H16O4**
- Different double bond positions:
  1. **4Z-Decenedioic acid** (cis at position 4)
     - InChIKey: `CXGDCGIPEJKSCK-IWQZZHSRSA-N`
  2. **2E-Decenedioic acid** (trans at position 2)
     - InChIKey: `XUNMWLWTZWWEIE-FNORWQNLSA-N`

**Interpretation**: True geometric isomers with different properties.

---

#### E. Structural/Skeletal Isomers

**Example: RAMP_C_000001505 - Cryptoxanthin isomers**
- Same formula: **C40H56O**
- Different carotenoid structures:
  - zeinoxanthin
  - alpha-Cryptoxanthin
  - Cryptoxanthin-alpha

**Interpretation**: Different arrangement of conjugated double bonds.

---

**Example: RAMP_C_000000998 - Hexanal vs 4-Methylpentanal**
- Same formula: **C6H12O**
- Different carbon skeletons:
  1. **hexanal**: straight chain (CH3-CH2-CH2-CH2-CH2-CHO)
  2. **4-methylpentanal**: branched ((CH3)2CH-CH2-CH2-CHO)

**Interpretation**: Constitutional isomers. Should have separate IDs.

---

## Recommendations

### For Category 1 (Different Formulas - 74.7%):

**These are DATA QUALITY ISSUES that should be addressed:**

1. **Complex lipids should be split** - RAMP IDs grouping multiple distinct lipid species need correction
2. **Verify cross-reference sources** - Check if the external IDs actually map to same or different compounds
3. **Consider creating separate RAMP IDs** for distinct molecular species
4. **Document ambiguous mappings** - Flag these 201 RAMP IDs as "heterogeneous" or "family-level"

### For Category 2 (Same Formula - 25.3%):

**More nuanced - depends on use case:**

#### Should be SPLIT (positional/structural isomers):
- ✗ 1-Methylhistidine vs 3-Methylhistidine (different biology)
- ✗ Hexanal vs 4-methylpentanal (different structure)
- ✗ 4Z-decenedioic acid vs 2E-decenedioic acid (different geometry)
- ✗ zeinoxanthin vs alpha-cryptoxanthin (different carotenoids)

#### Reasonable to GROUP (tautomers/ring forms):
- ✓ Glucose ring forms (alpha/beta/aldehydo - interconvert in solution)
- ✓ Fructose 6-phosphate ring forms (keto/furanose - tautomers)
- ✓ Ribose forms

#### Depends on CONTEXT (oxidation states):
- ? Calcium(0) vs Calcium(2+) - biologically only Ca²⁺ matters
- ? Selenium atom vs selenium(2+)
- ? Molybdenum forms

---

## Impact on ID Extraction

### For the `sources` column extraction:

1. **The 1:1 mapping still holds** - Each external ID maps to exactly one RAMP ID
2. **However, 269 RAMP IDs are "ambiguous"**:
   - 201 contain genuinely different compounds
   - 68 contain structural variants

3. **When using RAMP for entity resolution:**
   - Be aware that ~0.8% of multi-record RAMP IDs are problematic
   - Consider using InChIKey connectivity layer for disambiguation
   - For complex lipids, prefer specific structural identifiers

4. **For omnipath gold layer:**
   - May want to flag these 269 RAMP IDs
   - Consider using InChIKey first 14 chars as primary grouping
   - Use full InChIKey for stereoisomer distinction

---

## Data Quality Score

| Metric | Value |
|--------|-------|
| **Well-formed RAMP IDs** | 99.2% share connectivity layer |
| **Problematic groupings** | 0.8% (269 out of 33,483 multi-record IDs) |
| **Should be split** | ~0.6% (201 different formulas) |
| **Debatable grouping** | ~0.2% (68 same formula isomers) |

**Conclusion**: RaMP aggregation is **highly reliable** (99.2%) but has systematic issues with complex lipid families where different databases report different specific species.

---

## Appendix: Full List of Problematic RAMP IDs

See separate file: `ramp_problematic_ids_list.txt` (269 RAMP IDs)

Most affected compound classes:
1. Phospholipids (PIP, PC, PE, PS) - 150+ cases
2. Glycerophospholipids - 80+ cases
3. Sphingolipids - 40+ cases
4. Simple metabolites with isomers - 30+ cases
5. Metal ions/elements - 10+ cases

**Report generated:** 2025-10-11
