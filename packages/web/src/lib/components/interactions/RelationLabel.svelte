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

<span class="inline-flex flex-col items-center text-center leading-tight">
  <!-- The arrow is inline so it stays with the first word when the phrase wraps. -->
  <span class="text-sm text-balance text-foreground/80">
    {#if phrase.direction === 'up'}<ArrowUp
        class="mr-0.5 inline-block size-3.5 align-[-2px] text-green-600 dark:text-green-400"
        aria-label="increased"
      />{:else if phrase.direction === 'down'}<ArrowDown
        class="mr-0.5 inline-block size-3.5 align-[-2px] text-red-600 dark:text-red-400"
        aria-label="decreased"
      />{/if}{phrase.text}
  </span>
  {#if phrase.detail}<span class="text-xs text-muted-foreground">{phrase.detail}</span>{/if}
</span>
