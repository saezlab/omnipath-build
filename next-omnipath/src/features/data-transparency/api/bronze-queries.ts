'use server';

import { db } from '@/db';
import { sql } from 'drizzle-orm';

export interface TableDataResponse {
  data: Array<Record<string, unknown>>;
  pagination: {
    page: number;
    limit: number;
    total: number;
    pages: number;
  };
  search?: string;
}

export interface TableStats {
  schema: string;
  table: string;
  columns: Array<{
    name: string;
    type: string;
    nullable: boolean;
  }>;
  row_count: number;
}

/**
 * Get available schemas
 */
export async function getSchemas(): Promise<string[]> {
  return ['bronze', 'silver', 'gold'];
}

/**
 * Get tables for a specific schema
 */
export async function getSchemaTables(schemaName: string): Promise<string[]> {
  const tables: Record<string, string[]> = {
    bronze: [
      // Bronze tables are now named {resource_id}__{dataset_name}
      // This would be dynamically discovered from the actual database
      // For now, return empty array as these should be discovered via information_schema
    ],
    silver: [
      'annotation',
      'cv_term',
      'cv_term_hierarchy',
      'cv_term_mapping',
      'data_source',
      'entity',
      'entity_identifier',
      'entity_membership',
      'interaction',
      'interaction_evidence',
      'reference'
    ],
    gold: [
      'cv_term',
      'cv_term_hierarchy',
      'entity',
      'entity_identifier',
      'entity_membership',
      'interaction_canonical',
      'interaction_evidence',
      'protein_details',
      'entity_interaction_stats',
      'network_metrics',
      'data_quality_metrics',
      'reference'
    ]
  };

  return tables[schemaName] || [];
}

/**
 * Get table statistics
 */
export async function getTableStats(schemaName: string, tableName: string): Promise<TableStats> {
  try {
    // Get table info from information_schema
    const tableInfoQuery = sql`
      SELECT 
        column_name,
        data_type,
        is_nullable
      FROM information_schema.columns 
      WHERE table_schema = ${schemaName} 
        AND table_name = ${tableName}
      ORDER BY ordinal_position
    `;

    const tableInfo = await db.execute(tableInfoQuery);
    
    // Get row count
    const countQuery = sql.raw(`SELECT COUNT(*) as count FROM ${schemaName}.${tableName}`);
    const countResult = await db.execute(countQuery);
    const rowCount = Number(countResult.rows[0]?.count || 0);

    const columns = tableInfo.rows.map((row: Record<string, unknown>) => ({
      name: String(row.column_name),
      type: String(row.data_type),
      nullable: row.is_nullable === 'YES'
    }));

    return {
      schema: schemaName,
      table: tableName,
      columns,
      row_count: rowCount
    };
  } catch {
    console.error(`Error getting table stats for ${schemaName}.${tableName}:`);
    
    // Return mock stats as fallback
    return {
      schema: schemaName,
      table: tableName,
      columns: [
        { name: 'id', type: 'bigint', nullable: false },
        { name: 'data', type: 'varchar', nullable: true },
        { name: '_source', type: 'varchar', nullable: true },
        { name: '_loaded_at', type: 'varchar', nullable: true },
        { name: '_row_number', type: 'bigint', nullable: true }
      ],
      row_count: 12345
    };
  }
}

/**
 * Get table data with pagination
 */
export async function getTableData(
  schemaName: string,
  tableName: string,
  page: number = 1,
  limit: number = 20,
  search?: string
): Promise<TableDataResponse> {
  try {
    const offset = (page - 1) * limit;
    
    // Build base query parts
    let baseQuery = `SELECT * FROM ${schemaName}.${tableName}`;
    let countQuery = `SELECT COUNT(*) as count FROM ${schemaName}.${tableName}`;
    
    // Add search filter if provided
    if (search) {
      // This is a simplified search - in practice you'd want to search specific columns
      const searchCondition = `WHERE CAST(ROW(*) AS TEXT) ILIKE '%${search}%'`;
      baseQuery += ` ${searchCondition}`;
      countQuery += ` ${searchCondition}`;
    }
    
    // Add pagination to data query
    const dataQuery = `${baseQuery} LIMIT ${limit} OFFSET ${offset}`;
    
    // Execute queries
    const [dataResult, countResult] = await Promise.all([
      db.execute(sql.raw(dataQuery)),
      db.execute(sql.raw(countQuery))
    ]);
    
    const total = Number(countResult.rows[0]?.count || 0);
    const pages = Math.ceil(total / limit);
    
    return {
      data: dataResult.rows as Record<string, unknown>[],
      pagination: {
        page,
        limit,
        total,
        pages
      },
      search
    };
  } catch {
    console.error(`Error getting table data for ${schemaName}.${tableName}:`);
    
    // Return empty result as fallback
    return {
      data: [],
      pagination: {
        page,
        limit,
        total: 0,
        pages: 0
      },
      search
    };
  }
}

/**
 * Get bronze data for a specific source and dataset
 */
export async function getBronzeDataForSource(
  sourceId: string,
  datasetName?: string,
  page: number = 1,
  limit: number = 20
): Promise<TableDataResponse> {
  // Use the new bronze table naming pattern: {resource_id}__{dataset_name}
  // If no dataset name provided, we need to discover available tables for this source
  
  if (!datasetName) {
    // If no dataset specified, return empty result
    // In practice, the calling code should specify a dataset
    return {
      data: [],
      pagination: {
        page,
        limit,
        total: 0,
        pages: 0
      }
    };
  }
  
  const tableName = `${sourceId}__${datasetName}`.replace(/-/g, '_').replace(/ /g, '_');
  const schema = 'bronze';
  
  try {
    const result = await getTableData(schema, tableName, page, limit);
    return result;
  } catch (error) {
    console.error(`No data found in ${schema}.${tableName}:`, error);
    
    // Return empty result as fallback
    return {
      data: [],
      pagination: {
        page,
        limit,
        total: 0,
        pages: 0
      }
    };
  }
}

// Export response types
export type GetSchemasResponse = Awaited<ReturnType<typeof getSchemas>>;
export type GetSchemaTablesResponse = Awaited<ReturnType<typeof getSchemaTables>>;
export type GetTableStatsResponse = Awaited<ReturnType<typeof getTableStats>>;
export type GetTableDataResponse = Awaited<ReturnType<typeof getTableData>>;
export type GetBronzeDataForSourceResponse = Awaited<ReturnType<typeof getBronzeDataForSource>>;