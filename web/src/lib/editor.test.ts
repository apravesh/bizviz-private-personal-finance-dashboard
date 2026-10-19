import { describe, expect, it } from 'vitest';
import { makePayload, tx } from './__fixtures__/payload';
import {
  applyBody,
  categoryChoices,
  effectiveFlow,
  formFromTransaction,
  overrideBody,
  type EditorForm,
} from './editor';

const form = (over: Partial<EditorForm> = {}): EditorForm => ({
  category: 'auto',
  flow: 'auto',
  recurring: 'auto',
  ...over,
});

describe('categoryChoices', () => {
  const categories = makePayload().categories;
  const ids = (flow: string, selected = '') => categoryChoices(categories, flow, selected).map((c) => c.category_id);

  it('spending allows spending and both, never hidden', () => {
    expect(ids('spending')).toEqual(['groceries', 'dining', 'transfer']);
  });
  it('earning allows earning and both', () => {
    expect(ids('earning')).toEqual(['income', 'transfer']);
  });
  it('internal and ignored allow any visible category', () => {
    expect(ids('internal')).toEqual(['groceries', 'dining', 'income', 'transfer']);
    expect(ids('ignored')).toEqual(ids('internal'));
  });
  it('keeps an already-selected hidden category so it is not silently cleared', () => {
    expect(ids('spending', 'secret')).toContain('secret');
  });
});

describe('effectiveFlow', () => {
  it('uses the chosen flow, else the current classification', () => {
    expect(effectiveFlow(form({ flow: 'earning' }), 'spending')).toBe('earning');
    expect(effectiveFlow(form(), 'spending')).toBe('spending');
  });
});

describe('formFromTransaction', () => {
  it('maps no override to Auto', () => {
    expect(formFromTransaction(tx('a', '2026-10-01', 1))).toEqual(form());
  });
  it('maps override values', () => {
    const t = tx('a', '2026-10-01', 1, {
      override: { category_id: 'dining', flow_class: 'internal', recurring: false },
    });
    expect(formFromTransaction(t)).toEqual({ category: 'dining', flow: 'internal', recurring: 'no' });
  });
});

describe('request bodies', () => {
  it('override sends every field, Auto as null', () => {
    expect(overrideBody(form())).toEqual({ category_id: null, flow_class: null, recurring: null });
    expect(overrideBody(form({ category: 'dining', flow: 'spending', recurring: 'yes' }))).toEqual({
      category_id: 'dining',
      flow_class: 'spending',
      recurring: true,
    });
    expect(overrideBody(form({ recurring: 'no' })).recurring).toBe(false);
  });
  it('apply-to-merchant sends only non-Auto fields', () => {
    expect(applyBody(form())).toEqual({});
    expect(applyBody(form({ category: 'dining' }))).toEqual({ category_id: 'dining' });
    expect(applyBody(form({ flow: 'ignored', recurring: 'no' }))).toEqual({
      flow_class: 'ignored',
      recurring: false,
    });
  });
});
