"use client"

import { useState, useEffect, useCallback, useRef } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { FilterSidebar } from "./filter-sidebar"
import { searchInteractions } from "../api/queries"
import { MeilisearchInteraction, MeilisearchFilters } from "@/types/meilisearch"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ArrowRight, Minus, Filter, X } from "lucide-react"
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet"
import { EntityBadge } from "@/components/entity-badge"
import { CvTermBadge } from "@/features/cv-terms/components/cv-term-badge"
import { Skeleton } from "@/components/ui/skeleton"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { cn, formatNumber } from "@/lib/utils"
import { InteractionDetailsSheet } from "./interaction-details-sheet"
import GraphView from "@/features/interactions-search/components/graph-view"
import { DataCard } from "./data-card"
import { exportToCSV } from "@/lib/utils/export"
import { useInfiniteScroll } from "@/hooks/use-infinite-scroll"

const RESULTS_PER_PAGE = 20;
const MAX_GRAPH_INTERACTIONS = 1000;

type ViewMode = "table" | "network";

interface InteractionsSearchProps {
  entityId?: string;
  entityName?: string;
  hideEntityFilter?: boolean;
  initialEntityIds?: string[];
  hideFilters?: boolean; // Hide filter sidebar in AI context
}

export function InteractionsSearch({ 
  entityId,
  entityName,
  hideEntityFilter = false,
  initialEntityIds = [],
  hideFilters = false
}: InteractionsSearchProps = {}) {
  const router = useRouter();
  const searchParams = useSearchParams();
  
  // State
  const [filters, setFilters] = useState<MeilisearchFilters>(() => {
    // Parse filters from URL
    const urlFilters: MeilisearchFilters = {};
    
    // If entityId is provided, pre-filter by it
    if (entityId) {
      urlFilters.entity_ids = [entityId];
    } else if (initialEntityIds.length > 0) {
      urlFilters.entity_ids = initialEntityIds;
    }
    
    // Array filters
    ['interaction_types', 'data_sources', 'detection_methods', 'causal_statements', 
     'causal_mechanisms', 'interactor_types', 'signs', 'entity_ids'].forEach(key => {
      const value = searchParams.get(key);
      if (value) {
        // Don't override entity_ids if entityId or initialEntityIds props are provided
        if (key === 'entity_ids' && (entityId || initialEntityIds.length > 0)) {
          return;
        }
        const filterKey = key as keyof MeilisearchFilters;
        const values = value.split(',');
        if (values.length > 0) {
          (urlFilters[filterKey] as string[] | undefined) = values;
        }
      }
    });
    
    // Boolean/string filters
    const isDirected = searchParams.get('is_directed');
    if (isDirected !== null) urlFilters.is_directed = isDirected === 'true';
    
    const consensusSign = searchParams.get('consensus_sign');
    if (consensusSign) urlFilters.consensus_sign = consensusSign;
    
    const consensusDirection = searchParams.get('consensus_direction');
    if (consensusDirection) urlFilters.consensus_direction = consensusDirection as 'forward' | 'reverse';
    
    // Number filters
    const evidenceCountMin = searchParams.get('evidence_count_min');
    if (evidenceCountMin) urlFilters.evidence_count_min = parseInt(evidenceCountMin);
    
    const evidenceCountMax = searchParams.get('evidence_count_max');
    if (evidenceCountMax) urlFilters.evidence_count_max = parseInt(evidenceCountMax);
    
    return urlFilters;
  });
  
  const mainContentRef = useRef<HTMLDivElement | null>(null);
  const [rootElement, setRootElement] = useState<HTMLDivElement | null>(null);
  const [filterCounts, setFilterCounts] = useState<Record<string, Record<string, number>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  
  // Initialize selected entities state before using it in hooks
  const [selectedEntities, setSelectedEntities] = useState<Array<{ id: string; canonical_identifier: string; display_name?: string }>>(() => {
    // Initialize selected entities from URL or entityId prop
    if (entityId) {
      return [{ id: entityId, canonical_identifier: entityId, display_name: entityName }];
    } else if (initialEntityIds.length > 0) {
      return initialEntityIds.map(id => ({ id, canonical_identifier: id }));
    }
    const entityIds = searchParams.get('entity_ids');
    if (entityIds) {
      // For now, just store IDs - we'll fetch full entity data later if needed
      return entityIds.split(',').map(id => ({ id, canonical_identifier: id }));
    }
    return [];
  });
  
  // Infinite scroll hook - skip if we have initial data
  const {
    data: results,
    loading,
    loadingMore,
    hasMore,
    error: infiniteScrollError,
    totalResults,
    sentinelRef
  } = useInfiniteScroll<MeilisearchInteraction>({
    fetchData: useCallback(async (offset: number, limit: number) => {
      // Always fetch from API, even if we have initial data
      // This ensures infinite scroll works properly
      const transformedFilters: MeilisearchFilters = {
        ...filters,
        entity_ids: filters.entity_ids || selectedEntities.map(e => e.id) || initialEntityIds
      };
      
      const response = await searchInteractions("", transformedFilters, limit, offset);
      
      // Update filter counts from facet distribution if available
      if (response.facetDistribution && offset === 0) {
        const facetDist = response.facetDistribution;
        const counts: Record<string, Record<string, number>> = {};
        
        // Process CV term facets with combined ID:Name format
        const cvFields = ['interaction_types', 'data_sources', 'detection_methods', 
                          'causal_statements', 'causal_mechanisms', 'interactor_types'];
        
        for (const field of cvFields) {
          const facetField = `${field}_facet`;
          if (facetDist[facetField]) {
            counts[field] = facetDist[facetField];
          }
        }
        
        // Process boolean fields
        if (facetDist.is_directed) {
          counts.is_directed = facetDist.is_directed;
        }
        
        // Process evidence count range
        if (facetDist.evidence_count) {
          counts.evidence_count = {
            min: Math.min(...Object.keys(facetDist.evidence_count).map(Number)),
            max: Math.max(...Object.keys(facetDist.evidence_count).map(Number))
          };
        } else {
          // Provide reasonable defaults when evidence_count facet is not available
          counts.evidence_count = {
            min: 1,
            max: 100
          };
        }
        
        setFilterCounts(counts);
      }
      
      return {
        results: response.hits,
        totalResults: response.estimatedTotalHits || 0
      };
    }, [filters, selectedEntities, initialEntityIds]),
    pageSize: RESULTS_PER_PAGE,
    dependencies: [filters, selectedEntities],
    root: rootElement
  });
  const [selectedInteraction, setSelectedInteraction] = useState<MeilisearchInteraction | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [viewMode, setViewMode] = useState<ViewMode>("table");
  const [allInteractions, setAllInteractions] = useState<MeilisearchInteraction[]>([]);
  const [isLoadingAll, setIsLoadingAll] = useState(false);
  const [hasLoadedGraphData, setHasLoadedGraphData] = useState(false);

  // Update URL when filters change
  const updateURL = useCallback((newFilters: MeilisearchFilters) => {
    // Don't update URL if we're in entity-specific mode
    if (entityId) {
      return;
    }
    
    const params = new URLSearchParams();
    
    // Add filters to URL
    Object.entries(newFilters).forEach(([key, value]) => {
      if (value !== undefined && value !== null) {
        if (Array.isArray(value) && value.length > 0) {
          params.set(key, value.join(','));
        } else if (typeof value === 'boolean') {
          params.set(key, value.toString());
        } else if (value !== '') {
          params.set(key, value.toString());
        }
      }
    });
    
    router.push(`/interactions/search?${params.toString()}`);
  }, [router, entityId]);

  // Update error state from infinite scroll hook
  useEffect(() => {
    setError(infiniteScrollError?.message || null);
  }, [infiniteScrollError]);
  
  
  // Effect to update filters when selected entities change
  useEffect(() => {
    const entityIds = selectedEntities.map(e => e.id);
    const currentEntityIds = filters.entity_ids || [];
    
    // Only update if entity_ids actually changed
    if (JSON.stringify(entityIds.sort()) !== JSON.stringify(currentEntityIds.sort())) {
      const newFilters = { ...filters };
      
      if (entityIds.length > 0) {
        newFilters.entity_ids = entityIds;
      } else {
        delete newFilters.entity_ids;
      }
      
      setFilters(newFilters);
      updateURL(newFilters);
    }
  }, [selectedEntities, filters, updateURL]);

    // Function to load all interactions for graph view
    const loadAllInteractions = useCallback(async () => {
      if (isLoadingAll) return;
      
      setIsLoadingAll(true);
      setError(null);
      
      try {
        // No need to transform filters - they should already be in the correct format
        const response = await searchInteractions("", filters, MAX_GRAPH_INTERACTIONS, 0);
        console.log('Loaded interactions for graph:', response.hits.length);
        console.log('Sample interaction:', response.hits[0]);
        setAllInteractions(response.hits);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load all interactions");
      } finally {
        setIsLoadingAll(false);
      }
    }, [filters, isLoadingAll]);
  // Auto-load graph data when switching to network view
  useEffect(() => {
    if (viewMode === "network" && !hasLoadedGraphData && totalResults > 0 && !isLoadingAll) {
      loadAllInteractions();
      setHasLoadedGraphData(true);
    }
  }, [viewMode, hasLoadedGraphData, totalResults, isLoadingAll, loadAllInteractions]);

  // Reset hasLoadedGraphData when filters change
  useEffect(() => {
    setHasLoadedGraphData(false);
    setAllInteractions([]);
  }, [filters]);

  // Handlers
  const handleFilterChange = (newFilters: MeilisearchFilters) => {
    setFilters(newFilters);
    updateURL(newFilters);
  };

  const handleClearFilters = () => {
    // When viewing a specific entity's interactions, preserve the entity filter
    if (entityId) {
      const preservedEntityFilter = filters.entity_ids;
      setFilters({ entity_ids: preservedEntityFilter });
      setSelectedEntities(selectedEntities.filter(e => e.id === entityId));
      updateURL({ entity_ids: preservedEntityFilter });
    } else {
      setFilters({});
      setSelectedEntities([]);
      updateURL({});
    }
  };
  
  const handleEntitySelect = (entity: { id: string; canonical_identifier: string; display_name?: string }) => {
    setSelectedEntities([...selectedEntities, entity]);
  };
  
  const handleEntityRemove = (entityId: string) => {
    setSelectedEntities(selectedEntities.filter(e => e.id !== entityId));
  };

  const handleRowClick = (row: MeilisearchInteraction) => {
    setSelectedInteraction(row);
    setDetailsOpen(true);
  };



  // Convert MeilisearchInteraction to format expected by GraphView
  const convertToGraphViewFormat = useCallback((interactions: MeilisearchInteraction[]): Array<{ id: string; entity_a: { id: string; display_name: string; canonical_identifier: string }; entity_b: { id: string; display_name: string; canonical_identifier: string }; consensus_sign?: string | null; consensus_direction?: string | null; is_directed?: boolean; evidence_count: number }> => {
    console.log('Converting interactions:', interactions.length);
    
    // Create a unique ID for each entity based on canonical ID
    // This is important because GraphView uses entity.id to deduplicate nodes
    const converted = interactions.map((interaction) => ({
      id: interaction.id, // Keep as string - GraphView will convert
      entity_a: {
        // IMPORTANT: Use canonical ID as the entity ID - this is what GraphView uses to create unique nodes
        id: interaction.entity_a_canonical_id, // This must be unique per entity
        canonical_identifier: interaction.entity_a_canonical_id || '',
        display_name: interaction.entity_a_name || interaction.entity_a_canonical_id || '',
        entity_type: { 
          id: 0,
          namespace: { id: 0, name: '' },
          accession: '',
          name: 'protein', // Default to protein
          definition: '',
          is_obsolete: false
        }
      },
      entity_b: {
        // IMPORTANT: Use canonical ID as the entity ID - this is what GraphView uses to create unique nodes
        id: interaction.entity_b_canonical_id, // This must be unique per entity
        canonical_identifier: interaction.entity_b_canonical_id || '',
        display_name: interaction.entity_b_name || interaction.entity_b_canonical_id || '',
        entity_type: { 
          id: 0,
          namespace: { id: 0, name: '' },
          accession: '',
          name: 'protein', // Default to protein
          definition: '',
          is_obsolete: false
        }
      },
      has_directed_evidence: interaction.is_directed || false,
      consensus_sign: interaction.consensus_sign || null,
      evidence_count: interaction.evidence_count || 0,
      evidences: []  // GraphView doesn't need full evidence data
    }));
    
    console.log('Converted interactions:', converted.length);
    console.log('Sample converted:', converted[0]);
    
    // Log unique entities to debug
    const uniqueEntityAs = new Set(converted.map(i => i.entity_a.id));
    const uniqueEntityBs = new Set(converted.map(i => i.entity_b.id));
    console.log('Unique entity A count:', uniqueEntityAs.size);
    console.log('Unique entity B count:', uniqueEntityBs.size);
    console.log('Total unique entities:', new Set([...uniqueEntityAs, ...uniqueEntityBs]).size);
    
    return converted;
  }, []);

  // Handle export
  const handleExport = useCallback(() => {
    const dataToExport = viewMode === "network" && allInteractions.length > 0 ? allInteractions : results;
    
    const exportData = dataToExport.map(interaction => ({
      'Entity A': interaction.entity_a_name || interaction.entity_a_canonical_id || '',
      'Entity B': interaction.entity_b_name || interaction.entity_b_canonical_id || '',
      'Directed': interaction.is_directed ? 'Yes' : 'No',
      'Direction': interaction.consensus_direction || 'N/A',
      'Consensus Sign': interaction.consensus_sign || '',
      'Interaction Types': interaction.interaction_types?.map(t => t.name).join(', ') || '',
      'Data Sources': interaction.data_sources?.map(s => s.name).join(', ') || '',
      'Evidence Count': interaction.evidence_count || 0
    }));
    
    exportToCSV(exportData, `interactions_search_${viewMode}_${new Date().toISOString().split('T')[0]}`);
  }, [viewMode, allInteractions, results]);


  return (
      <div className=
        "flex gap-6 p-4 h-[calc(100vh-8rem)]"
      >
      {/* Filter Sidebar - hide if hideFilters is true */}
      {!hideFilters && (
        <aside className="hidden lg:block w-80 flex-shrink-0 h-full">
          {filterCounts && (
            <FilterSidebar
              filters={filters}
              filterCounts={filterCounts}
              onFilterChange={handleFilterChange}
              onClearFilters={handleClearFilters}
              selectedEntities={selectedEntities}
              onEntitySelect={handleEntitySelect}
              onEntityRemove={handleEntityRemove}
              hideEntityFilter={hideEntityFilter}
            />
          )}
        </aside>
      )}

      {/* Main Content */}
      <DataCard 
        className="flex-1 min-w-0 flex flex-col"
        title={`${formatNumber(totalResults)} interactions found`}
        viewMode={viewMode}
        onViewModeChange={setViewMode}
        onExport={handleExport}
      >
        {/* Mobile filter drawer - hide if hideFilters is true */}
        {!hideFilters && (
          <div className="lg:hidden p-4 border-b">
            <Sheet>
            <SheetTrigger asChild>
              <Button variant="outline" className="w-full">
                <Filter className="h-4 w-4 mr-2" />
                Filters
                {filterCounts && Object.keys(filters).length > 0 && (
                  <Badge variant="secondary" className="ml-2">
                    {Object.entries(filters).reduce((count, [, value]) => {
                      if (Array.isArray(value)) return count + value.length;
                      if (value !== null && value !== undefined) return count + 1;
                      return count;
                    }, 0)}
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
                  {Object.keys(filters).length > 0 && (
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
              <div className="h-[calc(100%-4rem)] overflow-y-auto">
                {filterCounts && (
                  <FilterSidebar
                    filters={filters}
                    filterCounts={filterCounts}
                    onFilterChange={handleFilterChange}
                    onClearFilters={handleClearFilters}
                    selectedEntities={selectedEntities}
                    onEntitySelect={handleEntitySelect}
                    onEntityRemove={handleEntityRemove}
                    hideEntityFilter={hideEntityFilter}
                    isMobile
                  />
                )}
              </div>
            </SheetContent>
          </Sheet>
        </div>
        )}

        {/* Results */}
        {viewMode === "table" ? (
          loading ? (
            <div className="p-6 space-y-4">
              <Skeleton className="h-8 w-48" />
              <Skeleton className="h-96 w-full" />
            </div>
          ) : error ? (
            <div className="p-6">
              <Alert variant="destructive">
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            </div>
          ) : results.length > 0 ? (
            <div className="flex flex-col h-full">
            {/* Fixed Table Header */}
            <div className="border-b bg-background px-3 h-[57px] flex items-center flex-shrink-0">
              <Table>
                <TableHeader className="">
                  <TableRow>
                    <TableHead className="w-[20%] py-2">Entity A</TableHead>
                    <TableHead className="w-[50px] text-center py-2"></TableHead>
                    <TableHead className="w-[20%] py-2">Entity B</TableHead>
                    <TableHead className="w-[30%] py-2">Interaction Types</TableHead>
                    <TableHead className="w-[20%] py-2">Sources</TableHead>
                    <TableHead className="w-[10%] text-center py-2">Evidence</TableHead>
                  </TableRow>
                </TableHeader>
              </Table>
            </div>

            {/* Scrollable Table Body */}
            <div 
              ref={(el) => {
                mainContentRef.current = el;
                setRootElement(el);
              }}
              className="flex-1 overflow-y-auto"
            >
              <Table>
                <TableBody>
                  {results.map((row) => {
                    // Swap entities if direction is reverse to always show arrow going left to right
                    const shouldSwap = row.is_directed && row.consensus_direction === 'reverse';
                    const entityA = shouldSwap ? {
                      canonical_id: row.entity_b_canonical_id,
                      name: row.entity_b_name
                    } : {
                      canonical_id: row.entity_a_canonical_id,
                      name: row.entity_a_name
                    };
                    const entityB = shouldSwap ? {
                      canonical_id: row.entity_a_canonical_id,
                      name: row.entity_a_name
                    } : {
                      canonical_id: row.entity_b_canonical_id,
                      name: row.entity_b_name
                    };

                    return (
                      <TableRow
                        key={row.id}
                        onClick={() => handleRowClick(row)}
                        className="cursor-pointer hover:bg-muted/50"
                      >
                        <TableCell className="w-[25%] max-w-0">
                          <div className="w-full">
                            <EntityBadge
                              canonicalIdentifier={entityA.canonical_id}
                              displayName={entityA.name}
                            />
                          </div>
                        </TableCell>
                        <TableCell className="w-[50px] text-center">
                          <div className="flex justify-center">
                            {row.is_directed ? (
                              <ArrowRight className={cn(
                                "h-4 w-4",
                                row.consensus_sign === "positive" ? "text-green-500" :
                                row.consensus_sign === "negative" ? "text-red-500" :
                                "text-muted-foreground"
                              )} />
                            ) : (
                              <Minus className="h-4 w-4 text-muted-foreground" />
                            )}
                          </div>
                        </TableCell>
                        <TableCell className="w-[20%] max-w-0">
                          <div className="w-full">
                            <EntityBadge
                              canonicalIdentifier={entityB.canonical_id}
                              displayName={entityB.name}
                            />
                          </div>
                        </TableCell>
                      <TableCell className="w-[30%] max-w-0">
                        <div className="flex flex-wrap gap-1 overflow-hidden">
                          {row.interaction_types.slice(0, 2).map((type) => (
                            <CvTermBadge
                              key={type.id}
                              cvTermId={type.id}
                              cvTermName={type.name}
                              variant="secondary"
                              className="text-xs max-w-[180px]"
                            />
                          ))}
                          {row.interaction_types.length > 2 && (
                            <Badge variant="outline" className="text-xs">
                              +{formatNumber(row.interaction_types.length - 2)}
                            </Badge>
                          )}
                        </div>
                      </TableCell>
                      <TableCell className="w-[20%] max-w-0">
                        <div className="flex flex-wrap gap-1 overflow-hidden">
                          {row.data_sources.slice(0, 1).map((source) => (
                            <CvTermBadge
                              key={source.id}
                              cvTermId={source.id}
                              cvTermName={source.name}
                              variant="outline"
                              className="text-xs max-w-[150px]"
                            />
                          ))}
                          {row.data_sources.length > 1 && (
                            <Badge variant="outline" className="text-xs">
                              +{formatNumber(row.data_sources.length - 1)}
                            </Badge>
                          )}
                        </div>
                      </TableCell>
                      <TableCell className="w-[10%] text-center">
                        <Badge variant="outline">
                          {formatNumber(row.evidence_count)}
                        </Badge>
                      </TableCell>
                    </TableRow>
                    );
                  })}
                  {/* Infinite scroll trigger - inside table body */}
                  <TableRow style={{ display: hasMore ? 'table-row' : 'none' }}>
                    <TableCell colSpan={6} className="p-0">
                      <div
                        ref={sentinelRef as React.RefObject<HTMLDivElement>}
                        className="flex justify-center py-4"
                        style={{ minHeight: '40px' }}
                      >
                        {loadingMore ? (
                          <div className="flex items-center gap-2">
                            <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                            <span className="text-sm text-muted-foreground">Loading more...</span>
                          </div>
                        ) : (
                          /* Invisible trigger to ensure intersection observer works */
                          <div className="h-4 w-4" />
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                </TableBody>
              </Table>
              
              {/* End of results message */}
              {!hasMore && results.length > 0 && (
                <div className="py-4 text-center text-sm text-muted-foreground">
                  No more results to load
                </div>
              )}
            </div>
            </div>
          ) : !loading && (
            <div className="p-6 flex-1 flex items-center justify-center">
              <p className="text-muted-foreground text-center">
                {Object.keys(filters).length > 0
                  ? "No interactions found matching your criteria."
                  : "Loading interactions..."}
              </p>
            </div>
          )
        ) : viewMode === "network" ? (
          // Graph View
          <div className="flex-1 overflow-hidden">
            {isLoadingAll ? (
              <div className="flex flex-col items-center justify-center h-full p-8">
                <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent mb-4" />
                <p className="text-muted-foreground">
                  Loading {formatNumber(Math.min(totalResults, MAX_GRAPH_INTERACTIONS))} interactions...
                </p>
              </div>
            ) : allInteractions.length > 0 ? (
              <GraphView
                interactions={convertToGraphViewFormat(allInteractions)}
                onSelectInteraction={(interaction) => {
                  // Find the original MeilisearchInteraction
                  const meilisearchInteraction = allInteractions.find(i => i.id.toString() === interaction.id?.toString());
                  if (meilisearchInteraction) {
                    setSelectedInteraction(meilisearchInteraction);
                    setDetailsOpen(true);
                  }
                }}
              />
            ) : (
              <div className="flex flex-col items-center justify-center h-full p-8">
                <p className="text-muted-foreground mb-4">
                  {totalResults > 0 
                    ? `No interactions loaded yet`
                    : "No interactions to visualize"}
                </p>
                {totalResults > 0 && !hasLoadedGraphData && (
                  <Button 
                    onClick={loadAllInteractions} 
                    disabled={isLoadingAll}
                    size="lg"
                  >
                    Load All (max {formatNumber(MAX_GRAPH_INTERACTIONS)})
                  </Button>
                )}
              </div>
            )}
          </div>
        ) : null}
        </DataCard>
      
      {/* Interaction Details Sheet */}
      <InteractionDetailsSheet
        open={detailsOpen}
        onOpenChange={setDetailsOpen}
        interaction={selectedInteraction}
      />
    </div>
  );
}