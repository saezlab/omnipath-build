"use client";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useSidebarContent } from "@/contexts/sidebar-content-context";
import { useInfiniteScroll } from "@/hooks/use-infinite-scroll";
import { useCallback, useEffect, useState, useTransition } from "react";
import { searchMeilisearch } from "./api/queries";
import { EntityFilterSidebar } from "./components/entity-filter-sidebar";
import type { SearchResult } from "./components/result-card";
import { SearchBar } from "./components/search-bar";
import { SearchResults } from "./components/search-results";
import { IdentifierMatches, type IdentifierMatch } from "./components/identifier-matches";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

interface SearchPageProps {
  // Props for embedded mode (like in AI dialogs)
  embedded?: boolean;
  initialQuery?: string;
  initialSearchType?: "search_entities" | "cv_terms";
}

type SearchMode = "full-text" | "identifier" | "batch";

export default function SearchPage({
  embedded = false,
  initialQuery = "",
  initialSearchType = "search_entities"
}: SearchPageProps = {}) {
  const [query, setQuery] = useState(initialQuery);
  const [, startTransition] = useTransition();
  const [searchMode, setSearchMode] = useState<SearchMode>("full-text");
  const [selectedSpecies, setSelectedSpecies] = useState<string>("9606"); // Default to Human
  const [filters, setFilters] = useState<{ entity_types?: string[]; sources?: string[]; ncbi_tax_id?: string[] }>({ ncbi_tax_id: ["9606"] });
  const [filterCounts, setFilterCounts] = useState<{ entity_type?: Record<string, number>; sources?: Record<string, number>; ncbi_tax_id?: Record<string, number> }>({});
  const [lookupMatches, setLookupMatches] = useState<IdentifierMatch[]>([]);
  const [lookupEntities, setLookupEntities] = useState<SearchResult[]>([]);
  const [lookupError, setLookupError] = useState<string | null>(null);
  const [lookupLoading, setLookupLoading] = useState(false);
  const [identifierInput, setIdentifierInput] = useState("");
  const [batchInput, setBatchInput] = useState("");
  const { setSidebarContent } = useSidebarContent();

  // Fetch function for infinite scroll
  const fetchSearchData = useCallback(
    async (offset: number, limit: number) => {
      if (searchMode !== "full-text") {
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
    [query, searchMode, initialSearchType, filters]
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
    dependencies: [query, searchMode, initialSearchType, filters]
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
    if (!embedded && searchMode === "full-text" && initialSearchType === "search_entities" && Object.keys(filterCounts).length > 0) {
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
  }, [embedded, searchMode, initialSearchType, filterCounts, filters, handleFilterChange, handleClearFilters, setSidebarContent]);

  // Clear identifier results when returning to full-text mode
  useEffect(() => {
    if (searchMode === "full-text") {
      setLookupMatches([]);
      setLookupEntities([]);
      setLookupError(null);
    }
  }, [searchMode]);

  // Debounced search - This will be passed directly to SearchBar's onSearch
  const doSearch = useCallback((q: string) => {
    setQuery(q);
  }, []);

  // Identifier lookup helpers
  const runLookup = useCallback(async (identifiers: string[]) => {
    setLookupLoading(true);
    setLookupError(null);
    try {
      const response = await fetch("/api/entity-lookup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ identifiers }),
      });

      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.error || `Lookup failed with status ${response.status}`);
      }

      const data = await response.json();
      setLookupMatches((data.matches || []) as IdentifierMatch[]);
      setLookupEntities((data.entities || []) as SearchResult[]);
    } catch (err) {
      console.error("Identifier lookup error", err);
      setLookupMatches([]);
      setLookupEntities([]);
      setLookupError(err instanceof Error ? err.message : "Lookup failed");
    } finally {
      setLookupLoading(false);
    }
  }, []);

  const handleIdentifierLookup = useCallback(() => {
    const trimmed = identifierInput.trim();
    if (!trimmed) {
      setLookupError("Please enter an identifier to look up.");
      return;
    }
    startTransition(() => runLookup([trimmed]));
  }, [identifierInput, runLookup]);

  const handleBatchLookup = useCallback(() => {
    const ids = batchInput
      .split(/[\n,]/)
      .map((id) => id.trim())
      .filter((id) => id.length > 0);

    if (ids.length === 0) {
      setLookupError("Please enter at least one identifier.");
      return;
    }
    startTransition(() => runLookup(ids));
  }, [batchInput, runLookup]);

  // Render content based on embedded mode
  return (
    <div className={embedded ? "h-full flex flex-col overflow-hidden" : "flex-1 flex flex-col"}>
      {!embedded && (
        <div className="sticky top-0 z-10 p-4 bg-background/95 backdrop-blur">
          <div className="w-full max-w-screen-xl mx-auto space-y-3">
            {/* Tabs and Search Bar on same row */}
            <div className="flex items-center gap-4">
              <Tabs
                value={searchMode}
                onValueChange={(value) => {
                  setSearchMode(value as SearchMode);
                  setLookupError(null);
                }}
              >
                <TabsList>
                  <TabsTrigger value="full-text">Full text</TabsTrigger>
                  <TabsTrigger value="identifier">Identifier lookup</TabsTrigger>
                  <TabsTrigger value="batch">Batch identifiers</TabsTrigger>
                </TabsList>
              </Tabs>

              {searchMode === "full-text" && (
                <div className="flex-1">
                  <SearchBar
                    placeholder="Search proteins, molecules, ontology terms…"
                    onSearch={doSearch}
                    initialQuery={query}
                    autoFocus={false}
                    selectedSpecies={selectedSpecies}
                    onSpeciesChange={handleSpeciesChange}
                  />
                </div>
              )}
            </div>

            {searchMode === "identifier" && (
              <div className="flex flex-col gap-3 rounded-xl border bg-muted/40 p-4">
                <div className="flex items-center gap-3">
                  <Input
                    placeholder="Enter one identifier (e.g. UniProt, gene symbol, etc.)"
                    value={identifierInput}
                    onChange={(e) => setIdentifierInput(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && handleIdentifierLookup()}
                  />
                  <Button onClick={handleIdentifierLookup} disabled={lookupLoading}>
                    Look up
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                  Uses the entity service at localhost:8080 to resolve identifiers and fetches entity details from Meilisearch.
                </p>
              </div>
            )}

            {searchMode === "batch" && (
              <div className="flex flex-col gap-3 rounded-xl border bg-muted/40 p-4">
                <Textarea
                  placeholder="Paste comma or newline separated identifiers"
                  value={batchInput}
                  onChange={(e) => setBatchInput(e.target.value)}
                  rows={4}
                />
                <div className="flex items-center justify-between">
                  <p className="text-xs text-muted-foreground">
                    We will look up all identifiers and group candidate entities for each. No enforced species filter.
                  </p>
                  <Button onClick={handleBatchLookup} disabled={lookupLoading}>
                    Run lookup
                  </Button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Results */}
      <div className={embedded ? "flex-1 overflow-y-auto p-4" : "flex-1 overflow-y-auto"}>
        <div className={embedded ? "w-full min-h-full" : "w-full max-w-screen-xl mx-auto px-4 sm:px-6 lg:px-8 py-6"}>
            {searchMode === "full-text" ? (
              <SearchResults
                results={results}
                loading={loading}
                loadingMore={loadingMore}
                hasMore={hasMore}
                sentinelRef={sentinelRef}
              />
            ) : (
              <IdentifierMatches
                matches={lookupMatches}
                entities={lookupEntities}
                loading={lookupLoading}
                error={lookupError}
              />
            )}
        </div>
      </div>
    </div>
  );
}
