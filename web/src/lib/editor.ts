import { writable } from 'svelte/store';
import type { CategoryOut, TransactionOut } from './types';

export const editingTx = writable<TransactionOut | null>(null);

export type FlowChoice = 'auto' | 'spending' | 'earning' | 'internal' | 'ignored';
export type RecurringChoice = 'auto' | 'yes' | 'no';

export interface EditorForm {
  category: string;
  flow: FlowChoice;
  recurring: RecurringChoice;
}

export interface OverrideBody {
  category_id: string | null;
  flow_class: string | null;
  recurring: boolean | null;
}

export const FLOW_LABELS: Record<FlowChoice, string> = {
  auto: 'Auto',
  spending: 'Spending',
  earning: 'Earning',
  internal: 'Transfer (excluded)',
  ignored: 'Ignore',
};

export function formFromTransaction(tx: TransactionOut): EditorForm {
  const o = tx.override;
  return {
    category: o?.category_id ?? 'auto',
    flow: (o?.flow_class as FlowChoice | null) ?? 'auto',
    recurring: o?.recurring == null ? 'auto' : o.recurring ? 'yes' : 'no',
  };
}

export function categoryFits(category: CategoryOut, flow: string): boolean {
  if (flow === 'spending') return category.applies_to === 'spending' || category.applies_to === 'both';
  if (flow === 'earning') return category.applies_to === 'earning' || category.applies_to === 'both';
  return true;
}

export function categoryChoices(categories: CategoryOut[], flow: string, selected: string): CategoryOut[] {
  return categories.filter((c) => categoryFits(c, flow) && (!c.hidden || c.category_id === selected));
}

export function effectiveFlow(form: EditorForm, currentFlow: string | undefined): string {
  return form.flow === 'auto' ? (currentFlow ?? 'auto') : form.flow;
}

export function overrideBody(form: EditorForm): OverrideBody {
  return {
    category_id: form.category === 'auto' ? null : form.category,
    flow_class: form.flow === 'auto' ? null : form.flow,
    recurring: form.recurring === 'auto' ? null : form.recurring === 'yes',
  };
}

export function applyBody(form: EditorForm): Partial<OverrideBody> {
  const body: Partial<OverrideBody> = {};
  if (form.category !== 'auto') body.category_id = form.category;
  if (form.flow !== 'auto') body.flow_class = form.flow;
  if (form.recurring !== 'auto') body.recurring = form.recurring === 'yes';
  return body;
}
