<script lang="ts">
  import { onDestroy } from 'svelte';
  import { page } from '$app/state';
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
  import { Button } from '$lib/components/ui/button/index.js';
  import * as Tabs from '$lib/components/ui/tabs/index.js';
  import type { PageData } from './$types';

  let { data }: { data: PageData } = $props();
  type Skill = PageData['skills'][number];

  // Each skill keeps its own open tab.
  let tabs = $state<Record<string, string>>({});
  let copied = $state<string | null>(null);
  let copyTimer: ReturnType<typeof setTimeout> | undefined;
  onDestroy(() => clearTimeout(copyTimer));

  function install(skill: Skill, folder: string) {
    return `mkdir -p ${folder}/${skill.name} && curl -fsSL ${page.url.origin}${skill.href} -o ${folder}/${skill.name}/SKILL.md`;
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

<!-- The skills side by side, fitting the viewport on large screens (each panel scrolls on its
     own); below each other on small ones, in one scrolling page. -->
<div
  class="flex h-full min-h-0 flex-col gap-3 overflow-y-auto pt-2 pb-6 lg:overflow-hidden lg:pb-0"
>
  <header class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
    <h1 class="text-xl font-semibold tracking-tight">Agent skills</h1>
    <p class="text-sm text-muted-foreground">
      Give your coding agent what it needs to use OmniPath data or to add a resource to it.
    </p>
  </header>

  <div class="grid gap-3 lg:min-h-0 lg:flex-1 lg:grid-cols-2 lg:grid-rows-[minmax(0,1fr)]">
    {#each data.skills as skill (skill.name)}
      {@render skillPanel(skill)}
    {/each}
  </div>
</div>

{#snippet skillPanel(skill: Skill)}
  <section
    aria-labelledby={`title-${skill.name}`}
    class="flex min-w-0 flex-col overflow-hidden rounded-xl border bg-background lg:min-h-0"
  >
    <header class="space-y-3 border-b px-5 py-4">
      <div class="flex items-start gap-3">
        <span
          class="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary"
          aria-hidden="true"
        >
          {#if skill.kind === 'Contribute'}<GitPullRequest class="size-4" />{:else}<Database
              class="size-4"
            />{/if}
        </span>
        <div class="min-w-0">
          <h2 id={`title-${skill.name}`} class="text-lg leading-tight font-semibold tracking-tight">
            {skill.title}
          </h2>
          <p class="mt-1 text-sm text-muted-foreground">{skill.description}</p>
        </div>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          onclick={() => copy(`skill:${skill.name}`, skill.content, 'Skill copied')}
          aria-label={`Copy ${skill.title} skill`}
        >
          {#if copied === `skill:${skill.name}`}<Check />Copied{:else}<Copy />Copy skill{/if}
        </Button>
        <Button
          size="sm"
          variant="outline"
          href={skill.href}
          download={`${skill.name}-SKILL.md`}
          aria-label={`Download ${skill.title} skill`}><Download />Download</Button
        >
        <span class="ml-auto font-mono text-xs text-muted-foreground">{skill.name}/SKILL.md</span>
      </div>
    </header>

    <Tabs.Root
      bind:value={() => tabs[skill.name] ?? 'overview', (value) => (tabs[skill.name] = value)}
      class="flex min-h-0 flex-1 flex-col gap-0"
    >
      <div class="shrink-0 px-5 pt-4">
        <Tabs.List aria-label={`${skill.title} skill`}>
          <Tabs.Trigger value="overview" class="flex-none px-3">Overview</Tabs.Trigger>
          <Tabs.Trigger value="document" class="flex-none px-3">SKILL.md</Tabs.Trigger>
          <Tabs.Trigger value="install" class="flex-none px-3">Install</Tabs.Trigger>
        </Tabs.List>
      </div>

      <Tabs.Content value="overview" class="min-h-0 space-y-6 overflow-y-auto p-5">
        <section class="space-y-3">
          <h3 class="section-title">What your agent learns</h3>
          <ul class="space-y-2.5">
            {#each skill.capabilities as capability}
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
            {#each skill.examples as example, index}
              {@const id = `example:${skill.name}:${index}`}
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
                  class="shrink-0 rounded p-1 text-muted-foreground opacity-60 transition group-hover:opacity-100 hover:bg-muted hover:text-foreground focus-visible:opacity-100"
                  aria-label="Copy prompt"
                  onclick={() => copy(id, example, 'Prompt copied')}
                >
                  {#if copied === id}<Check class="size-3.5" />{:else}<Copy class="size-3.5" />{/if}
                </button>
              </li>
            {/each}
          </ul>
        </section>
      </Tabs.Content>

      <Tabs.Content value="document" class="min-h-0 overflow-y-auto px-5 py-4">
        <article class="skill-doc">
          <!-- eslint-disable-next-line svelte/no-at-html-tags -- first-party SKILL.md files -->
          {@html skill.html}
        </article>
      </Tabs.Content>

      <Tabs.Content value="install" class="min-h-0 space-y-6 overflow-y-auto p-5">
        {@render installStep(
          skill,
          'Claude Code',
          'For every project of your user account. Claude Code picks the skill up the next time it starts.',
          'user',
          install(skill, '~/.claude/skills'),
        )}
        {@render installStep(
          skill,
          'Claude Code, one project',
          'Run in the project directory; commit the file to share the skill with collaborators.',
          'project',
          install(skill, '.claude/skills'),
        )}
        <section class="space-y-1.5">
          <h3 class="text-sm font-semibold">Claude apps</h3>
          <p class="text-sm text-muted-foreground">
            Download SKILL.md, put it in a folder named <code class="inline-code">{skill.name}</code
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
      </Tabs.Content>
    </Tabs.Root>
  </section>
{/snippet}

{#snippet installStep(
  skill: Skill,
  title: string,
  description: string,
  id: string,
  command: string,
)}
  {@const key = `install:${id}:${skill.name}`}
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
