"use server";

import { db } from '@/db';
import { sql } from 'drizzle-orm';

export async function testFunctionRegistration() {
  try {
    // Test a simple function
    const testFunc = `
      CREATE OR REPLACE FUNCTION test_extract_mi_term(field TEXT) 
      RETURNS TEXT AS $$
      SELECT substring(field from 'MI:[0-9][0-9][0-9][0-9]')
      $$ LANGUAGE sql IMMUTABLE;
    `;
    
    await db.execute(sql.raw(testFunc));
    console.log('Successfully created test function');
    
    // Test the function
    const result = await db.execute(sql.raw(`SELECT test_extract_mi_term('psi-mi:"MI:0326"(protein)')`));
    console.log('Function result:', result.rows);
    
    // Clean up
    await db.execute(sql.raw('DROP FUNCTION IF EXISTS test_extract_mi_term(TEXT)'));
    
    return { success: true, result: result.rows };
  } catch (error) {
    console.error('Function test failed:', error);
    return { success: false, error: error instanceof Error ? error.message : 'Unknown error' };
  }
}