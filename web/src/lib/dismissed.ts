import { writable } from 'svelte/store';

export const DISMISSED_KEY = 'bizviz.dismissed';

function load(): string[] {
  try {
    const value = JSON.parse(localStorage.getItem(DISMISSED_KEY) ?? '[]');
    return Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : [];
  } catch {
    return [];
  }
}

export const dismissed = writable<Set<string>>(new Set(load()));

export function dismiss(key: string): void {
  dismissed.update((set) => {
    const next = new Set(set);
    next.add(key);
    try {
      localStorage.setItem(DISMISSED_KEY, JSON.stringify([...next]));
    } catch {
      // private mode: dismissal lasts for this session only
    }
    return next;
  });
}

export function reloadDismissed(): void {
  dismissed.set(new Set(load()));
}
