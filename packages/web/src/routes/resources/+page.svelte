<script lang="ts">
  import { goto } from '$app/navigation';
  import { page } from '$app/state';
  import {
    Search,
    SlidersHorizontal,
    X,
    Database,
    ArrowDownWideNarrow,
    ChevronDown,
  } from '@lucide/svelte';
  import { Button } from '$lib/components/ui/button/index.js';
  import { Input } from '$lib/components/ui/input/index.js';
  import { Badge } from '$lib/components/ui/badge/index.js';
  import * as DropdownMenu from '$lib/components/ui/dropdown-menu/index.js';
  import ResourceCard from '$lib/components/resources/ResourceCard.svelte';
  import {
    dimensions,
    resourceTags,
    filterResources,
    sortResources,
    formatBytes,
  } from '$lib/resources/catalog';
  import type { PageData } from './$types';

  let { data }: { data: PageData } = $props();
  let inputRef = $state<HTMLInputElement | null>(null);
  let draftQuery = $state('');
  let showFilters = $state(false);
  const query = $derived(page.url.searchParams.get('q') || '');
  const selectedTags = $derived(page.url.searchParams.getAll('tag'));
  const licenses = $derived(page.url.searchParams.getAll('use'));
  const sortLabels: Record<string, string> = {
    name: 'Name A–Z',
    entities: 'Most entities',
    relations: 'Most relations',
    size: 'Largest size',
  };
  const sort = $derived(page.url.searchParams.get('sort') || 'name');
  const tags = $derived(resourceTags(data.resources));
  const filtered = $derived(
    sortResources(filterResources(data.resources, query, selectedTags, licenses), sort),
  );
  const totals = $derived(
    filtered.reduce(
      (sum, r) => ({
        entities: sum.entities + r.entity_count,
        relations: sum.relations + r.interaction_count,
        bytes: sum.bytes + r.total_size_bytes,
      }),
      { entities: 0, relations: 0, bytes: 0 },
    ),
  );
  const activeCount = $derived(selectedTags.length + licenses.length);
  const number = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 });
  $effect(() => {
    draftQuery = query;
  });

  function tagCount(id: string, dimension: string) {
    const otherGroups = selectedTags.filter(
      (selected) => tags.find((tag) => tag.id === selected)?.dimension !== dimension,
    );
    return filterResources(data.resources, query, [...otherGroups, id], licenses).length;
  }
  function navigate(url: URL) {
    url.searchParams.delete('tab');
    void goto(url, { keepFocus: true, noScroll: true });
  }
  function setParam(key: string, value: string) {
    const url = new URL(page.url);
    if (value.trim()) url.searchParams.set(key, value.trim());
    else url.searchParams.delete(key);
    navigate(url);
  }
  function toggle(key: string, id: string) {
    const url = new URL(page.url);
    const values = url.searchParams.getAll(key);
    url.searchParams.delete(key);
    for (const value of values.filter((value) => value !== id)) url.searchParams.append(key, value);
    if (!values.includes(id)) url.searchParams.append(key, id);
    navigate(url);
  }
  function clear() {
    const url = new URL(page.url);
    for (const key of ['q', 'tag', 'use']) url.searchParams.delete(key);
    navigate(url);
  }
  function keydown(event: KeyboardEvent) {
    const target = event.target as HTMLElement;
    if (
      event.key === '/' &&
      !event.metaKey &&
      !event.ctrlKey &&
      !event.altKey &&
      !target.closest('input, textarea, select, [contenteditable="true"]')
    ) {
      event.preventDefault();
      inputRef?.focus();
      inputRef?.select();
    }
  }
</script>

<svelte:head><title>Resources · OmniPath</title></svelte:head>
<svelte:window onkeydown={keydown} />

