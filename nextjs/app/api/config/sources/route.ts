import { NextResponse } from 'next/server';
import { readdirSync } from 'fs';
import { join } from 'path';

export async function GET() {
  try {
    const configDir = join(process.cwd(), '..', 'databases', 'omnipath', 'configuration', 'resources');
    const files = readdirSync(configDir)
      .filter(file => file.endsWith('.yaml'))
      .map(file => file.replace('.yaml', ''));

    return NextResponse.json({ sources: files });
  } catch (error) {
    return NextResponse.json({ error: 'Failed to read configuration directory' }, { status: 500 });
  }
}
