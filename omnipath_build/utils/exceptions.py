__all__ = [
    'BronzeLoaderError',
    'ConnectionError',
    'GoldLoaderError',
    'LoaderError',
    'OmniPathError',
    'SilverLoaderError',
]

"""Custom exceptions for OmniPath 2.0 pipeline.
Provides a hierarchy of exceptions for better error handling.
"""


class OmniPathError(Exception):
    """Base exception for all OmniPath pipeline errors."""

    pass


class ConnectionError(OmniPathError):
    """Raised when database connection fails."""

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
