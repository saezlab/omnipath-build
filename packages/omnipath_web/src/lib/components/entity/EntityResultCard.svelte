<script lang="ts">
  import { AlertTriangle, Box, Check, Link, Network, Plus } from '@lucide/svelte';
  import IdentifierBadge from './IdentifierBadge.svelte';
  import EntityDetailsDialog from './EntityDetailsDialog.svelte';
  import { getSelectionStore } from '$lib/stores/selection.svelte';
  import {
    getEntityDisplayName,
    getEntityPublicId,
    getEntitySecondaryName,
    getEntityTypeLabel,
    getIdentifierTypeLabel,
    getIdentifierDisplayTypeForValue,
    isChemicalEntity,
    isCvTermEntity,
    isUnresolvedEntity,
  } from '$lib/domain/display';
  import { getEntityTypeEmoji, getEntityTypeStyle } from '$lib/utils/entity-types';
  import { releaseFetch } from '$lib/api/release';
  import { formatNumber } from '$lib/utils/format';
  import { formatTaxonomy } from '$lib/utils/taxonomy';
  import type { EntityWithIdentifiers as EntityResult } from '$lib/types/entities';
  let { result, onopen }: { result: EntityResult; onopen?: (entity: EntityResult) => void } =
    $props();
  const selection = getSelectionStore();
  let detailsOpen = $state(false);
  let selectingGroup = $state(false);
  let selectionError = $state('');
  async function toggleGroup() {
    if (selectingGroup) return;
    selectingGroup = true;
    selectionError = '';
    try {
      const keys = [...(result.groupMemberKeys || [])];
      let cursor = result.groupMemberCursor;
      while (cursor) {
        const response = await releaseFetch('/app-api/entities/groups', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            group_key: result.entityPk,
            query: result.groupQuery,
            filters: result.groupFilters,
            resources: result.groupResources,
            member_cursor: cursor,
            member_limit: 100,
          }),
        });
        if (!response.ok) throw new Error('Unable to select the group. Please retry.');
        const group = (await response.json()).groups[0];
        if (!group) throw new Error('This group is no longer available.');
        keys.push(...group.entity.groupMemberKeys);
        cursor = group.nextMemberCursor;
      }
      selection.toggleEntityGroup(keys, result.entityPk);
    } catch (error) {
      selectionError = (error as Error).message;
    } finally {
      selectingGroup = false;
    }
  }
  function openDetails(entity: EntityResult) {
    if (onopen) onopen(entity);
    else detailsOpen = true;
  }
  function handleDetailsKeydown(event: KeyboardEvent, entity: EntityResult) {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    openDetails(entity);
  }
  function getEntityOntologyTerm(entity: EntityResult) {
    if (entity.ontologyHierarchy) return entity.ontologyHierarchy;
    if (!isCvTermEntity(entity)) return null;
    const id = entity.canonicalIdentifier || '';
    if (!id) return null;
    const prefix = id.includes(':') ? id.split(':')[0] : id.includes('-') ? id.split('-')[0] : null;
    return {
      termId: id,
      ontologyPrefix: prefix,
      label: getEntityDisplayName(entity),
      definition: null,
      ontologyId: null,
      childCount: 0,
      parentCount: 0,
    };
  }

  function isPlainNameIdentifierType(identifierType: string | undefined): boolean {
    const label = getIdentifierTypeLabel(identifierType).toLowerCase();
    const normalized = [identifierType || '', label].join(' ').toLowerCase().replace(/[_-]/g, ' ');
    return (
      label === 'name' ||
      normalized.includes('gene name') ||
      normalized.includes('gene symbol') ||
      normalized.includes('recommended name') ||
      normalized.includes('entry name')
    );
  }

  function getIdentifierBadgeType(
    entity: EntityResult,
    value: string | undefined,
  ): string | undefined {
    const text = value?.trim() || '';
    if (!text) return undefined;
    const identifierType = getIdentifierDisplayTypeForValue(entity, text);
    if (!identifierType || isPlainNameIdentifierType(identifierType)) return undefined;
    return identifierType;
  }

  function firstHint(values: string[] | undefined): string | undefined {
    return values?.map((value) => value.trim()).find(Boolean);
  }

  function getSpecificEntityHint(entity: EntityResult): { label: string; value: string } | null {
    if (isChemicalEntity(entity)) {
      const specificity = firstHint(entity.entityFacetHints?.structuralSpecificities);
      if (specificity) return { label: 'Specificity', value: specificity };

      const chemicalClass = firstHint(entity.entityFacetHints?.chemicalClasses);
      if (chemicalClass) return { label: 'Subclass', value: chemicalClass };

      const metabolicDomain = firstHint(entity.entityFacetHints?.metabolicDomains);
      if (metabolicDomain) return { label: 'Domain', value: metabolicDomain };
    }

    const taxonomy = formatTaxonomy(entity.taxonomyId, entity.taxonomyName);
    return taxonomy ? { label: 'Taxon', value: taxonomy } : null;
  }
</script>

