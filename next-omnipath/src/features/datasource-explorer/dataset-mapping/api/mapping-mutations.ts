"use server";

import { db } from '@/db';
import { datasetsInMetadata } from '../../../../../drizzle/schema';
import { eq, and } from 'drizzle-orm';
import { DataProcessingConfig, FieldMapping, TargetModel } from '../types';
import * as yaml from 'js-yaml';
import * as fs from 'fs/promises';
import * as path from 'path';

export async function saveMappingConfiguration(
  datasourceId: string,
  datasetName: string,
  config: DataProcessingConfig
) {
  try {
    // Update the dataset's data_processing field in the database
    await db
      .update(datasetsInMetadata)
      .set({
        dataProcessing: {
          target_model: config.targetModel,
          ...Object.entries(config.mappings).reduce((acc, [field, mapping]) => {
            if (mapping.constantValue) {
              acc[field] = {
                constant_value: mapping.constantValue
              };
            } else if (mapping.sourceColumn) {
              acc[field] = {
                source: mapping.sourceColumn,
                ...(mapping.transform && { transform: mapping.transform }),
                ...(mapping.transformArgs && { transform_args: mapping.transformArgs })
              };
            }
            return acc;
          }, {} as Record<string, unknown>)
        }
      })
      .where(
        and(
          eq(datasetsInMetadata.resourceId, datasourceId),
          eq(datasetsInMetadata.name, datasetName)
        )
      );

    // Also update the YAML file
    await updateYamlConfiguration(datasourceId, datasetName, config);

    return { success: true };
  } catch (error) {
    console.error('Error saving mapping configuration:', error);
    throw new Error('Failed to save mapping configuration');
  }
}

async function updateYamlConfiguration(
  datasourceId: string,
  datasetName: string,
  config: DataProcessingConfig
) {
  const yamlPath = path.join(
    process.cwd(),
    'db_build',
    'pipeline_new',
    'source_configurations',
    `${datasourceId}.yaml`
  );

  try {
    // Read existing YAML
    const yamlContent = await fs.readFile(yamlPath, 'utf-8');
    const yamlData = yaml.load(yamlContent) as Record<string, unknown>;

    // Find and update the specific dataset
    if (yamlData.datasets) {
      const datasets = yamlData.datasets as Array<{ name: string; data_processing?: unknown }>;
      const datasetIndex = datasets.findIndex((d) => d.name === datasetName);
      if (datasetIndex !== -1) {
        datasets[datasetIndex].data_processing = {
          target_model: config.targetModel,
          ...Object.entries(config.mappings).reduce((acc, [field, mapping]) => {
            if (mapping.constantValue) {
              acc[field] = {
                constant_value: mapping.constantValue
              };
            } else if (mapping.sourceColumn) {
              acc[field] = {
                source: mapping.sourceColumn,
                ...(mapping.transform && { transform: mapping.transform }),
                ...(mapping.transformArgs && { transform_args: mapping.transformArgs })
              };
            }
            return acc;
          }, {} as Record<string, unknown>)
        };
      }
    }

    // Write updated YAML
    const updatedYaml = yaml.dump(yamlData, {
      indent: 2,
      lineWidth: -1,
      noRefs: true,
      sortKeys: false
    });
    
    await fs.writeFile(yamlPath, updatedYaml, 'utf-8');
  } catch (error) {
    console.error('Error updating YAML configuration:', error);
    // Don't throw here as the database update is more important
  }
}

export async function loadMappingConfiguration(
  datasourceId: string,
  datasetName: string
): Promise<DataProcessingConfig | null> {
  try {
    const result = await db
      .select({ dataProcessing: datasetsInMetadata.dataProcessing })
      .from(datasetsInMetadata)
      .where(
        and(
          eq(datasetsInMetadata.resourceId, datasourceId),
          eq(datasetsInMetadata.name, datasetName)
        )
      )
      .limit(1);

    if (result.length === 0 || !result[0].dataProcessing) {
      return null;
    }

    const processing = result[0].dataProcessing as Record<string, unknown>;
    
    // Convert from database format to our format
    const mappings: Record<string, FieldMapping> = {};
    Object.entries(processing).forEach(([key, value]) => {
      if (key === 'target_model') return;
      
      const val = value as { constant_value?: unknown; source?: string; transform?: string; transform_args?: unknown };
      if (val.constant_value !== undefined) {
        mappings[key] = {
          targetField: key,
          constantValue: val.constant_value as string
        };
      } else if (val.source) {
        mappings[key] = {
          targetField: key,
          sourceColumn: val.source,
          transform: val.transform,
          transformArgs: val.transform_args as Record<string, unknown> | undefined
        };
      }
    });

    return {
      targetModel: (processing.target_model as TargetModel) || 'interactions',
      mappings
    };
  } catch (error) {
    console.error('Error loading mapping configuration:', error);
    return null;
  }
}