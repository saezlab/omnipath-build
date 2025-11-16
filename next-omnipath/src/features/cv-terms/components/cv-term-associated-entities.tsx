"use client";

import { useCallback } from "react";
import { SearchResults } from "@/features/search/components/search-results";
import { useInfiniteScroll } from "@/hooks/use-infinite-scroll";
import type { SearchResult } from "@/features/search/components/result-card";
import { fetchMeilisearchDocuments } from "@/lib/meilisearch/search";

interface CvTermAssociatedEntitiesProps {
  entityIds: string[];
}

export function CvTermAssociatedEntities({ entityIds }: CvTermAssociatedEntitiesProps) {
  // Fetch function for infinite scroll
  const fetchAssociatedEntities = useCallback(
    async (offset: number, limit: number) => {
      if (!entityIds || entityIds.length === 0) {
        return { results: [], totalResults: 0 };
      }

      // Get a slice of entity IDs based on offset and limit
      const idsToFetch = entityIds.slice(offset, offset + limit);
      
      if (idsToFetch.length === 0) {
        return { results: [], totalResults: entityIds.length };
      }

      try {
        const response = await fetchMeilisearchDocuments('entities', idsToFetch);
        const documents = response.documents as SearchResult[];
        
        return {
          results: documents || [],
          totalResults: entityIds.length
        };
      } catch (error) {
        console.error('Error fetching associated entities:', error);
        return { results: [], totalResults: entityIds.length };
      }
    },
    [entityIds]
  );

  const {
    data: associatedEntities,
    loading,
    loadingMore,
    hasMore,
    sentinelRef,
    error
  } = useInfiniteScroll<SearchResult>({
    fetchData: fetchAssociatedEntities,
    pageSize: 20,
    dependencies: [entityIds]
  });

  if (error) {
    return (
      <div className="text-red-500">
        Error loading associated entities: {error.message}
      </div>
    );
  }

  return (
    <SearchResults 
      results={associatedEntities} 
      loading={loading}
      loadingMore={loadingMore}
      hasMore={hasMore}
      sentinelRef={sentinelRef}
    />
  );
}