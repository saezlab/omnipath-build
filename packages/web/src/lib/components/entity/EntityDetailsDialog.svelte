<script lang="ts">
  import EntityRelationsTab from './EntityRelationsTab.svelte';
  import IdentifierBadge from './IdentifierBadge.svelte';
  import { entityFromWire } from '$lib/api/adapters';
  import { fetchEntitiesByPublicIds, fetchRelationsSearch } from '$lib/api/client';
  import { untrack } from 'svelte';
  import { page } from '$app/state';
  import { formatTaxonomy } from '$lib/utils/taxonomy';
  import { measurementPresentation } from '$lib/utils/measurements';
  import {
    annotationLabel,
    plainText,
    isNarrativeAnnotation,
  } from '$lib/utils/annotation-presentation';
  import { releaseFetch } from '$lib/api/release';
  import { groupPublications, isPublicationTerm } from '$lib/utils/publications';
  import { getEntityTypeEmoji, getEntityTypeStyle } from '$lib/utils/entity-types';
  import { formatNumber } from '$lib/utils/format';
  import {
    ALL_SCOPE,
    identifiersOrgHref,
    keyIdentifiers,
    relationScopes,
    type MolecularContextLike,
    type RelationScope,
    type ScopeEntity,
  } from '$lib/utils/entity-overview';

  import { ArrowLeft, ExternalLink } from '@lucide/svelte';
  import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
  } from '$lib/components/ui/dialog/index.js';
  import { Button } from '$lib/components/ui/button/index.js';
  import * as Table from '$lib/components/ui/table/index.js';
  import * as Tabs from '$lib/components/ui/tabs/index.js';
  import MoleculeStructure from '$lib/components/entity/MoleculeStructure.svelte';
  import OntologyHierarchyBrowser from '$lib/components/explore/OntologyHierarchyBrowser.svelte';
  import {
    getEntityDisplayName,
    getEntityIdentifiers,
    getEntityPrimaryIdentifierBadge,
    getEntityPublicId,
    getEntitySecondaryName,
    getEntitySmiles,
    getEntityTypeLabel,
    getEntityTypeValue,
    getIdentifierTypeLabel,
    isChemicalEntity,
    isCvTermEntity,
    type EntityLike,
  } from '$lib/domain/display';

  interface Props {
    open: boolean;
    entity: EntityLike | null;
  }

  type AnnotationRow = {
    identity: string;
    term: string;
    label: string;
    value: string;
    unit: string;
    source: string;
  };

  type EntityIdentifierLike = {
    key: string;
    value: string;
  };

  type IdentifierRow = {
    section: 'Name' | 'Synonym' | 'Identifier';
    type: string;
    value: string;
    href: string | null;
  };

  type IdentifierGroup = {
    type: string;
    rows: IdentifierRow[];
  };

  type EntityOntologyHierarchyLike = {
    termId: string;
    ontologyPrefix: string | null;
    label: string | null;
    definition: string | null;
    ontologyId?: string | null;
    childCount?: number;
    parentCount?: number;
  };

  let { open = $bindable(false), entity }: Props = $props();
  let hydratedEntity = $state<EntityLike | null>(null);
  let loadingDetails = $state(false);
  let detailsError = $state<string | null>(null);

  let identifierLimit = $state(20);
  let descriptionExpanded = $state(false);
  let relationScopeList = $state<RelationScope[]>([]);
  let relationScopeId = $state(ALL_SCOPE);
  let relationTotals = $state<Record<string, number>>({});
  // Entities visited from the relations table, so the header can offer a way back.
  let history = $state<Array<{ entity: EntityLike; tab: string }>>([]);
  let restoreTab: string | null = null;
  let detailAttempt = $state(0);
  const detailKey = $derived(
    JSON.stringify([
      open,
      entity ? getEntityPublicId(entity) : null,
      page.data.selectedRelease,
      entity?.groupQuery,
      entity?.groupFilters,
      detailAttempt,
    ]),
  );
  let activeTab = $state('overview');

  $effect(() => {
    if (!open || !entity || loadingDetails) return;
    const current = hydratedEntity ?? entity;
    const rows = normalizeAnnotationRows(
      Array.isArray(current.entityAttributes) ? current.entityAttributes : [],
    );
    const available = [
      'overview',
      'relations',
      ...(getEntityIdentifierTotal(current) || normalizeIdentifierEntries(current).length
        ? ['identifiers']
        : []),
      ...(rows.some((row) => !isPublicationTerm(row.term) && !isNarrative(row.term))
        ? ['annotations']
        : []),
      ...(groupPublications(rows).length ? ['publications'] : []),
      ...(getOntologyHierarchy(current) ? ['ontology'] : []),
    ];
    if (available.length && !available.includes(activeTab)) activeTab = available[0];
  });

  const IDENTIFIER_PAGE_SIZE = 20;

  function normalizeIdentifierEntries(
    entityLike: EntityLike | null | undefined,
  ): EntityIdentifierLike[] {
    if (!entityLike) return [];
    const identifiers = new Map<string, EntityIdentifierLike>();
    for (const identifier of getEntityIdentifiers(entityLike)) {
      const key = identifier.key?.trim();
      const value = identifier.value?.trim();
      if (!key || !value) continue;
      identifiers.set(`${key.toLowerCase()}\u0000${value.toLowerCase()}`, { key, value });
    }
    return Array.from(identifiers.values());
  }

  function mergeEntityDetails(
    fallbackEntity: EntityLike,
    nextEntity: EntityLike | null | undefined,
    previousEntity: EntityLike | null,
  ): EntityLike {
    const merged = {
      ...fallbackEntity,
      ...(previousEntity ?? {}),
      ...(nextEntity ?? {}),
    } as EntityLike & {
      identifiers?: EntityIdentifierLike[];
      entityAttributes?: unknown;
      sources?: string[];
    };

    const identifiers = new Map<string, EntityIdentifierLike>();
    for (const entityLike of [fallbackEntity, previousEntity, nextEntity]) {
      for (const identifier of normalizeIdentifierEntries(entityLike)) {
        identifiers.set(
          `${identifier.key.toLowerCase()}\u0000${identifier.value.toLowerCase()}`,
          identifier,
        );
      }
    }

    if (identifiers.size > 0) {
      merged.identifiers = Array.from(identifiers.values());
    }

    const nextAttributes = Array.isArray(nextEntity?.entityAttributes)
      ? nextEntity.entityAttributes
      : null;
    const previousAttributes = Array.isArray(previousEntity?.entityAttributes)
      ? previousEntity.entityAttributes
      : null;
    const fallbackAttributes = Array.isArray(fallbackEntity.entityAttributes)
      ? fallbackEntity.entityAttributes
      : null;
    merged.entityAttributes =
      previousAttributes && nextAttributes
        ? [
            ...new Map(
              [...previousAttributes, ...nextAttributes].map((row) => [JSON.stringify(row), row]),
            ).values(),
          ]
        : (nextAttributes ?? previousAttributes ?? fallbackAttributes ?? null);

    const sourceValues = [fallbackEntity, previousEntity, nextEntity].flatMap((entityLike) =>
      Array.isArray(entityLike?.sources) ? entityLike.sources.filter(Boolean) : [],
    );
    if (sourceValues.length > 0) {
      merged.sources = Array.from(new Set(sourceValues));
    }

    return merged;
  }

  async function fetchDetailPage(fallback: EntityLike, offset: number, signal?: AbortSignal) {
    const publicId = getEntityPublicId(fallback);
    const response = fallback.groupMemberKeys
      ? await releaseFetch('/app-api/entities/groups', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          signal,
          body: JSON.stringify({
            strategy: fallback.groupStrategy || 'chemical_connectivity',
            group_key: publicId,
            query: fallback.groupQuery,
            filters: fallback.groupFilters,
            resources: fallback.groupResources,
            include_details: true,
            detail_limit: 20,
            detail_offset: offset,
          }),
        })
      : await releaseFetch(
          `/app-api/entities/${encodeURIComponent(publicId)}?includeRelationships=false&detail_limit=20&detail_offset=${offset}`,
          { signal },
        );
    if (!response.ok) throw new Error('Entity details could not be loaded.');
    const body = await response.json();
    const next = fallback.groupMemberKeys ? body.groups?.[0]?.entity : body.entity;
    if (next?.entityPk !== publicId)
      throw new Error('The response did not match the requested entity.');
    return entityFromWire(next);
  }

  async function moreDetails() {
    if (!entity || loadingDetails || !hydratedEntity?.detailNextCursor) return;
    const key = detailKey;
    const fallback = entity;
    loadingDetails = true;
    detailsError = null;
    try {
      const next = await fetchDetailPage(fallback, Number(hydratedEntity.detailNextCursor));
      if (key !== detailKey) return;
      hydratedEntity = mergeEntityDetails(fallback, next, hydratedEntity);
      identifierLimit += IDENTIFIER_PAGE_SIZE;
    } catch (error) {
      if (key === detailKey) detailsError = (error as Error).message;
    } finally {
      if (key === detailKey) loadingDetails = false;
    }
  }

  $effect(() => {
    const [isOpen, publicId] = JSON.parse(detailKey);
    const fallback = untrack(() => $state.snapshot(entity));
    hydratedEntity = null;
    detailsError = null;
    descriptionExpanded = false;
    activeTab = restoreTab ?? 'overview';
    restoreTab = null;
    identifierLimit = IDENTIFIER_PAGE_SIZE;
    loadingDetails = false;
    if (!isOpen || !fallback || !publicId) return;
    const controller = new AbortController();
    loadingDetails = true;
    untrack(() => fetchDetailPage(fallback, 0, controller.signal))
      .then((next) => {
        if (!controller.signal.aborted) hydratedEntity = mergeEntityDetails(fallback, next, null);
      })
      .catch((error) => {
        if (!controller.signal.aborted) detailsError = error.message;
      })
      .finally(() => {
        if (!controller.signal.aborted) loadingDetails = false;
      });
    return () => controller.abort();
  });

  function isGeneLike(entityLike: EntityLike): boolean {
    const type = (getEntityTypeValue(entityLike) || '').toLowerCase();
    return (
      entityLike.groupStrategy === 'gene_reference' ||
      Boolean(entityLike.geneReferenceKeys?.length) ||
      type === 'gene' ||
      type === 'protein'
    );
  }

  async function loadRelationScopes(current: EntityLike, signal: AbortSignal) {
    const scopeEntity: ScopeEntity = {
      entityPk: getEntityPublicId(current),
      label: getEntityDisplayName(current),
    };
    const memberKeys = current.groupMemberKeys ?? [];
    let context: MolecularContextLike | null = null;
    if (isGeneLike(current)) {
      const response = await releaseFetch(
        `/app-api/entities/${encodeURIComponent(scopeEntity.entityPk)}/molecular-context?view=reference&limit=1`,
        { signal },
      );
      if (response.ok) context = await response.json();
    }
    const members =
      !context?.referenceEntityKey && memberKeys.length > 1
        ? (await fetchEntitiesByPublicIds(memberKeys.slice(0, 24))).entities.map((member) => ({
            entityPk: member.entityPk,
            // The record label tells members apart ("Beta-D-Glucose") where display names agree.
            label: member.label || getEntityDisplayName(member),
            canonicalIdentifier: member.canonicalIdentifier,
          }))
        : [];
    return relationScopes(scopeEntity, { context, memberKeys, members });
  }

  // Structure groups only list their members once details load; gene groups scope by reference.
  const scopeKey = $derived(
    JSON.stringify([
      detailKey,
      entity && !isGeneLike(entity) ? ((hydratedEntity ?? entity).groupMemberKeys ?? null) : null,
    ]),
  );

  $effect(() => {
    const [key, loadedMemberKeys] = JSON.parse(scopeKey);
    const [isOpen, publicId] = JSON.parse(key);
    const snapshot = untrack(() => $state.snapshot(entity)) as EntityLike | null;
    const current =
      snapshot && loadedMemberKeys ? { ...snapshot, groupMemberKeys: loadedMemberKeys } : snapshot;
    relationScopeList = [];
    relationTotals = {};
    relationScopeId = ALL_SCOPE;
    if (!isOpen || !current || !publicId) return;
    if (current.groupMemberCount && !current.groupMemberKeys?.length) return;
    const controller = new AbortController();
    const countScopes = (scopes: RelationScope[]) => {
      for (const scope of scopes.slice(0, 25)) {
        fetchRelationsSearch({ filters: scope.filters, limit: 1, offset: 0 }, controller.signal)
          .then((data) => {
            if (!controller.signal.aborted)
              relationTotals = { ...relationTotals, [scope.id]: Number(data.total ?? 0) };
          })
          .catch(() => {});
      }
    };
    loadRelationScopes(current, controller.signal)
      .catch(() =>
        relationScopes(
          { entityPk: publicId, label: getEntityDisplayName(current) },
          { memberKeys: current.groupMemberKeys ?? [] },
        ),
      )
      .then(({ scopes, initial }) => {
        if (controller.signal.aborted) return;
        relationScopeList = scopes;
        relationScopeId = initial;
        countScopes(scopes);
      });
    return () => controller.abort();
  });

  $effect(() => {
    if (!open) history = [];
  });

  function showEntity(next: EntityLike) {
    if (entity) history = [...history, { entity, tab: activeTab }];
    entity = next;
  }

  function goBack() {
    const previous = history.at(-1);
    if (!previous) return;
    history = history.slice(0, -1);
    restoreTab = previous.tab;
    entity = previous.entity;
  }

  function showRelations(scopeId = ALL_SCOPE) {
    relationScopeId = scopeId;
    activeTab = 'relations';
  }

  function groupLabel(entityLike: EntityLike): string | null {
    const count = entityLike.groupMemberCount;
    if (!count) return null;
    const kind = entityLike.groupStrategy === 'gene_reference' ? 'Gene group' : 'Structure group';
    const types = entityLike.memberEntityTypes?.length
      ? ` (${entityLike.memberEntityTypes.join(', ')})`
      : '';
    return `${kind} · ${count} ${count === 1 ? 'entity' : 'entities'}${types}`;
  }

  function isNarrative(term: string) {
    return isNarrativeAnnotation(term);
  }

  function getDescriptionSections(entity: EntityLike) {
    return normalizeAnnotationRows(
      Array.isArray(entity.entityAttributes) ? entity.entityAttributes : [],
    )
      .filter((row) => isNarrative(row.term) && row.value)
      .map((row) => ({ label: row.label, items: [plainText(row.value)], source: row.source }));
  }

  function normalizeAnnotationRows(attributes: unknown[]): AnnotationRow[] {
    const rows = attributes.flatMap((attribute) => {
      if (typeof attribute !== 'object' || attribute === null || !('term' in attribute)) return [];
      const row = attribute as Record<string, unknown>;
      const measurement = measurementPresentation(row);
      const term = typeof row.term === 'string' ? row.term.trim() : '';
      const value = measurement?.value ?? (typeof row.value === 'string' ? row.value.trim() : '');
      const unit = measurement?.unit ?? (typeof row.unit === 'string' ? row.unit.trim() : '');
      const source = typeof row.source === 'string' ? row.source.trim() : '';
      const label = annotationLabel(measurement?.sourceField || term);
      if (!term) return [];

      return [
        {
          identity: JSON.stringify([term, row.value, row.quantity, unit, source, row.dataset]),
          term,
          label,
          value,
          unit,
          source,
        },
      ];
    });

    const uniqueRows = new Map<string, AnnotationRow>();
    for (const row of rows) {
      uniqueRows.set(row.identity, row);
    }
    return Array.from(uniqueRows.values());
  }

  function annotationSortRank(labelValue: string): number {
    const label = labelValue.toLowerCase();
    if (label === 'function') return 0;
    if (label === 'subcellular location') return 1;
    if (label === 'disease involvement' || label === 'disease') return 2;
    return 10;
  }

  function sortAnnotations(rows: AnnotationRow[]): AnnotationRow[] {
    return [...rows].sort(
      (a, b) =>
        annotationSortRank(a.label) - annotationSortRank(b.label) ||
        a.label.localeCompare(b.label) ||
        a.value.localeCompare(b.value),
    );
  }

  function formatAnnotationValue(row: AnnotationRow): string {
    const rawValue = row.value ? `${row.value}${row.unit ? ` ${row.unit}` : ''}` : '';
    if (!rawValue) return 'No value';

    const withoutPrefix = rawValue.replace(
      new RegExp(`^${row.label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}:\\s*`, 'i'),
      '',
    );
    const cleaned = plainText(withoutPrefix).replace(/\s+/g, ' ').trim();
    return cleaned || rawValue;
  }

  function getEntityIdentifierTotal(entity: EntityLike): number {
    const total = (entity as { identifiersTotal?: unknown }).identifiersTotal;
    const numericTotal = Number(total);
    return Number.isFinite(numericTotal) ? numericTotal : normalizeIdentifierEntries(entity).length;
  }

  function identifierTypeText(identifier: EntityIdentifierLike): string {
    return `${identifier.key} ${getIdentifierTypeLabel(identifier.key)}`
      .toLowerCase()
      .replace(/[_-]/g, ' ');
  }

  function identifierNameSection(identifier: EntityIdentifierLike): 'Name' | 'Synonym' | null {
    const text = identifierTypeText(identifier);
    if (text.includes('synonym')) return 'Synonym';
    if (text.includes('gene name primary')) return 'Name';
    if (text.includes('recommended name')) return 'Name';
    if (text.includes('entry name')) return 'Name';
    if (getIdentifierTypeLabel(identifier.key).toLowerCase() === 'name') return 'Name';
    return null;
  }

  function getIdentifierRows(identifiers: EntityIdentifierLike[]): IdentifierRow[] {
    const nameSynonymRows = new Map<string, IdentifierRow>();
    const identifierRows: IdentifierRow[] = [];

    for (const identifier of identifiers) {
      const section = identifierNameSection(identifier);
      const type = getIdentifierTypeLabel(identifier.key);
      if (section) {
        const key = `${section}\u0000${type.toLowerCase()}\u0000${identifier.value.toLowerCase()}`;
        nameSynonymRows.set(key, { section, type, value: identifier.value, href: null });
      } else {
        identifierRows.push({
          section: 'Identifier',
          type,
          value: identifier.value,
          href: identifiersOrgHref(identifierTypeText(identifier), identifier.value),
        });
      }
    }

    return [
      ...Array.from(nameSynonymRows.values()).sort(
        (a, b) =>
          a.section.localeCompare(b.section) ||
          a.type.localeCompare(b.type) ||
          a.value.localeCompare(b.value),
      ),
      ...identifierRows.sort(
        (a, b) => a.type.localeCompare(b.type) || a.value.localeCompare(b.value),
      ),
    ];
  }

  function identifierSectionRank(section: IdentifierRow['section']): number {
    if (section === 'Name') return 0;
    if (section === 'Synonym') return 1;
    return 2;
  }

  function getIdentifierGroups(rows: IdentifierRow[]): IdentifierGroup[] {
    const groups = new Map<string, IdentifierGroup>();
    for (const row of rows) {
      const group = groups.get(row.type) ?? { type: row.type, rows: [] };
      group.rows.push(row);
      groups.set(row.type, group);
    }

    return Array.from(groups.values())
      .map((group) => ({
        ...group,
        rows: group.rows.sort(
          (a, b) =>
            identifierSectionRank(a.section) - identifierSectionRank(b.section) ||
            a.value.localeCompare(b.value),
        ),
      }))
      .sort(
        (a, b) =>
          Math.min(...a.rows.map((row) => identifierSectionRank(row.section))) -
            Math.min(...b.rows.map((row) => identifierSectionRank(row.section))) ||
          a.type.localeCompare(b.type),
      );
  }

  function getOntologyHierarchy(entityLike: EntityLike): EntityOntologyHierarchyLike | null {
    const hierarchy = (entityLike as { ontologyHierarchy?: unknown }).ontologyHierarchy;
    if (hierarchy && typeof hierarchy === 'object') {
      const row = hierarchy as Partial<EntityOntologyHierarchyLike>;
      if (row.termId) {
        return {
          termId: row.termId,
          ontologyPrefix: row.ontologyPrefix ?? null,
          label: row.label ?? null,
          definition: row.definition ?? null,
          ontologyId: row.ontologyId ?? null,
          childCount: Number(row.childCount || 0),
          parentCount: Number(row.parentCount || 0),
        };
      }
    }
    if (!isCvTermEntity(entityLike)) return null;
    const id = entityLike.canonicalIdentifier || '';
    if (!id) return null;
    const prefix = id.includes(':') ? id.split(':')[0] : id.includes('-') ? id.split('-')[0] : null;
    return {
      termId: id,
      ontologyPrefix: prefix,
      label: getEntityDisplayName(entityLike),
      definition: null,
      ontologyId: null,
      childCount: 0,
      parentCount: 0,
    };
  }
