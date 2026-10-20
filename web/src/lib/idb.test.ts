import 'fake-indexeddb/auto';
import { describe, expect, it } from 'vitest';
import { makePayload } from './__fixtures__/payload';
import { deleteDataset, getDataset, putDataset } from './idb';

describe('idb dataset cache', () => {
  it('returns null when empty, round-trips, and deletes', async () => {
    await deleteDataset();
    expect(await getDataset()).toBeNull();
    const payload = makePayload();
    await putDataset({ payload, fetched_at: '2026-10-19T00:00:00Z' });
    expect(await getDataset()).toEqual({ payload, fetched_at: '2026-10-19T00:00:00Z' });
    await putDataset({ payload: { ...payload, data_version: 9 }, fetched_at: 'x' });
    expect((await getDataset())?.payload.data_version).toBe(9);
    await deleteDataset();
    expect(await getDataset()).toBeNull();
  });

  it('uses database bizviz, store dataset, key current', async () => {
    await putDataset({ payload: makePayload(), fetched_at: 'y' });
    const db = await new Promise<IDBDatabase>((resolve) => {
      const r = indexedDB.open('bizviz');
      r.onsuccess = () => resolve(r.result);
    });
    expect([...db.objectStoreNames]).toEqual(['dataset']);
    const value = await new Promise<{ fetched_at: string }>((resolve) => {
      const r = db.transaction('dataset').objectStore('dataset').get('current');
      r.onsuccess = () => resolve(r.result);
    });
    expect(value.fetched_at).toBe('y');
    db.close();
  });
});
