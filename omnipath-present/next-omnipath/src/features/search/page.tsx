"use client";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useSidebarContent } from "@/contexts/sidebar-content-context";
import { useInfiniteScroll } from "@/hooks/use-infinite-scroll";
import { useCallback, useEffect, useMemo, useState, useTransition } from "react";
import { searchMeilisearch } from "./api/queries";
import { EntityFilterSidebar } from "./components/entity-filter-sidebar";
import type { SearchResult } from "./components/result-card";
import { SearchBar } from "./components/search-bar";
import { SearchResults } from "./components/search-results";
import { IdentifierMatches, type IdentifierMatch } from "./components/identifier-matches";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Search } from "lucide-react";
import { AnnotationFilterSidebar } from "@/features/interactions-search/components/filter-sidebar";
import { cn } from "@/lib/utils";
import type { MeilisearchFilters } from "@/types/meilisearch";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";

interface SearchPageProps {
  // Props for embedded mode (like in AI dialogs)
  embedded?: boolean;
  initialQuery?: string;
  initialSearchType?: "search_entities" | "cv_terms";
  initialFilters?: { entity_ids?: number[]; entity_types?: string[]; sources?: string[]; ncbi_tax_id?: string[]; cv_terms?: string[] };
  // Whether to show filter sidebar even when embedded
  showFilters?: boolean;
}

type SearchMode = "full-text" | "identifier" | "batch";
type LayoutMode = "search" | "split" | "ontology";

