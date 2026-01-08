import { MeiliSearch } from 'meilisearch';
import { getMeilisearchUrl } from '@/lib/api/config';

// Create Meilisearch client
export const meilisearchClient = new MeiliSearch({
  host: getMeilisearchUrl(),
  apiKey: process.env.MEILISEARCH_API_KEY,
});

// Index names
export const INDEXES = {
  ENTITIES: 'search_entities',
  INTERACTIONS: 'search_interactions',
} as const;

export type IndexName = typeof INDEXES[keyof typeof INDEXES];