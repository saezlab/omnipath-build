import { NextRequest, NextResponse } from 'next/server';
import { loadParquetFile } from '../../lib/database-scanner';

export async function GET(request: NextRequest) {
  const searchParams = request.nextUrl.searchParams;
  const filePath = searchParams.get('path');
  
  if (!filePath) {
    return NextResponse.json({ error: 'File path is required' }, { status: 400 });
  }
  
  try {
    const data = await loadParquetFile(filePath);
    
    return new NextResponse(data, {
      headers: {
        'Content-Type': 'application/octet-stream',
        'Content-Length': data.byteLength.toString(),
      },
    });
  } catch (error) {
    console.error('Error loading parquet file:', error);
    return NextResponse.json(
      { error: 'Failed to load parquet file' },
      { status: 500 }
    );
  }
}
