<script lang="ts">
  import { onDestroy } from 'svelte';
  import { Check, CircleCheck, Copy, Download, FileText, ChevronRight } from '@lucide/svelte';
  import { toast } from 'svelte-sonner';
  import { Button } from '$lib/components/ui/button/index.js';
  import * as Card from '$lib/components/ui/card/index.js';
  import * as Dialog from '$lib/components/ui/dialog/index.js';
  import type { PageData } from './$types';

  let { data }: { data: PageData } = $props();
  let copiedSkill = $state<string | null>(null);
  let copyTimer: ReturnType<typeof setTimeout> | undefined;
  onDestroy(() => clearTimeout(copyTimer));

  async function copySkill(name: string, content: string) {
    try {
      await navigator.clipboard.writeText(content);
      copiedSkill = name;
      toast.success('Skill copied to clipboard');
      clearTimeout(copyTimer);
      copyTimer = setTimeout(() => (copiedSkill = null), 1800);
    } catch {
      toast.error('Could not copy skill. You can download SKILL.md instead.');
    }
  }
</script>

<svelte:head>
  <title>Skills · OmniPath</title>
  <meta
    name="description"
    content="Teach your agent to use OmniPath data or contribute a new resource to pypath. Copy, preview, or download a skill."
  />
</svelte:head>

<div class="py-7 md:pt-12">
  <header class="mb-8">
    <h1 class="text-3xl font-bold tracking-tight md:text-4xl">
      Teach your agent to work with OmniPath
    </h1>
    <p class="mt-3 text-base text-muted-foreground md:text-lg">
      Choose a skill to explore the data or contribute to the project.
    </p>
  </header>

  <div class="grid grid-cols-1 gap-6 lg:grid-cols-2">
    {#each data.skills as skill (skill.name)}
      <article class="min-w-0" aria-labelledby={`title-${skill.name}`}>
        <Card.Root class="h-full">
          <Card.Header>
            <h2 id={`title-${skill.name}`} class="text-2xl font-semibold tracking-tight">
              {skill.title}
            </h2>
            <Card.Description class="mt-2 text-base leading-relaxed"
              >{skill.description}</Card.Description
            >
          </Card.Header>
          <Card.Content class="mt-auto">
            <ul class="space-y-4 border-y border-border py-6">
              {#each skill.capabilities as capability}<li
                  class="flex items-center gap-3 text-sm text-muted-foreground"
                >
                  <CircleCheck class="size-4 shrink-0 text-primary" aria-hidden="true" /><span
                    >{capability}</span
                  >
                </li>{/each}
            </ul>
          </Card.Content>
          <Card.Footer class="flex flex-wrap gap-2">
            <Button
              size="lg"
              onclick={() => copySkill(skill.name, skill.content)}
              aria-label={`Copy ${skill.title} skill`}
            >
              {#if copiedSkill === skill.name}<Check size={17} />Copied{:else}<Copy size={17} />Copy
                skill{/if}
            </Button>
            <Button
              variant="outline"
              size="lg"
              href={skill.href}
              download={`${skill.name}-SKILL.md`}
              aria-label={`Download ${skill.title} skill`}><Download size={17} />Download</Button
            >
            <Dialog.Root>
              <Dialog.Trigger>
                {#snippet child({ props })}<Button
                    {...props}
                    variant="ghost"
                    class="sm:ml-auto"
                    aria-label={`Preview ${skill.title} skill`}
                    ><ChevronRight size={15} />Preview skill</Button
                  >{/snippet}
              </Dialog.Trigger>
              <Dialog.Content class="flex max-h-[85svh] flex-col sm:max-w-3xl">
                <Dialog.Header
                  ><Dialog.Title>{skill.title}</Dialog.Title><Dialog.Description
                    >{skill.name}/SKILL.md</Dialog.Description
                  ></Dialog.Header
                >
                <pre
                  class="min-h-0 overflow-auto rounded-lg border border-border bg-muted/30 p-4 text-xs leading-relaxed whitespace-pre-wrap break-words"><code
                    >{skill.content}</code
                  ></pre>
                <Dialog.Footer>
                  <Button variant="outline" href={skill.href} download={`${skill.name}-SKILL.md`}
                    ><Download size={16} />Download</Button
                  >
                  <Button onclick={() => copySkill(skill.name, skill.content)}
                    ><Copy size={16} />{copiedSkill === skill.name
                      ? 'Copied'
                      : 'Copy skill'}</Button
                  >
                </Dialog.Footer>
              </Dialog.Content>
            </Dialog.Root>
          </Card.Footer>
        </Card.Root>
      </article>
    {/each}
  </div>

  <Card.Root class="mt-6">
    <Card.Content class="flex items-center gap-4">
      <div
        class="hidden size-12 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground sm:flex"
        aria-hidden="true"
      >
        <FileText size={24} />
      </div>
      <div>
        <h2 class="text-base font-semibold">Give your agent the right context</h2>
        <Card.Description class="mt-1 leading-relaxed"
          >Copy the skill into your agent, or download SKILL.md to add it to your workspace.</Card.Description
        >
      </div>
    </Card.Content>
  </Card.Root>
</div>
