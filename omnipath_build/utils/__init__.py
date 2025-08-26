"""Shared utilities for OmniPath 2.0 database build pipeline."""

from .database import ConnectionError, PostgresDuckDBConnector
from .constants import SQLPatterns, LoaderConstants
from .exceptions import (
    LoaderError,
    OmniPathError,
    GoldLoaderError,
    ValidationError,
    BronzeLoaderError,
    SilverLoaderError,
    SQLExecutionError,
    ConfigurationError,
    DataProcessingError,
    TransformationError,
    ResourceNotFoundError,
)
from .base_loader import BaseLoader
from .sql_adapter import SQLAdapter, SQLExecutionManager
from .bronze_utils import BronzeWriter
from .logging_utils import (
    LogContext,
    log_progress,
    log_row_count,
    setup_logging,
    log_execution_time,
)
from .pypath_adapter import PyPathAdapter, PyPathMethodInfo
from .config_validator import PyPathConfigValidator

__all__ = [
    # Database
    'PostgresDuckDBConnector',
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
    'setup_logging',
    'log_row_count',
    'log_progress',
    'LogContext',
    # Exceptions
    'OmniPathError',
    'ConfigurationError',
    'DataProcessingError',
    'ValidationError',
    'ResourceNotFoundError',
    'LoaderError',
    'BronzeLoaderError',
    'SilverLoaderError',
    'GoldLoaderError',
    'TransformationError',
    'SQLExecutionError',
]
