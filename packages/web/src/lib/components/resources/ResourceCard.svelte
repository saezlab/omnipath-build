<script lang="ts">
  import { releaseUrl } from '$lib/api/release';
  import { Download, EllipsisVertical } from '@lucide/svelte';
  import { Button } from '$lib/components/ui/button/index.js';
  import { Badge } from '$lib/components/ui/badge/index.js';
  import * as Card from '$lib/components/ui/card/index.js';
  import { formatBytes } from '$lib/resources/catalog';
  import type { ResourceFile, ResourceRecord, LicenseUseStatus } from '$lib/resources/types';

  let {
    resource,
    selectedTags = [],
    onTagClick,
  }: {
    resource: ResourceRecord;
    selectedTags?: string[];
    onTagClick?: (id: string) => void;
  } = $props();
  let detailsOpen = $state(false);
  const countFormatter = new Intl.NumberFormat('en-US');
  const formatCount = (value: number | null | undefined) => countFormatter.format(value || 0);
  const files = $derived(
    (resource.files || []).filter((file) => file.name !== 'evidence_payloads.parquet'),
  );
  const totalBytes = $derived(
    resource.total_size_bytes || files.reduce((sum, file) => sum + (file.size_bytes || 0), 0),
  );
  const size = $derived(formatBytes(totalBytes).split(' '));
  const totalRecords = $derived(resource.entity_count + resource.interaction_count);
  const entityPercent = $derived(
    totalRecords > 0 ? (resource.entity_count / totalRecords) * 100 : 0,
  );
  const relationPercent = $derived(
    totalRecords > 0 ? (resource.interaction_count / totalRecords) * 100 : 0,
  );
  const cardTags = $derived(resource.tags ?? []);
  function safeUrl(value: string | null | undefined): string | undefined {
    if (!value) return undefined;
    try {
      const url = new URL(value);
      return ['http:', 'https:'].includes(url.protocol) ? url.href : undefined;
    } catch {
      return undefined;
    }
  }
  function fileHref(id: string, file: ResourceFile): string {
    return (
      safeUrl(file.url) ??
      releaseUrl(`/api/resources/${encodeURIComponent(id)}/files/${encodeURIComponent(file.name)}`)
    );
  }
  function fileLabel(file: ResourceFile): string {
    return file.name.replace(/\.parquet$/, '').replaceAll('_', ' ');
  }
  function licenseLabel(status?: LicenseUseStatus): string {
    return {
      allowed: 'Allowed',
      requires_permission: 'Requires permission',
      not_allowed: 'Not allowed',
      unknown: 'Unknown',
    }[status ?? 'unknown'];
  }
</script>