export default function SearchPage({
  embedded = false,
  initialQuery = "",
  initialSearchType = "search_entities",
  initialFilters,
  showFilters = false
}: SearchPageProps = {}) {
  const [query, setQuery] = useState(initialQuery);
  const [, startTransition] = useTransition();
  const [searchMode, setSearchMode] = useState<SearchMode>("full-text");
  const [selectedSpecies, setSelectedSpecies] = useState<string>("9606"); // Default to Human
  const [filters, setFilters] = useState<{ entity_ids?: number[]; entity_types?: string[]; sources?: string[]; ncbi_tax_id?: string[]; cv_terms?: string[] }>(
    initialFilters || { ncbi_tax_id: ["9606"] }
  );
  const [filterCounts, setFilterCounts] = useState<{ entity_type?: Record<string, number>; sources?: Record<string, number>; ncbi_tax_id?: Record<string, number>; cv_terms?: Record<string, number> }>({});
  const [lookupMatches, setLookupMatches] = useState<IdentifierMatch[]>([]);
  const [lookupEntities, setLookupEntities] = useState<SearchResult[]>([]);
  const [lookupError, setLookupError] = useState<string | null>(null);
  const [lookupLoading, setLookupLoading] = useState(false);
  const [identifierInput, setIdentifierInput] = useState("");
  const [batchInput, setBatchInput] = useState("");
  const { setSidebarContent } = useSidebarContent();
  const [layoutMode, setLayoutMode] = useState<LayoutMode>("split");

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
          ncbi_tax_id: response.facetDistribution.ncbi_tax_id || {},
          cv_terms: response.facetDistribution.cv_terms || {},
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
  const handleFilterChange = useCallback((newFilters: { entity_types?: string[]; sources?: string[]; ncbi_tax_id?: string[]; cv_terms?: string[] }) => {
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

  const annotationFilters = useMemo<MeilisearchFilters>(
    () => ({
      ...filters,
      interaction_annotation_terms: filters.cv_terms,
    }),
    [filters]
  );

  const annotationFilterCounts = useMemo<Record<string, Record<string, number>>>(
    () => ({
      interaction_annotation_terms: filterCounts.cv_terms || {},
    }),
    [filterCounts.cv_terms]
  );

  const handleAnnotationFilterChange = useCallback((newFilters: MeilisearchFilters) => {
    setFilters(prev => ({
      ...prev,
      cv_terms: newFilters.interaction_annotation_terms || undefined,
    }));
  }, []);

  const ontologyEnabled =
    !embedded &&
    searchMode === "full-text" &&
    initialSearchType === "search_entities";

  const effectiveLayoutMode = embedded ? "search" : layoutMode;

  useEffect(() => {
    if (!ontologyEnabled && layoutMode !== "search") {
      setLayoutMode("search");
    }
  }, [layoutMode, ontologyEnabled]);

  // Set sidebar content when filter counts are available (not in embedded mode unless showFilters is true)
  useEffect(() => {
    if ((!embedded || showFilters) && searchMode === "full-text" && initialSearchType === "search_entities" && Object.keys(filterCounts).length > 0) {
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
  }, [embedded, showFilters, searchMode, initialSearchType, filterCounts, filters, handleFilterChange, handleClearFilters, setSidebarContent]);

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

  const isSplitLayout = effectiveLayoutMode === "split" && ontologyEnabled;
  const searchContainerClass = embedded
    ? "w-full min-h-full"
    : isSplitLayout
      ? "w-full h-full px-4 py-6"
      : "w-full max-w-screen-xl mx-auto px-4 sm:px-6 lg:px-8 py-6";
  const ontologyContainerClass = isSplitLayout
    ? "h-full"
    : "h-full max-w-md mx-auto lg:max-w-none";

  const renderSearchHeader = (inline: boolean) => (
    <div className={cn(
      inline
        ? "border-b bg-background/60 backdrop-blur-md supports-[backdrop-filter]:bg-background/60"
        : "sticky top-0 z-20 border-b bg-background/60 backdrop-blur-md supports-[backdrop-filter]:bg-background/60"
    )}>
      <div className={cn(
        inline ? "w-full px-4 py-4 space-y-4" : "w-full max-w-screen-xl mx-auto px-4 sm:px-6 lg:px-8 py-4 space-y-4"
      )}>
        {/* Tabs and Search Bar on same row */}
        <div className={`flex flex-wrap gap-4 ${searchMode === 'batch' ? 'items-start' : 'items-center'}`}>
          <Tabs
            value={searchMode}
            onValueChange={(value) => {
              setSearchMode(value as SearchMode);
              setLookupError(null);
            }}
            className={searchMode === 'batch' ? 'mt-2' : ''}
          >
            <TabsList>
              <TabsTrigger value="full-text">Full text</TabsTrigger>
              <TabsTrigger value="identifier">Identifier lookup</TabsTrigger>
              <TabsTrigger value="batch">Batch identifiers</TabsTrigger>
            </TabsList>
          </Tabs>

          {searchMode === "full-text" && effectiveLayoutMode !== "ontology" && (
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

          {searchMode === "identifier" && effectiveLayoutMode !== "ontology" && (
            <div className="flex-1 relative group backdrop-blur-sm rounded-full transition-all focus-within:shadow-md focus-within:ring-2 focus-within:ring-primary/20 bg-background border">
              <Search className="absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-muted-foreground transition-colors group-focus-within:text-primary z-10" />
              <Input
                placeholder="Enter one identifier (e.g. UniProt, gene symbol, etc.)"
                className="w-full pl-12 pr-[100px] h-12 text-lg rounded-full shadow-sm border-0 focus-visible:ring-0"
                value={identifierInput}
                onChange={(e) => setIdentifierInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleIdentifierLookup()}
              />
              <div className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-2 z-10">
                <Button
                  onClick={handleIdentifierLookup}
                  disabled={lookupLoading}
                  className="h-8 px-4 rounded-full shadow-sm transition-all hover:shadow-md"
                >
                  Look up
                </Button>
              </div>
            </div>
          )}

          {searchMode === "batch" && effectiveLayoutMode !== "ontology" && (
            <div className="flex-1 flex flex-col gap-3 rounded-xl border bg-background/50 p-1 shadow-sm focus-within:ring-2 focus-within:ring-primary/20 transition-all backdrop-blur-sm">
              <Textarea
                placeholder="Paste comma or newline separated identifiers"
                value={batchInput}
                onChange={(e) => setBatchInput(e.target.value)}
                rows={4}
                className="resize-none border-0 focus-visible:ring-0 bg-transparent min-h-[100px]"
              />
              <div className="flex items-center justify-between px-3 pb-2">
                <p className="text-xs text-muted-foreground">
                  We will look up all identifiers and group candidate entities for each.
                </p>
                <Button
                  onClick={handleBatchLookup}
                  disabled={lookupLoading}
                  size="sm"
                  className="rounded-full"
                >
                  Run lookup
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );

  // Render content based on embedded mode
  const searchContent = (
    <div className={cn("h-full min-h-0 flex flex-col")}>
      {!embedded && isSplitLayout ? renderSearchHeader(true) : null}
      <div className={cn(
        embedded ? "flex-1 overflow-y-auto p-4" : "flex-1 overflow-y-auto",
        "min-h-0"
      )}>
        <div className={searchContainerClass}>
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

  const ontologyContent = (
    <div className="h-full overflow-y-auto p-4">
      <div className={ontologyContainerClass}>
        <AnnotationFilterSidebar
          filters={annotationFilters}
          filterCounts={annotationFilterCounts}
          onFilterChange={handleAnnotationFilterChange}
        />
      </div>
    </div>
  );

  return (
    <div className={cn(
      embedded ? "h-full flex flex-col overflow-hidden" : "flex-1 flex flex-col h-svh overflow-hidden",
      "relative"
    )}>
      {!embedded && !isSplitLayout && effectiveLayoutMode !== "ontology" && renderSearchHeader(false)}

      <div className="flex-1 min-h-0">
        {effectiveLayoutMode === "split" && ontologyEnabled ? (
          <ResizablePanelGroup direction="horizontal" className="h-full">
            <ResizablePanel defaultSize={68} minSize={50} className="min-h-0">
              {searchContent}
            </ResizablePanel>
            <ResizableHandle withHandle />
            <ResizablePanel defaultSize={32} minSize={25} className="min-h-0">
              {ontologyContent}
            </ResizablePanel>
          </ResizablePanelGroup>
        ) : effectiveLayoutMode === "ontology" && ontologyEnabled ? (
          <div className="h-full">
            {ontologyContent}
          </div>
        ) : (
          <div className="h-full">
            {searchContent}
          </div>
        )}
      </div>

      {!embedded && (
        <div className="fixed bottom-4 right-4 z-40">
          <div className="inline-flex items-center rounded-full border bg-background/90 p-1 shadow-sm">
            <Button
              size="sm"
              variant={effectiveLayoutMode === "search" ? "default" : "ghost"}
              onClick={() => setLayoutMode("search")}
              className="rounded-full h-8"
            >
              Search
            </Button>
            <Button
              size="sm"
              variant={effectiveLayoutMode === "split" ? "default" : "ghost"}
              onClick={() => setLayoutMode("split")}
              className="rounded-full h-8"
              disabled={!ontologyEnabled}
            >
              Both
            </Button>
            <Button
              size="sm"
              variant={effectiveLayoutMode === "ontology" ? "default" : "ghost"}
              onClick={() => setLayoutMode("ontology")}
              className="rounded-full h-8"
              disabled={!ontologyEnabled}
            >
              Ontology
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
