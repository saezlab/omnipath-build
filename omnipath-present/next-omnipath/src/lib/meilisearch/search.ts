'use server';

import { meilisearchClient, INDEXES, type IndexName } from './client';
import type { MeilisearchFilters } from '@/types/meilisearch';
import { buildEntityFilterString, buildInteractionFilterString } from './filters';

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
    filters = {},
  } = params;

  try {
    const indexClient = meilisearchClient.index(index);
    const searchOptions: Record<string, unknown> = {
      limit,
      offset,
      attributesToHighlight: ["*"],
    };

    // Add filters and facets for entity search
    if (index === INDEXES.ENTITIES) {
      // Always request facets for entity search
      searchOptions.facets = [
        'entity_type',
        'sources',
        'ncbi_tax_id',
        'cv_terms_go',
        'cv_terms_mi',
        'cv_terms_om',
        'cv_terms_hp',
        'cv_terms_kw',
      ];

      // Add filters if present
      if (Object.keys(filters).length > 0) {
        const filterString = buildEntityFilterString(filters);
        if (filterString) {
          searchOptions.filter = filterString;
        }
      }
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
    const filterString = buildInteractionFilterString(filters);

    const searchOptions: Record<string, unknown> = {
      limit,
      offset,
      facets: [
        'member_types',
        'has_direction',
        'has_positive_sign',
        'has_negative_sign',
        'interaction_annotation_terms',
        'sources',
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
  filterField: string = 'id',
): Promise<{ documents: Record<string, unknown>[] }> {
  try {
    const indexClient = meilisearchClient.index(indexName);
    const documents = await indexClient.getDocuments({
      filter: documentIds.map(id => `${filterField} = "${id}"`).join(' OR '),
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

/**
 * Build filter string for Meilisearch associations search
 */
function buildAssociationsFilterString(filters: MeilisearchFilters): string {
  const filterParts: string[] = [];

  // Parent entity IDs filter
  if (filters.parent_entity_ids?.length) {
    const parentFilters = filters.parent_entity_ids.map(id => `parent_entity_id = ${id}`).join(' OR ');
    filterParts.push(`(${parentFilters})`);
  }

  // Member entity IDs filter
  if (filters.member_entity_ids?.length) {
    const memberFilters = filters.member_entity_ids.map(id => `member_entity_id = ${id}`).join(' OR ');
    filterParts.push(`(${memberFilters})`);
  }

  // Parent entity types filter
  if (filters.parent_entity_types?.length) {
    const typeFilters = filters.parent_entity_types.map(type => `parent_entity_type = "${type}"`).join(' OR ');
    filterParts.push(`(${typeFilters})`);
  }

  // Member entity types filter
  if (filters.member_entity_types?.length) {
    const typeFilters = filters.member_entity_types.map(type => `member_entity_type = "${type}"`).join(' OR ');
    filterParts.push(`(${typeFilters})`);
  }

  // Sources filter
  if (filters.sources?.length) {
    const sourceFilters = filters.sources.map(source => `sources = "${source}"`).join(' OR ');
    filterParts.push(`(${sourceFilters})`);
  }

  // Association annotation terms filter
  if (filters.association_annotation_terms?.length) {
    const termFilters = filters.association_annotation_terms.map(term => `association_annotation_terms = "${term}"`).join(' OR ');
    filterParts.push(`(${termFilters})`);
  }

  return filterParts.join(' AND ');
}

/**
 * Search associations with filters
 */
export async function searchAssociationsMeilisearch(
  params: SearchParams & { filters?: MeilisearchFilters }
): Promise<SearchResponse> {
  const {
    query,
    limit = 20,
    offset = 0,
    filters = {},
  } = params;

  try {
    const indexClient = meilisearchClient.index(INDEXES.ASSOCIATIONS);
    const filterString = buildAssociationsFilterString(filters);

    const searchOptions: Record<string, unknown> = {
      limit,
      offset,
      facets: [
        'parent_entity_type',
        'member_entity_type',
        'sources',
        'association_annotation_terms',
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
    console.error('Meilisearch associations search error:', error);
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
