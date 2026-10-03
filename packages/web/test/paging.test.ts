import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  createPagedQuery,
  collectEntityKeys,
  type PageState,
} from '../src/lib/features/explorer/paged-query.ts';
import { createPoller } from '../src/lib/features/admin/polling.ts';

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}
test('out-of-order initial responses cannot overwrite the current query', async () => {
  let state!: PageState<string>;
  const paging = createPagedQuery<string>(2, (next) => {
    state = next;
  });
  const old = deferred<string[]>();
  let oldSignal!: AbortSignal;
  const first = paging.reset((_offset, signal) => {
    oldSignal = signal;
    return old.promise;
  });
  await paging.reset(async () => ['new']);
  old.resolve(['old']);
  await first;
  assert.equal(oldSignal.aborted, true);
  assert.deepEqual(state.items, ['new']);
  assert.equal(state.loading, false);
});
test('stale pages cannot append to another query or alter its loading state', async () => {
  let state!: PageState<string>;
  const paging = createPagedQuery<string>(2, (next) => {
    state = next;
  });
  const more = deferred<string[]>();
  await paging.reset(async (offset) => (offset ? more.promise : ['a', 'b']));
  const oldPage = paging.loadMore();
  await paging.reset(async () => ['new']);
  more.resolve(['c']);
  await oldPage;
  assert.deepEqual(state.items, ['new']);
  assert.equal(state.loadingMore, false);
});
test('relation entity resolution exhausts more than 200 matches and reports cursor loops', async () => {
  const keys = await collectEntityKeys<number>(
    async (cursor) => ({
      entities: Array.from({ length: cursor === null ? 200 : 17 }, (_, i) => ({
        entityPk: String((cursor ?? 0) + i),
      })),
      nextCursor: cursor === null ? 200 : null,
    }),
    new AbortController().signal,
  );
  assert.equal(keys.length, 217);
  await assert.rejects(
    collectEntityKeys(async () => ({ entities: [], nextCursor: 1 }), new AbortController().signal),
    /repeated/,
  );
});
test('manual refreshes share pending work and stop aborts the request', async () => {
  let calls = 0;
  let signal!: AbortSignal;
  const pending = deferred<void>();
  const poller = createPoller(
    async (value) => {
      calls++;
      signal = value;
      await pending.promise;
    },
    () => 1000,
  );
  const a = poller.refresh();
  const b = poller.refresh();
  await Promise.resolve();
  assert.equal(calls, 1);
  poller.stop();
  assert.equal(signal.aborted, true);
  pending.resolve();
  await Promise.all([a, b]);
});
