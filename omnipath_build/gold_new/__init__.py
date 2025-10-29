"""
Gold loader module (new) - builds gold tables from silver tables with updated schema.

This module contains:
- Individual table builders (build_*.py) adapted for new silver schema
- Main orchestration script (gold_loader_new.py)
- DuckDB-based alternatives for high-performance processing
"""

from omnipath_build.gold_new.build_cv_terms import build_cv_terms
from omnipath_build.gold_new.build_compounds import build_compounds
from omnipath_build.gold_new.build_sources import build_sources
from omnipath_build.gold_new.build_entity_identifiers import build_entity_identifier_unified
__all__ = [
    'build_cv_terms',
    'build_entity_identifier_unified',
    'build_sources',
    'build_compounds',
]
