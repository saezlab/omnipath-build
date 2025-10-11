"""
Gold loader module - builds gold tables from silver tables.

This module contains:
- Individual table builders (build_*.py)
- Main orchestration script (3_gold_loader.py)
"""

__all__ = [
    'build_compounds',
    'build_cv_terms',
    'build_entity_evidence',
    'build_identifiers',
    'build_interaction_evidence',
    'build_interactions',
    'build_membership',
    'build_provenance',
    'build_references',
    'build_sources',
]
