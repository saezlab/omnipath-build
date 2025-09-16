"""Shared utilities for OmniPath 2.0 database build pipeline."""

from .database import (
    ConnectionError,
    PostgresConnector,
    PostgresDuckDBConnector,
)
from .constants import SQLPatterns, LoaderConstants
from .exceptions import (
    LoaderError,
    OmniPathError,
    GoldLoaderError,
    BronzeLoaderError,
    SilverLoaderError,
)
from .base_loader import BaseLoader
from .sql_adapter import SQLAdapter, SQLExecutionManager
from .bronze_utils import BronzeWriter
from .logging_utils import (
    log_execution_time,
)
from .pypath_adapter import PyPathAdapter, PyPathMethodInfo
from .config_validator import PyPathConfigValidator

__all__ = [
    # Database
    'PostgresDuckDBConnector',
    'PostgresConnector',
    'ConnectionError',
    # Constants
    'LoaderConstants',
    'SQLPatterns',
    # Base classes
    'BaseLoader',
    # Utilities
    'BronzeWriter',
    'SQLAdapter',
    'SQLExecutionManager',
    'PyPathAdapter',
    'PyPathMethodInfo',
    'PyPathConfigValidator',
    # Logging
    'log_execution_time',
    # Exceptions
    'OmniPathError',
    'LoaderError',
    'BronzeLoaderError',
    'SilverLoaderError',
    'GoldLoaderError',
]
