"use server";
import { db } from '@/db';
import { resourcesInMetadata, datasetsInMetadata } from '../../../../../drizzle/schema';
import { eq } from 'drizzle-orm';
import { DatasourceFormData } from '../types';
import * as yaml from 'js-yaml';
import * as fs from 'fs/promises';
import * as path from 'path';

export async function createDatasource(
  data: Omit<DatasourceFormData, 'id'> & { id: string }
) {
  try {

    // Check if datasource ID already exists
    const existingResource = await db
      .select()
      .from(resourcesInMetadata)
      .where(eq(resourcesInMetadata.id, data.id))
      .limit(1);

    if (existingResource.length > 0) {
      throw new Error(`Datasource with ID '${data.id}' already exists`);
    }

    // Start transaction
    const result = await db.transaction(async (tx) => {
      // Insert resource
      await tx.insert(resourcesInMetadata).values({
        id: data.id,
        name: data.name,
        description: data.description,
        license: data.license,
        primaryPubmed: data.primaryPubmed || null,
        health: data.health,
        website: data.website,
        updateCategory: data.updateCategory,
        accessCategory: data.accessCategory
      });

      // Insert datasets
      for (const dataset of data.datasets) {
        await tx.insert(datasetsInMetadata).values({
          resourceId: data.id,
          name: dataset.name,
          entityType: dataset.entityType,
          category: dataset.category,
          types: dataset.types,
          evidenceLevel: dataset.evidenceLevel,
          taxonScope: dataset.taxonScope,
          download: dataset.download || null,
          dataProcessing: dataset.dataProcessing || null
        });
      }

      return { resourceId: data.id };
    });

    // Generate YAML configuration file
    const yamlPath = await generateYamlConfig(data);

    return {
      success: true,
      datasourceId: result.resourceId,
      yamlPath
    };
  } catch (error) {
    console.error("Error creating datasource:", error);
    throw new Error(error instanceof Error ? error.message : "Failed to create datasource");
  }
}

async function generateYamlConfig(data: DatasourceFormData): Promise<string> {
  // Convert form data to YAML format matching the pipeline structure
  const yamlData = {
    // Resource-level information
    id: data.id,
    name: data.name,
    description: data.description,
    license: data.license,
    primary_pubmed: data.primaryPubmed || null,
    health: data.health,
    website: data.website,
    update_category: data.updateCategory,
    access_category: data.accessCategory,
    
    // Dataset-level information
    datasets: data.datasets.map(dataset => ({
      name: dataset.name,
      entity_type: dataset.entityType,
      category: dataset.category,
      types: dataset.types,
      evidence_level: dataset.evidenceLevel,
      taxon_scope: dataset.taxonScope.replace('-', '_'), // Convert to underscore format
      download: dataset.download ? {
        url: dataset.download.url,
        method: dataset.download.method,
        ...(dataset.download.extractFromZip && {
          extract_from_zip: {
            target_file_pattern: dataset.download.extractFromZip.targetFilePattern
          }
        }),
        ...(dataset.download.delimiter && { delimiter: dataset.download.delimiter }),
        ...(dataset.download.header !== undefined && { header: dataset.download.header }),
        ...(dataset.download.postData && { post_data: dataset.download.postData }),
        tags: [] // Empty tags for now
      } : undefined,
      data_processing: dataset.dataProcessing || {}
    }))
  };

  // Convert to YAML string
  const yamlString = yaml.dump(yamlData, {
    indent: 2,
    lineWidth: -1,
    noRefs: true,
    sortKeys: false
  });

  // Define the path for the YAML file
  const yamlDir = path.join(process.cwd(), 'db_build', 'pipeline_new', 'source_configurations');
  const yamlPath = path.join(yamlDir, `${data.id}.yaml`);

  // Ensure directory exists
  await fs.mkdir(yamlDir, { recursive: true });

  // Write YAML file
  await fs.writeFile(yamlPath, yamlString, 'utf-8');

  return yamlPath;
}

export async function validateDatasourceId(id: string): Promise<boolean> {
  try {
    const existing = await db
      .select({ id: resourcesInMetadata.id })
      .from(resourcesInMetadata)
      .where(eq(resourcesInMetadata.id, id))
      .limit(1);

    return existing.length === 0;
  } catch (error) {
    console.error("Error validating datasource ID:", error);
    return false;
  }
}

export async function suggestDatasourceId(name: string): Promise<string> {
  // Convert name to lowercase, replace spaces and special chars with underscores
  let suggested = name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '') // Remove leading/trailing underscores
    .substring(0, 30); // Limit length

  // Check if ID is available
  let isAvailable = await validateDatasourceId(suggested);
  let counter = 1;

  while (!isAvailable && counter < 100) {
    const newId = `${suggested}_${counter}`;
    isAvailable = await validateDatasourceId(newId);
    if (isAvailable) {
      suggested = newId;
    }
    counter++;
  }

  return suggested;
}