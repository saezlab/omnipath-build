'use server';

import { datasourceCreationSchema, DatasourceCreationFormData } from './schema';
import type { ActionResponse } from './schema';
import { createDatasource } from '../api/mutations';

export async function createDatasourceAction(
  prevState: ActionResponse,
  formData: FormData
): Promise<ActionResponse> {
  const rawData = {
    id: formData.get('id'),
    name: formData.get('name'),
    description: formData.get('description'),
    license: formData.get('license'),
    primaryPubmed: formData.get('primaryPubmed'),
    health: formData.get('health'),
    website: formData.get('website'),
    updateCategory: formData.get('updateCategory'),
    accessCategory: formData.get('accessCategory'),
    datasets: JSON.parse(formData.get('datasets') as string || '[]'),
  };

  // Validate the data
  const validatedData = datasourceCreationSchema.safeParse(rawData);

  if (!validatedData.success) {
    return {
      success: false,
      message: "Validation failed",
      errors: validatedData.error.flatten().fieldErrors,
      previousData: rawData as DatasourceCreationFormData,
    };
  }

  try {
    const result = await createDatasource(validatedData.data);
    
    return {
      success: true,
      message: "Datasource created successfully",
      datasourceId: result.datasourceId,
      yamlPath: result.yamlPath,
    };
  } catch (error) {
    return {
      success: false,
      message: error instanceof Error ? error.message : "Failed to create datasource",
      previousData: validatedData.data,
    };
  }
}