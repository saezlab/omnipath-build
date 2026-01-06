"""Loaders for the omnipath_build pipeline."""

from .silver import DiscoveryError, ResourceFunction, run_silver_loader
from .gold import run_gold_loader_new

__all__ = [
    'DiscoveryError',
    'ResourceFunction',
    'run_silver_loader',
    'run_gold_loader_new',
]
