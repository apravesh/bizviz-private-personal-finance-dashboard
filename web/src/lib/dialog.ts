import { writable } from 'svelte/store';

export interface ConfirmRequest {
  message: string;
  confirmLabel: string;
  resolve: (ok: boolean) => void;
}

export const confirmRequest = writable<ConfirmRequest | null>(null);

export function confirmDialog(message: string, confirmLabel = 'Continue'): Promise<boolean> {
  return new Promise((resolve) => {
    confirmRequest.set({
      message,
      confirmLabel,
      resolve: (ok) => {
        confirmRequest.set(null);
        resolve(ok);
      },
    });
  });
}
