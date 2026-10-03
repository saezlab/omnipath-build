<script lang="ts">
  import InteractionDetailsSheet from '../interactions/InteractionDetailsSheet.svelte';
  import type { InteractionListRow } from '$lib/types/interactions';
  import {
    getEntityDisplayName,
    getRelationPredicateLabel,
    type EntityLike,
  } from '$lib/domain/display';
  import type { EntityRelation } from '$lib/domain/entity-types';
  import { annotationLabel, plainText } from '$lib/utils/annotation-presentation';
  import { measurementPresentation } from '$lib/utils/measurements';
  import { isPublicationTerm } from '$lib/utils/publications';
  export type Relationship = {
    groupOutgoing?: boolean;
    relation: EntityRelation;
    subjectEntity?: EntityLike;
    objectEntity?: EntityLike;
    annotations: Record<string, unknown>[];
  };
  let {
    entityKey,
    entityKeys,
    rows,
    total,
    onSelect,
  }: {
    entityKey: string;
    entityKeys?: string[];
    rows: Relationship[];
    total: number;
    onSelect: (entity: EntityLike) => void;
  } = $props();
  let selectedRelationship = $state<InteractionListRow | null>(null);
  let evidenceOpen = $state(false);
  function property(a: Record<string, unknown>) {
    const q = measurementPresentation(a);
    return `${annotationLabel(q?.sourceField || String(a.term || ''))}: ${q ? [q.value, q.unit].filter(Boolean).join(' ') : plainText(String(a.value || ''))}`;
  }
</script>

{#if rows.length}
  <section class="space-y-2">
    <h3 class="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
      Participants and composition
    </h3>
    <p class="text-xs text-muted-foreground">{rows.length} of {total} relationships</p>
    <div class="max-h-80 overflow-auto rounded-lg border">
      <table class="w-full text-left text-sm">
        <thead class="sticky top-0 bg-muted"
          ><tr
            ><th class="p-2">Relationship</th><th class="p-2">Entity</th><th class="p-2"
              >Properties and context</th
            ></tr
          ></thead
        >
        <tbody>
          {#each rows as row}
            {@const outgoing =
              row.groupOutgoing ??
              (entityKeys ?? [entityKey]).includes(row.relation.subjectEntityPk)}
            {@const other = outgoing ? row.objectEntity : row.subjectEntity}
            <tr class="border-t align-top">
              <td class="p-2"
                ><button
                  class="text-primary hover:underline"
                  onclick={() => {
                    selectedRelationship = row as unknown as InteractionListRow;
                    evidenceOpen = true;
                  }}
                  >{outgoing ? '→' : '←'}
                  {row.relation.displayLabel ||
                    getRelationPredicateLabel(row.relation.predicate, row.relation.sign)}<span
                    class="block text-xs"
                    >{row.relation.evidenceCount}
                    {row.relation.evidenceCount === 1 ? 'observation' : 'observations'}</span
                  ></button
                ></td
              >
              <td class="p-2"
                >{#if other?.entityPk}<button
                    class="text-primary hover:underline"
                    onclick={() => onSelect(other)}>{getEntityDisplayName(other)}</button
                  >{:else}Endpoint unavailable{/if}
                <div class="text-xs text-muted-foreground">{row.relation.sources.join(', ')}</div>
              </td>
              <td class="p-2"
                ><div class="max-w-md space-y-1 break-words">
                  {#each row.annotations.filter((a) => !isPublicationTerm(String(a.term || ''))) as annotation}<div
                    >
                      {property(annotation)}
                      <span class="text-xs text-muted-foreground"
                        >{String(annotation.source || '')}</span
                      >
                    </div>{/each}
                  {#if !row.annotations.some((a) => !isPublicationTerm(String(a.term || '')))}<span
                      class="text-muted-foreground">No recorded properties</span
                    >{/if}
                </div></td
              >
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  </section>
{/if}

<InteractionDetailsSheet
  open={evidenceOpen}
  onOpenChange={(open) => {
    evidenceOpen = open;
  }}
  interaction={selectedRelationship}
/>
