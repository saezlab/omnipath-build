"use server";

import { fetchMeilisearchDocuments } from "@/lib/meilisearch/search";

interface CvTermData {
  id: string;
  type: string;
  name: string;
  associated_entity_ids?: string[];
  [key: string]: unknown;
}

export interface CvTermDetails {
  id: string;
  type: 'cv_term';
  name: string;
  synonyms: string[];
  namespace: string | null;
  definition: string;
  associated_entity_ids: string[];
}

export async function fetchCvTerm(id: string): Promise<CvTermData | null> {
  try {
    const data = await fetchMeilisearchDocuments('cv_terms', [id]);
    const documents = data.documents as unknown[];
    return documents?.[0] as CvTermData || null;
  } catch (error) {
    console.error('Error fetching CV term:', error);
    return null;
  }
}

export async function fetchCvTermDetails(cvTermId: string): Promise<CvTermDetails | null> {
  try {
    const data = await fetchMeilisearchDocuments('cv_terms', [cvTermId]);
    const documents = data.documents as unknown[];
    return documents?.[0] as CvTermDetails || null;
  } catch (error) {
    console.error('Error fetching CV term details:', error);
    return null;
  }
}

// Export response types
export type FetchCvTermResponse = Awaited<ReturnType<typeof fetchCvTerm>>;
export type FetchCvTermDetailsResponse = Awaited<ReturnType<typeof fetchCvTermDetails>>;