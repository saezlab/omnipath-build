"use server";

import { MeilisearchFilters, MeilisearchSearchResponse } from '@/types/meilisearch';
import { searchInteractionsMeilisearch, fetchMeilisearchDocuments } from '@/lib/meilisearch/search';
import { INDEXES } from '@/lib/meilisearch/client';

export interface EntityInfo {
  id: string;
  canonical_identifier: string;
  display_name: string;
  entity_type_name?: string;
  gene_symbol?: string;
}

export async function searchInteractions(
  query: string,
  filters: MeilisearchFilters,
  limit: number = 20,
  offset: number = 0
): Promise<MeilisearchSearchResponse> {
  try {
    const result = await searchInteractionsMeilisearch({
      query,
      limit,
      offset,
      index: "search_interactions",
      filters,
    });

    return {
      hits: (result.hits as MeilisearchSearchResponse['hits']) || [],
      estimatedTotalHits: (result.estimatedTotalHits as number) || 0,
      limit,
      offset,
      processingTimeMs: (result.processingTimeMs as number) || 0,
      query,
      facetDistribution: result.facetDistribution as Record<string, Record<string, number>> | undefined,
    };
  } catch (error) {
    console.error('Error searching interactions:', error);
    return {
      hits: [],
      estimatedTotalHits: 0,
      limit,
      offset,
      processingTimeMs: 0,
      query,
    };
  }
}

// Helper to check if entity type is a small molecule or lipid
function isSmallMoleculeType(entityTypeName: string | undefined): boolean {
  if (!entityTypeName) return false;
  const type = entityTypeName.toLowerCase();
  return type === 'smallmolecule' ||
    type === 'small_molecule' ||
    type === 'compound' ||
    type === 'metabolite' ||
    type === 'drug' ||
    type === 'lipid';
}

// Helper to get shortest valid name for small molecules
function getShortestName(names: string[] | undefined): string | undefined {
  if (!names || names.length === 0) return undefined;

  // Filter out ID-like names
  const validNames = names.filter(name =>
    !/^(MLS|SMR|cid_|ZINC|SID_|CID_)/i.test(name) && name.length > 3
  );

  if (validNames.length > 0) {
    return validNames.reduce((shortest, current) =>
      current.length < shortest.length ? current : shortest
    );
  }

  return names[0];
}

/**
 * Fetch entities by their IDs
 */
export async function fetchEntitiesByIds(entityIds: number[]): Promise<Map<number, EntityInfo>> {
  if (entityIds.length === 0) {
    return new Map();
  }

  try {
    const uniqueIds = [...new Set(entityIds)];
    const stringIds = uniqueIds.map(id => id.toString());

    const data = await fetchMeilisearchDocuments(INDEXES.ENTITIES, stringIds, 'entity_id');
    const entityMap = new Map<number, EntityInfo>();

    for (const doc of data.documents) {
      const id = Number(doc.entity_id);
      const names = doc.names as string[] | undefined;
      const geneSymbols = doc.gene_symbols as string[] | undefined;
      const entityType = doc.entity_type as string | undefined;
      // entity_type format is "TypeLabel:id", extract just the label
      const entityTypeName = entityType?.split(':')[0];

      // For small molecules/lipids, prefer shortest name; for others use gene symbol or first name
      let displayName: string;
      if (isSmallMoleculeType(entityTypeName)) {
        displayName = getShortestName(names) || String(doc.entity_id);
      } else {
        displayName = geneSymbols?.[0] || names?.[0] || String(doc.entity_id);
      }

      entityMap.set(id, {
        id: String(doc.entity_id),
        canonical_identifier: names?.[0] || String(doc.entity_id),
        display_name: displayName,
        entity_type_name: entityTypeName,
        gene_symbol: geneSymbols?.[0],
      });
    }

    return entityMap;
  } catch (error) {
    console.error('Error fetching entities by IDs:', error);
    return new Map();
  }
}

export type SearchInteractionsResponse = Awaited<ReturnType<typeof searchInteractions>>;
export type FetchEntitiesByIdsResponse = Awaited<ReturnType<typeof fetchEntitiesByIds>>;
