"use client";
import React, { useState, useCallback, useTransition } from "react";
import { SearchBar } from "./components/search-bar";
import { SearchResults } from "./components/search-results";
import type { SearchResult } from "./components/result-card";
import { searchMeilisearch } from "./api/queries";
import { SiteLayout } from "@/components/layout/main-layout";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { InteractionsSearch } from "@/features/interactions-search/components/interactions-search";
import { useInfiniteScroll } from "@/hooks/use-infinite-scroll";
import { fetchMeilisearchDocuments } from "@/lib/meilisearch/search";
import { EntityFilterSidebar } from "./components/entity-filter-sidebar";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Filter, X } from "lucide-react";
import type { MeilisearchFilters } from "@/types/meilisearch";

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
  const [filters, setFilters] = useState<{ entity_types?: string[]; sources?: string[]; ncbi_tax_id?: string[] }>({});
  const [filterCounts, setFilterCounts] = useState<{ entity_type?: Record<string, number>; sources?: Record<string, number>; ncbi_tax_id?: Record<string, number> }>({});

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
        index: initialSearchType === "search_entities" ? "search_entities" : "cv_terms",
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
    setFilters({});
  }, []);

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
  const content = (
    <>
      {!embedded && (
        <div className="fixed top-10 left-0 right-0 z-10 p-4">
          <SearchBar
            placeholder="Search proteins, molecules, ontology terms…"
            onSearch={doSearch}
            initialQuery={query}
            autoFocus={false}
          />
        </div>
      )}


      {/* Results */}
      <div className={embedded ? "flex-1 overflow-y-auto p-4" : "transition-opacity duration-500 ease-in-out mb-10"}>
        <div className={embedded ? "w-full min-h-full" : "w-full max-w-screen-xl mx-auto px-4 sm:px-6 lg:px-8 pt-24"}>
            {isMultiSearch ? (
              <Tabs defaultValue="entities" className="w-full">
                <TabsList className="grid w-fit grid-cols-2 relative z-20">
                  <TabsTrigger value="entities">
                    Entities ({multiSearchResults.length})
                  </TabsTrigger>
                  <TabsTrigger value="interactions">
                    Interactions
                  </TabsTrigger>
                </TabsList>
                <TabsContent value="entities">
                  <SearchResults results={multiSearchResults} />
                </TabsContent>
                <TabsContent value="interactions" className="mt-6">
                  {entityIds.length > 0 && (
                    <InteractionsSearch
                      key={entityIds.join(',')}
                      initialEntityIds={entityIds}
                      hideEntityFilter={false}
                    />
                  )}
                </TabsContent>
              </Tabs>
            ) : (
              <div className="flex gap-6">
                {/* Filter Sidebar - Desktop */}
                {initialSearchType === "search_entities" && !embedded && (
                  <aside className="hidden lg:block w-80 flex-shrink-0">
                    {Object.keys(filterCounts).length > 0 && (
                      <EntityFilterSidebar
                        filters={filters}
                        filterCounts={filterCounts}
                        onFilterChange={handleFilterChange}
                        onClearFilters={handleClearFilters}
                      />
                    )}
                  </aside>
                )}

                {/* Results with Mobile Filter Button */}
                <div className="flex-1 min-w-0">
                  {/* Mobile filter drawer */}
                  {initialSearchType === "search_entities" && !embedded && Object.keys(filterCounts).length > 0 && (
                    <div className="lg:hidden mb-4">
                      <Sheet>
                        <SheetTrigger asChild>
                          <Button variant="outline" className="w-full">
                            <Filter className="h-4 w-4 mr-2" />
                            Filters
                            {(filters.entity_types?.length || 0) + (filters.sources?.length || 0) + (filters.ncbi_tax_id?.length || 0) > 0 && (
                              <Badge variant="secondary" className="ml-2">
                                {(filters.entity_types?.length || 0) + (filters.sources?.length || 0) + (filters.ncbi_tax_id?.length || 0)}
                              </Badge>
                            )}
                          </Button>
                        </SheetTrigger>
                        <SheetContent side="left" className="w-[85%] sm:w-[400px] p-0">
                          <SheetHeader className="px-6 py-4 border-b">
                            <div className="flex items-center justify-between">
                              <SheetTitle className="flex items-center gap-2">
                                <Filter className="h-5 w-5 text-primary" />
                                Filters
                              </SheetTitle>
                              {((filters.entity_types?.length || 0) + (filters.sources?.length || 0) + (filters.ncbi_tax_id?.length || 0) > 0) && (
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  onClick={handleClearFilters}
                                  className="flex items-center gap-1 text-muted-foreground hover:text-foreground"
                                >
                                  <X className="h-4 w-4" />
                                  Clear all
                                </Button>
                              )}
                            </div>
                          </SheetHeader>
                          <div className="h-[calc(100%-4rem)] overflow-y-auto p-6">
                            <EntityFilterSidebar
                              filters={filters}
                              filterCounts={filterCounts}
                              onFilterChange={handleFilterChange}
                              onClearFilters={handleClearFilters}
                              isMobile
                            />
                          </div>
                        </SheetContent>
                      </Sheet>
                    </div>
                  )}

                  <SearchResults
                    results={results}
                    loading={loading}
                    loadingMore={loadingMore}
                    hasMore={hasMore}
                    sentinelRef={sentinelRef}
                  />
                </div>
              </div>
            )}
          </div>
        </div>
    </>
  );

  return embedded ? (
    <div className="h-full flex flex-col overflow-hidden">
      {content}
    </div>
  ) : (
    <SiteLayout>
      {content}
    </SiteLayout>
  );
} 