<div class="resource-page">
  <section class="hero" aria-labelledby="resource-heading">
    <div>
      <h1 id="resource-heading">Explore resources</h1>
      <p class="intro">Browse and download the databases integrated into OmniPath.</p>
    </div>
    <div class="search-controls">
      <form
        class="search"
        onsubmit={(event) => {
          event.preventDefault();
          setParam('q', draftQuery);
        }}
      >
        <Search size={19} aria-hidden="true" />
        <Input
          class="resource-search-input"
          bind:ref={inputRef}
          bind:value={draftQuery}
          type="search"
          aria-label="Search resources"
          placeholder="Search resources, topics, entities…"
        />
        <Button
          variant="outline"
          type="submit"
          aria-label="Submit resource search"
          title="Search (press / to focus)">↵</Button
        >
      </form>
      <DropdownMenu.Root>
        <DropdownMenu.Trigger class="resource-sort" aria-label="Sort resources"
          ><ArrowDownWideNarrow size={17} aria-hidden="true" />{sortLabels[sort] ??
            sortLabels.name}<ChevronDown size={15} aria-hidden="true" /></DropdownMenu.Trigger
        >
        <DropdownMenu.Content align="end">
          <DropdownMenu.RadioGroup value={sort} onValueChange={(value) => setParam('sort', value)}>
            {#each Object.entries(sortLabels) as [value, label]}<DropdownMenu.RadioItem {value}
                >{label}</DropdownMenu.RadioItem
              >{/each}
          </DropdownMenu.RadioGroup>
        </DropdownMenu.Content>
      </DropdownMenu.Root>
    </div>
  </section>

  <section class="filters" aria-label="Resource filters">
    <div class="quick-filters">
      <Button
        variant="outline"
        class={`pill filter-toggle ${showFilters ? 'active' : ''}`}
        aria-expanded={showFilters}
        aria-controls="all-resource-filters"
        onclick={() => (showFilters = !showFilters)}
        ><SlidersHorizontal size={15} />All filters{#if activeCount}<Badge
            variant="secondary"
            class="count">{activeCount}</Badge
          >{/if}</Button
      >
      <Button
        variant="outline"
        class={`pill ${!selectedTags.length && !licenses.length ? 'active' : ''}`}
        onclick={() => {
          const url = new URL(page.url);
          url.searchParams.delete('tag');
          url.searchParams.delete('use');
          navigate(url);
        }}
        >All resources <Badge variant="secondary" class="count"
          >{filterResources(data.resources, query, [], []).length}</Badge
        ></Button
      >
      {#each tags.filter((tag) => tag.dimension === 'topic') as tag (tag.id)}
        <Button
          variant="outline"
          class={`pill ${selectedTags.includes(tag.id) ? 'active' : ''}`}
          aria-pressed={selectedTags.includes(tag.id)}
          title={tag.description}
          onclick={() => toggle('tag', tag.id)}
          >{tag.label}<Badge variant="secondary" class="count"
            >{tagCount(tag.id, tag.dimension)}</Badge
          ></Button
        >
      {/each}
    </div>
    {#if showFilters}
      <div id="all-resource-filters" class="filter-panel">
        {#each dimensions as dimension}
          <fieldset>
            <legend>{dimension.label}</legend>
            <div class="options">
              {#each tags.filter((tag) => tag.dimension === dimension.id) as tag (tag.id)}
                <Button
                  variant="outline"
                  class={`pill ${selectedTags.includes(tag.id) ? 'active' : ''}`}
                  aria-pressed={selectedTags.includes(tag.id)}
                  title={tag.description}
                  onclick={() => toggle('tag', tag.id)}
                  >{tag.label}<Badge variant="secondary" class="count"
                    >{tagCount(tag.id, tag.dimension)}</Badge
                  ></Button
                >
              {/each}
            </div>
          </fieldset>
        {/each}
        <fieldset>
          <legend>License use</legend>
          <div class="options">
            {#each ['academic', 'commercial'] as audience}
              <Button
                variant="outline"
                class={`pill ${licenses.includes(audience) ? 'active' : ''}`}
                aria-pressed={licenses.includes(audience)}
                onclick={() => toggle('use', audience)}
                >{audience === 'academic' ? 'Academic use' : 'Commercial use'}<Badge
                  variant="secondary"
                  class="count"
                  >{filterResources(data.resources, query, selectedTags, [
                    ...new Set([...licenses, audience]),
                  ]).length}</Badge
                ></Button
              >
            {/each}
          </div>
        </fieldset>
      </div>
    {/if}
    {#if activeCount || query}
      <div class="active-filters" aria-label="Selected filters">
        {#if query}<Button variant="outline" class="selected" onclick={() => setParam('q', '')}
            >Search: {query}<X size={13} /></Button
          >{/if}
        {#each selectedTags as id}<Button
            variant="outline"
            class="selected"
            onclick={() => toggle('tag', id)}
            aria-label={`Remove ${tags.find((t) => t.id === id)?.label ?? id} filter`}
            >{tags.find((t) => t.id === id)?.label ?? id}<X size={13} /></Button
          >{/each}
        {#each licenses as audience}<Button
            variant="outline"
            class="selected"
            onclick={() => toggle('use', audience)}
            >{audience === 'academic'
              ? 'Academic use'
              : audience === 'commercial'
                ? 'Commercial use'
                : audience}<X size={13} /></Button
          >{/each}
        <Button variant="outline" class="clear" onclick={clear}>Clear all</Button>
      </div>
    {/if}
  </section>

  <div class="results-line">
    <span aria-live="polite">{filtered.length} of {data.resources.length} resources</span><span
      >Ring: share of records · Center: download size</span
    >
  </div>
  {#if filtered.length}
    <div class="cards">
      {#each filtered as resource (resource.resource_id)}<ResourceCard
          {resource}
          {selectedTags}
          onTagClick={(id) => toggle('tag', id)}
        />{/each}
    </div>
  {:else}
    <div class="empty">
      <Database size={30} />
      <h2>{data.resourcesUnavailable ? 'Resources are unavailable' : 'No matching resources'}</h2>
      <p>
        {data.resourcesUnavailable
          ? 'The API has no resources available for this release.'
          : 'Try another search or remove a filter to broaden your results.'}
      </p>
      {#if !data.resourcesUnavailable}<Button variant="outline" class="pill" onclick={clear}
          >Clear all filters</Button
        >{/if}
    </div>
  {/if}
  <footer>
    <strong><Database size={16} />{filtered.length} resources</strong><span
      >{number.format(totals.entities)} entities</span
    ><span>{number.format(totals.relations)} relations</span><span
      >{formatBytes(totals.bytes)} total size</span
    >
  </footer>
</div>

<style>
  .resource-page {
    --resource-cyan: #087e8b;
    --resource-green: #419538;
    --resource-panel: var(--card);
    --resource-line: var(--border);
    padding: 30px 0 0;
  }
  :global(.dark) .resource-page {
    --resource-cyan: #11c5d8;
    --resource-green: #6bd05d;
    --resource-panel: #0c1a23;
    --resource-line: #243b47;
  }
  .hero {
    display: grid;
    grid-template-columns: 1fr 1.4fr;
    gap: 30px;
    align-items: end;
    margin-bottom: 25px;
  }
  h1 {
    font-size: 28px;
    font-weight: 730;
    letter-spacing: -0.035em;
    line-height: 1.2;
  }
  .intro {
    color: var(--muted-foreground);
    margin-top: 8px;
    font-size: 14px;
  }
  .search-controls {
    display: flex;
    gap: 12px;
  }
  .search,
  .resource-page :global(.resource-sort) {
    display: flex;
    align-items: center;
    gap: 12px;
    border: 1px solid var(--resource-line);
    border-radius: 12px;
    background: var(--resource-panel);
    height: 50px;
    padding: 0 14px;
  }
  .search {
    flex: 1;
    min-width: 0;
  }
  .search:focus-within,
  .resource-page :global(.resource-sort):focus-within {
    outline: 2px solid var(--resource-cyan);
    outline-offset: 2px;
  }
  .resource-page :global(.resource-search-input) {
    width: 100%;
    min-width: 0;
    background: transparent;
    border: 0;
    box-shadow: none;
    padding: 0;
    outline: none;
    font-size: 13px;
  }
  .search :global(button) {
    padding: 2px 8px;
    border: 1px solid var(--resource-line);
    border-radius: 5px;
    color: var(--muted-foreground);
  }
  .quick-filters,
  .options,
  .active-filters {
    display: flex;
    flex-wrap: nowrap;
    gap: 8px;
    align-items: center;
    max-width: 100%;
    overflow-x: auto;
    padding: 3px 0 6px;
    scrollbar-width: thin;
    scrollbar-color: var(--resource-line) transparent;
  }
  .quick-filters > :global(*),
  .options > :global(*),
  .active-filters > :global(*) {
    flex-shrink: 0;
    white-space: nowrap;
  }
  .resource-page :global(.pill) {
    height: auto;
    min-height: 36px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    padding: 8px 12px;
    border: 1px solid var(--resource-line);
    border-radius: 999px;
    background: var(--resource-panel);
    font-size: 12px;
    cursor: pointer;
    transition:
      border-color 0.15s,
      background 0.15s;
  }
  .resource-page :global(.pill):hover {
    border-color: var(--resource-cyan);
  }
  .resource-page :global(.pill.active) {
    border-color: var(--resource-cyan);
    background: color-mix(in srgb, var(--resource-cyan) 13%, var(--resource-panel));
    color: var(--resource-cyan);
  }
  .resource-page :global(.count) {
    height: auto;
    min-height: 18px;
    color: inherit;
    font-size: 10px;
    font-variant-numeric: tabular-nums;
    padding: 1px 6px;
    background: color-mix(in srgb, var(--resource-cyan) 9%, var(--resource-panel));
    border-radius: 999px;
  }
  .resource-page :global(.filter-toggle) {
    margin-left: 0;
  }
  .filter-panel {
    margin-top: 15px;
    padding: 20px;
    border: 1px solid var(--resource-line);
    border-radius: 14px;
    background: var(--resource-panel);
  }
  fieldset {
    border: 0;
    margin-top: 15px;
    min-width: 0;
  }
  legend {
    font-size: 12px;
    font-weight: 650;
    margin-bottom: 9px;
  }
  .active-filters {
    margin-top: 15px;
  }
  .resource-page :global(.selected) {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    font-size: 11px;
    background: color-mix(in srgb, var(--resource-cyan) 10%, var(--resource-panel));
    padding: 5px 9px;
    border-radius: 6px;
  }
  .resource-page :global(.clear) {
    color: var(--muted-foreground);
    text-decoration: underline;
    font-size: 12px;
    padding: 5px;
  }
  .results-line {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    gap: 8px;
    font-size: 11px;
    color: var(--muted-foreground);
    margin: 21px 0 12px;
  }
  .cards {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 16px;
    align-items: start;
  }
  .empty {
    display: flex;
    align-items: center;
    flex-direction: column;
    gap: 12px;
    border: 1px dashed var(--resource-line);
    border-radius: 16px;
    padding: 65px 20px;
    text-align: center;
    color: var(--muted-foreground);
  }
  .empty h2 {
    color: var(--foreground);
    font-weight: 650;
  }
  .empty p {
    font-size: 14px;
  }
  footer {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 22px;
    padding: 24px 0 8px;
    color: var(--muted-foreground);
    font-size: 12px;
  }
  footer strong {
    display: flex;
    align-items: center;
    gap: 8px;
    color: var(--foreground);
    font-weight: 600;
  }

  @media (max-width: 1100px) {
    .cards {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .hero {
      grid-template-columns: 1fr;
      gap: 20px;
    }
  }
  @media (max-width: 650px) {
    .cards {
      grid-template-columns: 1fr;
    }
    .search-controls {
      flex-wrap: wrap;
    }
    .search {
      flex-basis: 100%;
    }
    .resource-page :global(.filter-toggle) {
      margin-left: 0;
    }
    .resource-page {
      padding-top: 20px;
    }
    .filter-panel {
      padding: 14px;
    }
  }
</style>
