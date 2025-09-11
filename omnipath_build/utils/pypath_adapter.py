"""PyPath Adapter for OmniPath 2.0 database builder.

This adapter integrates pypath.inputs resource-specific methods with the existing
bronze loader infrastructure, allowing direct downloading and preprocessing of data
from original sources via pypath.

Usage:
    adapter = PyPathAdapter()
    data = adapter.get_resource_data('uniprot_db.all_uniprots')
"""

from typing import Any
import inspect
import logging
from pathlib import Path
from dataclasses import dataclass
from collections.abc import Callable

import pandas as pd
import pypath.inputs

from .exceptions import BronzeLoaderError

__all__ = [
    'PyPathAdapter',
    'PyPathMethodInfo',
]


@dataclass
class PyPathMethodInfo:
    """Information about a pypath input method."""

    module_name: str
    method_name: str
    full_name: str
    method: Callable
    signature: inspect.Signature
    docstring: str | None


class PyPathAdapter:
    """Adapter for integrating pypath.inputs with the OmniPath 2.0 bronze loader.

    This class provides a clean interface to access pypath's resource-specific
    download methods and integrates them with the existing pipeline infrastructure.
    """

    def __init__(self) -> None:
        """Initialize the PyPath adapter."""
        self.logger = logging.getLogger(self.__class__.__name__)

    def get_method_info(self, method_name: str) -> PyPathMethodInfo | None:
        """Get information about a specific method.

        Args:
            method_name: Full method name (e.g., 'uniprot_db.all_uniprots')

        Returns:
            PyPathMethodInfo object or None if method not found
        """
        try:
            # Get the method using pypath's get_method function
            method = pypath.inputs.get_method(method_name)
            if method:
                signature = inspect.signature(method)
                docstring = inspect.getdoc(method)
                module_name, func_name = method_name.split('.', 1)

                return PyPathMethodInfo(
                    module_name=module_name,
                    method_name=func_name,
                    full_name=method_name,
                    method=method,
                    signature=signature,
                    docstring=docstring,
                )
        except (ImportError, AttributeError, OSError, ValueError) as e:
            self.logger.debug(
                f'Could not get method info for {method_name}: {e}'
            )

        return None

    def get_resource_data(
        self,
        method_name: str,
        **kwargs: Any,  # noqa: ANN401
    ) -> pd.DataFrame:
        """Get resource data using a pypath.inputs method.

        Args:
            method_name: Full method name (e.g., 'uniprot_db.all_uniprots')
            **kwargs: Arguments to pass to the pypath method

        Returns:
            pandas DataFrame with the resource data

        Raises:
            BronzeLoaderError: If method not found or execution fails
        """
        self.logger.info(f'Getting resource data using method: {method_name}')

        method_info = self.get_method_info(method_name)
        if not method_info:
            raise BronzeLoaderError(f'PyPath method not found: {method_name}')

        try:
            # Get the method using pypath's get_method function
            method = pypath.inputs.get_method(method_name)

            self.logger.debug(f'Calling {method_name} with args: {kwargs}')

            # Call the method
            result = method(**kwargs)

            # Convert result to DataFrame
            df = self._convert_to_dataframe(result, method_name)

            self.logger.info(f'Retrieved {len(df)} rows from {method_name}')
            return df

        except (ImportError, AttributeError, OSError) as e:
            self.logger.error(f'Error calling pypath method {method_name}: {e}')
            raise BronzeLoaderError(
                f'PyPath method execution failed: {e}'
            ) from e

    def _convert_to_dataframe(
        self,
        data: Any,  # noqa: ANN401
        method_name: str,
    ) -> pd.DataFrame:
        """Convert pypath method result to pandas DataFrame.

        Args:
            data: Result from pypath method
            method_name: Name of the method that produced this data

        Returns:
            pandas DataFrame
        """
        if isinstance(data, pd.DataFrame):
            # Clean DataFrame by converting complex objects to strings
            return self._clean_dataframe_for_parquet(data)

        elif isinstance(data, list | tuple):
            if not data:
                return pd.DataFrame()

            # Check if it's a list of dictionaries
            if isinstance(data[0], dict):
                df = pd.DataFrame(data)
                return self._clean_dataframe_for_parquet(df)

            # Check if it's a list of tuples/lists (tabular data)
            elif isinstance(data[0], list | tuple):
                df = pd.DataFrame(data)
                return self._clean_dataframe_for_parquet(df)

            # Simple list - convert to single column
            else:
                df = pd.DataFrame({f'{method_name}_value': data})
                return self._clean_dataframe_for_parquet(df)

        elif isinstance(data, dict):
            # Single record
            df = pd.DataFrame([data])
            return self._clean_dataframe_for_parquet(df)

        elif hasattr(data, '__iter__') and not isinstance(data, str | bytes):
            # Try to convert iterable to list first
            try:
                data_list = list(data)
                return self._convert_to_dataframe(data_list, method_name)
            except (ImportError, AttributeError, OSError) as e:
                self.logger.warning(
                    f'Could not convert iterable to DataFrame: {e}'
                )
                return pd.DataFrame({f'{method_name}_value': [str(data)]})

        else:
            # Single value
            return pd.DataFrame({f'{method_name}_value': [data]})

    def _clean_dataframe_for_parquet(self, df: pd.DataFrame) -> pd.DataFrame:
        """Clean DataFrame by converting complex objects to string representations that can be saved to parquet format.

        This method handles complex objects that cannot be directly serialized to parquet
        format by converting them to string representations.

        Args:
            df: Input DataFrame

        Returns:
            Cleaned DataFrame safe for parquet conversion
        """
        df_cleaned = df.copy()

        for column in df_cleaned.columns:
            try:
                # Check if column contains complex objects that can't be converted to parquet
                # Try to infer the arrow type - if it fails, convert to string
                import pyarrow as pa

                try:
                    pa.array(df_cleaned[column])
                except (ImportError, AttributeError, OSError):
                    # Convert complex objects to string representation
                    self.logger.debug(
                        f"Converting column '{column}' to string due to complex objects"
                    )
                    df_cleaned[column] = df_cleaned[column].astype(str)
            except ImportError:
                # PyArrow not available, use fallback method
                # Check for common complex object types and convert to string
                sample_val = (
                    df_cleaned[column].iloc[0] if len(df_cleaned) > 0 else None
                )
                if sample_val is not None and hasattr(sample_val, '__class__'):
                    # Check if it's a complex object type that might cause issues
                    val_type = type(sample_val).__name__
                    if val_type not in [
                        'str',
                        'int',
                        'float',
                        'bool',
                        'NoneType',
                    ]:
                        self.logger.debug(
                            f"Converting column '{column}' (type: {val_type}) to string"
                        )
                        df_cleaned[column] = df_cleaned[column].astype(str)
            except (AttributeError, OSError) as e:
                # Fallback: convert problematic columns to string
                self.logger.warning(
                    f"Error processing column '{column}': {e}. Converting to string."
                )
                df_cleaned[column] = df_cleaned[column].astype(str)

        return df_cleaned

    def save_to_parquet(
        self,
        method_name: str,
        output_path: Path | str,
        resource_id: str,
        dataset_name: str,
        **kwargs: Any,  # noqa: ANN401
    ) -> tuple[Path | str, int]:
        """Get resource data and save directly to parquet file.

        Args:
            method_name: Full method name (e.g., 'uniprot_db.all_uniprots')
            output_path: Path or S3 URL to save the parquet file
            resource_id: Resource identifier for metadata
            dataset_name: Dataset name for metadata
            **kwargs: Arguments to pass to the pypath method

        Returns:
            Tuple of (parquet_file_path, row_count)
        """
        self.logger.info(f'Saving {method_name} data to parquet: {output_path}')

        # Get data
        df = self.get_resource_data(method_name, **kwargs)

        if df.empty:
            self.logger.warning(f'No data returned from {method_name}')
            return output_path, 0

        # Add metadata columns
        df['metadata_resource'] = resource_id
        df['metadata_dataset'] = dataset_name
        df['metadata_loaded_at'] = pd.Timestamp.now().isoformat()
        df['metadata_row_number'] = range(1, len(df) + 1)
        df['metadata_pypath_method'] = method_name

        row_count = len(df)

        # Handle S3 vs local paths
        if isinstance(output_path, str) and output_path.startswith('s3://'):
            # Save directly to S3 using DuckDB
            self._save_to_s3_parquet(df, output_path)
        else:
            # Ensure parent directory exists for local paths
            if isinstance(output_path, Path):
                output_path.parent.mkdir(parents=True, exist_ok=True)

            # Save to local parquet file
            df.to_parquet(output_path, index=False)

        self.logger.info(f'Saved {row_count} rows to {output_path}')
        return output_path, row_count

    def _save_to_s3_parquet(self, df: pd.DataFrame, s3_path: str) -> None:
        """Save DataFrame to S3 as parquet using DuckDB.

        Args:
            df: DataFrame to save
            s3_path: S3 path for the parquet file
        """
        # This method will be overridden by subclasses that have DuckDB connection
        # For now, save to temp file for bronze loader to handle
        import tempfile

        with tempfile.NamedTemporaryFile(
            suffix='.parquet', delete=False
        ) as temp_file:
            df.to_parquet(temp_file.name, index=False)
            temp_path = temp_file.name

        # Store temp path for caller to handle S3 upload
        self._temp_parquet_path = temp_path

    def get_method_parameters(self, method_name: str) -> dict[str, Any]:
        """Get parameter information for a method.

        Args:
            method_name: Full method name

        Returns:
            Dictionary with parameter information
        """
        method_info = self.get_method_info(method_name)
        if not method_info:
            return {}

        params = {}
        for param_name, param in method_info.signature.parameters.items():
            params[param_name] = {
                'name': param_name,
                'kind': param.kind.name,
                'default': param.default
                if param.default != inspect.Parameter.empty
                else None,
                'annotation': param.annotation
                if param.annotation != inspect.Parameter.empty
                else None,
            }

        return params
