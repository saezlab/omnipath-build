'use server';

import { meilisearchClient, INDEXES, type IndexName } from './client';
import type { MeilisearchFilters } from '@/types/meilisearch';

// Re-export INDEXES for other modules
//export { INDEXES } from './client';

export interface SearchParams {
  query: string;
  index: IndexName;
  limit?: number;
  offset?: number;
  filters?: MeilisearchFilters;
}

export interface SearchResponse {
  hits: Record<string, unknown>[];
  estimatedTotalHits: number;
  limit: number;
  offset: number;
  processingTimeMs: number;
  query: string;
  facetDistribution?: Record<string, Record<string, number>>;
}

/**
 * Search entities or CV terms
 */
export async function searchMeilisearch(params: SearchParams): Promise<SearchResponse> {
  const {
    query,
    index,
    limit = 20,
    offset = 0,
  } = params;

  try {
    const indexClient = meilisearchClient.index(index);
    const result = await indexClient.search(query, {
      limit,
      offset,
      attributesToHighlight: ["*"],
    });

    return {
      hits: result.hits,
      estimatedTotalHits: result.estimatedTotalHits || 0,
      limit,
      offset,
      processingTimeMs: result.processingTimeMs,
      query,
      facetDistribution: result.facetDistribution,
    };
  } catch (error) {
    console.error('Meilisearch error:', error);
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

/**
 * Build filter string for Meilisearch interactions
 */
function buildMeilisearchFilterString(filters: MeilisearchFilters): string {
  const filterParts: string[] = [];

  // Helper to build filter for facet fields
  const buildFacetFilter = (fieldName: string, values: string[]) => {
    const filters = values.map(value => `${fieldName}_facet = "${value}"`).join(' OR ');
    return `(${filters})`;
  };

  // Add each filter type
  if (filters.interaction_types?.length) {
    filterParts.push(buildFacetFilter('interaction_types', filters.interaction_types));
  }

  if (filters.data_sources?.length) {
    filterParts.push(buildFacetFilter('data_sources', filters.data_sources));
  }

  if (filters.detection_methods?.length) {
    filterParts.push(buildFacetFilter('detection_methods', filters.detection_methods));
  }

  if (filters.causal_statements?.length) {
    filterParts.push(buildFacetFilter('causal_statements', filters.causal_statements));
  }

  if (filters.causal_mechanisms?.length) {
    filterParts.push(buildFacetFilter('causal_mechanisms', filters.causal_mechanisms));
  }

  if (filters.interactor_types?.length) {
    filterParts.push(buildFacetFilter('interactor_types', filters.interactor_types));
  }

  if (filters.signs?.length) {
    const signFilters = filters.signs.map(sign => `signs = "${sign}"`).join(' OR ');
    filterParts.push(`(${signFilters})`);
  }

  if (filters.consensus_sign !== undefined && filters.consensus_sign !== null) {
    filterParts.push(`consensus_sign = "${filters.consensus_sign}"`);
  }

  if (filters.is_directed !== undefined && filters.is_directed !== null) {
    filterParts.push(`is_directed = ${filters.is_directed}`);
  }

  if (filters.consensus_direction !== undefined && filters.consensus_direction !== null) {
    filterParts.push(`consensus_direction = "${filters.consensus_direction}"`);
  }

  if (filters.evidence_count_min !== undefined) {
    filterParts.push(`evidence_count >= ${filters.evidence_count_min}`);
  }

  if (filters.evidence_count_max !== undefined) {
    filterParts.push(`evidence_count <= ${filters.evidence_count_max}`);
  }

  // Add entity ID filter
  if (filters.entity_ids?.length) {
    const entityFilters = filters.entity_ids.map(id => `entity_ids = "${id}"`).join(' OR ');
    filterParts.push(`(${entityFilters})`);
  }

  return filterParts.join(' AND ');
}

/**
 * Search interactions with filters
 */
export async function searchInteractionsMeilisearch(
  params: SearchParams & { filters?: MeilisearchFilters }
): Promise<SearchResponse> {
  const {
    query,
    limit = 20,
    offset = 0,
    filters = {},
  } = params;

  try {
    const indexClient = meilisearchClient.index(INDEXES.INTERACTIONS);
    const filterString = buildMeilisearchFilterString(filters);

    const searchOptions: Record<string, unknown> = {
      limit,
      offset,
      facets: [
        'interaction_types_facet',
        'data_sources_facet',
        'detection_methods_facet',
        'causal_statements_facet',
        'causal_mechanisms_facet',
        'interactor_types_facet',
        'signs',
        'consensus_sign',
        'is_directed',
        'consensus_direction',
        'evidence_count',
      ],
    };

    if (filterString) {
      searchOptions.filter = filterString;
    }

    const result = await indexClient.search(query, searchOptions);

    return {
      hits: result.hits,
      estimatedTotalHits: result.estimatedTotalHits || 0,
      limit,
      offset,
      processingTimeMs: result.processingTimeMs,
      query,
      facetDistribution: result.facetDistribution,
    };
  } catch (error) {
    console.error('Meilisearch interactions search error:', error);
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

/**
 * Fetch documents by IDs
 */
export async function fetchMeilisearchDocuments(
  indexName: IndexName,
  documentIds: string[],
): Promise<{ documents: Record<string, unknown>[] }> {
  try {
    const indexClient = meilisearchClient.index(indexName);
    const documents = await indexClient.getDocuments({
      filter: documentIds.map(id => `id = "${id}"`).join(' OR '),
      limit: documentIds.length > 0 ? Math.max(documentIds.length, 1000) : 20,
    });

    return {
      documents: documents.results,
    };
  } catch (error) {
    console.error('Meilisearch fetch documents error:', error);
    return {
      documents: [],
    };
  }
}

/**
 * Fetch CV terms by IDs
 */
export async function fetchCvTermsByIds(
  termIds: string[]
): Promise<{ documents: Record<string, unknown>[] }> {
  return fetchMeilisearchDocuments('cv_terms', termIds);
}

/**
 * Get interaction statistics
 */
export async function getInteractionStats(): Promise<Record<string, unknown>> {
  try {
    const indexClient = meilisearchClient.index(INDEXES.INTERACTIONS);
    const stats = await indexClient.getStats();
    return stats;
  } catch (error) {
    console.error('Meilisearch stats error:', error);
    return {};
  }
}