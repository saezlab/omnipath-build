export type TargetModel = 'interactions' | 'entities' | 'controlled_vocabularies';

export interface TransformFunction {
  name: string;
  description: string;
  requiresArgs?: boolean;
  argSchema?: Record<string, unknown>;
}

export interface FieldMapping {
  targetField: string;
  sourceColumn?: string;
  constantValue?: string;
  transform?: string;
  transformArgs?: Record<string, unknown>;
}

export interface DataProcessingConfig {
  targetModel: TargetModel;
  mappings: Record<string, FieldMapping>;
}

export interface MappingPreview {
  sourceData: Record<string, unknown>[];
  transformedData: Record<string, unknown>[];
  errors?: string[];
}

import { getTransformationFunctions } from '../api/function-mutations';

// Make TRANSFORM_FUNCTIONS dynamic
export let TRANSFORM_FUNCTIONS: TransformFunction[] = [];

// Function to load functions from database
export async function loadTransformFunctions() {
  const functions = await getTransformationFunctions();
  
  TRANSFORM_FUNCTIONS = functions.filter(f => f.name !== null).map(f => ({
    name: f.name!,
    description: f.description || '',
    requiresArgs: !!f.argumentSchema,
    argSchema: f.argumentSchema as Record<string, unknown> | undefined
  }));
  
  return TRANSFORM_FUNCTIONS;
}

// Target model fields
export const INTERACTION_FIELDS = [
  { name: 'entity_a', required: true, description: 'First interacting entity' },
  { name: 'entity_b', required: true, description: 'Second interacting entity' },
  { name: 'entity_a_id_type', required: true, description: 'ID type for entity A' },
  { name: 'entity_b_id_type', required: true, description: 'ID type for entity B' },
  { name: 'entity_a_type', required: false, description: 'Entity type for A' },
  { name: 'entity_b_type', required: false, description: 'Entity type for B' },
  { name: 'interaction_type', required: false, description: 'Type of interaction' },
  { name: 'detection_methods', required: false, description: 'Detection method used' },
  { name: 'pubmed_id', required: false, description: 'Publication reference' },
  { name: 'causal_mechanism', required: false, description: 'Causal mechanism' },
  { name: 'causal_statement', required: false, description: 'Causal statement' },
  { name: 'evidence_sentence', required: false, description: 'Supporting evidence' },
  { name: 'source_identifier', required: false, description: 'Source database ID' },
  { name: 'data_source', required: true, description: 'Data source MI term' }
];

export const ENTITY_FIELDS = [
  { name: 'canonical_identifier', required: true, description: 'Primary identifier' },
  { name: 'canonical_identifier_type', required: true, description: 'Type of primary ID' },
  { name: 'entity_type', required: true, description: 'Type of entity' },
  { name: 'alt_id', required: false, description: 'Alternative identifier' },
  { name: 'description', required: false, description: 'Entity description' },
  { name: 'members', required: false, description: 'Complex/family members' }
];

export const CV_FIELDS = [
  { name: 'term_id', required: true, description: 'Ontology term ID' },
  { name: 'term_name', required: true, description: 'Ontology term name' },
  { name: 'namespace', required: true, description: 'Ontology namespace' },
  { name: 'definition', required: false, description: 'Term definition' },
  { name: 'synonyms', required: false, description: 'Term synonyms' },
  { name: 'parent_terms', required: false, description: 'Parent terms' }
];

export function getFieldsForModel(model: TargetModel) {
  switch (model) {
    case 'interactions':
      return INTERACTION_FIELDS;
    case 'entities':
      return ENTITY_FIELDS;
    case 'controlled_vocabularies':
      return CV_FIELDS;
    default:
      return [];
  }
}