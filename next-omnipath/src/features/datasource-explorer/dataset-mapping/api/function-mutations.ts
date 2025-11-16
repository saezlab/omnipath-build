"use server";

import { db } from '@/db';
import { transformationFunctionsInMetadata } from '../../../../../drizzle/schema';
import { eq } from 'drizzle-orm';
import { sql } from 'drizzle-orm';

// List all transformation functions
export async function getTransformationFunctions() {
  return await db.select().from(transformationFunctionsInMetadata);
}

// Get a single function by ID
export async function getTransformationFunction(id: number) {
  const results = await db
    .select()
    .from(transformationFunctionsInMetadata)
    .where(eq(transformationFunctionsInMetadata.id, id))
    .limit(1);
  return results[0];
}

// Create a new transformation function
export async function createTransformationFunction(data: {
  name: string;
  description?: string;
  category?: string;
  sqlDefinition: string;
  argumentSchema?: unknown;
}) {
  // Validate SQL syntax
  await validateFunctionSQL(data.sqlDefinition);
  
  // Insert into database
  const result = await db
    .insert(transformationFunctionsInMetadata)
    .values(data)
    .returning();
  
  // Register the function in PostgreSQL
  await db.execute(sql.raw(data.sqlDefinition));
  
  return result[0];
}

// Update an existing function
export async function updateTransformationFunction(
  id: number,
  data: {
    name?: string;
    description?: string;
    category?: string;
    sqlDefinition?: string;
    argumentSchema?: unknown;
  }
) {
  if (data.sqlDefinition) {
    await validateFunctionSQL(data.sqlDefinition);
  }
  
  const result = await db
    .update(transformationFunctionsInMetadata)
    .set({
      ...data,
      updatedAt: new Date().toISOString()
    })
    .where(eq(transformationFunctionsInMetadata.id, id))
    .returning();
  
  if (data.sqlDefinition) {
    await db.execute(sql.raw(data.sqlDefinition));
  }
  
  return result[0];
}

// Delete a function
export async function deleteTransformationFunction(id: number) {
  const func = await getTransformationFunction(id);
  if (!func) throw new Error('Function not found');
  
  // Drop the function from PostgreSQL
  await db.execute(sql.raw(`DROP FUNCTION IF EXISTS ${func.name} CASCADE`));
  
  // Delete from database
  await db
    .delete(transformationFunctionsInMetadata)
    .where(eq(transformationFunctionsInMetadata.id, id));
}

// Test a function with sample data
export async function testTransformationFunction(
  sqlDefinition: string,
  testInput: string,
  testArgs?: Record<string, unknown>
) {
  // Create a temporary function
  const tempName = `temp_func_${Date.now()}`;
  const tempSQL = sqlDefinition.replace(/CREATE OR REPLACE FUNCTION \w+/, `CREATE OR REPLACE FUNCTION ${tempName}`);
  
  try {
    // Create temporary function
    await db.execute(sql.raw(tempSQL));
    
    // Build test query
    let testQuery = `SELECT ${tempName}('${testInput.replace(/'/g, "''")}'`;
    if (testArgs) {
      for (const [, value] of Object.entries(testArgs)) {
        testQuery += `, '${String(value).replace(/'/g, "''")}'`;
      }
    }
    testQuery += ')';
    
    // Execute test
    const result = await db.execute(sql.raw(testQuery));
    
    return {
      success: true,
      output: result.rows[0][tempName]
    };
  } catch (error) {
    return {
      success: false,
      error: error instanceof Error ? error.message : 'Unknown error'
    };
  } finally {
    // Clean up temporary function
    await db.execute(sql.raw(`DROP FUNCTION IF EXISTS ${tempName}`));
  }
}

// Validate SQL function definition
async function validateFunctionSQL(sqlDefinition: string) {
  // Basic validation
  if (!sqlDefinition.includes('CREATE OR REPLACE FUNCTION')) {
    throw new Error('SQL must start with CREATE OR REPLACE FUNCTION');
  }
  
  // Ensure it's marked as IMMUTABLE for performance
  if (!sqlDefinition.includes('IMMUTABLE')) {
    throw new Error('Functions must be marked as IMMUTABLE');
  }
  
  // Prevent dangerous operations
  const forbidden = ['DROP', 'DELETE', 'UPDATE', 'INSERT', 'TRUNCATE', 'ALTER'];
  for (const keyword of forbidden) {
    if (sqlDefinition.toUpperCase().includes(keyword)) {
      throw new Error(`Forbidden SQL keyword: ${keyword}`);
    }
  }
}