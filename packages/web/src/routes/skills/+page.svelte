<script lang="ts">
  import { onDestroy } from 'svelte';
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import {
    Check,
    CircleCheck,
    Copy,
    Download,
    GitPullRequest,
    MessageSquare,
    Database,
  } from '@lucide/svelte';
  import { toast } from 'svelte-sonner';
  import { Badge } from '$lib/components/ui/badge/index.js';
  import { Button } from '$lib/components/ui/button/index.js';
  import * as Tabs from '$lib/components/ui/tabs/index.js';
  import type { PageData } from './$types';

  let { data }: { data: PageData } = $props();

  // The selected skill is part of the URL, so a skill can be linked to.
  const selected = $derived(
    data.skills.find((skill) => skill.name === page.url.searchParams.get('skill')) ??
      data.skills[0],
  );
  let tab = $state('overview');
  let copied = $state<string | null>(null);
  let copyTimer: ReturnType<typeof setTimeout> | undefined;
  onDestroy(() => clearTimeout(copyTimer));

  const fileUrl = $derived(`${page.url.origin}${selected.href}`);
  const userInstall = $derived(
    `mkdir -p ~/.claude/skills/${selected.name} && curl -fsSL ${fileUrl} -o ~/.claude/skills/${selected.name}/SKILL.md`,
  );
  const projectInstall = $derived(
    `mkdir -p .claude/skills/${selected.name} && curl -fsSL ${fileUrl} -o .claude/skills/${selected.name}/SKILL.md`,
  );

  function select(name: string) {
    const url = new URL(page.url);
    url.searchParams.set('skill', name);
    tab = 'overview';
    goto(url, { replaceState: true, keepFocus: true, noScroll: true });
  }

  async function copy(id: string, text: string, message: string) {
    try {
      await navigator.clipboard.writeText(text);
      copied = id;
      toast.success(message);
      clearTimeout(copyTimer);
      copyTimer = setTimeout(() => (copied = null), 1800);
    } catch {
      toast.error('Could not copy. Select the text and copy it instead.');
    }
  }
</script>

<svelte:head>
  <title>Skills · OmniPath</title>
  <meta
    name="description"
    content="Teach your agent to use OmniPath data or contribute a new resource to pypath. Copy, preview, or install a skill."
  />
</svelte:head>

<!-- Fits the viewport on large screens (the skill document scrolls on its own); one scrolling
     page on small ones. -->
<div
  class="flex h-full min-h-0 flex-col gap-3 overflow-y-auto pt-2 pb-6 lg:overflow-hidden lg:pb-0"
