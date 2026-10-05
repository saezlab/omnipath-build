import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { appendMolecularPage, molecularContextParams } from '../src/lib/utils/molecular-paging.ts';
import type { MolecularFormRecord } from '../src/lib/types/molecular.ts';
import { productLabel, type ProductSummary } from '../src/lib/utils/molecular-presentation.ts';

type Page = {
  relations: string[];
  standaloneEvidence: string[];
  observedForms: MolecularFormRecord[];
  referencedProducts: ProductSummary[];
  nextCursor: string | null;
};

test('reference navigation and exact product/isoform navigation use distinct API views', () => {
  assert.deepEqual(Object.fromEntries(molecularContextParams('reference', 0)), {
    view: 'reference',
    limit: '20',
    offset: '0',
  });
  assert.deepEqual(Object.fromEntries(molecularContextParams('product', 20, 'uniprot:P04637-2')), {
    view: 'product',
    limit: '20',
    offset: '20',
    isoform_identifier: 'uniprot:P04637-2',
  });
  assert.throws(
    () => molecularContextParams('reference', 0, 'uniprot:P04637-2'),
    /requires product view/,
  );
});

test('standalone evidence continues after relations finish without losing earlier forms', () => {
  const first: Page = {
    relations: ['relation'],
    standaloneEvidence: ['first', 'second'],
    observedForms: [{ protein_entity_key: 'protein' }],
    referencedProducts: [],
    nextCursor: '2',
  };
  const second: Page = {
    relations: [],
    standaloneEvidence: ['third', 'fourth'],
    observedForms: [{ protein_entity_key: 'protein' }, { transcript_entity_key: 'transcript' }],
    referencedProducts: [],
    nextCursor: '4',
  };
  assert.deepEqual(appendMolecularPage(first, second, 2), {
    relations: ['relation'],
    standaloneEvidence: ['first', 'second', 'third', 'fourth'],
    observedForms: [{ protein_entity_key: 'protein' }, { transcript_entity_key: 'transcript' }],
    referencedProducts: [],
    nextCursor: '4',
  });
  assert.deepEqual(appendMolecularPage(first, second, 0), second);
});

test('relation-only later pages retain loaded standalone observations and final cursor', () => {
  const first: Page = {
    relations: ['first'],
    standaloneEvidence: ['observation'],
    observedForms: [],
    referencedProducts: [],
    nextCursor: '2',
  };
  const last: Page = {
    relations: ['last'],
    standaloneEvidence: [],
    observedForms: [],
    referencedProducts: [],
    nextCursor: null,
  };
  assert.deepEqual(appendMolecularPage(first, last, 2), {
    ...last,
    relations: ['first', 'last'],
    standaloneEvidence: ['observation'],
  });
});

test('referenced native product labels survive later pages and duplicate summaries merge', () => {
  const key = 'a'.repeat(64);
  const first: Page = {
    relations: [],
    standaloneEvidence: ['native observation'],
    observedForms: [{ protein_entity_key: key }],
    referencedProducts: [{ entityPk: key, canonicalIdentifier: 'Q9Y6K9', label: 'Q9Y6K9' }],
    nextCursor: '20',
  };
  const next: Page = {
    relations: ['later relation'],
    standaloneEvidence: [],
    observedForms: [],
    referencedProducts: [{ entityPk: 'b'.repeat(64), canonicalIdentifier: 'ENST000001.2' }],
    nextCursor: '40',
  };
  const loaded = appendMolecularPage(first, next, 20);
  assert.equal(productLabel(key, loaded.referencedProducts), 'Q9Y6K9');
  const final = appendMolecularPage(
    loaded,
    { ...next, referencedProducts: first.referencedProducts, nextCursor: null },
    40,
  );
  assert.equal(final.referencedProducts.length, 2);
  assert.equal(productLabel(key, final.referencedProducts), 'Q9Y6K9');
  assert.deepEqual(appendMolecularPage(first, next, 0).referencedProducts, next.referencedProducts);
});
