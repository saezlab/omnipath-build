"use client";

import { useState, useCallback, useMemo, useEffect } from "react";
import { searchMeilisearch } from "@/lib/meilisearch/search";
import { INDEXES } from "@/lib/meilisearch/client";
import { SearchResults } from "@/features/search/components/search-results";
import type { SearchResult } from "@/features/search/components/result-card";
import { useInfiniteScroll } from "@/hooks/use-infinite-scroll";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useEntitySelection, type SelectedEntity } from "@/contexts/entity-selection-context";

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

interface RelatedEntitiesTabProps {
  /**
   * Type of related entities to show: "complex", "cv_term", or "reference"
   */
  relatedType: "complex" | "cv_term" | "reference";
  /**
   * Current filters
   */
  filters?: EntityFilters;
  /**
   * Callback when filters change
   */
  onFilterChange?: (filters: EntityFilters) => void;
  /**
   * Callback to provide filter counts to parent
   */
  onFilterCountsUpdate?: (counts: EntityFilterCounts) => void;
}

/**
 * Aggregates related IDs from selected entities and counts frequency
 */
function aggregateRelatedIds<T extends string | number>(
  entities: SelectedEntity[],
  getter: (entity: SelectedEntity) => T[] | undefined
): Map<T, number> {
  const frequencyMap = new Map<T, number>();

  for (const entity of entities) {
    const ids = getter(entity);
    if (ids) {
      for (const id of ids) {
        frequencyMap.set(id, (frequencyMap.get(id) || 0) + 1);
      }
    }
  }

  return frequencyMap;
}

export function RelatedEntitiesTab({
  relatedType,
  filters = {},
  onFilterChange: _onFilterChange,
  onFilterCountsUpdate
}: RelatedEntitiesTabProps) {
  // Note: onFilterChange is passed but filtering is handled by parent via sidebar
  void _onFilterChange;
  const { selectedEntities } = useEntitySelection();
  const [error, setError] = useState<string | null>(null);

  // Aggregate related IDs from selected entities based on type
  const relatedIdFrequencies = useMemo(() => {
    switch (relatedType) {
      case "complex":
        return aggregateRelatedIds(selectedEntities, e => e.complexes);
      case "cv_term":
        return aggregateRelatedIds(selectedEntities, e => e.cv_terms);
      case "reference":
        return aggregateRelatedIds(selectedEntities, e => e.references);
      default:
        return new Map<string | number, number>();
    }
  }, [selectedEntities, relatedType]);

  // Sort IDs by frequency (descending)
  const sortedIds = useMemo(() => {
    return Array.from(relatedIdFrequencies.entries())
      .sort((a, b) => b[1] - a[1])
      .map(([id]) => id);
  }, [relatedIdFrequencies]);

  // Fetch function for infinite scroll - uses searchMeilisearch with entity_ids filter
  const fetchRelatedData = useCallback(
    async (offset: number, limit: number) => {
      // If no related IDs found, return empty
      if (sortedIds.length === 0) {
        return { results: [], totalResults: 0 };
      }

      try {
        // Combine entity_ids with other filters
        // Convert string IDs to numbers (references come as strings from the database)
        const numericIds = sortedIds
          .map(id => typeof id === 'string' ? parseInt(id, 10) : id)
          .filter(id => !isNaN(id));

        if (numericIds.length === 0) {
          return { results: [], totalResults: 0 };
        }

        const combinedFilters = {
          ...filters,
          entity_ids: numericIds,
        };

        const response = await searchMeilisearch({
          query: "",
          index: INDEXES.ENTITIES,
          limit,
          offset,
          filters: combinedFilters,
        });

        let hits = response.hits as SearchResult[];

        // Sort results by frequency
        // Try both number and string keys since references come as strings
        hits.sort((a, b) => {
          const idA = a.entity_id || Number(a.id);
          const idB = b.entity_id || Number(b.id);
          const freqA = relatedIdFrequencies.get(idA) || relatedIdFrequencies.get(String(idA)) || 0;
          const freqB = relatedIdFrequencies.get(idB) || relatedIdFrequencies.get(String(idB)) || 0;
          return freqB - freqA;
        });

        // Update filter counts on first page
        if (offset === 0 && response.facetDistribution && onFilterCountsUpdate) {
          onFilterCountsUpdate({
            entity_type: response.facetDistribution.entity_type || {},
            sources: response.facetDistribution.sources || {},
            ncbi_tax_id: response.facetDistribution.ncbi_tax_id || {},
          });
        }

        return {
          results: hits,
          totalResults: response.estimatedTotalHits || hits.length
        };
      } catch (err) {
        console.error('Error fetching related entities:', err);
        setError(err instanceof Error ? err.message : 'Failed to fetch data');
        return { results: [], totalResults: 0 };
      }
    },
    [sortedIds, filters, relatedIdFrequencies, onFilterCountsUpdate]
  );

  // Use infinite scroll
  const {
    data: results,
    loading,
    loadingMore,
    hasMore,
    sentinelRef
  } = useInfiniteScroll<SearchResult>({
    fetchData: fetchRelatedData,
    pageSize: 20,
    dependencies: [sortedIds, relatedType, filters]
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
    return (
      <div className="flex items-center justify-center py-12">
        <p className="text-muted-foreground">
          Select entities to see related {relatedType === "cv_term" ? "CV terms" : relatedType + "es"}
        </p>
      </div>
    );
  }

  // Show empty state if no related items found
  if (sortedIds.length === 0) {
    return (
      <div className="flex items-center justify-center py-12">
        <p className="text-muted-foreground">
          No {relatedType === "cv_term" ? "CV terms" : relatedType + "es"} found for the selected entities
        </p>
      </div>
    );
  }

  const typeLabel = relatedType === "cv_term" ? "CV term" : relatedType;
  const typeLabelPlural = relatedType === "cv_term" ? "CV terms" : relatedType + "es";

  return (
    <div className="space-y-4">
      <div className="text-sm text-muted-foreground">
        Found {sortedIds.length} {sortedIds.length === 1 ? typeLabel : typeLabelPlural} across {selectedEntities.length} selected entit{selectedEntities.length !== 1 ? 'ies' : 'y'}
      </div>
      <SearchResults
        results={results}
        loading={loading}
        loadingMore={loadingMore}
        hasMore={hasMore}
        sentinelRef={sentinelRef}
      />
    </div>
  );
}
