"use client";

import { useSidebarContent } from "@/contexts/sidebar-content-context";
import { useEffect, useMemo, useState } from "react";
import { useEntitySelection } from "@/contexts/entity-selection-context";
import { searchAssociationsMeilisearch } from "@/lib/meilisearch/search";
import SearchPage from "@/features/search/page";

// Component to fetch all associated entity IDs and render SearchPage
function AssociatedEntitiesSearch({
    selectedEntityIds
}: {
    selectedEntityIds: number[];
}) {
    const [associatedEntityIds, setAssociatedEntityIds] = useState<number[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        async function fetchAssociatedEntityIds() {
            if (selectedEntityIds.length === 0) {
                setAssociatedEntityIds([]);
                setLoading(false);
                return;
            }

            setLoading(true);
            try {
                // Query associations in both directions
                // 1. Find parents (where selected entities are members)
                // 2. Find members (where selected entities are parents)
                const [parentsResponse, membersResponse] = await Promise.all([
                    searchAssociationsMeilisearch({
                        query: "",
                        index: 'search_associations' as any,
                        limit: 10000,
                        offset: 0,
                        filters: { member_entity_ids: selectedEntityIds }
                    }),
                    searchAssociationsMeilisearch({
                        query: "",
                        index: 'search_associations' as any,
                        limit: 10000,
                        offset: 0,
                        filters: { parent_entity_ids: selectedEntityIds }
                    })
                ]);

                // Extract unique entity IDs from both queries
                const entityIds = new Set<number>();

                // Add parent entity IDs
                (parentsResponse.hits as any[]).forEach(hit => {
                    if (hit.parent_entity_id) entityIds.add(hit.parent_entity_id);
                });

                // Add member entity IDs
                (membersResponse.hits as any[]).forEach(hit => {
                    if (hit.member_entity_id) entityIds.add(hit.member_entity_id);
                });

                setAssociatedEntityIds(Array.from(entityIds));
            } catch (error) {
                console.error("Error fetching associated entity IDs:", error);
                setAssociatedEntityIds([]);
            } finally {
                setLoading(false);
            }
        }

        fetchAssociatedEntityIds();
    }, [selectedEntityIds]);

    if (loading) {
        return (
            <div className="flex items-center justify-center py-12">
                <div className="animate-pulse text-muted-foreground">Loading associated entities...</div>
            </div>
        );
    }

    if (associatedEntityIds.length === 0) {
        return (
            <div className="flex items-center justify-center py-12">
                <p className="text-muted-foreground">
                    No associated entities found
                </p>
            </div>
        );
    }

    // Render SearchPage with the associated entity IDs as filter
    return (
        <SearchPage
            embedded={true}
            initialQuery=""
            initialSearchType="search_entities"
            initialFilters={{ entity_ids: associatedEntityIds }}
            showFilters={true}
        />
    );
}

export default function AssociationsPage() {
    const { setSidebarContent } = useSidebarContent();
    const { selectedEntities } = useEntitySelection();

    // Get selected entity IDs
    const selectedEntityIds = useMemo(() =>
        selectedEntities
            .map(e => e.entityId || parseInt(e.id, 10))
            .filter(id => !isNaN(id)),
        [selectedEntities]
    );

    // Cleanup sidebar content on unmount
    useEffect(() => {
        return () => {
            setSidebarContent(null);
        };
    }, [setSidebarContent]);

    // Show empty state when no entities selected
    if (selectedEntities.length === 0) {
        return (
            <div className="flex-1 flex flex-col">
                <div className="flex-1 flex items-center justify-center">
                    <p className="text-muted-foreground">
                        Select entities to see associated entities (complexes, pathways, reactions, etc.)
                    </p>
                </div>
            </div>
        );
    }

    return (
        <div className="flex-1 flex flex-col overflow-hidden">
            <AssociatedEntitiesSearch selectedEntityIds={selectedEntityIds} />
        </div>
    );
}
