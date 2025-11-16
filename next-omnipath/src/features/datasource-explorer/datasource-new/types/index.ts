export interface DatasetFormData {
  name: string;
  entityType: 'protein' | 'drug' | 'protein_complex' | 'rna' | 'metabolite' | 'microbe' | 'phenotype';
  category: 'annotation' | 'interaction' | 'ontology';
  types: string[];
  evidenceLevel: 'experimental' | 'literature_curated' | 'predicted' | 'text_mined';
  taxonScope: 'human-only' | 'multi-species';
  download?: {
    url: string;
    method: 'GET' | 'POST';
    delimiter?: string;
    header?: boolean;
    extractFromZip?: {
      targetFilePattern: string;
    };
    postData?: Record<string, string>;
  };
  dataProcessing?: {
    targetModel: string;
    [key: string]: unknown;
  };
  uploadedFile?: {
    name: string;
    content: string;
    type: string;
  };
}

export interface DatasourceFormData {
  id: string;
  name: string;
  description: string;
  license: string;
  primaryPubmed?: string;
  health: 'success' | 'error';
  website: string;
  updateCategory: 'one_time_paper' | 'discontinued' | 'infrequent' | 'frequent';
  accessCategory: 'file_download' | 'api' | 'web_scraping';
  datasets: DatasetFormData[];
}

export interface DatasourceCreationResponse {
  success: boolean;
  message: string;
  datasourceId?: string;
  yamlPath?: string;
}

export const INTERACTION_TYPES = [
  'protein_protein_directed',
  'protein_protein_undirected',
  'protein_drug',
  'protein_metabolite',
  'protein_rna',
  'protein_dna',
  'drug_drug',
  'genetic_interaction'
] as const;

export const ANNOTATION_TYPES = [
  'functional',
  'structural',
  'localization',
  'expression',
  'modification',
  'disease_association',
  'pathway_membership'
] as const;

export const ONTOLOGY_TYPES = [
  'gene_ontology',
  'disease_ontology',
  'phenotype_ontology',
  'chemical_ontology',
  'anatomy_ontology',
  'cell_type_ontology'
] as const;

export const COMMON_LICENSES = [
  { value: 'CC0', label: 'CC0 - Public Domain' },
  { value: 'CC-BY-4.0', label: 'CC BY 4.0 - Attribution' },
  { value: 'CC-BY-SA-4.0', label: 'CC BY-SA 4.0 - Attribution ShareAlike' },
  { value: 'CC-BY-NC-4.0', label: 'CC BY-NC 4.0 - Attribution NonCommercial' },
  { value: 'MIT', label: 'MIT License' },
  { value: 'Apache-2.0', label: 'Apache License 2.0' },
  { value: 'GPL-3.0', label: 'GPL v3.0' },
  { value: 'custom', label: 'Custom/Proprietary' },
  { value: 'academic', label: 'Academic Use Only' }
] as const;