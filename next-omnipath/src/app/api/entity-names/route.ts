import { fetchMeilisearchDocuments } from '@/lib/meilisearch/search';
import { NextResponse } from 'next/server';

export async function POST(request: Request) {
    try {
        const { ids } = await request.json();

        if (!ids || !Array.isArray(ids) || ids.length === 0) {
            return NextResponse.json({});
        }

        const { documents } = await fetchMeilisearchDocuments("search_entities", ids, "entity_id");

        const nameMap: Record<string, string> = {};
        documents.forEach((doc: any) => {
            // Try to find the best name
            const name = (doc.names && doc.names[0]) ||
                (doc.gene_symbols && doc.gene_symbols[0]) ||
                `Entity ${doc.entity_id}`;
            nameMap[doc.entity_id] = name;
        });

        return NextResponse.json(nameMap);
    } catch (e) {
        console.error("Error fetching entity names:", e);
        return NextResponse.json({}, { status: 500 });
    }
}
