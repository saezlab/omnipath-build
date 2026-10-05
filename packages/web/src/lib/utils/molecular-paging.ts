import type { ProductSummary } from './molecular-presentation';

type EvidencePage = {
  relations: unknown[];
  standaloneEvidence: unknown[];
  observedForms: unknown[];
  referencedProducts?: ProductSummary[];
};

export function molecularContextParams(
  view: 'reference' | 'product',
  offset: number,
  isoform?: string,
): URLSearchParams {
  if (isoform && view !== 'product') throw new Error('Isoform selection requires product view.');
  const params = new URLSearchParams({ view, limit: '20', offset: String(offset) });
  if (isoform) params.set('isoform_identifier', isoform);
  return params;
}

/** Both evidence streams share the API cursor and can finish on different pages. */
export function appendMolecularPage<T extends EvidencePage>(
  previous: T | null,
  incoming: T,
  offset: number,
): T {
  if (!offset || !previous) return incoming;
  const forms = new Map(
    [...previous.observedForms, ...incoming.observedForms].map((form) => [
      JSON.stringify(form),
      form,
    ]),
  );
  const products = new Map(
    [...(previous.referencedProducts || []), ...(incoming.referencedProducts || [])].map(
      (product) => [product.entityPk, product],
    ),
  );
  return {
    ...incoming,
    relations: [...previous.relations, ...incoming.relations],
    standaloneEvidence: [...previous.standaloneEvidence, ...incoming.standaloneEvidence],
    observedForms: [...forms.values()],
    referencedProducts: [...products.values()],
  };
}
