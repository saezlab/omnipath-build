"use server";

import { db } from '@/db';
import { resourcesInMetadata, datasetsInMetadata } from '../../../../drizzle/schema';
import { eq, ilike, or, and, inArray, sql } from 'drizzle-orm';
import { DataSource, Dataset, DataSourceFilters, LICENSE_TYPES } from '../types/datasource';

// Cache for parsed datasources
let cachedDatasources: DataSource[] | null = null;
let cacheTimestamp: number = 0;
const CACHE_DURATION = 5 * 60 * 1000; // 5 minutes

/**
 * Get all datasources from PostgreSQL
 */
export async function getAllDatasources(): Promise<DataSource[]> {
  // Check cache
  if (cachedDatasources && Date.now() - cacheTimestamp < CACHE_DURATION) {
    return cachedDatasources;
  }

  try {
    // Fetch all resources with their datasets
    const resources = await db
      .select()
      .from(resourcesInMetadata)
      .orderBy(resourcesInMetadata.name);

    const datasets = await db
      .select()
      .from(datasetsInMetadata);

    // Group datasets by resource ID
    const datasetsByResourceId = datasets.reduce((acc, dataset) => {
      if (!dataset.resourceId) return acc;
      
      if (!acc[dataset.resourceId]) {
        acc[dataset.resourceId] = [];
      }
      
      const mappedDataset: Dataset = {
        name: dataset.name || 'unnamed',
        entityType: (dataset.entityType as Dataset['entityType']) || 'protein',
        category: (dataset.category as Dataset['category']) || 'annotation',
        types: Array.isArray(dataset.types) ? dataset.types as string[] : [],
        evidenceLevel: (dataset.evidenceLevel as Dataset['evidenceLevel']) || 'literature_curated',
        taxonScope: (dataset.taxonScope as Dataset['taxonScope']) || 'multi-species',
        download: dataset.download as Dataset['download'],
        dataProcessing: dataset.dataProcessing as Dataset['dataProcessing']
      };
      
      acc[dataset.resourceId].push(mappedDataset);
      return acc;
    }, {} as Record<string, Dataset[]>);

    // Map resources to DataSource format
    const datasources: DataSource[] = resources.map(resource => ({
      id: resource.id || '',
      name: resource.name || '',
      description: resource.description || '',
      license: resource.license || 'Unknown',
      primaryPubmed: resource.primaryPubmed || undefined,
      health: (resource.health as DataSource['health']) || 'success',
      website: resource.website || '',
      updateCategory: (resource.updateCategory as DataSource['updateCategory']) || 'infrequent',
      accessCategory: (resource.accessCategory as DataSource['accessCategory']) || 'file_download',
      datasets: datasetsByResourceId[resource.id || ''] || []
    }));

    // Update cache
    cachedDatasources = datasources;
    cacheTimestamp = Date.now();
    
    return datasources;
  } catch (error) {
    console.error('Error reading datasources from database:', error);
    return [];
  }
}

/**
 * Get a single datasource by ID
 */
export async function getDatasourceById(id: string): Promise<DataSource | null> {
  try {
    // Fetch resource by ID
    const resources = await db
      .select()
      .from(resourcesInMetadata)
      .where(eq(resourcesInMetadata.id, id))
      .limit(1);

    if (resources.length === 0) {
      return null;
    }

    const resource = resources[0];

    // Fetch datasets for this resource
    const datasets = await db
      .select()
      .from(datasetsInMetadata)
      .where(eq(datasetsInMetadata.resourceId, id));

    // Map datasets
    const mappedDatasets: Dataset[] = datasets.map(dataset => ({
      name: dataset.name || 'unnamed',
      entityType: (dataset.entityType as Dataset['entityType']) || 'protein',
      category: (dataset.category as Dataset['category']) || 'annotation',
      types: Array.isArray(dataset.types) ? dataset.types as string[] : [],
      evidenceLevel: (dataset.evidenceLevel as Dataset['evidenceLevel']) || 'literature_curated',
      taxonScope: (dataset.taxonScope as Dataset['taxonScope']) || 'multi-species',
      download: dataset.download as Dataset['download'],
      dataProcessing: dataset.dataProcessing as Dataset['dataProcessing']
    }));

    // Return mapped datasource
    return {
      id: resource.id || '',
      name: resource.name || '',
      description: resource.description || '',
      license: resource.license || 'Unknown',
      primaryPubmed: resource.primaryPubmed || undefined,
      health: (resource.health as DataSource['health']) || 'success',
      website: resource.website || '',
      updateCategory: (resource.updateCategory as DataSource['updateCategory']) || 'infrequent',
      accessCategory: (resource.accessCategory as DataSource['accessCategory']) || 'file_download',
      datasets: mappedDatasets
    };
  } catch (error) {
    console.error('Error fetching datasource by ID:', error);
    return null;
  }
}

/**
 * Categorize license type
 */
