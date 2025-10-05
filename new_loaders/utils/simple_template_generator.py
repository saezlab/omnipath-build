#!/usr/bin/env python3
"""Simple On-Demand Template Generator for PyPath Resources.

This module provides a streamlined approach to generate resource configuration
templates by actually executing PyPath functions and inspecting their output.

Key Features:
- No complex static analysis or AST parsing
- Works with any PyPath function (partials, regular functions, etc.)
- Generates templates on-demand only when needed
- Handles both namedtuples and dict outputs
- Extremely simple and maintainable (~100 lines)
"""

from typing import Any
import inspect
import logging

import pypath.inputs

__all__ = [
    'generate_and_save_template',
    'generate_pypath_template',
]


def generate_pypath_template(resource_name: str) -> dict[str, Any] | None:
    """Generate a resource template for a PyPath resource.

    Args:
        resource_name: Either 'module' (all functions) or 'module.function' (specific function)

    Returns:
        Template dict ready to be saved as YAML, or None if generation failed
    """
    logger = logging.getLogger(__name__)

    # Parse resource name
    if '.' in resource_name:
        module_name, function_name = resource_name.rsplit('.', 1)
        logger.info(
            f'Generating template for function: {module_name}.{function_name}'
        )
        return _generate_function_template(module_name, function_name)
    else:
        module_name = resource_name
        logger.info(f'Generating template for entire module: {module_name}')
        return _generate_module_template(module_name)


def _generate_function_template(
    module_name: str, function_name: str
) -> dict[str, Any] | None:
    """Generate template for a specific function in a module."""
    logger = logging.getLogger(__name__)

    try:
        # Import the module
        module = __import__(f'pypath.inputs.{module_name}', fromlist=[''])

        # Check if function exists
        if not hasattr(module, function_name):
            logger.error(
                f'Function {function_name} not found in module {module_name}'
            )
            return None

        # Get the function
        func = getattr(module, function_name)

        # Extract function info
        func_info = _extract_function_info(func)

        if not func_info:
            logger.error(
                f'Could not extract info from function {function_name}'
            )
            return None

        # Build template with single function
        template = {
            'module': module_name,
            'functions': {function_name: func_info},
        }

        logger.info(
            f'Successfully generated template for {module_name}.{function_name}'
        )
        return template

    except ImportError:
        logger.error(f'Module not found: pypath.inputs.{module_name}')
        return None
    except (AttributeError, TypeError) as e:
        logger.error(
            f'Failed to generate template for {module_name}.{function_name}: {e}'
        )
        return None


def _generate_module_template(module_name: str) -> dict[str, Any] | None:
    """Generate template for an entire module (all functions)."""
    logger = logging.getLogger(__name__)

    try:
        # Import the module
        module = __import__(f'pypath.inputs.{module_name}', fromlist=[''])

        # Discover all functions in the module
        functions = _discover_module_functions(module, module_name)

        if not functions:
            logger.error(f'No suitable functions found in module {module_name}')
            return None

        # Build template with all functions
        template = {'module': module_name, 'functions': functions}

        logger.info(
            f'Successfully generated template for {module_name} with {len(functions)} functions'
        )
        return template

    except ImportError:
        logger.error(f'Module not found: pypath.inputs.{module_name}')
        return None
    except (AttributeError, TypeError) as e:
        logger.error(f'Failed to generate template for {module_name}: {e}')
        return None


def _discover_module_functions(
    module: Any,  # noqa: ANN401
    module_name: str,
) -> dict[str, dict[str, Any]]:
    """Discover all suitable functions in a PyPath module.

    Args:
        module: The imported module object
        module_name: Name of the module for logging

    Returns:
        Dict mapping function names to their extracted info
    """
    logger = logging.getLogger(__name__)
    functions = {}

    for attr_name in dir(module):
        if attr_name.startswith('_'):
            continue

        attr = getattr(module, attr_name)

        # Only process callables
        if not callable(attr):
            continue

        # Skip classes that are namedtuple definitions (not actual functions)
        if isinstance(attr, type) and issubclass(attr, tuple):
            continue

        # Skip imported functions/modules from other libraries
        if hasattr(attr, '__module__'):
            # Accept if it's from this pypath module, or if it's a partial
            if f'pypath.inputs.{module_name}' not in str(attr.__module__):
                if not hasattr(attr, 'func'):  # Not a partial
                    continue

        try:
            # Extract function info
            func_info = _extract_function_info(attr)

            if func_info:  # Only add if we extracted some useful info
                functions[attr_name] = func_info
                logger.debug(f'Added function: {attr_name}')
            else:
                logger.debug(f'Skipped function (no useful info): {attr_name}')

        except (AttributeError, TypeError) as e:
            logger.debug(f'Error processing function {attr_name}: {e}')

    return functions


