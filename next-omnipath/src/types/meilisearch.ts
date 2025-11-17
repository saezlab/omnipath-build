// Types for Meilisearch interaction data

export interface MeilisearchInteraction {
  id: string;
  type: 'interaction';
  
  // Entity data
  entity_ids: string[];
  entity_a_canonical_id: string;
  entity_b_canonical_id: string;
  entity_a_name: string;
  entity_b_name: string;
  
  // Aggregated evidence data
  evidence_count: number;
  interaction_types: CvTermReference[];
  data_sources: CvTermReference[];
  detection_methods: CvTermReference[];
  causal_statements: CvTermReference[];
  causal_mechanisms: CvTermReference[];
  interactor_types: CvTermReference[];
  signs: string[];
  consensus_sign: string | null;
  is_directed: boolean;
  consensus_direction: 'forward' | 'reverse' | null;
  
  // Index signature to satisfy DataRow constraint
  [key: string]: unknown;
}

export interface CvTermReference {
  id: string;
  name: string;
}

export interface MeilisearchFilters {
  // Interaction filters
  interaction_types?: string[];
  data_sources?: string[];
  detection_methods?: string[];
  causal_statements?: string[];
  causal_mechanisms?: string[];
  interactor_types?: string[];
  signs?: string[];
  consensus_sign?: string | null;
  is_directed?: boolean | null;
  consensus_direction?: string | null;
  evidence_count_min?: number;
  evidence_count_max?: number;
  entity_ids?: string[];

  // Entity search filters
  entity_types?: string[];
  sources?: string[];
  ncbi_tax_id?: string[];
}

export interface MeilisearchSearchParams {
  query: string;
  filters: MeilisearchFilters;
  limit: number;
  offset: number;
}

export interface MeilisearchSearchResponse {
  hits: MeilisearchInteraction[];
  estimatedTotalHits: number;
  limit: number;
  offset: number;
  processingTimeMs: number;
  query: string;
  facetDistribution?: Record<string, Record<string, number>>;
}