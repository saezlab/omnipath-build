"use client";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useSidebarContent } from "@/contexts/sidebar-content-context";
import { useInfiniteScroll } from "@/hooks/use-infinite-scroll";
import { fetchMeilisearchDocuments } from "@/lib/meilisearch/search";
import { useCallback, useEffect, useState, useTransition } from "react";
import { searchMeilisearch } from "./api/queries";
import { EntityFilterSidebar } from "./components/entity-filter-sidebar";
import type { SearchResult } from "./components/result-card";
import { SearchBar } from "./components/search-bar";
import { SearchResults } from "./components/search-results";

interface SearchPageProps {
  // Props for embedded mode (like in AI dialogs)
  embedded?: boolean;
  initialQuery?: string;
  initialSearchType?: "search_entities" | "cv_terms";
}

export default function SearchPage({
  embedded = false,
  initialQuery = "",
  initialSearchType = "search_entities"
}: SearchPageProps = {}) {
  const [query, setQuery] = useState(initialQuery);
  const [, startTransition] = useTransition();
  const [isMultiSearch, setIsMultiSearch] = useState(false);
  const [entityIds, setEntityIds] = useState<string[]>([]);
  const [multiSearchResults, setMultiSearchResults] = useState<Array<SearchResult>>([]);
  const [selectedSpecies, setSelectedSpecies] = useState<string>("9606"); // Default to Human
  const [filters, setFilters] = useState<{ entity_types?: string[]; sources?: string[]; ncbi_tax_id?: string[] }>({ ncbi_tax_id: ["9606"] });
  const [filterCounts, setFilterCounts] = useState<{ entity_type?: Record<string, number>; sources?: Record<string, number>; ncbi_tax_id?: Record<string, number> }>({});
  const { setSidebarContent } = useSidebarContent();

  // Detect if query contains comma-separated identifiers
  const detectMultiSearch = (q: string): boolean => {
    return q.includes(',') && q.split(',').length > 1;
  };

  // Fetch function for infinite scroll
  const fetchSearchData = useCallback(
    async (offset: number, limit: number) => {
      if (isMultiSearch) {
        return { results: [], totalResults: 0 };
      }

      const response = await searchMeilisearch({
        query: query || "", // Allow empty query to fetch all results
        index: "search_entities",
        limit,
        offset,
        filters
      });

      // Update filter counts from facet distribution (only on first page)
      if (offset === 0 && 'facetDistribution' in response && response.facetDistribution && initialSearchType === "search_entities") {
        setFilterCounts({
          entity_type: response.facetDistribution.entity_type || {},
          sources: response.facetDistribution.sources || {},
          ncbi_tax_id: response.facetDistribution.ncbi_tax_id || {}
        });
      }

      // The API returns estimatedTotalHits for the total count
      const hits = response.hits as SearchResult[] || [];
      const estimatedTotalHits = ('estimatedTotalHits' in response ? response.estimatedTotalHits as number : 0) || hits.length || 0;

      return {
        results: hits,
        totalResults: estimatedTotalHits
      };
    },
    [query, isMultiSearch, initialSearchType, filters]
  );

  // Use infinite scroll hook for regular search
  const {
    data: results,
    loading,
    loadingMore,
    hasMore,
    sentinelRef
  } = useInfiniteScroll<SearchResult>({
    fetchData: fetchSearchData,
    pageSize: 20,
    dependencies: [query, isMultiSearch, initialSearchType, filters]
  });

  // Handlers for filters
  const handleFilterChange = useCallback((newFilters: { entity_types?: string[]; sources?: string[]; ncbi_tax_id?: string[] }) => {
    setFilters(newFilters);
  }, []);

  const handleClearFilters = useCallback(() => {
    setFilters({ ncbi_tax_id: [selectedSpecies] });
  }, [selectedSpecies]);

  // Handler for species change
  const handleSpeciesChange = useCallback((species: string) => {
    setSelectedSpecies(species);
    setFilters(prev => ({ ...prev, ncbi_tax_id: [species] }));
  }, []);

  // Set sidebar content when filter counts are available (not in embedded mode and not multi-search)
  useEffect(() => {
    if (!embedded && !isMultiSearch && initialSearchType === "search_entities" && Object.keys(filterCounts).length > 0) {
      setSidebarContent(
        <EntityFilterSidebar
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

    // Cleanup on unmount
    return () => {
      setSidebarContent(null);
    };
  }, [embedded, isMultiSearch, initialSearchType, filterCounts, filters, handleFilterChange, handleClearFilters, setSidebarContent]);

  // Debounced search - This will be passed directly to SearchBar's onSearch
  const doSearch = useCallback(
    (q: string) => {
      // Update query state when search is triggered by
      setQuery(q);
      const isMulti = detectMultiSearch(q);
      setIsMultiSearch(isMulti);

      if (isMulti) {
        startTransition(() => {
          // Multi-search: use documents endpoint with canonical identifiers
          const identifiers = q.split(',').map(id => id.trim()).filter(id => id.length > 0);

          // Fetch entities by canonical identifiers using documents endpoint
          fetchMeilisearchDocuments('search_entities', identifiers)
          .then(data => {
            const documents = data.documents as unknown[] || [];
            setMultiSearchResults(documents as SearchResult[]);

            // Get entity IDs for inter action search
            const ids = documents.map((entity: unknown) => (entity as SearchResult).id);
            setEntityIds(ids);
          })
          .catch(err => {
            console.error('Error fetching entities:', err);
            setMultiSearchResults([]);
            setEntityIds([]);
          });
        });
      } else {
        // For regular search, just update the query - infinite scroll hook will handle the rest
        setMultiSearchResults([]);
        setEntityIds([]);
      }
    },
    []
  );

  // Render content based on embedded mode
  return (
    <div className={embedded ? "h-full flex flex-col overflow-hidden" : "flex-1 flex flex-col"}>
      {!embedded && (
        <div className="sticky top-0 z-10 p-4">
          <div className="w-full max-w-screen-xl mx-auto">
            <SearchBar
              placeholder="Search proteins, molecules, ontology terms…"
              onSearch={doSearch}
              initialQuery={query}
              autoFocus={false}
              selectedSpecies={selectedSpecies}
              onSpeciesChange={handleSpeciesChange}
            />
          </div>
        </div>
      )}

      {/* Results */}
      <div className={embedded ? "flex-1 overflow-y-auto p-4" : "flex-1 overflow-y-auto"}>
        <div className={embedded ? "w-full min-h-full" : "w-full max-w-screen-xl mx-auto px-4 sm:px-6 lg:px-8 py-6"}>
            <SearchResults
              results={results}
              loading={loading}
              loadingMore={loadingMore}
              hasMore={hasMore}
              sentinelRef={sentinelRef}
            />
        </div>
      </div>
    </div>
  );
} 