"use client";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useSidebarContent } from "@/contexts/sidebar-content-context";
import { useCallback, useEffect, useState, useMemo } from "react";
import { AssociationsExploreTab } from "./components/associations-explore-tab";
import { useEntitySelection } from "@/contexts/entity-selection-context";
import { searchAssociationsMeilisearch } from "@/lib/meilisearch/search";

// Entity type accessions for filtering associations
const ENTITY_TYPES = {
    COMPLEX: "Complex:OM:0002",
    PATHWAY: "Pathway:OM:0004",
    REACTION: "Reaction:OM:0005",
    FOOD: "Food:OM:0007",
    SMALL_MOLECULE: "Small Molecule:OM:0003",
    PROTEIN: "Protein:OM:0001",
} as const;

export default function AssociationsPage() {
    const [activeTab, setActiveTab] = useState("complexes");
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
    const [tabCountsLoading, setTabCountsLoading] = useState(false);

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
                setTabCounts({ complexes: 0, pathways: 0, reactions: 0, foods: 0, compounds: 0, members: 0 });
                return;
            }

            setTabCountsLoading(true);
            try {
                // Query 1: Find PARENTS (where selected entities are members)
                const parentsPromise = searchAssociationsMeilisearch({
                    query: "",
                    index: 'search_associations' as any,
                    limit: 0,
                    offset: 0,
                    filters: {
                        member_entity_ids: selectedEntityIds,
                    },
                });

                // Query 2: Find MEMBERS (where selected entities are parents)
                const membersPromise = searchAssociationsMeilisearch({
                    query: "",
                    index: 'search_associations' as any,
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
            } finally {
                setTabCountsLoading(false);
            }
        }

        loadTabCounts();
    }, [selectedEntityIds]);

    // Calculate which tabs have results
    const tabsWithResults = useMemo(() => ({
        complexes: tabCounts.complexes > 0,
        pathways: tabCounts.pathways > 0,
        reactions: tabCounts.reactions > 0,
        foods: tabCounts.foods > 0,
        compounds: tabCounts.compounds > 0,
        members: tabCounts.members > 0,
    }), [tabCounts]);

    // Clear sidebar content for this page (filter handled in tabs)
    useEffect(() => {
        setSidebarContent(null);
        return () => {
            setSidebarContent(null);
        };
    }, [setSidebarContent]);

    // Check if any tabs are available
    const hasAnyTabs = Object.values(tabsWithResults).some(v => v);

    // Show empty state when no entities selected
    if (selectedEntities.length === 0) {
        return (
            <div className="flex-1 flex flex-col">
                <div className="flex-1 flex items-center justify-center">
                    <p className="text-muted-foreground">
                        Select entities to see associations (complexes, pathways, reactions, etc.)
                    </p>
                </div>
            </div>
        );
    }

    // Show loading state
    if (tabCountsLoading) {
        return (
            <div className="flex-1 flex flex-col">
                <div className="flex-1 flex items-center justify-center">
                    <div className="animate-pulse text-muted-foreground">Loading associations...</div>
                </div>
            </div>
        );
    }

    // Show empty state when no associations found
    if (!hasAnyTabs) {
        return (
            <div className="flex-1 flex flex-col">
                <div className="flex-1 flex items-center justify-center">
                    <p className="text-muted-foreground">
                        No associations found for the selected entities
                    </p>
                </div>
            </div>
        );
    }

    return (
        <div className="flex-1 flex flex-col">
            <div className="sticky top-0 z-10 bg-background border-b">
                <div className="w-full max-w-screen-xl mx-auto px-4 py-4">
                    <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
                        <TabsList>
                            {tabsWithResults.complexes && <TabsTrigger value="complexes">Complexes</TabsTrigger>}
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
                        {tabsWithResults.complexes && (
                            <TabsContent value="complexes" className="mt-0">
                                <AssociationsExploreTab
                                    mode="parents"
                                    parentEntityType={ENTITY_TYPES.COMPLEX}
                                />
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
                                    memberEntityType="Small Molecule"
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
