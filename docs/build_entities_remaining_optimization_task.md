# Task: investigate and optimize `build_entities` extraction performance

## Goal

Investigate ways to improve the performance of the gold entity build step, specifically the extraction phase that reads silver parquet files and produces entity descriptions.

The work should start with analysis and experiments. Do **not** jump directly to implementation. Compare several options first, measure them, and then implement the best low-risk improvement.

## Current hotspot

The main entry point is:

```text
omnipath_build/gold/build_entities.py
```

It calls:

```python
extract_all_from_silver(silver_dir, source_name)
```

from:

```text
omnipath_build/gold/utils/entity_extraction.py
```

In previous profiling on UniProt, the extraction phase was the dominant cost:

```text
extract_all_from_silver     ~9.3s   (~59% of build_entities runtime)
full build_entities         ~15.8s
```

The exact numbers should be re-measured before making changes.

## Files to inspect

Primary files:

```text
omnipath_build/gold/build_entities.py
omnipath_build/gold/utils/entity_extraction.py
```

Related helper files used heavily during extraction:

```text
omnipath_build/gold/utils/schema.py
omnipath_build/gold/utils/cv_terms.py
omnipath_build/gold/utils/canonicalization.py
```

Pipeline wrapper, useful for end-to-end validation:

```text
omnipath_build/pipeline/tasks.py
```

## Suggested investigation areas

Please think through and benchmark several possible approaches before implementing anything.