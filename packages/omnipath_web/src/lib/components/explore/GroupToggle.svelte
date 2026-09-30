<script lang="ts">
  import * as Tooltip from '$lib/components/ui/tooltip/index.js';
  let {
    pressed = $bindable(false),
    explanation = 'Group chemicals with the same connectivity (first 14 InChIKey letters). Groups merge matching entities’ names and annotations and are sorted by matched entity count. Other entities stay separate.',
  }: { pressed?: boolean; explanation?: string } = $props();
</script>

<Tooltip.Provider delayDuration={200}>
  <Tooltip.Root>
    <Tooltip.Trigger
      type="button"
      aria-pressed={pressed}
      class="inline-flex h-9 shrink-0 items-center gap-2 rounded-md px-2 text-xs text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      onclick={() => (pressed = !pressed)}
    >
      <span
        aria-hidden="true"
        class={`inline-flex h-4 w-7 items-center rounded-full p-0.5 transition-colors ${pressed ? 'bg-primary' : 'bg-muted-foreground/30'}`}
      >
        <span
          class={`size-3 rounded-full bg-background shadow-sm transition-transform ${pressed ? 'translate-x-3' : ''}`}
        ></span>
      </span>
      Group</Tooltip.Trigger
    >
    <Tooltip.Content class="max-w-xs" side="bottom" sideOffset={6}>{explanation}</Tooltip.Content>
  </Tooltip.Root>
</Tooltip.Provider>