function categorizeLicense(license: string): string {
  const normalizedLicense = license.toLowerCase();
  
  for (const licenseType of LICENSE_TYPES) {
    if (licenseType.regex.test(normalizedLicense)) {
      return licenseType.value;
    }
  }
  
  return 'custom';
}

/**
 * Filter datasources based on filters
 */
export async function filterDatasources(filters: DataSourceFilters): Promise<DataSource[]> {
  try {
    // Build where conditions for resources
    const resourceConditions = [];
    
    // Search filter - apply to resources
    if (filters.search) {
      const searchPattern = `%${filters.search}%`;
      resourceConditions.push(
        or(
          ilike(resourcesInMetadata.name, searchPattern),
          ilike(resourcesInMetadata.description, searchPattern),
          ilike(resourcesInMetadata.id, searchPattern)
        )
      );
    }
    
    // Update category filter
    if (filters.updateCategories && filters.updateCategories.length > 0) {
      resourceConditions.push(
        inArray(resourcesInMetadata.updateCategory, filters.updateCategories)
      );
    }
    
    // Access category filter
    if (filters.accessCategories && filters.accessCategories.length > 0) {
      resourceConditions.push(
        inArray(resourcesInMetadata.accessCategory, filters.accessCategories)
      );
    }
    
    // Health status filter
    if (filters.healthStatuses && filters.healthStatuses.length > 0) {
      resourceConditions.push(
        inArray(resourcesInMetadata.health, filters.healthStatuses)
      );
    }
    
    // Fetch resources with conditions
    const resources = await db
      .select()
      .from(resourcesInMetadata)
      .where(resourceConditions.length > 0 ? and(...resourceConditions) : undefined)
      .orderBy(resourcesInMetadata.name);
    
    // Get all datasets
    const datasets = await db
      .select()
      .from(datasetsInMetadata);
    
    // Group datasets by resource ID and filter
    const datasetsByResourceId = datasets.reduce((acc, dataset) => {
      if (!dataset.resourceId) return acc;
      
      if (!acc[dataset.resourceId]) {
        acc[dataset.resourceId] = [];
      }
      
      const mappedDataset: Dataset = {
        name: dataset.name || 'unnamed',
        entityType: (dataset.entityType as Dataset['entityType']) || 'protein',
        category: (dataset.category as Dataset['category']) || 'annotation',
        types: Array.isArray(dataset.types) ? dataset.types as string[] : [],
        evidenceLevel: (dataset.evidenceLevel as Dataset['evidenceLevel']) || 'literature_curated',
        taxonScope: (dataset.taxonScope as Dataset['taxonScope']) || 'multi-species',
        download: dataset.download as Dataset['download'],
        dataProcessing: dataset.dataProcessing as Dataset['dataProcessing']
      };
      
      acc[dataset.resourceId].push(mappedDataset);
      return acc;
    }, {} as Record<string, Dataset[]>);
    
    // Map resources to DataSource format and apply remaining filters
    let datasources: DataSource[] = resources.map(resource => ({
      id: resource.id || '',
      name: resource.name || '',
      description: resource.description || '',
      license: resource.license || 'Unknown',
      primaryPubmed: resource.primaryPubmed || undefined,
      health: (resource.health as DataSource['health']) || 'success',
      website: resource.website || '',
      updateCategory: (resource.updateCategory as DataSource['updateCategory']) || 'infrequent',
      accessCategory: (resource.accessCategory as DataSource['accessCategory']) || 'file_download',
      datasets: datasetsByResourceId[resource.id || ''] || []
    }));
    
    // Apply dataset-level filters
    if (filters.categories && filters.categories.length > 0) {
      datasources = datasources.filter(ds =>
        ds.datasets.some(dataset => filters.categories!.includes(dataset.category))
      );
    }
    
    if (filters.entityTypes && filters.entityTypes.length > 0) {
      datasources = datasources.filter(ds =>
        ds.datasets.some(dataset => filters.entityTypes!.includes(dataset.entityType))
      );
    }
    
    if (filters.evidenceLevels && filters.evidenceLevels.length > 0) {
      datasources = datasources.filter(ds =>
        ds.datasets.some(dataset => filters.evidenceLevels!.includes(dataset.evidenceLevel))
      );
    }
    
    if (filters.taxonScopes && filters.taxonScopes.length > 0) {
      datasources = datasources.filter(ds =>
        ds.datasets.some(dataset => filters.taxonScopes!.includes(dataset.taxonScope))
      );
    }
    
    // License type filter
    if (filters.licenseTypes && filters.licenseTypes.length > 0) {
      datasources = datasources.filter(ds =>
        filters.licenseTypes!.includes(categorizeLicense(ds.license))
      );
    }
    
    // Type filters - filter by specific types within each category
    if (filters.interactionTypes && filters.interactionTypes.length > 0) {
      datasources = datasources.filter(ds =>
        ds.datasets.some(dataset => 
          dataset.category === 'interaction' && 
          dataset.types.some(type => filters.interactionTypes!.includes(type))
        )
      );
    }
    
    if (filters.annotationTypes && filters.annotationTypes.length > 0) {
      datasources = datasources.filter(ds =>
        ds.datasets.some(dataset => 
          dataset.category === 'annotation' && 
          dataset.types.some(type => filters.annotationTypes!.includes(type))
        )
      );
    }
    
    if (filters.ontologyTypes && filters.ontologyTypes.length > 0) {
      datasources = datasources.filter(ds =>
        ds.datasets.some(dataset => 
          dataset.category === 'ontology' && 
          dataset.types.some(type => filters.ontologyTypes!.includes(type))
        )
      );
    }
    
    return datasources;
  } catch (error) {
    console.error('Error filtering datasources:', error);
    return [];
  }
}

