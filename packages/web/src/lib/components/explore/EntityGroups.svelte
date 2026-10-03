<script lang="ts">
  import { entityFromWire } from '$lib/api/adapters';
  import type { WireEntity } from '$lib/api/contracts';
  import { untrack, type Snippet } from 'svelte';
  import { page } from '$app/state';
  import { releaseFetch } from '$lib/api/release';
  import { Button } from '$lib/components/ui/button/index.js';
  import type { SearchFilters } from '$lib/types/search';
  import type { EntityWithIdentifiers } from '$lib/types/entities';
  type Group = {
    group_key: string;
    group_label: string | null;
    is_group: boolean;
    member_count: number;
    entity: EntityWithIdentifiers;
    members: EntityWithIdentifiers[];
    nextMemberCursor: string | null;
  };
  let {
    query,
    filters,
    renderMember,
    strategy = 'chemical_connectivity',
  }: {
    strategy?: string;
    query: string;
    filters: SearchFilters;
    renderMember: Snippet<[EntityWithIdentifiers]>;
  } = $props();
  let groups = $state<Group[]>([]);
  let nextCursor = $state<string | null>(null);
  let loading = $state(false);
  let error = $state('');
  let generation = 0;
  const requestKey = $derived(
    JSON.stringify([query, filters, strategy, page.data.selectedRelease]),
  );
  let controller: AbortController | undefined;

  async function request(
    extra: Record<string, unknown>,
    q = query,
    f = filters,
    selectedStrategy = strategy,
  ) {
    const response = await releaseFetch('/app-api/entities/groups', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: controller?.signal,
      body: JSON.stringify({
        strategy: selectedStrategy,
        query: q,
        filters: f,
        member_limit: 1,
        ...extra,
      }),
    });
    if (!response.ok) throw new Error('Unable to load groups. Please retry.');
    const data = (await response.json()) as {
      groups: Array<
        Omit<Group, 'entity' | 'members'> & { entity: WireEntity; members: WireEntity[] }
      >;
      nextCursor: string | null;
    };
    return {
      ...data,
      groups: data.groups.map((group) => ({
        ...group,
        entity: entityFromWire(group.entity),
        members: group.members.map(entityFromWire),
      })),
    };
  }

  $effect(() => {
    const [q, f, selectedStrategy] = JSON.parse(requestKey);
    controller = new AbortController();
    const current = ++generation;
    groups = [];
    nextCursor = null;
    loading = true;
    error = '';
    untrack(() => request({}, q, f, selectedStrategy))
      .then((data) => {
        if (current !== generation) return;
        groups = data.groups;
        nextCursor = data.nextCursor;
      })
      .catch((e) => {
        if (current === generation) error = e.message;
      })
      .finally(() => {
        if (current === generation) loading = false;
      });
    return () => {
      generation++;
      controller?.abort();
    };
  });

  async function moreGroups() {
    if (loading || !nextCursor) return;
    const current = generation;
    loading = true;
    error = '';
    try {
      const data = await request({ cursor: nextCursor });
      if (current !== generation) return;
      groups = [...groups, ...data.groups];
      nextCursor = data.nextCursor;
    } catch (e) {
      if (current === generation) error = (e as Error).message;
    } finally {
      if (current === generation) loading = false;
    }
  }
</script>

{#if error}<p role="alert" class="mb-3 text-destructive">{error}</p>{/if}
{#if loading && !groups.length}<p role="status">Loading groups…</p>{/if}
<div
  class="grid gap-4"
  style="grid-template-columns: repeat(auto-fit, minmax(min(100%, max(280px, calc((100% - 3rem) / 4))), 1fr));"
>
  {#each groups as group (group.group_key)}
    {@render renderMember(group.entity)}
  {/each}
</div>
{#if !loading && !error && !groups.length}<p>No matching entities found.</p>{/if}
{#if nextCursor}<Button class="mt-4" variant="outline" disabled={loading} onclick={moreGroups}
    >Load more groups</Button
  >{/if}
