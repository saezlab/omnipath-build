import { z } from "zod";
import { INTERACTION_TYPES, ANNOTATION_TYPES } from "../types";

const downloadConfigSchema = z.object({
  url: z.string().url("Invalid URL format"),
  method: z.enum(['GET', 'POST']),
  delimiter: z.string().optional(),
  header: z.boolean().optional(),
  extractFromZip: z.object({
    targetFilePattern: z.string()
  }).optional(),
  postData: z.record(z.string()).optional()
}).optional();

const dataProcessingSchema = z.object({
  targetModel: z.string(),
}).passthrough().optional();

const datasetSchema = z.object({
  name: z.string()
    .min(3, "Dataset name must be at least 3 characters")
    .max(50, "Dataset name must be less than 50 characters")
    .regex(/^[a-z0-9_]+$/, "Dataset name must be lowercase alphanumeric with underscores"),
  entityType: z.enum(['protein', 'drug', 'protein_complex', 'rna', 'metabolite', 'microbe', 'phenotype']),
  category: z.enum(['annotation', 'interaction', 'ontology']),
  types: z.array(z.string()),
  evidenceLevel: z.enum(['experimental', 'literature_curated', 'predicted', 'text_mined']),
  taxonScope: z.enum(['human-only', 'multi-species']),
  download: downloadConfigSchema,
  dataProcessing: dataProcessingSchema
}).refine((data) => {
  // Types are optional for ontology, required for others
  if (data.category === 'ontology') {
    return true;
  }
  if (data.types.length === 0) {
    return false;
  }
  // Validate types based on category
  if (data.category === 'interaction') {
    return data.types.every(t => INTERACTION_TYPES.includes(t as typeof INTERACTION_TYPES[number]));
  } else if (data.category === 'annotation') {
    return data.types.every(t => ANNOTATION_TYPES.includes(t as typeof ANNOTATION_TYPES[number]));
  }
  return true;
}, {
  message: "At least one type is required for interaction and annotation categories"
});

export const datasourceCreationSchema = z.object({
  id: z.string()
    .min(3, "ID must be at least 3 characters")
    .max(30, "ID must be less than 30 characters")
    .regex(/^[a-z0-9_]+$/, "ID must be lowercase alphanumeric with underscores"),
  name: z.string()
    .min(3, "Name must be at least 3 characters")
    .max(100, "Name must be less than 100 characters"),
  description: z.string()
    .min(50, "Description must be at least 50 characters")
    .max(500, "Description must be less than 500 characters"),
  license: z.string().min(1, "License is required"),
  primaryPubmed: z.string()
    .regex(/^\d+$/, "PubMed ID must be numeric")
    .optional()
    .or(z.literal('')),
  health: z.enum(['success', 'error']),
  website: z.string().url("Invalid website URL"),
  updateCategory: z.enum(['one_time_paper', 'discontinued', 'infrequent', 'frequent']),
  accessCategory: z.enum(['file_download', 'api', 'web_scraping']),
  datasets: z.array(datasetSchema)
    .min(1, "At least one dataset is required")
    .max(5, "Maximum 5 datasets allowed")
});

export type DatasourceCreationFormData = z.infer<typeof datasourceCreationSchema>;

export interface ActionResponse {
  success: boolean;
  message: string;
  errors?: {
    [K in keyof DatasourceCreationFormData]?: string[];
  };
  datasourceId?: string;
  yamlPath?: string;
  previousData?: DatasourceCreationFormData;
}