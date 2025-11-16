"use server";

import { db } from '@/db';
import { sql } from 'drizzle-orm';
import { DataProcessingConfig, FieldMapping } from '../types';

export interface TransformationPreviewResult {
  success: boolean;
  transformedData?: Record<string, unknown>[];
  sourceData?: Record<string, unknown>[];
  errors?: string[];
  warnings?: string[];
  totalRows?: number;
}


/**
 * Build SQL SELECT expression for a field mapping
 */
function buildSelectExpression(fieldName: string, mapping: FieldMapping, availableColumns: string[]): string {
  if (mapping.constantValue !== undefined) {
    // Handle constant values
    return `'${mapping.constantValue.replace(/'/g, "''")}' AS "${fieldName}"`;
  }
  
  if (!mapping.sourceColumn) {
    return `NULL AS "${fieldName}"`;
  }
  
  // Check if source column exists
  if (!availableColumns.includes(mapping.sourceColumn)) {
    console.warn(`Column '${mapping.sourceColumn}' not found, using NULL`);
    return `NULL AS "${fieldName}"`;
  }
  
  let expression = `"${mapping.sourceColumn}"`;
  
  // Apply transformation if specified
  if (mapping.transform) {
    if (mapping.transformArgs) {
      // Build function call with arguments
      const args = [expression];
      
      for (const [, argValue] of Object.entries(mapping.transformArgs)) {
        if (typeof argValue === 'string') {
          args.push(`'${argValue.replace(/'/g, "''")}'`);
        } else if (typeof argValue === 'number') {
          args.push(argValue.toString());
        } else if (typeof argValue === 'boolean') {
          args.push(argValue ? 'TRUE' : 'FALSE');
        } else {
          args.push(`'${String(argValue).replace(/'/g, "''")}'`);
        }
      }
      
      expression = `${mapping.transform}(${args.join(', ')})`;
    } else {
      // No additional arguments
      expression = `${mapping.transform}(${expression})`;
    }
  }
  
  return `${expression} AS "${fieldName}"`;
}

/**
 * Preview transformation of source data using mapping configuration
 */
export async function previewTransformation(
  datasourceId: string,
  _datasetName: string,
  mappingConfig: DataProcessingConfig
): Promise<TransformationPreviewResult> {
  try {
    // Use the bronze schema with the correct table naming pattern from migration
    // Pattern: {resource_id}__{dataset_name} (double underscore)
    const bronzeTableName = `${datasourceId}__${_datasetName}`.replace(/-/g, '_').replace(/ /g, '_');
    const schema = 'bronze';
    
    // First, get table structure to know available columns
    const tableInfoQuery = `
      SELECT column_name 
      FROM information_schema.columns 
      WHERE table_schema = '${schema}' 
      AND table_name = '${bronzeTableName}'
      ORDER BY ordinal_position
    `;
    
    let tableInfo;
    let availableColumns: string[] = [];
    
    try {
      tableInfo = await db.execute(sql.raw(tableInfoQuery));
      availableColumns = (tableInfo.rows as Array<{ column_name: string }>).map(row => row.column_name);
      
      if (availableColumns.length === 0) {
        return {
          success: false,
          errors: [`Could not find bronze table ${schema}.${bronzeTableName} for datasource ${datasourceId}`]
        };
      }
    } catch (error) {
      return {
        success: false,
        errors: [`Could not find bronze table ${schema}.${bronzeTableName} for datasource ${datasourceId}: ${error}`]
      };
    }
    
    // Get total row count
    const countQuery = `SELECT COUNT(*) as count FROM ${schema}.${bronzeTableName}`;
    const countResult = await db.execute(sql.raw(countQuery));
    const totalRows = Number(countResult.rows[0]?.count || 0);
    
    // Fetch sample source data (first 100 rows)
    const sourceDataQuery = `SELECT * FROM ${schema}.${bronzeTableName} LIMIT 100`;
    const sourceDataResult = await db.execute(sql.raw(sourceDataQuery));
    const sourceData = sourceDataResult.rows as Record<string, unknown>[];
    
    if (sourceData.length === 0) {
      return {
        success: false,
        errors: ['No data found in bronze table']
      };
    }
    
    // Build transformation query
    const selectExpressions: string[] = [];
    const mappingEntries = Object.entries(mappingConfig.mappings);
    
    if (mappingEntries.length === 0) {
      return {
        success: false,
        errors: ['No field mappings configured']
      };
    }
    
    for (const [fieldName, mapping] of mappingEntries) {
      const expr = buildSelectExpression(fieldName, mapping, availableColumns);
      selectExpressions.push(expr);
    }
    
    // Create the transformation query using a CTE
    const transformationQuery = `
      WITH source_data AS (
        SELECT * FROM ${schema}.${bronzeTableName} LIMIT 100
      )
      SELECT ${selectExpressions.join(',\n       ')}
      FROM source_data
    `;
    
    console.log('Executing transformation query:', transformationQuery);
    console.log('Mapping config:', JSON.stringify(mappingConfig, null, 2));
    
    // Execute transformation
    const transformationResult = await db.execute(sql.raw(transformationQuery));
    const transformedData = transformationResult.rows as Record<string, unknown>[];
    
    // Validate transformed data and collect warnings
    const warnings: string[] = [];
    const requiredFields = getRequiredFieldsForModel(mappingConfig.targetModel);
    
    transformedData.forEach((row, index) => {
      requiredFields.forEach(field => {
        if (!row.hasOwnProperty(field) || row[field] === null || row[field] === undefined) {
          warnings.push(`Row ${index + 1}: Missing required field '${field}'`);
        }
      });
    });
    
    return {
      success: true,
      transformedData,
      sourceData,
      totalRows,
      warnings: warnings.length > 0 ? warnings : undefined
    };
    
  } catch (error) {
    console.error('Error previewing transformation:', error);
    return {
      success: false,
      errors: [`Transformation preview failed: ${error instanceof Error ? error.message : 'Unknown error'}`]
    };
  }
}

/**
 * Get required fields for a target model
 */
function getRequiredFieldsForModel(model: string): string[] {
  switch (model) {
    case 'interactions':
      return ['entity_a', 'entity_b', 'entity_a_id_type', 'entity_b_id_type', 'data_source'];
    case 'entities':
      return ['canonical_identifier', 'canonical_identifier_type', 'entity_type'];
    case 'controlled_vocabularies':
      return ['term_id', 'term_name', 'namespace'];
    default:
      return [];
  }
}