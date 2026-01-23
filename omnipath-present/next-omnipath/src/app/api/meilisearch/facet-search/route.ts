import { NextResponse } from "next/server";
import { meilisearchClient, INDEXES } from "@/lib/meilisearch/client";
import { buildEntityFilterString, buildInteractionFilterString } from "@/lib/meilisearch/filters";
import type { MeilisearchFilters } from "@/types/meilisearch";

interface FacetSearchRequest {
  facetName: string;
  facetQuery: string;
  filters?: MeilisearchFilters;
  limit?: number;
  index?: "entities" | "interactions";
}

export async function POST(request: Request) {
  try {
    const body = (await request.json()) as FacetSearchRequest;
    const { facetName, facetQuery, filters, limit, index = "entities" } = body;

    if (!facetName || typeof facetQuery !== "string") {
      return NextResponse.json({ error: "Missing facetName or facetQuery" }, { status: 400 });
    }

    const indexClient = meilisearchClient.index(
      index === "interactions" ? INDEXES.INTERACTIONS : INDEXES.ENTITIES
    );
    const filterString = filters
      ? index === "interactions"
        ? buildInteractionFilterString(filters)
        : buildEntityFilterString(filters)
      : "";
    const options: { filter?: string; limit?: number } = {};
    if (filterString) {
      options.filter = filterString;
    }
    if (typeof limit === "number") {
      options.limit = limit;
    }

    const result = await indexClient.searchForFacetValues({
      facetName,
      facetQuery,
      ...options,
    });
    return NextResponse.json(result);
  } catch (error) {
    console.error("Facet search error:", error);
    return NextResponse.json({ error: "Facet search failed" }, { status: 500 });
  }
}
