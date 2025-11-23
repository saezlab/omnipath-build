"use client";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useSidebarContent } from "@/contexts/sidebar-content-context";
import { useCallback, useEffect, useState, useMemo } from "react";
import { useSearchParams } from "next/navigation";
import { InteractionsExploreTab } from "./components/interactions-explore-tab";
import { RelatedEntitiesTab } from "./components/related-entities-tab";
import { FilterSidebar } from "@/features/interactions-search/components/filter-sidebar";
import { EntityFilterSidebar } from "@/features/search/components/entity-filter-sidebar";
import { MeilisearchFilters } from "@/types/meilisearch";
import { useEntitySelection } from "@/contexts/entity-selection-context";

interface EntityFilters {
  entity_types?: string[];
  sources?: string[];
  ncbi_tax_id?: string[];
}

interface EntityFilterCounts {
  entity_type?: Record<string, number>;
  sources?: Record<string, number>;
  ncbi_tax_id?: Record<string, number>;
}

export default function ExplorePage() {
  const [activeTab, setActiveTab] = useState("interactions");
  const { setSidebarContent } = useSidebarContent();
  const searchParams = useSearchParams();
  const { selectedEntities } = useEntitySelection();

  // Parse entity IDs from URL params (supports both single "entity" and multiple "entities")
  const parseEntityIds = useCallback(() => {
    const singleEntity = searchParams.get("entity");
    const multipleEntities = searchParams.get("entities");

    if (multipleEntities) {
      const ids = multipleEntities.split(',').map(id => parseInt(id.trim(), 10)).filter(id => !isNaN(id));
      return ids.length > 0 ? ids : undefined;
    }
    if (singleEntity) {
      const id = parseInt(singleEntity, 10);
      return !isNaN(id) ? [id] : undefined;
    }
    return undefined;
  }, [searchParams]);

  // Interactions filter state - initialize with entity filter if present
  const [interactionsFilters, setInteractionsFilters] = useState<MeilisearchFilters>(() => {
    const entityIds = parseEntityIds();
    if (entityIds?.length) {
      return { entity_ids: entityIds };
    }
    return {};
  });
  const [interactionsFilterCounts, setInteractionsFilterCounts] = useState<Record<string, Record<string, number>>>({});

  // Entity filters state for related tabs (complexes, cv_terms, pathways, reactions)
  const [entityFilters, setEntityFilters] = useState<EntityFilters>({});
  const [entityFilterCounts, setEntityFilterCounts] = useState<EntityFilterCounts>({});

  // Calculate which tabs have results based on selected entities
  const tabsWithResults = useMemo(() => {
    const tabs = {
      interactions: true, // Always show interactions
      complexes: false,
      cv_terms: false,
      pathways: false,
      reactions: false,
    };

    // Check if any selected entity has results for each tab type
    for (const entity of selectedEntities) {
      if (entity.complexes && entity.complexes.length > 0) {
        tabs.complexes = true;
      }
      if (entity.cv_terms && entity.cv_terms.length > 0) {
        tabs.cv_terms = true;
      }
      if (entity.pathways && entity.pathways.length > 0) {
        tabs.pathways = true;
      }
      if (entity.reactions && entity.reactions.length > 0) {
        tabs.reactions = true;
      }
    }

    return tabs;
  }, [selectedEntities]);

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

  // Handlers for entity filters (related tabs)
  const handleEntityFilterChange = useCallback((newFilters: EntityFilters) => {
    setEntityFilters(newFilters);
  }, []);

  const handleEntityClearFilters = useCallback(() => {
    setEntityFilters({});
  }, []);

  const handleEntityFilterCountsUpdate = useCallback((counts: EntityFilterCounts) => {
    setEntityFilterCounts(counts);
  }, []);

  // Sync URL params with filter state when URL changes
  useEffect(() => {
    const entityIds = parseEntityIds();
    if (entityIds?.length) {
      setInteractionsFilters(prev => ({
        ...prev,
        entity_ids: entityIds,
        member_a_id: undefined  // Clear old single ID filter
      }));
    }
  }, [searchParams, parseEntityIds]);

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
    } else if ((activeTab === "complexes" || activeTab === "cv_terms" || activeTab === "pathways" || activeTab === "reactions") && Object.keys(entityFilterCounts).length > 0) {
      setSidebarContent(
        <EntityFilterSidebar
          filters={entityFilters}
          filterCounts={entityFilterCounts}
          onFilterChange={handleEntityFilterChange}
          onClearFilters={handleEntityClearFilters}
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
  }, [activeTab, interactionsFilters, interactionsFilterCounts, handleInteractionsFilterChange, handleInteractionsClearFilters, entityFilters, entityFilterCounts, handleEntityFilterChange, handleEntityClearFilters, setSidebarContent]);

  return (
    <div className="flex-1 flex flex-col">
      <div className="sticky top-0 z-10 bg-background border-b">
        <div className="w-full max-w-screen-xl mx-auto px-4 py-4">
          <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
            <TabsList>
              <TabsTrigger value="interactions">Interactions</TabsTrigger>
              {tabsWithResults.complexes && <TabsTrigger value="complexes">Complexes</TabsTrigger>}
              {tabsWithResults.cv_terms && <TabsTrigger value="cv_terms">CV Terms</TabsTrigger>}
              {tabsWithResults.pathways && <TabsTrigger value="pathways">Pathways</TabsTrigger>}
              {tabsWithResults.reactions && <TabsTrigger value="reactions">Reactions</TabsTrigger>}
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
            {tabsWithResults.complexes && (
              <TabsContent value="complexes" className="mt-0">
                <RelatedEntitiesTab
                  relatedType="complex"
                  filters={entityFilters}
                  onFilterChange={handleEntityFilterChange}
                  onFilterCountsUpdate={handleEntityFilterCountsUpdate}
                />
              </TabsContent>
            )}
            {tabsWithResults.cv_terms && (
              <TabsContent value="cv_terms" className="mt-0">
                <RelatedEntitiesTab
                  relatedType="cv_term"
                  filters={entityFilters}
                  onFilterChange={handleEntityFilterChange}
                  onFilterCountsUpdate={handleEntityFilterCountsUpdate}
                />
              </TabsContent>
            )}
            {tabsWithResults.pathways && (
              <TabsContent value="pathways" className="mt-0">
                <RelatedEntitiesTab
                  relatedType="pathway"
                  filters={entityFilters}
                  onFilterChange={handleEntityFilterChange}
                  onFilterCountsUpdate={handleEntityFilterCountsUpdate}
                />
              </TabsContent>
            )}
            {tabsWithResults.reactions && (
              <TabsContent value="reactions" className="mt-0">
                <RelatedEntitiesTab
                  relatedType="reaction"
                  filters={entityFilters}
                  onFilterChange={handleEntityFilterChange}
                  onFilterCountsUpdate={handleEntityFilterCountsUpdate}
                />
              </TabsContent>
            )}
          </Tabs>
        </div>
      </div>
    </div>
  );
}
