import { NextResponse } from 'next/server';
import { readFileSync } from 'fs';
import { join } from 'path';

export async function GET() {
  try {
    const configPath = join(process.cwd(), '..', 'databases', 'omnipath', 'configuration', 'gold_tables.py');
    const fileContent = readFileSync(configPath, 'utf-8');

    // Parse the Python file to extract gold_tables dictionary
    // This is a simple parser - for production you might want to use a proper Python parser
    const goldTablesMatch = fileContent.match(/gold_tables\s*=\s*{([\s\S]*?)}\s*$/m);

    if (!goldTablesMatch) {
      return NextResponse.json({ error: 'Could not parse gold_tables' }, { status: 500 });
    }

    // Return the raw Python content for now
    // The frontend will need to parse this or we can do more sophisticated parsing here
    return NextResponse.json({
      rawContent: fileContent,
      goldTablesSection: goldTablesMatch[0]
    });
  } catch (error) {
    return NextResponse.json({ error: 'Failed to read gold tables configuration' }, { status: 500 });
  }
}
