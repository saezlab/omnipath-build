"""Basic sanity check tests for test infrastructure.

This file contains simple tests to verify pytest is working correctly.
"""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

__all__ = [
    'test_sum_numbers',
    'test_imports',
]


def test_sum_numbers() -> None:
    """Basic arithmetic test."""
    assert 1 + 1 == 2


def test_imports() -> None:
    """Test that main package modules can be imported."""
    from omnipath_build import SilverLoader, GoldLoader
    from omnipath_build.utils import PathManager

    assert SilverLoader is not None
    assert GoldLoader is not None
    assert PathManager is not None
