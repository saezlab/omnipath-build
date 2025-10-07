import { NextResponse } from 'next/server';
import { readFileSync } from 'fs';
import { join } from 'path';

export async function GET() {
  try {
    const sqlPath = join(process.cwd(), '..', 'databases', 'omnipath', 'configuration', 'transformation_functions.sql');
    const sqlContent = readFileSync(sqlPath, 'utf-8');

    // Extract individual functions/macros
    const functionRegex = /CREATE\s+OR\s+REPLACE\s+(?:MACRO|FUNCTION)\s+(\w+)\s*\(([\s\S]*?)\)\s+AS\s+\(([\s\S]*?)\);/gi;
    const functions: Array<{ name: string; params: string; body: string; fullText: string }> = [];

    let match;
    while ((match = functionRegex.exec(sqlContent)) !== null) {
      functions.push({
        name: match[1],
        params: match[2],
        body: match[3],
        fullText: match[0]
      });
    }

    return NextResponse.json({
      rawContent: sqlContent,
      functions
    });
  } catch (error) {
    return NextResponse.json({ error: 'Failed to read transformation functions' }, { status: 500 });
  }
}
