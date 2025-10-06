import { NextRequest, NextResponse } from 'next/server';
import { getSource } from '../../../lib/database-scanner';

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;
    const source = getSource(id);

    if (!source) {
      return NextResponse.json(
        { error: 'Source not found' },
        { status: 404 }
      );
    }

    return NextResponse.json({
      source
    });
  } catch (error) {
    console.error('Error fetching source:', error);
    return NextResponse.json(
      { error: 'Failed to fetch source' },
      { status: 500 }
    );
  }
}