</script>

<Dialog bind:open>
  <DialogContent
    class="flex h-[min(720px,90dvh)] max-h-[90dvh] flex-col gap-0 overflow-hidden p-0 sm:max-w-5xl"
  >
    {#if entity}
      {@const detailEntity = hydratedEntity ?? entity}
      {@const displayName = getEntityDisplayName(detailEntity)}
      {@const detailSections = getDescriptionSections(detailEntity)}
      {@const detailIdentifiers = normalizeIdentifierEntries(detailEntity)}
      {@const detailIdentifierRows = getIdentifierRows(detailIdentifiers.slice(0, identifierLimit))}
      {@const detailIdentifierGroups = getIdentifierGroups(detailIdentifierRows)}
      {@const detailIdentifierTotal = getEntityIdentifierTotal(detailEntity)}
      {@const detailSmiles = getEntitySmiles(detailEntity)}
      {@const headerIdentifiers = keyIdentifiers(
        getEntityPrimaryIdentifierBadge(detailEntity),
        detailIdentifiers,
      )}
      {@const secondaryName = getEntitySecondaryName(detailEntity)}
      {@const subtitle =
        secondaryName &&
        secondaryName !== detailEntity.canonicalIdentifier &&
        !detailIdentifiers.some(
          (identifier) =>
            identifier.value === secondaryName && identifierNameSection(identifier) === null,
        )
          ? secondaryName
          : null}
      {@const typeLabel = getEntityTypeLabel(detailEntity)}
      {@const typeStyle = getEntityTypeStyle(getEntityTypeValue(detailEntity))}
      {@const detailGroupLabel = groupLabel(detailEntity)}
      {@const detailTaxonomy = formatTaxonomy(detailEntity.taxonomyId, detailEntity.taxonomyName)}
      {@const ontologyHierarchy = getOntologyHierarchy(detailEntity)}
      {@const showChemicalStructure =
        !detailEntity.groupMemberKeys && isChemicalEntity(detailEntity) && detailSmiles}
      {@const detailAttributes = Array.isArray(detailEntity.entityAttributes)
        ? detailEntity.entityAttributes
        : []}
      {@const detailAnnotationRows = normalizeAnnotationRows(detailAttributes)}
      {@const detailPublications = groupPublications(detailAnnotationRows)}
      {@const detailNonPubmedAnnotations = sortAnnotations(
        detailAnnotationRows.filter(
          (row) => !isPublicationTerm(row.term) && !isNarrative(row.term),
        ),
      )}
      {@const relationTotal = relationTotals[ALL_SCOPE]}
      {@const partScopes = relationScopeList.filter((scope) => scope.kind !== 'all')}
      {@const longDescription = detailSections.some((section) =>
        section.items.some((item) => item.length > 420),
      )}

      <DialogHeader class="shrink-0 gap-3 px-6 pt-5 pb-4 pr-14 text-left">
        {#if history.length}
          <button
            type="button"
            class="inline-flex w-fit items-center gap-1 rounded-sm text-xs text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onclick={goBack}
          >
            <ArrowLeft class="size-3.5" />
            Back to {getEntityDisplayName(history[history.length - 1].entity)}
          </button>
        {/if}
        <div class="space-y-1">
          <DialogTitle class="break-words text-2xl font-semibold leading-tight tracking-tight"
            >{displayName}</DialogTitle
          >
          {#if subtitle}<DialogDescription class="break-words">{subtitle}</DialogDescription>{/if}
        </div>
        <div class="flex flex-wrap items-center gap-1.5">
          <span
            class={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-medium ${typeStyle.chipClass}`}
            ><span aria-hidden="true"
              >{getEntityTypeEmoji(getEntityTypeValue(detailEntity) || '')}</span
            >{typeLabel}</span
          >
          {#if detailGroupLabel}<span
              class="rounded-md border px-2 py-0.5 text-xs font-medium text-muted-foreground"
              >{detailGroupLabel}</span
            >{/if}
          {#if detailTaxonomy}<span
              class="inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-medium text-muted-foreground"
              ><span aria-hidden="true">🌿</span>{detailTaxonomy}</span
            >{/if}
        </div>
        {#if headerIdentifiers.length}
          <div class="flex flex-wrap gap-1.5">
            {#each headerIdentifiers as identifier (`${identifier.key}:${identifier.value}`)}
              {@const href = identifiersOrgHref(
                `${identifier.key} ${getIdentifierTypeLabel(identifier.key)}`,
                identifier.value,
              )}
              {#if href}
                <a
                  {href}
                  target="_blank"
                  rel="noreferrer"
                  class="inline-flex max-w-full items-center gap-1 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [&:hover>span]:border-foreground/40"
                  title="Open on identifiers.org"
                >
                  <IdentifierBadge
                    identifierType={identifier.key}
                    value={identifier.value}
                    variant="subtle"
                  />
                </a>
              {:else}
                <IdentifierBadge
                  identifierType={identifier.key}
                  value={identifier.value}
                  variant="subtle"
                />
              {/if}
            {/each}
          </div>
        {/if}
      </DialogHeader>
      {#if detailsError}
        <div
          role="alert"
          class="mx-6 mb-3 flex flex-wrap items-center gap-3 rounded-md border border-destructive/40 px-3 py-2 text-sm text-destructive"
        >
          <span>{detailsError}</span>
          <Button variant="outline" size="sm" onclick={() => detailAttempt++}>Try again</Button>
        </div>
      {/if}
      <Tabs.Root bind:value={activeTab} class="min-h-0 flex-1 gap-0">
        <div class="shrink-0 overflow-x-auto border-b px-6">
          <Tabs.List
            variant="line"
            class="h-11 justify-start gap-4 p-0"
            aria-label="Entity details"
          >
            <Tabs.Trigger value="overview" class="flex-none">Overview</Tabs.Trigger>
            <Tabs.Trigger value="relations" class="flex-none"
              >Relations {#if relationTotal !== undefined}<span
                  class="text-xs text-muted-foreground tabular-nums"
                  >{formatNumber(relationTotal)}</span
                >{/if}</Tabs.Trigger
            >
            {#if detailIdentifierRows.length > 0 || detailIdentifierTotal > 0}<Tabs.Trigger
                value="identifiers"
                class="flex-none"
                >Identifiers <span class="text-xs text-muted-foreground tabular-nums"
                  >{detailIdentifierTotal}</span
                ></Tabs.Trigger
              >{/if}
            {#if detailNonPubmedAnnotations.length > 0}<Tabs.Trigger
                value="annotations"
                class="flex-none"
                >Annotations <span class="text-xs text-muted-foreground tabular-nums"
                  >{detailNonPubmedAnnotations.length}</span
                ></Tabs.Trigger
              >{/if}
            {#if detailPublications.length > 0}<Tabs.Trigger value="publications" class="flex-none"
                >Publications <span class="text-xs text-muted-foreground tabular-nums"
                  >{detailPublications.length}</span
                ></Tabs.Trigger
              >{/if}
            {#if ontologyHierarchy}<Tabs.Trigger value="ontology" class="flex-none"
                >Ontology</Tabs.Trigger
              >{/if}
          </Tabs.List>
        </div>
        <Tabs.Content value="overview" class="min-h-0 overflow-y-auto overscroll-contain p-6">
          <div
            class={`grid gap-8 ${showChemicalStructure ? 'md:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]' : 'max-w-3xl'}`}
          >
            <div class="min-w-0 space-y-6">
              {#if detailSections.length}
                {#each detailSections as section}
                  <section class="space-y-1.5">
                    <h3 class="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                      {section.label}
                    </h3>
                    {#each section.items as item}<p
                        class={`break-words text-sm leading-6 ${descriptionExpanded ? '' : 'line-clamp-5'}`}
                      >
                        {item}
                      </p>{/each}
                    {#if section.source}<p class="text-xs text-muted-foreground">
                        Source: {section.source}
                      </p>{/if}
                  </section>
                {/each}
                {#if longDescription}<Button
                    variant="link"
                    size="sm"
                    class="h-auto px-0"
                    onclick={() => (descriptionExpanded = !descriptionExpanded)}
                    >{descriptionExpanded ? 'Show less' : 'Show more'}</Button
                  >{/if}
              {:else if loadingDetails && !hydratedEntity}
                <p role="status" class="text-sm text-muted-foreground">Loading details…</p>
              {:else}
                <p class="text-sm text-muted-foreground">
                  No description is available for this entity.
                </p>
              {/if}

              {#if partScopes.length}
                <section class="space-y-2">
                  <h3 class="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    {partScopes[0].kind === 'product' ? 'Products' : 'Grouped entities'}
                    <span class="font-normal normal-case tracking-normal tabular-nums"
                      >{partScopes.length}</span
                    >
                  </h3>
                  <div class="divide-y overflow-hidden rounded-lg border">
                    {#each partScopes as scope (scope.id)}
                      <button
                        type="button"
                        class="flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm transition-colors hover:bg-muted/50 focus-visible:bg-muted/50 focus-visible:outline-none"
                        onclick={() => showRelations(scope.id)}
                      >
                        <span class="min-w-0">
                          <span class="font-medium">{scope.title ?? scope.label}</span>
                          {#if scope.identifier && scope.identifier !== scope.title}<span
                              class="ml-2 break-all font-mono text-xs text-muted-foreground"
                              >{scope.identifier}</span
                            >{/if}
                        </span>
                        <span class="shrink-0 text-xs text-muted-foreground tabular-nums">
                          {relationTotals[scope.id] !== undefined
                            ? `${formatNumber(relationTotals[scope.id])} relations`
                            : 'Relations'} →
                        </span>
                      </button>
                    {/each}
                  </div>
                </section>
              {/if}
            </div>

            {#if showChemicalStructure}
              <section
                class="flex min-w-0 justify-center self-start overflow-x-auto rounded-lg border p-4"
              >
                <MoleculeStructure
                  smiles={detailSmiles}
                  width={300}
                  height={220}
                  renderOnClick={false}
                />
              </section>
            {/if}
          </div>
        </Tabs.Content>
        <Tabs.Content value="relations" class="min-h-0 overflow-y-auto overscroll-contain p-6">
          {#if activeTab === 'relations'}
            {#if relationScopeList.length}
              <EntityRelationsTab
                scopes={relationScopeList}
                bind:scopeId={relationScopeId}
                totals={relationTotals}
                onEntitySelect={showEntity}
              />
            {:else}
              <p role="status" class="text-sm text-muted-foreground">Loading relations…</p>
            {/if}
          {/if}
        </Tabs.Content>
        <Tabs.Content value="identifiers" class="min-h-0 overflow-y-auto overscroll-contain p-6">
          {#if detailIdentifierRows.length}
            <Table.Root>
              <Table.Header class="sticky top-0 z-10 bg-muted/80 backdrop-blur">
                <Table.Row>
                  <Table.Head>Value</Table.Head>
                </Table.Row>
              </Table.Header>
              <Table.Body>
                {#each detailIdentifierGroups as group}
                  <Table.Row class="bg-muted/45 hover:bg-muted/45">
                    <Table.Cell
                      class="whitespace-normal py-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground"
                    >
                      <div class="flex items-center justify-between gap-3">
                        <span>{group.type}</span>
                        <span class="font-normal tabular-nums">{group.rows.length}</span>
                      </div>
                    </Table.Cell>
                  </Table.Row>
                  {#each group.rows as row}
                    <Table.Row>
                      <Table.Cell
                        class={`max-w-lg whitespace-normal align-top text-foreground ${row.section === 'Identifier' ? 'break-all font-mono' : 'break-words font-medium'}`}
                      >
                        {#if row.href}
                          <a
                            href={row.href}
                            target="_blank"
                            rel="noreferrer"
                            class="inline-flex items-center gap-1 text-foreground underline decoration-muted-foreground/50 underline-offset-4 hover:decoration-foreground"
                          >
                            {row.value}
                            <ExternalLink class="size-3 shrink-0" />
                          </a>
                        {:else}
                          {row.value}
                        {/if}
                      </Table.Cell>
                    </Table.Row>
                  {/each}
                {/each}
              </Table.Body>
            </Table.Root>
          {:else}<p class="text-muted-foreground">No identifiers are available.</p>{/if}
          {#if detailEntity.identifiersNextCursor || detailIdentifiers.length > identifierLimit}<div
              class="mt-4 flex flex-wrap items-center gap-3"
            >
              <Button
                variant="outline"
                size="sm"
                disabled={loadingDetails}
                onclick={() => {
                  if (detailIdentifiers.length > identifierLimit)
                    identifierLimit += IDENTIFIER_PAGE_SIZE;
                  else void moreDetails();
                }}>Load more identifiers</Button
              >
              <span class="text-xs text-muted-foreground"
                >Showing {Math.min(identifierLimit, detailIdentifiers.length)} of {detailIdentifierTotal}</span
              >
            </div>{/if}
        </Tabs.Content>
        <Tabs.Content value="annotations" class="min-h-0 overflow-y-auto overscroll-contain p-6">
          {#if detailNonPubmedAnnotations.length}
            <Table.Root>
              <Table.Header class="sticky top-0 z-10 bg-muted/80 backdrop-blur">
                <Table.Row>
                  <Table.Head class="w-48">Annotation</Table.Head>
                  <Table.Head>Value</Table.Head>
                  <Table.Head class="w-36">Source</Table.Head>
                </Table.Row>
              </Table.Header>
              <Table.Body>
                {#each detailNonPubmedAnnotations as annotation}
                  <Table.Row>
                    <Table.Cell class="whitespace-normal align-top font-medium text-foreground">
                      {annotation.label}
                    </Table.Cell>
                    <Table.Cell
                      class="max-w-lg whitespace-normal break-words align-top text-foreground"
                    >
                      {#if annotation.term.replace(/^biolink:/, '') === 'has_biological_sequence'}
                        <details>
                          <summary>Sequence ({annotation.value.length} residues)</summary>
                          <pre
                            class="whitespace-pre-wrap break-all text-xs">{annotation.value}</pre>
                        </details>
                      {:else}{formatAnnotationValue(annotation)}{/if}
                    </Table.Cell>
                    <Table.Cell class="whitespace-normal align-top text-muted-foreground">
                      {annotation.source || 'Unknown'}
                    </Table.Cell>
                  </Table.Row>
                {/each}
              </Table.Body>
            </Table.Root>
          {:else}<p class="text-muted-foreground">
              No annotations are available for this entity.
            </p>{/if}
          {#if detailEntity.detailNextCursor}<div class="mt-4">
              <Button variant="outline" size="sm" disabled={loadingDetails} onclick={moreDetails}
                >{loadingDetails ? 'Loading…' : 'Load more'}</Button
              >
            </div>{/if}
        </Tabs.Content>
        <Tabs.Content value="publications" class="min-h-0 overflow-y-auto overscroll-contain p-6">
          {#if detailPublications.length}
            <Table.Root>
              <Table.Header class="sticky top-0 z-10 bg-muted/80 backdrop-blur">
                <Table.Row>
                  <Table.Head class="w-40">Publication</Table.Head>
                  <Table.Head>Sources</Table.Head>
                </Table.Row>
              </Table.Header>
              <Table.Body>
                {#each detailPublications as publication}
                  {@const publicationSources = publication.sources}
                  <Table.Row>
                    <Table.Cell class="align-top">
                      {#if publication.url}
                        <a
                          href={publication.url}
                          target="_blank"
                          rel="noreferrer"
                          class="inline-flex items-center gap-1 font-mono text-foreground underline decoration-muted-foreground/50 underline-offset-4 hover:decoration-foreground"
                        >
                          {publication.id}
                          <ExternalLink class="size-3" />
                        </a>
                      {:else}{publication.id}{/if}
                    </Table.Cell>
                    <Table.Cell
                      class="whitespace-normal break-words align-top text-muted-foreground"
                    >
                      {publicationSources.length ? publicationSources.join(', ') : 'Unknown'}
                    </Table.Cell>
                  </Table.Row>
                {/each}
              </Table.Body>
            </Table.Root>
          {:else}<p class="text-muted-foreground">No publications are available.</p>{/if}
          {#if detailEntity.detailNextCursor}<div class="mt-4">
              <Button variant="outline" size="sm" disabled={loadingDetails} onclick={moreDetails}
                >{loadingDetails ? 'Loading…' : 'Load more'}</Button
              >
            </div>{/if}
        </Tabs.Content>
        {#if ontologyHierarchy}
          <Tabs.Content value="ontology" class="min-h-0 overflow-y-auto overscroll-contain p-6"
            ><OntologyHierarchyBrowser term={ontologyHierarchy} /></Tabs.Content
          >
        {/if}
      </Tabs.Root>
    {/if}
  </DialogContent>
</Dialog>
