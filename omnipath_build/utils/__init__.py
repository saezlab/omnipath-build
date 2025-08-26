"""
Shared utilities for OmniPath 2.0 database build pipeline.
"""

from .database import PostgresDuckDBConnector, ConnectionError
from .constants import LoaderConstants, SQLPatterns
from .base_loader import BaseLoader
from .bronze_utils import BronzeWriter
from .sql_adapter import SQLAdapter, SQLExecutionManager
from .logging_utils import log_execution_time, setup_logging, log_row_count, log_progress, LogContext
from .pypath_adapter import PyPathAdapter, PyPathMethodInfo
from .config_validator import PyPathConfigValidator
from .exceptions import (
    OmniPathError,
    ConfigurationError,
    DataProcessingError,
    ValidationError,
    ResourceNotFoundError,
    LoaderError,
    BronzeLoaderError,
    SilverLoaderError,
    GoldLoaderError,
    TransformationError,
    SQLExecutionError
)

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
    'SQLExecutionError'
]