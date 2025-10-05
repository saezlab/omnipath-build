import { NextResponse } from 'next/server';
import { scanDatabases } from '../../lib/database-scanner';

export async function GET() {
  try {
    const databases = scanDatabases();
    return NextResponse.json(databases);
  } catch (error) {
    console.error('Error scanning databases:', error);
    return NextResponse.json(
      { error: 'Failed to scan databases' },
      { status: 500 }
    );
  }
}
