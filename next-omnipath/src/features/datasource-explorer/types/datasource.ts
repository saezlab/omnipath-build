export interface DataSource {
  id: string;
  name: string;
  description: string;
  license: string;
  primaryPubmed?: string | null;
  health: 'success' | 'error';
  website: string;
  updateCategory: 'one_time_paper' | 'discontinued' | 'infrequent' | 'frequent';
  accessCategory: 'file_download' | 'api' | 'web_scraping';
  datasets: Dataset[];
}

export interface Dataset {
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
    postData?: Record<string, string>;
  };
  dataProcessing?: {
    targetModel: string;
    [key: string]: unknown;
  };
}

export interface DataSourceFilters {
  search?: string;
  categories?: string[];
  entityTypes?: string[];
  updateCategories?: string[];
  accessCategories?: string[];
  healthStatuses?: string[];
  licenseTypes?: string[];
  evidenceLevels?: string[];
  taxonScopes?: string[];
  interactionTypes?: string[];
  annotationTypes?: string[];
  ontologyTypes?: string[];
}

export const UPDATE_CATEGORIES = [
  { value: 'frequent', label: 'Frequent', description: 'Updated regularly (< 3 months)' },
  { value: 'infrequent', label: 'Infrequent', description: 'Updated occasionally (> 3 months)' },
  { value: 'discontinued', label: 'Discontinued', description: 'No longer maintained' },
  { value: 'one_time_paper', label: 'One-time Paper', description: 'Published once with a paper' }
] as const;

export const ACCESS_CATEGORIES = [
  { value: 'file_download', label: 'File Download', description: 'Direct file download' },
  { value: 'api', label: 'API', description: 'REST or GraphQL API' },
  { value: 'web_scraping', label: 'Web Scraping', description: 'Extracted from web pages' }
] as const;

export const HEALTH_STATUSES = [
  { value: 'success', label: 'Active', color: 'bg-green-100 text-green-800' },
  { value: 'error', label: 'Error', color: 'bg-red-100 text-red-800' }
] as const;

export const LICENSE_TYPES = [
  { value: 'open', label: 'Open (CC-BY/CC0)', regex: /CC[\s-]*(BY|0)/i },
  { value: 'non-commercial', label: 'Non-commercial', regex: /non[\s-]*commercial|NC/i },
  { value: 'custom', label: 'Custom/Proprietary', regex: /custom|proprietary|academic/i }
] as const;

export const ENTITY_TYPES = [
  { value: 'protein', label: 'Protein', icon: '🧬' },
  { value: 'drug', label: 'Drug', icon: '💊' },
  { value: 'protein_complex', label: 'Protein Complex', icon: '🔗' },
  { value: 'rna', label: 'RNA', icon: '🧬' },
  { value: 'metabolite', label: 'Metabolite', icon: '🧪' },
  { value: 'microbe', label: 'Microbe', icon: '🦠' },
  { value: 'phenotype', label: 'Phenotype', icon: '📊' }
] as const;

export const CATEGORIES = [
  { value: 'interaction', label: 'Interaction', icon: 'Network' },
  { value: 'annotation', label: 'Annotation', icon: 'Tag' },
  { value: 'ontology', label: 'Ontology', icon: 'Database' }
] as const;

export const EVIDENCE_LEVELS = [
  { value: 'experimental', label: 'Experimental' },
  { value: 'literature_curated', label: 'Literature Curated' },
  { value: 'predicted', label: 'Predicted' },
  { value: 'text_mined', label: 'Text Mined' }
] as const;

export const TAXON_SCOPES = [
  { value: 'human-only', label: 'Human Only' },
  { value: 'multi-species', label: 'Multi-species' }
] as const;