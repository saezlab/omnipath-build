"""
Configuration validator for PyPath database configurations.

This module provides validation of database-specific configurations against
their referenced pypath templates, ensuring all functions exist and parameters are correct.
"""

import logging
import yaml
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional

from .pypath_adapter import PyPathAdapter


class PyPathConfigValidator:
    """Validate pypath configurations against templates and actual pypath methods."""
    
    def __init__(self, 
                 templates_dir: str = "pypath_templates",
                 databases_dir: str = "databases"):
        """Initialize the validator."""
        self.logger = logging.getLogger(self.__class__.__name__)
        self.templates_dir = Path(templates_dir)
        self.databases_dir = Path(databases_dir)
        self.adapter = PyPathAdapter()
        
        self.logger.debug(f"Config validator initialized")
        self.logger.debug(f"  Templates dir: {self.templates_dir}")
        self.logger.debug(f"  Databases dir: {self.databases_dir}")
    
    def validate_database_module(self, database_name: str, module_name: str) -> Tuple[bool, List[str]]:
        """
        Validate a specific database/module configuration.
        
        Args:
            database_name: Database name
            module_name: Module name
            
        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        self.logger.debug(f"Validating {database_name}/{module_name}")
        
        errors = []
        
        try:
            # Load database config
            config_path = self.databases_dir / database_name / "bronze" / "configs" / f"{module_name}.yaml"
            if not config_path.exists():
                return False, [f"Database config not found: {config_path}"]
            
            with config_path.open() as f:
                config = yaml.safe_load(f)
            
            # Check basic structure
            structure_errors = self._validate_config_structure(config)
            errors.extend(structure_errors)
            
            # Load referenced template
            extends_path = config.get('extends')
            if extends_path:
                template_errors = self._validate_against_template(config, extends_path, module_name)
                errors.extend(template_errors)
            
            # Validate against actual pypath
            pypath_errors = self._validate_against_pypath(config, module_name)
            errors.extend(pypath_errors)
            
        except Exception as e:
            errors.append(f"Error loading config: {e}")
        
        is_valid = len(errors) == 0
        if is_valid:
            self.logger.debug(f"✓ {database_name}/{module_name} validation passed")
        else:
            self.logger.warning(f"✗ {database_name}/{module_name} validation failed with {len(errors)} errors")
        
        return is_valid, errors
    
    def _validate_config_structure(self, config: Dict[str, Any]) -> List[str]:
        """Validate basic configuration structure."""
        errors = []
        
        # Required fields
        required_fields = ['extends', 'database', 'functions']
        for field in required_fields:
            if field not in config:
                errors.append(f"Missing required field: {field}")
        
        # Check functions structure
        functions = config.get('functions', {})
        if not isinstance(functions, dict):
            errors.append("'functions' must be a dictionary")
        else:
            for func_name, func_config in functions.items():
                if not isinstance(func_config, dict):
                    errors.append(f"Function '{func_name}' config must be a dictionary")
                    continue
                
                # Check function structure
                if 'enabled' not in func_config:
                    errors.append(f"Function '{func_name}' missing 'enabled' field")
                
                if 'kwargs' not in func_config:
                    errors.append(f"Function '{func_name}' missing 'kwargs' field")
                elif not isinstance(func_config['kwargs'], dict):
                    errors.append(f"Function '{func_name}' kwargs must be a dictionary")
        
        return errors
    
    def _validate_against_template(self, config: Dict[str, Any], extends_path: str, module_name: str) -> List[str]:
        """Validate configuration against its template."""
        errors = []
        
        # Find template file
        template_path = Path(extends_path)
        if not template_path.exists():
            template_path = self.templates_dir.parent / extends_path
        
        if not template_path.exists():
            return [f"Referenced template not found: {extends_path}"]
        
        try:
            with template_path.open() as f:
                template = yaml.safe_load(f)
        except Exception as e:
            return [f"Error loading template {extends_path}: {e}"]
        
        # Check module name matches
        template_module = template.get('module')
        if template_module != module_name:
            errors.append(f"Module name mismatch: config uses '{module_name}', template is '{template_module}'")
        
        # Validate functions exist in template
        config_functions = config.get('functions', {})
        template_functions = template.get('available_functions', {})
        
        for func_name in config_functions:
            if func_name not in template_functions:
                errors.append(f"Function '{func_name}' not found in template")
            else:
                # Validate function parameters
                func_errors = self._validate_function_parameters(
                    func_name, 
                    config_functions[func_name], 
                    template_functions[func_name]
                )
                errors.extend(func_errors)
        
        return errors
    
    def _validate_function_parameters(self, 
                                    func_name: str, 
                                    func_config: Dict[str, Any], 
                                    func_template: Dict[str, Any]) -> List[str]:
        """Validate function parameters against template."""
        errors = []
        
        config_kwargs = func_config.get('kwargs', {})
        template_params = func_template.get('parameters', {})
        
        # Check required parameters are provided
        for param_name, param_info in template_params.items():
            if param_info.get('required', False):
                if param_name not in config_kwargs:
                    errors.append(f"Function '{func_name}' missing required parameter: {param_name}")
                elif isinstance(config_kwargs[param_name], str) and config_kwargs[param_name].startswith('#'):
                    errors.append(f"Function '{func_name}' parameter '{param_name}' is not configured (still has comment)")
        
        # Check for unknown parameters
        for param_name in config_kwargs:
            if param_name not in template_params and not isinstance(config_kwargs[param_name], str):
                # Allow comment parameters (starting with #)
                if not (isinstance(config_kwargs[param_name], str) and config_kwargs[param_name].startswith('#')):
                    errors.append(f"Function '{func_name}' has unknown parameter: {param_name}")
        
        return errors
    
    def _validate_against_pypath(self, config: Dict[str, Any], module_name: str) -> List[str]:
        """Validate configuration against actual pypath methods."""
        errors = []
        
        functions = config.get('functions', {})
        
        for func_name in functions:
            method_name = f"{module_name}.{func_name}"
            method_info = self.adapter.get_method_info(method_name)
            
            if not method_info:
                errors.append(f"Function '{func_name}' not found in pypath module '{module_name}'")
        
        return errors
    
    def validate_database(self, database_name: str) -> Tuple[int, int, Dict[str, List[str]]]:
        """
        Validate all modules for a database.
        
        Returns:
            Tuple of (passed_count, total_count, errors_by_module)
        """
        self.logger.debug(f"Validating all modules for database: {database_name}")
        
        db_dir = self.databases_dir / database_name / "bronze" / "configs"
        if not db_dir.exists():
            self.logger.error(f"Database directory not found: {db_dir}")
            return 0, 0, {}
        
        results = {}
        passed = 0
        total = 0
        
        for config_file in db_dir.glob("*.yaml"):
            module_name = config_file.stem
            total += 1
            
            is_valid, errors = self.validate_database_module(database_name, module_name)
            
            if is_valid:
                passed += 1
            else:
                results[module_name] = errors
        
        self.logger.debug(f"Database {database_name}: {passed}/{total} modules passed validation")
        return passed, total, results