import { MeiliSearch } from 'meilisearch';
import { getMeilisearchUrl } from '@/lib/api/config';

// Create Meilisearch client
export const meilisearchClient = new MeiliSearch({
  host: getMeilisearchUrl(),
  apiKey: process.env.MEILI_MASTER_KEY || process.env.MEILISEARCH_MASTER_KEY || process.env.MEILISEARCH_API_KEY,
});

// Index names
export const INDEXES = {
  ENTITIES: 'entities',
  CV_TERMS: 'cv_terms',
  INTERACTIONS: 'interactions',
} as const;

export type IndexName = typeof INDEXES[keyof typeof INDEXES];