<article>
  <Card.Root class="resource-card">
    <div class="card-head">
      <div class="title-wrap">
        <h2>
          <a href={releaseUrl(`/explore?source=${encodeURIComponent(resource.resource_id)}`)}
            >{resource.resource_name}</a
          >
        </h2>
        <Card.Description class="description"
          >{resource.description ||
            (resource.interaction_count
              ? 'Molecular entities and relationships available to explore and download.'
              : 'A reference catalog of molecular entities and annotations.')}</Card.Description
        >
      </div>
      <div class="card-actions">
        <Button
          variant="outline"
          size="icon-sm"
          class="square-button"
          href={releaseUrl(`/api/resources/${encodeURIComponent(resource.resource_id)}/download`)}
          download={`${resource.resource_id}.zip`}
          aria-label={`Download ${resource.resource_name} ZIP`}
          title="Download resource ZIP"><Download size={18} /></Button
        >
        <Button
          variant="outline"
          class="square-button"
          aria-label={`Details and files for ${resource.resource_name}`}
          aria-expanded={detailsOpen}
          aria-controls={`details-${resource.resource_id}`}
          title="Resource details and files"
          onclick={() => (detailsOpen = !detailsOpen)}><EllipsisVertical size={18} /></Button
        >
      </div>
    </div>
    {#if resource.dataset_subset?.length}<p class="build-note">
        Selected datasets: {resource.dataset_subset.join(', ')}
      </p>{/if}
    <div class="body-row">
      <div
        class="donut"
        style:background={totalRecords
          ? `conic-gradient(var(--resource-cyan) 0 ${entityPercent}%, var(--resource-green) ${entityPercent}% 100%)`
          : 'var(--resource-line)'}
        role="img"
        aria-label={`${formatBytes(totalBytes)} download. ${entityPercent.toFixed(1)}% entity records, ${relationPercent.toFixed(1)}% relation records.`}
      >
        <div class="donut-center"><strong>{size[0]}</strong><span>{size[1]}</span></div>
      </div>
      <div class="stats">
        <div class="stat-row">
          <span class="stat-label"><span class="dot entities"></span>Entities</span><strong
            >{formatCount(resource.entity_count)}</strong
          ><span class="percent">{entityPercent.toFixed(1)}%</span>
        </div>
        <div class="stat-row">
          <span class="stat-label"><span class="dot relations"></span>Relations</span><strong
            >{formatCount(resource.interaction_count)}</strong
          ><span class="percent">{relationPercent.toFixed(1)}%</span>
        </div>
      </div>
    </div>
    <div class="card-footer">
      {#if resource.version}<Badge
          variant="outline"
          class="version"
          title="Resource version in the selected release">v{resource.version}</Badge
        >{/if}
      {#each cardTags as tag (tag.id)}<Button
          variant="outline"
          class={`chip ${selectedTags.includes(tag.id) ? 'selected' : ''}`}
          aria-pressed={selectedTags.includes(tag.id)}
          title={tag.description}
          onclick={() => onTagClick?.(tag.id)}>{tag.label}</Button
        >{/each}
    </div>
    <div
      hidden={!detailsOpen}
      id={`details-${resource.resource_id}`}
      class="border-t border-border/50 pt-3 text-xs"
    >
      <div class="file-links">
        {#each files as file (file.name)}
          <a
            href={fileHref(resource.resource_id, file)}
            download={`${resource.resource_id}-${file.name}`}
            ><Download size={12} />{fileLabel(file)} <span>{formatBytes(file.size_bytes)}</span></a
          >
        {/each}
      </div>
      <div class="all-tags">
        {#each resource.tags ?? [] as tag (tag.id)}
          <Button
            variant="outline"
            class={`chip ${selectedTags.includes(tag.id) ? 'selected' : ''}`}
            title={tag.description}
            aria-pressed={selectedTags.includes(tag.id)}
            onclick={() => onTagClick?.(tag.id)}>{tag.label}</Button
          >
        {/each}
      </div>
      <dl class="mt-3 space-y-3 text-muted-foreground">
        <div>
          <dt class="font-medium text-foreground">pypath input module</dt>
          <dd class="mt-1">
            {#if safeUrl(resource.input_module_url)}<a
                class="text-primary hover:underline"
                href={safeUrl(resource.input_module_url)}
                target="_blank"
                rel="noopener noreferrer">View on GitHub ↗</a
              >{:else}Not recorded{/if}
          </dd>
        </div>
        <div>
          <dt class="font-medium text-foreground">Website</dt>
          <dd class="mt-1 break-words">
            {#if safeUrl(resource.website)}<a
                class="text-primary hover:underline"
                href={safeUrl(resource.website)}
                target="_blank"
                rel="noopener noreferrer">{resource.website} ↗</a
              >{:else}Not specified{/if}
          </dd>
        </div>
        <div>
          <dt class="font-medium text-foreground">License use</dt>
          <dd class="mt-1">
            Academic: {licenseLabel(resource.license_use?.academic)} · Commercial: {licenseLabel(
              resource.license_use?.commercial,
            )}
          </dd>
        </div>
        <div>
          <dt class="font-medium text-foreground">License</dt>
          <dd class="mt-1">
            {#if safeUrl(resource.license_url)}<a
                class="text-primary hover:underline"
                href={safeUrl(resource.license_url)}
                target="_blank"
                rel="noopener noreferrer">{resource.license || 'License terms'} ↗</a
              >{:else}{resource.license || 'Not specified'}{/if}
          </dd>
        </div>
      </dl>
    </div>
  </Card.Root>
</article>

<style>
  article {
    min-width: 0;
  }
  article :global(.resource-card) {
    --tw-ring-shadow: 0 0 #0000;
    min-width: 0;
    padding: 20px;
    border-radius: 16px;
    border: 1px solid var(--resource-line);
    background: var(--resource-panel);
    box-shadow: 0 12px 32px rgb(0 0 0 / 0.04);
    display: flex;
    flex-direction: column;
    gap: 17px;
    transition:
      border-color 0.18s,
      box-shadow 0.18s;
  }
  :global(.dark) article :global(.resource-card) {
    background: linear-gradient(180deg, #0d1e28, #08161e);
    box-shadow: 0 18px 44px rgb(0 0 0 / 0.15);
  }
  article :global(.resource-card):hover {
    border-color: color-mix(in srgb, var(--resource-cyan) 45%, var(--resource-line));
  }
  .card-head {
    display: flex;
    align-items: start;
    gap: 10px;
  }
  .title-wrap {
    flex: 1;
    min-width: 0;
  }
  h2 {
    font-size: 20px;
    font-weight: 720;
    letter-spacing: -0.03em;
    color: var(--resource-cyan);
    line-height: 1.15;
    overflow-wrap: anywhere;
  }
  h2 a:hover {
    text-decoration: underline;
  }
  article :global(.description) {
    color: var(--muted-foreground);
    font-size: 12px;
    line-height: 1.5;
    margin-top: 8px;
    min-height: 54px;
  }
  .card-actions {
    display: flex;
    gap: 6px;
  }
  article :global(.square-button) {
    display: grid;
    place-items: center;
    height: 32px;
    width: 32px;
    border-radius: 8px;
    border: 1px solid var(--resource-line);
    flex-shrink: 0;
  }
  article :global(.square-button):hover {
    color: var(--resource-cyan);
    border-color: var(--resource-cyan);
  }
  .body-row {
    display: flex;
    align-items: center;
    gap: 14px;
  }
  .donut {
    width: 90px;
    height: 90px;
    flex-shrink: 0;
    border-radius: 50%;
    display: grid;
    place-items: center;
  }
  .donut-center {
    width: 64px;
    height: 64px;
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    background: var(--resource-panel);
    border-radius: 50%;
  }
  .donut-center strong {
    font-size: 19px;
    letter-spacing: -0.04em;
  }
  .donut-center span {
    font-size: 11px;
    color: var(--muted-foreground);
  }
  .stats {
    flex: 1;
    min-width: 0;
  }
  .stat-row {
    display: grid;
    grid-template-columns: 1fr auto;
    align-items: center;
    gap: 5px 8px;
    padding: 9px 0;
  }
  .stat-row + .stat-row {
    border-top: 1px solid var(--resource-line);
  }
  .stat-label {
    grid-column: 1;
    grid-row: 1 / span 2;
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 15px;
    font-weight: 500;
    color: var(--muted-foreground);
  }
  .dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    flex-shrink: 0;
  }
  .entities {
    background: var(--resource-cyan);
  }
  .relations {
    background: var(--resource-green);
  }
  .stat-row strong {
    grid-column: 2;
    grid-row: 1;
    font-size: 14px;
    font-variant-numeric: tabular-nums;
    text-align: right;
    font-weight: 650;
  }
  .percent {
    grid-column: 2;
    grid-row: 2;
    font-size: 11px;
    text-align: right;
    color: var(--muted-foreground);
    font-variant-numeric: tabular-nums;
  }
  .card-footer,
  .all-tags {
    display: flex;
    flex-wrap: nowrap;
    gap: 6px;
    max-width: 100%;
    overflow-x: auto;
    padding: 3px 0 6px;
    scrollbar-width: thin;
    scrollbar-color: var(--resource-line) transparent;
  }
  .card-footer > :global(*),
  .all-tags > :global(*) {
    flex-shrink: 0;
    white-space: nowrap;
  }
  .card-footer {
    margin-top: auto;
  }
  article :global(.chip),
  article :global(.version) {
    height: auto;
    min-height: 26px;
    border: 1px solid var(--resource-line);
    border-radius: 999px;
    background: color-mix(in srgb, var(--resource-panel) 90%, var(--resource-cyan));
    font-size: 10px;
    padding: 5px 8px;
    color: var(--muted-foreground);
  }
  article :global(.version) {
    font-variant-numeric: tabular-nums;
    color: var(--foreground);
  }
  article :global(.chip):hover,
  article :global(.chip.selected) {
    color: var(--resource-cyan);
    border-color: var(--resource-cyan);
  }
  .build-note {
    font-size: 11px;
    color: var(--muted-foreground);
  }
  .file-links {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin: 14px 0;
  }
  .file-links a {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    border: 1px solid var(--resource-line);
    padding: 6px 8px;
    border-radius: 6px;
    color: var(--resource-cyan);
  }
  .file-links span {
    color: var(--muted-foreground);
  }
  article :global(button) {
    cursor: pointer;
  }
  @media (min-width: 1400px) {
    .donut {
      width: 108px;
      height: 108px;
    }
    .donut-center {
      width: 78px;
      height: 78px;
    }
    .donut-center strong {
      font-size: 23px;
    }
  }
  @media (max-width: 380px) {
    .body-row {
      flex-wrap: wrap;
    }
    .stats {
      min-width: 150px;
    }
    article :global(.resource-card) {
      padding: 16px;
    }
  }
</style>