def _extract_function_info(func: Any) -> dict[str, Any]:  # noqa: ANN401
    """Extract all useful information from a PyPath function.

    Uses the pragmatic approach:
    1. Get parameter info from signature
    2. Try to execute function to get sample output structure
    3. Build template with discovered fields
    """
    info = {}

    # Get description
    docstring = inspect.getdoc(func)
    if docstring:
        # Use first line, truncated
        info['description'] = docstring.split('\n')[0][:150]

    # Get parameters with defaults
    try:
        sig = inspect.signature(func)
        params = {}

        for param_name, param in sig.parameters.items():
            if param_name in ['self', 'cls']:
                continue

            # Use actual default value, or '?' for required params
            if param.default != inspect.Parameter.empty:
                params[param_name] = param.default
            else:
                params[param_name] = '?'  # User must fill this in

        if params:
            info['kwargs'] = params

    except (AttributeError, TypeError, ValueError):
        pass

    # Try to get output structure by executing the function
    fields = _discover_output_fields(func)
    if fields:
        info['processing'] = {
            'target_table': '?',  # User must specify
            'field_mapping': [
                {
                    'source': field,
                    'target': '?',  # User must specify target column
                }
                for field in fields
            ],
        }

    return info


def _discover_output_fields(func: Any) -> list[str] | None:  # noqa: ANN401
    """Discover output fields by actually executing the function.

    This is the key insight - instead of complex static analysis,
    we just run the function and see what comes out.
    """
    logger = logging.getLogger(__name__)

    try:
        # Execute function with default parameters
        result = func()

        # Get first record to inspect structure
        first_record = next(iter(result))

        # Extract fields based on type
        if hasattr(first_record, '_fields'):
            # It's a namedtuple - perfect!
            fields = list(first_record._fields)
            logger.debug(f'Discovered namedtuple fields: {fields[:5]}...')
            return fields

        elif isinstance(first_record, dict):
            # It's a dictionary - get keys
            fields = list(first_record.keys())
            logger.debug(f'Discovered dict fields: {fields[:5]}...')
            return fields

        elif isinstance(first_record, list | tuple):
            # It's a list/tuple - can't easily determine field names
            logger.debug(
                'Function returns list/tuple - no field names available'
            )
            return None

    except StopIteration:
        logger.debug('Function returned empty result')
        return None

    except (TypeError, AttributeError, ImportError, RuntimeError) as e:
        # Function might need required parameters, or network access, etc.
        logger.debug(f'Could not execute function to discover fields: {e}')

        # Fallback: try to extract from type annotations
        return _extract_fields_from_annotations(func)

    return None


def _extract_fields_from_annotations(func: Any) -> list[str] | None:  # noqa: ANN401
    """Fallback method: extract field names from function return type annotations."""
    try:
        sig = inspect.signature(func)
        ret_annotation = sig.return_annotation

        if ret_annotation == inspect.Parameter.empty:
            return None

        # Handle Generator[NamedTuple], List[NamedTuple], etc.
        if hasattr(ret_annotation, '__args__') and ret_annotation.__args__:
            inner_type = ret_annotation.__args__[0]
            if hasattr(inner_type, '_fields'):
                return list(inner_type._fields)

        # Handle direct namedtuple return
        elif hasattr(ret_annotation, '_fields'):
            return list(ret_annotation._fields)

    except (AttributeError, TypeError, ValueError):
        pass

    return None


def generate_and_save_template(resource_name: str, output_path: str) -> bool:
    """Generate template and save it to a file.

    Args:
        resource_name: PyPath resource name
        output_path: Where to save the YAML file

    Returns:
        True if successful, False otherwise
    """
    template = generate_pypath_template(resource_name)
    if not template:
        return False

    from pathlib import Path

    import yaml

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with output_file.open('w') as f:
        # Add header comment
        f.write(f'# AUTO-GENERATED TEMPLATE for {resource_name}\n')
        f.write('# Fill in all question marks (?)\n\n')

        # Write YAML
        yaml.dump(
            template, f, default_flow_style=False, sort_keys=False, indent=2
        )

    return True


if __name__ == '__main__':
    # Quick test
    import sys

    if len(sys.argv) != 2:
        print('Usage: python simple_template_generator.py <resource_name>')
        sys.exit(1)

    logging.basicConfig(level=logging.DEBUG)

    resource_name = sys.argv[1]
    template = generate_pypath_template(resource_name)

    if template:
        import yaml

        print('Generated template:')
        print(yaml.dump(template, default_flow_style=False, sort_keys=False))
    else:
        print('Failed to generate template')
        sys.exit(1)
