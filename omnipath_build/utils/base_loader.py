"""
Base loader class for OmniPath 2.0 pipeline.
Provides common functionality for all data loaders.
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Optional, List
from pathlib import Path

from .database import PostgresDuckDBConnector
from .constants import LoaderConstants

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # dotenv not available, skip loading .env file
    pass


class BaseLoader(ABC):
    """
    Abstract base class for all data loaders in the pipeline.
    Provides common initialization, database connection, and utility methods.
    """
    
    def __init__(self, 
                 db_connector: PostgresDuckDBConnector,
                 logger_name: Optional[str] = None):
        """
        Initialize base loader with provided database connection.
        
        Args:
            db_connector: Database connector instance to use
            logger_name: Name for the logger (defaults to class name)
        """
        # Setup logging
        self.logger = logging.getLogger(logger_name or self.__class__.__name__)
        
        # Use provided database connector
        self.db_connector = db_connector
        
        # Convenience reference to connection
        self.conn = self.db_connector.conn
        
        # Initialize loader-specific attributes
        self._initialize()
        
        self.logger.info(f"{self.__class__.__name__} initialized successfully")
    
    
    @abstractmethod
    def _initialize(self) -> None:
        """
        Initialize loader-specific attributes and setup.
        Must be implemented by subclasses.
        """
        pass
    
    @abstractmethod
    def load(self, *args, **kwargs) -> Any:
        """
        Main loading method. Must be implemented by subclasses.
        """
        pass
    
    def validate(self) -> bool:
        """
        Validate loader state and data. Can be overridden by subclasses.
        
        Returns:
            bool: True if validation passes
        """
        self.logger.info("Running validation...")
        
        # Check database connection
        try:
            self.conn.execute("SELECT 1").fetchone()
            self.logger.debug("Database connection validated")
        except Exception as e:
            self.logger.error(f"Database connection validation failed: {e}")
            return False
        
        # Run subclass-specific validation
        return self._validate_specific()
    
    def _validate_specific(self) -> bool:
        """
        Loader-specific validation. Override in subclasses if needed.
        
        Returns:
            bool: True if validation passes
        """
        return True
    
    def execute_sql(self, sql: str, parameters: Optional[list] = None) -> Any:
        """
        Execute SQL query with error handling and logging.
        
        Args:
            sql: SQL query to execute
            parameters: Optional query parameters
            
        Returns:
            Query results
        """
        try:
            result = self.db_connector.execute(sql, parameters)
            return result
        except Exception as e:
            self.logger.error(f"SQL execution failed: {e}")
            self.logger.debug(f"Failed SQL: {sql}")
            raise
    
    def create_schemas(self, schemas: List[str]) -> None:
        """
        Create multiple schemas if they don't exist.
        
        Args:
            schemas: List of schema names to create
        """
        for schema in schemas:
            self.db_connector.create_schema_if_not_exists(schema)
        self.logger.info(f"Ensured schemas exist: {schemas}")
    
    def get_table_row_count(self, table_name: str, schema: Optional[str] = None) -> int:
        """
        Get row count for a table.
        
        Args:
            table_name: Name of the table
            schema: Optional schema name
            
        Returns:
            int: Number of rows
        """
        return self.db_connector.get_row_count(table_name, schema)
    
    def table_exists(self, table_name: str, schema: Optional[str] = None) -> bool:
        """
        Check if a table exists.
        
        Args:
            table_name: Name of the table
            schema: Optional schema name
            
        Returns:
            bool: True if table exists
        """
        return self.db_connector.table_exists(table_name, schema)
    
    def sanitize_table_name(self, *parts: str) -> str:
        """
        Sanitize and combine parts into a valid table name.
        
        Args:
            *parts: Parts to combine into table name
            
        Returns:
            str: Sanitized table name
        """
        # Join parts with double underscore
        combined = "__".join(str(part) for part in parts)
        
        # Replace invalid characters
        sanitized = combined.replace("-", "_").replace(" ", "_").replace(".", "_")
        
        # Remove consecutive underscores
        while "__" in sanitized:
            sanitized = sanitized.replace("__", "_")
        
        # Ensure it starts with a letter or underscore
        if sanitized and not sanitized[0].isalpha() and sanitized[0] != '_':
            sanitized = f"t_{sanitized}"
        
        return sanitized.lower()
    
    def format_row_count(self, count: int) -> str:
        """
        Format row count with thousands separator.
        
        Args:
            count: Row count
            
        Returns:
            str: Formatted count (e.g., "1,234,567")
        """
        return f"{count:,}"
    
    def log_progress(self, current: int, total: Optional[int] = None, 
                    message: str = "Processing") -> None:
        """
        Log progress at regular intervals.
        
        Args:
            current: Current item number
            total: Total items (optional)
            message: Progress message prefix
        """
        if current % LoaderConstants.PROGRESS_LOG_INTERVAL == 0:
            if total:
                percentage = (current / total) * 100
                self.logger.info(
                    f"{message}: {self.format_row_count(current)}/{self.format_row_count(total)} "
                    f"({percentage:.1f}%)"
                )
            else:
                self.logger.info(f"{message}: {self.format_row_count(current)} rows")
    
    def ensure_directory(self, path: Path) -> Path:
        """
        Ensure a directory exists, creating it if necessary.
        
        Args:
            path: Directory path
            
        Returns:
            Path: The directory path
        """
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        return path
    
    def close(self) -> None:
        """
        Close database connections and cleanup resources.
        Can be extended by subclasses.
        """
        try:
            self.db_connector.close()
            self.logger.info(f"{self.__class__.__name__} closed successfully")
        except Exception as e:
            self.logger.warning(f"Error during cleanup: {e}")
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit with cleanup."""
        self.close()
        return False  # Don't suppress exceptions