import { NextRequest, NextResponse } from "next/server";
import { getEntityServiceUrl } from "@/lib/api/config";
import { fetchMeilisearchDocuments } from "@/lib/meilisearch/search";
import { INDEXES } from "@/lib/meilisearch/client";

interface LookupPayload {
  identifiers?: string[];
}

interface LookupServiceResponse {
  results: Record<string, number[]>;
}

export async function POST(req: NextRequest) {
  try {
    const body: LookupPayload = await req.json();
    const identifiers = (body.identifiers || [])
      .map((id) => id.trim())
      .filter((id) => id.length > 0);

    if (identifiers.length === 0) {
      return NextResponse.json({ error: "No identifiers provided" }, { status: 400 });
    }

    // Call entity-service
    const entityServiceUrl = getEntityServiceUrl();
    const response = await fetch(`${entityServiceUrl}/lookup`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ identifiers }),
    });

    if (!response.ok) {
      const text = await response.text();
      return NextResponse.json(
        { error: `Entity service error: ${response.status} ${text}` },
        { status: 502 }
      );
    }

    const data = (await response.json()) as LookupServiceResponse;
    const entries = Object.entries(data.results || {});
    const matches = entries.map(([identifier, entityIds]) => ({
      identifier,
      entityIds: entityIds || [],
    }));

    // Fetch entity details for all returned IDs (if any)
    const allEntityIds = Array.from(
      new Set(matches.flatMap((m) => m.entityIds.map((id) => id.toString())))
    );

    const documents = allEntityIds.length
      ? await fetchMeilisearchDocuments(INDEXES.ENTITIES, allEntityIds, "entity_id")
      : { documents: [] };

    return NextResponse.json({ matches, entities: documents.documents });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unexpected error";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
