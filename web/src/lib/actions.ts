import { ApiError, NetworkError } from './api';

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof NetworkError) return 'The Mac is unreachable.';
  return error instanceof Error ? error.message : 'Something went wrong.';
}

export async function runAction(action: () => Promise<unknown>): Promise<string | null> {
  try {
    await action();
    return null;
  } catch (error) {
    return errorMessage(error);
  }
}
