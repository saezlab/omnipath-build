"""PyPath Adapter for OmniPath 2.0 database builder.

This adapter integrates pypath.inputs resource-specific methods with the existing
bronze loader infrastructure, allowing direct downloading and preprocessing of data
from original sources via pypath.

Usage:
    adapter = PyPathAdapter()
    data = adapter.get_resource_data('uniprot_db.all_uniprots')
    adapter.get_available_methods()
"""

import ast
from typing import Any
import inspect
import logging
from pathlib import Path
import pkgutil
import tempfile
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
        self._method_cache: dict[str, PyPathMethodInfo] = {}
        self._discover_methods()

    def _discover_methods(self) -> None:
        """Discover all available pypath.inputs methods."""
        self.logger.info('Discovering pypath.inputs methods...')

        method_count = 0

        # Iterate through all submodules in pypath.inputs
        for _importer, modname, ispkg in pkgutil.iter_modules(
            pypath.inputs.__path__
        ):
            try:
                if ispkg:
                    # Handle package modules (folders with __init__.py)
                    self.logger.debug(f'Processing package module: {modname}')
                    try:
                        # Import the package directly
                        actual_module = __import__(
                            f'pypath.inputs.{modname}', fromlist=['']
                        )

                        # Find all callable methods in the package's __init__.py
                        for attr_name in dir(actual_module):
                            if attr_name.startswith('_'):
                                continue

                            attr = getattr(actual_module, attr_name)
                            if not callable(attr):
                                continue

                            # Check if it's a function from this package
                            if (
                                hasattr(attr, '__module__')
                                and f'pypath.inputs.{modname}'
                                in attr.__module__
                            ):
                                full_name = f'{modname}.{attr_name}'

                                try:
                                    signature = inspect.signature(attr)
                                    docstring = inspect.getdoc(attr)

                                    method_info = PyPathMethodInfo(
                                        module_name=modname,
                                        method_name=attr_name,
                                        full_name=full_name,
                                        method=attr,
                                        signature=signature,
                                        docstring=docstring,
                                    )

                                    # Only include methods that return namedtuples
                                    if self._returns_namedtuple(method_info):
                                        self._method_cache[full_name] = (
                                            method_info
                                        )
                                        method_count += 1
                                        self.logger.debug(
                                            f'Found package method: {full_name}'
                                        )
                                    else:
                                        self.logger.debug(
                                            f"Skipped {full_name} - doesn't return namedtuple"
                                        )

                                except (ValueError, TypeError) as e:
                                    self.logger.debug(
                                        f'Could not process method {full_name}: {e}'
                                    )

                    except (ImportError, AttributeError, OSError) as e:
                        self.logger.debug(
                            f'Could not process package {modname}: {e}'
                        )

                else:
                    # Handle regular modules (single .py files)
                    self.logger.debug(f'Processing regular module: {modname}')
                    try:
                        # Direct import of the single-file module
                        actual_module = __import__(
                            f'pypath.inputs.{modname}', fromlist=['']
                        )
                    except (ImportError, AttributeError, OSError) as e:
                        self.logger.debug(
                            f'Could not import module {modname}: {e}'
                        )
                        continue

                    # Find all callable methods in the module
                    for attr_name in dir(actual_module):
                        if attr_name.startswith('_'):
                            continue

                        attr = getattr(actual_module, attr_name)
                        if not callable(attr):
                            continue

                        # Skip if it's not from this module
                        if not hasattr(
                            attr, '__module__'
                        ) or not attr.__module__.endswith(modname):
                            continue

                        full_name = f'{modname}.{attr_name}'

                        try:
                            signature = inspect.signature(attr)
                            docstring = inspect.getdoc(attr)

                            method_info = PyPathMethodInfo(
                                module_name=modname,
                                method_name=attr_name,
                                full_name=full_name,
                                method=attr,
                                signature=signature,
                                docstring=docstring,
                            )

                            # Only include methods that return namedtuples
                            if self._returns_namedtuple(method_info):
                                self._method_cache[full_name] = method_info
                                method_count += 1
                                self.logger.debug(
                                    f'Found regular method: {full_name}'
                                )
                            else:
                                self.logger.debug(
                                    f"Skipped {full_name} - doesn't return namedtuple"
                                )

                        except (ValueError, TypeError) as e:
                            self.logger.debug(
                                f'Could not process method {full_name}: {e}'
                            )

            except (ImportError, AttributeError, OSError) as e:
                self.logger.debug(f'Could not process module {modname}: {e}')

        self.logger.info(f'Discovered {method_count} pypath.inputs methods')

    def get_available_methods(
        self, filter_by_module: str | None = None
    ) -> list[PyPathMethodInfo]:
        """Get list of available pypath.inputs methods.

        Args:
            filter_by_module: Optional module name to filter by (e.g., 'uniprot_db')

        Returns:
            List of PyPathMethodInfo objects
        """
        methods = list(self._method_cache.values())

        if filter_by_module:
            methods = [m for m in methods if m.module_name == filter_by_module]

        return sorted(methods, key=lambda x: x.full_name)

    def get_method_info(self, method_name: str) -> PyPathMethodInfo | None:
        """Get information about a specific method.

        Args:
            method_name: Full method name (e.g., 'uniprot_db.all_uniprots')

        Returns:
            PyPathMethodInfo object or None if method not found
        """
        return self._method_cache.get(method_name)

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
        output_path: Path,
        resource_id: str,
        dataset_name: str,
        **kwargs: Any,  # noqa: ANN401
    ) -> tuple[Path, int]:
        """Get resource data and save directly to parquet file.

        Args:
            method_name: Full method name (e.g., 'uniprot_db.all_uniprots')
            output_path: Path to save the parquet file
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

        # Ensure parent directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Save to parquet
        df.to_parquet(output_path, index=False)

        row_count = len(df)
        self.logger.info(f'Saved {row_count} rows to {output_path}')

        return output_path, row_count

    def save_to_temp_csv(
        self,
        method_name: str,
        resource_id: str,
        dataset_name: str,
        **kwargs: Any,  # noqa: ANN401
    ) -> tuple[Path, int]:
        """Get resource data and save to temporary CSV file.

        Args:
            method_name: Full method name (e.g., 'uniprot_db.all_uniprots')
            resource_id: Resource identifier for metadata
            dataset_name: Dataset name for metadata
            **kwargs: Arguments to pass to the pypath method

        Returns:
            Tuple of (csv_file_path, row_count)
        """
        self.logger.info(f'Saving {method_name} data to temporary CSV')

        # Get data
        df = self.get_resource_data(method_name, **kwargs)

        if df.empty:
            self.logger.warning(f'No data returned from {method_name}')
            # Create empty CSV file
            temp_path = Path(tempfile.mkstemp(suffix='.csv')[1])
            df.to_csv(temp_path, index=False)
            return temp_path, 0

        # Add metadata columns
        df['metadata_resource'] = resource_id
        df['metadata_dataset'] = dataset_name
        df['metadata_loaded_at'] = pd.Timestamp.now().isoformat()
        df['metadata_row_number'] = range(1, len(df) + 1)
        df['metadata_pypath_method'] = method_name

        # Save to temporary CSV
        temp_path = Path(tempfile.mkstemp(suffix='.csv')[1])
        df.to_csv(temp_path, index=False, sep='\t')

        row_count = len(df)
        self.logger.info(
            f'Saved {row_count} rows to temporary CSV: {temp_path}'
        )

        return temp_path, row_count

    def list_available_modules(self) -> list[str]:
        """Get list of available pypath.inputs modules.

        Returns:
            List of module names
        """
        modules = {
            method_info.module_name
            for method_info in self._method_cache.values()
        }
        return sorted(modules)

    def search_methods(self, query: str) -> list[PyPathMethodInfo]:
        """Search for methods by name or docstring.

        Args:
            query: Search query string

        Returns:
            List of matching PyPathMethodInfo objects
        """
        query_lower = query.lower()
        matches = []

        for method_info in self._method_cache.values():
            # Check method name
            if query_lower in method_info.full_name.lower():
                matches.append(method_info)
                continue

            # Check docstring
            if (
                method_info.docstring
                and query_lower in method_info.docstring.lower()
            ):
                matches.append(method_info)

        return sorted(matches, key=lambda x: x.full_name)

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

    def _returns_namedtuple(self, method_info: PyPathMethodInfo) -> bool:
        """Check if a method returns namedtuples (either directly or in collections).

        Args:
            method_info: PyPathMethodInfo object containing method information

        Returns:
            True if the method returns namedtuples, False otherwise
        """
        try:
            ret_annotation = method_info.signature.return_annotation

            if ret_annotation == inspect.Parameter.empty:
                # No annotation - assume it might return namedtuples for PyPath methods
                return True

            # Handle Generator, Iterator, List, etc. that contain namedtuples
            if hasattr(ret_annotation, '__args__') and ret_annotation.__args__:
                inner_type = ret_annotation.__args__[0]
                # Check if it's a specific namedtuple
                if hasattr(inner_type, '_fields'):
                    return True
                # Many PyPath methods use list[tuple] but actually return list[NamedTuple]
                elif inner_type is tuple:
                    return True

            # Handle direct namedtuple return
            elif hasattr(ret_annotation, '_fields'):
                return True

            return False

        except (ImportError, AttributeError, OSError):
            # If we can't determine, assume it might return namedtuples for PyPath methods
            return True

    def extract_namedtuple_from_annotation(
        self, method_info: PyPathMethodInfo
    ) -> dict[str, list[str]]:
        """Extract namedtuple field definitions from function return type annotation.

        Args:
            method_info: PyPathMethodInfo object containing method information

        Returns:
            Dictionary mapping namedtuple names to their field lists
            Example: {'BindingdbInteraction': ['ligand', 'target']}
        """
        try:
            # Get return annotation from signature
            ret_annotation = method_info.signature.return_annotation

            if ret_annotation == inspect.Parameter.empty:
                return {}

            namedtuples = {}

            # Handle Generator, Iterator, List, etc. that contain namedtuples
            if hasattr(ret_annotation, '__args__') and ret_annotation.__args__:
                # Get the inner type (e.g., BindingdbInteraction from Generator[BindingdbInteraction])
                inner_type = ret_annotation.__args__[0]

                # Check if it's a namedtuple (has _fields attribute)
                if hasattr(inner_type, '_fields'):
                    type_name = inner_type.__name__
                    fields = list(inner_type._fields)
                    namedtuples[type_name] = fields
                    self.logger.debug(
                        f'Extracted namedtuple {type_name} with fields {fields} from annotation'
                    )

            # Handle direct namedtuple return (less common)
            elif hasattr(ret_annotation, '_fields'):
                type_name = ret_annotation.__name__
                fields = list(ret_annotation._fields)
                namedtuples[type_name] = fields
                self.logger.debug(
                    f'Extracted namedtuple {type_name} with fields {fields} from annotation'
                )

            return namedtuples

        except (ImportError, AttributeError, OSError) as e:
            self.logger.debug(
                f'Could not extract namedtuple from annotation for {method_info.full_name}: {e}'
            )
            return {}

    def extract_source_fields(self, method_name: str) -> dict[str, list[str]]:
        """Extract namedtuple field definitions from function source code.

        Args:
            method_name: Full method name (e.g., 'biogrid.biogrid_all_interactions')

        Returns:
            Dictionary mapping namedtuple names to their field lists
            Example: {'BiogridInteraction': ['partner_a', 'partner_b', 'pmid', ...]}
        """
        method_info = self.get_method_info(method_name)
        if not method_info:
            return {}

        try:
            source = inspect.getsource(method_info.method)
            tree = ast.parse(source)

            namedtuples = {}

            # Walk through AST to find namedtuple definitions
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    # Check if this is a namedtuple assignment
                    if isinstance(node.value, ast.Call):
                        # Check if it's calling namedtuple
                        func_name = None
                        if (
                            hasattr(node.value.func, 'attr')
                            and node.value.func.attr == 'namedtuple'
                        ):
                            func_name = 'namedtuple'
                        elif (
                            hasattr(node.value.func, 'id')
                            and node.value.func.id == 'namedtuple'
                        ):
                            func_name = 'namedtuple'

                        if func_name and len(node.value.args) >= 2:
                            # Get the namedtuple name
                            tuple_name = None
                            if isinstance(node.value.args[0], ast.Constant):
                                tuple_name = node.value.args[0].value
                            elif isinstance(node.value.args[0], ast.Str):
                                tuple_name = node.value.args[0].s

                            if not tuple_name:
                                continue

                            # Get the fields
                            fields = []
                            field_arg = node.value.args[1]

                            if isinstance(field_arg, ast.List | ast.Tuple):
                                for elt in field_arg.elts:
                                    if isinstance(elt, ast.Constant):
                                        fields.append(elt.value)
                                    elif isinstance(elt, ast.Str):
                                        fields.append(elt.s)
                            elif isinstance(
                                field_arg, ast.Constant
                            ) and isinstance(field_arg.value, str):
                                # Space-separated string
                                fields = field_arg.value.split()

                            if fields:
                                namedtuples[tuple_name] = fields

            if namedtuples:
                self.logger.debug(
                    f'Extracted {len(namedtuples)} namedtuple definitions from {method_name}'
                )

            return namedtuples

        except (ImportError, AttributeError, OSError) as e:
            self.logger.debug(
                f'Could not extract source fields from {method_name}: {e}'
            )
            return {}

    def print_method_help(self, method_name: str) -> None:
        """Print help information for a method.

        Args:
            method_name: Full method name
        """
        method_info = self.get_method_info(method_name)
        if not method_info:
            print(f'Method not found: {method_name}')
            return

        print(f'\n=== PyPath Method: {method_name} ===')
        print(f'Module: {method_info.module_name}')
        print(f'Method: {method_info.method_name}')
        print(f'Signature: {method_info.signature}')

        if method_info.docstring:
            print('\nDocstring:')
            print(method_info.docstring)
        else:
            print('\nNo docstring available')

        # Show parameters
        params = self.get_method_parameters(method_name)
        if params:
            print('\nParameters:')
            for param_name, param_info in params.items():
                default_str = (
                    f' = {param_info["default"]}'
                    if param_info['default'] is not None
                    else ''
                )
                print(f'  {param_name}{default_str}')
