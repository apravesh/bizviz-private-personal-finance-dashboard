import type { DataPayload } from './types';

export interface CachedDataset {
  payload: DataPayload;
  fetched_at: string;
}

const DB_NAME = 'bizviz';
const STORE = 'dataset';
const KEY = 'current';

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => {
      request.result.createObjectStore(STORE);
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function run<T>(mode: IDBTransactionMode, action: (store: IDBObjectStore) => IDBRequest): Promise<T> {
  const db = await open();
  try {
    return await new Promise<T>((resolve, reject) => {
      const tx = db.transaction(STORE, mode);
      const request = action(tx.objectStore(STORE));
      tx.oncomplete = () => resolve(request.result as T);
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error);
    });
  } finally {
    db.close();
  }
}

export async function getDataset(): Promise<CachedDataset | null> {
  const value = await run<CachedDataset | undefined>('readonly', (s) => s.get(KEY));
  return value ?? null;
}

export async function putDataset(value: CachedDataset): Promise<void> {
  await run('readwrite', (s) => s.put(value, KEY));
}

export async function deleteDataset(): Promise<void> {
  await run('readwrite', (s) => s.delete(KEY));
}
