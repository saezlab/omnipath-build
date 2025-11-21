"use server";

import { MeilisearchFilters, MeilisearchSearchResponse } from '@/types/meilisearch';
import { searchInteractionsMeilisearch } from '@/lib/meilisearch/search';

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

// Export response types
export type SearchInteractionsResponse = Awaited<ReturnType<typeof searchInteractions>>;
