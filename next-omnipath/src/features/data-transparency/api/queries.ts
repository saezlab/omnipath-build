"use server";

import { db } from '@/db';
import { sql } from 'drizzle-orm';

export interface DataSource {
  source_code: string;
  name: string;
  category: string;
  description: string;
  active: boolean;
  license: string;
  citation: string;
  website: string;
}

/**
 * Get all data sources, optionally filtered by category
 */
async function getDataSources(category?: string): Promise<DataSource[]> {
  try {
    // Query the metadata.data_sources table
    const query = sql<DataSource>`
      SELECT 
        source_code,
        name,
        category,
        description,
        active,
        license,
        citation,
        website
      FROM metadata.data_sources
      ${category ? sql`WHERE category = ${category}` : sql``}
      ORDER BY name
    `;

    const result = await db.execute(query);
    return result.rows as unknown as DataSource[];
  } catch (error) {
    console.error('Error fetching data sources:', error);
    throw error;
  }
}

export async function fetchDataSource(sourceId: string): Promise<DataSource | null> {
  try {
    // Fetch all data sources and find the one we need
    const sources = await getDataSources();
    const foundSource = sources.find((s) => s.source_code === sourceId);
    
    return foundSource || null;
  } catch (error) {
    console.error('Error fetching source:', error);
    return null;
  }
}

export async function fetchAllDataSources(category?: string): Promise<DataSource[]> {
  try {
    return await getDataSources(category);
  } catch (error) {
    console.error('Error fetching data sources:', error);
    return [];
  }
}

// Export response types
export type FetchDataSourceResponse = Awaited<ReturnType<typeof fetchDataSource>>;
export type FetchAllDataSourcesResponse = Awaited<ReturnType<typeof fetchAllDataSources>>;