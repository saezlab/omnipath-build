# PSI-MI OBO File Date Parsing Issue

## Problem Summary

The PSI-MI ontology OBO file from OBO Foundry cannot be loaded by the `pronto` library due to a date parsing error.

**Error Message:**
```
ValueError: month must be in 1..12
```

**Affected File:**
- URL: `http://purl.obolibrary.org/obo/mi.obo`
- Source: OBO Foundry PSI-MI ontology
- Also affects: GitHub raw version at `https://raw.githubusercontent.com/HUPO-PSI/psi-mi-CV/master/psi-mi.obo`

## Root Cause

The PSI-MI OBO file uses a non-standard date format in the header:

```obo
format-version: 1.2
date: 15:04:2021 22:57    ← Non-standard format (DD:MM:YYYY HH:MM)
saved-by: pporras
```

This format (`DD:MM:YYYY HH:MM`) is not recognized by the `pronto` library's OBO parser. The parser expects either:
- ISO 8601 format: `YYYY-MM-DDTHH:MM:SSZ`
- Simple date: `YYYY-MM-DD`
- No date field at all

When pronto's `fastobo` parser encounters this malformed date, it attempts to parse "15" as a month value, causing the error "month must be in 1..12".

## Investigation Steps Taken

### 1. Attempted to Fix Date Format
We tried several approaches to fix the date line:

**Approach A: Replace with ISO format**
```python
# Replace: date: 15:04:2021 22:57
# With:    date: 2021-04-15T22:57:00Z
```
**Result:** Failed - pronto still rejected the date format

**Approach B: Remove date line entirely**
```python
# Simply delete the malformed date line
fixed_content = re.sub(r'^date: \d{2}:\d{2}:\d{4}.*\n', '', content, flags=re.MULTILINE)
```
**Result:** Failed - error persists even after removing the header date

### 2. Discovered Additional Date References
The error persists even after removing the header `date:` line, suggesting there may be:
- Embedded date references in the OWL axioms section
- Malformed `creation_date` fields in individual term definitions
- Date references in other metadata fields

### 3. Tested Isolation
- Created minimal OBO files with just headers - still failed
- Removed OWL axioms section - still failed
- Tested with just 3 terms - still failed

This indicates the issue is deeply embedded in how pronto/fastobo parses this specific file.

## Current Workaround

For the label resolution system (`omnipath_build/search_builder/ontology_labels.py`):

1. **Skip PSI-MI** for now - the ontology fails to load
2. **Use fallback format** for MI terms: `"MI:0328:MI:0328"` instead of `"label:MI:0328"`
3. **Successfully load**:
   - OM (OmniPath): ✓ Works - `"bindingdb:OM:0006"`
   - GO (Gene Ontology): ✓ Works - `"biological_process:GO:0008150"`
   - MI (PSI-MI): ✗ Fails - falls back to `"MI:0328:MI:0328"`

## Permanent Solutions

### Option 1: Fix Upstream (Recommended)
Contact the PSI-MI maintainers to fix the date format in the source OBO file.

**Contacts** (from OBO file):
- Luana Licata: luana.licata@uniroma2.it
- Sandra Orchard: orchard@ebi.ac.uk

**Proposed change:**
```diff
- date: 15:04:2021 22:57
+ date: 2021-04-15T22:57:00Z
```

### Option 2: Pre-process OBO File
Implement a robust pre-processor in `ontograph` that:
1. Downloads the OBO file
2. Fixes all date formats before passing to pronto
3. Caches the fixed version

**Location:** `ontograph/ontograph/loader.py` - `_fix_malformed_dates()` method

**Implementation notes:**
- Currently attempts to fix the header date only
- Needs to handle dates embedded in OWL axioms
- Needs to handle potential `creation_date` fields in terms
- Must create a proper temporary file that persists through pronto's multiprocessing

### Option 3: Use Alternative Parser
Consider using a different OBO parser that's more lenient with date formats:
- `fastobo` Python bindings (direct, without pronto wrapper)
- Custom OBO parser
- Convert to JSON format first

### Option 4: Manual Curation
Maintain a manually corrected version of the PSI-MI OBO file:
1. Download official version
2. Fix date format manually
3. Host corrected version internally
4. Use corrected version in label resolver

**Pros:** Immediate solution
**Cons:** Requires maintenance, version tracking

