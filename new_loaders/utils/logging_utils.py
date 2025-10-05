"""Logging utilities.

Provides execution time logging decorator.
"""

import time
from typing import Any
import logging
import functools
from collections.abc import Callable

__all__ = [
    'log_execution_time',
]


def log_execution_time(
    logger: logging.Logger = None, message_prefix: str = 'Execution time'
) -> Callable:
    """Decorator to log execution time of a function.

    Args:
        logger: Logger instance to use (uses function's module logger if None)
        message_prefix: Prefix for the log message

    Returns:
        Decorated function
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            # Get logger
            nonlocal logger
            if logger is None:
                logger = logging.getLogger(func.__module__)

            # Log start
            func_name = func.__name__
            logger.debug(f'Starting {func_name}')

            # Execute function
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                elapsed = time.time() - start_time

                # Log completion
                logger.info(f'{message_prefix} for {func_name}: {elapsed:.2f}s')
                return result

            except Exception as e:
                elapsed = time.time() - start_time
                logger.error(f'{func_name} failed after {elapsed:.2f}s: {e}')
                raise

        return wrapper

    return decorator
