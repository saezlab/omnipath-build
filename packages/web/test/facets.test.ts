import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { splitEmptyOptions } from '../src/lib/utils/facets.ts';

test('zero-count facet options fold away unless selected or not yet counted', () => {
  const counts: Record<string, number | undefined> = {
    kegg: 20,
    brenda: 0,
    chebi: 0,
    new: undefined,
  };
  const { shown, empty } = splitEmptyOptions(
    ['kegg', 'brenda', 'chebi', 'new'],
    (option) => counts[option],
    (option) => option === 'chebi',
  );
  assert.deepEqual(shown, ['kegg', 'chebi', 'new']);
  assert.deepEqual(empty, ['brenda']);
});
