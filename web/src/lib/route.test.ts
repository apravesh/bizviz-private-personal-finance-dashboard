import { describe, expect, it } from 'vitest';
import { parseHash } from './route';

describe('parseHash', () => {
  it('maps each tab to its route', () => {
    expect(parseHash('').name).toBe('dashboard');
    expect(parseHash('#/').name).toBe('dashboard');
    expect(parseHash('#/investments').name).toBe('investments');
    expect(parseHash('#/retirement').name).toBe('retirement');
    expect(parseHash('#/transactions?account=a').name).toBe('transactions');
    expect(parseHash('#/settings').name).toBe('settings');
    expect(parseHash('#/nope').name).toBe('dashboard');
    expect(parseHash('#toString').name).toBe('dashboard');
  });

  it('keeps query parameters', () => {
    expect(parseHash('#/transactions?account=a').params.get('account')).toBe('a');
  });
});
