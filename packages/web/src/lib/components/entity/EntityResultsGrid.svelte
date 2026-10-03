<script lang="ts">
  import type { Snippet } from 'svelte';
  let { children }: { children: Snippet } = $props();
</script>

<div class="entity-card-grid">{@render children()}</div>

<style>
  .entity-card-grid {
    display: grid;
    gap: 1rem;
    grid-template-columns: repeat(
      auto-fit,
      minmax(min(100%, max(280px, calc((100% - 3rem) / 4))), 1fr)
    );
  }
</style>
