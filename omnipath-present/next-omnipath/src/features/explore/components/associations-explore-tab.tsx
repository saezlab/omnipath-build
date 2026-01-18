"use client";

import { useState, useCallback, useEffect, useMemo } from "react";
import { searchAssociationsMeilisearch } from "@/lib/meilisearch/search";
import { useInfiniteScroll } from "@/hooks/use-infinite-scroll";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useEntitySelection } from "@/contexts/entity-selection-context";
import type { MeilisearchFilters, MeilisearchAssociation } from "@/types/meilisearch";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { EntityBadge } from "@/components/entity-badge";

interface AssociationFilters {
    parent_entity_types?: string[];
    member_entity_types?: string[];
    sources?: string[];
    association_annotation_terms?: string[];
}

interface AssociationFilterCounts {
    parent_entity_type?: Record<string, number>;
    member_entity_type?: Record<string, number>;
    sources?: Record<string, number>;
    association_annotation_terms?: Record<string, number>;
}

interface AssociationsExploreTabProps {
    /**
     * Mode: 'parents' shows parent entities for selected members, 'members' shows member entities for selected parents
     */
    mode?: 'parents' | 'members';
    /**
     * Filter by parent entity type (e.g., "Complex:OM:0002", "Food:OM:0007")
     */
    parentEntityType?: string;
    /**
     * Filter by member entity type (e.g., "Protein:OM:0001", "Small Molecule:OM:0003")
     */
    memberEntityType?: string;
    /**
     * Current filters
     */
    filters?: AssociationFilters;
    /**
     * Callback when filters change
     */
    onFilterChange?: (filters: AssociationFilters) => void;
    /**
     * Callback to provide filter counts to parent
     */
    onFilterCountsUpdate?: (counts: AssociationFilterCounts) => void;
}

/**
 * Association result card component
 */
function AssociationCard({ association, mode }: { association: MeilisearchAssociation; mode: 'parents' | 'members' }) {
    // Show the "other" side based on mode
    const displayEntity = mode === 'parents'
        ? {
            name: association.parent_name,
            type: association.parent_entity_type,
            id: association.parent_entity_id,
            identifiers: association.parent_identifiers
        }
        : {
            name: association.member_name,
            type: association.member_entity_type,
            id: association.member_entity_id,
            identifiers: association.member_identifiers
        };

    return (
        <Card className="p-4 hover:bg-muted/50 transition-colors">
            <div className="flex items-start justify-between gap-4">
                <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                        <EntityBadge
                            displayName={displayEntity.name || `Entity ${displayEntity.id}`}
                            canonicalIdentifier={String(displayEntity.id)}
                            entityType={displayEntity.type}
                            showHover={false}
                        />          </div>

                    {/* Show a few key identifiers */}
                    <div className="flex flex-wrap gap-1 mt-2">
                        {displayEntity.identifiers?.slice(0, 3).map((id, idx) => (
                            <Badge key={idx} variant="outline" className="text-xs">
                                {id.key}: {id.value}
                            </Badge>
                        ))}
                        {(displayEntity.identifiers?.length || 0) > 3 && (
                            <Badge variant="outline" className="text-xs text-muted-foreground">
                                +{displayEntity.identifiers!.length - 3} more
                            </Badge>
                        )}
                    </div>

                    {/* Show annotations if any */}
                    {association.annotations?.length > 0 && (
                        <div className="flex flex-wrap gap-1 mt-2">
                            {association.annotations.slice(0, 2).map((ann, idx) => (
                                <Badge key={idx} variant="secondary" className="text-xs">
                                    {ann.key}: {ann.value}{ann.unit ? ` ${ann.unit}` : ''}
                                </Badge>
                            ))}
                        </div>
                    )}
                </div>

                {/* Sources */}
                <div className="flex flex-wrap gap-1 justify-end">
                    {association.sources?.slice(0, 2).map((source, idx) => (
                        <Badge key={idx} variant="outline" className="text-xs">
                            {source.split(':')[0]}
                        </Badge>
                    ))}
                </div>
            </div>
        </Card>
    );
}

