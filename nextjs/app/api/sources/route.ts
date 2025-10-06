import { NextResponse } from 'next/server';
import { scanDatabases } from '../../lib/database-scanner';

export async function GET() {
  try {
    const databases = scanDatabases();
    const omnipath = databases[0];

    return NextResponse.json({
      database: omnipath,
      sources: omnipath?.sources || []
    });
  } catch (error) {
    console.error('Error scanning databases:', error);
    return NextResponse.json(
      { error: 'Failed to scan databases' },
      { status: 500 }
    );
  }
}
