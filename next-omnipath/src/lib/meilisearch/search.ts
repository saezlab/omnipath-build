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
 * Build filter string for Meilisearch entity search
 */
function buildEntityFilterString(filters: MeilisearchFilters): string {
  const filterParts: string[] = [];

  // Entity IDs filter (for related entities tab)
  if (filters.entity_ids?.length) {
    const entityIdFilters = filters.entity_ids.map(id => `entity_id = ${id}`).join(' OR ');
    filterParts.push(`(${entityIdFilters})`);
  }

  // Entity type filter
  if (filters.entity_types?.length) {
    const entityTypeFilters = filters.entity_types.map(type => `entity_type = "${type}"`).join(' OR ');
    filterParts.push(`(${entityTypeFilters})`);
  }

  // Sources filter
  if (filters.sources?.length) {
    const sourceFilters = filters.sources.map(source => `sources = "${source}"`).join(' OR ');
    filterParts.push(`(${sourceFilters})`);
  }

  // NCBI taxonomy ID filter
  // When filtering by tax ID, include records with the specified tax ID(s) OR no tax ID at all
  if (filters.ncbi_tax_id?.length) {
    const taxIdFilters = filters.ncbi_tax_id.map(taxId => `ncbi_tax_id = "${taxId}"`).join(' OR ');
    filterParts.push(`(${taxIdFilters} OR ncbi_tax_id IS NULL)`);
  }

  return filterParts.join(' AND ');
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
 * Build filter string for Meilisearch interactions (new schema)
 */
function buildMeilisearchFilterString(filters: MeilisearchFilters): string {
  const filterParts: string[] = [];

  // Multiple entity IDs filter - matches interactions where ANY of the entity IDs is member_a or member_b
  if (filters.entity_ids?.length) {
    const entityFilters = filters.entity_ids.map(id =>
      `(member_a_id = ${id} OR member_b_id = ${id})`
    ).join(' OR ');
    filterParts.push(`(${entityFilters})`);
  }

  // Single member ID filters - filter on either member_a_id OR member_b_id
  if (filters.member_a_id !== undefined) {
    filterParts.push(`(member_a_id = ${filters.member_a_id} OR member_b_id = ${filters.member_a_id})`);
  }

  if (filters.member_b_id !== undefined) {
    filterParts.push(`(member_a_id = ${filters.member_b_id} OR member_b_id = ${filters.member_b_id})`);
  }

  // Member types filter (array field)
  if (filters.member_types?.length) {
    const typeFilters = filters.member_types.map(type => `member_types = "${type}"`).join(' OR ');
    filterParts.push(`(${typeFilters})`);
  }

  // Direction filter
  if (filters.has_direction !== undefined && filters.has_direction !== null) {
    filterParts.push(`has_direction = ${filters.has_direction}`);
  }

  // Sign filters
  if (filters.has_positive_sign !== undefined && filters.has_positive_sign !== null) {
    filterParts.push(`has_positive_sign = ${filters.has_positive_sign}`);
  }

  if (filters.has_negative_sign !== undefined && filters.has_negative_sign !== null) {
    filterParts.push(`has_negative_sign = ${filters.has_negative_sign}`);
  }

  // Interaction annotation terms filter
  if (filters.interaction_annotation_terms?.length) {
    const termFilters = filters.interaction_annotation_terms.map(term => `interaction_annotation_terms = "${term}"`).join(' OR ');
    filterParts.push(`(${termFilters})`);
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
        'member_types',
        'has_direction',
        'has_positive_sign',
        'has_negative_sign',
        'interaction_annotation_terms',
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