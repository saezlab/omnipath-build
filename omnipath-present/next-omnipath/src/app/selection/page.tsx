"use client";

import { useEntitySelection } from "@/contexts/entity-selection-context";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import Link from "next/link";
import SearchPage from "@/features/search/page";
import InteractionsPage from "@/features/explore/interactions-page";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useMemo, useState, useEffect } from "react";
import { searchInteractionsMeilisearch } from "@/lib/meilisearch/search";

export default function SelectionPage() {
  const { selectionCount, selectedEntities } = useEntitySelection();
  const [activeTab, setActiveTab] = useState("selection");
  const [interactionsCount, setInteractionsCount] = useState<number | null>(null);
  const [loadingCounts, setLoadingCounts] = useState(true);

  // Get selected entity IDs for filtering
  const selectedEntityIds = useMemo(() =>
    selectedEntities
      .map(e => e.entityId || parseInt(e.id, 10))
      .filter(id => !isNaN(id)),
    [selectedEntities]
  );

  // Get associated entity IDs from context
  const associatedEntityIds = useMemo(() => {
    const entityIdSet = new Set<number>();
    selectedEntities.forEach(entity => {
      entity.associated_entity_ids?.forEach(id => entityIdSet.add(id));
    });
    return Array.from(entityIdSet);
  }, [selectedEntities]);

  // Fetch interaction count
  useEffect(() => {
    async function fetchInteractionsCount() {
      if (selectedEntityIds.length === 0) {
        setInteractionsCount(0);
        setLoadingCounts(false);
        return;
      }

      setLoadingCounts(true);
      try {
        const response = await searchInteractionsMeilisearch({
          query: "",
          index: 'search_interactions' as any,
          limit: 1,
          offset: 0,
          filters: { entity_ids: selectedEntityIds }
        });
        setInteractionsCount(response.estimatedTotalHits || 0);
      } catch (error) {
        console.error("Error fetching interactions count:", error);
        setInteractionsCount(0);
      } finally {
        setLoadingCounts(false);
      }
    }

    fetchInteractionsCount();
  }, [selectedEntityIds]);

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
      <Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1 flex flex-col">
        <div className="sticky top-0 z-10 bg-background border-b">
          <div className="w-full max-w-screen-xl mx-auto px-4 py-4">
            <TabsList>
              <TabsTrigger value="selection" className="flex items-center gap-2">
                Selection
                <Badge variant="secondary" className="ml-1">
                  {selectionCount}
                </Badge>
              </TabsTrigger>
              <TabsTrigger
                value="interactions"
                className="flex items-center gap-2"
                disabled={!loadingCounts && (interactionsCount ?? 0) === 0}
              >
                Interactions
                {loadingCounts ? (
                  <Badge variant="secondary" className="ml-1">...</Badge>
                ) : (
                  <Badge variant="secondary" className="ml-1">
                    {interactionsCount?.toLocaleString() || 0}
                  </Badge>
                )}
              </TabsTrigger>
              <TabsTrigger
                value="associations"
                className="flex items-center gap-2"
                disabled={associatedEntityIds.length === 0}
              >
                Associations
                <Badge variant="secondary" className="ml-1">
                  {associatedEntityIds.length}
                </Badge>
              </TabsTrigger>
            </TabsList>
          </div>
        </div>

        <TabsContent value="selection" className="flex-1 overflow-hidden mt-0">
          <SearchPage
            embedded={true}
            allowOntologyInEmbedded={true}
            showLayoutSwitcherInEmbedded={true}
            showFilters={true}
            initialFilters={{ entity_ids: selectedEntityIds }}
          />
        </TabsContent>

        <TabsContent value="interactions" className="flex-1 overflow-hidden mt-0">
          <InteractionsPage />
        </TabsContent>

        <TabsContent value="associations" className="flex-1 overflow-hidden mt-0">
          {associatedEntityIds.length > 0 ? (
            <SearchPage
              embedded={true}
              allowOntologyInEmbedded={true}
              showLayoutSwitcherInEmbedded={true}
              showFilters={true}
              initialFilters={{ entity_ids: associatedEntityIds }}
            />
          ) : (
            <div className="flex items-center justify-center py-12">
              <p className="text-muted-foreground">
                No associated entities found
              </p>
            </div>
          )}
        </TabsContent>

      </Tabs>
    </div>
  );
}
