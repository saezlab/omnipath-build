"use client";

import { EntityBadge } from "@/components/entity-badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { EntityInfo, fetchEntitiesByIds, searchInteractions } from "@/features/interactions-search/api/queries";
import { DataCard } from "@/features/interactions-search/components/data-card";
import { AnnotationFilterSidebar, FilterSidebar } from "@/features/interactions-search/components/filter-sidebar";
import GraphView from "@/features/interactions-search/components/graph-view";
import { InteractionDetailsSheet } from "@/features/interactions-search/components/interaction-details-sheet";
import { useInfiniteScroll } from "@/hooks/use-infinite-scroll";
import { cn, formatNumber } from "@/lib/utils";
import { exportToCSV } from "@/lib/utils/export";
import { MeilisearchFilters, MeilisearchInteraction } from "@/types/meilisearch";
import { ArrowRight, Filter, Minus, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

const RESULTS_PER_PAGE = 20;
const MAX_GRAPH_INTERACTIONS = 1000;

type ViewMode = "table" | "network";
type LayoutMode = "search" | "split" | "ontology";

interface InteractionsExploreTabProps {
  filters: MeilisearchFilters;
  onFilterChange: (filters: MeilisearchFilters) => void;
  onFilterCountsUpdate: (counts: Record<string, Record<string, number>>) => void;
}

// Helper function to extract type label from "TypeLabel:ID" format
function extractTypeLabel(memberType: string): string {
  const colonIndex = memberType.indexOf(':');
  return colonIndex > 0 ? memberType.substring(0, colonIndex) : memberType;
}

// Helper function to determine consensus sign from directions
function getConsensusSign(directions: MeilisearchInteraction['directions']): 'positive' | 'negative' | 'mixed' | null {
  if (!directions || directions.length === 0) return null;

  const hasPositive = directions.some(d => d.sign === 1 || d.sign === 0);
  const hasNegative = directions.some(d => d.sign === -1 || d.sign === 0);

  if (hasPositive && hasNegative) return 'mixed';
  if (hasPositive) return 'positive';
  if (hasNegative) return 'negative';
  return null;
}

// Helper function to determine if members should be swapped based on direction
function shouldSwapMembers(directions: MeilisearchInteraction['directions']): boolean {
  if (!directions || directions.length === 0) return false;
  return directions[0]?.direction === 'b-a';
}

export function InteractionsExploreTab({
  filters,
  onFilterChange,
  onFilterCountsUpdate
}: InteractionsExploreTabProps) {
  const mainContentRef = useRef<HTMLDivElement | null>(null);
  const [rootElement, setRootElement] = useState<HTMLDivElement | null>(null);
  const [filterCounts, setFilterCounts] = useState<Record<string, Record<string, number>> | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Infinite scroll hook
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
      const response = await searchInteractions("", filters, limit, offset);

      // Update filter counts from facet distribution if available
      if (response.facetDistribution && offset === 0) {
        const facetDist = response.facetDistribution;
        const counts: Record<string, Record<string, number>> = {};

        // Process facets matching new schema
        if (facetDist.member_types) {
          counts.member_types = facetDist.member_types;
        }
        if (facetDist.has_direction) {
          counts.has_direction = facetDist.has_direction;
        }
        if (facetDist.has_positive_sign) {
          counts.has_positive_sign = facetDist.has_positive_sign;
        }
        if (facetDist.has_negative_sign) {
          counts.has_negative_sign = facetDist.has_negative_sign;
        }
        if (facetDist.interaction_annotation_terms) {
          counts.interaction_annotation_terms = facetDist.interaction_annotation_terms;
        }
        if (facetDist.sources) {
          counts.sources = facetDist.sources;
        }

        setFilterCounts(counts);
        onFilterCountsUpdate(counts);
      }

      return {
        results: response.hits,
        totalResults: response.estimatedTotalHits || 0
      };
    }, [filters, onFilterCountsUpdate]),
    pageSize: RESULTS_PER_PAGE,
    dependencies: [filters],
    root: rootElement
  });

  const [selectedInteraction, setSelectedInteraction] = useState<MeilisearchInteraction | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [viewMode, setViewMode] = useState<ViewMode>("table");
  const [allInteractions, setAllInteractions] = useState<MeilisearchInteraction[]>([]);
  const [isLoadingAll, setIsLoadingAll] = useState(false);
  const [hasLoadedGraphData, setHasLoadedGraphData] = useState(false);
  const [entityMap, setEntityMap] = useState<Map<number, EntityInfo>>(new Map());
  const [layoutMode, setLayoutMode] = useState<LayoutMode>("split");

  // Update error state from infinite scroll hook
  useEffect(() => {
    setError(infiniteScrollError?.message || null);
  }, [infiniteScrollError]);

  // Fetch entity details when results change
  useEffect(() => {
    async function loadEntityDetails() {
      if (results.length === 0) return;

      // Collect all unique entity IDs from results
      const entityIds = new Set<number>();
      for (const interaction of results) {
        entityIds.add(interaction.member_a_id);
        entityIds.add(interaction.member_b_id);
      }

      // Only fetch entities we don't already have
      const idsToFetch = [...entityIds].filter(id => !entityMap.has(id));
      if (idsToFetch.length === 0) return;

      const newEntities = await fetchEntitiesByIds(idsToFetch);
      console.log('Fetched entities:', Object.fromEntries(newEntities));
      if (newEntities.size > 0) {
        setEntityMap(prev => new Map([...prev, ...newEntities]));
      }
    }

    loadEntityDetails();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [results]);

  // Function to load all interactions for graph view
  const loadAllInteractions = useCallback(async () => {
    if (isLoadingAll) return;

    setIsLoadingAll(true);
    setError(null);

    try {
      const response = await searchInteractions("", filters, MAX_GRAPH_INTERACTIONS, 0);
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

  // Handler for clear filters
  const handleClearFilters = () => {
    onFilterChange({});
  };

  const handleRowClick = (row: MeilisearchInteraction) => {
    setSelectedInteraction(row);
    setDetailsOpen(true);
  };

  // Convert MeilisearchInteraction to format expected by GraphView
  const convertToGraphViewFormat = useCallback((interactions: MeilisearchInteraction[]) => {
    return interactions.map((interaction) => {
      const consensusSign = getConsensusSign(interaction.directions);
      const typeA = interaction.member_types[0] ? extractTypeLabel(interaction.member_types[0]) : 'Unknown';
      const typeB = interaction.member_types[1] ? extractTypeLabel(interaction.member_types[1]) : 'Unknown';

      return {
        id: interaction.interaction_key,
        entity_a: {
          id: interaction.member_a_id.toString(),
          canonical_identifier: interaction.member_a_id.toString(),
          display_name: `${typeA} ${interaction.member_a_id}`,
        },
        entity_b: {
          id: interaction.member_b_id.toString(),
          canonical_identifier: interaction.member_b_id.toString(),
          display_name: `${typeB} ${interaction.member_b_id}`,
        },
        has_directed_evidence: interaction.has_direction,
        consensus_sign: consensusSign,
        evidence_count: interaction.evidence.length,
        evidences: []
      };
    });
  }, []);

  // Handle export
  const handleExport = useCallback(() => {
    const dataToExport = viewMode === "network" && allInteractions.length > 0 ? allInteractions : results;

    const exportData = dataToExport.map(interaction => {
      const consensusSign = getConsensusSign(interaction.directions);
      return {
        'Interaction Key': interaction.interaction_key,
        'Member A ID': interaction.member_a_id,
        'Member B ID': interaction.member_b_id,
        'Member Types': interaction.member_types.join(', '),
        'Has Direction': interaction.has_direction ? 'Yes' : 'No',
        'Has Positive Sign': interaction.has_positive_sign ? 'Yes' : 'No',
        'Has Negative Sign': interaction.has_negative_sign ? 'Yes' : 'No',
        'Consensus Sign': consensusSign || 'Unknown',
        'Evidence Count': interaction.evidence.length
      };
    });

    exportToCSV(exportData, `interactions_explore_${viewMode}_${new Date().toISOString().split('T')[0]}`);
  }, [viewMode, allInteractions, results]);

  // Helper to render sign indicator
  const renderSignIndicator = (interaction: MeilisearchInteraction) => {
    const consensusSign = getConsensusSign(interaction.directions);

    if (interaction.has_direction) {
      return (
        <ArrowRight className={cn(
          "h-4 w-4",
          consensusSign === "positive" ? "text-green-500" :
            consensusSign === "negative" ? "text-red-500" :
              consensusSign === "mixed" ? "text-orange-500" :
                "text-muted-foreground"
        )} />
      );
    }
    return <Minus className="h-4 w-4 text-muted-foreground" />;
  };

  const searchPanel = (
    <div className="h-full overflow-hidden p-4">
      <DataCard
        className={cn("h-full min-w-0 flex flex-col")}
        title={`${formatNumber(totalResults)} interactions found`}
        viewMode={viewMode}
        onViewModeChange={setViewMode}
        onExport={handleExport}
      >
      {/* Mobile filter drawer */}
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
                  onFilterChange={onFilterChange}
                  onClearFilters={handleClearFilters}
                  isMobile
                />
              )}
            </div>
          </SheetContent>
        </Sheet>
      </div>

      {/* Results */}
      {viewMode === "table" ? (
        error ? (
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
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[35%] py-2">Source</TableHead>
                    <TableHead className="w-[50px] text-center py-2"></TableHead>
                    <TableHead className="w-[35%] py-2">Target</TableHead>
                    <TableHead className="w-[20%] text-center py-2">Evidence</TableHead>
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
              className="flex-1 min-h-0 overflow-y-auto"
            >
              <Table>
                <TableBody>
                  {results.map((row) => {
                    const swap = shouldSwapMembers(row.directions);
                    const sourceId = swap ? row.member_b_id : row.member_a_id;
                    const targetId = swap ? row.member_a_id : row.member_b_id;
                    const sourceEntity = entityMap.get(sourceId);
                    const targetEntity = entityMap.get(targetId);
                    // Get entity type from member_types array
                    const sourceTypeRaw = swap ? row.member_types[1] : row.member_types[0];
                    const targetTypeRaw = swap ? row.member_types[0] : row.member_types[1];
                    const sourceType = sourceTypeRaw ? extractTypeLabel(sourceTypeRaw) : undefined;
                    const targetType = targetTypeRaw ? extractTypeLabel(targetTypeRaw) : undefined;

                    return (
                      <TableRow
                        key={row.interaction_key}
                        onClick={() => handleRowClick(row)}
                        className="cursor-pointer hover:bg-muted/50"
                      >
                        <TableCell className="w-[35%] max-w-0">
                          <EntityBadge
                            displayName={sourceEntity?.display_name || String(sourceId)}
                            canonicalIdentifier={sourceEntity?.canonical_identifier || String(sourceId)}
                            entityType={sourceEntity?.entity_type_name || sourceType}
                          />
                        </TableCell>
                        <TableCell className="w-[50px] text-center">
                          <div className="flex justify-center">
                            {renderSignIndicator(row)}
                          </div>
                        </TableCell>
                        <TableCell className="w-[35%] max-w-0">
                          <EntityBadge
                            displayName={targetEntity?.display_name || String(targetId)}
                            canonicalIdentifier={targetEntity?.canonical_identifier || String(targetId)}
                            entityType={targetEntity?.entity_type_name || targetType}
                          />
                        </TableCell>
                        <TableCell className="w-[20%] text-center">
                          <Badge variant="outline">
                            {formatNumber(row.evidence.length)}
                          </Badge>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                  {/* Infinite scroll trigger */}
                  <TableRow style={{ display: hasMore ? 'table-row' : 'none' }}>
                    <TableCell colSpan={4} className="p-0">
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
                          <div className="h-4 w-4" />
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                </TableBody>
              </Table>

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
        <div className="flex-1 overflow-hidden" style={{ minHeight: '500px' }}>
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
                const meilisearchInteraction = allInteractions.find(i => i.interaction_key === interaction.id?.toString());
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
    </div>
  );

  const ontologyPanel = (
    <div className="h-full min-h-0 overflow-hidden p-4">
      <div className="h-full overflow-y-auto">
        <AnnotationFilterSidebar
          mode="interactions"
          filters={filters}
          filterCounts={filterCounts ?? {}}
          onFilterChange={onFilterChange}
        />
      </div>
    </div>
  );

  return (
    <div className="relative flex flex-col h-svh overflow-hidden">
      <div className="flex-1 min-h-0">
        {layoutMode === "split" ? (
          <ResizablePanelGroup direction="horizontal" className="h-full">
            <ResizablePanel defaultSize={68} minSize={50} className="min-h-0">
              {searchPanel}
            </ResizablePanel>
            <ResizableHandle withHandle className="mx-3" />
            <ResizablePanel defaultSize={32} minSize={25} className="min-h-0">
              {ontologyPanel}
            </ResizablePanel>
          </ResizablePanelGroup>
        ) : layoutMode === "ontology" ? (
          ontologyPanel
        ) : (
          searchPanel
        )}
      </div>

      {/* Interaction Details Sheet */}
      <InteractionDetailsSheet
        open={detailsOpen}
        onOpenChange={setDetailsOpen}
        interaction={selectedInteraction}
      />

      <div className="fixed bottom-4 right-4 z-40">
        <div className="inline-flex items-center rounded-full border bg-background/90 p-1 shadow-sm">
          <Button
            size="sm"
            variant={layoutMode === "search" ? "default" : "ghost"}
            onClick={() => setLayoutMode("search")}
            className="rounded-full h-8"
          >
            Search
          </Button>
          <Button
            size="sm"
            variant={layoutMode === "split" ? "default" : "ghost"}
            onClick={() => setLayoutMode("split")}
            className="rounded-full h-8"
          >
            Both
          </Button>
          <Button
            size="sm"
            variant={layoutMode === "ontology" ? "default" : "ghost"}
            onClick={() => setLayoutMode("ontology")}
            className="rounded-full h-8"
          >
            Ontology
          </Button>
        </div>
      </div>
    </div>
  );
}
