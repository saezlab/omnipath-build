<script lang="ts">
  import { Minus } from '@lucide/svelte';
  import { Badge } from '$lib/components/ui/badge/index.js';
  import { Button } from '$lib/components/ui/button/index.js';
  import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
  } from '$lib/components/ui/table/index.js';
  import EntityBadge from '$lib/components/entity/EntityBadge.svelte';
  import RelationLabel from './RelationLabel.svelte';
  import {
    getEntityBadgeIdentifier,
    getEntityDisplayName,
    getEntityTypeLabel,
    type EntityLike,
  } from '$lib/domain/display';
  import type { InteractionListRow } from '$lib/types/interactions';
  import { formatNumber } from '$lib/utils/format';

  interface Props {
    rows: InteractionListRow[];
    hasMore?: boolean;
    loadingMore?: boolean;
    onLoadMore?: () => void;
    onRowClick: (row: InteractionListRow) => void;
    onEntityClick: (entity: EntityLike) => void;
  }

  let {
    rows,
    hasMore = false,
    loadingMore = false,
    onLoadMore,
    onRowClick,
    onEntityClick,
  }: Props = $props();
</script>

{#snippet endpoint(entity: EntityLike)}
  <button
    type="button"
    onclick={(event) => {
      event.stopPropagation();
      onEntityClick(entity);
    }}
    class="block w-full cursor-pointer rounded-lg p-0.5 text-left transition-all hover:bg-primary/10 hover:ring-2 hover:ring-primary/35 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
  >
    <EntityBadge
      displayName={getEntityDisplayName(entity)}
      canonicalIdentifier={getEntityBadgeIdentifier(entity)}
      entityType={getEntityTypeLabel(entity)}
      resolutionStatus={entity.resolutionStatus}
    />
  </button>
{/snippet}

<Table class="table-fixed">
  <TableHeader>
    <TableRow>
      <TableHead class="w-[36%] pl-3">From</TableHead>
      <TableHead class="w-[22%] text-center">Relation</TableHead>
      <TableHead class="w-[36%]">To</TableHead>
      <TableHead class="w-20 pr-3 text-center">Evidence</TableHead>
    </TableRow>
  </TableHeader>
  <TableBody>
    {#each rows as row}
      <TableRow onclick={() => onRowClick(row)} class="cursor-pointer hover:bg-muted/50">
        <TableCell class="max-w-0 pl-3">{@render endpoint(row.subjectEntity)}</TableCell>
        <TableCell class="whitespace-normal text-center">
          {#if row.relation.predicate.trim()}
            <RelationLabel relation={row.relation} />
          {:else}
            <Minus class="mx-auto h-4 w-4 text-muted-foreground" />
          {/if}
        </TableCell>
        <TableCell class="max-w-0">{@render endpoint(row.objectEntity)}</TableCell>
        <TableCell class="pr-3 text-center">
          <Badge variant="outline">
            {formatNumber(row.relation.evidenceCount || 0)}
          </Badge>
        </TableCell>
      </TableRow>
    {/each}
    {#if hasMore}
      <TableRow>
        <TableCell colspan={4} class="p-0">
          <div class="flex justify-center py-4" style="min-height: 40px;">
            <Button variant="outline" onclick={onLoadMore} disabled={loadingMore}>
              {#if loadingMore}
                <div
                  class="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent mr-2"
                ></div>
                <span>Loading...</span>
              {:else}
                <span>Load more</span>
              {/if}
            </Button>
          </div>
        </TableCell>
      </TableRow>
    {/if}
  </TableBody>
</Table>
