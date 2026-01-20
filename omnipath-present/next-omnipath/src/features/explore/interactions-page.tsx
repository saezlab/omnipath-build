"use client";

import { useSidebarContent } from "@/contexts/sidebar-content-context";
import { useCallback, useEffect, useState, useMemo } from "react";
import { useSearchParams } from "next/navigation";
import { InteractionsExploreTab } from "./components/interactions-explore-tab";
import { FilterSidebar } from "@/features/interactions-search/components/filter-sidebar";
import { MeilisearchFilters } from "@/types/meilisearch";
import { useEntitySelection } from "@/contexts/entity-selection-context";

interface InteractionsPageProps {
    useEntityFilters?: boolean;
}

export default function InteractionsPage({ useEntityFilters = true }: InteractionsPageProps) {
    const { setSidebarContent } = useSidebarContent();
    const searchParams = useSearchParams();
    const { selectedEntities } = useEntitySelection();

    // Get selected entity IDs from context
    const selectedEntityIds = useMemo(() =>
        selectedEntities
            .map(e => e.entityId || parseInt(e.id, 10))
            .filter(id => !isNaN(id)),
        [selectedEntities]
    );

    // Parse entity IDs from URL params
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

    // Interactions filter state - use URL params first, then fall back to entity selection context
    const [filters, setFilters] = useState<MeilisearchFilters>(() => {
        if (!useEntityFilters) {
            return {};
        }
        const urlEntityIds = parseEntityIds();
        if (urlEntityIds?.length) {
            return { entity_ids: urlEntityIds };
        }
        if (selectedEntityIds.length > 0) {
            return { entity_ids: selectedEntityIds };
        }
        return {};
    });
    const [filterCounts, setFilterCounts] = useState<Record<string, Record<string, number>>>({});

    // Handlers for interactions filters
    const handleFilterChange = useCallback((newFilters: MeilisearchFilters) => {
        setFilters(newFilters);
    }, []);

    const handleClearFilters = useCallback(() => {
        setFilters({});
    }, []);

    const handleFilterCountsUpdate = useCallback((counts: Record<string, Record<string, number>>) => {
        setFilterCounts(counts);
    }, []);

    // Sync URL params and entity selection context with filter state
    useEffect(() => {
        if (!useEntityFilters) {
            return;
        }
        const urlEntityIds = parseEntityIds();
        if (urlEntityIds?.length) {
            // URL params take priority
            setFilters(prev => ({
                ...prev,
                entity_ids: urlEntityIds,
                member_a_id: undefined
            }));
        } else if (selectedEntityIds.length > 0) {
            // Fall back to entity selection context
            setFilters(prev => ({
                ...prev,
                entity_ids: selectedEntityIds,
                member_a_id: undefined
            }));
        }
    }, [searchParams, parseEntityIds, selectedEntityIds, useEntityFilters]);

    // Set sidebar content
    useEffect(() => {
        if (Object.keys(filterCounts).length > 0) {
            setSidebarContent(
                <FilterSidebar
                    filters={filters}
                    filterCounts={filterCounts}
                    onFilterChange={handleFilterChange}
                    onClearFilters={handleClearFilters}
                    isMobile
                />
            );
        } else {
            setSidebarContent(null);
        }

        return () => {
            setSidebarContent(null);
        };
    }, [filters, filterCounts, handleFilterChange, handleClearFilters, setSidebarContent]);

    return (
        <div className="flex-1 flex flex-col">
            <div className="flex-1 overflow-y-auto">
                <div className="w-full max-w-screen-xl mx-auto px-4 py-6">
                    <InteractionsExploreTab
                        filters={filters}
                        onFilterChange={handleFilterChange}
                        onFilterCountsUpdate={handleFilterCountsUpdate}
                    />
                </div>
            </div>
        </div>
    );
}
