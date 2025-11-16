"use server";

import { searchMeilisearch as meilisearchDirectSearch } from '@/lib/meilisearch/search';
import type { IndexName } from '@/lib/meilisearch/client';
import type { MeilisearchFilters } from '@/types/meilisearch';

export async function searchMeilisearch({ query, index = "entities", limit = 20, offset = 0 }: { query: string; index?: IndexName; limit?: number; offset?: number; filters?: MeilisearchFilters | string }) {
  if (!query) return { hits: [] };
  try {
    return await meilisearchDirectSearch({ index, query, limit, offset });
  } catch (e) {
    return { hits: [], error: e instanceof Error ? e.message : "Unknown error" };
  }
}

export type SearchMeilisearchResponse = Awaited<ReturnType<typeof searchMeilisearch>>; 