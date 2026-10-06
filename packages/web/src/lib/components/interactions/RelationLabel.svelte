<script lang="ts">
  import { ArrowDown, ArrowUp } from '@lucide/svelte';
  import { getRelationPredicateLabel } from '$lib/domain/display';
  import { relationPhrase, type PhraseRelation } from '$lib/domain/relation-phrase';

  let { relation }: { relation: PhraseRelation } = $props();

  const phrase = $derived(
    relationPhrase(
      relation,
      getRelationPredicateLabel(relation.predicate, relation.sign ?? 0) || '—',
    ),
  );
</script>

<span class="inline-flex flex-col items-center leading-tight">
  <span class="inline-flex items-center gap-1 text-sm text-foreground/80">
    {#if phrase.direction === 'up'}<ArrowUp
        class="size-3.5 shrink-0 text-green-600 dark:text-green-400"
        aria-label="increased"
      />{:else if phrase.direction === 'down'}<ArrowDown
        class="size-3.5 shrink-0 text-red-600 dark:text-red-400"
        aria-label="decreased"
      />{/if}
    <span>{phrase.text}</span>
  </span>
  {#if phrase.detail}<span class="text-xs text-muted-foreground">{phrase.detail}</span>{/if}
</span>
