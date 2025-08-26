# Ruff Fixes TODO

This file contains a comprehensive list of all ruff errors that need to be fixed across the codebase.

## Summary by Error Type

### 1. BLE001 - Blind Exception Catching (85 occurrences)
Files need specific exception handling instead of catching `Exception`:
- database_manager.py: 16 occurrences 
- bronze_loader.py: 7 occurrences
- gold_loader.py: 4 occurrences
- metadata_loader.py: 1 occurrence
- silver_loader.py: 6 occurrences
- simple_template_generator.py: 4 occurrences
- sql_adapter.py: 1 occurrence
- And many others across utils files

**Fix**: Replace `except Exception as e:` with specific exception types or use `except Exception as e:` with `# noqa: BLE001` comment where generic catching is intentional.

### 2. UP015 - Unnecessary mode argument (25 occurrences)
Remove `'r'` mode argument from file opens since it's the default:
- Files: database_manager.py, bronze_loader.py, metadata_loader.py, silver_loader.py, etc.

**Fix**: Change `open(file, 'r')` to `open(file)` or `open(file, encoding='utf-8')`

### 3. UP035 - Deprecated typing imports (16 occurrences) 
Convert old typing imports to modern equivalents:
- `typing.Dict` → `dict`
- `typing.List` → `list`  
- `typing.Tuple` → `tuple`
- `typing.Set` → `set`
- `typing.Optional` → `T | None`

**Fix**: Update import statements and type annotations in:
- metadata_loader.py
- silver_loader.py  
- simple_template_generator.py
- And other utils files

### 4. UP006 - Use modern type annotations (43 occurrences)
Update type annotations to use built-in types:
- `Dict[str, Any]` → `dict[str, Any]`
- `List[str]` → `list[str]`
- `Tuple[int, int]` → `tuple[int, int]`

### 5. UP007 - Use X | Y for type annotations (13 occurrences)
Convert Optional to union syntax:
- `Optional[str]` → `str | None`
- `Optional[Dict[str, Any]]` → `dict[str, Any] | None`

### 6. D205 - Docstring formatting (9 occurrences)
Add blank lines between docstring summary and description:
- bronze_loader.py, gold_loader.py, metadata_loader.py, silver_loader.py

### 7. ANN001 - Missing type annotations (8 occurrences)
Add type annotations to function arguments:
- `db_connector` parameters in loader classes
- Various function parameters in utils files

### 8. ANN401 - Any type disallowed (3 occurrences)
Replace `Any` with more specific types where possible

### 9. E722 - Bare except clause (2 occurrences)
Replace `except:` with `except Exception:`

### 10. UP038 - Use X | Y in isinstance (1 occurrence)
Convert `isinstance(obj, (type1, type2))` to `isinstance(obj, type1 | type2)`

## Files by Priority

### HIGH PRIORITY (Most errors)
1. **simple_template_generator.py** - 23 errors
2. **database_manager.py** - 16 errors  
3. **silver_loader.py** - 15 errors
4. **metadata_loader.py** - 13 errors
5. **bronze_loader.py** - 11 errors

### MEDIUM PRIORITY
6. **sql_adapter.py** - 4 errors
7. **gold_loader.py** - 4 errors
8. **pypath_adapter.py** - 3 errors
9. **base_loader.py** - 2 errors

### LOW PRIORITY
10. Various other utils files with 1-2 errors each

## Detailed Fix List

### database_manager.py
- [ ] Fix 16 BLE001 exceptions - replace with specific exceptions
- [ ] Fix 2 UP015 file mode arguments
- [ ] Total: 18 fixes needed

### simple_template_generator.py  
- [ ] Fix typing imports (UP035): Dict, List, Optional → dict, list, str | None
- [ ] Fix type annotations (UP006, UP007): 8 occurrences
- [ ] Fix 4 BLE001 blind exceptions
- [ ] Fix 1 E722 bare except clause
- [ ] Fix 1 UP038 isinstance call
- [ ] Fix 5 ANN001 missing annotations
- [ ] Total: 23 fixes needed

### silver_loader.py
- [ ] Fix typing imports (UP035): Set, Dict, List → set, dict, list  
- [ ] Fix D205 docstring formatting
- [ ] Fix ANN001 missing db_connector annotation
- [ ] Fix multiple UP006/UP007 type annotations
- [ ] Fix UP015 file mode arguments
- [ ] Fix BLE001 exceptions
- [ ] Total: 15 fixes needed

### metadata_loader.py
- [ ] Fix typing imports (UP035): Dict, Tuple → dict, tuple
- [ ] Fix D205 docstring formatting
- [ ] Fix ANN001 missing db_connector annotation
- [ ] Fix UP006 type annotations: 8 occurrences
- [ ] Fix UP015 file mode arguments: 4 occurrences  
- [ ] Fix ANN401 Any type usage
- [ ] Fix BLE001 exception
- [ ] Total: 13 fixes needed

### bronze_loader.py
- [ ] Fix D205 docstring formatting
- [ ] Fix ANN001 missing db_connector annotation
- [ ] Fix UP015 file mode arguments: 3 occurrences
- [ ] Fix BLE001 exceptions: 7 occurrences
- [ ] Total: 11 fixes needed

## Implementation Strategy

1. **Start with typing imports (UP035)** - These are straightforward find/replace operations
2. **Fix type annotations (UP006, UP007)** - Update all type hints to modern syntax
3. **Remove unnecessary file modes (UP015)** - Simple deletions
4. **Add docstring blank lines (D205)** - Add single blank lines
5. **Add missing type annotations (ANN001)** - Add types to function parameters
6. **Fix exception handling (BLE001)** - Most complex, requires understanding context

## Notes

- Some BLE001 errors may be intentional for robustness - consider adding `# noqa: BLE001` comments
- ANN401 (Any usage) should be evaluated case-by-case - some may be legitimate
- Test thoroughly after each batch of changes to ensure functionality is preserved
- Consider running `uv run ruff check --fix` after manual fixes to catch auto-fixable issues

## Progress Tracking

- [ ] Phase 1: Fix typing imports and annotations (UP035, UP006, UP007) 
- [ ] Phase 2: Fix file operations and formatting (UP015, D205)
- [ ] Phase 3: Add missing annotations (ANN001)
- [ ] Phase 4: Fix exception handling (BLE001) 
- [ ] Phase 5: Final cleanup and testing

**Total Errors: 274**
**Estimated time: 4-6 hours for systematic fixes**
