"use server";

import { searchMeilisearch as meilisearchDirectSearch } from '@/lib/meilisearch/search';
import type { IndexName } from '@/lib/meilisearch/client';
import type { MeilisearchFilters } from '@/types/meilisearch';

export async function searchMeilisearch({
  query,
  index = "search_entities",
  limit = 20,
  offset = 0,
  filters = {}
}: {
  query: string;
  index?: IndexName;
  limit?: number;
  offset?: number;
  filters?: MeilisearchFilters;
}) {
  // Allow empty query to show all results with facets (for initial load and filtering)
  try {
    return await meilisearchDirectSearch({ index, query, limit, offset, filters });
  } catch (e) {
    return { hits: [], error: e instanceof Error ? e.message : "Unknown error" };
  }
}

export type SearchMeilisearchResponse = Awaited<ReturnType<typeof searchMeilisearch>>; 