"use server";

import { MeilisearchFilters, MeilisearchSearchResponse } from '@/types/meilisearch';
import { searchInteractionsMeilisearch } from '@/lib/meilisearch/search';
import { db } from '@/db';
import { sql } from 'drizzle-orm';

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
      index: "interactions",
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

// Define types locally
export interface InteractionEvidenceDetail {
  id: number;
  interactionId: number;
  dataSourceId: number | null;
  referenceId: number | null;
  interactionTypeId: number | null;
  causalMechanismId: number | null;
  causalStatementId: number | null;
  evidenceSentence: string | null;
  sourceIdentifier: string | null;
  isDirected: boolean | null;
  direction: string | null;
  sign: string | null;
  dataSourceName?: string | null;
  interactionTypeName?: string | null;
  causalMechanismName?: string | null;
  causalStatementName?: string | null;
  pubmedId?: number | null;
}

export interface PaginatedResponse<T> {
  data: T[];
  total: number;
  page: number;
  pageSize: number;
  totalPages: number;
  hasNext: boolean;
  hasPrevious: boolean;
}

// Type alias for clarity
export type PaginatedEvidenceResponse = PaginatedResponse<InteractionEvidenceDetail>;

// Export response types
export type SearchInteractionsResponse = Awaited<ReturnType<typeof searchInteractions>>;
export type GetInteractionEvidencesResponse = Awaited<ReturnType<typeof getInteractionEvidences>>;

export async function getInteractionEvidences(
  interactionId: number,
  page: number = 1
): Promise<PaginatedEvidenceResponse> {
  try {
    const pageSize = 20;
    const offset = (page - 1) * pageSize;
    
    // Get the evidences with proper schema references
    const evidenceQuery = sql`
      SELECT 
        ie.id,
        ie.interaction_id,
        ie.data_source_id,
        ie.reference_id,
        ie.interaction_type_id,
        ie.causal_mechanism_id,
        ie.causal_statement_id,
        ie.evidence_sentence,
        ie.source_identifier,
        ie.is_directed,
        ie.direction,
        ie.sign,
        ds.name as data_source_name,
        it.name as interaction_type_name,
        cm.name as causal_mechanism_name,
        cs.name as causal_statement_name,
        r.pubmed_id,
        COUNT(*) OVER() as total_count
      FROM gold.interaction_evidence ie
      LEFT JOIN gold.cv_term ds ON ie.data_source_id = ds.id
      LEFT JOIN gold.cv_term it ON ie.interaction_type_id = it.id
      LEFT JOIN gold.cv_term cm ON ie.causal_mechanism_id = cm.id
      LEFT JOIN gold.cv_term cs ON ie.causal_statement_id = cs.id
      LEFT JOIN gold.reference r ON ie.reference_id = r.id
      WHERE ie.interaction_id = ${interactionId}
      ORDER BY ie.id
      LIMIT ${pageSize}
      OFFSET ${offset}
    `;

    const evidenceResult = await db.execute(evidenceQuery);
    const totalCount = evidenceResult.rows.length > 0 
      ? (evidenceResult.rows[0] as Record<string, unknown>).total_count as number 
      : 0;
    
    const evidences: InteractionEvidenceDetail[] = evidenceResult.rows.map((row: Record<string, unknown>) => ({
      id: row.id as number,
      interactionId: row.interaction_id as number,
      dataSourceId: row.data_source_id as number | null,
      referenceId: row.reference_id as number | null,
      interactionTypeId: row.interaction_type_id as number | null,
      causalMechanismId: row.causal_mechanism_id as number | null,
      causalStatementId: row.causal_statement_id as number | null,
      evidenceSentence: row.evidence_sentence as string | null,
      sourceIdentifier: row.source_identifier as string | null,
      isDirected: row.is_directed as boolean | null,
      direction: row.direction as string | null,
      sign: row.sign as string | null,
      dataSourceName: row.data_source_name as string | null,
      interactionTypeName: row.interaction_type_name as string | null,
      causalMechanismName: row.causal_mechanism_name as string | null,
      causalStatementName: row.causal_statement_name as string | null,
      pubmedId: row.pubmed_id as number | null,
    }));

    const totalPages = Math.ceil(totalCount / pageSize);

    return {
      data: evidences,
      total: totalCount,
      page,
      pageSize,
      totalPages,
      hasNext: page < totalPages,
      hasPrevious: page > 1,
    };
  } catch (error) {
    console.error('Error fetching interaction evidences:', error);
    throw error;
  }
}