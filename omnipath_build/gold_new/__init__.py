"""
Gold loader module (new) - builds gold tables from silver tables with updated schema.

This module contains:
- Individual table builders (build_*.py) adapted for new silver schema
- Main orchestration script (gold_loader_new.py)
"""

from omnipath_build.gold_new.build_sources import build_sources
from omnipath_build.gold_new.build_compounds import build_compounds

__all__ = [
    'build_sources',
    'build_compounds',
]