>
  <header class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
    <h1 class="text-xl font-semibold tracking-tight">Agent skills</h1>
    <p class="text-sm text-muted-foreground">
      Give your coding agent what it needs to use OmniPath data or to add a resource to it.
    </p>
  </header>

  <div
    class="grid gap-3 lg:min-h-0 lg:flex-1 lg:grid-cols-[300px_minmax(0,1fr)] lg:grid-rows-[minmax(0,1fr)]"
  >
    <nav aria-label="Skills" class="flex flex-col gap-2">
      {#each data.skills as skill (skill.name)}
        {@const active = skill.name === selected.name}
        <button
          type="button"
          aria-current={active ? 'true' : undefined}
          onclick={() => select(skill.name)}
          class={`flex gap-3 rounded-xl border p-3 text-left transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none ${
            active ? 'border-primary/60 bg-primary/5' : 'bg-background hover:bg-muted/40'
          }`}
        >
          <span
            class={`flex size-9 shrink-0 items-center justify-center rounded-lg ${
              active ? 'bg-primary/15 text-primary' : 'bg-muted text-muted-foreground'
            }`}
            aria-hidden="true"
          >
            {#if skill.kind === 'Contribute'}<GitPullRequest class="size-4" />{:else}<Database
                class="size-4"
              />{/if}
          </span>
          <span class="min-w-0 space-y-1">
            <span class="flex items-center gap-2">
              <span class="font-medium">{skill.title}</span>
            </span>
            <span class="block text-xs leading-relaxed text-muted-foreground"
              >{skill.description}</span
            >
            <Badge variant="outline" class="text-[11px] font-normal">{skill.kind}</Badge>
          </span>
        </button>
      {/each}
    </nav>

    <section
      aria-labelledby="skill-title"
      class="flex min-w-0 flex-col overflow-hidden rounded-xl border bg-background lg:min-h-0"
    >
      <header class="flex flex-wrap items-start justify-between gap-3 border-b px-5 py-4">
        <div class="min-w-0">
          <h2 id="skill-title" class="text-lg font-semibold tracking-tight">{selected.title}</h2>
          <p class="font-mono text-xs text-muted-foreground">{selected.name}/SKILL.md</p>
        </div>
        <div class="flex flex-wrap gap-2">
          <Button
            onclick={() => copy(`skill:${selected.name}`, selected.content, 'Skill copied')}
            aria-label={`Copy ${selected.title} skill`}
          >
            {#if copied === `skill:${selected.name}`}<Check />Copied{:else}<Copy />Copy skill{/if}
          </Button>
          <Button
            variant="outline"
            href={selected.href}
            download={`${selected.name}-SKILL.md`}
            aria-label={`Download ${selected.title} skill`}><Download />Download</Button
          >
        </div>
      </header>

      <Tabs.Root bind:value={tab} class="flex min-h-0 flex-1 flex-col gap-0">
        <div class="shrink-0 border-b px-5">
          <Tabs.List variant="line" class="h-10 justify-start gap-4 p-0" aria-label="Skill">
            <Tabs.Trigger value="overview" class="flex-none">Overview</Tabs.Trigger>
            <Tabs.Trigger value="document" class="flex-none">SKILL.md</Tabs.Trigger>
            <Tabs.Trigger value="install" class="flex-none">Install</Tabs.Trigger>
          </Tabs.List>
        </div>

        <Tabs.Content value="overview" class="min-h-0 overflow-y-auto p-5">
          <div class="grid max-w-4xl gap-8 md:grid-cols-2">
            <section class="space-y-3">
              <h3 class="section-title">What your agent learns</h3>
              <ul class="space-y-3">
                {#each selected.capabilities as capability}
                  <li class="flex items-start gap-2.5 text-sm">
                    <CircleCheck class="mt-0.5 size-4 shrink-0 text-primary" aria-hidden="true" />
                    <span>{capability}</span>
                  </li>
                {/each}
              </ul>
            </section>
            <section class="space-y-3">
              <h3 class="section-title">Try asking</h3>
              <ul class="space-y-2">
                {#each selected.examples as example, index}
                  {@const id = `example:${selected.name}:${index}`}
                  <li
                    class="group flex items-start gap-2.5 rounded-lg border bg-muted/20 px-3 py-2.5 text-sm"
                  >
                    <MessageSquare
                      class="mt-0.5 size-4 shrink-0 text-muted-foreground"
                      aria-hidden="true"
                    />
                    <span class="min-w-0 flex-1">{example}</span>
                    <button
                      type="button"
                      class="shrink-0 rounded p-1 text-muted-foreground opacity-60 transition hover:bg-muted hover:text-foreground focus-visible:opacity-100 group-hover:opacity-100"
                      aria-label="Copy prompt"
                      onclick={() => copy(id, example, 'Prompt copied')}
                    >
                      {#if copied === id}<Check class="size-3.5" />{:else}<Copy
                          class="size-3.5"
                        />{/if}
                    </button>
                  </li>
                {/each}
              </ul>
            </section>
          </div>
        </Tabs.Content>

        <Tabs.Content value="document" class="min-h-0 overflow-y-auto px-5 py-4">
          <article class="skill-doc max-w-3xl">
            <!-- eslint-disable-next-line svelte/no-at-html-tags -- first-party SKILL.md files -->
            {@html selected.html}
          </article>
        </Tabs.Content>

        <Tabs.Content value="install" class="min-h-0 overflow-y-auto p-5">
          <div class="max-w-3xl space-y-6">
            {@render installStep(
              'Claude Code',
              'For every project of your user account. Claude Code picks the skill up the next time it starts.',
              'user',
              userInstall,
            )}
            {@render installStep(
              'Claude Code, one project',
              'Run in the project directory; commit the file to share the skill with collaborators.',
              'project',
              projectInstall,
            )}
            <section class="space-y-1.5">
              <h3 class="text-sm font-semibold">Claude apps</h3>
              <p class="text-sm text-muted-foreground">
                Download SKILL.md, put it in a folder named <code class="inline-code"
                  >{selected.name}</code
                >, compress the folder as a ZIP file and upload it as a custom skill in Claude's
                settings.
              </p>
            </section>
            <section class="space-y-1.5">
              <h3 class="text-sm font-semibold">Other agents</h3>
              <p class="text-sm text-muted-foreground">
                Save SKILL.md in your workspace and point to it from the agent's instructions (for
                example <code class="inline-code">AGENTS.md</code>), or copy the skill into the
                conversation.
              </p>
            </section>
          </div>
        </Tabs.Content>
      </Tabs.Root>
    </section>
  </div>
</div>

{#snippet installStep(title: string, description: string, id: string, command: string)}
  {@const key = `install:${id}:${selected.name}`}
  <section class="space-y-1.5">
    <h3 class="text-sm font-semibold">{title}</h3>
    <p class="text-sm text-muted-foreground">{description}</p>
    <div class="flex items-start gap-2 rounded-lg border bg-muted/30 p-3">
      <code class="min-w-0 flex-1 font-mono text-xs leading-relaxed break-all">{command}</code>
      <button
        type="button"
        class="shrink-0 rounded p-1 text-muted-foreground transition hover:bg-muted hover:text-foreground"
        aria-label={`Copy ${title} install command`}
        onclick={() => copy(key, command, 'Command copied')}
      >
        {#if copied === key}<Check class="size-3.5" />{:else}<Copy class="size-3.5" />{/if}
      </button>
    </div>
  </section>
{/snippet}

<style>
  .section-title {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--muted-foreground);
  }
  .inline-code,
  .skill-doc :global(:not(pre) > code) {
    border-radius: 4px;
    background: color-mix(in oklab, var(--muted) 70%, transparent);
    padding: 0.1em 0.35em;
    font-family: var(--font-mono, ui-monospace, monospace);
    font-size: 0.85em;
  }
  .skill-doc {
    font-size: 0.875rem;
    line-height: 1.65;
  }
  .skill-doc :global(h1) {
    margin: 0 0 0.75rem;
    font-size: 1.35rem;
    font-weight: 600;
    letter-spacing: -0.01em;
  }
  .skill-doc :global(h2) {
    margin: 1.75rem 0 0.5rem;
    border-bottom: 1px solid var(--border);
    padding-bottom: 0.35rem;
    font-size: 1.05rem;
    font-weight: 600;
  }
  .skill-doc :global(h3) {
    margin: 1.25rem 0 0.4rem;
    font-size: 0.95rem;
    font-weight: 600;
  }
  .skill-doc :global(p),
  .skill-doc :global(ul),
  .skill-doc :global(ol),
  .skill-doc :global(pre),
  .skill-doc :global(table) {
    margin: 0 0 0.85rem;
  }
  .skill-doc :global(ul) {
    list-style: disc;
    padding-left: 1.25rem;
  }
  .skill-doc :global(ol) {
    list-style: decimal;
    padding-left: 1.25rem;
  }
  .skill-doc :global(li + li) {
    margin-top: 0.25rem;
  }
  .skill-doc :global(a) {
    color: var(--primary);
    text-decoration: underline;
    text-underline-offset: 3px;
  }
  .skill-doc :global(pre) {
    overflow-x: auto;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: color-mix(in oklab, var(--muted) 35%, transparent);
    padding: 0.75rem 0.9rem;
    font-size: 0.8rem;
    line-height: 1.55;
  }
  .skill-doc :global(pre code) {
    font-family: var(--font-mono, ui-monospace, monospace);
  }
  .skill-doc :global(table) {
    display: block;
    overflow-x: auto;
    border-collapse: collapse;
    font-size: 0.8rem;
  }
  .skill-doc :global(th),
  .skill-doc :global(td) {
    border: 1px solid var(--border);
    padding: 0.35rem 0.6rem;
    text-align: left;
    vertical-align: top;
  }
  .skill-doc :global(th) {
    background: color-mix(in oklab, var(--muted) 45%, transparent);
    font-weight: 600;
  }
  .skill-doc :global(hr) {
    margin: 1.5rem 0;
    border-color: var(--border);
  }
</style>
