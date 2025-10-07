import { NextResponse } from 'next/server';
import { readFileSync } from 'fs';
import { join } from 'path';
import * as yaml from 'js-yaml';

export async function GET(
  request: Request,
  { params }: { params: Promise<{ name: string }> }
) {
  try {
    const { name } = await params;
    const configPath = join(process.cwd(), '..', 'databases', 'omnipath', 'configuration', 'resources', `${name}.yaml`);
    const fileContent = readFileSync(configPath, 'utf-8');
    const config = yaml.load(fileContent);

    return NextResponse.json({ config });
  } catch (error) {
    return NextResponse.json({ error: 'Failed to read or parse configuration file' }, { status: 500 });
  }
}
