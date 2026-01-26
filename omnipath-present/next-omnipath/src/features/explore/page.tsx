"use client";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useSidebarContent } from "@/contexts/sidebar-content-context";
import { useCallback, useEffect, useState, useMemo } from "react";
import { InteractionsExploreTab } from "./components/interactions-explore-tab";
import { AssociationsExploreTab } from "./components/associations-explore-tab";
import { AnnotationsExploreTab } from "./components/annotations-explore-tab";
import { FilterSidebar } from "@/features/interactions-search/components/filter-sidebar";
import { MeilisearchFilters } from "@/types/meilisearch";
import { useEntitySelection } from "@/contexts/entity-selection-context";
import { searchAssociationsMeilisearch } from "@/lib/meilisearch/search";
import { INDEXES } from "@/lib/meilisearch/client";

// Entity type accessions for filtering associations
const ENTITY_TYPES = {
  COMPLEX: "Complex:OM:0002",
  PATHWAY: "Pathway:OM:0004",
  REACTION: "Reaction:OM:0005",
  FOOD: "Food:OM:0007",
  SMALL_MOLECULE: "Small Molecule:OM:0003",
  PROTEIN: "Protein:OM:0001",
} as const;

export default function ExplorePage() {
  const [activeTab, setActiveTab] = useState("interactions");
  const { setSidebarContent } = useSidebarContent();
  const { selectedEntities } = useEntitySelection();

  // Tab availability state (loaded dynamically via associations count)
  const [tabCounts, setTabCounts] = useState<Record<string, number>>({
    complexes: 0,
    pathways: 0,
    reactions: 0,
    foods: 0,
    compounds: 0,
    members: 0,
  });
  // Interactions filter state
  const [interactionsFilters, setInteractionsFilters] = useState<MeilisearchFilters>({});
  const [interactionsFilterCounts, setInteractionsFilterCounts] = useState<Record<string, Record<string, number>>>({});

  // Get selected entity IDs
  const selectedEntityIds = useMemo(() =>
    selectedEntities
      .map(e => e.entityId || parseInt(e.id, 10))
      .filter(id => !isNaN(id)),
    [selectedEntities]
  );

  // Load association counts for tab visibility
  useEffect(() => {
    async function loadTabCounts() {
      if (selectedEntityIds.length === 0) {
        setTabCounts({ complexes: 0, pathways: 0, reactions: 0, foods: 0, compounds: 0, members: 0, annotations: 0 });
        return;
      }

      try {
        // Query 1: Find PARENTS (where selected entities are members)
        const parentsPromise = searchAssociationsMeilisearch({
          query: "",
          index: INDEXES.ASSOCIATIONS,
          limit: 0,
          offset: 0,
          filters: {
            member_entity_ids: selectedEntityIds,
          },
        });

        // Query 2: Find MEMBERS (where selected entities are parents)
        const membersPromise = searchAssociationsMeilisearch({
          query: "",
          index: INDEXES.ASSOCIATIONS,
          limit: 0,
          offset: 0,
          filters: {
            parent_entity_ids: selectedEntityIds,
          },
        });

        const [parentsResponse, membersResponse] = await Promise.all([parentsPromise, membersPromise]);

        const parentTypeCounts = parentsResponse.facetDistribution?.parent_entity_type || {};
        const memberTypeCounts = membersResponse.facetDistribution?.member_entity_type || {};

        setTabCounts({
          // Parents
          complexes: Object.entries(parentTypeCounts)
            .filter(([key]) => key.includes("Complex"))
            .reduce((sum, [, count]) => sum + count, 0),
          pathways: Object.entries(parentTypeCounts)
            .filter(([key]) => key.includes("Pathway"))
            .reduce((sum, [, count]) => sum + count, 0),
          reactions: Object.entries(parentTypeCounts)
            .filter(([key]) => key.includes("Reaction"))
            .reduce((sum, [, count]) => sum + count, 0),
          foods: Object.entries(parentTypeCounts)
            .filter(([key]) => key.includes("Food"))
            .reduce((sum, [, count]) => sum + count, 0),

          // Members
          compounds: Object.entries(memberTypeCounts)
            .filter(([key]) => key.toLowerCase().includes("small molecule") || key.toLowerCase().includes("compound"))
            .reduce((sum, [, count]) => sum + count, 0),
          members: Object.entries(memberTypeCounts)
            .filter(([key]) => !key.toLowerCase().includes("small molecule") && !key.toLowerCase().includes("compound"))
            .reduce((sum, [, count]) => sum + count, 0),
        });
      } catch (error) {
        console.error("Error loading tab counts:", error);
      }
    }

    loadTabCounts();
  }, [selectedEntityIds]);

  // Calculate which tabs have results
  const tabsWithResults = useMemo(() => {
    // Check if any selected entities have CV terms
    const hasCvTerms = selectedEntities.some(e => {
      const cvTerms = e.cv_terms || e.fullResult?.cv_terms || [];
      return cvTerms.length > 0;
    });

    return {
      interactions: true, // Always show interactions
      complexes: tabCounts.complexes > 0,
      cv_terms: hasCvTerms,
      pathways: tabCounts.pathways > 0,
      reactions: tabCounts.reactions > 0,
      foods: tabCounts.foods > 0,
      compounds: tabCounts.compounds > 0,
      members: tabCounts.members > 0,
    };
  }, [tabCounts, selectedEntities]);

  // Handlers for interactions filters
  const handleInteractionsFilterChange = useCallback((newFilters: MeilisearchFilters) => {
    setInteractionsFilters(newFilters);
  }, []);

  const handleInteractionsClearFilters = useCallback(() => {
    setInteractionsFilters({});
  }, []);

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
              {tabsWithResults.complexes && <TabsTrigger value="complexes">Complexes</TabsTrigger>}
              {tabsWithResults.cv_terms && <TabsTrigger value="cv_terms">CV Terms</TabsTrigger>}
              {tabsWithResults.pathways && <TabsTrigger value="pathways">Pathways</TabsTrigger>}
              {tabsWithResults.reactions && <TabsTrigger value="reactions">Reactions</TabsTrigger>}
              {tabsWithResults.foods && <TabsTrigger value="foods">Foods</TabsTrigger>}
              {tabsWithResults.compounds && <TabsTrigger value="compounds">Compounds</TabsTrigger>}
              {tabsWithResults.members && <TabsTrigger value="members">Members</TabsTrigger>}
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
                <AssociationsExploreTab
                  mode="parents"
                  parentEntityType={ENTITY_TYPES.COMPLEX}
                />
              </TabsContent>
            )}

            {tabsWithResults.cv_terms && (
              <TabsContent value="cv_terms" className="mt-0">
                <AnnotationsExploreTab />
              </TabsContent>
            )}

            {tabsWithResults.pathways && (
              <TabsContent value="pathways" className="mt-0">
                <AssociationsExploreTab
                  mode="parents"
                  parentEntityType={ENTITY_TYPES.PATHWAY}
                />
              </TabsContent>
            )}

            {tabsWithResults.reactions && (
              <TabsContent value="reactions" className="mt-0">
                <AssociationsExploreTab
                  mode="parents"
                  parentEntityType={ENTITY_TYPES.REACTION}
                />
              </TabsContent>
            )}

            {tabsWithResults.foods && (
              <TabsContent value="foods" className="mt-0">
                <AssociationsExploreTab
                  mode="parents"
                  parentEntityType={ENTITY_TYPES.FOOD}
                />
              </TabsContent>
            )}

            {tabsWithResults.compounds && (
              <TabsContent value="compounds" className="mt-0">
                <AssociationsExploreTab
                  mode="members"
                  memberEntityType="Small Molecule" // Filters loosely
                />
              </TabsContent>
            )}

            {tabsWithResults.members && (
              <TabsContent value="members" className="mt-0">
                <AssociationsExploreTab
                  mode="members"
                />
              </TabsContent>
            )}
          </Tabs>
        </div>
      </div>
    </div>
  );
}
