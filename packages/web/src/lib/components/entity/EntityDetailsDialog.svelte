<script lang="ts">
  import MolecularContext from './MolecularContext.svelte';
  import { entityFromWire } from '$lib/api/adapters';
  import { untrack } from 'svelte';
  import { page } from '$app/state';
  import { formatTaxonomy } from '$lib/utils/taxonomy';
  import EntityRelationships, { type Relationship } from './EntityRelationships.svelte';
  import { measurementPresentation } from '$lib/utils/measurements';
  import {
    annotationLabel,
    plainText,
    isNarrativeAnnotation,
  } from '$lib/utils/annotation-presentation';
  import { releaseFetch } from '$lib/api/release';
  import { groupPublications, isPublicationTerm } from '$lib/utils/publications';

  import { ExternalLink } from '@lucide/svelte';
  import {
    Dialog,
    DialogContent,
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
    getEntitySmiles,
    getEntityTypeLabel,
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
  let relationships = $state<Relationship[]>([]);
  let relationshipsTotal = $state(0);
  let loadingDetails = $state(false);
  let detailsError = $state<string | null>(null);

  let identifierLimit = $state(20);
  let loadingRelationships = $state(false);
  let relationshipsError = $state<string | null>(null);
  let relationshipsLoaded = $state(false);
  let relationshipOffset = $state(0);
  let loadedRelationshipRequest: string | null = null;
  let relationshipNext = $state<string | null>(null);
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
  let activeTab = $state('general');

  $effect(() => {
    if (!open || !entity || loadingDetails) return;
    const current = hydratedEntity ?? entity;
    const rows = normalizeAnnotationRows(
      Array.isArray(current.entityAttributes) ? current.entityAttributes : [],
    );
    const available = [
      ...(getDescriptionSections(current).length ||
      (!current.groupMemberKeys && isChemicalEntity(current) && getEntitySmiles(current))
        ? ['general']
        : []),
      ...(rows.some((row) => !isPublicationTerm(row.term) && !isNarrative(row.term))
        ? ['annotations']
        : []),
      ...(groupPublications(rows).length ? ['publications'] : []),
      ...(getEntityIdentifierTotal(current) || normalizeIdentifierEntries(current).length
        ? ['identifiers']
        : []),
      'relationships',
      'molecular',
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
    relationships = [];
    relationshipsTotal = 0;
    relationshipsLoaded = false;
    loadedRelationshipRequest = null;
    relationshipsError = null;
    relationshipOffset = 0;
    relationshipNext = null;
    detailsError = null;
    activeTab = 'general';
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

  $effect(() => {
    const [isOpen, publicId] = JSON.parse(detailKey);
    const offset = relationshipOffset;
    if (!isOpen || !publicId || activeTab !== 'relationships') return;
    const requestKey = `${detailKey}:${offset}`;
    if (loadedRelationshipRequest === requestKey) return;
    const controller = new AbortController();
    loadingRelationships = true;
    relationshipsError = null;
    const group = untrack(() => (entity?.groupMemberKeys ? entity : null));
    untrack(() =>
      group
        ? releaseFetch('/app-api/entities/group-relationships', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            signal: controller.signal,
            body: JSON.stringify({
              strategy: group.groupStrategy || 'chemical_connectivity',
              group_key: group.entityPk,
              query: group.groupQuery,
              filters: group.groupFilters,
              resources: group.groupResources,
              limit: 20,
              offset,
            }),
          })
        : releaseFetch(
            `/app-api/entities/${encodeURIComponent(publicId)}/relationships?limit=20&offset=${offset}`,
            { signal: controller.signal },
          ),
    )
      .then(async (response) => {
        if (!response.ok) throw new Error('Relationships could not be loaded.');
        const payload = await response.json();
        if (!controller.signal.aborted) {
          relationships = offset
            ? [...relationships, ...payload.relationships]
            : payload.relationships;
          relationshipsTotal = payload.relationshipsTotal;
          relationshipNext = payload.nextCursor;
          relationshipsLoaded = true;
          loadedRelationshipRequest = requestKey;
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted) relationshipsError = error.message;
      })
      .finally(() => {
        if (!controller.signal.aborted) loadingRelationships = false;
      });
    return () => controller.abort();
  });

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
          href: identifiersOrgHref(identifier.key, identifier.value),
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

  function compactIdentifier(identifierType: string, value: string): string | null {
    const trimmedValue = value.trim();
    if (!trimmedValue) return null;
    if (/^[A-Za-z][A-Za-z0-9_.-]*:\S+$/.test(trimmedValue)) return trimmedValue;

    const text = `${identifierType} ${getIdentifierTypeLabel(identifierType)}`.toLowerCase();
    const namespace = text.includes('uniprot')
      ? 'uniprot'
      : text.includes('chebi')
        ? 'chebi'
        : text.includes('chembl')
          ? 'chembl.compound'
          : text.includes('hmdb')
            ? 'hmdb'
            : text.includes('pubchem')
              ? 'pubchem.compound'
              : text.includes('ensembl')
                ? 'ensembl'
                : text.includes('hgnc')
                  ? 'hgnc'
                  : text.includes('entrez') || text.includes('ncbi gene')
                    ? 'ncbigene'
                    : text.includes('taxonomy') || text.includes('tax id')
                      ? 'taxonomy'
                      : text.includes('reactome')
                        ? 'reactome'
                        : text.includes('interpro')
                          ? 'interpro'
                          : null;

    return namespace ? `${namespace}:${trimmedValue}` : null;
  }

  function identifiersOrgHref(identifierType: string, value: string): string | null {
    const compactId = compactIdentifier(identifierType, value);
    if (!compactId) return null;
    const separatorIndex = compactId.indexOf(':');
    if (separatorIndex === -1) return `https://identifiers.org/${encodeURIComponent(compactId)}`;
    const namespace = compactId.slice(0, separatorIndex);
    const localId = compactId.slice(separatorIndex + 1);
    return `https://identifiers.org/${encodeURIComponent(namespace)}:${encodeURIComponent(localId)}`;
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
    class="flex h-[min(700px,88dvh)] max-h-[88dvh] flex-col gap-0 overflow-hidden p-0 sm:max-w-5xl"
  >
    {#if entity}
      {@const detailEntity = hydratedEntity ?? entity}
      {@const detailSections = getDescriptionSections(detailEntity)}
      {@const detailIdentifiers = normalizeIdentifierEntries(detailEntity)}
      {@const detailIdentifierRows = getIdentifierRows(detailIdentifiers.slice(0, identifierLimit))}
      {@const detailIdentifierGroups = getIdentifierGroups(detailIdentifierRows)}
      {@const detailIdentifierTotal = getEntityIdentifierTotal(detailEntity)}
      {@const detailSmiles = getEntitySmiles(detailEntity)}
      {@const primaryIdentifierBadge = getEntityPrimaryIdentifierBadge(detailEntity)}
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

      <DialogHeader class="shrink-0 px-6 pt-6 pb-4 pr-14 text-left">
        <DialogTitle class="break-words text-xl leading-snug"
          >{getEntityDisplayName(detailEntity)}</DialogTitle
        >
        <div class="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted-foreground">
          <span>{getEntityTypeLabel(detailEntity)}</span>
          {#if detailEntity.memberEntityTypes?.length}<span
              >{detailEntity.memberEntityTypes.join(', ')} source records</span
            >{/if}
          {#if detailEntity.groupMemberCount}<span
              >Grouped · {detailEntity.groupMemberCount} matched entities</span
            >{/if}
          {#if detailTaxonomy}<span>Taxon: {detailTaxonomy}</span>{/if}
          <span class="break-all font-mono text-xs"
            >{getIdentifierTypeLabel(primaryIdentifierBadge.key)}: {primaryIdentifierBadge.value}</span
          >
        </div>
      </DialogHeader>
      {#if loadingDetails && !hydratedEntity}<p
          role="status"
          class="px-6 pb-3 text-sm text-muted-foreground"
        >
          Loading details…
        </p>{/if}
      {#if detailsError}<p role="alert" class="px-6 pb-3 text-sm text-destructive">
          {detailsError}
        </p>{/if}
      <Tabs.Root bind:value={activeTab} class="min-h-0 flex-1 gap-0">
        <div class="shrink-0 overflow-x-auto border-b px-6">
          <Tabs.List
            variant="line"
            class="h-11 justify-start gap-4 p-0"
            aria-label="Entity details"
          >
            {#if detailSections.length || showChemicalStructure}<Tabs.Trigger
                value="general"
                class="flex-none">General</Tabs.Trigger
              >{/if}
            {#if detailNonPubmedAnnotations.length > 0}<Tabs.Trigger
                value="annotations"
                class="flex-none"
                >Annotations <span class="text-xs text-muted-foreground"
                  >{detailNonPubmedAnnotations.length}</span
                ></Tabs.Trigger
              >{/if}
            {#if detailPublications.length > 0}<Tabs.Trigger value="publications" class="flex-none"
                >Publications <span class="text-xs text-muted-foreground"
                  >{detailPublications.length}</span
                ></Tabs.Trigger
              >{/if}
            {#if detailIdentifierRows.length > 0 || detailIdentifierTotal > 0}<Tabs.Trigger
                value="identifiers"
                class="flex-none"
                >Identifiers <span class="text-xs text-muted-foreground"
                  >{detailIdentifierTotal}</span
                ></Tabs.Trigger
              >{/if}
            <Tabs.Trigger value="molecular" class="flex-none"
              >Products and molecular evidence</Tabs.Trigger
            >
            <Tabs.Trigger value="relationships" class="flex-none"
              >Relationships {#if relationshipsLoaded}<span class="text-xs text-muted-foreground"
                  >{relationshipsTotal}</span
                >{/if}</Tabs.Trigger
            >
            {#if ontologyHierarchy}<Tabs.Trigger value="ontology" class="flex-none"
                >Ontology</Tabs.Trigger
              >{/if}
          </Tabs.List>
        </div>
        <Tabs.Content value="general" class="min-h-0 overflow-y-auto overscroll-contain p-6">
          <div class="space-y-6">
            {#if showChemicalStructure}
              <section class="rounded-lg border p-4">
                <h3 class="text-sm font-medium">Chemical structure</h3>
                <div class="flex justify-center overflow-x-auto">
                  <MoleculeStructure
                    smiles={detailSmiles}
                    width={320}
                    height={240}
                    renderOnClick={false}
                  />
                </div>
              </section>
            {/if}
            {#each detailSections as section}
              <section class="space-y-2">
                <h3 class="font-medium">{section.label}</h3>
                {#each section.items as item}<p class="break-words text-sm leading-7">
                    {item}
                  </p>{/each}
                {#if section.source}<p class="text-xs text-muted-foreground">
                    Source: {section.source}
                  </p>{/if}
              </section>
            {/each}
            {#if !detailSections.length && !showChemicalStructure}<p
                class="text-sm text-muted-foreground"
              >
                No description is available for this entity.
              </p>{/if}
          </div>
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
        </Tabs.Content>
        <Tabs.Content value="molecular" class="min-h-0 overflow-y-auto overscroll-contain p-6">
          {#if activeTab === 'molecular'}<MolecularContext entityKey={detailEntity.entityPk} />{/if}
        </Tabs.Content>
        <Tabs.Content value="relationships" class="min-h-0 overflow-y-auto overscroll-contain p-6">
          {#if loadingRelationships}<p role="status" class="mb-3 text-sm text-muted-foreground">
              Loading relationships…
            </p>{/if}
          {#if relationshipsError}<p role="alert">{relationshipsError}</p>{/if}
          {#if relationshipsLoaded && !relationships.length}<p>
              No participant or composition relationships recorded.
            </p>{/if}
          <EntityRelationships
            entityKey={detailEntity.entityPk}
            entityKeys={detailEntity.groupMemberKeys}
            rows={relationships}
            total={relationshipsTotal}
            onSelect={(selected) => {
              entity = selected;
            }}
          />
          {#if relationshipNext}<Button
              disabled={loadingRelationships}
              onclick={() => (relationshipOffset = Number(relationshipNext))}
              >Load more relationships</Button
            >{/if}
        </Tabs.Content>
        {#if ontologyHierarchy}
          <Tabs.Content value="ontology" class="min-h-0 overflow-y-auto overscroll-contain p-6"
            ><OntologyHierarchyBrowser term={ontologyHierarchy} /></Tabs.Content
          >
        {/if}
      </Tabs.Root>
      {#if !hydratedEntity && !loadingDetails}
        <div class="shrink-0 border-t px-6 py-3">
          <Button variant="outline" onclick={() => detailAttempt++}
            >Load identifiers and details</Button
          >
        </div>
      {:else if detailEntity.detailNextCursor || detailIdentifiers.length > identifierLimit}
        <div class="shrink-0 border-t px-6 py-3">
          {#if activeTab === 'identifiers' && (detailEntity.identifiersNextCursor || detailIdentifiers.length > identifierLimit)}
            <Button
              variant="outline"
              disabled={loadingDetails}
              onclick={() => {
                if (detailIdentifiers.length > identifierLimit)
                  identifierLimit += IDENTIFIER_PAGE_SIZE;
                else void moreDetails();
              }}>Load more identifiers</Button
            >
            <span class="ml-3 text-xs text-muted-foreground"
              >Showing {Math.min(identifierLimit, detailIdentifiers.length)} of {detailIdentifierTotal}</span
            >
          {:else}
            <Button variant="outline" disabled={loadingDetails} onclick={moreDetails}
              >Load more details</Button
            >
            <span class="ml-3 text-xs text-muted-foreground"
              >Names and annotations load 20 at a time.</span
            >
          {/if}
        </div>
      {/if}
    {/if}
  </DialogContent>
</Dialog>
