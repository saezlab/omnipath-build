<script lang="ts">
  import { annotationLabel } from '$lib/utils/annotation-presentation';
  import { appendMolecularPage, molecularContextParams } from '$lib/utils/molecular-paging';
  import { releaseFetch } from '$lib/api/release';
  import { entityFromWire, relationFromWire } from '$lib/api/adapters';
  import type { WireEntity, WireRelation } from '$lib/api/contracts';
  import type { InteractionDetailsData, InteractionListRow } from '$lib/types/interactions';
  import InteractionDetails from '$lib/components/interactions/InteractionDetails.svelte';
  import MolecularForm from './MolecularForm.svelte';
  import type { MolecularFormRecord } from '$lib/types/molecular';
  let { entityKey }: { entityKey: string } = $props();
  let selectedKey = $state('');
  let view = $state<'reference' | 'product'>('reference');
  let selectedIsoform = $state<string | undefined>();
  let data = $state<{
    referenceEntityKey?: string;
    standaloneEvidence: Array<{
      entityPk: string;
      occurrence: {
        source?: string;
        molecular_form?: MolecularFormRecord | null;
        annotations?: Array<{ term: string; value?: string | null }>;
      };
    }>;
    catalogueProducts: WireEntity[];
    referencedProducts?: WireEntity[];
    observedForms: MolecularFormRecord[];
    relations: InteractionListRow[];
    relationsTotal: number;
    nextCursor?: string;
    filters: Record<string, unknown>;
  } | null>(null);
  let selectedRelation = $state<InteractionDetailsData | null>(null);
  let loading = $state(false);
  let error = $state('');
  let offset = $state(0);
  let generation = 0;
  const activeKey = $derived(selectedKey || entityKey);
  const knownProducts = $derived([
    ...(data?.catalogueProducts || []),
    ...(data?.referencedProducts || []),
    ...(data?.relations.flatMap((row) => [row.subjectEntity, row.objectEntity]) || []),
  ]);
  $effect(() => {
    void entityKey; // Reset local selection whenever the entity changes.
    selectedKey = '';
    view = 'reference';
    selectedIsoform = undefined;
    offset = 0;
  });
  $effect(() => {
    const key = activeKey,
      isoform = selectedIsoform,
      pageOffset = offset,
      contextView = view;
    const current = ++generation;
    if (!pageOffset) data = null;
    loading = true;
    error = '';
    selectedRelation = null;
    const params = molecularContextParams(contextView, pageOffset, isoform);
    releaseFetch(`/app-api/entities/${encodeURIComponent(key)}/molecular-context?${params}`)
      .then(async (response) => {
        if (!response.ok) throw new Error('Unable to load molecular evidence.');
        const result = await response.json();
        if (current !== generation) return;
        const rows = result.relations.map(
          (row: {
            relation: WireRelation;
            subjectEntity: WireEntity;
            objectEntity: WireEntity;
          }) => ({
            relation: relationFromWire(row.relation),
            subjectEntity: entityFromWire(row.subjectEntity),
            objectEntity: entityFromWire(row.objectEntity),
          }),
        );
        data = appendMolecularPage(data, { ...result, relations: rows }, pageOffset);
      })
      .catch((reason) => {
        if (current === generation) error = reason.message;
      })
      .finally(() => {
        if (current === generation) loading = false;
      });
    return () => {
      generation++;
    };
  });
  function selectProduct(key: string, isoform?: string) {
    selectedKey = key;
    view = 'product';
    selectedIsoform = isoform;
    offset = 0;
  }
  function backToReference() {
    selectedKey = '';
    selectedIsoform = undefined;
    view = 'reference';
    offset = 0;
  }
  async function selectRelation(row: InteractionListRow) {
    const filters = encodeURIComponent(JSON.stringify(data?.filters || {}));
    const response = await releaseFetch(
      `/app-api/relations/${encodeURIComponent(row.relation.relationPk)}/evidence?filters=${filters}`,
    );
    if (!response.ok) {
      error = 'Unable to load evidence.';
      return;
    }
    selectedRelation = { ...row, evidence: (await response.json()).evidence };
  }
