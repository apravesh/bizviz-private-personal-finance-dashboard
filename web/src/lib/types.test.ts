import { describe, expect, it } from 'vitest';
import type { Flow, NetWorthPoint, Scope } from './types';

describe('types', () => {
  it('accepts values shaped like the API payload', () => {
    const flow: Flow = 'spending';
    const scope: Scope = 'mine';
    const point: NetWorthPoint = {
      date: '2026-10-15',
      mine: { assets: 1, debts: 0, stale_accounts: 0 },
      household: { assets: 2, debts: 0, stale_accounts: 0 },
    };
    expect(point[scope].assets).toBe(1);
    expect(flow).toBe('spending');
  });
});