/**
 * Get aggregated stats for all datasources
 */
export async function getDatasourceStats() {
  try {
    // Get total resources count
    const totalCount = await db
      .select({ count: sql<number>`count(*)::int` })
      .from(resourcesInMetadata);
    
    // Get health stats
    const healthStats = await db
      .select({
        health: resourcesInMetadata.health,
        count: sql<number>`count(*)::int`
      })
      .from(resourcesInMetadata)
      .groupBy(resourcesInMetadata.health);
    
    // Get update category stats
    const updateCategoryStats = await db
      .select({
        updateCategory: resourcesInMetadata.updateCategory,
        count: sql<number>`count(*)::int`
      })
      .from(resourcesInMetadata)
      .groupBy(resourcesInMetadata.updateCategory);
    
    // Get dataset category stats
    const categoryStats = await db
      .select({
        category: datasetsInMetadata.category,
        count: sql<number>`count(*)::int`
      })
      .from(datasetsInMetadata)
      .groupBy(datasetsInMetadata.category);
    
    return {
      total: totalCount[0]?.count || 0,
      byCategory: categoryStats.reduce((acc, stat) => {
        if (stat.category) {
          acc[stat.category] = stat.count;
        }
        return acc;
      }, {} as Record<string, number>),
      byHealth: healthStats.reduce((acc, stat) => {
        if (stat.health) {
          acc[stat.health] = stat.count;
        }
        return acc;
      }, {} as Record<string, number>),
      byUpdateCategory: updateCategoryStats.reduce((acc, stat) => {
        if (stat.updateCategory) {
          acc[stat.updateCategory] = stat.count;
        }
        return acc;
      }, {} as Record<string, number>)
    };
  } catch (error) {
    console.error('Error getting datasource stats:', error);
    return {
      total: 0,
      byCategory: {},
      byHealth: {},
      byUpdateCategory: {}
    };
  }
}

/**
 * Get sample data from bronze tables for a datasource
 */
export interface BronzeTableSample {
  datasetName: string;
  tableName: string;
  rows: Record<string, unknown>[];
  columns: string[];
  totalRows: number;
}

export async function getBronzeTableSamples(datasourceId: string): Promise<BronzeTableSample[]> {
  try {
    // Get the datasource to find its datasets
    const datasource = await getDatasourceById(datasourceId);
    if (!datasource) {
      return [];
    }

    const samples: BronzeTableSample[] = [];
    const schema = 'bronze';

    for (const dataset of datasource.datasets) {
      try {
        // Use the new bronze table naming pattern: {resource_id}__{dataset_name}
        const bronzeTableName = `${datasourceId}__${dataset.name}`.replace(/-/g, '_').replace(/ /g, '_');

        // Query sample data using raw SQL since we need dynamic table names
        const sampleQuery = `
          SELECT * FROM ${schema}.${bronzeTableName} 
          LIMIT 100
        `;
        
        const sampleData = await db.execute(sql.raw(sampleQuery)) as { rows: Record<string, unknown>[] };
        
        // Get total row count
        const countQuery = `
          SELECT COUNT(*) as total FROM ${schema}.${bronzeTableName}
        `;
        const countResult = await db.execute(sql.raw(countQuery)) as { rows: { total?: number }[] };
        const totalRows = countResult.rows[0]?.total || 0;

        if (sampleData.rows && sampleData.rows.length > 0) {
          // Extract column names from the first row
          const columns = Object.keys(sampleData.rows[0]);
          
          // Convert rows to proper format
          const rows = sampleData.rows.map(row => {
            const formattedRow: Record<string, unknown> = {};
            columns.forEach(col => {
              formattedRow[col] = row[col];
            });
            return formattedRow;
          });

          samples.push({
            datasetName: dataset.name,
            tableName: `${schema}.${bronzeTableName}`,
            rows,
            columns,
            totalRows: Number(totalRows)
          });
        }
      } catch (tableError) {
        // If table doesn't exist for this dataset, skip it
        console.warn(`Bronze table not found for dataset ${dataset.name}: ${tableError}`);
        continue;
      }
    }

    return samples;
  } catch (error) {
    console.error('Error fetching bronze table samples:', error);
    return [];
  }
}