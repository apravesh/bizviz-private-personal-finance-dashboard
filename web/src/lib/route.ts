import { readable } from 'svelte/store';

export type RouteName = 'dashboard' | 'investments' | 'retirement' | 'transactions' | 'settings';

export interface Route {
  name: RouteName;
  params: URLSearchParams;
}

const ROUTES: Record<string, RouteName> = {
  '/investments': 'investments',
  '/retirement': 'retirement',
  '/transactions': 'transactions',
  '/settings': 'settings',
};

export function parseHash(hash: string): Route {
  const [path, query = ''] = hash.replace(/^#/, '').split('?');
  const name = Object.hasOwn(ROUTES, path) ? ROUTES[path] : 'dashboard';
  return { name, params: new URLSearchParams(query) };
}

export const route = readable<Route>(parseHash(location.hash), (set) => {
  const update = () => set(parseHash(location.hash));
  window.addEventListener('hashchange', update);
  update();
  return () => window.removeEventListener('hashchange', update);
});