{#snippet card()}
  {@const publicId = getEntityPublicId(result)}
  {@const displayName = getEntityDisplayName(result)}
  {@const secondaryName = getEntitySecondaryName(result)}
  {@const displayIdentifierType = getIdentifierBadgeType(result, displayName)}
  {@const secondaryIdentifierType = getIdentifierBadgeType(result, secondaryName)}
  {@const entityTypeLabel = getEntityTypeLabel(result)}
  {@const selected = result.groupMemberKeys
    ? selection.selectedEntities.filter((entity) => entity.groupKey === publicId).length ===
        result.groupMemberCount ||
      (!result.groupMemberCursor &&
        result.groupMemberKeys.every((key) => selection.isSelected(key)))
    : selection.isSelected(publicId)}
  {@const entityTypeIcon = getEntityTypeEmoji(entityTypeLabel)}
  {@const entityTypeStyle = getEntityTypeStyle(entityTypeLabel)}
  {@const unresolved = isUnresolvedEntity(result)}
  {@const ontologyHierarchy = getEntityOntologyTerm(result)}
  {@const showOntologyHint = !!ontologyHierarchy}
  {@const entitySpecificHint = getSpecificEntityHint(result)}
  <div
    class={`w-full min-w-0 cursor-pointer overflow-hidden rounded-xl ${
      unresolved
        ? 'bg-amber-50/35 dark:bg-amber-950/10'
        : `bg-gradient-to-br ${entityTypeStyle.bgColor}`
    }`}
    role="button"
    tabindex="0"
    onclick={() => openDetails(result)}
    onkeydown={(event) => handleDetailsKeydown(event, result)}
  >
    <div class="flex items-start gap-3 px-4 py-3">
      <div class="min-w-0 flex-1 text-left">
        <div class="flex min-w-0 items-baseline gap-2">
          {#if displayIdentifierType}
            <IdentifierBadge
              identifierType={displayIdentifierType}
              value={displayName}
              variant="subtle"
              class="max-w-[60%]"
            />
          {:else}
            <h3 class="truncate text-base font-medium text-foreground" title={displayName}>
              {displayName}
            </h3>
          {/if}
          {#if secondaryName && secondaryName !== displayName}
            {#if secondaryIdentifierType}
              <IdentifierBadge
                identifierType={secondaryIdentifierType}
                value={secondaryName}
                variant="compact"
                class="max-w-[42%]"
              />
            {:else}
              <p class="truncate text-sm text-muted-foreground" title={secondaryName}>
                {secondaryName}
              </p>
            {/if}
          {/if}
        </div>
        {#if unresolved}
          <span
            class="mt-2 inline-flex shrink-0 items-center gap-1 rounded-md border border-amber-300/80 bg-amber-100/70 px-1.5 py-1 text-[10px] font-medium leading-none text-amber-800 dark:border-amber-700/70 dark:bg-amber-950/60 dark:text-amber-200"
            title="Unresolved entity"
          >
            <AlertTriangle class="size-3" />
            Unresolved
          </span>
        {/if}
      </div>
      <div class="flex shrink-0 items-center gap-2">
        <button
          type="button"
          disabled={selectingGroup}
          onkeydown={(event) => event.stopPropagation()}
          onclick={(event) => {
            event.stopPropagation();
            if (result.groupMemberKeys) {
              void toggleGroup();
            } else if (selected) {
              selection.removeEntity(publicId);
            } else {
              selection.addEntity({
                id: publicId,
                entityId: publicId,
                entityPk: result.entityPk,
                name: displayName,
                type: entityTypeLabel,
                fullResult: result,
              });
            }
          }}
          class={`inline-flex h-8 min-w-20 items-center justify-center gap-1.5 rounded-md px-2.5 text-xs font-medium transition-colors ${
            selected
              ? 'bg-green-600 text-white hover:bg-green-700'
              : 'bg-background/70 text-muted-foreground hover:bg-background hover:text-foreground'
          }`}
        >
          {#if selectingGroup}
            Adding…
          {:else if selected}
            <Check class="size-3.5" />
            Selected
          {:else}
            <Plus class="size-3.5" />
            Add
          {/if}
        </button>
      </div>
    </div>
    <div class="flex min-w-0 flex-wrap gap-1.5 px-4 pb-3">
      {#if result.groupMemberCount}
        <span class="rounded-md border px-1.5 py-0.5 text-[10px] font-medium"
          >Grouped · {result.groupMemberCount} matched {result.groupMemberCount === 1
            ? 'entity'
            : 'entities'}</span
        >
      {/if}
      <span
        class={`inline-flex min-w-0 max-w-full items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10px] font-medium leading-none ${entityTypeStyle.chipClass}`}
      >
        {#if entityTypeIcon}
          <span aria-hidden="true">{entityTypeIcon}</span>
        {:else}
          <Box class="size-3" />
        {/if}
        <span class="truncate">{entityTypeLabel}</span>
      </span>
      {#if entitySpecificHint}
        <span
          class={`inline-flex min-w-0 max-w-full items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10px] font-medium leading-none ${entityTypeStyle.chipClass}`}
        >
          {#if entitySpecificHint.label === 'Taxon'}
            <span aria-hidden="true">🌿</span>
          {/if}
          <span>{entitySpecificHint.label}:</span>
          <span class="truncate">{entitySpecificHint.value}</span>
        </span>
      {/if}
      {#if (result.relationCount || 0) > 0}
        <span
          class={`inline-flex min-w-0 max-w-full items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10px] font-medium leading-none ${entityTypeStyle.chipClass}`}
          title="Associated entities or relations"
        >
          <Link class="size-3" />
          <span>Relations:</span>
          <span class="tabular-nums">{formatNumber(result.relationCount || 0)}</span>
        </span>
      {/if}
      {#if showOntologyHint}
        <span
          class={`inline-flex h-5 items-center justify-center rounded-md border px-1.5 text-[10px] font-medium leading-none ${entityTypeStyle.chipClass}`}
          title="Click the card to see details and explore its position in the ontology"
        >
          <Network class="size-3" />
        </span>
      {/if}
    </div>
  </div>
{/snippet}
{@render card()}
{#if selectionError}<p role="alert" class="text-sm text-destructive">{selectionError}</p>{/if}
{#if !onopen}<EntityDetailsDialog bind:open={detailsOpen} entity={result} />{/if}