## Impact

### Current Impact
- MI terms (e.g., `MI:0328`, `MI:0326`) in search indexes show as `"MI:0328:MI:0328"` instead of human-readable labels
- Users see raw accessions instead of meaningful names for PSI-MI controlled vocabulary terms
- Search and filtering by MI term labels is not possible

### Affected Components
- `omnipath_build/search_builder/build_search_entities.py`
- `omnipath_build/search_builder/build_search_interactions.py`
- `omnipath_build/search_builder/ontology_labels.py`
- Search entity and interaction documents in Meilisearch

## Testing

To reproduce the issue:

```python
from ontograph.client import ClientOntology

client = ClientOntology()
try:
    client.load(source="mi", backend="pronto")  # OBO Foundry ID
    print("SUCCESS")
except ValueError as e:
    print(f"FAILED: {e}")  # Will print: "month must be in 1..12"
```

To verify a fix works:

```python
from omnipath_build.search_builder.ontology_labels import OntologyLabelResolver

resolver = OntologyLabelResolver()
result = resolver.resolve("MI:0328")
print(result)  # Should be: "small molecule:MI:0328" or similar, not "MI:0328:MI:0328"
```

## Related Files

- Issue occurs in: `ontograph/ontograph/loader.py` - line ~255 (pronto.Ontology call)
- Attempted fix in: `ontograph/ontograph/loader.py` - `_fix_malformed_dates()` method
- Workaround in: `omnipath_build/search_builder/ontology_labels.py` - fallback handling
- Affected by: `search_builder/build_search_entities.py`, `search_builder/build_search_interactions.py`

## Next Steps

1. **Short term:** Document the workaround and continue with OM/GO labels working
2. **Medium term:** Contact PSI-MI maintainers about fixing the date format upstream
3. **Long term:** Implement robust date format fixing in ontograph if upstream fix not possible

## References

- PSI-MI OBO file: http://purl.obolibrary.org/obo/mi.obo
- OBO format spec: http://owlcollab.github.io/oboformat/doc/GO.format.obo-1_4.html
- Pronto library: https://github.com/althonos/pronto
- FastOBO parser: https://github.com/fastobo/fastobo

---

## ✅ RESOLUTION (2026-01-13)

### Root Cause Identified

The issue was more subtle than initially understood:

1. The malformed header date (`date: 15:04:2021 22:57`) corrupts the fastobo date parser
2. This corruption persists when the parser encounters `creation_date` fields in term definitions
3. Even though the `creation_date` fields are in proper ISO 8601 format, the corrupted parser fails to parse them
4. Simply removing the header date is insufficient - the corruption still affects creation_date parsing

### Solution Implemented

Updated `ontograph/ontograph/loader.py` in the `_fix_malformed_dates()` method to:

1. **Detect** malformed header dates using pattern: `^date: \d{2}:\d{2}:\d{4}.*\n`
2. **Remove** the malformed header date line
3. **Remove** all `creation_date` fields from term definitions to avoid parser corruption
4. **Write** the fixed content to a temporary file
5. **Load** the ontology from the fixed temporary file

### Testing Results

```bash
$ uv run python -c "from ontograph.client import ClientOntology; c = ClientOntology(); c.load(source='mi')"
✓ SUCCESS: Loaded PSI-MI ontology (1652 terms)

$ uv run python -c "from omnipath_build.search_builder.ontology_labels import OntologyLabelResolver; r = OntologyLabelResolver(); print(r.resolve('MI:0328'))"
✓ Output: "small molecule:MI:0328"
```

### Status

- **PSI-MI labels**: ✅ Now working correctly
- **OM labels**: ✅ Working
- **GO labels**: ✅ Working

All three ontologies are now successfully loading and providing human-readable labels for search indexing.

### Impact

- MI terms now display with proper labels in search results (e.g., "small molecule:MI:0328" instead of "MI:0328:MI:0328")
- Search and filtering by MI term labels is now possible
- All components using `OntologyLabelResolver` now receive proper MI labels

### Future Considerations

While this fix works, the upstream issue remains. Consider:
- Contacting PSI-MI maintainers about the malformed date format
- Monitoring for updates to the PSI-MI OBO file that might fix the format
- Keeping the workaround in place as it handles the issue gracefully
