<script lang="ts">
  import WorkspacePanel from '$lib/components/workspace/WorkspacePanel.svelte';

  import { untrack } from 'svelte';
  import { page as currentPage } from '$app/state';
  import { formatTaxonomy } from '$lib/utils/taxonomy';
  import { Button } from '$lib/components/ui/button/index.js';
  import { Checkbox } from '$lib/components/ui/checkbox/index.js';
  import { Label } from '$lib/components/ui/label/index.js';
  import type { SearchFilters } from '$lib/types/search';
  import { fetchScopedRelationFacetCounts } from '$lib/api/client';
  import { getIdentifierTypeLabel, getRelationPredicateLabel } from '$lib/domain/display';
  import { getEntityTypeEmoji } from '$lib/utils/entity-types';
  import { splitEmptyOptions } from '$lib/utils/facets';
  import { truncateTitle } from '$lib/actions/truncate-title';
  import { formatNumber } from '$lib/utils/format';

  interface Props {
    filters: SearchFilters;
    onFilterChange: (filters: SearchFilters) => void;
    onClearFilters?: () => void;
    isMobile?: boolean;
    scopedEntityIds?: string[];
    scopedAnnotationIds?: string[];
    scopeEndpointMode?: 'any' | 'both';
    scopeMode?: 'union' | 'intersection';
    queryEntityIds?: string[];
  }

  let {
    filters,
    onFilterChange,
    onClearFilters,
    isMobile = false,
    scopedEntityIds,
    scopedAnnotationIds,
    scopeEndpointMode = 'any',
    scopeMode = 'union',
    queryEntityIds,
  }: Props = $props();

  // Qualifiers of "affects" relations: X increases the activity of Y through binding.
  const qualifierFilters = [
    { key: 'object_direction_qualifier', label: 'Direction' },
    { key: 'object_aspect_qualifier', label: 'Aspect' },
    { key: 'causal_mechanism_qualifier', label: 'Mechanism' },
  ] as const;
  type QualifierKey = (typeof qualifierFilters)[number]['key'];
  const MECHANISM_PREVIEW = 5;
  let showAllMechanisms = $state(false);
  let qualifierOptions = $state<Record<string, string[]>>({});
  let predicatesByCategory = $state<Record<string, string[]>>({});
  let interactionTypeOptions = $state<string[]>([]);
  let sourceOptions = $state<string[]>([]);
  let taxonomyOptions = $state<string[]>([]);
  let taxonomyNames = $state<Record<string, string>>({});
  const TAXONOMY_PAGE_SIZE = 15;
  let taxonomyFacetLimit = $state(TAXONOMY_PAGE_SIZE);
  let taxonomyQuery = $state('');
  let taxonomyDraft = $state('');
  $effect(() => {
    const next = taxonomyDraft;
    const timer = setTimeout(() => {
      taxonomyQuery = next;
      taxonomyFacetLimit = TAXONOMY_PAGE_SIZE;
    }, 200);
    return () => clearTimeout(timer);
  });
  let taxonomyHasMore = $state(false);
  let scopedFacetCounts = $state<Map<string, { count: number; category?: string | null }>>(
    new Map(),
  );
  let loading = $state(true);

  // Fetch facet counts (scoped when selection present, global otherwise) and build filter options.
  // Counts reflect the current scope AND all OTHER active filters (cross-facet filtering).
  const scopeKey = $derived(
    JSON.stringify([
      currentPage.data.selectedRelease,
      {
        entityIds: [...(scopedEntityIds || []), ...(queryEntityIds || [])],
        endpointMode: scopeEndpointMode,
        mode: scopeMode,
        annotationTermIds: scopedAnnotationIds?.length ? scopedAnnotationIds : undefined,
        predicates: filters.predicates,
        relation_categories: filters.relation_categories,
        object_aspect_qualifier: filters.object_aspect_qualifier,
        object_direction_qualifier: filters.object_direction_qualifier,
        causal_mechanism_qualifier: filters.causal_mechanism_qualifier,
        interactionTypes: filters.interaction_types,
        sources: filters.sources,
        taxonomyIds: filters.taxonomy_ids,
      },
    ]),
  );
  $effect(() => {
    void scopeKey;
    taxonomyFacetLimit = TAXONOMY_PAGE_SIZE;
  });
  const requestKey = $derived(JSON.stringify([scopeKey, taxonomyFacetLimit, taxonomyQuery]));
  $effect(() => {
    const [key, visibleLimit, search] = JSON.parse(requestKey);
    const [, scope] = JSON.parse(key);
    const controller = new AbortController();

    let cancelled = false;
    loading = true;
    untrack(() =>
      fetchScopedRelationFacetCounts(
        { ...scope, taxonomyLimit: visibleLimit + 1, taxonomyQuery: search },
        controller.signal,
      ),
    )
      .then((counts) => {
        if (cancelled) return;
        const map = new Map<string, { count: number; category?: string | null }>();
        const categories: Record<string, string[]> = {};
        const types: string[] = [];
        const sources: string[] = [];
        const taxonomies: string[] = [];

        const names: Record<string, string> = {};
        for (const c of counts) {
          map.set(`${c.facetName}:${c.facetValue}`, {
            count: c.scopedCount,
            category: c.facetCategory,
          });
          if (c.facetName === 'predicate' && c.facetCategory) {
            if (!categories[c.facetCategory]) categories[c.facetCategory] = [];
            categories[c.facetCategory].push(c.facetValue);
          } else if (c.facetName === 'participant_type') {
            types.push(c.facetValue);
          } else if (c.facetName === 'source') {
            sources.push(c.facetValue);
          } else if (c.facetName === 'taxonomy_id') {
            taxonomies.push(c.facetValue);
            if (c.facetLabel) names[c.facetValue] = c.facetLabel;
          }
        }

        qualifierOptions = Object.fromEntries(
          qualifierFilters.map(({ key }) => [
            key,
            [
              ...new Set([
                ...counts.filter((c) => c.facetName === key).map((c) => c.facetValue),
                ...(filters[key] || []),
              ]),
            ],
          ]),
        );
        scopedFacetCounts = map;
        predicatesByCategory = categories;
        interactionTypeOptions = types;
        sourceOptions = sources;
        const selected = scope.taxonomyIds || [];
        const matching = taxonomies.filter(
          (id) =>
            !search ||
            id.toLowerCase().includes(search.toLowerCase()) ||
            (names[id] || '').toLowerCase().includes(search.toLowerCase()),
        );
        taxonomyHasMore = matching.length > visibleLimit;
        taxonomyOptions = taxonomies.filter(
          (id) => matching.slice(0, visibleLimit).includes(id) || selected.includes(id),
        );
        taxonomyNames = names;
      })
      .catch(() => {
        if (!cancelled) {
          qualifierOptions = {};
          scopedFacetCounts = new Map();
          predicatesByCategory = {};
          interactionTypeOptions = [];
          sourceOptions = [];
          taxonomyOptions = [];
        }
      })
      .finally(() => {
        if (!cancelled) loading = false;
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  });

  const activeFilterCount = $derived(
    Object.entries(filters).reduce((count, [, value]) => {
      if (Array.isArray(value)) return count + value.length;
      if (value !== null && value !== undefined) return count + 1;
      return count;
    }, 0),
  );

  function handleArrayToggle(filterKey: keyof SearchFilters, value: string) {
    const currentValues = (filters[filterKey] as string[] | undefined) || [];
    const newValues = currentValues.includes(value)
      ? currentValues.filter((v) => v !== value)
      : [...currentValues, value];

    onFilterChange({
      ...filters,
      [filterKey]: newValues.length > 0 ? newValues : undefined,
    });
  }

  function handlePredicateToggle(category: string, predicate: string) {
    const currentPredicates = filters.predicates || [];
    const isSelected = currentPredicates.includes(predicate);
    const nextPredicates = isSelected
      ? currentPredicates.filter((value) => value !== predicate)
      : [...currentPredicates, predicate];
    const currentCategories = filters.relation_categories || [];
    const nextCategories =
      !isSelected && !currentCategories.includes(category)
        ? [...currentCategories, category]
        : currentCategories;

    onFilterChange({
      ...filters,
      predicates: nextPredicates.length > 0 ? nextPredicates : undefined,
      relation_categories: nextCategories.length > 0 ? nextCategories : undefined,
    });
  }

  function formatParticipantType(value: string) {
    const label = getIdentifierTypeLabel(value);
    return {
      label,
      icon: getEntityTypeEmoji(label),
    };
  }

  function getCount(filterName: string, value: string): number | undefined {
    return scopedFacetCounts.get(`${filterName}:${value}`)?.count;
  }

  /** Qualifier values with results, most frequent first; selected values always stay. */
  function qualifierValues(key: QualifierKey): string[] {
    const selected = filters[key] || [];
    return splitEmptyOptions(
      qualifierOptions[key] || [],
      (value) => getCount(key, value) ?? 0,
      (value) => selected.includes(value),
    ).shown.sort((a, b) => (getCount(key, b) ?? 0) - (getCount(key, a) ?? 0));
  }
  const directionValues = $derived(qualifierValues('object_direction_qualifier'));
  const aspectValues = $derived(qualifierValues('object_aspect_qualifier'));
  const mechanismValues = $derived(qualifierValues('causal_mechanism_qualifier'));
  const visibleMechanisms = $derived(
    showAllMechanisms
      ? mechanismValues
      : mechanismValues.filter(
          (value, index) =>
            index < MECHANISM_PREVIEW || (filters.causal_mechanism_qualifier || []).includes(value),
        ),
  );
  const hasEffectFilters = $derived(
    directionValues.length + aspectValues.length + mechanismValues.length > 0,
  );

  // Sources with no relations for the current filters are hidden.
  const sourceSplit = $derived(
    splitEmptyOptions(
      sourceOptions,
      (option) => getCount('source', option),
      (option) => (filters.sources || []).includes(option),
    ),
  );

  function formatPredicate(value: string): string {
    return getRelationPredicateLabel(value) || value;
  }

  function formatCategory(value: string): string {
    if (value.toLowerCase() === 'default') return 'General';
    return value.charAt(0).toUpperCase() + value.slice(1).replace(/_/g, ' ');
  }
</script>

{#snippet filterOptionRow(
  filterKey: keyof SearchFilters,
  value: string,
  label: string,
  selectedValues: string[],
  onToggle: () => void,
  icon?: string,
  count?: number,
)}
  <div class="flex items-center justify-between py-0.5 gap-2">
    <Label
      for={`${filterKey}-${value}`}
      class="flex min-w-0 flex-1 cursor-pointer items-center gap-2 text-sm font-normal leading-5 text-foreground {selectedValues?.includes(
        value,
      )
        ? 'font-medium'
        : ''}"
    >
      <Checkbox
        id={`${filterKey}-${value}`}
        checked={selectedValues?.includes(value) || false}
        onCheckedChange={onToggle}
        class="h-4 w-4 flex-shrink-0"
      />
      <span class="truncate" use:truncateTitle={label}>
        {#if icon}<span class="mr-1.5">{icon}</span>{/if}
        {label}
      </span>
    </Label>
    {#if count != null}
      <span class="text-xs text-muted-foreground tabular-nums flex-shrink-0">
        {formatNumber(count)}
      </span>
    {/if}
  </div>
{/snippet}

{#snippet sectionHeading(title: string, showClear = false)}
  <div class="flex items-center justify-between gap-2">
    <h3 class="text-sm font-semibold">{title}</h3>
    {#if showClear && !isMobile && activeFilterCount > 0 && onClearFilters}
      <Button
        variant="ghost"
        size="xs"
        onclick={onClearFilters}
        class="h-auto px-0 text-xs text-muted-foreground"
      >
        Clear all
      </Button>
    {/if}
  </div>
{/snippet}

{#snippet content()}
  <div class={isMobile ? 'space-y-6' : 'contents'} class:opacity-70={loading}>
    <WorkspacePanel id="filters" title="Relation filters" enabled={!isMobile}>
      <div class="space-y-6">
        {#if Object.keys(predicatesByCategory).length > 0}
          <div class="space-y-2">
            {@render sectionHeading('Relation types', true)}
            <div class="space-y-2">
              {#each Object.entries(predicatesByCategory) as [category, predicates]}
                {@const isDefaultOnly =
                  category.toLowerCase() === 'default' &&
                  Object.keys(predicatesByCategory).length === 1}
                <div class="space-y-2">
                  {#if !isDefaultOnly}
                    {@render filterOptionRow(
                      'relation_categories',
                      category,
                      formatCategory(category),
                      filters.relation_categories || [],
                      () => handleArrayToggle('relation_categories', category),
                    )}
                  {/if}
                  <div
                    class="space-y-1 max-h-64 overflow-y-auto pr-2 {isDefaultOnly ? '' : 'pl-4'}"
                  >
                    {#each predicates as predicate}
                      {@render filterOptionRow(
                        'predicates',
                        predicate,
                        formatPredicate(predicate),
                        filters.predicates || [],
                        () => handlePredicateToggle(category, predicate),
                        undefined,
                        getCount('predicate', predicate),
                      )}
                    {/each}
                  </div>
                </div>
              {/each}
            </div>
          </div>
        {/if}
      </div>
    </WorkspacePanel>
    {#if hasEffectFilters}
      <WorkspacePanel id="effect" title="Effect" enabled={!isMobile}>
        <div class="space-y-4">
          {#if isMobile}{@render sectionHeading('Effect')}{/if}
          {#if directionValues.length}
            <div class="space-y-2">
              <p class="effect-heading" role="heading" aria-level="4">Direction</p>
              <div class="flex flex-wrap gap-1.5" role="group" aria-label="Direction">
                {#each directionValues as value}
                  {@const pressed = (filters.object_direction_qualifier || []).includes(value)}
                  <Button
                    size="xs"
                    variant={pressed ? 'secondary' : 'outline'}
                    aria-pressed={pressed}
                    onclick={() => handleArrayToggle('object_direction_qualifier', value)}
                  >
                    <span aria-hidden="true"
                      >{value === 'increased' ? '↑' : value === 'decreased' ? '↓' : ''}</span
                    >
                    {formatCategory(value)}
                    <span class="tabular-nums text-muted-foreground"
                      >{formatNumber(getCount('object_direction_qualifier', value) ?? 0)}</span
                    >
                  </Button>
                {/each}
              </div>
            </div>
          {/if}
          {#if aspectValues.length}
            <div class="space-y-1.5">
              <p class="effect-heading" role="heading" aria-level="4">Aspect</p>
              <div class="space-y-1">
                {#each aspectValues as value}
                  {@render filterOptionRow(
                    'object_aspect_qualifier',
                    value,
                    formatCategory(value),
                    filters.object_aspect_qualifier || [],
                    () => handleArrayToggle('object_aspect_qualifier', value),
                    undefined,
                    getCount('object_aspect_qualifier', value) ?? 0,
                  )}
                {/each}
              </div>
            </div>
          {/if}
          {#if mechanismValues.length}
            <div class="space-y-1.5">
              <p class="effect-heading" role="heading" aria-level="4">Mechanism</p>
              <div class="space-y-1">
                {#each visibleMechanisms as value}
                  {@render filterOptionRow(
                    'causal_mechanism_qualifier',
                    value,
                    formatCategory(value),
                    filters.causal_mechanism_qualifier || [],
                    () => handleArrayToggle('causal_mechanism_qualifier', value),
                    undefined,
                    getCount('causal_mechanism_qualifier', value) ?? 0,
                  )}
                {/each}
              </div>
              {#if mechanismValues.length > visibleMechanisms.length || showAllMechanisms}
                <Button
                  variant="ghost"
                  size="xs"
                  class="h-auto px-0 text-xs text-muted-foreground"
                  onclick={() => (showAllMechanisms = !showAllMechanisms)}
                >
                  {showAllMechanisms
                    ? 'Show fewer'
                    : `Show ${mechanismValues.length - visibleMechanisms.length} more`}
                </Button>
              {/if}
            </div>
          {/if}
        </div>
      </WorkspacePanel>
    {/if}
    {#if interactionTypeOptions.length > 0}
      <WorkspacePanel id="interaction_types" title="Participant types" enabled={!isMobile}>
        <div class="space-y-2">
          {#if isMobile}{@render sectionHeading(
              'Participant types',
              Object.keys(predicatesByCategory).length === 0,
            )}{/if}
          <div class="space-y-1 max-h-64 overflow-y-auto pr-2">
            {#each interactionTypeOptions as option}
              {@const participantType = formatParticipantType(option)}
              {@render filterOptionRow(
                'interaction_types',
                option,
                participantType.label,
                filters.interaction_types || [],
                () => handleArrayToggle('interaction_types', option),
                participantType.icon,
                getCount('participant_type', option),
              )}
            {/each}
          </div>
        </div>
      </WorkspacePanel>
    {/if}
    <WorkspacePanel id="sources" title="Sources" enabled={!isMobile}>
      <div class="space-y-2">
        {#if isMobile}{@render sectionHeading(
            'Sources',
            Object.keys(predicatesByCategory).length === 0 && interactionTypeOptions.length === 0,
          )}{/if}
        <div class="space-y-1 max-h-64 overflow-y-auto pr-2">
          {#each sourceSplit.shown as option}
            {@render filterOptionRow(
              'sources',
              option,
              option,
              filters.sources || [],
              () => handleArrayToggle('sources', option),
              undefined,
              getCount('source', option),
            )}
          {/each}
        </div>
      </div>
    </WorkspacePanel>
    {#if taxonomyOptions.length > 0 || taxonomyQuery}
      <WorkspacePanel id="ncbi_tax_id" title="Taxonomy" enabled={!isMobile}>
        <div class="space-y-2">
          {#if isMobile}{@render sectionHeading(
              'Taxonomy',
              Object.keys(predicatesByCategory).length === 0 &&
                interactionTypeOptions.length === 0 &&
                sourceOptions.length === 0,
            )}{/if}
          <div class="space-y-1 max-h-64 overflow-y-auto pr-2">
            <input
              aria-label="Search taxonomy filters"
              placeholder="Search taxonomy…"
              class="mb-2 w-full rounded border px-2 py-1 text-sm"
              bind:value={taxonomyDraft}
            />
            {#each taxonomyOptions as option}
              {@render filterOptionRow(
                'taxonomy_ids',
                option,
                formatTaxonomy(option, taxonomyNames[option]) || option,
                filters.taxonomy_ids || [],
                () => handleArrayToggle('taxonomy_ids', option),
                undefined,
                getCount('taxonomy_id', option),
              )}
            {/each}
            {#if taxonomyHasMore}
              <Button
                variant="ghost"
                size="sm"
                class="mt-2 w-full text-xs"
                disabled={loading}
                onclick={() => (taxonomyFacetLimit += TAXONOMY_PAGE_SIZE)}
              >
                Load more
              </Button>
            {/if}
          </div>
        </div>
      </WorkspacePanel>
    {/if}
  </div>
{/snippet}

{@render content()}

<style>
  /* Group labels inside the Effect box. Not h4: WorkspacePanel hides h4 section titles. */
  .effect-heading {
    margin: 0;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--muted-foreground);
  }
</style>