export function AssociationsExploreTab({
    mode = 'parents',
    parentEntityType,
    memberEntityType,
    filters,
    onFilterChange: _onFilterChange,
    onFilterCountsUpdate
}: AssociationsExploreTabProps) {
    void _onFilterChange;
    const { selectedEntities } = useEntitySelection();
    const [error, setError] = useState<string | null>(null);

    // Get selected entity IDs
    const selectedEntityIds = useMemo(() =>
        selectedEntities
            .map(e => e.entityId || parseInt(e.id, 10))
            .filter(id => !isNaN(id)),
        [selectedEntities]
    );

    // Build filters based on mode and selected entities
    const buildQueryFilters = useCallback((): MeilisearchFilters => {
        const queryFilters: MeilisearchFilters = { ...(filters || {}) };

        if (mode === 'parents') {
            // Find parents of selected members
            queryFilters.member_entity_ids = selectedEntityIds;
            if (parentEntityType) {
                queryFilters.parent_entity_types = [parentEntityType];
            }
        } else {
            // Find members of selected parents
            queryFilters.parent_entity_ids = selectedEntityIds;
            if (memberEntityType) {
                queryFilters.member_entity_types = [memberEntityType];
            }
        }

        return queryFilters;
    }, [selectedEntityIds, mode, parentEntityType, memberEntityType, filters]);

    // Fetch function for infinite scroll
    const fetchAssociations = useCallback(
        async (offset: number, limit: number) => {
            if (selectedEntityIds.length === 0) {
                return { results: [], totalResults: 0 };
            }

            try {
                const queryFilters = buildQueryFilters();

                const response = await searchAssociationsMeilisearch({
                    query: "",
                    index: 'search_associations' as any,
                    limit,
                    offset,
                    filters: queryFilters,
                });

                const hits = response.hits as unknown as MeilisearchAssociation[];

                // Update filter counts on first page
                if (offset === 0 && response.facetDistribution && onFilterCountsUpdate) {
                    onFilterCountsUpdate({
                        parent_entity_type: response.facetDistribution.parent_entity_type || {},
                        member_entity_type: response.facetDistribution.member_entity_type || {},
                        sources: response.facetDistribution.sources || {},
                        association_annotation_terms: response.facetDistribution.association_annotation_terms || {},
                    });
                }

                return {
                    results: hits,
                    totalResults: response.estimatedTotalHits || hits.length
                };
            } catch (err) {
                console.error('Error fetching associations:', err);
                setError(err instanceof Error ? err.message : 'Failed to fetch data');
                return { results: [], totalResults: 0 };
            }
        },
        [selectedEntityIds, buildQueryFilters, onFilterCountsUpdate]
    );

    // Use infinite scroll
    const {
        data: results,
        loading,
        loadingMore,
        hasMore,
        sentinelRef
    } = useInfiniteScroll<MeilisearchAssociation>({
        fetchData: fetchAssociations,
        pageSize: 20,
        dependencies: [selectedEntityIds, mode, parentEntityType, memberEntityType, filters]
    });

    // Clear filter counts when no entities selected
    useEffect(() => {
        if (selectedEntities.length === 0 && onFilterCountsUpdate) {
            onFilterCountsUpdate({});
        }
    }, [selectedEntities.length, onFilterCountsUpdate]);

    // Show error if any
    if (error) {
        return (
            <div className="p-6">
                <Alert variant="destructive">
                    <AlertDescription>{error}</AlertDescription>
                </Alert>
            </div>
        );
    }

    // Show empty state if no entities selected
    if (selectedEntities.length === 0) {
        const typeLabel = parentEntityType
            ? parentEntityType.split(':')[0].toLowerCase() + 's'
            : mode === 'parents' ? 'parent entities' : 'member entities';
        return (
            <div className="flex items-center justify-center py-12">
                <p className="text-muted-foreground">
                    Select entities to see associated {typeLabel}
                </p>
            </div>
        );
    }

    // Show loading state
    if (loading) {
        return (
            <div className="flex items-center justify-center py-12">
                <div className="animate-pulse text-muted-foreground">Loading associations...</div>
            </div>
        );
    }

    // Show empty state if no results
    if (results.length === 0) {
        const typeLabel = parentEntityType
            ? parentEntityType.split(':')[0].toLowerCase() + 's'
            : mode === 'parents' ? 'parent entities' : 'member entities';
        return (
            <div className="flex items-center justify-center py-12">
                <p className="text-muted-foreground">
                    No {typeLabel} found for the selected entities
                </p>
            </div>
        );
    }

    const typeLabel = parentEntityType
        ? parentEntityType.split(':')[0].toLowerCase()
        : mode === 'parents' ? 'parent' : 'member';

    return (
        <div className="space-y-4">
            <div className="text-sm text-muted-foreground">
                Found {results.length} {typeLabel}{results.length !== 1 ? 's' : ''} across {selectedEntities.length} selected entit{selectedEntities.length !== 1 ? 'ies' : 'y'}
            </div>

            <div className="space-y-2">
                {results.map((association) => (
                    <AssociationCard
                        key={association.association_key}
                        association={association}
                        mode={mode}
                    />
                ))}
            </div>

            {/* Infinite scroll sentinel */}
            {hasMore && (
                <div ref={sentinelRef as React.RefObject<HTMLDivElement>} className="py-4 text-center">
                    {loadingMore && (
                        <div className="animate-pulse text-muted-foreground">Loading more...</div>
                    )}
                </div>
            )}
        </div>
    );
}
