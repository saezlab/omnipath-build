"""Complete entity matching against immutable OmniPath reference indexes."""

from ._omnipath_resolver import resolve_precomputed_batch, resolve_molecular_batch
from .contracts import RawEntityObservation
from .canonical import LibraryMatcher, Match, get_policy
from .index import FullRuntime
from .resolver import EntityResolver, ResolvedEntityInfo, ResolvedEntityTarget, locate_library_dir

__all__ = [
    "resolve_precomputed_batch",
    "resolve_molecular_batch",
    "RawEntityObservation",
    "LibraryMatcher",
    "Match",
    "get_policy",
    "FullRuntime",
    "EntityResolver",
    "ResolvedEntityInfo",
    "ResolvedEntityTarget",
    "locate_library_dir",
]
