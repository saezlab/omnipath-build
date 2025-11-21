"use client";

import { useEntitySelection } from "@/contexts/entity-selection-context";
import { Button } from "@/components/ui/button";
import { Trash2, ArrowRight } from "lucide-react";
import Link from "next/link";
import { SearchResults } from "@/features/search/components/search-results";
import type { SearchResult } from "@/features/search/components/result-card";

export default function SelectionPage() {
  const { selectedEntities, clearSelection, selectionCount } = useEntitySelection();

  // Convert selected entities to SearchResult format for reuse
  const results: SearchResult[] = selectedEntities.map((entity) => ({
    id: entity.id,
    entity_id: entity.entityId,
    type: "entity",
    entity_type: entity.type ? `${entity.type}:${entity.id}` : undefined,
    names: [entity.name],
    gene_symbols: [],
    descriptions: [],
    identifiers: [],
    sources: [],
    references: [],
  }));

  if (selectionCount === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center p-8">
        <div className="text-center space-y-4">
          <h1 className="text-2xl font-bold">No entities selected</h1>
          <p className="text-muted-foreground">
            Use the search page to find and add entities to your selection.
          </p>
          <Link href="/search">
            <Button>Go to Search</Button>
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col">
      <div className="sticky top-0 z-10 bg-background border-b">
        <div className="w-full max-w-screen-xl mx-auto px-4 py-4">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-2xl font-bold">Current Selection</h1>
              <p className="text-muted-foreground">
                {selectionCount} {selectionCount === 1 ? "entity" : "entities"} selected
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Button variant="outline" onClick={clearSelection}>
                <Trash2 className="h-4 w-4 mr-2" />
                Clear All
              </Button>
              <Link href={`/explore?entities=${selectedEntities.map(e => e.id).join(',')}`}>
                <Button>
                  <ArrowRight className="h-4 w-4 mr-2" />
                  Explore
                </Button>
              </Link>
            </div>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        <div className="w-full max-w-screen-xl mx-auto px-4 py-6">
          <SearchResults results={results} loading={false} />
        </div>
      </div>
    </div>
  );
}
