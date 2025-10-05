import { NextRequest, NextResponse } from 'next/server';
import { scanDatabases, buildDatabaseTree } from '../../../lib/database-scanner';

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;
    const databases = scanDatabases();
    const currentDb = databases.find(db => db.name === id);
    
    if (!currentDb) {
      return NextResponse.json(
        { error: 'Database not found' },
        { status: 404 }
      );
    }
    
    const treeData = buildDatabaseTree([currentDb]);
    
    return NextResponse.json({
      database: currentDb,
      tree: treeData
    });
  } catch (error) {
    console.error('Error fetching database:', error);
    return NextResponse.json(
      { error: 'Failed to fetch database' },
      { status: 500 }
    );
  }
}
