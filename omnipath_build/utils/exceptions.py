__all__ = [
    'BronzeLoaderError',
    'ConfigurationError',
    'ConnectionError',
    'DataProcessingError',
    'GoldLoaderError',
    'LoaderError',
    'OmniPathError',
    'ResourceNotFoundError',
    'SQLExecutionError',
    'SilverLoaderError',
    'TransformationError',
    'ValidationError',
]

"""Custom exceptions for OmniPath 2.0 pipeline.
Provides a hierarchy of exceptions for better error handling.
"""


class OmniPathError(Exception):
    """Base exception for all OmniPath pipeline errors."""

    pass


class ConfigurationError(OmniPathError):
    """Raised when configuration is invalid or missing."""

    pass


class ConnectionError(OmniPathError):
    """Raised when database connection fails."""

    pass


class DataProcessingError(OmniPathError):
    """Raised during data processing operations."""

    pass


class ValidationError(OmniPathError):
    """Raised when data validation fails."""

    pass


class ResourceNotFoundError(OmniPathError):
    """Raised when a required resource is not found."""

    pass


class LoaderError(OmniPathError):
    """Base exception for loader-specific errors."""

    pass


class BronzeLoaderError(LoaderError):
    """Errors specific to bronze layer loading."""

    pass


class SilverLoaderError(LoaderError):
    """Errors specific to silver layer loading."""

    pass


class GoldLoaderError(LoaderError):
    """Errors specific to gold layer loading."""

    pass


class TransformationError(DataProcessingError):
    """Raised when data transformation fails."""

    pass


class SQLExecutionError(OmniPathError):
    """Raised when SQL execution fails."""

    def __init__(self, message: str, sql: str = None) -> None:
        super().__init__(message)
        self.sql = sql
