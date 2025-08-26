"""
Logging utilities.
Provides standardized logging configuration and decorators.
"""

import logging
import time
import functools
from typing import Callable, Any


def setup_logging(
    logger_name: str,
    level: int = logging.INFO,
    format_string: str = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
) -> logging.Logger:
    """
    Setup standardized logging configuration.
    
    Args:
        logger_name: Name for the logger
        level: Logging level (default: INFO)
        format_string: Log message format
        
    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(logger_name)
    
    # Only add handler if logger doesn't have one
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(format_string)
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    
    logger.setLevel(level)
    return logger


def log_execution_time(logger: logging.Logger = None, 
                      message_prefix: str = "Execution time") -> Callable:
    """
    Decorator to log execution time of a function.
    
    Args:
        logger: Logger instance to use (uses function's module logger if None)
        message_prefix: Prefix for the log message
        
    Returns:
        Decorated function
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            # Get logger
            nonlocal logger
            if logger is None:
                logger = logging.getLogger(func.__module__)
            
            # Log start
            func_name = func.__name__
            logger.debug(f"Starting {func_name}")
            
            # Execute function
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                elapsed = time.time() - start_time
                
                # Log completion
                logger.info(f"{message_prefix} for {func_name}: {elapsed:.2f}s")
                return result
                
            except Exception as e:
                elapsed = time.time() - start_time
                logger.error(f"{func_name} failed after {elapsed:.2f}s: {e}")
                raise
        
        return wrapper
    return decorator


def log_row_count(logger: logging.Logger = None,
                  table_name: str = None) -> Callable:
    """
    Decorator to log row count changes for database operations.
    
    Args:
        logger: Logger instance to use
        table_name: Name of the table being modified
        
    Returns:
        Decorated function
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs) -> Any:
            # Get logger
            nonlocal logger
            if logger is None:
                logger = getattr(self, 'logger', logging.getLogger(func.__module__))
            
            # Try to get table name from arguments if not provided
            nonlocal table_name
            if table_name is None and args:
                table_name = str(args[0]) if args else "unknown"
            
            # Execute function
            result = func(self, *args, **kwargs)
            
            # Log result if it's a row count
            if isinstance(result, int):
                logger.info(f"{func.__name__} - {table_name}: {result:,} rows")
            
            return result
        
        return wrapper
    return decorator


def log_progress(total: int = None, 
                interval: int = 10000,
                logger: logging.Logger = None) -> Callable:
    """
    Decorator for functions that process items in a loop.
    Logs progress at regular intervals.
    
    Args:
        total: Total number of items (if known)
        interval: Log progress every N items
        logger: Logger instance to use
        
    Returns:
        Decorated function
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            # Get logger
            nonlocal logger
            if logger is None:
                logger = logging.getLogger(func.__module__)
            
            # Create progress tracker
            progress = {'count': 0, 'last_logged': 0}
            
            # Wrap any generator/iterator results
            result = func(*args, **kwargs)
            
            if hasattr(result, '__iter__') and not isinstance(result, (str, bytes)):
                def track_progress(items):
                    for item in items:
                        progress['count'] += 1
                        
                        if progress['count'] - progress['last_logged'] >= interval:
                            if total:
                                percentage = (progress['count'] / total) * 100
                                logger.info(
                                    f"{func.__name__} progress: {progress['count']:,}/{total:,} "
                                    f"({percentage:.1f}%)"
                                )
                            else:
                                logger.info(f"{func.__name__} progress: {progress['count']:,} items")
                            
                            progress['last_logged'] = progress['count']
                        
                        yield item
                    
                    # Log final count
                    if progress['count'] > progress['last_logged']:
                        logger.info(f"{func.__name__} completed: {progress['count']:,} items")
                
                return track_progress(result)
            
            return result
        
        return wrapper
    return decorator


class LogContext:
    """
    Context manager for temporary log level changes.
    
    Example:
        with LogContext(logger, logging.DEBUG):
            # Debug logging enabled here
            logger.debug("This will be logged")
    """
    
    def __init__(self, logger: logging.Logger, level: int):
        """
        Initialize log context.
        
        Args:
            logger: Logger to modify
            level: Temporary log level
        """
        self.logger = logger
        self.new_level = level
        self.old_level = None
    
    def __enter__(self):
        """Enter context and change log level."""
        self.old_level = self.logger.level
        self.logger.setLevel(self.new_level)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context and restore log level."""
        self.logger.setLevel(self.old_level)
        return False