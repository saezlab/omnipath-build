"use server";

import { searchMeilisearch as meilisearchDirectSearch, fetchMeilisearchDocuments } from '@/lib/meilisearch/search';
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

export async function getEntityNames(ids: string[]): Promise<Record<string, string>> {
  if (!ids.length) return {};

  try {
    const { documents } = await fetchMeilisearchDocuments("search_entities", ids, "entity_id");

    const nameMap: Record<string, string> = {};
    (documents as Array<{ entity_id: string; names?: string[]; gene_symbols?: string[] }>).forEach((doc) => {
      // Try to find the best name
      const name = (doc.names && doc.names[0]) ||
        (doc.gene_symbols && doc.gene_symbols[0]) ||
        `Entity ${doc.entity_id}`;
      nameMap[doc.entity_id] = name;
    });

    return nameMap;
  } catch (e) {
    console.error("Error fetching entity names:", e);
    return {};
  }
}