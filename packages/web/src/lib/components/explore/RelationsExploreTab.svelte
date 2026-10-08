<script lang="ts">
  import ExplorerWorkspace from '$lib/components/workspace/ExplorerWorkspace.svelte';
  import WorkspacePanel from '$lib/components/workspace/WorkspacePanel.svelte';
  import { untrack } from 'svelte';
  import { page as currentPage } from '$app/state';
  import { Filter, X } from '@lucide/svelte';
  import { Badge } from '$lib/components/ui/badge/index.js';
  import { Button } from '$lib/components/ui/button/index.js';
  import { Alert, AlertDescription } from '$lib/components/ui/alert/index.js';
  import {
    Sheet,
    SheetContent,
    SheetHeader,
    SheetTitle,
    SheetTrigger,
  } from '$lib/components/ui/sheet/index.js';
  import EntityDetailsDialog from '$lib/components/entity/EntityDetailsDialog.svelte';
  import InteractionFilterSidebar from '$lib/components/interactions/InteractionFilterSidebar.svelte';
  import InteractionDetailsSheet from '$lib/components/interactions/InteractionDetailsSheet.svelte';
  import RelationsTable from '$lib/components/interactions/RelationsTable.svelte';
  import { IsMobile } from '$lib/hooks/is-mobile.svelte';
  import { collectEntityKeys } from '$lib/features/explorer/paged-query';
  import {
    fetchRelationsSearch,
    fetchEntitiesSearch,
    type EntitySearchCursor,
  } from '$lib/api/client';
  import type { EntityLike } from '$lib/domain/display';
  import type { SearchFilters } from '$lib/types/search';
  import type { InteractionListRow } from '$lib/types/interactions';

  interface Props {
    filters: SearchFilters;
    onFilterChange: (filters: SearchFilters) => void;
    query?: string;
    entitySearchFilters?: SearchFilters;
    scopedEntityIds?: string[];
    scopedAnnotationIds?: string[];
    scopeEndpointMode?: 'any' | 'both';
    scopeMode?: 'union' | 'intersection';
  }

  let {
    filters,
    onFilterChange,
    query = '',
    entitySearchFilters = {},
    scopedEntityIds,
    scopedAnnotationIds,
    scopeEndpointMode = 'any',
    scopeMode = 'union',
  }: Props = $props();

  const isMobile = new IsMobile();
  const RESULTS_PER_PAGE = 20;

  let results = $state<InteractionListRow[]>([]);
  let loading = $state(true);
  let loadingMore = $state(false);
  let hasMore = $state(true);
  let offset = $state(0);
  let error = $state<string | null>(null);
  let selectedInteraction = $state<InteractionListRow | null>(null);
  let detailsOpen = $state(false);
  let detailsEntity = $state<EntityLike | null>(null);
  let entityDetailsOpen = $state(false);
  let queryEntityIds = $state<string[]>([]);
  let queryEntityIdsLoading = $state(false);
  let queryEntityIdsError = $state<string | null>(null);

  function rowsFromSearch(
    data: Awaited<ReturnType<typeof fetchRelationsSearch>>,
  ): InteractionListRow[] {
    if (data.rows?.length) {
      return data.rows.filter((row) => row.relation && row.subjectEntity && row.objectEntity);
    }
    return [];
  }

  const activeFilterCount = $derived(
    Object.entries(filters).reduce((count, [, value]) => {
      if (Array.isArray(value)) return count + value.length;
      if (value !== null && value !== undefined) return count + 1;
      return count;
    }, 0),
  );

  const effectiveFilters = $derived({
    ...filters,
    ...(query.trim() && queryEntityIds.length > 0 ? { entity_ids: queryEntityIds } : {}),
    ...(scopedEntityIds && scopedEntityIds.length > 0 ? { scope_entity_ids: scopedEntityIds } : {}),
    ...(scopedEntityIds && scopedEntityIds.length > 0
      ? { scope_endpoint_mode: scopeEndpointMode }
      : {}),
    ...(scopedAnnotationIds && scopedAnnotationIds.length > 0
      ? { scope_annotation_ids: scopedAnnotationIds }
      : {}),
    ...(scopedEntityIds?.length || scopedAnnotationIds?.length
      ? { selection_scope_mode: scopeMode }
      : {}),
  });

  const queryKey = $derived(
    JSON.stringify([currentPage.data.selectedRelease, query.trim(), entitySearchFilters]),
  );
  // Resolve query text to entity IDs for relation scoping.
  $effect(() => {
    const [, q, searchFilters] = JSON.parse(queryKey);
    if (!q) {
      queryEntityIds = [];
      queryEntityIdsLoading = false;
      return;
    }

    let cancelled = false;
    const controller = new AbortController();
    queryEntityIds = [];
    queryEntityIdsError = null;
    queryEntityIdsLoading = true;
    untrack(() =>
      collectEntityKeys<EntitySearchCursor>(
        (cursor, signal) =>
          fetchEntitiesSearch({ query: q, limit: 200, cursor, filters: searchFilters }, signal),
        controller.signal,
      ),
    )
      .then((keys) => {
        if (cancelled) return;
        queryEntityIds = keys;
      })
      .catch((err) => {
        if (!cancelled) {
          queryEntityIds = [];
          queryEntityIdsError =
            err instanceof Error ? err.message : 'Failed to resolve entity search';
        }
      })
      .finally(() => {
        if (!cancelled) queryEntityIdsLoading = false;
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  });

  let searchGeneration = 0;
  const searchKey = $derived(
    JSON.stringify([
      currentPage.data.selectedRelease,
      effectiveFilters,
      query.trim(),
      queryEntityIdsLoading,
      queryEntityIds.length,
      queryEntityIdsError,
    ]),
  );
  $effect(() => {
    const current = ++searchGeneration;
    const [, f, q, resolvingQueryEntities, matchCount, resolutionError] = JSON.parse(searchKey);
    const queryHasNoEntityMatches = !!q && !resolvingQueryEntities && matchCount === 0;
    const controller = new AbortController();

    loadingMore = false;
    offset = 0;
    hasMore = true;
    loading = true;
    error = null;

    if (resolvingQueryEntities) {
      return;
    }

    if (queryHasNoEntityMatches) {
      error = resolutionError;
      results = [];
      loading = false;
      hasMore = false;
      return;
    }

    (async () => {
      try {
        const data = await untrack(() =>
          fetchRelationsSearch(
            { filters: f, limit: RESULTS_PER_PAGE, offset: 0 },
            controller.signal,
          ),
        );
        if (current !== searchGeneration) return;
        const hits = rowsFromSearch(data);

        results = hits;
        hasMore = hits.length === RESULTS_PER_PAGE;
        offset = RESULTS_PER_PAGE;
      } catch (err) {
        if (current !== searchGeneration) return;
        error = err instanceof Error ? err.message : 'Failed to load relations';
        hasMore = false;
      } finally {
        if (current === searchGeneration) loading = false;
      }
    })();
    return () => {
      searchGeneration++;
      controller.abort();
    };
  });

  async function loadMore() {
    if (loadingMore || !hasMore) return;
    const current = searchGeneration;
    loadingMore = true;
    try {
      const data = await fetchRelationsSearch({
        filters: effectiveFilters,
        limit: RESULTS_PER_PAGE,
        offset,
      });
      if (current !== searchGeneration) return;
      const hits = rowsFromSearch(data);

      results = [...results, ...hits];
      hasMore = hits.length === RESULTS_PER_PAGE;
      offset += RESULTS_PER_PAGE;
    } catch (err) {
      if (current !== searchGeneration) return;
      error = err instanceof Error ? err.message : 'Failed to load more relations';
    } finally {
      if (current === searchGeneration) loadingMore = false;
    }
  }

  function handleClearFilters() {
    onFilterChange({});
  }

  function handleRowClick(row: InteractionListRow) {
    selectedInteraction = row;
    detailsOpen = true;
  }

  function openEntityDetails(entity: EntityLike) {
    detailsEntity = entity;
    entityDetailsOpen = true;
  }
</script>

{#snippet searchPanel()}
  <div class="relative">
    {#if isMobile.current}
      <div class="lg:hidden p-4 border-b">
        <Sheet>
          <SheetTrigger>
            {#snippet child({ props })}
              <Button {...props} variant="outline" class="w-full">
                <Filter class="h-4 w-4 mr-2" />
                Filters
                {#if activeFilterCount > 0}
                  <Badge variant="secondary" class="ml-2">
                    {activeFilterCount}
                  </Badge>
                {/if}
              </Button>
            {/snippet}
          </SheetTrigger>
          <SheetContent side="left" class="w-[85%] sm:w-[400px] p-0">
            <SheetHeader class="px-6 py-4 border-b">
              <div class="flex items-center justify-between">
                <SheetTitle class="flex items-center gap-2">
                  <Filter class="h-5 w-5 text-primary" />
                  Filters
                </SheetTitle>
                {#if activeFilterCount > 0}
                  <Button
                    variant="ghost"
                    size="sm"
                    onclick={handleClearFilters}
                    class="flex items-center gap-1 text-muted-foreground hover:text-foreground"
                  >
                    <X class="h-4 w-4" />
                    Clear all
                  </Button>
                {/if}
              </div>
            </SheetHeader>
            <div class="h-[calc(100%-4rem)] overflow-y-auto">
              <InteractionFilterSidebar
                filters={effectiveFilters}
                {onFilterChange}
                onClearFilters={handleClearFilters}
                isMobile
                {scopedEntityIds}
                {scopedAnnotationIds}
                {scopeEndpointMode}
                {scopeMode}
                {queryEntityIds}
              />
            </div>
          </SheetContent>
        </Sheet>
      </div>
    {/if}

    {#if error}
      <div class="p-6">
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      </div>
    {:else if loading && results.length === 0}
      <div class="flex items-center justify-center h-full">
        <div class="flex items-center gap-2">
          <div
            class="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent"
          ></div>
          <span class="text-sm text-muted-foreground">Loading relations...</span>
        </div>
      </div>
    {:else if results.length > 0}
      <RelationsTable
        rows={results}
        {hasMore}
        {loadingMore}
        onLoadMore={loadMore}
        onRowClick={handleRowClick}
        onEntityClick={openEntityDetails}
      />
    {:else if !loading && results.length === 0}
      <div class="p-6 flex-1 flex items-center justify-center">
        <p class="text-muted-foreground text-center">
          {Object.keys(effectiveFilters).length > 0
            ? 'No relations found matching your criteria.'
            : 'Select filters to explore relations.'}
        </p>
      </div>
    {/if}
  </div>
{/snippet}

{#snippet desktopSidebar()}
  <ExplorerWorkspace
    name="relations"
    panels={['results', 'filters', 'effect', 'interaction_types', 'sources', 'ncbi_tax_id']}
  >
    <WorkspacePanel id="results" title="Results" busy={loading && results.length > 0}
      >{@render searchPanel()}</WorkspacePanel
    >
    <InteractionFilterSidebar
      filters={effectiveFilters}
      {onFilterChange}
      onClearFilters={handleClearFilters}
      {scopedEntityIds}
      {scopedAnnotationIds}
      {scopeEndpointMode}
      {scopeMode}
      {queryEntityIds}
    />
  </ExplorerWorkspace>
{/snippet}

<div class="relative flex min-h-0 flex-1 flex-col overflow-hidden">
  {#if isMobile.current}
    {@render searchPanel()}
  {:else}
    {@render desktopSidebar()}
  {/if}

  <!-- Interaction Details Sheet -->
  <InteractionDetailsSheet
    open={detailsOpen}
    onOpenChange={(open) => {
      detailsOpen = open;
    }}
    interaction={selectedInteraction}
  />

  <EntityDetailsDialog bind:open={entityDetailsOpen} entity={detailsEntity} />
</div>
