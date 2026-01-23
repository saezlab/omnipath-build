// Types for Meilisearch interaction data

// Map entry for annotation key-value pairs
export interface AnnotationMapEntry {
  key: string;
  value: string;
}

// Evidence structure with annotations for interaction, member_a, and member_b
export interface InteractionEvidence {
  interaction_annotation_terms: AnnotationMapEntry[];
  interaction_annotation_values: AnnotationMapEntry[];
  interaction_annotation_units: AnnotationMapEntry[];
  member_a_annotation_terms: AnnotationMapEntry[];
  member_a_annotation_values: AnnotationMapEntry[];
  member_a_annotation_units: AnnotationMapEntry[];
  member_b_annotation_terms: AnnotationMapEntry[];
  member_b_annotation_values: AnnotationMapEntry[];
  member_b_annotation_units: AnnotationMapEntry[];
}

// Direction with sign information
export interface InteractionDirection {
  direction: 'a-b' | 'b-a';
  sign: -1 | 0 | 1; // -1 = negative/inhibition, 0 = mixed, 1 = positive/activation
}

export interface MeilisearchInteraction {
  // Primary key - pair key like "123-456"
  interaction_key: string;

  // Member entity IDs
  member_a_id: number;
  member_b_id: number;

  // Member types as "TypeName:EntityId" format
  member_types: string[];

  // Evidence array with nested annotation data
  evidence: InteractionEvidence[];

  // Directions with sign information
  directions: InteractionDirection[];

  // Flattened filter fields
  has_direction: boolean;
  has_positive_sign: boolean;
  has_negative_sign: boolean;
  interaction_annotation_terms: string[];
  sources?: string[];

  // Index signature to satisfy DataRow constraint
  [key: string]: unknown;
}

// Association annotation entry
export interface AssociationAnnotation {
  key: string;
  value: string;
  unit?: string;
}

// Identifier entry
export interface IdentifierEntry {
  key: string;
  value: string;
}

// Association document type
export interface MeilisearchAssociation {
  // Primary key
  association_key: string;

  // Parent entity info
  parent_entity_id: number;
  parent_entity_type: string;
  parent_name: string;
  parent_identifiers: IdentifierEntry[];

  // Member entity info
  member_entity_id: number;
  member_entity_type: string;
  member_name: string;
  member_identifiers: IdentifierEntry[];

  // Sources
  sources: string[];

  // Annotations
  annotations: AssociationAnnotation[];
  association_annotation_terms: string[];

  // Index signature
  [key: string]: unknown;
}

export interface CvTermReference {
  id: string;
  name: string;
}

export interface MeilisearchFilters {
  // Interaction filters (new schema)
  member_a_id?: number;
  member_b_id?: number;
  entity_ids?: number[];  // Filter by multiple entity IDs (matches member_a_id OR member_b_id)
  member_types?: string[];
  has_direction?: boolean | null;
  has_positive_sign?: boolean | null;
  has_negative_sign?: boolean | null;
  interaction_annotation_terms?: string[];

  // Entity search filters
  entity_types?: string[];
  sources?: string[];
  ncbi_tax_id?: string[];
  cv_terms_go?: string[];
  cv_terms_mi?: string[];
  cv_terms_om?: string[];
  cv_terms_hp?: string[];
  cv_terms_kw?: string[];

  // Association filters
  parent_entity_ids?: number[];
  member_entity_ids?: number[];
  parent_entity_types?: string[];
  member_entity_types?: string[];
  association_annotation_terms?: string[];
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
