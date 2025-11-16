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

interface SearchPageProps {
  // Props for embedded mode (like in AI dialogs)
  embedded?: boolean;
  initialQuery?: string;
  initialSearchType?: "entities" | "cv_terms";
}

export default function SearchPage({ 
  embedded = false, 
  initialQuery = "", 
  initialSearchType = "entities" 
}: SearchPageProps = {}) {
  const [query, setQuery] = useState(initialQuery);
  const [, startTransition] = useTransition();
  const [isMultiSearch, setIsMultiSearch] = useState(false);
  const [entityIds, setEntityIds] = useState<string[]>([]);
  const [multiSearchResults, setMultiSearchResults] = useState<Array<SearchResult>>([]);

  // Detect if query contains comma-separated identifiers
  const detectMultiSearch = (q: string): boolean => {
    return q.includes(',') && q.split(',').length > 1;
  };

  // Fetch function for infinite scroll
  const fetchSearchData = useCallback(
    async (offset: number, limit: number) => {
      if (!query || isMultiSearch) {
        return { results: [], totalResults: 0 };
      }
      
      const response = await searchMeilisearch({ 
        query, 
        index: initialSearchType === "entities" ? "entities" : "cv_terms",
        limit, 
        offset 
      });
      
      // The API returns estimatedTotalHits for the total count
      const hits = response.hits as SearchResult[] || [];
      const estimatedTotalHits = ('estimatedTotalHits' in response ? response.estimatedTotalHits as number : 0) || hits.length || 0;
      
      return {
        results: hits,
        totalResults: estimatedTotalHits
      };
    },
    [query, isMultiSearch, initialSearchType]
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
    dependencies: [query, isMultiSearch, initialSearchType]
  });

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
          fetchMeilisearchDocuments('entities', identifiers)
          .then(data => {
            const documents = data.documents as unknown[] || [];
            setMultiSearchResults(documents as SearchResult[]);
            
            // Get entity IDs for interaction search
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
            autoFocus={true}
          />
        </div>
      )}
      
      
      {/* Results */}
      {!!query && (
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
              <SearchResults 
                results={results} 
                loading={loading}
                loadingMore={loadingMore}
                hasMore={hasMore}
                sentinelRef={sentinelRef}
              />
            )}
          </div>
        </div>
      )}
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