import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { observationSummary, productLabel } from '../src/lib/utils/molecular-presentation.ts';

test('product presentation uses catalogue accession and label without exposing unknown hashes', () => {
  const key = 'a'.repeat(64);
  assert.equal(
    productLabel(key, [{ entityPk: key, label: 'TP53', canonicalIdentifier: 'P04637' }]),
    'TP53 · P04637',
  );
  assert.equal(
    productLabel(key, [{ entityPk: key, label: 'P04637', canonicalIdentifier: 'P04637' }]),
    'P04637',
  );
  assert.equal(productLabel(key, []), undefined);
});

test('loaded matching evidence supplies summary across resources sharing a relation key', () => {
  const relation = { evidenceCount: 1, sources: ['a'] };
  assert.deepEqual(observationSummary(relation, [{ source: 'a' }, { source: 'b' }]), {
    evidenceCount: 2,
    sources: ['a', 'b'],
  });
  assert.deepEqual(observationSummary(relation), relation);
  assert.deepEqual(observationSummary(relation, []), { evidenceCount: 0, sources: [] });
});
