"use server";

import { fetchMeilisearchDocuments, searchMeilisearch } from "@/lib/meilisearch/search";
import { db } from '@/db';
import { sql } from 'drizzle-orm';
import { INDEXES } from '@/lib/meilisearch/client';

interface OntologyNode {
  id: string;
  name: string;
  namespace?: string;
  definition?: string;
  synonyms?: string[];
  is_annotated?: boolean;
  is_namespace?: boolean;
  children?: OntologyNode[];
  associated_entity_ids?: string[];
}

interface EntityData {
  id: string;
  type: string;
  canonical_identifier: string;
  entity_type_name?: string;
  description?: string;
  display_name?: string;
  ontology_tree?: OntologyNode | OntologyNode[];
  cvTermIds?: string[];
  cv_term_ids?: string[];
  [key: string]: unknown;
}

export async function fetchEntity(id: string): Promise<EntityData | null> {
  try {
    const data = await fetchMeilisearchDocuments('entities', [id]);
    const documents = data.documents as unknown[];
    return documents.length > 0 ? documents[0] as EntityData : null;
  } catch (error) {
    console.error('Error fetching entity:', error);
    return null;
  }
}

export async function fetchAssociatedEntities(entityId: string): Promise<EntityData[]> {
  try {
    // Query entity memberships directly from the database
    const membershipQuery = sql`
      SELECT 
        e.id,
        e.canonical_identifier,
        e.description,
        et.name as entity_type_name
      FROM gold.entity_membership em
      JOIN gold.entity e ON em.member_entity_id = e.id
      LEFT JOIN gold.cv_term et ON e.entity_type_id = et.id
      WHERE em.parent_entity_id = ${parseInt(entityId)}
    `;
    
    const result = await db.execute(membershipQuery);
    const memberIds = result.rows.map((row: Record<string, unknown>) => (row.id as number).toString());
    
    if (memberIds.length === 0) {
      return [];
    }
    
    // Fetch the full entity data from Meilisearch for better display
    try {
      const msData = await fetchMeilisearchDocuments('entities', memberIds);
      return msData.documents as EntityData[] || [];
    } catch (error) {
      console.error(`Failed to fetch member details from Meilisearch:`, error);
      // Fall back to using the database data directly
      return result.rows.map((row: Record<string, unknown>) => ({
        id: (row.id as number).toString(),
        type: 'entity',
        canonical_identifier: row.canonical_identifier as string,
        entity_type_name: (row.entity_type_name as string) || 'Unknown',
        description: row.description as string,
        display_name: row.canonical_identifier as string
      })) as EntityData[];
    }
  } catch (error) {
    console.error('Error fetching associated entities:', error);
    return [];
  }
}

// Add searchEntities function for general entity search
export async function searchEntities(params: {
  search?: string;
  limit?: number;
  offset?: number;
}): Promise<{ entities: EntityData[]; total: number }> {
  try {
    const {
      search = '',
      limit = 20,
      offset = 0,
    } = params;

    // Use Meilisearch for search
    const searchResult = await searchMeilisearch({
      query: search,
      index: INDEXES.ENTITIES,
      limit,
      offset,
    });

    // Transform Meilisearch results to match our entity types
    const entities = searchResult.hits.map((hit: Record<string, unknown>) => ({
      id: String(hit.id),
      type: String(hit.type || 'entity'),
      canonical_identifier: String(hit.canonical_identifier),
      entity_type_name: hit.entity_type_name as string,
      description: String(hit.description || ''),
      display_name: String(hit.gene_symbol || hit.canonical_identifier),
      cvTermIds: (hit.cv_term_ids as string[]) || [],
      cv_term_ids: (hit.cv_term_ids as string[]) || [],
    }));

    return {
      entities,
      total: searchResult.estimatedTotalHits || 0,
    };
  } catch (error) {
    console.error('Error searching entities:', error);
    throw error;
  }
}

// Export response types
export type FetchEntityResponse = Awaited<ReturnType<typeof fetchEntity>>;
export type FetchAssociatedEntitiesResponse = Awaited<ReturnType<typeof fetchAssociatedEntities>>;
export type SearchEntitiesResponse = Awaited<ReturnType<typeof searchEntities>>;