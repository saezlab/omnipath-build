"use client";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useSidebarContent } from "@/contexts/sidebar-content-context";
import { useCallback, useEffect, useState } from "react";
import { InteractionsExploreTab } from "./components/interactions-explore-tab";
import { FilterSidebar } from "@/features/interactions-search/components/filter-sidebar";
import { MeilisearchFilters } from "@/types/meilisearch";

export default function ExplorePage() {
  const [activeTab, setActiveTab] = useState("interactions");
  const { setSidebarContent } = useSidebarContent();

  // Interactions filter state
  const [interactionsFilters, setInteractionsFilters] = useState<MeilisearchFilters>({});
  const [interactionsFilterCounts, setInteractionsFilterCounts] = useState<Record<string, Record<string, number>>>({});

  // Handlers for interactions filters
  const handleInteractionsFilterChange = useCallback((newFilters: MeilisearchFilters) => {
    setInteractionsFilters(newFilters);
  }, []);

  const handleInteractionsClearFilters = useCallback(() => {
    setInteractionsFilters({});
  }, []);

  // Callback to receive filter counts from interactions tab
  const handleInteractionsFilterCountsUpdate = useCallback((counts: Record<string, Record<string, number>>) => {
    setInteractionsFilterCounts(counts);
  }, []);

  // Set sidebar content based on active tab
  useEffect(() => {
    if (activeTab === "interactions" && Object.keys(interactionsFilterCounts).length > 0) {
      setSidebarContent(
        <FilterSidebar
          filters={interactionsFilters}
          filterCounts={interactionsFilterCounts}
          onFilterChange={handleInteractionsFilterChange}
          onClearFilters={handleInteractionsClearFilters}
          isMobile
        />
      );
    } else {
      setSidebarContent(null);
    }

    // Cleanup on unmount
    return () => {
      setSidebarContent(null);
    };
  }, [activeTab, interactionsFilters, interactionsFilterCounts, handleInteractionsFilterChange, handleInteractionsClearFilters, setSidebarContent]);

  return (
    <div className="flex-1 flex flex-col">
      <div className="sticky top-0 z-10 bg-background border-b">
        <div className="w-full max-w-screen-xl mx-auto px-4 py-4">
          <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
            <TabsList>
              <TabsTrigger value="interactions">Interactions</TabsTrigger>
            </TabsList>
          </Tabs>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        <div className="w-full max-w-screen-xl mx-auto px-4 py-6">
          <Tabs value={activeTab} onValueChange={setActiveTab}>
            <TabsContent value="interactions" className="mt-0">
              <InteractionsExploreTab
                filters={interactionsFilters}
                onFilterChange={handleInteractionsFilterChange}
                onFilterCountsUpdate={handleInteractionsFilterCountsUpdate}
              />
            </TabsContent>
          </Tabs>
        </div>
      </div>
    </div>
  );
}
