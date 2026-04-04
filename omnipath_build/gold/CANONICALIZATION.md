# Gold canonicalization summary

## Current model

- `entity_cross_references.parquet` stores raw source-provided identifiers
- `entity_identifiers.parquet` stores only authoritative identifiers produced by canonicalization
- `entities.canonical_identifier` is chosen from the authoritative identifier set

## Pipeline

1. `convert.py` writes entities and raw cross references
2. `canonicalize.py` reads cross references and resolves supported IDs:
   - proteins -> UniProt
   - chemicals/lipids -> Standard InChI
3. An entity is canonicalized only if all supported evidence resolves to exactly one backbone
4. The accepted backbone is expanded to the full authoritative identifier set
5. `dedup.py` merges entities using authoritative identifiers, not raw cross references

## What gets used as evidence

All resolver-supported raw cross references are used.

Examples:
- proteins: UniProt, Ensembl, Entrez, gene primary, gene synonym, UniProt entry name
- chemicals: Standard InChI, HMDB, ChEBI, LipidMaps, SwissLipids

## Conflict policy

- If multiple source identifiers resolve to the same backbone, the entity is canonicalized
- If they resolve to different backbones, the entity is left unresolved
- For chemicals/lipids, conflicts should be reported separately as:
  - backbone conflicts: different backbones after normal comparison
  - near conflicts: differences only in stereo/protonation layers
- No source namespace wins by default in conflicts
- Raw source identifiers remain preserved in `entity_cross_references.parquet`

## Reporting

- Per-source reports should be tabular and easy to scan
- Use identifier type labels in reports, not accession strings
- Include counts for backbone conflicts and near conflicts separately
- Include one overview report across all sources so problematic sources are easy to spot

## Consequence

A source can provide many identifiers, but only a unique, non-conflicting resolved backbone becomes authoritative.