</script>

<div class="space-y-5">
  {#if error}<p role="alert">{error}</p>{/if}
  {#if loading}<p role="status">Loading molecular evidence…</p>{/if}
  {#if data}
    {#if view === 'product'}<button class="text-sm underline" onclick={backToReference}
        >Back to selected entity</button
      >{/if}
    <p class="text-sm text-muted-foreground">
      {data.referenceEntityKey?.startsWith('entrez:') ? 'Gene reference' : 'Native reference'}: {data.referenceEntityKey ||
        'Unknown'}. Molecular context that was not reported remains unspecified.
    </p>
    {#if data.catalogueProducts.length}
      <section class="space-y-2">
        <h3 class="font-medium">Catalogue products</h3>
        <p class="text-sm text-muted-foreground">
          Known gene links. Select a product to see only evidence that names it.
        </p>
        <div class="flex flex-wrap gap-2">
          {#each data.catalogueProducts as product (product.entityPk)}<button
              class="rounded border px-3 py-2 text-sm"
              onclick={() => selectProduct(product.entityPk)}
              >{product.displayName || product.label || product.canonicalIdentifier} · {product.entityType}</button
            >{/each}
        </div>
      </section>
    {/if}
    <section class="space-y-3">
      <h3 class="font-medium">Observed molecular forms</h3>
      {#if !data.observedForms.length}<p class="text-sm text-muted-foreground">
          No specific molecular form was reported for these observations.
        </p>{/if}
      {#each data.observedForms as form}
        {@const productKey = form.protein_entity_key || form.transcript_entity_key}
        {@const iso = form.isoform_identifier
          ? `${form.isoform_identifier.ns}:${form.isoform_identifier.id}`
          : undefined}
        <div class="rounded border p-3">
          <MolecularForm {form} products={knownProducts} onSelectProduct={selectProduct} />
          {#if productKey}<button
              class="mt-2 text-sm underline"
              onclick={() => selectProduct(productKey, iso)}
              >View evidence for {iso || 'this product'}</button
            >{/if}
        </div>{/each}
    </section>
    <section class="space-y-2">
      <h3 class="font-medium">Relations with matching evidence · {data.relationsTotal}</h3>
      {#each data.relations as row, index (`${row.relation.relationPk}:${index}`)}<button
          class="block w-full rounded border p-3 text-left text-sm"
          onclick={() => selectRelation(row)}
          >{row.subjectEntity.displayName || row.subjectEntity.label} · {row.relation
            .displayLabel || row.relation.predicate} · {row.objectEntity.displayName ||
            row.objectEntity.label}</button
        >{/each}
    </section>
    {#if data.standaloneEvidence?.length}<section class="space-y-3">
        <h3 class="font-medium">Standalone observations</h3>
        {#each data.standaloneEvidence as record}<div class="rounded border p-3">
            <p class="mb-2 text-sm">{record.occurrence.source || 'Unknown source'}</p>
            <MolecularForm
              form={record.occurrence.molecular_form}
              products={knownProducts}
              onSelectProduct={selectProduct}
            />
            {#if record.occurrence.annotations?.length}<dl class="mt-3 space-y-1 text-sm">
                {#each record.occurrence.annotations as annotation}<div>
                    <dt class="text-muted-foreground">{annotationLabel(annotation.term)}</dt>
                    <dd class="break-words">{annotation.value || 'Unspecified'}</dd>
                  </div>{/each}
              </dl>{/if}
          </div>{/each}
      </section>{/if}
    {#if data.nextCursor}<button
        class="text-sm underline"
        disabled={loading}
        onclick={() => (offset = Number(data?.nextCursor))}>Load more evidence</button
      >{/if}
    {#if selectedRelation}<InteractionDetails
        selectedInteraction={selectedRelation}
        products={knownProducts}
        onSelectProduct={selectProduct}
      />{/if}
  {/if}
</div>
