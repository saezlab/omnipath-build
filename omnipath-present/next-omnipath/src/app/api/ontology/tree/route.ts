import { NextRequest, NextResponse } from "next/server";
import { getOntologyServiceUrl } from "@/lib/api/config";

interface TreeRequestPayload {
  ontologyId?: string;
  termIds?: string[];
}

export async function POST(req: NextRequest) {
  try {
    const body = (await req.json()) as TreeRequestPayload;
    const ontologyId = body.ontologyId || "psi_mi";
    const termIds = (body.termIds || []).filter((id) => id.length > 0);

    if (termIds.length === 0) {
      return NextResponse.json({ error: "No term IDs provided" }, { status: 400 });
    }

    const ontologyServiceUrl = getOntologyServiceUrl();
    const response = await fetch(`${ontologyServiceUrl}/${ontologyId}/tree`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ term_ids: termIds }),
    });

    if (!response.ok) {
      const text = await response.text();
      return NextResponse.json(
        { error: `Ontology service error: ${response.status} ${text}` },
        { status: 502 }
      );
    }

    const data = await response.json();
    return NextResponse.json(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unexpected error";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
